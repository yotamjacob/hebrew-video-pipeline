"""Story Assembler (Phase 1) - the backend behind the standalone /assembler page.

Turns ONE long raw video (interview, business review, walkaround) into a
story-driven highlight cut: transcribe -> Sonnet picks the golden moments ->
the user curates a storyboard -> render trims + concats the kept moments.

DELIBERATELY SEPARATE from process_video (user directive 2026-08-13: do not
touch the main pipeline). It shares infrastructure by IMPORT ONLY:
- uploads arrive through the existing /upload_chunk route (same chunk layout),
- transcription mirrors process_video's settings at SEGMENT level (no word
  timestamps - storyboard moments snap to Whisper segment boundaries),
- the rendered output is written with the standard `_out.mp4` key + a
  jobs_store record via _record_job, so History/download/share/retention all
  work on it with zero changes to those systems.

Hidden beta: no credits are charged (the page is unlinked). Before making the
page public, add pricing at the /assembler/render spawn like /process does.
"""

import json
import subprocess
import tempfile
from pathlib import Path

import modal

from pipeline_core import (
    app, image, burn_image, model_volume, MODEL_DIR,
    WHISPER_MODEL, WHISPER_INITIAL_PROMPT, tmp_vol, TMP_DIR,
    SONNET_MODEL, costs_store, _record_ai_spend,
)

# Mirrors process_video's noise gates (kept local - importing them would mean
# editing pipeline_fns' import surface, and these are stable tuned constants).
_NO_SPEECH_MAX = 0.6
_AVG_LOGPROB_MIN = -1.0

# Storyboard shape guardrails: what the Sonnet pass may return.
MAX_MOMENTS = 8
MIN_MOMENTS = 3
TARGET_TOTAL_SECONDS = "45-90"


def _run(cmd, **kw):
    return subprocess.run(cmd, check=True, capture_output=True, **kw)


def _resolve_source(upload_key: str, workdir: Path) -> Path:
    """Assemble chunk files into the cached `{key}_src.mp4` (same layout and
    cache convention as process_video) or reuse an existing cache."""
    cache = Path(TMP_DIR) / f"{upload_key}_src.mp4"
    if cache.exists():
        return cache
    chunk_paths = sorted(Path(TMP_DIR).glob(f"{upload_key}_chunk_*"))
    if not chunk_paths:
        raise ValueError(f"No upload found for key {upload_key}")
    with open(cache, "wb") as out:
        for p in chunk_paths:
            with open(p, "rb") as inp:
                while True:
                    buf = inp.read(1 << 20)
                    if not buf:
                        break
                    out.write(buf)
    for p in chunk_paths:
        try:
            p.unlink()
        except Exception:
            pass
    tmp_vol.commit()
    return cache


def _probe_duration(path: Path) -> float:
    r = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
              "-of", "csv=p=0", str(path)], text=True)
    return float(r.stdout.strip() or 0)


def _pick_moments(segs, duration):
    """Sonnet reads the timestamped transcript and returns the golden moments
    as SEGMENT RANGES (never free timestamps - snapping to Whisper segment
    boundaries keeps every cut on a speech edge)."""
    import os
    from anthropic import Anthropic

    lines = "\n".join(
        f"[{i}] {s['start']:.1f}-{s['end']:.1f}: {s['text']}" for i, s in enumerate(segs))
    prompt = f"""לפניך תמלול מתוזמן של סרטון גלם באורך {duration:.0f} שניות (ראיון / סקירת עסק / אינטראקציה). המטרה: לבחור את הרגעים הכי חזקים לסרטון אינסטגרם קצר שמספר סיפור.

כל שורה היא מקטע: [מספר] התחלה-סוף: טקסט.

בחר {MIN_MOMENTS}-{MAX_MOMENTS} רגעים, בסך הכל {TARGET_TOTAL_SECONDS} שניות. כל רגע הוא טווח מקטעים רצוף (from_seg עד to_seg, כולל). סדר אותם כסיפור: פתיח שתופס (hook), גוף שמספר את הסיפור (story), רגע רגשי או ציטוט חזק (gold), וסגירה (closing).

החזר JSON בלבד:
{{"title": "כותרת קצרה לסרטון", "moments": [{{"from_seg": 0, "to_seg": 2, "role": "hook", "quote": "הציטוט המרכזי מהרגע", "reason": "למה הרגע הזה חזק לסיפור"}}]}}

חוקים: role אחד מ-hook/story/gold/closing. הציטוט חייב להופיע בתמלול. אל תמציא טקסט.

התמלול:
{lines}"""

    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    resp = client.messages.create(
        model=SONNET_MODEL, max_tokens=2000,
        messages=[{"role": "user", "content": prompt}])
    _record_ai_spend(costs_store, "assembler_moments", SONNET_MODEL, resp.usage)
    text = resp.content[0].text
    start, end = text.find("{"), text.rfind("}")
    data = json.loads(text[start:end + 1])

    moments = []
    for m in (data.get("moments") or [])[:MAX_MOMENTS]:
        try:
            a, b = int(m["from_seg"]), int(m["to_seg"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (0 <= a <= b < len(segs)):
            continue
        moments.append({
            "start": round(segs[a]["start"], 2),
            "end": round(segs[b]["end"], 2),
            "role": m.get("role") if m.get("role") in ("hook", "story", "gold", "closing") else "story",
            "quote": str(m.get("quote") or segs[a]["text"])[:300],
            "reason": str(m.get("reason") or "")[:300],
        })
    return str(data.get("title") or "")[:80], moments


def _thumb_b64(src: Path, at: float, workdir: Path, idx: int) -> str:
    """Small storyboard thumbnail as a data-URI-ready base64 jpeg."""
    import base64
    out = workdir / f"thumb_{idx}.jpg"
    try:
        _run(["ffmpeg", "-y", "-ss", f"{max(0.0, at):.2f}", "-i", str(src),
              "-frames:v", "1", "-vf", "scale=240:-2", "-q:v", "6", str(out)])
        return base64.b64encode(out.read_bytes()).decode()
    except Exception:
        return ""


@app.function(
    gpu="L4",
    cpu=4,
    timeout=900,
    # Hidden beta: keep the assembler's GPU ceiling tiny and independent of
    # the main pipeline's cap so it can never crowd out paying /process jobs.
    max_containers=2,
    volumes={MODEL_DIR: model_volume, TMP_DIR: tmp_vol},
    memory=4096,
    secrets=[modal.Secret.from_name("anthropic-secret")],
)
def analyze_story(upload_key: str, filename: str = "video.mp4") -> dict:
    """Transcribe (segment-level) + pick golden moments + thumbnails."""
    import os

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        src = _resolve_source(upload_key, tmp)
        duration = _probe_duration(src)
        if duration < 20:
            return {"error": "too_short", "duration": duration}

        wav = tmp / "audio.wav"
        _run(["ffmpeg", "-y", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000", str(wav)])

        os.environ["HF_HOME"] = MODEL_DIR
        from faster_whisper import WhisperModel
        # Same model + gates as the main pipeline, but SEGMENT level only -
        # the storyboard snaps cuts to segment boundaries, so word timing is
        # wasted GPU time here.
        common = dict(
            language="he", word_timestamps=False,
            vad_filter=True,
            vad_parameters={"threshold": 0.5, "min_silence_duration_ms": 500,
                            "speech_pad_ms": 400},
            beam_size=5, condition_on_previous_text=False,
            no_speech_threshold=_NO_SPEECH_MAX,
            log_prob_threshold=_AVG_LOGPROB_MIN,
            compression_ratio_threshold=2.4,
            initial_prompt=WHISPER_INITIAL_PROMPT,
        )
        try:
            m = WhisperModel(WHISPER_MODEL, device="cuda", compute_type="float16",
                             download_root=MODEL_DIR)
            raw, _info = m.transcribe(str(wav), **common)
        except Exception:
            m = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8",
                             download_root=MODEL_DIR)
            raw, _info = m.transcribe(str(wav), **common)

        segs = []
        for s in raw:
            nsp = float(getattr(s, "no_speech_prob", 0.0) or 0.0)
            alp = float(getattr(s, "avg_logprob", 0.0) or 0.0)
            if nsp >= _NO_SPEECH_MAX and alp <= _AVG_LOGPROB_MIN:
                continue
            text = (s.text or "").strip()
            if text:
                segs.append({"start": float(s.start), "end": float(s.end), "text": text})
        model_volume.commit()
        if len(segs) < MIN_MOMENTS:
            return {"error": "no_speech", "duration": duration}

        title, moments = _pick_moments(segs, duration)
        if not moments:
            return {"error": "no_moments", "duration": duration}
        for i, mo in enumerate(moments):
            mo["thumb"] = _thumb_b64(src, mo["start"] + 0.5, tmp, i)

        return {"duration": round(duration, 1), "title": title,
                "moments": moments,
                "segments": [{"start": round(s["start"], 2), "end": round(s["end"], 2),
                              "text": s["text"]} for s in segs]}


@app.function(
    image=burn_image,
    cpu=8,
    timeout=900,
    max_containers=2,
    volumes={TMP_DIR: tmp_vol},
    memory=4096,
)
def render_story(upload_key: str, segments: list, filename: str = "story.mp4") -> dict:
    """Cut the kept moments (in the user's storyboard ORDER, which may differ
    from chronological) and concat into one H.264 output. Per-segment
    re-encode + concat-demuxer copy: accurate cuts, uniform streams."""
    from pipeline_fns import _record_job

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        src = _resolve_source(upload_key, tmp)
        duration = _probe_duration(src)

        windows = []
        for seg in list(segments)[:MAX_MOMENTS * 2]:
            try:
                a, b = float(seg[0]), float(seg[1])
            except (TypeError, ValueError, IndexError):
                continue
            a, b = max(0.0, a), min(b, duration)
            if b - a >= 0.5:
                windows.append((a, b))
        if not windows:
            return {"error": "no_segments"}

        parts = []
        for i, (a, b) in enumerate(windows):
            part = tmp / f"part_{i:02d}.mp4"
            _run(["ffmpeg", "-y", "-ss", f"{a:.2f}", "-to", f"{b:.2f}", "-i", str(src),
                  "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                  "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
                  "-movflags", "+faststart", str(part)])
            parts.append(part)

        concat_list = tmp / "list.txt"
        concat_list.write_text("".join(f"file '{p}'\n" for p in parts))
        out_key = f"{upload_key}_out.mp4"
        out_path = Path(TMP_DIR) / out_key
        _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list),
              "-c", "copy", "-movflags", "+faststart", str(out_path)])
        tmp_vol.commit()

        # Standard History record: download / share / thumbnail / retention all
        # come along for free. notify=False - the assembler page shows the
        # result itself; no push story here.
        _record_job(out_key, filename or "story", out_path, notify=False)
        total = sum(b - a for a, b in windows)
        return {"video_key": out_key, "duration": round(total, 1)}
