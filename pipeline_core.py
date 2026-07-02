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
    # torch (CPU-only) is required by deepfilternet's Python layer;
    # the actual inference runs in DeepFilterNet's Rust backend so GPU torch isn't needed.
    .pip_install("torch", "torchaudio", extra_options="--index-url https://download.pytorch.org/whl/cpu")
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

