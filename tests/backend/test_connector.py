"""Claude connector (remote MCP server) - docs/connector.md.

The connector lets a chat spend a person's credits and fetch arbitrary links
server-side, so the things that must not regress are: link normalization and
the SSRF guard, the JSON-RPC envelope Claude speaks, and the job state machine
(fetch -> process -> ready -> export -> done) staying a thin layer over the
existing routes (every step is asserted through the loopback calls it makes).
"""
import asyncio
import importlib.util
import json
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).parent.parent.parent


def _load():
    """Load connector_fns.py with `modal` / `pipeline_core` stubbed (the real
    ones need Modal credentials). @app.function becomes the identity decorator,
    so fetch_url_fn stays a plain callable."""
    core = types.ModuleType("pipeline_core")
    core.app = MagicMock()
    core.app.function = lambda **kw: (lambda f: f)
    core.light_image, core.tmp_vol, core.TMP_DIR = None, MagicMock(), "/tmp/connector-test"
    saved = {k: sys.modules.get(k) for k in ("modal", "pipeline_core")}
    sys.modules["modal"], sys.modules["pipeline_core"] = MagicMock(), core
    try:
        spec = importlib.util.spec_from_file_location("connector_fns_under_test", ROOT / "connector_fns.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


C = _load()
run = asyncio.run


# ── Link normalization ────────────────────────────────────────────────────────

def test_drive_share_link_becomes_a_direct_download():
    u = C._normalize_video_url("https://drive.google.com/file/d/1AbCdEfGhIjKlMnOp_qrs/view?usp=sharing")
    assert u == "https://drive.usercontent.google.com/download?id=1AbCdEfGhIjKlMnOp_qrs&export=download&confirm=t"
    assert C._normalize_video_url("https://drive.google.com/open?id=1AbCdEfGhIjKlMnOp_qrs") == u


def test_drive_folder_link_is_refused_with_a_useful_message():
    with pytest.raises(ValueError, match="folder"):
        C._normalize_video_url("https://drive.google.com/drive/folders/")


def test_dropbox_link_is_forced_to_download_mode():
    u = C._normalize_video_url("https://www.dropbox.com/scl/fi/abc/clip.mp4?rlkey=xyz&dl=0")
    assert "dl=1" in u and "dl=0" not in u and "rlkey=xyz" in u


def test_only_https_links_are_accepted():
    for bad in ("http://example.com/a.mp4", "ftp://example.com/a.mp4", "file:///etc/passwd", "", None, "a.mp4"):
        with pytest.raises(ValueError):
            C._normalize_video_url(bad)
    assert C._normalize_video_url(" https://cdn.example.com/a.mp4 ") == "https://cdn.example.com/a.mp4"


# ── SSRF guard ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("ip,public", [
    ("8.8.8.8", True), ("2606:4700:4700::1111", True),
    ("127.0.0.1", False), ("10.0.0.5", False), ("192.168.1.1", False), ("172.16.0.1", False),
    ("169.254.169.254", False), ("::1", False), ("fd00::1", False), ("0.0.0.0", False), ("not-an-ip", False),
])
def test_ip_classification(ip, public):
    assert C._ip_is_public(ip) is public


def test_host_is_public_requires_every_resolved_address_to_be_public():
    def res(*addrs):
        return lambda host, port: [(None, None, None, None, (a, port)) for a in addrs]
    assert C._host_is_public("ok.example", res("8.8.8.8", "1.1.1.1")) is True
    # One private address among public ones is enough to refuse (DNS trickery).
    assert C._host_is_public("mixed.example", res("8.8.8.8", "10.0.0.1")) is False
    assert C._host_is_public("meta.example", res("169.254.169.254")) is False
    assert C._host_is_public("nx.example", lambda h, p: (_ for _ in ()).throw(OSError("nxdomain"))) is False
    assert C._host_is_public("", res("8.8.8.8")) is False


def test_fetch_refuses_an_internal_address_before_any_request():
    out = C.fetch_url_fn("https://127.0.0.1/secret.mp4", "u" + "a" * 32 + "__mcpkey")
    assert out["ok"] is False and "public https" in out["error"]


def test_filename_comes_from_content_disposition_and_is_sanitized():
    f = C._filename_from_headers
    assert f({"content-disposition": 'attachment; filename="my clip.mp4"'}) == "my clip.mp4"
    assert f({"Content-Disposition": "attachment; filename*=UTF-8''%D7%A9%D7%9C%D7%95%D7%9D.mp4"}) == "שלום.mp4"
    assert "/" not in f({"content-disposition": 'attachment; filename="../../etc/passwd"'})
    assert f({}) == "video.mp4"


# ── Caption edits ─────────────────────────────────────────────────────────────

CAPS = [{"start": 0, "end": 1, "text": "שלום לכולם", "words": [{"w": "שלום"}, {"w": "לכולם"}]},
        {"start": 1, "end": 2, "text": "היום נדבר", "words": [{"w": "היום"}, {"w": "נדבר"}]},
        {"start": 2, "end": 3, "text": "תודה", "words": [{"w": "תודה"}]}]


def test_caption_edit_drops_stale_word_timings_only_for_the_edited_line():
    out = C._apply_caption_edits(CAPS, [{"index": 1, "text": "היום נדבר על מים"}, {"index": 0, "text": "שלום לכולם"}])
    assert out[1]["text"] == "היום נדבר על מים" and "words" not in out[1]
    assert "words" in out[0], "an unchanged line keeps its karaoke timings"
    assert "words" in CAPS[1], "the stored captions are not mutated"


def test_empty_edit_text_removes_the_line_and_bad_indexes_are_reported():
    assert [c["text"] for c in C._apply_caption_edits(CAPS, [{"index": 1, "text": "  "}])] == ["שלום לכולם", "תודה"]
    with pytest.raises(C.ConnectorError, match="no caption 7"):
        C._apply_caption_edits(CAPS, [{"index": 7, "text": "x"}])
    with pytest.raises(C.ConnectorError):
        C._apply_caption_edits(CAPS, [{"text": "x"}])


# ── JSON-RPC envelope ─────────────────────────────────────────────────────────

def _rpc(method, params=None, mid=1, **kw):
    msg = {"jsonrpc": "2.0", "method": method}
    if mid is not None:
        msg["id"] = mid
    if params is not None:
        msg["params"] = params
    return run(C._mcp_handle(msg, kw.pop("ctx", None), **kw))


def test_initialize_echoes_a_supported_version_and_falls_back_otherwise():
    r = _rpc("initialize", {"protocolVersion": "2025-03-26", "capabilities": {}})["result"]
    assert r["protocolVersion"] == "2025-03-26" and "tools" in r["capabilities"]
    assert r["serverInfo"]["name"] == "paypline" and r["instructions"]
    assert _rpc("initialize", {"protocolVersion": "1999-01-01"})["result"]["protocolVersion"] == C.MCP_PROTOCOL_VERSIONS[0]


def test_notifications_get_no_reply_and_unknown_methods_get_an_error():
    assert _rpc("notifications/initialized", mid=None) is None
    assert _rpc("no/such")["error"]["code"] == -32601
    assert run(C._mcp_handle({"hello": 1}, None))["error"]["code"] == -32600
    assert _rpc("ping")["result"] == {}


def test_tools_list_is_valid_and_every_listed_tool_is_implemented():
    tools = _rpc("tools/list")["result"]["tools"]
    assert {t["name"] for t in tools} == set(C._CONNECTOR_IMPL)
    for t in tools:
        assert t["description"] and t["inputSchema"]["type"] == "object"
        for req in t["inputSchema"].get("required", []):
            assert req in t["inputSchema"]["properties"]


def test_tool_errors_are_readable_and_internal_ones_are_masked():
    async def user_err(ctx, args):
        raise C.ConnectorError("No video credits left")

    async def crash(ctx, args):
        raise RuntimeError("/tmp/secret/path exploded")

    r = _rpc("tools/call", {"name": "a", "arguments": {}}, impl={"a": user_err})["result"]
    assert r["isError"] is True and "No video credits left" in r["content"][0]["text"]
    r = _rpc("tools/call", {"name": "b", "arguments": {}}, impl={"b": crash})["result"]
    assert r["isError"] is True and "secret" not in r["content"][0]["text"]
    assert _rpc("tools/call", {"name": "nope"})["error"]["code"] == -32602


# ── Job flow over the loopback ────────────────────────────────────────────────

class FakeCtx:
    """Records every loopback call; routes answer from a scripted table."""
    uid, uprefix, base_url = "a" * 32, "u" + "a" * 32 + "__", "https://api.test"

    def __init__(self, credits=3, fetch=None):
        self.store, self.calls, self.spawned = {}, [], []
        self.me = {"username": "tester@example.com", "role": "user", "videos_used": 0, "video_limit": credits}
        self.fetch_result = fetch or {"ok": True, "bytes": 5_000_000, "duration": 42.5, "is_audio": False,
                                      "filename": "raw clip.mp4"}
        self.fetch_running = False
        self.process = (200, {"video_key": self.uprefix + "abc_cut.mp4", "captions": [dict(c) for c in CAPS]})
        self.burn = (200, {"output_key": self.uprefix + "abc_out.mp4"})
        self.hooks = (200, {"hooks": [{"text": "הוק ראשון", "rationale": "סקרנות"}, {"text": "הוק שני"}]})

    def spawn_fetch(self, url, dest):
        self.spawned.append((url, dest))
        return "fc-fetch"

    def poll(self, call_id):
        return (None, True) if self.fetch_running else (self.fetch_result, False)

    async def call(self, method, path, query=None, body=None):
        self.calls.append((method, path, query, body))
        if path == "/auth/me":
            return 200, self.me
        if path == "/auth/media-token":
            return 200, {"token": "m.tok.en", "expires_in": 3600}
        if path == "/process/":
            return 202, {"call_id": "fc-process"}
        if path.startswith("/process_poll/"):
            return self.process
        if path == "/burn/":
            return 202, {"call_id": "fc-burn"}
        if path.startswith("/burn_poll/"):
            return self.burn
        if path == "/generate-hook/":
            return 202, {"call_id": "fc-hook"}
        if path.startswith("/generate-hook-poll/"):
            return self.hooks
        if path == "/jobs/":
            return 200, {"jobs": [{"key": self.uprefix + "abc_out.mp4", "name": "raw clip.mp4", "ts": 1795550000,
                                   "duration": 14, "size": 1}]}
        raise AssertionError(f"unexpected loopback call {method} {path}")

    def sent(self, path):
        return [c for c in self.calls if c[1] == path]


def _status(ctx, job_id):
    return run(C._tool_get_video_status(ctx, {"job_id": job_id}, wait_s=0.2, tick=0.01))


def test_full_flow_fetch_process_export_download():
    ctx = FakeCtx()
    started = run(C._tool_start_video(ctx, {"url": "https://www.dropbox.com/s/x/clip.mp4?dl=0", "cut_silences": False}))
    job_id = started["job_id"]
    url, dest = ctx.spawned[0]
    assert "dl=1" in url and dest.startswith(ctx.uprefix + "mcp"), "the file lands under the owner's key prefix"

    ready = _status(ctx, job_id)
    assert ready["status"] == "ready"
    assert [c["text"] for c in ready["captions"]] == ["שלום לכולם", "היום נדבר", "תודה"]
    assert "words" not in ready["captions"][0], "word timings stay server-side"
    (_, _, q, _), = ctx.sent("/process/")
    assert "cut_silences=false" in q and "burn_captions=true" in q and "duration=42.50" in q
    assert "filename=raw+clip.mp4" in q, "the name from the download headers is used"

    out = run(C._tool_export_video(ctx, {"job_id": job_id, "style": "karaoke", "hook_text": "הוק ראשון",
                                         "caption_edits": [{"index": 2, "text": ""}],
                                         "highlight_color": "#00ff00", "text_color": "red"}))
    assert out["status"] == "exporting"
    (_, _, q, body), = ctx.sent("/burn/")
    assert "video_key=" + ctx.uprefix + "abc_cut.mp4" in q.replace("%5F", "_")
    assert body["caption_style"]["mode"] == "karaoke"
    assert body["caption_style"]["highlight_color"] == "#00FF00"
    assert body["caption_style"]["font_color"] == "#FFFFFF", "an invalid colour falls back to the default"
    assert body["hook"]["text"] == "הוק ראשון" and "lines" not in body["hook"]
    assert len(body["captions"]) == 2 and body["broll"] == []

    done = _status(ctx, job_id)
    assert done["status"] == "done"
    assert done["download_url"].startswith("https://api.test/download/" + ctx.uprefix + "abc_out.mp4/?filename=")
    assert "token=m.tok.en" in done["download_url"] and "raw%20clip_edited.mp4" in done["download_url"]


def test_no_credits_fails_fast_without_fetching_anything():
    ctx = FakeCtx(credits=0)
    with pytest.raises(C.ConnectorError, match="credits"):
        run(C._tool_start_video(ctx, {"url": "https://cdn.example.com/a.mp4"}))
    assert ctx.spawned == [] and ctx.store == {}


def test_a_failed_download_surfaces_the_reason_and_never_starts_processing():
    ctx = FakeCtx(fetch={"ok": False, "error": "The link opens a web page, not a file"})
    job_id = run(C._tool_start_video(ctx, {"url": "https://cdn.example.com/a.mp4"}))["job_id"]
    st = _status(ctx, job_id)
    assert st["status"] == "error" and "web page" in st["error"]
    assert ctx.sent("/process/") == []


def test_status_keeps_waiting_while_the_download_is_running():
    ctx = FakeCtx()
    ctx.fetch_running = True
    job_id = run(C._tool_start_video(ctx, {"url": "https://cdn.example.com/a.mp4"}))["job_id"]
    assert _status(ctx, job_id)["status"] == "fetching"
    assert ctx.sent("/process/") == []


def test_processing_failure_and_limit_reached_are_explained():
    ctx = FakeCtx()
    ctx.process = (500, {"error": "boom"})
    job_id = run(C._tool_start_video(ctx, {"url": "https://cdn.example.com/a.mp4"}))["job_id"]
    st = _status(ctx, job_id)
    assert st["status"] == "error" and "refunded" in st["error"]
    assert C._err_text({"error": "limit_reached"}, "x").startswith("No video credits left")


def test_cut_only_run_finishes_without_an_export_step():
    ctx = FakeCtx()
    ctx.process = (200, {"video_key": ctx.uprefix + "abc_cut.mp4", "captions": []})
    job_id = run(C._tool_start_video(ctx, {"url": "https://cdn.example.com/a.mp4", "captions": False}))["job_id"]
    st = _status(ctx, job_id)
    assert st["status"] == "done" and "abc_cut.mp4" in st["download_url"]
    with pytest.raises(C.ConnectorError):
        run(C._tool_export_video(ctx, {"job_id": job_id}))


def test_export_is_refused_before_ready_and_a_failed_export_returns_to_ready():
    ctx = FakeCtx()
    ctx.fetch_running = True
    job_id = run(C._tool_start_video(ctx, {"url": "https://cdn.example.com/a.mp4"}))["job_id"]
    with pytest.raises(C.ConnectorError, match="ready"):
        run(C._tool_export_video(ctx, {"job_id": job_id}))
    ctx.fetch_running = False
    _status(ctx, job_id)
    ctx.burn = (500, {"error": "ffmpeg died"})
    run(C._tool_export_video(ctx, {"job_id": job_id}))
    st = _status(ctx, job_id)
    assert st["status"] == "ready" and st["last_export_error"] == "ffmpeg died"


def test_hooks_are_returned_with_their_reason_and_need_captions():
    ctx = FakeCtx()
    job_id = run(C._tool_start_video(ctx, {"url": "https://cdn.example.com/a.mp4"}))["job_id"]
    with pytest.raises(C.ConnectorError):
        run(C._tool_generate_hooks(ctx, {"job_id": job_id}))
    _status(ctx, job_id)
    out = run(C._tool_generate_hooks(ctx, {"job_id": job_id}, wait_s=0.1, tick=0.01))
    assert out["hooks"] == [{"text": "הוק ראשון", "why": "סקרנות"}, {"text": "הוק שני"}]
    (_, _, _, body), = ctx.sent("/generate-hook/")
    assert json.loads(body["captions_json"])[0]["text"] == "שלום לכולם"


def test_a_job_is_only_visible_to_the_account_that_started_it():
    ctx = FakeCtx()
    job_id = run(C._tool_start_video(ctx, {"url": "https://cdn.example.com/a.mp4"}))["job_id"]
    other = FakeCtx()
    other.uid, other.store = "b" * 32, ctx.store
    with pytest.raises(C.ConnectorError, match="Unknown job_id"):
        _status(other, job_id)
    with pytest.raises(C.ConnectorError):
        _status(ctx, "../../etc")


def test_account_and_video_list():
    ctx = FakeCtx(credits=5)
    ctx.me["videos_used"] = 2
    assert run(C._tool_get_account(ctx, {})) == {"account": "tester@example.com", "credits_left": 3, "unlimited": False}
    ctx.me["role"] = "admin"
    assert run(C._tool_get_account(ctx, {}))["unlimited"] is True
    vids = run(C._tool_list_videos(ctx, {}))["videos"]
    assert vids[0]["kind"] == "exported" and vids[0]["download_url"].startswith("https://api.test/download/")


# ── Router wiring (source-level: the router itself needs Modal to run) ────────

def test_router_serves_mcp_before_the_session_guard_and_key_route_after_it():
    src = (ROOT / "app_modal.py").read_text()
    guard = src.index("# ── Session guard — every route below requires a valid token.")
    assert src.index('if path.startswith("/mcp/"):') < guard, "the connector link is its own login"
    assert src.index('"/auth/connector-key"') > guard, "creating a link needs a real session"
    assert "hashlib" in src[src.index('if path.startswith("/mcp/"):'):guard], "keys are looked up by hash"


def test_connector_module_ships_in_every_image():
    core = (ROOT / "pipeline_core.py").read_text()
    assert core.count('"connector_fns"') == 3
