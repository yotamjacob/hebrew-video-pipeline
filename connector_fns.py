"""
Claude connector (remote MCP server) - lets a person drive the pipeline from a
Claude chat: hand over a video LINK, get it cut + captioned, pick a hook, export,
and receive a download link. Contract + rationale: docs/connector.md.

Everything here is a thin layer over the EXISTING HTTP routes: each tool calls
them through an in-process loopback (ctx.call), so credits, ownership checks and
validation stay in exactly one place - the router in app_modal.py.
"""
import modal

from pipeline_core import (
    app, light_image, tmp_vol, TMP_DIR,
)

# Connector keys + per-job state. Not in the metadata backup on purpose: a key
# is re-minted in one click and job state is only useful while its video lives.
mcp_store = modal.Dict.from_name("hebpipe-mcp", create_if_missing=True)

# Same ceilings the upload card states ("up to 1 GB, up to 20 minutes").
CONNECTOR_MAX_BYTES   = 1024 * 1024 * 1024
CONNECTOR_MAX_SECONDS = 20 * 60
CONNECTOR_JOB_TTL     = 30 * 24 * 3600      # matches video retention
MCP_PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")


class ConnectorError(Exception):
    """A failure the person (or Claude) can act on - shown verbatim."""


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested via AST extraction - keep them import-free)
# ---------------------------------------------------------------------------

def _normalize_video_url(url: str) -> str:
    """Turn a share link into a direct-download URL. Raises ValueError on
    anything that is not an https link."""
    import re
    from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
    url = (url or "").strip()
    p = urlparse(url)
    if p.scheme != "https" or not p.hostname:
        raise ValueError("The link must start with https://")
    host = p.hostname.lower()
    if host in ("drive.google.com", "docs.google.com"):
        m = re.search(r"/file/d/([A-Za-z0-9_-]{10,})", p.path)
        fid = m.group(1) if m else (parse_qs(p.query).get("id") or [""])[0]
        if not re.fullmatch(r"[A-Za-z0-9_-]{10,}", fid or ""):
            raise ValueError("This Google Drive link has no file id - share the file itself, not a folder")
        # usercontent + confirm=t skips the "can't scan for viruses" interstitial
        # that the classic uc?export=download link shows for large files.
        return f"https://drive.usercontent.google.com/download?id={fid}&export=download&confirm=t"
    if host in ("www.dropbox.com", "dropbox.com"):
        q = parse_qs(p.query, keep_blank_values=True)
        q["dl"] = ["1"]
        return urlunparse(p._replace(query=urlencode(q, doseq=True)))
    return url


def _ip_is_public(ip: str) -> bool:
    import ipaddress
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return not (a.is_private or a.is_loopback or a.is_link_local or a.is_multicast
                or a.is_reserved or a.is_unspecified)


def _host_is_public(host: str, resolver=None) -> bool:
    """SSRF guard: every address the host resolves to must be public."""
    import socket
    if not host:
        return False
    try:
        infos = (resolver or socket.getaddrinfo)(host, 443)
    except Exception:
        return False
    addrs = {i[4][0] for i in infos}
    return bool(addrs) and all(_ip_is_public(a) for a in addrs)


def _filename_from_headers(headers, fallback: str = "video.mp4") -> str:
    """Best-effort file name from Content-Disposition, sanitized."""
    import re
    from urllib.parse import unquote
    cd = (headers or {}).get("content-disposition") or (headers or {}).get("Content-Disposition") or ""
    m = re.search(r"filename\*=(?:UTF-8'')?([^;]+)", cd, re.I) or re.search(r'filename="?([^";]+)"?', cd, re.I)
    name = unquote(m.group(1)).strip().strip('"') if m else ""
    name = re.sub(r"[\\/\x00-\x1f]", "_", name)[:120].strip(". ")
    return name or fallback


def _clean_hex(color, default: str) -> str:
    import re
    c = str(color or "").strip()
    return c.upper() if re.fullmatch(r"#[0-9a-fA-F]{6}", c) else default


def _apply_caption_edits(captions: list, edits) -> list:
    """Apply [{index, text}] edits. Empty text removes the line. An edited line
    drops its per-word timings (they describe the OLD words), so karaoke falls
    back to even spacing for that line instead of highlighting the wrong word."""
    out = [dict(c) for c in (captions or [])]
    drop = set()
    for e in (edits or []):
        try:
            i = int(e.get("index"))
        except (TypeError, ValueError, AttributeError):
            raise ConnectorError("Each caption edit needs a numeric index")
        if not 0 <= i < len(out):
            raise ConnectorError(f"There is no caption {i} (valid: 0-{len(out) - 1})")
        text = str(e.get("text") or "").strip()
        if not text:
            drop.add(i)
        elif text != out[i].get("text"):
            out[i]["text"] = text
            out[i].pop("words", None)
    return [c for i, c in enumerate(out) if i not in drop]


def _public_captions(captions: list) -> list:
    return [{"index": i, "start": round(float(c.get("start", 0)), 2),
             "end": round(float(c.get("end", 0)), 2), "text": c.get("text", "")}
            for i, c in enumerate(captions or [])]


CONNECTOR_INSTRUCTIONS = (
    "Paypline (פייפליין) edits Hebrew talking-head videos. Typical flow: "
    "1) start_video with an https link to the raw file (Google Drive / Dropbox share link or a direct link; "
    "the link must be viewable by anyone who has it). 2) get_video_status until status is 'ready' - it returns "
    "the Hebrew captions. 3) Optional: generate_hooks, and fix caption typos. 4) export_video to burn captions "
    "(+ hook). 5) get_video_status until 'done' - it returns a download link valid for one hour. "
    "Processing a video spends one of the person's credits (two for videos over 10 minutes); exporting and "
    "re-exporting is free. Always show the person the captions before exporting, and give them the download "
    "link as a clickable link."
)

CONNECTOR_TOOLS = [
    {"name": "start_video",
     "description": ("Import a video from an https link and start processing it (cut silences, transcribe Hebrew "
                     "speech into timed captions). Spends 1 credit (2 if longer than 10 minutes). Returns a job_id; "
                     "then call get_video_status."),
     "inputSchema": {"type": "object", "required": ["url"], "properties": {
         "url": {"type": "string", "description": "https link to the video file (Drive/Dropbox share link or direct)"},
         "name": {"type": "string", "description": "Optional display name for the video"},
         "cut_silences": {"type": "boolean", "description": "Remove pauses and filler (default true)"},
         "captions": {"type": "boolean", "description": "Transcribe and prepare captions (default true). "
                                                         "false = only cut silences; the result is ready to download"},
         "enhance_audio": {"type": "boolean", "description": "Noise reduction (default true)"}}}},
    {"name": "get_video_status",
     "description": ("Check a job. Waits up to ~35 seconds for progress before answering. status is one of: "
                     "fetching, processing, ready (captions returned - call export_video), exporting, "
                     "done (download_url returned), error."),
     "inputSchema": {"type": "object", "required": ["job_id"], "properties": {
         "job_id": {"type": "string"}}}},
    {"name": "generate_hooks",
     "description": ("Write 3 Hebrew opening-hook options for a job whose status is 'ready' or 'done'. "
                     "A hook is a short line shown at the top of the video for the first seconds."),
     "inputSchema": {"type": "object", "required": ["job_id"], "properties": {
         "job_id": {"type": "string"}}}},
    {"name": "export_video",
     "description": ("Burn captions (and optionally a hook) into the processed video. Free; can be repeated with "
                     "different options. Then call get_video_status for the download link."),
     "inputSchema": {"type": "object", "required": ["job_id"], "properties": {
         "job_id": {"type": "string"},
         "style": {"type": "string", "enum": ["classic", "karaoke", "word"],
                   "description": "classic = full lines (default); karaoke = line with the spoken word highlighted; "
                                  "word = one word at a time"},
         "caption_edits": {"type": "array", "description": "Corrections by caption index; empty text deletes the line",
                           "items": {"type": "object", "required": ["index", "text"], "properties": {
                               "index": {"type": "integer"}, "text": {"type": "string"}}}},
         "hook_text": {"type": "string", "description": "Hook line to show at the top (omit for no hook)"},
         "hook_seconds": {"type": "number", "description": "How long the hook stays (default 3)"},
         "font": {"type": "string", "enum": ["Heebo", "Assistant", "Rubik", "Secular One", "Suez One",
                                               "Frank Ruhl Libre", "Karantina", "Miriam Libre"]},
         "font_size": {"type": "integer", "description": "Caption size in px, 24-200 (default 48)"},
         "text_color": {"type": "string", "description": "Hex like #FFFFFF"},
         "highlight_color": {"type": "string", "description": "Karaoke highlight, hex like #FFD400"}}}},
    {"name": "list_videos",
     "description": "List the person's finished videos (last 30 days) with download links valid for one hour.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "get_account",
     "description": "Who is connected and how many video credits are left.",
     "inputSchema": {"type": "object", "properties": {}}},
]


# ---------------------------------------------------------------------------
# Tool implementations. `ctx` is built by the router (app_modal.py):
#   ctx.uid / ctx.uprefix / ctx.base_url
#   await ctx.call(method, path, query=None, body=None) -> (status, parsed_json)
#   ctx.spawn_fetch(url, dest_key) -> call_id      ctx.poll(call_id) -> (result, still_running)
#   ctx.store  (dict-like: get / __setitem__)
# ---------------------------------------------------------------------------

def _job_key(ctx, job_id: str) -> str:
    return f"job:{ctx.uid}:{job_id}"


def _load_job(ctx, job_id) -> dict:
    import re
    jid = str(job_id or "").strip()
    job = ctx.store.get(_job_key(ctx, jid)) if re.fullmatch(r"[a-f0-9]{12}", jid) else None
    if not isinstance(job, dict):
        raise ConnectorError("Unknown job_id - start a video with start_video first")
    return job


def _save_job(ctx, job: dict) -> None:
    ctx.store[_job_key(ctx, job["id"])] = job


async def _download_url(ctx, key: str, name: str) -> str:
    from urllib.parse import quote
    st, data = await ctx.call("GET", "/auth/media-token")
    tok = (data or {}).get("token") if st == 200 else None
    if not tok:
        raise ConnectorError("Could not create a download link - try again")
    return f"{ctx.base_url}/download/{key}/?filename={quote(name)}&token={quote(tok)}"


def _err_text(data, fallback: str) -> str:
    msg = (data or {}).get("error") if isinstance(data, dict) else None
    return {"limit_reached": "No video credits left - buy credits in the app, then try again",
            "upscale_too_long": "This video is too long for the AI upscale"}.get(msg, msg or fallback)


async def _tool_get_account(ctx, args) -> dict:
    st, me = await ctx.call("GET", "/auth/me")
    if st != 200:
        raise ConnectorError("This connection is no longer valid - create a new link in the app")
    limit, used = me.get("video_limit"), int(me.get("videos_used") or 0)
    unlimited = me.get("role") == "admin" or limit is None or int(limit) < 0
    return {"account": me.get("username"),
            "credits_left": None if unlimited else max(0, int(limit) - used),
            "unlimited": bool(unlimited)}


async def _tool_start_video(ctx, args) -> dict:
    import time, uuid
    try:
        url = _normalize_video_url(args.get("url"))
    except ValueError as e:
        raise ConnectorError(str(e))
    acct = await _tool_get_account(ctx, {})
    if not acct["unlimited"] and acct["credits_left"] < 1:
        raise ConnectorError("No video credits left - buy credits in the app, then try again")
    job_id = uuid.uuid4().hex[:12]
    upload_key = "mcp" + uuid.uuid4().hex
    job = {"id": job_id, "stage": "fetching", "ts": time.time(), "upload_key": upload_key,
           "name": str(args.get("name") or "").strip()[:80],
           "opts": {"cut_silences": args.get("cut_silences") is not False,
                    "captions": args.get("captions") is not False,
                    "enhance_audio": args.get("enhance_audio") is not False}}
    job["fetch_call"] = ctx.spawn_fetch(url, ctx.uprefix + upload_key)
    _save_job(ctx, job)
    return {"job_id": job_id, "status": "fetching",
            "next": "Call get_video_status with this job_id (downloading the file can take a minute)."}


async def _advance_job(ctx, job: dict) -> bool:
    """Move the job one step forward if its current stage finished. Returns True
    when the stage changed."""
    from urllib.parse import urlencode
    stage = job.get("stage")
    if stage == "fetching":
        try:
            res, running = ctx.poll(job["fetch_call"])
        except Exception as e:
            job.update(stage="error", error=f"Could not download the file ({str(e)[:120]})")
            return True
        if running:
            return False
        if not (isinstance(res, dict) and res.get("ok")):
            job.update(stage="error", error=(res or {}).get("error") or "Could not download the file")
            return True
        name = job.get("name") or res.get("filename") or "video.mp4"
        if "." not in name[-6:]:
            name += ".mp4"
        job.update(name=name, duration=res.get("duration"), is_audio=bool(res.get("is_audio")))
        o = job["opts"]
        q = urlencode({"key": job["upload_key"], "filename": name,
                       "cut_silences": str(o["cut_silences"]).lower(),
                       "burn_captions": str(o["captions"]).lower(),
                       "enhance_audio": str(o["enhance_audio"]).lower(),
                       "is_audio": str(job["is_audio"]).lower(),
                       "duration": f"{float(res.get('duration') or 0):.2f}"})
        st, data = await ctx.call("POST", "/process/", query=q)
        if st != 202 or not (data or {}).get("call_id"):
            job.update(stage="error", error=_err_text(data, "Could not start processing"))
        else:
            job.update(stage="processing", process_call=data["call_id"])
        return True
    if stage == "processing":
        st, data = await ctx.call("GET", f"/process_poll/{job['process_call']}/",
                                  query=urlencode({"key": job["upload_key"], "filename": job["name"]}))
        if st == 202:
            prog = ((data or {}).get("progress") or {}).get("stage")
            changed = prog != job.get("progress")
            job["progress"] = prog
            return False if not changed else None   # None = progress note only, keep waiting
        if st != 200 or not (data or {}).get("video_key"):
            job.update(stage="error", error=_err_text(data, "Processing failed") + " (the credit was refunded)")
            return True
        job.update(video_key=data["video_key"], captions=data.get("captions") or [])
        # Cut-only runs (and videos with no speech) have nothing to burn: the
        # processed file itself is the result.
        job["stage"] = "ready" if job["captions"] else "done"
        if job["stage"] == "done":
            job["output_key"] = data["video_key"]
        return True
    if stage == "exporting":
        st, data = await ctx.call("GET", f"/burn_poll/{job['burn_call']}/")
        if st == 202:
            return False
        if st != 200 or not (data or {}).get("output_key"):
            job.update(stage="ready", last_export_error=_err_text(data, "Export failed"))
            return True
        job.update(stage="done", output_key=data["output_key"])
        job.pop("last_export_error", None)
        return True
    return False


async def _job_view(ctx, job: dict) -> dict:
    stage = job.get("stage")
    out = {"job_id": job["id"], "status": stage, "name": job.get("name") or None}
    if stage == "processing" and job.get("progress"):
        out["progress"] = job["progress"]
    if stage == "error":
        out["error"] = job.get("error")
    if stage in ("ready", "done") and job.get("captions"):
        out["captions"] = _public_captions(job["captions"])
    if stage == "ready":
        out["next"] = "Show the captions, then call export_video (optionally generate_hooks first)."
        if job.get("last_export_error"):
            out["last_export_error"] = job["last_export_error"]
    if stage == "done":
        stem = (job.get("name") or "video").rsplit(".", 1)[0]
        ext = job["output_key"].rsplit(".", 1)[-1]
        out["download_url"] = await _download_url(ctx, job["output_key"], f"{stem}_edited.{ext}")
        out["download_url_valid_for"] = "1 hour - call get_video_status again for a fresh link"
    if stage in ("fetching", "processing", "exporting"):
        out["next"] = "Still working - call get_video_status again."
    return out


async def _tool_get_video_status(ctx, args, wait_s: float = 35.0, tick: float = 4.0) -> dict:
    import asyncio, time
    job = _load_job(ctx, args.get("job_id"))
    deadline = time.time() + wait_s
    while True:
        changed = await _advance_job(ctx, job)
        if changed is not False:
            _save_job(ctx, job)
        # Chain straight on (fetched -> processing) but answer as soon as there
        # is something the person can act on.
        if job["stage"] in ("ready", "done", "error") or time.time() >= deadline:
            break
        if changed is True:
            continue
        await asyncio.sleep(tick)
    return await _job_view(ctx, job)


async def _tool_generate_hooks(ctx, args, wait_s: float = 45.0, tick: float = 3.0) -> dict:
    import asyncio, json, time
    job = _load_job(ctx, args.get("job_id"))
    if not job.get("captions"):
        raise ConnectorError("Hooks need a processed video with captions (status 'ready')")
    if not job.get("hook_call"):
        st, data = await ctx.call("POST", "/generate-hook/", body={
            "captions_json": json.dumps(job["captions"], ensure_ascii=False), "video_key": job["video_key"]})
        if st != 202 or not (data or {}).get("call_id"):
            raise ConnectorError(_err_text(data, "Could not start hook writing"))
        job["hook_call"] = data["call_id"]
        _save_job(ctx, job)
    deadline = time.time() + wait_s
    while True:
        st, data = await ctx.call("GET", f"/generate-hook-poll/{job['hook_call']}/")
        if st != 202:
            break
        if time.time() >= deadline:
            return {"status": "running", "next": "Still writing - call generate_hooks again."}
        await asyncio.sleep(tick)
    job.pop("hook_call", None)
    _save_job(ctx, job)
    if st != 200:
        raise ConnectorError(_err_text(data, "Hook writing failed"))
    hooks = [h for h in (data or {}).get("hooks") or [] if isinstance(h, dict) and h.get("text")]
    if not hooks:
        raise ConnectorError("No hook ideas came back for this video - write one yourself and pass it as hook_text")
    return {"hooks": [{"text": h["text"], **({"why": h["rationale"]} if h.get("rationale") else {})} for h in hooks],
            "next": "Let the person pick one, then pass it to export_video as hook_text."}


async def _tool_export_video(ctx, args) -> dict:
    from urllib.parse import urlencode
    job = _load_job(ctx, args.get("job_id"))
    if job.get("stage") == "exporting":
        raise ConnectorError("An export is already running - call get_video_status")
    if job.get("stage") not in ("ready", "done") or not job.get("captions"):
        raise ConnectorError("Nothing to export yet - wait until get_video_status says 'ready'")
    style = args.get("style") or "classic"
    if style not in ("classic", "karaoke", "word"):
        raise ConnectorError("style must be classic, karaoke or word")
    captions = _apply_caption_edits(job["captions"], args.get("caption_edits"))
    if not captions:
        raise ConnectorError("All captions were removed - nothing to burn")
    try:
        font_size = max(24, min(200, int(args.get("font_size") or 48)))
    except (TypeError, ValueError):
        font_size = 48
    fonts = ("Heebo", "Assistant", "Rubik", "Secular One", "Suez One", "Frank Ruhl Libre", "Karantina", "Miriam Libre")
    font = args.get("font") if args.get("font") in fonts else "Heebo"
    body = {"captions": captions, "broll": [],
            # Same defaults the web editor sends (_captionStylePayload in site/app.js).
            "caption_style": {"font_color": _clean_hex(args.get("text_color"), "#FFFFFF"),
                              "border_color": "#000000", "border_size": 2,
                              "bg_color": "#000000", "bg_opacity": 0, "mode": style,
                              "highlight_color": _clean_hex(args.get("highlight_color"), "#FFD400"),
                              "progress_bar": False, "auto_zoom": False}}
    hook_text = str(args.get("hook_text") or "").strip()
    if hook_text:
        try:
            hook_s = max(1.0, min(15.0, float(args.get("hook_seconds") or 3)))
        except (TypeError, ValueError):
            hook_s = 3.0
        # No `lines`: the server wraps the text itself when the editor preview
        # did not (build_caption_ass).
        body["hook"] = {"text": hook_text[:200], "font": "Heebo", "font_color": "#FFFFFF",
                        "bg_color": "#000000", "bg_opacity": 0.6, "font_size_pct": 100,
                        "border_color": "#000000", "border_size": 0, "start_seconds": 0.0,
                        "duration_seconds": hook_s, "vertical_position": 10}
    q = urlencode({"video_key": job["video_key"], "filename": job.get("name") or "video.mp4",
                   "font": font, "font_size": font_size, "margin_v": "0.0800"})
    st, data = await ctx.call("POST", "/burn/", query=q, body=body)
    if st != 202 or not (data or {}).get("call_id"):
        raise ConnectorError(_err_text(data, "Could not start the export"))
    job.update(stage="exporting", burn_call=data["call_id"], captions=captions)
    _save_job(ctx, job)
    return {"job_id": job["id"], "status": "exporting",
            "next": "Call get_video_status for the download link (usually under a minute)."}


async def _tool_list_videos(ctx, args) -> dict:
    import datetime
    st, data = await ctx.call("GET", "/jobs/")
    if st != 200:
        raise ConnectorError("Could not read the video list")
    out = []
    for j in ((data or {}).get("jobs") or [])[:25]:
        key, name = j.get("key", ""), j.get("name") or "video"
        stem, ext = name.rsplit(".", 1)[0], key.rsplit(".", 1)[-1]
        out.append({"name": name,
                    "kind": "exported" if key.endswith("_out.mp4") else "processed (no captions burned)",
                    "created": datetime.datetime.fromtimestamp(j.get("ts") or 0, datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
                    "seconds": j.get("duration") or 0,
                    "download_url": await _download_url(ctx, key, f"{stem}_edited.{ext}")})
    return {"videos": out, "note": "Links are valid for one hour. Videos are kept for 30 days."}


_CONNECTOR_IMPL = {
    "start_video": _tool_start_video, "get_video_status": _tool_get_video_status,
    "generate_hooks": _tool_generate_hooks, "export_video": _tool_export_video,
    "list_videos": _tool_list_videos, "get_account": _tool_get_account,
}


async def _mcp_handle(msg, ctx, impl=None, tools=None):
    """One JSON-RPC message in, one response dict out (None for notifications)."""
    import json
    impl = _CONNECTOR_IMPL if impl is None else impl
    tools = CONNECTOR_TOOLS if tools is None else tools
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or not isinstance(msg.get("method"), str):
        return {"jsonrpc": "2.0", "id": (msg or {}).get("id") if isinstance(msg, dict) else None,
                "error": {"code": -32600, "message": "Invalid Request"}}
    mid, method, params = msg.get("id"), msg["method"], msg.get("params") or {}
    if "id" not in msg:                      # notification - never answered
        return None

    def ok(result):
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    if method == "initialize":
        want = params.get("protocolVersion")
        return ok({"protocolVersion": want if want in MCP_PROTOCOL_VERSIONS else MCP_PROTOCOL_VERSIONS[0],
                   "capabilities": {"tools": {"listChanged": False}},
                   "serverInfo": {"name": "paypline", "title": "Paypline - Hebrew video editing", "version": "1.0.0"},
                   "instructions": CONNECTOR_INSTRUCTIONS})
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": tools})
    if method == "tools/call":
        fn = impl.get(params.get("name"))
        if not fn:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": "Unknown tool"}}
        args = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        try:
            res, is_err = await fn(ctx, args), False
        except ConnectorError as e:
            res, is_err = {"error": str(e)}, True
        except Exception as e:               # never leak internals to the chat
            print(f"[connector] {params.get('name')} failed: {e!r}")
            res, is_err = {"error": "Something went wrong on the server - try again in a minute"}, True
        return ok({"content": [{"type": "text", "text": json.dumps(res, ensure_ascii=False)}], "isError": is_err})
    if method in ("resources/list", "prompts/list"):
        return ok({method.split("/")[0]: []})
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "Method not found"}}


# ---------------------------------------------------------------------------
# Server-side fetch: pull the linked file onto the pipeline volume as
# `{dest_key}_src.mp4` - the single-file form process_video already accepts.
# ---------------------------------------------------------------------------

@app.function(image=light_image, timeout=1500, volumes={TMP_DIR: tmp_vol})
def fetch_url_fn(url: str, dest_key: str) -> dict:
    import json, subprocess
    from pathlib import Path
    from urllib.parse import urlparse, urljoin
    import requests

    dst = Path(TMP_DIR) / f"{dest_key}_src.mp4"
    sess, cur, resp = requests.Session(), url, None
    try:
        for _hop in range(6):
            p = urlparse(cur)
            # Re-checked on EVERY hop: a public link may redirect inward.
            if p.scheme != "https" or not _host_is_public(p.hostname):
                return {"ok": False, "error": "This link cannot be fetched (only public https links are allowed)"}
            resp = sess.get(cur, stream=True, timeout=(10, 60), allow_redirects=False,
                            headers={"User-Agent": "Mozilla/5.0 (compatible; Paypline-connector/1.0)"})
            if resp.status_code in (301, 302, 303, 307, 308) and resp.headers.get("location"):
                cur = urljoin(cur, resp.headers["location"])
                resp.close()
                resp = None
                continue
            break
        if resp is None:
            return {"ok": False, "error": "The link redirects too many times"}
        if resp.status_code != 200:
            return {"ok": False, "error": f"The link answered HTTP {resp.status_code} - check that it is shared with "
                                          "'anyone with the link'"}
        if (resp.headers.get("content-type") or "").lower().startswith("text/html"):
            return {"ok": False, "error": "The link opens a web page, not a file - share the file itself with "
                                          "'anyone with the link'"}
        total = 0
        with open(dst, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                total += len(chunk)
                if total > CONNECTOR_MAX_BYTES:
                    f.close()
                    dst.unlink(missing_ok=True)
                    return {"ok": False, "error": "The file is larger than 1 GB"}
                f.write(chunk)
        if total < 1024:
            dst.unlink(missing_ok=True)
            return {"ok": False, "error": "The link returned an empty file"}
        pr = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type",
                             "-of", "json", str(dst)], capture_output=True, text=True, timeout=120)
        try:
            info = json.loads(pr.stdout or "{}")
            kinds = {s.get("codec_type") for s in info.get("streams") or []}
            duration = float((info.get("format") or {}).get("duration") or 0)
        except Exception:
            kinds, duration = set(), 0.0
        if "audio" not in kinds and "video" not in kinds:
            dst.unlink(missing_ok=True)
            return {"ok": False, "error": "The file is not a video or audio file"}
        if "audio" not in kinds:
            dst.unlink(missing_ok=True)
            return {"ok": False, "error": "The video has no sound - there is nothing to transcribe"}
        if duration > CONNECTOR_MAX_SECONDS + 5:
            dst.unlink(missing_ok=True)
            return {"ok": False, "error": "The video is longer than 20 minutes"}
        tmp_vol.commit()
        return {"ok": True, "bytes": total, "duration": round(duration, 2), "is_audio": "video" not in kinds,
                "filename": _filename_from_headers(dict(resp.headers))}
    except requests.RequestException as e:
        dst.unlink(missing_ok=True)
        return {"ok": False, "error": f"Could not download the file ({type(e).__name__})"}
    finally:
        if resp is not None:
            resp.close()
