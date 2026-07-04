"""B-roll item validation — the guard on the /burn route (and the burn worker)
that stops a crafted broll item from reaching an arbitrary file (path
traversal / another tenant's video) or making the worker fetch an arbitrary
URL (SSRF).

Legitimate broll clips are either local `broll_<uuid>.mp4` files on the shared
volume, or remote stock clips from a small set of trusted hosts. Anything else
must be rejected.
"""
import re as _re_mod

from tests.backend.conftest import MODAL_SRC, _extract_fn, _build_ns

_ns = _build_ns()
# The extracted functions reference these module globals — pull them straight
# from source so the test can't drift from the real values.
_ns["_BROLL_KEY_RE"] = _re_mod.compile(
    _re_mod.search(r"_BROLL_KEY_RE = _re\.compile\(r'([^']+)'\)", MODAL_SRC).group(1))
_ns["_BROLL_URL_HOSTS"] = eval(
    _re_mod.search(r"_BROLL_URL_HOSTS = (\([^)]*\))", MODAL_SRC).group(1))
_fns = _extract_fn(MODAL_SRC, "_is_allowed_broll_url", "_broll_item_safe", extra_ns=_ns)
_is_allowed_broll_url = _fns["_is_allowed_broll_url"]
_broll_item_safe      = _fns["_broll_item_safe"]

_GOOD_KEY = "broll_" + ("a" * 32) + ".mp4"


# ── URL allowlist (SSRF guard) ───────────────────────────────────────────────

def test_trusted_stock_hosts_allowed():
    for url in [
        "https://videos.pexels.com/video-files/123/clip.mp4",
        "https://cdn.pixabay.com/video/2024/clip.mp4",
        "https://i.vimeocdn.com/video/x.mp4",
        "https://player.vimeo.com/external/1.mp4",
        "https://pexels.com/a.mp4",
    ]:
        assert _is_allowed_broll_url(url), url

def test_internal_and_metadata_urls_rejected():
    for url in [
        "http://169.254.169.254/latest/meta-data/",
        "https://169.254.169.254/latest/meta-data/",
        "http://localhost/secret",
        "https://127.0.0.1/x",
        "http://metadata.google.internal/computeMetadata/v1/",
        "https://internal.corp/x",
    ]:
        assert not _is_allowed_broll_url(url), url

def test_non_https_rejected():
    assert not _is_allowed_broll_url("http://videos.pexels.com/clip.mp4")

def test_host_suffix_spoof_rejected():
    # A hostname that merely ends with a lookalike must not slip through.
    assert not _is_allowed_broll_url("https://pexels.com.evil.com/clip.mp4")
    assert not _is_allowed_broll_url("https://notpexels.com/clip.mp4")
    assert not _is_allowed_broll_url("https://evilpexels.com/clip.mp4")

def test_non_url_inputs_rejected():
    for bad in [None, "", 123, "not a url", "ftp://pexels.com/x"]:
        assert not _is_allowed_broll_url(bad)


# ── Item validation (combines key + URL rules, mirrors worker precedence) ─────

def test_local_broll_key_allowed():
    assert _broll_item_safe({"video_key": _GOOD_KEY, "start": 0, "end": 3})

def test_path_traversal_key_rejected():
    for vk in [
        "../../etc/passwd",
        "/etc/passwd",
        "broll_" + ("a" * 32) + ".mp4/../../etc/passwd",
        "../broll_" + ("a" * 32) + ".mp4",
    ]:
        assert not _broll_item_safe({"video_key": vk, "start": 0}), vk

def test_cross_tenant_video_key_rejected():
    # Another tenant's processed video is NOT a broll_<uuid>.mp4 key.
    victim = "u" + ("f" * 32) + "__" + ("b" * 32) + "_cut.mp4"
    assert not _broll_item_safe({"video_key": victim, "start": 0})

def test_remote_stock_item_allowed():
    assert _broll_item_safe({"download_url": "https://videos.pexels.com/x.mp4", "start": 0})
    assert _broll_item_safe({"preview_url": "https://cdn.pixabay.com/x.mp4", "start": 0})

def test_ssrf_item_rejected():
    assert not _broll_item_safe({"download_url": "http://169.254.169.254/", "start": 0})
    assert not _broll_item_safe({"preview_url": "https://internal.corp/x", "start": 0})

def test_bad_key_wins_over_url():
    # The worker uses video_key when present, so a malicious key must be
    # rejected even if a legitimate-looking URL is also supplied.
    assert not _broll_item_safe({
        "video_key": "/etc/passwd",
        "download_url": "https://videos.pexels.com/x.mp4",
        "start": 0,
    })

def test_sourceless_item_allowed():
    # No key and no URL — the worker skips it, so it's harmless.
    assert _broll_item_safe({"start": 0, "end": 3})

def test_non_dict_item_rejected():
    for bad in [None, "x", 5, ["video_key"]]:
        assert not _broll_item_safe(bad)
