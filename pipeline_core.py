"""
Hebrew Video Pipeline — shared Modal app, images, volumes, constants,
and pure helpers (security, RTL text, rate limiting, Veo-free).

Imported by every other backend module. No @app.function lives here.
"""


import modal

# ---------------------------------------------------------------------------
# Modal image
# ---------------------------------------------------------------------------
image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("ffmpeg", "git", "libsndfile1", "fontconfig", "wget")
    # CUDA torch: needed by the Real-ESRGAN video-upscale pass (fp16 on the L4).
    # DeepFilterNet's Python layer also uses torch (CPU path, CUDA build is a
    # superset); Whisper is unaffected (CTranslate2 brings its own CUDA).
    .pip_install("torch", "torchaudio", extra_options="--index-url https://download.pytorch.org/whl/cu124")
    .pip_install(
        "faster-whisper>=1.0.0",
        "nvidia-cublas-cu12",
        "nvidia-cudnn-cu12",
        "deepfilternet>=0.5",
        "soundfile",
        "requests",
        "fastapi",
        "python-multipart",
        "google-genai>=1.9.0",
        "anthropic>=0.40.0",
    )
    .run_commands(
        "mkdir -p /usr/local/share/fonts/hebrew",
        'wget -q "https://github.com/google/fonts/raw/main/ofl/heebo/Heebo%5Bwght%5D.ttf" -O /usr/local/share/fonts/hebrew/Heebo.ttf',
        'wget -q "https://github.com/google/fonts/raw/main/ofl/assistant/Assistant%5Bwght%5D.ttf" -O /usr/local/share/fonts/hebrew/Assistant.ttf',
        'wget -q "https://github.com/google/fonts/raw/main/ofl/frankruhllibre/FrankRuhlLibre%5Bwght%5D.ttf" -O /usr/local/share/fonts/hebrew/FrankRuhlLibre.ttf',
        'wget -q "https://github.com/google/fonts/raw/main/ofl/secularone/SecularOne-Regular.ttf" -O /usr/local/share/fonts/hebrew/SecularOne.ttf',
        "apt-get install -y fonts-noto-hinted",
        "fc-cache -f /usr/local/share/fonts/hebrew",
    )
    # Local backend modules must be shipped explicitly (Modal 1.x no longer automounts imports)
    .add_local_python_source(
        "pipeline_core", "pipeline_fns", "stock_helpers",
        "broll_fns", "content_fns", "metricool_fns",
    )
)

# Lightweight image for the caption-burn worker — no ML packages, just ffmpeg + fonts.
# Cold-starts in ~20 s vs 3-5 min for the full ML image.
burn_image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("ffmpeg", "fontconfig", "wget")
    .pip_install("requests")
    .run_commands(
        "mkdir -p /usr/local/share/fonts/hebrew",
        'wget -q "https://github.com/google/fonts/raw/main/ofl/heebo/Heebo%5Bwght%5D.ttf" -O /usr/local/share/fonts/hebrew/Heebo.ttf',
        'wget -q "https://github.com/google/fonts/raw/main/ofl/assistant/Assistant%5Bwght%5D.ttf" -O /usr/local/share/fonts/hebrew/Assistant.ttf',
        'wget -q "https://github.com/google/fonts/raw/main/ofl/frankruhllibre/FrankRuhlLibre%5Bwght%5D.ttf" -O /usr/local/share/fonts/hebrew/FrankRuhlLibre.ttf',
        'wget -q "https://github.com/google/fonts/raw/main/ofl/secularone/SecularOne-Regular.ttf" -O /usr/local/share/fonts/hebrew/SecularOne.ttf',
        "fc-cache -f /usr/local/share/fonts/hebrew",
    )
    .add_local_python_source(
        "pipeline_core", "pipeline_fns", "stock_helpers",
        "broll_fns", "content_fns", "metricool_fns",
    )
)

# Lightweight image for Claude-API workers (hooks, captions, stock B-roll) and the
# ASGI router — ffmpeg for frame sampling/thumbnails + HTTP clients, no ML packages.
# Cold-starts in seconds vs minutes for the full ML image.
light_image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("ffmpeg")
    .pip_install("requests", "anthropic>=0.40.0", "fastapi", "python-multipart")
    .add_local_python_source(
        "pipeline_core", "pipeline_fns", "stock_helpers",
        "broll_fns", "content_fns", "metricool_fns",
    )
)

app = modal.App("hebrew-video-pipeline", image=image)

model_volume = modal.Volume.from_name("heb-whisper-model", create_if_missing=True)
MODEL_DIR = "/model-cache"
WHISPER_MODEL = "ivrit-ai/whisper-large-v3-turbo-ct2"

tmp_vol = modal.Volume.from_name("hebrew-pipeline-tmp", create_if_missing=True)
TMP_DIR = "/pipeline-tmp"

# Job history manifest — key = burned output key, value = {name, ts, size, duration}
jobs_store = modal.Dict.from_name("hebpipe-jobs", create_if_missing=True)
JOB_RETENTION_DAYS      = 30   # burned outputs kept this long for the History tab
SCRATCH_RETENTION_HOURS = 48   # _src/_words/_audio/_cut scratch files kept this long

# Live processing progress — key = upload key, value = {stage, done:{step: secs}}.
# Written by process_video at real stage transitions, read by /process_poll,
# deleted when the job finishes. Drives the site's checklist with real times.
progress_store = modal.Dict.from_name("hebpipe-progress", create_if_missing=True)

# ---------------------------------------------------------------------------
# User accounts & sessions
#
# Identity is reduced to a single opaque `uid` (random hex). Every volume key
# a user touches is prefixed `u{uid}__` by the API router — workers derive
# output-key prefixes from their input keys, so isolation is structural.
# Tokens are stateless HMAC-signed `uid.expiry.sig`; the signing secret lives
# in the `hebpipe-auth` Modal Secret (AUTH_SECRET + INVITE_CODE).
# Migration note: to move to a managed provider (e.g. Supabase), swap
# _verify_token for provider-JWT verification mapping to the same uid.
# ---------------------------------------------------------------------------
users_store = modal.Dict.from_name("hebpipe-users", create_if_missing=True)   # username → {uid, salt, pw, created}
calls_store = modal.Dict.from_name("hebpipe-calls", create_if_missing=True)   # call_id  → {uid, ts}

import re as _auth_re

_USERNAME_RE    = _auth_re.compile(r"^[a-zA-Z0-9_\-]{3,32}$")
_UID_RE         = _auth_re.compile(r"^[0-9a-f]{32}$")
_UID_PREFIX_RE  = _auth_re.compile(r"^u[0-9a-f]{32}__")
TOKEN_TTL_SECONDS       = 30 * 24 * 3600
CALL_RETENTION_SECONDS  = 24 * 3600


def _hash_password(password: str, salt_hex: str = None):
    """PBKDF2-HMAC-SHA256, 600k iterations (stdlib) — returns (salt_hex, hash_hex)."""
    import hashlib, secrets
    salt = bytes.fromhex(salt_hex) if salt_hex else secrets.token_bytes(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 600_000)
    return salt.hex(), h.hex()


def _verify_password(password: str, salt_hex: str, hash_hex: str) -> bool:
    import hmac
    _, h = _hash_password(password, salt_hex)
    return hmac.compare_digest(h, hash_hex)


def _sign_token(uid: str, secret: str, ttl: int = TOKEN_TTL_SECONDS, now: float = None) -> str:
    import hmac, hashlib, time
    exp = int((now if now is not None else time.time()) + ttl)
    msg = f"{uid}.{exp}"
    sig = hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()
    return f"{msg}.{sig}"


def _verify_token(token: str, secret: str, now: float = None):
    """Return uid for a valid, unexpired token — else None."""
    import hmac, hashlib, time, re
    try:
        uid, exp_s, sig = token.split(".")
        expect = hmac.new(secret.encode(), f"{uid}.{exp_s}".encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expect):
            return None
        if (now if now is not None else time.time()) > int(exp_s):
            return None
        if not re.match(r"^[0-9a-f]{32}$", uid):
            return None
        return uid
    except Exception:
        return None


# Short-lived token used ONLY in GET media URLs (img/video src, downloads) so
# the long-lived session token stays out of query strings, access logs and
# browser history. Format `m.<uid>.<exp>.<sig>` (4 parts) is disjoint from the
# 3-part session token, so the two verifiers can never be confused.
MEDIA_TOKEN_TTL_SECONDS = 3600


def _sign_media_token(uid: str, secret: str, ttl: int = MEDIA_TOKEN_TTL_SECONDS, now: float = None) -> str:
    import hmac, hashlib, time
    exp = int((now if now is not None else time.time()) + ttl)
    msg = f"m.{uid}.{exp}"
    sig = hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()
    return f"{msg}.{sig}"


def _verify_media_token(token: str, secret: str, now: float = None):
    """Return uid for a valid, unexpired media token — else None."""
    import hmac, hashlib, time, re
    try:
        prefix, uid, exp_s, sig = token.split(".")
        if prefix != "m":
            return None
        expect = hmac.new(secret.encode(), f"m.{uid}.{exp_s}".encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expect):
            return None
        if (now if now is not None else time.time()) > int(exp_s):
            return None
        if not re.match(r"^[0-9a-f]{32}$", uid):
            return None
        return uid
    except Exception:
        return None


def _user_prefix(uid: str) -> str:
    return f"u{uid}__"


def _owned_key(key: str, uid: str) -> bool:
    """True iff key belongs to uid. The only path to a user's data."""
    return isinstance(key, str) and key.startswith(_user_prefix(uid))


# ── Video quota (free tier) ──
# Each /process run consumes one credit. Admins are unlimited; a user is an
# admin if their username is in the ADMIN_USERS env var (comma-separated, in
# the hebpipe-auth secret) OR their record has role == "admin".
DEFAULT_VIDEO_LIMIT = 5


def _quota_state(rec: dict, admin_users: str = None, username: str = None):
    """(is_admin, used, limit) for a user record. limit -1 = unlimited."""
    admins = {u.strip().lower() for u in (admin_users or "").split(",") if u.strip()}
    is_admin = bool(username and username.lower() in admins) or (rec or {}).get("role") == "admin"
    used = int((rec or {}).get("videos_used", 0) or 0)
    limit = (rec or {}).get("video_limit", DEFAULT_VIDEO_LIMIT)
    limit = -1 if limit is None else int(limit)
    return is_admin, used, limit


def _quota_allows(is_admin: bool, used: int, limit: int) -> bool:
    return is_admin or limit < 0 or used < limit

TRANSCRIPT_ANALYSIS_MODEL   = "gemini-2.5-flash"
IMAGE_GENERATION_MODEL      = "gemini-3.1-flash-image-preview"
VIDEO_GENERATION_MODEL      = "veo-3.0-generate-001"
VIDEO_GENERATION_MODEL_FAST = "veo-3.0-fast-generate-001"

SONNET_MODEL = "claude-sonnet-5"
HAIKU_MODEL  = "claude-haiku-4-5-20251001"
OPUS_MODEL   = "claude-opus-4-7"

# Scoring temperatures (Haiku only — Sonnet 5 rejects sampling params)
HAIKU_SCORING_TEMPERATURE  = 0.2   # consistent judgment across clips
HAIKU_SANITY_TEMPERATURE   = 0.1   # deterministic override decisions

# Haiku scoring batch config
HAIKU_SCORING_MAX_TOKENS   = 4096  # sufficient for 8 clips with full reasons
HAIKU_SCORING_BATCH_SIZE   = 8     # max clips per Haiku scoring call
HAIKU_SCORING_CONCURRENCY  = 5     # parallel batch calls

# Disqualify sanity-check config
HAIKU_SANITY_MAX_TOKENS    = 256   # per-clip override check
HAIKU_SANITY_MIN_SCORE     = 7     # clips at or above this trigger override check

# Multi-frame scoring config (Phase 3 — video_context present)
HAIKU_MULTIFRAME_BATCH_SIZE   = 3     # 3 clips × 4 frames = 12 images per call
HAIKU_MULTIFRAME_MAX_TOKENS   = 1024  # 3 clips × ~300 token reasoning + JSON

# ---------------------------------------------------------------------------
# Security helpers
# ---------------------------------------------------------------------------
import re as _re
import collections as _collections
import threading as _threading

_SAFE_KEY_RE = _re.compile(r'^[a-zA-Z0-9_\-]{1,128}$')
_SAFE_DOWNLOAD_KEY_RE = _re.compile(r'^[a-zA-Z0-9_\-\.]{1,128}$')

# ── B-roll item validation (SSRF / IDOR / path-traversal guard) ──
# Generated & stock B-roll clips are stored on the shared volume as
# `broll_<uuid>.mp4` (see broll_fns) — they are NOT u{uid}__-namespaced, so a
# strict format match (not _owned_key) is what stops a burn request from
# escaping to an arbitrary file or another tenant's video. Remote clips carry a
# stock URL that the burn worker fetches server-side; only trusted hosts are
# allowed so the fetch can't be aimed at internal/metadata endpoints.
_BROLL_KEY_RE = _re.compile(r'^broll_[0-9a-f]{32}\.mp4$')
_BROLL_URL_HOSTS = ("pexels.com", "pixabay.com", "vimeocdn.com", "vimeo.com")


def _is_allowed_broll_url(url) -> bool:
    """True iff url is an https link to a trusted stock-video host."""
    if not isinstance(url, str) or not url:
        return False
    import urllib.parse as _up
    try:
        p = _up.urlparse(url)
    except Exception:
        return False
    if p.scheme != "https" or not p.hostname:
        return False
    host = p.hostname.lower()
    return any(host == h or host.endswith("." + h) for h in _BROLL_URL_HOSTS)


def _broll_item_safe(item) -> bool:
    """Reject burn B-roll items that could reach an arbitrary file or host.

    Mirrors the burn worker's own precedence (video_key wins over a URL): a
    local clip must match the broll_<uuid>.mp4 shape; a remote clip must point
    at a trusted stock host. Items with no source are harmless — the worker
    skips them."""
    if not isinstance(item, dict):
        return False
    vk = item.get("video_key")
    if vk:
        return isinstance(vk, str) and bool(_BROLL_KEY_RE.match(vk))
    url = item.get("download_url") or item.get("preview_url")
    if url:
        return _is_allowed_broll_url(url)
    return True

def _sanitize_transcript(text: str, max_chars: int = 50_000) -> str:
    """Strip control chars (keep newline/tab), cap length."""
    cleaned = _re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
    return cleaned[:max_chars]

# ---------------------------------------------------------------------------
# Caption forbidden-word filter (Instagram shadow-ban prevention)
# ---------------------------------------------------------------------------
_FORBIDDEN_WORDS = frozenset([
    # Death / violence
    "מוות", "מות", "הרג", "הריגה", "רצח", "רוצח", "רצחן",
    "טבח", "קטל", "גופה", "גוויה", "התאבדות", "ירייה", "דקירה",
    # Curses
    "חרא", "זין", "מזדיין", "זיון", "לזיין", "מזיין",
    "זונה", "שרמוטה", "מנייאק", "מניאק",
    # Sexual
    "סקס", "פורנו", "ביאה", "אוננות", "זנות",
])
_HEBREW_PREFIXES = frozenset("הבלמוכשו")

_RLM                = chr(0x200F)  # Unicode Right-to-Left Mark — invisible, strong RTL
_RTL_LEAD_PUNCT_RE  = _re.compile(r'^([?!.،؟]+)\s+(.+)$', _re.DOTALL)
_RTL_TRAIL_PUNCT_RE = _re.compile(r'([?!]+)(\\N|$)')

def _fix_rtl_punct(line: str) -> str:
    """Move leading sentence-final punctuation to the end of an RTL caption line.

    The ivrit-ai Whisper model sometimes emits '? שלום' where the punctuation
    token precedes the Hebrew word in logical order. In RTL display the first
    logical character is placed at the visual-right (start of reading), so the
    question mark appears on the wrong side. Moving it to the end yields
    'שלום?' which renders correctly in RTL.
    """
    m = _RTL_LEAD_PUNCT_RE.match(line.strip())
    return (m.group(2) + m.group(1)) if m else line

def _rtl_ass_text(text: str) -> str:
    """Wrap caption text with Unicode RTL markers for correct libass rendering.

    libass does not reliably anchor neutral characters (?, !) to the RTL run
    when they appear at the end of an ASS Dialogue string — they can float to
    the visual-right instead of staying at the visual-left where Hebrew sentence
    punctuation belongs.

    Two fixes applied only to the ASS text (not the HTML editor copy):
      1. Prepend U+200F (RLM) — forces RTL paragraph base direction in libass.
      2. Insert U+200F after each ? or ! before a \\N line-break or end-of-string —
         acts as a strong RTL anchor, pulling the punctuation to the visual-left.
    """
    marked = _RTL_TRAIL_PUNCT_RE.sub(lambda m: m.group(1) + _RLM + (m.group(2) or ''), text)
    return _RLM + marked

def _censor_caption_text(text: str) -> str:
    """Keep first char of each forbidden word, replace rest with ***.
    Also detects words prefixed with a single Hebrew prefix letter (ה,ב,ל,מ,ו,כ,ש).
    Example: מוות → מ*** | המוות → המ***
    """
    def _replace(m):
        word = m.group()
        if word in _FORBIDDEN_WORDS:
            return word[0] + "***"
        if len(word) > 2 and word[0] in _HEBREW_PREFIXES and word[1:] in _FORBIDDEN_WORDS:
            return word[0] + word[1] + "***"
        return word
    return _re.sub(r'[א-ת]+', _replace, text)

_rate_limit_lock = _threading.Lock()
_rate_limit: dict = {}
_RATE_WINDOW = 60.0
_RATE_MAX = 10

def _check_rate_limit(ip: str) -> bool:
    import time as _t
    now = _t.time()
    with _rate_limit_lock:
        if ip not in _rate_limit:
            _rate_limit[ip] = _collections.deque()
        dq = _rate_limit[ip]
        while dq and now - dq[0] > _RATE_WINDOW:
            dq.popleft()
        if len(dq) >= _RATE_MAX:
            return False
        dq.append(now)
        return True

def _get_client_ip(scope) -> str:
    headers = {bytes(k): bytes(v) for k, v in scope.get("headers", [])}
    xff = headers.get(b"x-forwarded-for", b"")
    if xff:
        return xff.decode().split(",")[0].strip()
    client = scope.get("client")
    return client[0] if client else "unknown"


def _poll_fn_call(fn_call):
    """Non-blocking poll of a Modal FunctionCall.

    Returns ``(result, still_running)`` where ``still_running=True`` means the
    job hasn't finished yet (caller should respond 202).  Raises on job-level
    timeout, expired output, or any other terminal error so the outer
    ``except Exception`` handler can surface a 500 to the client.

    Uses name-based matching rather than isinstance so it stays compatible
    across Modal versions where exception class paths may differ.
    """
    try:
        return fn_call.get(timeout=0), False
    except Exception as e:
        name = type(e).__name__
        # Modal raises TimeoutError (and various subclasses) while a job is
        # still running.  FunctionTimeoutError means the job itself exceeded
        # its own timeout= limit — that is terminal, not "still running".
        if "Timeout" in name and "Function" not in name:
            return None, True
        # Terminal error — ensure non-empty message for send_error downstream
        raise RuntimeError(str(e) or name) from e

