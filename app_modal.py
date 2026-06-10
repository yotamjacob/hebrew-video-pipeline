"""
Hebrew Video Pipeline — Modal API backend.

Deploy:  modal deploy app_modal.py
Dev:     modal serve app_modal.py

Endpoint:  POST /process
  - multipart form: video (file), cut_silences, burn_captions, min_silence, padding
  - returns: processed video (video/mp4)
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
)

app = modal.App("hebrew-video-pipeline", image=image)

model_volume = modal.Volume.from_name("heb-whisper-model", create_if_missing=True)
MODEL_DIR = "/model-cache"
WHISPER_MODEL = "ivrit-ai/whisper-large-v3-turbo-ct2"

tmp_vol = modal.Volume.from_name("hebrew-pipeline-tmp", create_if_missing=True)
TMP_DIR = "/pipeline-tmp"

TRANSCRIPT_ANALYSIS_MODEL   = "gemini-2.5-flash"
IMAGE_GENERATION_MODEL      = "gemini-3.1-flash-image-preview"
VIDEO_GENERATION_MODEL      = "veo-3.0-generate-001"
VIDEO_GENERATION_MODEL_FAST = "veo-3.0-fast-generate-001"

SONNET_MODEL = "claude-sonnet-4-6"
HAIKU_MODEL  = "claude-haiku-4-5-20251001"
OPUS_MODEL   = "claude-opus-4-7"

# Scoring temperatures — explicit so they're not buried in call sites
HAIKU_SCORING_TEMPERATURE  = 0.2   # consistent judgment across clips
SONNET_MOMENT_TEMPERATURE  = 0.4   # editorial flexibility for moment selection
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

# ---------------------------------------------------------------------------
# Veo helper — shared by generate_broll_video and analyze_broll
# ---------------------------------------------------------------------------
import random as _random
import time   as _time

_VEO_MAX_ATTEMPTS = 5   # retries on 429 / 503
_VEO_BASE_DELAY   = 2.0
_VEO_CAP_DELAY    = 60.0
_VEO_JOB_TIMEOUT  = 480  # seconds per job (8 min; documented max is ~6 min)
_VEO_POLL_INTERVAL = 10  # seconds between operation.get() calls


def _veo_call(client, prompt: str, video_cfg) -> object:
    """Start a Veo video generation with exponential back-off + jitter.
    Retries on 429/RESOURCE_EXHAUSTED and 5xx server errors; fails fast on auth/validation."""
    for attempt in range(_VEO_MAX_ATTEMPTS):
        try:
            print(f"[veo] start attempt {attempt+1}/{_VEO_MAX_ATTEMPTS}: {prompt[:80]!r}")
            return client.models.generate_videos(
                model=VIDEO_GENERATION_MODEL,
                prompt=prompt,
                **({"config": video_cfg} if video_cfg else {}),
            )
        except Exception as exc:
            msg = str(exc)
            is_quota  = "429" in msg or "RESOURCE_EXHAUSTED" in msg
            is_server = any(k in msg for k in ("503", "502", "500", "unavailable"))
            is_auth   = any(k in msg for k in ("401", "403", "PERMISSION_DENIED", "api_key"))
            is_invalid = "400" in msg or "INVALID_ARGUMENT" in msg or "not found" in msg.lower()

            if is_auth or is_invalid:
                raise   # fail fast — retrying won't help

            if (is_quota or is_server) and attempt < _VEO_MAX_ATTEMPTS - 1:
                delay = min(_VEO_BASE_DELAY * 2**attempt + _random.uniform(0, 1), _VEO_CAP_DELAY)
                print(f"[veo] {'quota' if is_quota else 'server'} error attempt {attempt+1}, "
                      f"retry in {delay:.1f}s — {msg[:120]}")
                _time.sleep(delay)
                continue

            raise   # last attempt or unclassified error

    raise RuntimeError("_veo_call: exhausted all retry attempts")  # unreachable


def _veo_poll(client, operation) -> object:
    """Poll a Veo long-running operation every 10 s up to _VEO_JOB_TIMEOUT seconds."""
    deadline = _time.time() + _VEO_JOB_TIMEOUT
    polls = 0
    while not operation.done:
        if _time.time() > deadline:
            raise TimeoutError(f"Veo job timed out after {_VEO_JOB_TIMEOUT}s ({polls} polls)")
        _time.sleep(_VEO_POLL_INTERVAL)
        operation = client.operations.get(operation)
        polls += 1
    return operation


# ---------------------------------------------------------------------------
# Core processing — GPU worker
# ---------------------------------------------------------------------------
@app.function(
    gpu="T4",
    timeout=900,
    volumes={MODEL_DIR: model_volume, TMP_DIR: tmp_vol},
    memory=4096,
)
def process_video(
    upload_key: str = None,
    video_bytes: bytes = None,
    filename: str = "video.mp4",
    cut_silences: bool = True,
    burn_captions: bool = True,
    min_silence: float = 0.3,
    padding: float = 0.2,
    enhance_audio: bool = True,
    transcribe_for_broll: bool = False,
) -> dict:
    # Warmup call — just starts the container, no real work
    if filename == "__warmup__":
        return {"captions": [], "video_key": ""}

    import json
    import shutil
    import subprocess
    import tempfile
    from dataclasses import dataclass, field
    from pathlib import Path
    from typing import List

    # Validate input before entering the tempdir
    if upload_key is not None:
        tmp_vol.reload()
        chunk_paths = sorted(Path(TMP_DIR).glob(f"{upload_key}_chunk_*"))
        if not chunk_paths and not (Path(TMP_DIR) / f"{upload_key}_src.mp4").exists():
            raise ValueError(f"No upload chunks found for key {upload_key}")
    elif not video_bytes:
        raise ValueError("No video data provided")

    @dataclass
    class Word:
        start: float
        end: float
        text: str

    @dataclass
    class KeepSegment:
        start: float
        end: float
        words: List[Word] = field(default_factory=list)

        @property
        def duration(self):
            return self.end - self.start

    def run(cmd):
        result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        if result.returncode != 0:
            stderr_tail = result.stderr.decode("utf-8", errors="replace")[-3000:]
            raise RuntimeError(f"ffmpeg exited {result.returncode}:\n{stderr_tail}")

    def probe_video(path):
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height,side_data_list",
             "-show_entries", "stream_tags=rotate",
             "-show_entries", "format=duration",
             "-of", "json", str(path)],
            capture_output=True, text=True, check=True,
        )
        d = json.loads(r.stdout)
        s = d["streams"][0]
        # Detect rotation from stream tags (older) or side_data_list (newer ffprobe)
        rotation = 0
        tag_rot = s.get("tags", {}).get("rotate")
        if tag_rot:
            rotation = int(tag_rot)
        else:
            for sd in s.get("side_data_list", []):
                if "rotation" in sd:
                    rotation = (-int(sd["rotation"])) % 360
                    break
        w, h = int(s["width"]), int(s["height"])
        # Swap to display dimensions when rotated 90/270
        if rotation in (90, 270):
            w, h = h, w
        return w, h, float(d["format"]["duration"]), rotation

    def extract_audio(video, out_wav):
        run(["ffmpeg", "-y", "-i", str(video),
             "-vn", "-af", "loudnorm", "-acodec", "pcm_s16le", "-ar", "48000", "-ac", "1",
             str(out_wav)])

    def enhance_deepfilter(in_wav, out_wav):
        import os, sys, types
        import torch
        import soundfile as sf
        os.environ["XDG_CACHE_HOME"] = MODEL_DIR

        # DeepFilterNet 0.5.x imports torchaudio APIs removed in torchaudio 2.x.
        # Patch all missing symbols before importing df.enhance, then bypass
        # load_audio/save_audio entirely (they use torchaudio.load which requires
        # TorchCodec in 2.5+). We do I/O with soundfile and call enhance() directly.
        import torchaudio

        class _AudioMetaData:
            def __init__(self, sample_rate, num_frames, num_channels,
                         bits_per_sample, encoding):
                self.sample_rate = sample_rate
                self.num_frames = num_frames
                self.num_channels = num_channels
                self.bits_per_sample = bits_per_sample
                self.encoding = encoding

        if not hasattr(torchaudio, "backend"):
            _common = types.ModuleType("torchaudio.backend.common")
            _common.AudioMetaData = _AudioMetaData
            _backend = types.ModuleType("torchaudio.backend")
            _backend.common = _common
            sys.modules["torchaudio.backend"] = _backend
            sys.modules["torchaudio.backend.common"] = _common
            torchaudio.backend = _backend
        torchaudio.AudioMetaData = _AudioMetaData
        torchaudio.info = lambda path, *a, **kw: (
            lambda i: _AudioMetaData(i.samplerate, i.frames, i.channels, 16, "PCM_S")
        )(sf.info(str(path)))
        torchaudio.load = lambda path, *a, **kw: (
            lambda d, sr: (torch.from_numpy(d.T), sr)
        )(*sf.read(str(path), dtype="float32", always_2d=True))
        torchaudio.save = lambda path, src, sr, *a, **kw: sf.write(
            str(path), src.numpy().T, sr
        )

        from df.enhance import enhance, init_df
        model, df_state, _ = init_df()

        # Load with soundfile; audio is already 48 kHz mono from extract_audio
        audio_np, _ = sf.read(str(in_wav), dtype="float32", always_2d=True)
        audio_t = torch.from_numpy(audio_np.T)       # [channels, frames]
        if audio_t.shape[0] > 1:
            audio_t = audio_t.mean(0, keepdim=True)  # force mono

        enhanced = enhance(model, df_state, audio_t)

        # DeepFilterNet attenuates the signal alongside the noise; restore
        # peak level to match the original so the output isn't too quiet.
        orig_peak = audio_t.abs().max().item()
        enh_peak  = enhanced.abs().max().item()
        if enh_peak > 1e-6:
            target = min(max(orig_peak, 0.25), 0.95)  # at least -12 dBFS, headroom at -0.4 dBFS
            enhanced = enhanced * (target / enh_peak)

        sf.write(str(out_wav), enhanced.numpy().T, df_state.sr())
        model_volume.commit()

    def transcribe(wav):
        import os
        os.environ["HF_HOME"] = MODEL_DIR
        from faster_whisper import WhisperModel

        def _segments(segs):
            # Return List[(List[Word], seg_end)] — one tuple per Whisper segment.
            # seg_end is Whisper's segment-level end timestamp, which is more
            # generous than the last word's end — it captures trailing vowels
            # (e.g. the "oooo" of ו) that fall below word-detection threshold.
            result = []
            for seg in segs:
                seg_words = [Word(w.start, w.end, w.word.strip())
                             for w in (seg.words or []) if w.word.strip()]
                if seg_words:
                    result.append((seg_words, seg.end))
            return result

        # Less aggressive VAD: lower threshold + longer speech padding so weak
        # Hebrew consonants (ע, ח, ה at word ends) aren't mistaken for silence.
        vad_params = {
            "threshold": 0.35,           # default 0.5 — lower catches quieter phonemes
            "min_silence_duration_ms": 600,  # default 2000 — allow shorter pauses within speech
            "speech_pad_ms": 600,            # default 400 — more buffer around speech boundaries
        }
        try:
            m = WhisperModel(WHISPER_MODEL, device="cuda", compute_type="float16",
                             download_root=MODEL_DIR)
            segs, _ = m.transcribe(
                str(wav), language="he", word_timestamps=True,
                vad_filter=True, vad_parameters=vad_params,
                beam_size=5, condition_on_previous_text=True,
            )
            whisper_segs = _segments(segs)
        except Exception:
            m = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8",
                             download_root=MODEL_DIR)
            segs, _ = m.transcribe(
                str(wav), language="he", word_timestamps=True,
                vad_filter=True, vad_parameters=vad_params,
                beam_size=5, condition_on_previous_text=True,
            )
            whisper_segs = _segments(segs)
        model_volume.commit()
        return whisper_segs

    def compute_keep_segments(whisper_segs, total_dur, min_sil, pad):
        """Cut only between Whisper segments, never within one.

        whisper_segs: List[List[Word]] — each inner list is one Whisper segment.
        Cuts happen only when the gap between two consecutive segments >= min_sil.
        Words within a segment are always kept together, preventing mid-word cuts
        caused by Whisper tokenization or VAD boundary artefacts.
        """
        if not whisper_segs:
            return [KeepSegment(0.0, total_dur)]
        out = []
        cur = None
        prev_end = None  # raw (unpadded) end timestamp of last added Whisper segment

        # Whisper (especially CUDA) consistently underestimates segment end
        # timestamps for Hebrew trailing vowels (ו, י, ה) by 150–300ms.
        # Add a fixed trailing buffer on top of pad to compensate.
        TRAIL = 0.25

        for seg_words, seg_end in whisper_segs:
            seg_start = seg_words[0].start

            if cur is None:
                cur = KeepSegment(start=max(0.0, seg_start - pad), end=0.0)
            else:
                gap = seg_start - prev_end
                if gap >= min_sil:
                    cur.end = min(total_dur, prev_end + pad + TRAIL)
                    out.append(cur)
                    cur = KeepSegment(start=max(cur.end, seg_start - pad), end=0.0)

            cur.words.extend(seg_words)
            prev_end = seg_end  # segment-level end, not last word's end

        cur.end = min(total_dur, prev_end + pad + TRAIL)
        out.append(cur)
        return out

    def seconds_to_ass(t):
        h = int(t // 3600)
        m = int((t % 3600) // 60)
        s = t % 60
        return f"{h}:{m:02d}:{s:05.2f}"

    def generate_ass(segs, path, w, h, font_size=48, min_sil=0.3):
        margin_h = max(25, w // 14)
        margin_v = h // 4
        max_chars = max(8, int((w - 2 * margin_h) / (font_size * 0.60)))

        def _clean(t):
            return t.strip("،,.-–—;:")

        events = []
        cumulative = 0.0
        for seg in segs:
            line_buf, line_chars = [], 0
            lines_in_cue = []

            def flush(cumulative=cumulative, seg=seg):
                if not lines_in_cue:
                    return
                first, last = lines_in_cue[0][0], lines_in_cue[-1][-1]
                ns = cumulative + (first.start - seg.start)
                ne = cumulative + (last.end - seg.start)
                text = r"\N".join(
                    _fix_rtl_punct(" ".join(_clean(ww.text) for ww in ln).strip())
                    for ln in lines_in_cue
                )
                events.append((ns, ne, text))
                lines_in_cue.clear()

            prev_end = None
            for ww in seg.words:
                # Split on silence gap — necessary when cut_silences=False puts all words
                # in one segment; also catches any sub-min_sil gaps left in segments.
                if prev_end is not None and (ww.start - prev_end) >= min_sil and line_buf:
                    lines_in_cue.append(line_buf)
                    line_buf, line_chars = [], 0
                    flush()
                prev_end = ww.end

                proj = line_chars + len(ww.text) + (1 if line_buf else 0)
                if proj > max_chars and line_buf:
                    lines_in_cue.append(line_buf)
                    line_buf, line_chars = [], 0
                    flush()
                line_buf.append(ww)
                line_chars += len(ww.text) + (1 if len(line_buf) > 1 else 0)
            if line_buf:
                lines_in_cue.append(line_buf)
            flush()
            cumulative += seg.duration

        header = (
            "[Script Info]\nScriptType: v4.00+\n"
            f"PlayResX: {w}\nPlayResY: {h}\n"
            "WrapStyle: 2\nScaledBorderAndShadow: yes\nYCbCr Matrix: TV.709\n\n"
            "[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
            "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
            "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
            "Alignment, MarginL, MarginR, MarginV, Encoding\n"
            f"Style: Default,Rubik,{font_size},"
            "&H00FFFFFF,&H000000FF,&H00000000,&HFF000000,"
            f"-1,0,0,0,100,100,0,0,1,3,0,2,"
            f"{margin_h},{margin_h},{margin_v},1\n\n"
            "[Events]\n"
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        )
        lines = [
            f"Dialogue: 0,{seconds_to_ass(s)},{seconds_to_ass(e)},"
            f"Default,,0,0,0,,{_rtl_ass_text(t)}\n"
            for s, e, t in events
        ]
        path.write_text(header + "".join(lines), encoding="utf-8")
        return events

    def _rotation_vf(rotation):
        """Return an ffmpeg vf filter string (with trailing comma) to bake in rotation metadata.
        rotate=90  → portrait iPhone selfie → needs 90° CCW (transpose=cclock)
        rotate=270 → needs 90° CW (transpose=clock)
        rotate=180 → flip both axes
        """
        r = rotation % 360
        if r == 90:  return "transpose=cclock,"
        if r == 270: return "transpose=clock,"
        if r == 180: return "hflip,vflip,"
        return ""

    def render(video, audio, segs, ass_file, out, crf=18, rotation=0):
        v_parts, a_parts = [], []
        for i, s in enumerate(segs):
            v_parts.append(f"[0:v]trim=start={s.start:.3f}:end={s.end:.3f},setpts=PTS-STARTPTS[v{i}]")
            a_parts.append(f"[1:a]atrim=start={s.start:.3f}:end={s.end:.3f},asetpts=PTS-STARTPTS[a{i}]")
        cin = "".join(f"[v{i}][a{i}]" for i in range(len(segs)))
        concat = f"{cin}concat=n={len(segs)}:v=1:a=1[vc][ac]"
        rot_vf = _rotation_vf(rotation)
        if ass_file:
            esc = str(ass_file).replace(":", r"\:")
            last = f"[vc]{rot_vf}subtitles='{esc}'[vout]"
        else:
            last = f"[vc]{rot_vf}copy[vout]"
        fc = ";".join(v_parts + a_parts + [concat, last])
        # -noautorotate: disable implicit rotation so our explicit transpose is the sole source of truth
        input_args = ["-noautorotate"] if rotation else []
        meta_args  = ["-metadata:s:v:0", "rotate=0"] if rotation else []
        run(["ffmpeg", "-y"] + input_args + ["-i", str(video), "-i", str(audio),
             "-filter_complex", fc,
             "-map", "[vout]", "-map", "[ac]",
             "-c:v", "libx264", "-crf", str(crf), "-preset", "veryfast",
             "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
             "-movflags", "+faststart"] + meta_args + [str(out)])

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        src = tmp / filename
        if upload_key is not None:
            src_cache = Path(TMP_DIR) / f"{upload_key}_src.mp4"
            if chunk_paths:
                # First run: assemble chunks, cache assembled video, delete chunks
                with open(src, "wb") as _out:
                    for p in chunk_paths:
                        with open(p, "rb") as _in:
                            shutil.copyfileobj(_in, _out)
                        p.unlink()
                shutil.copy(src, src_cache)
                tmp_vol.commit()
            else:
                # Re-run: use cached assembled video from volume
                shutil.copy(src_cache, src)
        else:
            src.write_bytes(video_bytes)

        width, height, duration, rotation = probe_video(src)
        raw_wav = tmp / "raw.wav"
        clean_wav = tmp / "clean.wav"
        ass_file = tmp / "captions.ass"
        out_file = tmp / ("out_" + filename)

        extract_audio(src, raw_wav)
        if enhance_audio:
            enhance_deepfilter(raw_wav, clean_wav)
        else:
            shutil.copy(raw_wav, clean_wav)

        need_transcription = cut_silences or burn_captions or transcribe_for_broll
        whisper_segs = transcribe(clean_wav) if need_transcription else []
        flat_words   = [w for seg_words, _end in whisper_segs for w in seg_words]

        segs = (
            compute_keep_segments(whisper_segs, duration, min_silence, padding)
            if cut_silences and whisper_segs
            else [KeepSegment(0.0, duration, flat_words)]
        )

        captions_list = []
        if (burn_captions or transcribe_for_broll) and flat_words:
            events = generate_ass(segs, ass_file, width, height, min_sil=min_silence)
            captions_list = [{"start": s, "end": e, "text": _censor_caption_text(t)} for s, e, t in events]

        if cut_silences and whisper_segs:
            render(src, clean_wav, segs, None, out_file, rotation=rotation)
        elif rotation:
            # No silence cut but source has rotation metadata — bake it in so the browser
            # gets correctly-oriented pixels without relying on display matrix hints.
            rot_vf = _rotation_vf(rotation).rstrip(",")
            run(["ffmpeg", "-y", "-noautorotate", "-i", str(src),
                 "-vf", rot_vf, "-c:v", "libx264", "-crf", "18", "-preset", "veryfast",
                 "-pix_fmt", "yuv420p", "-c:a", "copy",
                 "-metadata:s:v:0", "rotate=0", "-movflags", "+faststart", str(out_file)])
        else:
            shutil.copy(src, out_file)

        import uuid
        video_key = uuid.uuid4().hex + "_cut.mp4"
        shutil.copy(out_file, Path(TMP_DIR) / video_key)
        tmp_vol.commit()
        return {"captions": captions_list, "video_key": video_key}


# ---------------------------------------------------------------------------
# Caption-burn worker — CPU only, no GPU needed
# ---------------------------------------------------------------------------
@app.function(image=burn_image, timeout=600, volumes={TMP_DIR: tmp_vol})
def burn_captions_fn(video_key: str, captions_json: str, font: str = "Heebo", margin_v_pct: float = 0.08, broll_json: str = "[]", font_size: int = 48, hook_json: str = "") -> dict:
    import json, subprocess, tempfile, uuid, shutil
    from pathlib import Path

    tmp_vol.reload()
    captions = json.loads(captions_json)
    broll_items = json.loads(broll_json) if broll_json else []
    hook = json.loads(hook_json) if hook_json else {}

    def run(cmd):
        result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=480)
        if result.returncode != 0:
            stderr_tail = result.stderr.decode("utf-8", errors="replace")[-3000:]
            raise RuntimeError(f"ffmpeg exited {result.returncode}:\n{stderr_tail}")

    def probe_dims(path):
        """Return (width, height, rotation) with w/h already swapped for 90/270°."""
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height,side_data_list",
             "-show_entries", "stream_tags=rotate",
             "-of", "json", str(path)],
            capture_output=True, text=True, check=True,
        )
        s = json.loads(r.stdout)["streams"][0]
        w, h = int(s["width"]), int(s["height"])
        rot = 0
        tag_rot = s.get("tags", {}).get("rotate")
        if tag_rot:
            rot = int(tag_rot)
        else:
            for sd in s.get("side_data_list", []):
                if "rotation" in sd:
                    rot = (-int(sd["rotation"])) % 360
                    break
        if rot in (90, 270):
            w, h = h, w
        return w, h, rot

    def seconds_to_ass(t):
        h = int(t // 3600); m = int((t % 3600) // 60); s = t % 60
        return f"{h}:{m:02d}:{s:05.2f}"

    def hex_to_ass(hex_color: str, alpha_byte: int = 0) -> str:
        h = hex_color.lstrip("#")
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return f"&H{alpha_byte:02X}{b:02X}{g:02X}{r:02X}&"

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        video_in  = tmp / "input.mp4"
        video_out = tmp / "output.mp4"
        ass_path  = tmp / "captions.ass"
        shutil.copy(Path(TMP_DIR) / video_key, video_in)

        width, height, _rotation = probe_dims(video_in)
        # Build rotation vf prefix so output is always baked-portrait regardless of metadata
        if _rotation == 90:
            _rot_vf = "transpose=cclock,"
        elif _rotation == 270:
            _rot_vf = "transpose=clock,"
        elif _rotation == 180:
            _rot_vf = "vflip,hflip,"
        else:
            _rot_vf = ""
        _rot_input  = ["-noautorotate"] if _rotation else []
        _rot_meta   = ["-metadata:s:v:0", "rotate=0"] if _rotation else []
        font_size = max(12, min(200, font_size))
        margin_h  = max(25, width  // 14)
        margin_v  = int(margin_v_pct * height)

        # Re-wrap caption text to match the CSS preview's word-wrap behaviour.
        # The preview uses max-width = (videoWidth - 2*marginH)/videoWidth% with
        # the browser's actual font metrics; we approximate char width at 0.60×fontSize
        # for Hebrew (Heebo/Rubik glyphs average ~60% of em-square). Using 0.50 caused
        # Python to under-wrap, leaving lines too long for libass → libass added unexpected
        # extra wraps, making captions appear taller and "too high" than intended.
        def _rewrap_cap(text: str) -> str:
            words = text.replace(r"\N", " ").split()
            if not words:
                return text
            avail = width - 2 * margin_h
            char_w = font_size * 0.60
            lines: list[str] = []
            cur: list[str] = []
            cur_w = 0.0
            for word in words:
                ww = len(word) * char_w
                gap = char_w if cur else 0.0
                if cur and cur_w + gap + ww > avail:
                    lines.append(" ".join(cur))
                    cur, cur_w = [word], ww
                else:
                    cur.append(word)
                    cur_w += gap + ww
            if cur:
                lines.append(" ".join(cur))
            return r"\N".join(lines) if lines else text

        def _fix_cap_lines(t):
            return r'\N'.join(_fix_rtl_punct(l) for l in t.split(r'\N'))
        captions = [{"start": c["start"], "end": c["end"], "text": _fix_cap_lines(_rewrap_cap(_censor_caption_text(c["text"])))} for c in captions]

        # Build hook style + event if a hook was selected
        hook_style_line = ""
        hook_event_line = ""
        if hook.get("text"):
            h_font         = hook.get("font", "Heebo")
            h_size_pct     = max(50, min(200, int(hook.get("font_size_pct", 100))))
            h_fsize_base   = max(24, int(min(width, height) * 0.075 * h_size_pct / 100))
            h_fsize        = max(24, int(h_fsize_base * 1.30))
            h_primary      = hex_to_ass(hook.get("font_color", "#FFFFFF"), 0)
            h_bg_alpha     = int((1.0 - max(0.0, min(1.0, float(hook.get("bg_opacity", 0.6))))) * 255)
            h_back         = hex_to_ass(hook.get("bg_color", "#000000"), h_bg_alpha)
            h_border_size  = max(0, min(20, int(hook.get("border_size", 0))))
            h_border_color = hook.get("border_color", "#000000")
            h_vpos      = max(0, min(100, int(hook.get("vertical_position", 10))))
            h_pad       = h_fsize_base   # use unbumped size for position, matching preview edgePad
            h_y         = int(h_pad + (height - 2 * h_pad) * (h_vpos / 100))
            h_start     = float(hook.get("start_seconds", 1.0))
            h_dur       = float(hook.get("duration_seconds", 4.0))
            # Strip leading/trailing weak BiDi chars (commas, hyphens) — same fix as captions.
            # These weak chars can disrupt libass's RTL paragraph detection.
            def _hclean(w: str) -> str:
                return w.strip("،,.-–—;:")

            # Estimate line count for block-height positioning.
            # Use h_fsize_base (unbumped) for available width — matches canvas edgePad so
            # libass wraps at the same points as the canvas preview.
            h_avail_w = width - 2 * h_fsize_base
            char_w = h_fsize * 0.50
            raw_words = [_hclean(w) for w in hook["text"].split() if _hclean(w)]
            hook_lines, cur, cur_w = [], [], 0.0
            for word in raw_words:
                ww = len(word) * char_w
                gap = char_w if cur else 0.0
                if cur and cur_w + gap + ww > h_avail_w:
                    hook_lines.append(" ".join(cur))
                    cur, cur_w = [word], ww
                else:
                    cur.append(word)
                    cur_w += gap + ww
            if cur:
                hook_lines.append(" ".join(cur))
            # \q1 (simple end-of-line wrap) lets libass wrap as one block → BorderStyle=3/4
            # draws a SINGLE unified background box across all lines.  Using \N hard-breaks
            # caused libass to draw a separate box per line (two disjointed rectangles).
            #
            # BorderStyle=3: OutlineColour = box fill; no outer border — use when border_size=0.
            # BorderStyle=4: BackColour = box fill, OutlineColour = outline around box, Outline=width
            #   — use when border_size>0 so box color and border color are independent fields.
            #   With BorderStyle=3, \3c would override OutlineColour (= box background) instead of
            #   drawing a separate border, making the box turn the border color rather than bg_color.
            h_override = "\\q1"
            if h_border_size > 0:
                hbc = h_border_color.lstrip("#")
                if len(hbc) == 3: hbc = "".join(c * 2 for c in hbc)
                hr, hg, hb = int(hbc[0:2], 16), int(hbc[2:4], 16), int(hbc[4:6], 16)
                h_border_ass = f"&H{hb:02X}{hg:02X}{hr:02X}&"
                h_border_style = 4  # box(BackColour) + outline(OutlineColour)
                # Style carries all colors; no per-event override tags needed.
                h_outline_color = h_border_ass
                h_box_color     = h_back   # bg_color goes in BackColour for BorderStyle=4
                hook_outline    = h_border_size
            else:
                h_border_style  = 3  # opaque box only; OutlineColour = box fill
                h_outline_color = h_back   # bg_color in OutlineColour for BorderStyle=3
                h_box_color     = h_back   # also in BackColour for libass compat
                hook_outline    = max(10, int(h_fsize * 0.25))
            h_text_raw = " ".join(raw_words) if raw_words else hook["text"]
            h_text = ("{" + h_override + "}"
                      + h_text_raw.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}"))
            n_hook_lines = len(hook_lines) if hook_lines else 1
            block_h      = int(n_hook_lines * h_fsize * 1.10)
            h_margin_v   = max(0, h_y - block_h // 2)
            hook_style_line = (
                f"Style: Hook,{h_font},{h_fsize},"
                f"{h_primary},&H000000FF,{h_outline_color},{h_box_color},"
                f"-1,0,0,0,100,100,0,0,{h_border_style},{hook_outline},0,8,0,0,0,1\n"
            )
            # MarginL/R = h_fsize_base constrains libass wrap width to match canvas maxW.
            hook_event_line = (
                f"Dialogue: 1,{seconds_to_ass(h_start)},{seconds_to_ass(h_start + h_dur)},"
                f"Hook,,{h_fsize_base},{h_fsize_base},{h_margin_v},,{h_text}\n"
            )

        header = (
            "[Script Info]\nScriptType: v4.00+\n"
            f"PlayResX: {width}\nPlayResY: {height}\n"
            "WrapStyle: 0\nScaledBorderAndShadow: yes\nYCbCr Matrix: TV.709\n\n"
            "[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
            "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
            "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
            "Alignment, MarginL, MarginR, MarginV, Encoding\n"
            f"Style: Default,{font},{font_size},"
            "&H00FFFFFF,&H000000FF,&H00000000,&H80000000,"
            f"-1,0,0,0,100,100,0,0,1,2,0,2,"
            f"{margin_h},{margin_h},{margin_v},1\n"
            + hook_style_line +
            "\n[Events]\n"
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        )
        lines = [
            f"Dialogue: 0,{seconds_to_ass(c['start'])},{seconds_to_ass(c['end'])},"
            f"Default,,0,0,0,,{_rtl_ass_text(c['text'])}\n"
            for c in captions
        ]
        ass_path.write_text(header + "".join(lines) + hook_event_line, encoding="utf-8")

        # Copy / download selected B-roll clips into temp dir
        import requests as _req_burn
        broll_files = []
        for idx, item in enumerate(broll_items):
            dst = tmp / f"broll_{idx}.mp4"
            vid_key      = item.get("video_key")
            download_url = item.get("download_url") or item.get("preview_url")
            if vid_key:
                src = Path(TMP_DIR) / vid_key
                if not src.exists():
                    continue
                shutil.copy(src, dst)
            elif download_url:
                try:
                    r = _req_burn.get(download_url, timeout=30, stream=True)
                    r.raise_for_status()
                    with open(dst, "wb") as fh:
                        for chunk in r.iter_content(65536):
                            fh.write(chunk)
                except Exception as dl_err:
                    print(f"[burn] b-roll {idx} download failed: {dl_err}")
                    continue
            else:
                continue
            start         = float(item["start"])
            end           = float(item.get("end", start + 3.0))
            clip_in_start = float(item.get("clip_use_start_seconds", 0.0))
            clip_in_end   = item.get("clip_use_end_seconds")
            if clip_in_end is not None:
                clip_in_end = float(clip_in_end)
            broll_files.append((dst, start, end, clip_in_start, clip_in_end))

        esc = str(ass_path).replace(":", r"\:")

        if not broll_files:
            # Simple path: just subtitle burn (or copy if no captions)
            if captions:
                run(["ffmpeg", "-y"] + _rot_input + ["-i", str(video_in),
                     "-vf", f"{_rot_vf}subtitles='{esc}'",
                     "-c:v", "libx264", "-crf", "18", "-preset", "veryfast",
                     "-pix_fmt", "yuv420p", "-c:a", "copy",
                     "-movflags", "+faststart"] + _rot_meta + [str(video_out)])
            elif _rotation:
                run(["ffmpeg", "-y"] + _rot_input + ["-i", str(video_in),
                     "-vf", _rot_vf.rstrip(","),
                     "-c:v", "libx264", "-crf", "18", "-preset", "veryfast",
                     "-pix_fmt", "yuv420p", "-c:a", "copy",
                     "-movflags", "+faststart"] + _rot_meta + [str(video_out)])
            else:
                shutil.copy(video_in, video_out)
        else:
            # Complex path: B-roll video overlays first, captions burned on top
            cmd = ["ffmpeg", "-y"] + _rot_input + ["-i", str(video_in)]
            for vid_path, *_ in broll_files:
                cmd += ["-i", str(vid_path)]  # finite video clip — no -loop

            filters = []
            prev = "0:v"
            # Apply rotation to main track first if needed
            if _rotation:
                filters.append(f"[0:v]{_rot_vf.rstrip(',')}[vrot]")
                prev = "vrot"
            for idx, (_, start, end, clip_in_start, clip_in_end) in enumerate(broll_files):
                vid_idx = idx + 1
                out_label = f"vbr{idx}"
                # Trim to desired window within the stock clip, then scale to frame
                if clip_in_end is not None:
                    trim_f = f"trim=start={clip_in_start:.3f}:end={clip_in_end:.3f},"
                elif clip_in_start > 0:
                    trim_f = f"trim=start={clip_in_start:.3f},"
                else:
                    trim_f = ""
                filters.append(
                    f"[{vid_idx}:v]{trim_f}"
                    f"scale={width}:{height}:"
                    f"force_original_aspect_ratio=increase,crop={width}:{height},"
                    f"setpts=PTS-STARTPTS+{start:.3f}/TB[bri{idx}]"
                )
                # eof_action=pass: after clip ends, pass through main video (don't stop)
                filters.append(
                    f"[{prev}][bri{idx}]overlay=0:0:"
                    f"enable='between(t,{start:.3f},{end:.3f})':eof_action=pass[{out_label}]"
                )
                prev = out_label

            # Captions burned last so they appear over B-roll video
            if captions:
                filters.append(f"[{prev}]subtitles='{esc}'[vout]")
                map_out = "[vout]"
            else:
                map_out = f"[{prev}]"

            cmd += [
                "-filter_complex", ";".join(filters),
                "-map", map_out, "-map", "0:a",
                "-c:v", "libx264", "-crf", "18", "-preset", "veryfast",
                "-pix_fmt", "yuv420p", "-c:a", "copy",
                "-movflags", "+faststart",
                str(video_out),
            ]
            run(cmd)

        output_key = uuid.uuid4().hex + "_out.mp4"
        shutil.copy(video_out, Path(TMP_DIR) / output_key)
        tmp_vol.commit()
        return {"output_key": output_key}


# ---------------------------------------------------------------------------
# Single B-roll video (re)generation — called by the per-card retry button
# ---------------------------------------------------------------------------
@app.function(image=image, timeout=600, volumes={TMP_DIR: tmp_vol})
def generate_broll_video(description: str, aspect_ratio: str, gemini_key: str) -> dict:
    import subprocess, uuid
    from pathlib import Path
    from google import genai as _genai
    from google.genai import types as _gtypes

    if not gemini_key:
        return {"video_error": "No Gemini API key provided."}

    client = _genai.Client(api_key=gemini_key)

    _AR_LABEL = {"16:9": "landscape 16:9", "9:16": "vertical portrait 9:16", "1:1": "square 1:1"}
    ar_label  = _AR_LABEL.get(aspect_ratio, "vertical portrait 9:16")
    veo_ar    = aspect_ratio if aspect_ratio in ("16:9", "9:16") else "9:16"

    video_prompt = (
        f"{description} "
        f"Cinematic {ar_label} video clip. "
        "Photorealistic, professional stock footage aesthetic. "
        "Smooth natural motion, beautiful lighting. No text overlays. Safe for all audiences."
    )

    try:
        video_cfg = _gtypes.GenerateVideoConfig(aspect_ratio=veo_ar, number_of_videos=1)
    except AttributeError:
        video_cfg = None

    try:
        operation = _veo_call(client, video_prompt, video_cfg)
        operation = _veo_poll(client, operation)

        if not getattr(operation.response, "generated_videos", None):
            return {"video_error": "Veo returned no videos."}

        gen_video = operation.response.generated_videos[0]
        print(f"[veo] done, uri={getattr(gen_video.video, 'uri', '?')}")

        key      = uuid.uuid4().hex
        raw_path = Path(TMP_DIR) / f"broll_raw_{key}.mp4"
        out_path = Path(TMP_DIR) / f"broll_{key}.mp4"

        try:
            client.files.download(file=gen_video.video, path=str(raw_path))
        except TypeError:
            raw_path.write_bytes(client.files.download(file=gen_video.video))

        subprocess.run(
            ["ffmpeg", "-y", "-i", str(raw_path), "-t", "3",
             "-c:v", "libx264", "-crf", "23", "-preset", "fast",
             "-pix_fmt", "yuv420p", "-an",
             "-movflags", "+faststart", str(out_path)],
            check=True, capture_output=True,
        )
        raw_path.unlink(missing_ok=True)
        tmp_vol.commit()
        return {"video_key": f"broll_{key}.mp4"}

    except Exception as e:
        msg = str(e)
        if any(k in msg for k in ("401", "403", "PERMISSION_DENIED", "api_key")):
            return {"video_error": "Invalid API key — check your Gemini API key."}
        if "not found" in msg.lower() or "not available" in msg.lower():
            return {"video_error": "Veo model not available on this API key."}
        if "429" in msg or "RESOURCE_EXHAUSTED" in msg:
            return {"video_error": "Rate limit hit — wait a minute and retry."}
        return {"video_error": f"Video generation failed: {msg[:200]}"}


# ---------------------------------------------------------------------------
# B-roll analysis — CPU only, calls Gemini Flash
# ---------------------------------------------------------------------------
@app.function(image=image, timeout=900, volumes={TMP_DIR: tmp_vol})
def analyze_broll(video_key: str, captions_json: str, gemini_key: str, aspect_ratio: str = "16:9", anthropic_key: str = "") -> list:
    import json, os, subprocess, tempfile, uuid, time, traceback
    from pathlib import Path

    # ── Input validation ────────────────────────────────────────────────────
    if not gemini_key:
        raise ValueError("No Gemini API key provided.")
    captions = json.loads(captions_json)
    if not captions:
        raise ValueError("No transcript available. Enable captions or transcript generation before running B-roll analysis.")

    tmp_vol.reload()
    video_path = Path(TMP_DIR) / video_key
    if not video_path.exists():
        raise FileNotFoundError(f"Processed video not found in volume: {video_key}")

    from google import genai as _genai
    from google.genai import types as _gtypes

    client = _genai.Client(api_key=gemini_key)

    def _classify_api_error(msg: str) -> str | None:
        """Return a user-facing message for known error categories, or None to re-raise."""
        if "401" in msg or "403" in msg or "api_key" in msg.lower() or "invalid" in msg.lower():
            return "Invalid API key — check your Gemini API key and try again."
        if "429" in msg or "RESOURCE_EXHAUSTED" in msg:
            return "Rate limit hit — wait a minute and try again."
        return None

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = Path(tmp_dir)

        # ── Video duration ───────────────────────────────────────────────────
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "json", str(video_path)],
            capture_output=True, text=True, check=True,
        )
        duration = float(json.loads(r.stdout)["format"]["duration"])

        # ── Frame extraction (2–6 frames, O(1) seek per frame) ──────────────
        n_frames = min(6, max(2, int(duration / 8)))
        interval = duration / n_frames
        frame_parts = []
        for i in range(n_frames):
            t = interval * i + interval / 2
            out = tmp / f"frame_{i:02d}.jpg"
            subprocess.run(
                ["ffmpeg", "-y", "-ss", str(t), "-i", str(video_path),
                 "-frames:v", "1", "-vf", "scale=512:-1", "-q:v", "6", str(out)],
                capture_output=True,
            )
            if out.exists():
                frame_parts.append(out.read_bytes())

        # ── Transcript ───────────────────────────────────────────────────────
        lines = []
        for cap in captions:
            m = int(cap["start"] // 60)
            s = cap["start"] % 60
            lines.append(f"[{m}:{s:04.1f}] {cap['text']}")
        transcript = _sanitize_transcript("\n".join(lines))

        # ── Analysis prompt (structured JSON output) ─────────────────────────
        analysis_prompt = f"""You are a professional video editor analyzing a Hebrew speech video to choose B-roll footage.

Video duration: {duration:.1f}s
Transcript (Hebrew, with timestamps):
<transcript>
{transcript}
</transcript>

I've attached {len(frame_parts)} evenly-spaced frames from the video.

Identify exactly 1 moment — and only 1 — where B-roll would have the highest impact on the video. Be very selective: choose only the single most powerful moment where a visual would reinforce the message, evoke emotion, or make an abstract idea concrete. Prioritize moments where the speaker describes something vivid, emotional, or specific over generic transitions. Do not suggest any moment that starts in the first 3 seconds of the video.

For each moment provide:
- start / end: timestamp numbers (seconds)
- label: one English sentence summarizing what the speaker is saying at this moment
- description: a detailed, cinematic image-generation prompt in English describing the ideal shot — include subject and action, camera angle and framing (close-up, wide, over-the-shoulder, etc.), lighting and mood (golden hour, soft studio, harsh contrast, etc.), visual style (documentary, photorealistic, cinematic). 2–3 sentences. Be specific — this prompt will be sent directly to an AI image generator. IMPORTANT: the description must be fully safe for AI image generation — no violence, no gore, no weapons, no explicit or suggestive content, no controversial figures. If the spoken topic is sensitive, describe an abstract, symbolic, or nature-based visual instead.
- search_query: a 3–5 word stock-footage search phrase

Return a JSON array only — no markdown, no explanation."""

        if anthropic_key:
            import anthropic as _anthropic
            import base64 as _b64

            _ac = _anthropic.Anthropic(api_key=anthropic_key)
            content = []
            for fb in frame_parts:
                content.append({
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/jpeg",
                               "data": _b64.b64encode(fb).decode()},
                })
            content.append({"type": "text", "text": analysis_prompt})
            try:
                resp = _ac.messages.create(
                    model=OPUS_MODEL,
                    max_tokens=2048,
                    system="You are a professional video editor. Return only valid JSON — no markdown, no explanation.",
                    messages=[{"role": "user", "content": content}],
                )
                raw_json = resp.content[0].text.strip()
            except Exception as e:
                raise RuntimeError(str(e)) from None
        else:
            _safety_none = [
                _gtypes.SafetySetting(category="HARM_CATEGORY_HARASSMENT",        threshold="BLOCK_NONE"),
                _gtypes.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH",        threshold="BLOCK_NONE"),
                _gtypes.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT",  threshold="BLOCK_NONE"),
                _gtypes.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT",  threshold="BLOCK_NONE"),
            ]
            analysis_cfg = _gtypes.GenerateContentConfig(
                response_mime_type="application/json",
                safety_settings=_safety_none,
            )
            analysis_parts = [_gtypes.Part.from_text(text=analysis_prompt)]
            for fb in frame_parts:
                analysis_parts.append(_gtypes.Part.from_bytes(data=fb, mime_type="image/jpeg"))
            try:
                resp = client.models.generate_content(
                    model=TRANSCRIPT_ANALYSIS_MODEL, contents=analysis_parts, config=analysis_cfg
                )
                raw_json = resp.text.strip()
            except Exception as e:
                msg = str(e)
                user_msg = _classify_api_error(msg)
                raise RuntimeError(user_msg or msg) from None

        # Parse — retry once with a correction prompt if JSON is malformed
        try:
            suggestions = json.loads(raw_json)
        except json.JSONDecodeError:
            try:
                retry_resp = client.models.generate_content(
                    model=TRANSCRIPT_ANALYSIS_MODEL,
                    contents=[_gtypes.Part.from_text(
                        text=f"The following is not valid JSON. Return it corrected as a pure JSON array only, "
                             f"no markdown or explanation:\n{raw_json[:2000]}"
                    )],
                    config=_gtypes.GenerateContentConfig(response_mime_type="application/json"),
                )
                suggestions = json.loads(retry_resp.text.strip())
            except Exception:
                return []

        if not isinstance(suggestions, list):
            return []
        suggestions = [s for s in suggestions if float(s.get("start", 0)) >= 3.0][:1]
        for s in suggestions:
            s["end"] = s["start"] + 2.0

        # ── Video generation — sequential to stay under Veo RPM quota ──────────
        _AR_LABEL = {"16:9": "landscape 16:9", "9:16": "vertical portrait 9:16", "1:1": "square 1:1"}
        _ar_label = _AR_LABEL.get(aspect_ratio, "vertical portrait 9:16")
        _veo_ar   = aspect_ratio if aspect_ratio in ("16:9", "9:16") else "9:16"

        try:
            _video_cfg = _gtypes.GenerateVideoConfig(aspect_ratio=_veo_ar, number_of_videos=1)
        except AttributeError:
            _video_cfg = None

        for s in suggestions:
            video_prompt = (
                f"{s.get('description', '')} "
                f"Cinematic {_ar_label} video clip. "
                "Photorealistic, professional stock footage aesthetic. "
                "Smooth natural motion, beautiful lighting. No text overlays. Safe for all audiences."
            )
            try:
                operation = _veo_call(client, video_prompt, _video_cfg)
                operation = _veo_poll(client, operation)

                if not getattr(operation.response, "generated_videos", None):
                    raise RuntimeError("No videos in Veo response.")

                gen_video = operation.response.generated_videos[0]
                print(f"[veo] done, uri={getattr(gen_video.video, 'uri', '?')}")

                key      = uuid.uuid4().hex
                raw_path = Path(TMP_DIR) / f"broll_raw_{key}.mp4"
                out_path = Path(TMP_DIR) / f"broll_{key}.mp4"

                try:
                    client.files.download(file=gen_video.video, path=str(raw_path))
                except TypeError:
                    raw_path.write_bytes(client.files.download(file=gen_video.video))

                subprocess.run(
                    ["ffmpeg", "-y", "-i", str(raw_path), "-t", "3",
                     "-c:v", "libx264", "-crf", "23", "-preset", "fast",
                     "-pix_fmt", "yuv420p", "-an",
                     "-movflags", "+faststart", str(out_path)],
                    check=True, capture_output=True,
                )
                raw_path.unlink(missing_ok=True)
                s["video_key"] = f"broll_{key}.mp4"

            except Exception as e:
                msg = str(e)
                traceback.print_exc()
                if any(k in msg for k in ("401", "403", "PERMISSION_DENIED", "api_key")):
                    s["video_error"] = "Invalid API key."
                elif "not found" in msg.lower() or "not available" in msg.lower():
                    s["video_error"] = "Veo model not available on this API key."
                elif "429" in msg or "RESOURCE_EXHAUSTED" in msg:
                    s["video_error"] = "Rate limit hit — wait a minute and retry."
                else:
                    s["video_error"] = f"Video generation failed: {msg[:200]}"
                s["video_key"] = None

        tmp_vol.commit()
        return [s for s in suggestions if s is not None]


# ---------------------------------------------------------------------------
# Stock search helpers — module-level so both analyze_stock_broll and
# search_stock_clips can share them without duplication.
# ---------------------------------------------------------------------------

_frame_cache: dict = {}  # (source, n_frames, strategy) → list[(t, bytes)], per-container


def fetch_pexels(query: str, page: int, pexels_key: str, session=None) -> list:
    if not pexels_key:
        return []
    import requests as _req
    _get = (session or _req).get
    try:
        r = _get(
            "https://api.pexels.com/videos/search",
            params={"query": query, "per_page": 15, "page": page,
                    "size": "medium", "orientation": "portrait"},
            headers={"Authorization": pexels_key},
            timeout=15,
        )
        if r.status_code != 200:
            print(f"[stock] Pexels {r.status_code}: {r.text[:100]}")
            return []
        clips = []
        for v in r.json().get("videos", []):
            files = v.get("video_files", [])
            portrait_files = [f for f in files if f.get("height", 0) > f.get("width", 0)]
            sd_files = [f for f in files if f.get("quality") == "sd"]
            preview = (portrait_files[0] if portrait_files else None) or \
                      (sd_files[0] if sd_files else None) or \
                      (files[0] if files else None)
            if not preview:
                continue
            user = v.get("user", {})
            clips.append({
                "source": "pexels",
                "id": v["id"],
                "preview_url": preview["link"],
                "thumbnail": v.get("image", ""),
                "page_url": v.get("url", ""),
                "author": user.get("name", ""),
                "author_url": user.get("url", ""),
                "attribution": f"Video by {user.get('name', 'Unknown')} on Pexels",
                "title": v.get("alt", ""),
                "tags": [],
                "duration": v.get("duration", 0),
            })
        return clips
    except Exception as e:
        print(f"[stock] Pexels error: {e}")
        return []


def fetch_pixabay(query: str, page: int, pixabay_key: str, session=None) -> list:
    if not pixabay_key:
        return []
    import requests as _req
    _get = (session or _req).get
    try:
        r = _get(
            "https://pixabay.com/api/videos/",
            params={"key": pixabay_key, "q": query, "per_page": 15, "page": page, "video_type": "film"},
            timeout=15,
        )
        if r.status_code != 200:
            print(f"[stock] Pixabay {r.status_code}: {r.text[:100]}")
            return []
        clips = []
        for v in r.json().get("hits", []):
            videos = v.get("videos", {})
            preview = None
            for size in ("small", "medium", "large", "tiny"):
                candidate = videos.get(size, {})
                if candidate.get("url") and candidate.get("height", 0) > candidate.get("width", 1):
                    preview = candidate
                    break
            if not preview:
                continue
            user = v.get("user", "")
            uid  = v.get("user_id", "")
            clips.append({
                "source": "pixabay",
                "id": v["id"],
                "preview_url": preview["url"],
                "thumbnail": f"https://i.vimeocdn.com/video/{v.get('picture_id', '')}_295x166.jpg"
                             if v.get("picture_id") else "",
                "page_url": v.get("pageURL", ""),
                "author": user,
                "author_url": f"https://pixabay.com/users/{user}-{uid}/",
                "attribution": f"Video by {user or 'Unknown'} on Pixabay (CC0)",
                "title": v.get("tags", "").split(",")[0].strip().title() if v.get("tags") else "",
                "tags": [t.strip() for t in v.get("tags", "").split(",") if t.strip()],
                "duration": v.get("duration", 0),
            })
        return clips
    except Exception as e:
        print(f"[stock] Pixabay error: {e}")
        return []


# ---------------------------------------------------------------------------
# Frame sampling — ffmpeg-based, used for video context analysis and
# multi-frame clip scoring. Never raises: returns [] on any failure so
# callers can degrade to thumbnail-only scoring.
# ---------------------------------------------------------------------------

def sample_frames(source: str, n_frames: int = 4, strategy: str = "skip_intro") -> list:
    """Sample n_frames from a video source (local path or HTTP URL).

    strategy: 'evenly_spaced' — evenly across full duration.
              'skip_intro'    — skip first 0.5 s and last 0.3 s (avoids static
                                intro/outro frames common in stock clips).
    Returns: list of (timestamp_sec: float, jpeg_bytes: bytes).
             Returns [] on any failure — callers fall back to thumbnail.
    Caches by (source, n_frames, strategy) for the container lifetime.
    """
    cache_key = (source, n_frames, strategy)
    if cache_key in _frame_cache:
        return _frame_cache[cache_key]
    result = _sample_frames_impl(source, n_frames, strategy)
    _frame_cache[cache_key] = result
    return result


def _sample_frames_impl(source: str, n_frames: int, strategy: str) -> list:
    import subprocess as _sp, tempfile as _tf, os as _os
    tmp_path = None
    try:
        is_url = source.startswith("http://") or source.startswith("https://")
        if is_url:
            tmp_fd, tmp_path = _tf.mkstemp(suffix=".mp4")
            _os.close(tmp_fd)
            _download_video_partial(source, tmp_path, max_bytes=8 * 1024 * 1024)
            source_path = tmp_path
        else:
            source_path = source

        duration = _probe_video_duration(source_path)
        if not duration or duration <= 0:
            return []

        frames = []
        for t in _frame_timestamps(duration, n_frames, strategy):
            data = _extract_jpeg_frame(source_path, t)
            if data:
                frames.append((t, data))
        return frames
    except Exception as e:
        print(f"[frames] sample_frames failed {source[:80]!r}: {e}")
        return []
    finally:
        if tmp_path:
            try:
                import os as _os2
                _os2.unlink(tmp_path)
            except OSError:
                pass


def _download_video_partial(url: str, dest: str, max_bytes: int) -> None:
    import requests as _rq
    resp = _rq.get(url, headers={"Range": f"bytes=0-{max_bytes - 1}"},
                   stream=True, timeout=20)
    downloaded = 0
    with open(dest, "wb") as fh:
        for chunk in resp.iter_content(chunk_size=65_536):
            if not chunk:
                continue
            fh.write(chunk)
            downloaded += len(chunk)
            if downloaded >= max_bytes:
                break


def _probe_video_duration(path: str) -> float:
    import subprocess as _sp
    try:
        r = _sp.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=10,
        )
        val = r.stdout.strip()
        return float(val) if val else None
    except Exception:
        return None


def _frame_timestamps(duration: float, n: int, strategy: str) -> list:
    if strategy == "skip_intro":
        start = min(0.5, duration * 0.1)
        end   = max(start + 0.1, duration - 0.3)
    else:
        start, end = 0.0, duration
    if n == 1:
        return [round((start + end) / 2, 3)]
    span = end - start
    return [round(start + span * i / (n - 1), 3) for i in range(n)]


def _extract_jpeg_frame(video_path: str, timestamp: float) -> bytes:
    import subprocess as _sp
    try:
        r = _sp.run(
            ["ffmpeg", "-ss", str(timestamp), "-i", video_path,
             "-frames:v", "1", "-vf", "scale=min(768,iw):-2",
             "-f", "image2", "-vcodec", "mjpeg", "pipe:1",
             "-loglevel", "error"],
            capture_output=True, timeout=15,
        )
        return r.stdout if r.returncode == 0 and r.stdout else None
    except Exception:
        return None


def extract_disqualify_clause(strict_eval_prompt: str) -> str:
    """Return just the DISQUALIFY clause text from a strict_eval_prompt, or '' if absent."""
    upper = strict_eval_prompt.upper()
    idx = upper.find("DISQUALIFY:")
    if idx == -1:
        return ""
    return strict_eval_prompt[idx + len("DISQUALIFY:"):].strip()


def score_clips(clips: list, strict_eval: str, anthropic_client,
                video_context: dict = None, moment_ctx: dict = None) -> list:
    """Score clips against strict_eval via Haiku vision. Returns clips scored >= 6, sorted desc.

    When video_context is provided (set by analyze_stock_broll after the video-context pass),
    switches to multi-frame mode: downloads each clip's preview_url, extracts 4 frames with
    skip_intro strategy, and uses a richer 2-step scoring prompt that checks DISQUALIFY
    violations (STEP 1) and tonal register fit (STEP 2). Batch size reduces to 3 clips.

    Falls back to thumbnail-only mode when video_context is None (e.g. search_stock_clips).
    Frame sampling failures degrade gracefully to thumbnail fallback per clip.
    """
    import asyncio, json as _json, base64 as _b64

    if not clips or not anthropic_client:
        return clips

    has_thumb = [(i, c) for i, c in enumerate(clips) if c.get("thumbnail")]
    if not has_thumb:
        return clips

    use_multiframe = bool(video_context)
    batch_size = HAIKU_MULTIFRAME_BATCH_SIZE if use_multiframe else HAIKU_SCORING_BATCH_SIZE
    max_tokens = HAIKU_MULTIFRAME_MAX_TOKENS if use_multiframe else HAIKU_SCORING_MAX_TOKENS

    # Pre-build context prefixes (empty strings when context absent)
    _vc_prefix = ""
    if video_context:
        sensitive_str = "; ".join(video_context.get("sensitive_topics", [])) or "none"
        _vc_prefix = (
            "VIDEO CONTEXT (the user's video):\n"
            f"Genre: {video_context.get('video_genre', '')}\n"
            f"Speaker register: {video_context.get('speaker_emotional_register', '')}\n"
            f"Topic: {video_context.get('topic_summary', '')}\n"
            f"Sensitive topics: {sensitive_str}\n\n"
        )
    _mc_prefix = ""
    if moment_ctx:
        _mc_prefix = (
            "MOMENT CONTEXT (where this clip would appear):\n"
            f"Transcript: {moment_ctx.get('transcript_excerpt', '')}\n"
            f"Key insight: {moment_ctx.get('key_insight', '')}\n"
            f"Visual anchor: {moment_ctx.get('visual_anchor', '')}\n"
            f"Why this moment: {moment_ctx.get('reasoning', '')}\n\n"
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _call_haiku_sync(content, mt, temp):
        return anthropic_client.messages.create(
            model=HAIKU_MODEL,
            max_tokens=mt,
            temperature=temp,
            messages=[{"role": "user", "content": content}],
        ).content[0].text.strip()

    def _parse_json_array(raw):
        if "```" in raw:
            for part in raw.split("```"):
                part = part.strip().lstrip("json").strip()
                if part.startswith("["):
                    raw = part
                    break
        return _json.loads(raw)

    def _apply_scores(scores, local_to_orig):
        for rank, orig_i in enumerate(local_to_orig):
            if rank < len(scores):
                s = scores[rank]
                clips[orig_i]["score"] = max(0, min(10, int(s.get("score", 0))))
                clips[orig_i]["score_reason"] = s.get("reason", "")
                if s.get("frames_observed"):
                    clips[orig_i]["frames_observed"] = s["frames_observed"]
                if s.get("step1_disqualified"):
                    clips[orig_i]["step1_disqualified"] = True

    def _build_content_thumbnail(batch_clips):
        """Thumbnail-only scoring (no video_context). Original behaviour."""
        content = []
        local_valid = []
        for li, clip in enumerate(batch_clips):
            if not clip.get("thumbnail"):
                continue
            content.append({"type": "image", "source": {"type": "url", "url": clip["thumbnail"]}})
            local_valid.append(li)
            title    = clip.get("title", "")
            tags_str = ", ".join(clip.get("tags", [])) if clip.get("tags") else ""
            dur      = clip.get("duration", "")
            parts    = []
            if title:    parts.append(f"Title: {title}")
            if tags_str: parts.append(f"Tags: {tags_str}")
            if dur:      parts.append(f"Duration: {dur}s")
            content.append({"type": "text",
                             "text": f"[Clip {len(local_valid)}: {' | '.join(parts) or '(no metadata)'}]"})
        if not local_valid:
            return content, local_valid
        n = len(local_valid)
        content.append({"type": "text", "text": (
            f"You are scoring stock video clips for B-roll relevance.\n\n"
            f"TARGET: {strict_eval}\n\n"
            f"Score each of the {n} clips (1–10) for how well they match TARGET.\n\n"
            "SCORING GUIDE:\n"
            "- 7-10: clip clearly shows what TARGET describes\n"
            "- 4-6: generically related but doesn't clearly satisfy the specific criteria\n"
            "- 1-3: metadata or thumbnail matches a DISQUALIFY clause in TARGET\n\n"
            "CRITICAL: if the metadata (title/tags) clearly contradicts what TARGET requires "
            "(especially any DISQUALIFY clause), score ≤ 3 regardless of thumbnail. "
            "Metadata describes the full clip across all frames, not just the poster frame.\n\n"
            f"Return a JSON array of exactly {n} objects in order:\n"
            '[{"score": number, "reason": "short reason referencing both thumbnail AND metadata"}, ...]\n\n'
            "Raw JSON only, no markdown."
        )})
        return content, local_valid

    def _build_content_multiframe(batch_clips):
        """Multi-frame scoring with video + moment context and STEP 1/STEP 2 evaluation."""
        content = []
        local_valid = []
        for li, clip in enumerate(batch_clips):
            frames = clip.get("_frames", [])
            if not frames and not clip.get("thumbnail"):
                continue
            local_valid.append(li)
            title    = clip.get("title", "")
            tags_str = ", ".join(clip.get("tags", [])) if clip.get("tags") else ""
            dur      = clip.get("duration", "")
            if frames:
                for _t, jpeg in frames:
                    b64 = _b64.b64encode(jpeg).decode()
                    content.append({"type": "image",
                                     "source": {"type": "base64",
                                                "media_type": "image/jpeg", "data": b64}})
                frames_note = f"{len(frames)} frames sampled across clip"
            else:
                content.append({"type": "image",
                                 "source": {"type": "url", "url": clip["thumbnail"]}})
                frames_note = "1 thumbnail (frame sampling failed)"
            parts = []
            if title:    parts.append(f"Title: {title}")
            if tags_str: parts.append(f"Tags: {tags_str}")
            if dur:      parts.append(f"Duration: {dur}s")
            meta = " | ".join(parts) or "(no metadata)"
            content.append({"type": "text",
                             "text": f"[Clip {len(local_valid)}: {meta} | {frames_note}]"})
        if not local_valid:
            return content, local_valid
        n = len(local_valid)
        content.append({"type": "text", "text": (
            "You are scoring stock video clips for B-roll relevance.\n\n"
            f"{_vc_prefix}"
            f"{_mc_prefix}"
            f"EVALUATION TARGET: {strict_eval}\n\n"
            f"Score each of the {n} clips (1–10). For each clip:\n\n"
            "STEP 1 (mandatory first): Examine ALL frames, not just the first. Does the clip's "
            "actual content trigger any DISQUALIFY clause in EVALUATION TARGET? Also check if "
            "title/tags clearly contradict what the target requires. "
            "If yes → score 1–3, set step1_disqualified: true. STOP.\n\n"
            "STEP 2 (only if STEP 1 passes): How well does the clip serve the moment given the "
            "video's register and topic? Penalise tonal mismatch even if surface match exists "
            "(e.g. cheerful office footage in an intimate-confession video → ≤4 even if "
            "technically on-topic). Score 4–10 for varying degrees of fit.\n\n"
            f"Return a JSON array of exactly {n} objects:\n"
            '[{"score": number, "step1_disqualified": boolean, '
            '"frames_observed": "1 sentence on what frames actually show across the clip", '
            '"reason": "specific reason citing frames AND metadata"}, ...]\n\n'
            "Raw JSON only, no markdown."
        )})
        return content, local_valid

    # ── Async frame sampling ──────────────────────────────────────────────────

    async def _sample_clip_frames_async(clip, ffmpeg_sem):
        """Download preview_url and extract 4 frames. Attaches _frames to clip dict."""
        url = clip.get("preview_url") or clip.get("thumbnail", "")
        if not url:
            return
        cache_key = (url, 4, "skip_intro")
        if cache_key in _frame_cache:
            clip["_frames"] = _frame_cache[cache_key]
            return
        async with ffmpeg_sem:
            try:
                frames = await asyncio.to_thread(sample_frames, url, 4, "skip_intro")
                clip["_frames"] = frames
                if not frames:
                    clip["frame_sample_failed"] = True
            except Exception as e:
                clip["_frames"] = []
                clip["frame_sample_failed"] = True
                print(f"[frames] async sampling error clip id={clip.get('id')}: {e}")

    # ── Primary scoring (parallel batches) ───────────────────────────────────

    async def _score_batch(orig_indices, batch_clips, haiku_sem):
        if use_multiframe:
            content, local_valid = _build_content_multiframe(batch_clips)
        else:
            content, local_valid = _build_content_thumbnail(batch_clips)
        if not local_valid:
            return
        local_to_orig = [orig_indices[li] for li in local_valid]

        async with haiku_sem:
            raw_response = None
            try:
                raw_response = await asyncio.to_thread(
                    _call_haiku_sync, content, max_tokens, HAIKU_SCORING_TEMPERATURE
                )
                _apply_scores(_parse_json_array(raw_response), local_to_orig)
                return
            except Exception as e:
                print(
                    f"[stock] Haiku batch ({len(local_valid)} clips) failed (attempt 1): {e}\n"
                    f"RAW: {repr(raw_response)[:300]}"
                )

            # Retry with first half of the batch
            half        = max(1, len(local_valid) // 2)
            retry_local = local_valid[:half]
            retry_orig  = [orig_indices[li] for li in retry_local]
            retry_clips = [batch_clips[li] for li in retry_local]
            if use_multiframe:
                retry_content, retry_lv = _build_content_multiframe(retry_clips)
            else:
                retry_content, retry_lv = _build_content_thumbnail(retry_clips)
            if not retry_lv:
                return
            retry_l2o = [retry_orig[li] for li in retry_lv]
            raw2 = None
            try:
                raw2 = await asyncio.to_thread(
                    _call_haiku_sync, retry_content, max_tokens, HAIKU_SCORING_TEMPERATURE
                )
                _apply_scores(_parse_json_array(raw2), retry_l2o)
            except Exception as e2:
                skipped = [clips[i].get("id", i) for i in retry_l2o]
                print(
                    f"[stock] Batch retry failed: {e2}\n"
                    f"RAW2: {repr(raw2)[:200]}\n"
                    f"Skipping clip IDs: {skipped}"
                )

    # ── Disqualify sanity-check (one call per high-scoring clip) ──────────────

    async def _check_sanity(orig_i, clip, disqualify_clause, haiku_sem):
        title    = clip.get("title", "")
        tags_str = ", ".join(clip.get("tags", [])) if clip.get("tags") else ""
        prompt   = (
            f"Quick check. A clip has these properties:\n"
            f"  Title: {title}\n"
            f"  Tags: {tags_str}\n\n"
            f"An evaluation target says to disqualify clips matching: '{disqualify_clause}'\n\n"
            "Does the title or tags clearly indicate this clip matches the disqualifier? "
            "Examples of clear contradiction:\n"
            "  - Disqualify 'welcoming gestures': title 'high five' or tag 'celebration' → YES contradiction\n"
            "  - Disqualify 'happy energetic': title 'tired exhausted' → NO contradiction\n"
            "  - Disqualify 'physical contact': tag 'embrace' → YES contradiction\n\n"
            'Respond ONLY with JSON: {"contradicts": boolean, "reason": "short reason citing specific title or tag"}'
        )
        async with haiku_sem:
            try:
                msg = await asyncio.to_thread(
                    _call_haiku_sync,
                    [{"type": "text", "text": prompt}],
                    HAIKU_SANITY_MAX_TOKENS,
                    HAIKU_SANITY_TEMPERATURE,
                )
                raw_text = msg
                if "```" in raw_text:
                    for part in raw_text.split("```"):
                        part = part.strip().lstrip("json").strip()
                        if part.startswith("{"):
                            raw_text = part
                            break
                result = _json.loads(raw_text)
                if result.get("contradicts"):
                    orig_score = clips[orig_i]["score"]
                    clips[orig_i]["score"] = 2
                    clips[orig_i]["score_reason"] = f"[OVERRIDE] {clips[orig_i].get('score_reason', '')}"
                    print(
                        f"[stock] sanity-check override: clip '{title}' was {orig_score}/10 "
                        f"but title/tags contradict disqualifier '{disqualify_clause}': "
                        f"{result.get('reason', '')}"
                    )
            except Exception as e:
                print(f"[stock] sanity-check failed for clip '{title}': {e}")

    # ── Orchestrate ───────────────────────────────────────────────────────────

    async def _run_all():
        haiku_sem = asyncio.Semaphore(HAIKU_SCORING_CONCURRENCY)
        all_orig  = [i for i, _ in has_thumb]
        all_clips = [c for _, c in has_thumb]

        # Phase 0 (multi-frame only): sample frames for all clips concurrently
        if use_multiframe:
            ffmpeg_sem = asyncio.Semaphore(8)  # cap concurrent ffmpeg + download ops
            await asyncio.gather(*[
                _sample_clip_frames_async(c, ffmpeg_sem) for c in all_clips
            ])
            n_ok   = sum(1 for c in all_clips if c.get("_frames"))
            n_fail = sum(1 for c in all_clips if c.get("frame_sample_failed"))
            print(f"[frames] {n_ok} clips sampled OK, {n_fail} fell back to thumbnail")

        # Phase 1: primary scoring
        primary_tasks = [
            _score_batch(
                all_orig[b:b + batch_size],
                all_clips[b:b + batch_size],
                haiku_sem,
            )
            for b in range(0, len(all_orig), batch_size)
        ]
        await asyncio.gather(*primary_tasks)

        # Phase 2: disqualify override for high scorers
        disqualify_clause = extract_disqualify_clause(strict_eval)
        if disqualify_clause:
            high_scorers = [
                (i, c) for i, c in enumerate(clips)
                if c.get("score", 0) >= HAIKU_SANITY_MIN_SCORE and c.get("thumbnail")
            ]
            if high_scorers:
                await asyncio.gather(*[
                    _check_sanity(i, c, disqualify_clause, haiku_sem)
                    for i, c in high_scorers
                ])

    asyncio.run(_run_all())

    # Strip raw frame bytes — not JSON-serialisable and not needed downstream
    for c in clips:
        c.pop("_frames", None)

    passing = [c for c in clips if c.get("score", 0) >= 6]
    return sorted(passing, key=lambda c: c["score"], reverse=True)


def add_clip_window(clip: dict, broll_duration: float) -> dict:
    """Select the middle window of a stock clip to avoid static start/end frames."""
    stock_dur = float(clip.get("duration") or 0)
    if stock_dur <= 0 or stock_dur <= broll_duration:
        clip["clip_use_start_seconds"] = 0.0
        clip["clip_use_end_seconds"]   = round(stock_dur, 2) if stock_dur > 0 else round(broll_duration, 2)
        clip["clip_window_strategy"]   = "padded" if 0 < stock_dur < broll_duration else "whole"
    else:
        mid = round((stock_dur - broll_duration) / 2, 2)
        clip["clip_use_start_seconds"] = mid
        clip["clip_use_end_seconds"]   = round(mid + broll_duration, 2)
        clip["clip_window_strategy"]   = "middle"
    return clip


# ---------------------------------------------------------------------------
# Video context — one Sonnet call per video that grounds all downstream steps
# in the video's actual visual register, speaker presence, and topic.
# ---------------------------------------------------------------------------

_video_context_cache: dict = {}  # (video_key, transcript_hash) → context dict


def _get_video_context(video_path: str, transcript: str, client) -> dict:
    """Sample 10 frames from the user's video, send to Sonnet with the transcript,
    and return a structured understanding of the video's genre, register, and B-roll fit.
    Returns {} on any failure — callers treat missing context as graceful degradation.
    """
    import hashlib, json as _json, base64 as _b64
    cache_key = (video_path, hashlib.md5(transcript.encode()).hexdigest())
    if cache_key in _video_context_cache:
        print("[ctx] video context cache hit")
        return _video_context_cache[cache_key]

    frames = sample_frames(video_path, n_frames=10, strategy="evenly_spaced")
    if not frames:
        print(f"[ctx] frame sampling failed for {video_path!r} — skipping video context")
        return {}

    print(f"[ctx] sampled {len(frames)} frames from user video, calling Sonnet for context")
    content = []
    for t, jpeg in frames:
        b64 = _b64.b64encode(jpeg).decode()
        content.append({"type": "image",
                         "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}})
    content.append({"type": "text", "text": (
        "Analyze this Hebrew speech video. Examine the frames in order, the speaker's "
        "presence, setting, apparent emotional delivery, and read the corrected transcript. "
        "Produce a structured understanding as JSON.\n\n"
        f"Transcript (Hebrew with timestamps):\n<transcript>\n{transcript[:6000]}\n</transcript>\n\n"
        "Return ONLY a JSON object with these fields:\n"
        '- "video_genre": one of "personal monologue" | "educational" | "interview" | "tutorial" | "story" | "other"\n'
        '- "speaker_presence": one of "central focus" | "partial" | "absent"\n'
        '- "setting_description": 1-2 sentences on visible environment and setting\n'
        '- "speaker_emotional_register": 1 phrase describing the speaker\'s emotional delivery style '
        '(e.g. "intimate confession", "didactic teaching", "warm storytelling", "professional")\n'
        '- "topic_summary": 2-3 sentences on what this video is about and the speaker\'s apparent intent\n'
        '- "cultural_context_notes": anything specific to the content (Hebrew cultural references, '
        'religious context, niche topic) that affects what visual B-roll would feel appropriate vs jarring. '
        'Empty string if nothing notable.\n'
        '- "broll_style_recommendation": what visual register fits this video — '
        '"abstract/symbolic", "literal/concrete", "mood/atmospheric", "narrative" — and a brief reason\n'
        '- "sensitive_topics": array of strings listing any themes requiring care in B-roll selection '
        '(e.g. "discusses intimate relationships — avoid casual or romantic touch imagery"). '
        'Empty array [] if none.\n\n'
        "Raw JSON only, no markdown, no code blocks."
    )})

    try:
        resp = client.messages.create(
            model=SONNET_MODEL,
            max_tokens=1024,
            temperature=0.3,
            messages=[{"role": "user", "content": content}],
        )
        raw = resp.content[0].text.strip()
        if "```" in raw:
            for part in raw.split("```"):
                part = part.strip().lstrip("json").strip()
                if part.startswith("{"):
                    raw = part
                    break
        ctx = _json.loads(raw)
        print(f"[ctx] video context: genre={ctx.get('video_genre')!r}, "
              f"register={ctx.get('speaker_emotional_register')!r}, "
              f"sensitive={ctx.get('sensitive_topics')}")
        _video_context_cache[cache_key] = ctx
        return ctx
    except Exception as e:
        print(f"[ctx] _get_video_context failed: {e}")
        return {}


# ---------------------------------------------------------------------------
# Stock B-roll analysis — finds moments in transcript + searches Pexels/Pixabay
# ---------------------------------------------------------------------------
@app.function(
    image=image,
    timeout=600,
    volumes={TMP_DIR: tmp_vol},
    secrets=[
        modal.Secret.from_name("anthropic-secret"),
        modal.Secret.from_name("pexels-secret"),
        modal.Secret.from_name("pixabay-secret"),
    ],
)
def _process_moment(m: dict, pexels_key: str, pixabay_key: str, client,
                    video_context: dict, max_candidates: int = 40) -> None:
    """Search stock libraries + score clips for one B-roll moment. Mutates m in place.
    Designed to run in a ThreadPoolExecutor — creates its own requests.Session so
    threads don't share state, and score_clips can safely call asyncio.run()."""
    import requests as _r
    http_session = _r.Session()
    try:
        broll_dur     = m.get("broll_duration_seconds", 3.0)
        variants      = m.get("search_variants") or [m.get("broad_search_prompt", m.get("search_query", ""))]
        variants      = [v for v in variants if v]
        seen_ids      = set()
        clips         = []
        variant_stats = []

        for variant in variants:
            pex = fetch_pexels(variant, 1, pexels_key, http_session)
            pix = fetch_pixabay(variant, 1, pixabay_key, http_session)
            new_clips = []
            for i in range(max(len(pex), len(pix))):
                if i < len(pex):
                    c = dict(pex[i]); vid_id = f"pexels_{c['id']}"
                    if vid_id not in seen_ids:
                        seen_ids.add(vid_id); c["_source_variant"] = variant; new_clips.append(c)
                if i < len(pix):
                    c = dict(pix[i]); vid_id = f"pixabay_{c['id']}"
                    if vid_id not in seen_ids:
                        seen_ids.add(vid_id); c["_source_variant"] = variant; new_clips.append(c)
            variant_stats.append({"variant": variant, "count": len(new_clips)})
            clips.extend(new_clips)

        if not clips and variants:
            first = variants[0]; words = first.split()
            short = " ".join(words[:2]) if len(words) > 2 else first
            if short != first:
                print(f"[stock] 0 candidates across all variants for moment@{m['start']:.0f}s, "
                      f"retrying with {short!r}")
                pex2 = fetch_pexels(short, 1, pexels_key, http_session)
                pix2 = fetch_pixabay(short, 1, pixabay_key, http_session)
                for i in range(max(len(pex2), len(pix2))):
                    if i < len(pex2): clips.append(pex2[i])
                    if i < len(pix2): clips.append(pix2[i])

        clips = clips[:max_candidates]
        vlog  = ", ".join(f"'{s['variant']}': {s['count']}" for s in variant_stats)
        print(f"[stock] moment@{m['start']:.0f}s: {len(clips)} candidates "
              f"({len(variants)} variant(s)) — {vlog}")

        if not clips:
            print(f"[stock] no portrait candidates for moment@{m['start']:.0f}s — weak_match")
            m["clips"] = []; m["weak_match"] = True; m["_variant_stats"] = variant_stats
        else:
            strict_eval  = m.get("strict_eval_prompt") or m.get("search_query", "")
            moment_ctx_s = {
                "transcript_excerpt": m.get("transcript_excerpt", ""),
                "key_insight":        m.get("key_insight", ""),
                "visual_anchor":      m.get("visual_anchor", ""),
                "reasoning":          m.get("reasoning", ""),
            }
            scored = score_clips(clips, strict_eval, client,
                                 video_context=video_context or None,
                                 moment_ctx=moment_ctx_s)
            m["clips"]          = [add_clip_window(c, broll_dur) for c in scored]
            m["_variant_stats"] = variant_stats
            if scored:
                w = scored[0]
                print(f"[stock] winner moment@{m['start']:.0f}s: score={w.get('score')}, "
                      f"variant='{w.get('_source_variant','?')}', "
                      f"reason={w.get('score_reason','')[:80]!r}")
            if not scored:
                m["weak_match"] = True
    finally:
        http_session.close()


@app.function(
    image=image,
    timeout=600,
    volumes={TMP_DIR: tmp_vol},
    secrets=[
        modal.Secret.from_name("anthropic-secret"),
        modal.Secret.from_name("pexels-secret"),
        modal.Secret.from_name("pixabay-secret"),
    ],
)
def analyze_stock_broll(captions_json: str, video_key: str = "") -> list:
    import json, os, requests as _req

    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    pexels_key    = os.environ.get("PEXELS_API_KEY", "")
    pixabay_key   = os.environ.get("PIXABAY_API_KEY", "")

    # One session reused for all Pexels + Pixabay calls — avoids repeated TCP handshakes
    http_session = _req.Session()

    if not anthropic_key:
        raise ValueError("ANTHROPIC_API_KEY secret not configured.")
    if not pexels_key and not pixabay_key:
        raise ValueError("PEXELS_API_KEY or PIXABAY_API_KEY secret not configured.")

    captions = json.loads(captions_json)
    if not captions:
        return []

    lines = []
    for cap in captions:
        ms = int(cap["start"] // 60); ss = cap["start"] % 60
        me = int(cap["end"]   // 60); se = cap["end"]   % 60
        lines.append(f"[{ms}:{ss:04.1f}–{me}:{se:04.1f}] {cap['text']}")
    transcript = _sanitize_transcript("\n".join(lines))

    import anthropic as _anthropic
    from pathlib import Path as _Path
    client = _anthropic.Anthropic(api_key=anthropic_key)

    # --- Video context pass (Phase 2) ---
    # Sample frames from the user's video and ask Sonnet to characterise its
    # genre, register, and B-roll style fit. Result grounds all downstream steps.
    video_context = {}
    if video_key:
        tmp_vol.reload()
        _vpath = _Path(TMP_DIR) / video_key
        if _vpath.exists():
            video_context = _get_video_context(str(_vpath), transcript, client)
        else:
            print(f"[ctx] video_key={video_key!r} not found in volume — skipping context")

    # Build system blocks: video context first (if available), then the main instruction block.
    # The context block is NOT marked cache_control so it varies per video while the stable
    # instruction block stays cached across calls.
    _ctx_blocks = []
    if video_context:
        sensitive_str = ("; ".join(video_context.get("sensitive_topics", [])) or "none")
        _ctx_blocks = [{
            "type": "text",
            "text": (
                "VIDEO CONTEXT (analysed from the video frames before this call):\n"
                f"Genre: {video_context.get('video_genre', 'unknown')}\n"
                f"Speaker presence: {video_context.get('speaker_presence', 'unknown')}\n"
                f"Setting: {video_context.get('setting_description', '')}\n"
                f"Speaker register: {video_context.get('speaker_emotional_register', 'unknown')}\n"
                f"Topic: {video_context.get('topic_summary', '')}\n"
                f"Cultural context: {video_context.get('cultural_context_notes', '')}\n"
                f"B-roll style fit: {video_context.get('broll_style_recommendation', '')}\n"
                f"Sensitive topics: {sensitive_str}\n\n"
                "Use this context throughout. Your search_variants and strict_eval_prompts must "
                "reflect the video's actual register and avoid imagery that feels jarring given "
                "the speaker's intent and any sensitive topics listed above.\n"
            ),
        }]

    system_content = _ctx_blocks + [
        {
            "type": "text",
            "text": (
                "You are a professional video editor selecting stock B-roll moments for a Hebrew speech video.\n\n"
                "You select B-roll moments in TWO PASSES:\n\n"
                "PASS 1 — EMPHASIS (primary moments)\n"
                "Identify moments that genuinely deserve B-roll on their own merit: emotional peaks, intensity markers, concrete visualizable subjects, narrative climaxes, key insights. Use all criteria below (TWO FLAVORS, emotional signals, intensity scoring). Label these confidence: 'high' or 'medium' depending on strength.\n\n"
                "PASS 2 — COVERAGE (rhythm moments)\n"
                "After choosing emphasis moments, scan for uncovered gaps. If any stretch between two consecutive emphasis moments, or between the 0:03 opening buffer and the first emphasis moment, or between the last emphasis moment and the video_end−2s closing buffer, exceeds 12 seconds — you MUST add a coverage moment inside that gap.\n"
                "Coverage moment rules:\n"
                "  • Pick the best available moment within the gap, even if it is not a natural emphasis peak.\n"
                "  • Set moment_type: 'coverage'. Confidence: 'low' by default; 'medium' if it has a solid visual anchor.\n"
                "  • Assign a real visual_anchor and both search prompts using whatever concrete or emotional content exists in the gap.\n"
                "  • If the gap contains only pure filler with nothing visualizable, skip coverage for that gap and note it as an extra object: {\"skipped_coverage\": \"gap 0:22–0:45 — no usable content\"}.\n\n"
                "BUFFERS — HARD RULES (non-negotiable):\n"
                "  • Do NOT place any moment whose start_seconds < 3 (opening 3 seconds — speaker establishes presence).\n"
                "  • Do NOT place any moment whose broll_end_seconds > video_end − 2 (closing 2 seconds — conclusions land here).\n\n"
                "TARGET RHYTHM: A B-roll moment every 10–12 seconds across the video is ideal. Maximum density: 1 moment per 8 seconds averaged across the whole video. Minimum 8 seconds between consecutive B-roll windows (broll_end of one → broll_start of next).\n\n"
                "MINIMUM MOMENT COUNTS — use coverage to reach the floor if emphasis falls short:\n"
                "  ≤ 30 s    → at least 1 moment\n"
                "  31–45 s   → at least 2 moments\n"
                "  46–60 s   → at least 3 moments\n"
                "  61–90 s   → at least 4 moments\n"
                "  91–120 s  → at least 5 moments\n"
                "  121–180 s → at least 6 moments\n"
                "  > 180 s   → at least 1 moment per 25 seconds\n"
                "To account for post-filter drops (buffer and spacing rules), aim to return 1–2 moments above the minimum floor. If emphasis alone falls short, fill with coverage moments. If combined they still fall short, lower your strictness threshold and include next-best moments labeled 'low' confidence rather than returning fewer than the minimum.\n\n"
                "GENERAL RULES:\n"
                "  • Filler, throat-clearing, generic statements, and low-information sentences do NOT warrant B-roll.\n"
                "  • Choose moments with a SPECIFIC, NON-OBVIOUS claim — not generic filler.\n"
                "  • Each moment must be distinct.\n"
                "  • reasoning field must be in Hebrew.\n\n"

                "B-ROLL COMES IN TWO FLAVORS — RECOGNIZE BOTH:\n\n"

                "CONCRETE moments have a visualizable subject — the viewer benefits from seeing the thing being discussed "
                "(yoga instructor, pouring coffee, late-night office). moment_type: \"concrete\".\n"
                "First-person ACTION narrations are always CONCRETE — if the speaker narrates something they physically did "
                "('I brushed my teeth', 'I went to the gym', 'I cooked dinner', 'I opened my laptop'), show that action in stock footage. "
                "The action verb + its object is the subject; anchor both in every search variant.\n\n"

                "EMOTIONAL moments have no concrete subject but carry strong narrative weight — the viewer benefits from "
                "a symbolic or emotional visual that amplifies the feeling. These are peaks, confessions, realizations, "
                "vulnerabilities, breakthroughs, moments of struggle or triumph. Good editors ALWAYS cut to B-roll on "
                "these — typically a pensive face, hands, eyes, symbolic imagery (windows, doors, paths, water, light), "
                "or abstract textures. moment_type: \"emotional\".\n\n"

                "HYBRID moments have both: a concrete subject wrapped in emotional framing "
                "(exhausted founder at an empty desk). moment_type: \"hybrid\".\n\n"
                "COVERAGE moments (Pass 2 only) are the best available moments inside uncovered gaps — not peaks on their own merit, but genuine content that fills dead stretches. moment_type: \"coverage\".\n\n"

                "CONCRETE ACTION SIGNALS — any first-person past-tense or habitual action verb is a B-roll trigger:\n"
                "  עשיתי (I did), הלכתי (I went), קמתי (I got up), הכנתי (I made/prepared), פתחתי (I opened),\n"
                "  נסעתי (I drove/traveled), אכלתי (I ate), שתיתי (I drank), ניקיתי (I cleaned), כתבתי (I wrote),\n"
                "  צחצחתי שיניים (I brushed my teeth), הלכתי לחדר כושר (I went to the gym),\n"
                "  בישלתי (I cooked), קניתי (I bought), פגשתי (I met), שיחקתי (I played), רצתי (I ran).\n"
                "  Rule: speaker narrates a physical act → it is CONCRETE, moment_type: 'concrete'. Search for the action being performed.\n\n"
                "EMOTIONAL MOMENT SIGNALS — flag a moment with 2+ signals (or 1 strong intensity marker alone is enough):\n\n"
                "  A. Emotion words:\n"
                "     הרגשתי / אני מרגיש/ה (I felt/feel), כאב (pain), שבור/ה (broken), מנפץ/ת (shattering),\n"
                "     עצוב (sad), שמח (happy), מופתע (surprised), מתוסכל (frustrated), אבוד (lost), חופשי (free),\n"
                "     עמוק (deep), פחד (fear), בושה (shame), כמיהה (longing)\n\n"
                "  B. Intensity markers — superlatives, absolutes, extremes (1 alone raises the score):\n"
                "     מדהים/מדהימה (amazing/incredible), נהדר (wonderful), נורא (terrible), הכי (most),\n"
                "     תמיד (always), אף פעם (never), בחיים (ever / in life), לגמרי (completely),\n"
                "     לחלוטין (absolutely), כל (every / all), ממש (really / truly)\n"
                "     Example: 'יכולת מדהימה' ('incredible ability') → B-roll worthy from 'מדהימה' alone.\n\n"
                "  C. Struggle / transformation language:\n"
                "     לא מצליח/ה (can't / failing to), סתום/ה (blocked), תקוע/ה (stuck),\n"
                "     שיניתי (I changed), גיליתי (I discovered), הבנתי (I understood), לפני / אחרי (before / after)\n\n"
                "  D. First-person vulnerability:\n"
                "     Sentences starting with 'אני' (I) followed by emotional disclosure are narrative peaks.\n\n"
                "  E. Rhetorical peaks:\n"
                "     Sentences that are clearly the climax the speaker has been building toward — often shorter and "
                "punchier than surrounding text, ending a thought or turning a corner.\n\n"
                "RULE: a sentence does NOT need a concrete noun to deserve B-roll. If it hits 2+ signals (or 1 strong "
                "intensity marker), it is an emotional B-roll moment — include it with at least 'medium' confidence.\n\n"

                "For EMOTIONAL moments, visual_anchor and broad_search_prompt use symbolic conventions:\n"
                "  struggle/blocked → broad: 'pensive person face close-up'; visual_anchor: 'person staring blankly, hands over face, narrow dark corridor'\n"
                "  breakthrough/realization → broad: 'person looking up hope light'; visual_anchor: 'person looking upward toward light, window opening, sunrise'\n"
                "  shattered/broken → broad: 'breaking glass slow motion'; visual_anchor: 'breaking glass slow-motion, cracked surface, fragments falling'\n"
                "  amazement/wonder → broad: 'wonder awe face close-up'; visual_anchor: 'wide eyes close-up, awe-struck face, light catching hands'\n"
                "  transformation → broad: 'person walking forward sunrise'; visual_anchor: 'person walking forward into light, sunrise, new beginning'\n"
                "  free/liberated → broad: 'person arms open field sky'; visual_anchor: 'open sky, arms outstretched, wind in hair'\n"
                "For emotional moments, strict_eval_prompt focuses on EMOTIONAL TONE, not a literal scene.\n\n"

                "Assign each moment an intensity_score (1–10) — how emotionally / narratively charged it is, regardless of concreteness:\n"
                "  9–10: unmistakably a peak moment the audience will remember\n"
                "  7–8:  strong — clear emotional weight or important claim\n"
                "  5–6:  moderate — worth including, not transformative\n"
                "  3–4:  weak — borderline, filler-adjacent\n"
                "  1–2:  skip\n\n"

                "Assign each moment a confidence level:\n"
                "  'high'   — intensity_score ≥ 8 AND (concrete subject OR multiple emotional signals with clear narrative weight) — emphasis moments only\n"
                "  'medium' — intensity_score 6–7, OR emotional with one strong signal, OR coverage with a solid visual anchor\n"
                "  'low'    — intensity_score ≤ 5, OR borderline, OR coverage moment in a thin/filler gap\n\n"

                "You output TWO search fields per moment — a retrieval field and a scoring field. They serve different purposes.\n\n"

                "search_variants — an array of conceptually DISTINCT visual concepts that all express the moment's meaning.\n"
                "The principle: stock libraries tag clips with many words for the same concept. A clip showing 'broken mirror' won't appear in results for 'breaking glass' even though both express 'shattered.' You must cover the conceptual space, not just one phrasing.\n\n"
                "VARIANT GENERATION RULES — depend on moment_type:\n\n"
                "For CONCRETE moments (visualizable subject is the point):\n"
                "  Every variant MUST include the concrete subject. Variants differ only in framing, angle, action, or composition.\n"
                "  Subject 'yoga instructor and student':\n"
                "    GOOD: ['yoga instructor student teaching', 'yoga teacher guiding student class', 'yoga session instructor student mat', 'yoga studio one on one lesson']\n"
                "    BAD:  ['helping hand supportive', 'guidance gentle teaching', 'mentoring relationship'] — abstracted away the subject\n\n"
                "For HYBRID moments (concrete subject + emotional framing):\n"
                "  Same rule as concrete: anchor the subject in every variant. Emotional framing modifies HOW the subject is shown, not WHETHER it appears.\n"
                "  Subject 'yoga instructor', framing 'gentle support':\n"
                "    GOOD: ['yoga instructor gentle guidance student', 'yoga teacher supportive student', 'yoga instructor light correction student', 'yoga session careful teaching style']\n"
                "    BAD:  ['helping hand gentle assistance', 'supportive touch relationship'] — yoga context is lost; these match any gentle-contact clip\n\n"
                "For EMOTIONAL moments (no concrete subject — feeling or transformation only):\n"
                "  Conceptual diversity is correct — different visual metaphors all expressing the same feeling. Variants diverge intentionally.\n"
                "  Concept 'shattered/broken':\n"
                "    GOOD: ['broken mirror reflection', 'shattered glass slow motion', 'cracked surface texture', 'broken pieces falling']\n\n"
                "For COVERAGE moments:\n"
                "  Apply the rule of the underlying type — coverage is a pacing label, not a generation rule.\n\n"
                "Universal requirements:\n"
                "  • Each variant: 2-5 words, keyword-style, optimized for retrieval.\n"
                "  • All variants must serve the same strict_eval_prompt (scoring filters quality).\n"
                "  • Count: 2-3 variants for concrete/hybrid; 3-5 for emotional/symbolic.\n\n"
                "Self-check before finalising variants: for concrete and hybrid moments, ask yourself 'Does every variant contain at least one specific noun from key_insight or transcript_excerpt?' If any variant omits the subject, regenerate it before output.\n\n"

                "strict_eval_prompt: a full sentence capturing the specific insight (or emotional tone) AND what would disqualify a clip. Always include a DISQUALIFY clause. Used for scoring only, never for search.\n\n"
                "SUBJECT ANCHORING RULE: if moment_type is concrete or hybrid, strict_eval_prompt MUST name the concrete subject. Without it, scoring loses context and generic-but-on-tone clips will pass.\n\n"
                "  WRONG (hybrid — subject abstracted away):\n"
                "    'supportive, gentle assistance showing helping rather than controlling. DISQUALIFY: forceful manipulation, controlling touch.'\n"
                "    → matches any gentle-contact clip: holding hands, parent-child, friends. The yoga context is gone.\n\n"
                "  RIGHT (hybrid — subject anchored):\n"
                "    'yoga instructor providing gentle guidance to student — light touch supporting student's own movement, pedagogical context. DISQUALIFY: holding hands romantically or casually, parent-child contact, friends or strangers touching, any gentle contact outside a teaching context.'\n"
                "    → scoring now rejects the false-positive clip families explicitly.\n\n"
                "For emotional moments: no concrete subject exists, so strict_eval_prompt focuses on emotional register and visual conventions — existing rules apply.\n\n"
                "DISQUALIFY clause must explicitly name common false-positive categories. Ask: what other contexts share these same gestures or visuals? Name them. For a yoga instructor guiding a student: think holding hands romantically, parent-child contact, friends supporting each other — name all three.\n\n"
                "  Examples:\n"
                "    concrete: \"yoga instructor guiding student with minimal or no physical contact, verbal guidance or hand-hovering only. DISQUALIFY: hands-on adjustments, physical corrections, instructor touching student.\"\n"
                "    hybrid:   \"exhausted, defeated-looking founder at desk showing professional burnout — slumped posture, dim or empty environment. DISQUALIFY: energetic, productive, or celebratory work imagery; person looks focused, content, or active.\"\n"
                "    emotional: \"person with frustrated or blocked expression — introspective, searching, unable to find words. DISQUALIFY: happy, relaxed, confident, or productive-looking person.\"\n\n"

                "DURATION RULES — for each moment, set B-roll timing using the transcript timestamps:\n\n"
                "a) ALIGN TO SPEECH. B-roll starts with the phrase introducing the insight and ends at a natural pause (sentence end, clause boundary, or silence gap). Use the actual start/end timestamps from the transcript lines — no invented round numbers.\n\n"
                "b) CLAMP TO 2–5 SECONDS. Under 2s reads as a glitch. Over 5s loses the speaker.\n\n"
                "c) SCALE BY SUBJECT COMPLEXITY:\n"
                "   - simple (one concrete action/object, or a short emotional line): 2–3 seconds\n"
                "   - moderate (scene with one main focus, or an emotional passage): 3–4 seconds\n"
                "   - complex (abstract or multi-element: cityscape, conceptual scene): 4–5 seconds\n\n"
                "d) RESOLVE CONFLICTS. If speech window is 6s but subject is simple, cap at 3s. If speech is 1.5s but subject is complex, extend to 2s minimum.\n\n"
                "e) NEVER CUT MID-WORD. broll_end_seconds must fall on a word boundary — use the end timestamp of the last word in the key phrase, or the start of the next phrase (= silence gap).\n\n"
                "Write duration_reasoning as: '{speech_duration}s speech window, therefore {broll_duration}s B-roll ending at {natural pause type}.'\n\n"

                "Examples:\n\n"
                "Moment: \"כל בוקר אני קם וצוחצח שיניים לפני הכל\" (CONCRETE — first-person morning action, 2.5s at 0:18.0–0:20.5)\n"
                "  moment_type: \"concrete\", intensity_score: 5, intensity_markers: []\n"
                "  broll_start_seconds: 18.0, broll_end_seconds: 20.5, broll_duration_seconds: 2.5\n"
                "  duration_reasoning: \"2.5s speech window, therefore 2.5s B-roll — simple single action, ending at sentence-end pause.\"\n"
                "  key_insight: \"speaker brushes teeth every morning as the first thing they do\"\n"
                "  visual_anchor: \"person brushing teeth in bathroom\"\n"
                "  search_variants: [\"person brushing teeth bathroom\", \"morning routine teeth brushing\", \"toothbrush close-up dental hygiene\"]\n"
                "  strict_eval_prompt: \"person actively brushing teeth in bathroom — real action, close-up or medium shot. DISQUALIFY: toothbrush product shot without a person, dentist or clinical context, animated or illustrated teeth.\"\n"
                "  confidence: \"medium\"\n\n"
                "Moment: \"yoga corrections should be gentle, loose contact if at all\" (CONCRETE, 3.2s at 1:14.5–1:17.7)\n"
                "  moment_type: \"concrete\", intensity_score: 6, intensity_markers: []\n"
                "  broll_start_seconds: 74.5, broll_end_seconds: 77.7, broll_duration_seconds: 3.2\n"
                "  duration_reasoning: \"3.2s speech window, therefore 3.2s B-roll ending at sentence-end pause.\"\n"
                "  key_insight: \"yoga corrections should use minimal or no physical contact — verbal guidance only\"\n"
                "  search_variants: [\"yoga instructor student teaching\", \"yoga teacher demonstrating pose\", \"yoga class verbal instruction\"]\n"
                "  strict_eval_prompt: \"yoga instructor teaching student with minimal or no physical contact, verbal guidance or hand-hovering only. DISQUALIFY: hands-on adjustments, physical corrections, instructor touching student.\"\n"
                "  confidence: \"medium\"\n\n"

                "Moment: \"most founders burn out by year two\" (HYBRID, 2.1s at 0:45.0–0:47.1)\n"
                "  moment_type: \"hybrid\", intensity_score: 8, intensity_markers: [\"burn out\"]\n"
                "  broll_start_seconds: 45.0, broll_end_seconds: 47.1, broll_duration_seconds: 2.1\n"
                "  duration_reasoning: \"2.1s speech window, therefore 2.1s B-roll ending at sentence boundary.\"\n"
                "  key_insight: \"most founders burn out before achieving profitability\"\n"
                "  search_variants: [\"tired entrepreneur office late night\", \"founder slumped desk dim light\", \"empty startup office after hours\", \"exhausted professional head in hands\"]\n"
                "  strict_eval_prompt: \"exhausted or defeated-looking founder at desk showing professional burnout — slumped posture, dim or empty environment, end-of-day isolation. DISQUALIFY: energetic, productive, or celebratory work imagery; person looks focused, content, or active.\"\n"
                "  confidence: \"high\"\n\n"

                "Moment: \"המורה שלי תמיד עזרה לי להתיישב בצורה נכונה, לגעת קלות פה ושם\" (HYBRID — yoga instructor gentle correction, 3.1s at 1:22.4–1:25.5)\n"
                "  moment_type: \"hybrid\", intensity_score: 6, intensity_markers: []\n"
                "  broll_start_seconds: 82.4, broll_end_seconds: 85.5, broll_duration_seconds: 3.1\n"
                "  duration_reasoning: \"3.1s speech window, therefore 3.1s B-roll ending at clause-end pause.\"\n"
                "  key_insight: \"the yoga instructor used light touch to guide alignment, not control\"\n"
                "  search_variants: [\"yoga instructor gentle touch student\", \"yoga teacher guiding student correction\", \"yoga studio instructor adjustment\", \"yoga class instructor student hands\"]\n"
                "  strict_eval_prompt: \"yoga instructor providing light hands-on alignment correction to student in teaching context. DISQUALIFY: holding hands romantically or casually, parent-child contact, friends embracing, any gentle physical contact outside a yoga or coaching context.\"\n"
                "  confidence: \"medium\"\n\n"

                "Moment: \"וכל הזמן הרגשתי כאילו לא מצליחה להבין\" (EMOTIONAL — feeling blocked/failing, 3.5s at 2:15.0–2:18.5)\n"
                "  moment_type: \"emotional\", intensity_score: 8, intensity_markers: [\"הרגשתי\", \"לא מצליחה\"]\n"
                "  broll_start_seconds: 135.0, broll_end_seconds: 138.5, broll_duration_seconds: 3.5\n"
                "  duration_reasoning: \"3.5s speech window, therefore 3.5s B-roll ending at sentence-end pause.\"\n"
                "  key_insight: \"feeling perpetually blocked and unable to understand — a state of persistent confusion\"\n"
                "  search_variants: [\"pensive person face close-up\", \"frustrated woman looking down\", \"closed door narrow corridor\", \"fog covering path view\"]\n"
                "  strict_eval_prompt: \"person with frustrated or blocked expression — introspective, searching, unable to find words. DISQUALIFY: happy, relaxed, confident, or productive-looking person.\"\n"
                "  confidence: \"high\"\n\n"

                "Return ONLY a JSON array (any length) with these exact fields:\n"
                "- \"moment_type\": \"concrete\" | \"emotional\" | \"hybrid\" | \"coverage\"\n"
                "- \"intensity_score\": number 1–10, emotional/narrative charge of this moment\n"
                "- \"intensity_markers\": array of specific Hebrew words/phrases from the transcript that signal intensity (empty array [] if none)\n"
                "- \"broll_start_seconds\": when the B-roll starts — use the start timestamp of the phrase introducing the insight (number, from transcript)\n"
                "- \"broll_end_seconds\": per DURATION RULES above, 2–5s after broll_start_seconds (number)\n"
                "- \"broll_duration_seconds\": = broll_end_seconds - broll_start_seconds (number)\n"
                "- \"duration_reasoning\": one sentence: '{speech_duration}s speech window, therefore {broll_duration}s B-roll ending at {natural pause type}.'\n"
                "- \"transcript_excerpt\": the Hebrew text of the moment copied verbatim from the transcript above\n"
                "- \"key_insight\": the specific non-obvious claim or concrete subject being described, in English, one sentence\n"
                "- \"visual_anchor\": for concrete moments — the most searchable scene/object; for emotional moments — the symbolic B-roll convention being targeted (in English)\n"
                "- \"reasoning\": 1-2 sentences in Hebrew on why this moment deserves B-roll\n"
                "- \"search_variants\": array of 2-5 conceptually distinct stock-search queries (2-5 words each) — see guidance above\n"
                "- \"strict_eval_prompt\": full sentence with DISQUALIFY clause for scoring (see above)\n"
                "- \"confidence\": \"high\" | \"medium\" | \"low\" — based on intensity_score rules above\n\n"
                "No markdown, no code blocks — raw JSON array only."
            ),
            "cache_control": {"type": "ephemeral"},
        }
    ]

    def _call_sonnet_for_moments():
        r = client.messages.create(
            model=SONNET_MODEL,
            max_tokens=4096,
            temperature=SONNET_MOMENT_TEMPERATURE,
            system=system_content,
            messages=[{
                "role": "user",
                "content": f"Transcript (Hebrew with timestamps):\n<transcript>\n{transcript}\n</transcript>",
            }]
        )
        raw = r.content[0].text.strip()
        if "```" in raw:
            for part in raw.split("```"):
                part = part.strip().lstrip("json").strip()
                if part.startswith("["):
                    raw = part
                    break
        # Fallback: find first '[' in case Claude prefixed with explanatory text
        if not raw.startswith("["):
            idx = raw.find("[")
            if idx >= 0:
                raw = raw[idx:]
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            print(f"[stock] JSON parse failed: {raw[:300]}")
            return None
        if not isinstance(parsed, list):
            print(f"[stock] unexpected JSON type ({type(parsed).__name__}): {raw[:200]}")
            return None
        return parsed

    moments = _call_sonnet_for_moments()
    if moments is None or len(moments) == 0:
        # Retry once on parse failure or empty response (cold-container / transient Claude issue)
        print(f"[stock] first Sonnet call returned {'None' if moments is None else 'empty []'} — retrying")
        import time as _time
        _time.sleep(2)
        moments = _call_sonnet_for_moments()

    if moments is None:
        return {"moments": [], "total_moments_identified": 0, "moments_processed": 0,
                "cost_limit_hit": False, "video_context": video_context,
                "filter_stats": {"sonnet_moments_raw": 0, "buf_drops": 0,
                                 "spacing_drops": 0, "cost_cap_drops": 0}}
    if not moments:
        return {"moments": [], "total_moments_identified": 0, "moments_processed": 0,
                "cost_limit_hit": False, "video_context": video_context,
                "filter_stats": {"sonnet_moments_raw": 0, "buf_drops": 0,
                                 "spacing_drops": 0, "cost_cap_drops": 0}}

    sonnet_moments_raw = len(moments)

    # Compute video duration from captions for buffer enforcement
    video_end = max((c["end"] for c in captions), default=0.0)

    # Normalize field names and validate
    normalized = []
    buf_drops = 0
    for m in moments:
        if not isinstance(m, dict):
            continue
        # Skip skipped_coverage annotations from Sonnet
        if "skipped_coverage" in m and "start_seconds" not in m and "start" not in m:
            print(f"[stock] {m.get('skipped_coverage', 'skipped coverage note')}")
            continue
        start = float(m.get("broll_start_seconds", m.get("start_seconds", m.get("start", 0))))
        if start < 3.0:
            print(f"[stock] buffer drop: moment at {start:.1f}s before opening buffer (3.0s)")
            buf_drops += 1
            continue

        # B-roll timing: prefer new broll_* fields, fall back to legacy start/end_seconds
        broll_start = float(m.get("broll_start_seconds", m.get("speech_start_seconds", m.get("start_seconds", start))))
        broll_end   = float(m.get("broll_end_seconds",   m.get("speech_end_seconds",   broll_start + 3.0)))
        # Clamp duration to 2–5 seconds
        dur = broll_end - broll_start
        if dur < 2.0:   broll_end = broll_start + 2.0
        elif dur > 5.0: broll_end = broll_start + 5.0
        broll_start = round(broll_start, 2)
        broll_end   = round(broll_end, 2)
        broll_dur   = round(broll_end - broll_start, 2)

        # Closing buffer: drop any moment whose end falls within the last 2 seconds
        if video_end > 5.0 and broll_end > video_end - 2.0:
            print(f"[stock] buffer drop: moment ending at {broll_end:.1f}s within closing buffer "
                  f"(video_end={video_end:.1f}s)")
            buf_drops += 1
            continue

        # Parse search_variants — the array of conceptually distinct retrieval queries
        raw_variants = m.get("search_variants", [])
        if isinstance(raw_variants, list):
            variants = [v.strip() for v in raw_variants
                        if isinstance(v, str) and v.strip() and len(v.strip().split()) <= 10]
        else:
            variants = []
        variants = variants[:5]  # cap at 5 variants

        # Derive broad_search_prompt from variants (or fall back to legacy field)
        legacy_broad = m.get("broad_search_prompt", m.get("search_prompt", m.get("search_query", ""))).strip()
        if variants:
            broad = variants[0]          # first variant is the primary display query
        elif legacy_broad:
            broad = legacy_broad
            variants = [broad]
        else:
            broad = ""
            variants = []

        if not broad:
            print(f"[stock] WARNING: no search_variants and no broad_search_prompt for moment at {start}s")
        elif len(variants) == 1:
            print(f"[stock] WARNING: only 1 search variant for moment@{start}s — "
                  f"may miss candidates (expected 2-5)")

        strict = m.get("strict_eval_prompt", "").strip()
        if not strict:
            strict = broad

        m["start"]                = broll_start
        m["end"]                  = broll_end
        m["broll_start_seconds"]  = broll_start
        m["broll_end_seconds"]    = broll_end
        m["broll_duration_seconds"] = broll_dur
        m["duration_reasoning"]   = m.get("duration_reasoning", "")
        m["search_variants"]      = variants
        m["broad_search_prompt"]  = broad   # derived from search_variants[0]; kept for UI compat
        m["strict_eval_prompt"]   = strict
        m["search_query"]         = broad
        # Card label: first 6 words of key_insight; fall back to legacy topic/label fields
        _insight = m.get("key_insight", "").strip()
        _topic   = m.get("topic", m.get("label", "")).strip()
        if _insight:
            _words = _insight.split()
            m["label"] = " ".join(_words[:6]) + ("…" if len(_words) > 6 else "")
        else:
            m["label"] = _topic
        m["confidence"]           = m.get("confidence", "medium")
        if m["confidence"] not in ("high", "medium", "low"):
            m["confidence"] = "medium"
        m["moment_type"]          = m.get("moment_type", "concrete")
        if m["moment_type"] not in ("concrete", "emotional", "hybrid", "coverage"):
            m["moment_type"] = "concrete"
        raw_score = m.get("intensity_score", 5)
        try:
            m["intensity_score"]  = max(1, min(10, int(raw_score)))
        except (TypeError, ValueError):
            m["intensity_score"]  = 5
        markers = m.get("intensity_markers", [])
        m["intensity_markers"]    = markers if isinstance(markers, list) else []
        normalized.append(m)
    moments = normalized

    # Debug: two-pass breakdown + buffer summary
    n_emphasis = sum(1 for m in moments if m.get("moment_type") != "coverage")
    n_coverage = sum(1 for m in moments if m.get("moment_type") == "coverage")
    if buf_drops:
        print(f"[stock] buffer enforcement: dropped {buf_drops} moment(s)")
    print(f"[stock] 2-pass breakdown: emphasis={n_emphasis}, coverage={n_coverage}, "
          f"total={len(moments)} (video_end={video_end:.0f}s)")

    # ── Spacing filter ────────────────────────────────────────────────────────
    # 1. Even distribution: divide video into N equal slots, keep best per slot
    # 2. Minimum 6 s gap: drop/replace moments that crowd a neighbour
    n_before_spacing = len(moments)
    if len(moments) > 1:
        moments.sort(key=lambda m: m["broll_start_seconds"])
        # Use pre-computed video_end with fallback for empty-captions edge case
        video_end = video_end if video_end > 0 else moments[-1]["broll_end_seconds"]
        n = len(moments)
        _CONF = {"high": 0, "medium": 1, "low": 2}

        # Step 1 — even spacing
        slot_w = video_end / n
        slots: list = [[] for _ in range(n)]
        for m in moments:
            idx = min(int(m["broll_start_seconds"] / slot_w), n - 1)
            slots[idx].append(m)
        spaced = []
        seen_starts: set = set()
        for slot in slots:
            if not slot:
                continue
            best = min(slot, key=lambda m: (_CONF.get(m["confidence"], 1), m["broll_start_seconds"]))
            k = round(best["broll_start_seconds"], 2)
            if k not in seen_starts:
                spaced.append(best)
                seen_starts.add(k)
        moments = sorted(spaced, key=lambda m: m["broll_start_seconds"])

        # Step 2 — enforce minimum 6 s gap between consecutive B-rolls
        _MIN_GAP = 6.0
        filtered = []
        for m in moments:
            if not filtered:
                filtered.append(m)
                continue
            gap = m["broll_start_seconds"] - filtered[-1]["broll_end_seconds"]
            if gap >= _MIN_GAP:
                filtered.append(m)
            elif _CONF.get(m["confidence"], 1) < _CONF.get(filtered[-1]["confidence"], 1):
                filtered[-1] = m  # replace with higher-confidence neighbour
        moments = filtered
        print(f"[stock] spacing: {n} raw → {len(moments)} after even-spacing + {_MIN_GAP}s gap "
              f"(video_end={video_end:.0f}s, slot_w={slot_w:.0f}s)")

        # Gap analysis
        if len(moments) > 1:
            gaps = [round(moments[i]["broll_start_seconds"] - moments[i-1]["broll_end_seconds"], 1)
                    for i in range(1, len(moments))]
            max_gap = max(gaps)
            print(f"[stock] gap analysis: {gaps}s (max {max_gap:.1f}s)")

    # Min-floor check (warning only — Sonnet is responsible for meeting it)
    def _min_floor(dur):
        if dur <= 30:   return 1
        if dur <= 45:   return 2
        if dur <= 60:   return 3
        if dur <= 90:   return 4
        if dur <= 120:  return 5
        if dur <= 180:  return 6
        return max(6, int(dur / 25))
    floor = _min_floor(video_end) if video_end > 0 else 1
    print(f"[stock] min floor for {video_end:.0f}s video: {floor}, have {len(moments)}"
          + (" — OK" if len(moments) >= floor else " — WARNING: below minimum (spacing may have reduced count)"))

    # Max density cap: 1 moment per 8 s average — prevents over-dense B-roll on long dense videos
    if video_end > 0:
        max_cap = max(1, int(video_end / 8.0))
        if len(moments) > max_cap:
            # Prefer emphasis over coverage; within each tier prefer higher confidence
            _P = {"coverage": 1}   # 0 for concrete/emotional/hybrid
            _C = {"high": 0, "medium": 1, "low": 2}
            moments.sort(key=lambda m: (_P.get(m.get("moment_type", ""), 0),
                                        _C.get(m.get("confidence", "low"), 1)))
            moments = moments[:max_cap]
            moments.sort(key=lambda m: m["broll_start_seconds"])
            print(f"[stock] max density cap (1/8s): trimmed to {max_cap} moments "
                  f"for {video_end:.0f}s video")

    spacing_drops = n_before_spacing - len(moments)

    # Cost guard — cap Haiku fan-out to stay under $0.50 budget
    # Multi-frame mode: 40 candidates × batches of 3 × ~$0.003/batch ≈ $0.04/moment
    # Single-frame mode: 40 candidates × ~$0.002/call ≈ $0.08/moment (batched loosely)
    _HAIKU_COST_PER_MOMENT = 0.04 if video_context else 0.08
    # Sonnet: moment selection (~$0.04) + video context pass if used (~$0.04)
    _SONNET_COST_ESTIMATE  = 0.08 if video_context else 0.04
    _COST_BUDGET           = 1.00
    total_identified = len(moments)
    projected_cost   = _SONNET_COST_ESTIMATE + total_identified * _HAIKU_COST_PER_MOMENT
    cost_limit_hit   = False
    cost_cap_drops   = 0
    if projected_cost > _COST_BUDGET:
        max_m = int((_COST_BUDGET - _SONNET_COST_ESTIMATE) / _HAIKU_COST_PER_MOMENT)
        cost_cap_drops = max(0, len(moments) - max(max_m, 1))
        moments = moments[:max(max_m, 1)]
        cost_limit_hit = True
        print(f"[stock] cost cap hit: {total_identified} moments → {len(moments)} processed "
              f"(projected ${projected_cost:.3f} > ${_COST_BUDGET})")
    h   = sum(1 for m in moments if m.get("confidence") == "high")
    med = sum(1 for m in moments if m.get("confidence") == "medium")
    low = sum(1 for m in moments if m.get("confidence") == "low")
    est = _SONNET_COST_ESTIMATE + len(moments) * _HAIKU_COST_PER_MOMENT
    print(f"[stock] {len(moments)} moments ({h} high / {med} medium / {low} low), est cost ${est:.3f}")

    _MAX_CANDIDATES = 40

    import concurrent.futures as _cf
    _workers = min(5, len(moments))
    print(f"[stock] processing {len(moments)} moments in parallel (workers={_workers})")
    with _cf.ThreadPoolExecutor(max_workers=_workers) as _pool:
        futs = [
            _pool.submit(_process_moment, m, pexels_key, pixabay_key,
                         client, video_context, _MAX_CANDIDATES)
            for m in moments
        ]
        for fut in _cf.as_completed(futs):
            try:
                fut.result()
            except Exception as _exc:
                print(f"[stock] moment processing error: {_exc}")

    return {
        "moments":                 moments,
        "total_moments_identified": total_identified,
        "moments_processed":       len(moments),
        "cost_limit_hit":          cost_limit_hit,
        "video_context":           video_context,
        "filter_stats": {
            "sonnet_moments_raw": sonnet_moments_raw,
            "buf_drops":          buf_drops,
            "spacing_drops":      spacing_drops,
            "cost_cap_drops":     cost_cap_drops,
        },
    }


@app.function(
    image=image,
    timeout=120,
    secrets=[
        modal.Secret.from_name("anthropic-secret"),
        modal.Secret.from_name("pexels-secret"),
        modal.Secret.from_name("pixabay-secret"),
    ],
)
def search_stock_clips(search_query: str, page: int = 2, moment_context: str = "") -> list:
    """Fetch a fresh page of clips for 'Find different clips', with Haiku scoring when context is provided."""
    import os, json, requests as _req
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    pexels_key    = os.environ.get("PEXELS_API_KEY", "")
    pixabay_key   = os.environ.get("PIXABAY_API_KEY", "")

    http_session = _req.Session()
    pex = fetch_pexels(search_query, page, pexels_key, http_session)
    pix = fetch_pixabay(search_query, page, pixabay_key, http_session)
    clips = []
    for i in range(max(len(pex), len(pix))):
        if i < len(pex): clips.append(pex[i])
        if i < len(pix): clips.append(pix[i])
    clips = clips[:12]

    if moment_context:
        try:
            ctx = json.loads(moment_context)
        except Exception:
            ctx = {"label": moment_context}
        broll_dur   = float(ctx.get("broll_duration_seconds", 3.0))
        strict_eval = ctx.get("strict_eval_prompt") or search_query
        import anthropic as _anthropic
        ac = _anthropic.Anthropic(api_key=anthropic_key) if anthropic_key else None
        clips = [add_clip_window(c, broll_dur) for c in score_clips(clips, strict_eval, ac)]

    return clips


# ---------------------------------------------------------------------------
# Hook generator — generate 3 Hebrew hook options via Sonnet vision
# ---------------------------------------------------------------------------

@app.function(
    image=image,
    timeout=120,
    volumes={TMP_DIR: tmp_vol},
    secrets=[modal.Secret.from_name("anthropic-secret")],
)
def generate_hook_options(captions_json: str, video_key: str = "") -> dict:
    import os, json, base64 as _b64, anthropic as _anthropic
    from pathlib import Path

    client = _anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
    captions = json.loads(captions_json)
    transcript = " ".join(c.get("text", "") for c in captions).strip()
    if not transcript:
        return {"hooks": []}

    frames = []
    if video_key:
        tmp_vol.reload()
        vpath = Path(TMP_DIR) / video_key
        if vpath.exists():
            frames = sample_frames(str(vpath), n_frames=6, strategy="evenly_spaced")

    content = []
    for _t, jpeg in frames:
        b64 = _b64.b64encode(jpeg).decode()
        content.append({"type": "image",
                         "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}})

    content.append({"type": "text", "text": (
        "You are a social-media video editor writing Hebrew hooks — short, punchy opening captions "
        "that appear in the first seconds of a video to stop the scroll and compel viewers to watch.\n\n"
        f"TRANSCRIPT (Hebrew):\n{transcript}\n\n"
        "Generate exactly 3 distinct Hebrew hook options. Each hook:\n"
        "- Maximum 10 words\n"
        "- Must be provocative, intriguing, or emotionally striking — NOT a plain summary\n"
        "- Should create curiosity or tension that only resolves by watching the video\n"
        "- Must be grammatically correct Hebrew\n"
        "- No emojis, no hashtags\n\n"
        "Return JSON only — no markdown, no explanation:\n"
        "{\"hooks\": ["
        "{\"text\": \"...\", \"rationale\": \"one sentence in English explaining why this hook works\"},"
        "{\"text\": \"...\", \"rationale\": \"...\"},"
        "{\"text\": \"...\", \"rationale\": \"...\"}"
        "]}"
    )})

    resp = client.messages.create(
        model=SONNET_MODEL, max_tokens=512, temperature=0.8,
        messages=[{"role": "user", "content": content}],
    )
    raw = resp.content[0].text.strip()
    if "```" in raw:
        for part in raw.split("```"):
            part = part.strip().lstrip("json").strip()
            if part.startswith("{"):
                raw = part
                break
    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        return {"hooks": []}
    if "hooks" not in result or not isinstance(result["hooks"], list):
        return {"hooks": []}
    return result


# ---------------------------------------------------------------------------
# Hook burn — overlay hook text on processed video using ASS subtitle
# ---------------------------------------------------------------------------

@app.function(image=burn_image, timeout=180, volumes={TMP_DIR: tmp_vol})
def burn_hook_fn(
    video_key: str,
    hook_text: str,
    font: str = "Heebo",
    font_color: str = "#FFFFFF",
    bg_color: str = "#000000",
    bg_opacity: float = 0.6,
    start_seconds: float = 1.0,
    duration_seconds: float = 4.0,
    vertical_position: int = 10,
    border_color: str = "#000000",
    border_size: int = 0,
) -> dict:
    import json, subprocess, tempfile, shutil
    from pathlib import Path

    tmp_vol.reload()

    def run(cmd):
        r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=150)
        if r.returncode != 0:
            raise RuntimeError(f"ffmpeg {r.returncode}:\n{r.stderr.decode('utf-8', errors='replace')[-2000:]}")

    def probe_dims(path):
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height,side_data_list",
             "-show_entries", "stream_tags=rotate",
             "-of", "json", str(path)],
            capture_output=True, text=True, check=True,
        )
        s = json.loads(r.stdout)["streams"][0]
        w, h = int(s["width"]), int(s["height"])
        rot = 0
        tag_rot = s.get("tags", {}).get("rotate")
        if tag_rot:
            rot = int(tag_rot)
        else:
            for sd in s.get("side_data_list", []):
                if "rotation" in sd:
                    rot = (-int(sd["rotation"])) % 360
                    break
        if rot in (90, 270):
            w, h = h, w
        return w, h, rot

    def seconds_to_ass(t):
        h = int(t // 3600); m = int((t % 3600) // 60); s = t % 60
        return f"{h}:{m:02d}:{s:05.2f}"

    def hex_to_ass(hex_color: str, alpha_byte: int = 0) -> str:
        """Convert #RRGGBB to ASS &HAABBGGRR&. alpha_byte: 0=opaque, 255=transparent."""
        h = hex_color.lstrip("#")
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return f"&H{alpha_byte:02X}{b:02X}{g:02X}{r:02X}&"

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path  = Path(tmp)
        video_in  = tmp_path / "input.mp4"
        video_out = tmp_path / "output.mp4"
        ass_path  = tmp_path / "hook.ass"
        shutil.copy(Path(TMP_DIR) / video_key, video_in)

        width, height, _rotation = probe_dims(video_in)
        font_size = max(36, min(96, int(height * 0.075)))

        primary_col = hex_to_ass(font_color, alpha_byte=0)
        bg_alpha    = int((1.0 - max(0.0, min(1.0, bg_opacity))) * 255)
        back_col    = hex_to_ass(bg_color, alpha_byte=bg_alpha)

        # Vertical center of text; vertical_position: 0=top, 100=bottom
        pad = font_size
        y   = int(pad + (height - 2 * pad) * (max(0, min(100, vertical_position)) / 100))

        safe_text = hook_text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")
        override  = "{\\an5\\pos(" + str(width // 2) + "," + str(y)
        border_size = max(0, min(20, int(border_size)))
        if border_size > 0:
            bc = border_color.lstrip("#")
            if len(bc) == 3: bc = "".join(c * 2 for c in bc)
            br, bg_, bb = int(bc[0:2], 16), int(bc[2:4], 16), int(bc[4:6], 16)
            override += "\\bord" + str(border_size) + "\\3c&H" + f"{bb:02X}{bg_:02X}{br:02X}" + "&"
        override += "}"

        ass_content = (
            "[Script Info]\nScriptType: v4.00+\n"
            f"PlayResX: {width}\nPlayResY: {height}\n"
            "WrapStyle: 1\nScaledBorderAndShadow: yes\n\n"
            "[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
            "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
            "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
            "Alignment, MarginL, MarginR, MarginV, Encoding\n"
            f"Style: Hook,{font},{font_size},"
            f"{primary_col},&H000000FF,&H00000000,{back_col},"
            "-1,0,0,0,100,100,2,0,3,10,0,5,"
            "20,20,20,1\n\n"
            "[Events]\n"
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
            f"Dialogue: 0,{seconds_to_ass(start_seconds)},{seconds_to_ass(start_seconds + duration_seconds)},"
            f"Hook,,0,0,0,,{override}{safe_text}\n"
        )
        ass_path.write_text(ass_content, encoding="utf-8")

        run([
            "ffmpeg", "-y", "-i", str(video_in),
            "-vf", f"ass={ass_path}",
            "-c:v", "libx264", "-crf", "18", "-preset", "veryfast",
            "-c:a", "copy", str(video_out),
        ])

        out_key = f"hook_{video_key}"
        shutil.copy(video_out, Path(TMP_DIR) / out_key)
        tmp_vol.commit()

    return {"download_key": out_key}


# ---------------------------------------------------------------------------
# HTTP API — raw ASGI, zero external dependencies, dispatches to process_video
#
# POST /process?filename=x&cut_silences=true&burn_captions=true&min_silence=0.5&padding=0.2
#   Body: raw video bytes
#   Returns: processed video bytes (video/mp4)
# ---------------------------------------------------------------------------
@app.function(image=image, timeout=900, volumes={TMP_DIR: tmp_vol})
@modal.concurrent(max_inputs=20)
@modal.asgi_app()
def api():
    import asyncio
    import json
    from urllib.parse import parse_qs

    CORS = [
        (b"access-control-allow-origin",  b"*"),
        (b"access-control-allow-methods", b"GET, POST, OPTIONS"),
        (b"access-control-allow-headers", b"*"),
        (b"access-control-expose-headers", b"content-disposition"),
    ]

    async def _read_body(receive):
        body = b""
        while True:
            msg = await receive()
            body += msg.get("body", b"")
            if not msg.get("more_body", False):
                return body

    async def app(scope, receive, send):
        if scope["type"] != "http":
            return

        async def send_error(msg: str, status: int = 500):
            body = json.dumps({"error": msg}).encode()
            await send({"type": "http.response.start", "status": status,
                        "headers": CORS + [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": body})

        method = scope["method"]
        path   = scope["path"]

        # OPTIONS preflight
        if method == "OPTIONS":
            await send({"type": "http.response.start", "status": 204, "headers": CORS})
            await send({"type": "http.response.body",  "body": b""})
            return

        # Health check
        if path == "/" and method == "GET":
            body = json.dumps({"status": "ok"}).encode()
            await send({"type": "http.response.start", "status": 200,
                        "headers": CORS + [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": body})
            return

        # GPU warmup — fire-and-forget, wakes the GPU container so the next
        # real request doesn't hit a cold start
        if path in ("/warmup", "/warmup/") and method == "GET":
            try:
                process_video.spawn(
                    video_bytes=b"",
                    filename="__warmup__",
                    cut_silences=False,
                    burn_captions=False,
                    min_silence=0.5,
                    padding=0.2,
                )
            except Exception:
                pass
            body = json.dumps({"status": "warming up"}).encode()
            await send({"type": "http.response.start", "status": 200,
                        "headers": CORS + [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": body})
            return

        # Upload chunk — store one piece of a chunked video upload in Volume
        if path in ("/upload_chunk", "/upload_chunk/") and method == "POST":
            qs  = parse_qs(scope.get("query_string", b"").decode())
            key     = qs.get("key",   [""])[0]
            idx_raw = qs.get("index", ["0"])[0]
            if not key or not _SAFE_KEY_RE.match(key):
                await send_error("Invalid or missing key", 400)
                return
            try:
                idx = int(idx_raw)
                if not (0 <= idx <= 9999):
                    raise ValueError
            except (ValueError, TypeError):
                await send_error("Invalid index", 400)
                return
            chunk_bytes = await _read_body(receive)
            from pathlib import Path as _Path
            chunk_path = _Path(TMP_DIR) / f"{key}_chunk_{idx:04d}"
            # asyncio.to_thread so the blocking FUSE write doesn't freeze the event loop
            # (which would serialize all concurrent chunk requests despite max_inputs=20)
            await asyncio.to_thread(chunk_path.write_bytes, chunk_bytes)
            # No commit here — all chunks are committed once in /process before job spawn
            body = json.dumps({"ok": True}).encode()
            await send({"type": "http.response.start", "status": 200,
                        "headers": CORS + [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": body})
            return

        # Process endpoint — spawn job, return call_id immediately
        if path in ("/process", "/process/") and method == "POST":
            qs = parse_qs(scope.get("query_string", b"").decode())
            upload_key    = qs.get("key",           [""])[0]
            filename      = qs.get("filename",      ["video.mp4"])[0]
            cut_silences  = qs.get("cut_silences",  ["true"])[0].lower() == "true"
            burn_captions        = qs.get("burn_captions",        ["true"])[0].lower() == "true"
            enhance_audio        = qs.get("enhance_audio",        ["true"])[0].lower() == "true"
            transcribe_for_broll = qs.get("transcribe_for_broll", ["false"])[0].lower() == "true"
            min_silence          = float(qs.get("min_silence", ["0.5"])[0])
            padding              = float(qs.get("padding",     ["0.2"])[0])

            try:
                if upload_key:
                    # Flush all chunks to persistent storage before spawning the worker
                    tmp_vol.commit()
                    call = process_video.spawn(
                        upload_key=upload_key,
                        filename=filename,
                        cut_silences=cut_silences,
                        burn_captions=burn_captions,
                        min_silence=min_silence,
                        padding=padding,
                        enhance_audio=enhance_audio,
                        transcribe_for_broll=transcribe_for_broll,
                    )
                else:
                    # Legacy path — full body in request (may hit Modal 303 on slow connections)
                    video_bytes = await _read_body(receive)
                    call = process_video.spawn(
                        video_bytes=video_bytes,
                        filename=filename,
                        cut_silences=cut_silences,
                        burn_captions=burn_captions,
                        min_silence=min_silence,
                        padding=padding,
                        enhance_audio=enhance_audio,
                        transcribe_for_broll=transcribe_for_broll,
                    )
                body = json.dumps({"call_id": call.object_id}).encode()
                await send({"type": "http.response.start", "status": 202,
                            "headers": CORS + [(b"content-type", b"application/json")]})
                await send({"type": "http.response.body", "body": body})
            except Exception as e:
                await send_error(str(e))
            return

        # Poll process result — 200+binary when done, 202+JSON while running
        if path.startswith("/process_poll/") and method == "GET":
            call_id  = path[len("/process_poll/"):].rstrip("/")
            filename = parse_qs(scope.get("query_string", b"").decode()).get("filename", ["video.mp4"])[0]
            try:
                import modal as _modal
                fn_call = _modal.functions.FunctionCall.from_id(call_id)
                result, still_running = _poll_fn_call(fn_call)
                if still_running:
                    body = json.dumps({"status": "running"}).encode()
                    await send({"type": "http.response.start", "status": 202,
                                "headers": CORS + [(b"content-type", b"application/json")]})
                    await send({"type": "http.response.body", "body": body})
                    return
                body = json.dumps(result).encode()
                await send({"type": "http.response.start", "status": 200,
                            "headers": CORS + [(b"content-type", b"application/json")]})
                await send({"type": "http.response.body", "body": body})
            except Exception as e:
                await send_error(str(e))
            return

        # Burn endpoint — spawn job, return call_id immediately
        # Body: captions JSON string; video_key passed as query param
        if path in ("/burn", "/burn/") and method == "POST":
            qs = parse_qs(scope.get("query_string", b"").decode())
            filename     = qs.get("filename",  ["video.mp4"])[0]
            font         = qs.get("font",      ["Heebo"])[0]
            margin_v_pct = float(qs.get("margin_v", ["0.08"])[0])
            font_size    = int(qs.get("font_size",  ["48"])[0])
            video_key    = qs.get("video_key", [""])[0]

            body = await _read_body(receive)
            raw = body.decode("utf-8")
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    captions_json = raw
                    broll_json    = "[]"
                    hook_json     = ""
                else:
                    captions_json = json.dumps(parsed.get("captions", []))
                    broll_json    = json.dumps(parsed.get("broll", []))
                    hook_json     = json.dumps(parsed.get("hook", {})) if parsed.get("hook") else ""
            except Exception:
                captions_json = raw
                broll_json    = "[]"
                hook_json     = ""

            try:
                call = burn_captions_fn.spawn(video_key, captions_json, font, margin_v_pct, broll_json, font_size, hook_json)
                resp = json.dumps({"call_id": call.object_id}).encode()
                await send({"type": "http.response.start", "status": 202,
                            "headers": CORS + [(b"content-type", b"application/json")]})
                await send({"type": "http.response.body", "body": resp})
            except Exception as e:
                await send_error(str(e))
            return

        # Poll burn result — 200+video when done, 202+JSON while running
        if path.startswith("/burn_poll/") and method == "GET":
            call_id  = path[len("/burn_poll/"):].rstrip("/")
            try:
                import modal as _modal
                fn_call = _modal.functions.FunctionCall.from_id(call_id)
                result, still_running = _poll_fn_call(fn_call)
                if still_running:
                    body = json.dumps({"status": "running"}).encode()
                    await send({"type": "http.response.start", "status": 202,
                                "headers": CORS + [(b"content-type", b"application/json")]})
                    await send({"type": "http.response.body", "body": body})
                    return
                body = json.dumps(result).encode()
                await send({"type": "http.response.start", "status": 200,
                            "headers": CORS + [(b"content-type", b"application/json")]})
                await send({"type": "http.response.body", "body": body})
            except Exception as e:
                await send_error(str(e))
            return

        # Download a file from the pipeline volume by key
        if path.startswith("/download/") and method == "GET":
            from pathlib import Path as _Path
            import time as _time_mod
            key = path[len("/download/"):].rstrip("/")
            if not key or not _SAFE_DOWNLOAD_KEY_RE.match(key):
                await send_error("Invalid key", 400)
                return
            qs  = parse_qs(scope.get("query_string", b"").decode())
            filename = qs.get("filename", [key])[0]
            response_started = False
            try:
                import asyncio as _asyncio
                _base = str(_Path(TMP_DIR).resolve())
                file_path = _Path(TMP_DIR) / key
                if not str(file_path.resolve()).startswith(_base + "/") and \
                        str(file_path.resolve()) != _base:
                    raise ValueError("Forbidden path")

                # Reload is inside the loop so "open files" transient errors
                # (volume still being committed by the GPU container) are retried.
                for _attempt in range(10):
                    try:
                        tmp_vol.reload()
                        if file_path.exists():
                            break
                        print(f"[download] attempt {_attempt}: {key!r} not found")
                    except RuntimeError as _ve:
                        if "open files" in str(_ve):
                            print(f"[download] attempt {_attempt}: volume busy — {_ve}")
                        else:
                            raise
                    if _attempt < 9:
                        await _asyncio.sleep(1)
                else:
                    _vol_files = [p.name for p in _Path(TMP_DIR).iterdir()] if _Path(TMP_DIR).exists() else []
                    print(f"[download] FAIL key={key!r} vol_files={_vol_files}")
                    raise FileNotFoundError(f"File not found in volume after retries: {key}")

                file_size = file_path.stat().st_size

                # Parse Range header so the browser can seek in the preview player
                req_hdrs = {bytes(k).lower(): bytes(v) for k, v in scope.get("headers", [])}
                range_hdr = req_hdrs.get(b"range", b"").decode()
                start, end, status = 0, file_size - 1, 200
                if range_hdr.startswith("bytes="):
                    rng = range_hdr[6:]
                    if rng.startswith("-"):
                        start = max(0, file_size - int(rng[1:]))
                    else:
                        parts = rng.split("-", 1)
                        start = int(parts[0])
                        end   = int(parts[1]) if parts[1] else file_size - 1
                    end    = min(end, file_size - 1)
                    status = 206

                content_length = end - start + 1
                resp_headers = CORS + [
                    (b"content-type",        b"video/mp4"),
                    (b"accept-ranges",       b"bytes"),
                    (b"content-length",      str(content_length).encode()),
                    (b"content-disposition", f'attachment; filename="{filename}"'.encode()),
                ]
                if status == 206:
                    resp_headers.append(
                        (b"content-range", f"bytes {start}-{end}/{file_size}".encode())
                    )

                response_started = True
                await send({"type": "http.response.start", "status": status,
                            "headers": resp_headers})
                with open(file_path, "rb") as f:
                    f.seek(start)
                    remaining = content_length
                    CHUNK = 256 * 1024  # 256 KB — small enough to start playback fast
                    while remaining > 0:
                        chunk = f.read(min(CHUNK, remaining))
                        if not chunk:
                            break
                        remaining -= len(chunk)
                        await send({"type": "http.response.body",
                                    "body": chunk, "more_body": remaining > 0})
                if key.endswith("_out.mp4"):
                    file_path.unlink(missing_ok=True)
                    tmp_vol.commit()
            except Exception as e:
                print(f"[download] ERROR key={key!r} response_started={response_started} err={e!r}")
                if not response_started:
                    await send_error(str(e))
                # If response already started, the connection will close ungracefully —
                # nothing useful we can send at this point.
            return

        # Thumbnail — extract a single JPEG frame at 1s for preview use
        if path.startswith("/thumbnail/") and method == "GET":
            from pathlib import Path as _Path
            import time as _time_mod
            key = path[len("/thumbnail/"):].rstrip("/")
            if not key or not _SAFE_DOWNLOAD_KEY_RE.match(key):
                await send_error("Invalid key", 400)
                return
            try:
                import subprocess as _sp, asyncio as _asyncio
                file_path = _Path(TMP_DIR) / key
                for _attempt in range(10):
                    try:
                        tmp_vol.reload()
                        if file_path.exists():
                            break
                        print(f"[thumbnail] attempt {_attempt}: {key!r} not found")
                    except RuntimeError as _ve:
                        if "open files" in str(_ve):
                            print(f"[thumbnail] attempt {_attempt}: volume busy — {_ve}")
                        else:
                            raise
                    if _attempt < 9:
                        await _asyncio.sleep(1)
                else:
                    _vol_files = [p.name for p in _Path(TMP_DIR).iterdir()] if _Path(TMP_DIR).exists() else []
                    print(f"[thumbnail] FAIL key={key!r} vol_files={_vol_files}")
                    await send_error("Not found", 404)
                    return
                def _run_thumb(ss):
                    # -f mjpeg: correct muxer for JPEG to pipe (image2 is file-only)
                    return _sp.run(
                        ["ffmpeg", "-ss", str(ss), "-i", str(file_path),
                         "-frames:v", "1", "-vf", "scale=400:-2",
                         "-f", "mjpeg", "pipe:1", "-loglevel", "error"],
                        capture_output=True, timeout=15,
                    )
                r = _run_thumb(1)
                if r.returncode != 0 or not r.stdout:
                    print(f"[thumbnail] ss=1 failed (rc={r.returncode}): {r.stderr.decode(errors='replace')}")
                    r = _run_thumb(0)
                if r.returncode != 0 or not r.stdout:
                    print(f"[thumbnail] ss=0 failed (rc={r.returncode}): {r.stderr.decode(errors='replace')}")
                    await send_error("Thumbnail extraction failed", 500)
                    return
                await send({"type": "http.response.start", "status": 200,
                            "headers": CORS + [(b"content-type", b"image/jpeg"),
                                               (b"cache-control", b"max-age=300")]})
                await send({"type": "http.response.body", "body": r.stdout})
            except Exception as e:
                print(f"[thumbnail] ERROR key={key!r} err={e!r}")
                await send_error(str(e))
            return

        # B-roll analysis endpoint
        if path in ("/broll", "/broll/") and method == "POST":
            body = await _read_body(receive)
            data = json.loads(body.decode("utf-8"))
            try:
                call = analyze_broll.spawn(
                    data.get("video_key", ""),
                    json.dumps(data.get("captions", [])),
                    data.get("gemini_key", ""),
                    data.get("aspect_ratio", "16:9"),
                    data.get("anthropic_key", ""),
                )
                resp = json.dumps({"call_id": call.object_id}).encode()
                await send({"type": "http.response.start", "status": 202,
                            "headers": CORS + [(b"content-type", b"application/json")]})
                await send({"type": "http.response.body", "body": resp})
            except Exception as e:
                await send_error(str(e))
            return

        if path.startswith("/broll_poll/") and method == "GET":
            call_id = path[len("/broll_poll/"):].rstrip("/")
            try:
                import modal as _modal
                fn_call = _modal.functions.FunctionCall.from_id(call_id)
                result, still_running = _poll_fn_call(fn_call)
                if still_running:
                    body = json.dumps({"status": "running"}).encode()
                    await send({"type": "http.response.start", "status": 202,
                                "headers": CORS + [(b"content-type", b"application/json")]})
                    await send({"type": "http.response.body", "body": body})
                    return
                body = json.dumps({"suggestions": result}).encode()
                await send({"type": "http.response.start", "status": 200,
                            "headers": CORS + [(b"content-type", b"application/json")]})
                await send({"type": "http.response.body", "body": body})
            except Exception as e:
                await send_error(str(e))
            return

        # Single B-roll video generation (retry for one card)
        if path in ("/broll_image", "/broll_image/") and method == "POST":
            body = await _read_body(receive)
            data = json.loads(body.decode("utf-8"))
            try:
                call = generate_broll_video.spawn(
                    data.get("description", ""),
                    data.get("aspect_ratio", "9:16"),
                    data.get("gemini_key", ""),
                )
                resp = json.dumps({"call_id": call.object_id}).encode()
                await send({"type": "http.response.start", "status": 202,
                            "headers": CORS + [(b"content-type", b"application/json")]})
                await send({"type": "http.response.body", "body": resp})
            except Exception as e:
                await send_error(str(e))
            return

        if path.startswith("/broll_image_poll/") and method == "GET":
            call_id = path[len("/broll_image_poll/"):].rstrip("/")
            try:
                import modal as _modal
                fn_call = _modal.functions.FunctionCall.from_id(call_id)
                result, still_running = _poll_fn_call(fn_call)
                if still_running:
                    body = json.dumps({"status": "running"}).encode()
                    await send({"type": "http.response.start", "status": 202,
                                "headers": CORS + [(b"content-type", b"application/json")]})
                    await send({"type": "http.response.body", "body": body})
                    return
                body = json.dumps(result).encode()
                await send({"type": "http.response.start", "status": 200,
                            "headers": CORS + [(b"content-type", b"application/json")]})
                await send({"type": "http.response.body", "body": body})
            except Exception as e:
                await send_error(str(e))
            return

        if path.startswith("/cancel/") and method in ("GET", "POST", "DELETE"):
            call_id = path[len("/cancel/"):].rstrip("/")
            try:
                import modal as _modal
                _modal.functions.FunctionCall.from_id(call_id).cancel()
                body = json.dumps({"status": "cancelled"}).encode()
                await send({"type": "http.response.start", "status": 200,
                            "headers": CORS + [(b"content-type", b"application/json")]})
                await send({"type": "http.response.body", "body": body})
            except Exception as e:
                await send_error(str(e))
            return

        # Stock B-roll analysis — spawn Claude + Pexels/Pixabay search
        if path in ("/stock-broll", "/stock-broll/") and method == "POST":
            if not _check_rate_limit(_get_client_ip(scope)):
                await send_error("Rate limit exceeded. Try again in a minute.", 429)
                return
            body = await _read_body(receive)
            data = json.loads(body.decode("utf-8"))
            try:
                call = analyze_stock_broll.spawn(
                    data.get("captions_json", "[]"),
                    data.get("video_key", ""),
                )
                resp = json.dumps({"call_id": call.object_id}).encode()
                await send({"type": "http.response.start", "status": 202,
                            "headers": CORS + [(b"content-type", b"application/json")]})
                await send({"type": "http.response.body", "body": resp})
            except Exception as e:
                await send_error(str(e))
            return

        if path.startswith("/stock-broll-poll/") and method == "GET":
            call_id = path[len("/stock-broll-poll/"):].rstrip("/")
            try:
                import modal as _modal
                fn_call = _modal.functions.FunctionCall.from_id(call_id)
                result, still_running = _poll_fn_call(fn_call)
                if still_running:
                    body = json.dumps({"status": "running"}).encode()
                    await send({"type": "http.response.start", "status": 202,
                                "headers": CORS + [(b"content-type", b"application/json")]})
                    await send({"type": "http.response.body", "body": body})
                    return
                body = json.dumps(result).encode()
                await send({"type": "http.response.start", "status": 200,
                            "headers": CORS + [(b"content-type", b"application/json")]})
                await send({"type": "http.response.body", "body": body})
            except Exception as e:
                await send_error(str(e))
            return

        # Stock clip search — "Find different clips" per moment
        if path in ("/stock-broll-clips", "/stock-broll-clips/") and method == "POST":
            body = await _read_body(receive)
            data = json.loads(body.decode("utf-8"))
            try:
                call = search_stock_clips.spawn(
                    data.get("search_query", ""),
                    int(data.get("page", 2)),
                    data.get("moment_context", ""),
                )
                resp = json.dumps({"call_id": call.object_id}).encode()
                await send({"type": "http.response.start", "status": 202,
                            "headers": CORS + [(b"content-type", b"application/json")]})
                await send({"type": "http.response.body", "body": resp})
            except Exception as e:
                await send_error(str(e))
            return

        if path.startswith("/stock-broll-clips-poll/") and method == "GET":
            call_id = path[len("/stock-broll-clips-poll/"):].rstrip("/")
            try:
                import modal as _modal
                fn_call = _modal.functions.FunctionCall.from_id(call_id)
                result, still_running = _poll_fn_call(fn_call)
                if still_running:
                    body = json.dumps({"status": "running"}).encode()
                    await send({"type": "http.response.start", "status": 202,
                                "headers": CORS + [(b"content-type", b"application/json")]})
                    await send({"type": "http.response.body", "body": body})
                    return
                body = json.dumps({"clips": result}).encode()
                await send({"type": "http.response.start", "status": 200,
                            "headers": CORS + [(b"content-type", b"application/json")]})
                await send({"type": "http.response.body", "body": body})
            except Exception as e:
                await send_error(str(e))
            return

        # Hook generation
        if path in ("/generate-hook", "/generate-hook/") and method == "POST":
            if not _check_rate_limit(_get_client_ip(scope)):
                await send_error("Rate limit exceeded. Try again in a minute.", 429)
                return
            body = await _read_body(receive)
            data = json.loads(body.decode("utf-8"))
            try:
                call = generate_hook_options.spawn(
                    data.get("captions_json", "[]"),
                    data.get("video_key", ""),
                )
                resp = json.dumps({"call_id": call.object_id}).encode()
                await send({"type": "http.response.start", "status": 202,
                            "headers": CORS + [(b"content-type", b"application/json")]})
                await send({"type": "http.response.body", "body": resp})
            except Exception as e:
                await send_error(str(e))
            return

        if path.startswith("/generate-hook-poll/") and method == "GET":
            call_id = path[len("/generate-hook-poll/"):].rstrip("/")
            try:
                import modal as _modal
                fn_call = _modal.functions.FunctionCall.from_id(call_id)
                result, still_running = _poll_fn_call(fn_call)
                if still_running:
                    body = json.dumps({"status": "running"}).encode()
                    await send({"type": "http.response.start", "status": 202,
                                "headers": CORS + [(b"content-type", b"application/json")]})
                    await send({"type": "http.response.body", "body": body})
                    return
                body = json.dumps(result).encode()
                await send({"type": "http.response.start", "status": 200,
                            "headers": CORS + [(b"content-type", b"application/json")]})
                await send({"type": "http.response.body", "body": body})
            except Exception as e:
                await send_error(str(e))
            return

        # Hook burn
        if path in ("/burn-hook", "/burn-hook/") and method == "POST":
            body = await _read_body(receive)
            data = json.loads(body.decode("utf-8"))
            try:
                call = burn_hook_fn.spawn(
                    data.get("video_key", ""),
                    data.get("hook_text", ""),
                    data.get("font", "Heebo"),
                    data.get("font_color", "#FFFFFF"),
                    data.get("bg_color", "#000000"),
                    float(data.get("bg_opacity", 0.6)),
                    float(data.get("start_seconds", 1.0)),
                    float(data.get("duration_seconds", 4.0)),
                    int(data.get("vertical_position", 10)),
                    data.get("border_color", "#000000"),
                    int(data.get("border_size", 0)),
                )
                resp = json.dumps({"call_id": call.object_id}).encode()
                await send({"type": "http.response.start", "status": 202,
                            "headers": CORS + [(b"content-type", b"application/json")]})
                await send({"type": "http.response.body", "body": resp})
            except Exception as e:
                await send_error(str(e))
            return

        if path.startswith("/burn-hook-poll/") and method == "GET":
            call_id = path[len("/burn-hook-poll/"):].rstrip("/")
            try:
                import modal as _modal
                fn_call = _modal.functions.FunctionCall.from_id(call_id)
                result, still_running = _poll_fn_call(fn_call)
                if still_running:
                    body = json.dumps({"status": "running"}).encode()
                    await send({"type": "http.response.start", "status": 202,
                                "headers": CORS + [(b"content-type", b"application/json")]})
                    await send({"type": "http.response.body", "body": body})
                    return
                body = json.dumps(result).encode()
                await send({"type": "http.response.start", "status": 200,
                            "headers": CORS + [(b"content-type", b"application/json")]})
                await send({"type": "http.response.body", "body": body})
            except Exception as e:
                await send_error(str(e))
            return

        await send({"type": "http.response.start", "status": 404, "headers": CORS})
        await send({"type": "http.response.body", "body": b"Not found"})

    return app
