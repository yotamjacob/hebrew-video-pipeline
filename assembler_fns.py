"""Story Assembler (Phases 1+2) - the backend behind the standalone /assembler page.

Turns raw footage - ONE long video or UP TO 5 CLIPS (interview + walkaround +
tasting shots...) - into a story-driven highlight cut: transcribe everything ->
Sonnet weaves the golden moments ACROSS clips into one arc -> the user curates
a storyboard -> render cuts each moment from its own clip, normalizes all
parts onto a common canvas, and concats.

DELIBERATELY SEPARATE from process_video (user directive 2026-08-13: do not
touch the main pipeline). It shares infrastructure by IMPORT ONLY:
- uploads arrive through the existing /upload_chunk route (same chunk layout),
- transcription mirrors process_video's settings at SEGMENT level (no word
  timestamps - storyboard moments snap to Whisper segment boundaries),
- the rendered output is written with the standard `_out.mp4` key (first
  clip's key) + a jobs_store record via _record_job, so History/download/
  share/retention all work on it with zero changes to those systems.

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
    SONNET_MODEL, HAIKU_MODEL, costs_store, _record_ai_spend, _msg_text,
)

# Mirrors process_video's noise gates (kept local - importing them would mean
# editing pipeline_fns' import surface, and these are stable tuned constants).
_NO_SPEECH_MAX = 0.6
_AVG_LOGPROB_MIN = -1.0

# Storyboard shape guardrails.
MAX_MOMENTS = 8
MIN_MOMENTS = 3
TARGET_TOTAL_SECONDS = "45-90"
MAX_CLIPS = 5
TOTAL_INPUT_MAX_SECONDS = 2400   # 40 min of raw footage across all clips


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


def _probe_dims(path: Path):
    r = _run(["ffprobe", "-v", "error", "-select_streams", "v:0",
              "-show_entries", "stream=width,height", "-of", "csv=p=0", str(path)],
             text=True)
    w, h = (r.stdout.strip().split(",") + ["0", "0"])[:2]
    w, h = int(w or 0), int(h or 0)
    # Even dimensions for yuv420p.
    return max(2, w - w % 2), max(2, h - h % 2)


def _pick_moments(clips, total_duration):
    """Sonnet reads ALL clip transcripts (clip-tagged, timestamped) and weaves
    the golden moments across them into one story arc. Moments are SEGMENT
    RANGES within a clip (never free timestamps - snapping to Whisper segment
    boundaries keeps every cut on a speech edge)."""
    import os
    from anthropic import Anthropic

    blocks = []
    for ci, clip in enumerate(clips):
        lines = "\n".join(
            f"[{ci}:{i}] {s['start']:.1f}-{s['end']:.1f}: "
            f"{'[ויזואלי] ' if s.get('visual') else ''}{s['text']}"
            for i, s in enumerate(clip["segs"]))
        blocks.append(f'### קליפ {ci} - "{clip["name"]}" ({clip["duration"]:.0f} שניות)\n'
                      + (lines or "(אין דיבור בקליפ הזה)"))
    multi = len(clips) > 1
    prompt = f"""לפניך תמלול מתוזמן של {"מספר סרטוני גלם" if multi else "סרטון גלם"} (סך הכל {total_duration:.0f} שניות): ראיון, סקירת עסק, אינטראקציות. המטרה: לבחור את הרגעים הכי חזקים לסרטון אינסטגרם קצר אחד שמספר סיפור.

כל שורה היא מקטע: [קליפ:מספר] התחלה-סוף: טקסט.

בחר {MIN_MOMENTS}-{MAX_MOMENTS} רגעים, בסך הכל {TARGET_TOTAL_SECONDS} שניות. כל רגע הוא טווח מקטעים רצוף בתוך קליפ אחד (from_seg עד to_seg, כולל). {"שלב רגעים מקליפים שונים לסיפור אחד קוהרנטי - " if multi else ""}סדר אותם כסיפור: פתיח שתופס (hook), גוף שמספר את הסיפור (story), רגע רגשי או ציטוט חזק (gold), וסגירה (closing).

בנוסף, כתוב "story": הסיפור המלא כפי שאתה מבין אותו מהחומר - פסקה מפורטת (4-8 משפטים): מי מופיע, מה קורה, מה הקשת הסיפורית (מאיפה לאן), מה הרגש המרכזי ומה המסר. כתוב אותה כך שתוכל לשמש בסיס לתסריט קריינות.

החזר JSON בלבד:
{{"title": "כותרת קצרה לסרטון", "story": "הסיפור המפורט...", "moments": [{{"clip": 0, "from_seg": 0, "to_seg": 2, "role": "hook", "quote": "הציטוט המרכזי מהרגע", "reason": "למה הרגע הזה חזק לסיפור"}}]}}

חוקים: role אחד מ-hook/story/gold/closing. הציטוט חייב להופיע בתמלול. אל תמציא עובדות שלא נאמרו. from_seg ו-to_seg חייבים להיות מאותו קליפ שצוין ב-clip. מקטעים המסומנים [ויזואלי] מתארים מה שרואים בקטע ללא דיבור - שלב אותם כרגעי אווירה, פתיח או מעבר כשהם מחזקים את הסיפור, ובשדה quote של רגע כזה כתוב את תיאור הסצנה.

התמלול:
{chr(10).join(blocks)}"""

    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    resp = client.messages.create(
        # Sonnet 5 may spend a chunk of the budget on a thinking block before
        # the JSON - 2000 truncated the answer mid-object (field 500 #3,
        # 2026-08-13). Leave generous room for thinking + full JSON.
        model=SONNET_MODEL, max_tokens=6000,
        messages=[{"role": "user", "content": prompt}])
    _record_ai_spend(costs_store, "assembler_moments", SONNET_MODEL, resp.usage)
    # Sonnet 5 may emit a ThinkingBlock BEFORE the text block (observed on
    # this prompt 2026-08-13 - the intermittent analyze 500).
    text = _msg_text(resp)
    start, end = text.find("{"), text.rfind("}")
    data = json.loads(text[start:end + 1])

    moments = []
    for m in (data.get("moments") or [])[:MAX_MOMENTS]:
        try:
            ci = int(m.get("clip", 0))
            a, b = int(m["from_seg"]), int(m["to_seg"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (0 <= ci < len(clips)):
            continue
        segs = clips[ci]["segs"]
        if not (0 <= a <= b < len(segs)):
            continue
        moments.append({
            "clip": ci,
            "start": round(segs[a]["start"], 2),
            "end": round(segs[b]["end"], 2),
            "role": m.get("role") if m.get("role") in ("hook", "story", "gold", "closing") else "story",
            "quote": str(m.get("quote") or segs[a]["text"])[:300],
            "reason": str(m.get("reason") or "")[:300],
            # A moment built from scene descriptions, not speech - the UI
            # drops the quote marks and tags it.
            "visual": bool(segs[a].get("visual")),
        })
    story = str(data.get("story") or "")[:2000]
    return str(data.get("title") or "")[:80], story, moments


def _visual_segments(src: Path, duration: float, workdir: Path, clip_name: str):
    """Speechless footage still tells a story - sample frames and have Haiku
    describe the distinct scenes as time-ranged segments. Haiku (not Sonnet)
    is deliberate: this is plain scene DESCRIPTION - the narrative judgment
    of which visuals earn a storyboard slot stays with the Sonnet story pass.
    Same division of labor as the stock-B-roll frame scoring. Returns
    [{"start","end","text","visual":True,"words":[]}] - wordless, so the
    caption remap can never produce captions from them. Best-effort: any
    failure returns [] and the clip simply stays speechless."""
    import base64
    import os
    try:
        from anthropic import Anthropic
        step = max(3.0, duration / 8)          # ≤8 frames per clip
        times, content = [], []
        t = step / 2
        while t < duration and len(times) < 8:
            fp = workdir / f"vis_{clip_name[:8]}_{len(times)}.jpg"
            _run(["ffmpeg", "-y", "-ss", f"{t:.2f}", "-i", str(src),
                  "-frames:v", "1", "-vf", "scale=320:-2", "-q:v", "6", str(fp)])
            content.append({"type": "text", "text": f"פריים בשנייה {t:.0f}:"})
            content.append({"type": "image", "source": {
                "type": "base64", "media_type": "image/jpeg",
                "data": base64.b64encode(fp.read_bytes()).decode()}})
            times.append(t)
            t += step
        if not times:
            return []
        content.append({"type": "text", "text":
            f'אלה פריימים מסרטון ללא דיבור באורך {duration:.0f} שניות ("{clip_name}"). '
            'קבץ אותם לסצנות נפרדות ותאר כל סצנה בעברית במשפט קצר וקונקרטי (מה רואים, לא פרשנות). '
            'החזר JSON בלבד: [{"start": 0, "end": 10, "desc": "תיאור הסצנה"}] '
            'כשהטווחים מכסים את הסרטון בסדר כרונולוגי.'})
        client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        resp = client.messages.create(model=HAIKU_MODEL, max_tokens=1500,
                                      messages=[{"role": "user", "content": content}])
        _record_ai_spend(costs_store, "assembler_vision", HAIKU_MODEL, resp.usage)
        text = _msg_text(resp)
        arr = json.loads(text[text.find("["):text.rfind("]") + 1])
        segs = []
        for s in arr[:10]:
            try:
                a, b = float(s["start"]), float(s["end"])
                desc = str(s["desc"]).strip()
            except (KeyError, TypeError, ValueError):
                continue
            a, b = max(0.0, a), min(b, duration)
            if b - a >= 1.0 and desc:
                segs.append({"start": round(a, 2), "end": round(b, 2),
                             "text": desc[:200], "visual": True, "words": []})
        return segs
    except Exception as exc:
        print(f"[assembler] visual analysis failed for {clip_name!r}: {exc!r}")
        return []


def _asm_words_path(upload_key: str) -> Path:
    return Path(TMP_DIR) / f"{upload_key}_asm_words.json"


def _captions_for_windows(windows, clip_segments):
    """Remap the per-clip word-level transcript onto the OUTPUT timeline of
    the kept windows (in storyboard order). One caption event per source
    segment slice inside a window; word timings shift with it, so the shared
    ASS builder can render classic/word/karaoke modes unchanged.
    windows: [(clip_idx, start, end)] - clip_segments: per clip, a list of
    {"start","end","text","words":[[s,e,w],...]}."""
    events = []
    cum = 0.0
    for ci, a, b in windows:
        win_len = b - a
        for seg in clip_segments[ci]:
            words = [w for w in (seg.get("words") or [])
                     if a - 0.05 <= float(w[0]) < b]
            if not words:
                continue
            shifted = [[round(max(0.0, float(w[0]) - a) + cum, 3),
                        round(min(float(w[1]), b) - a + cum, 3),
                        str(w[2])] for w in words]
            events.append({
                "start": shifted[0][0],
                "end": round(min(shifted[-1][1] + 0.2, cum + win_len), 3),
                "text": " ".join(w[2] for w in shifted),
                "words": shifted,
            })
        cum += win_len
    return events


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
def analyze_story(upload_keys, filenames=None) -> dict:
    """Transcribe every clip (model loaded once) + cross-clip golden-moment
    selection + per-moment thumbnails. `upload_keys` may be a single string
    (Phase-1 clients) or a list of up to MAX_CLIPS keys."""
    import os

    if isinstance(upload_keys, str):
        upload_keys = [upload_keys]
    upload_keys = list(upload_keys)[:MAX_CLIPS]
    names = list(filenames or [])
    while len(names) < len(upload_keys):
        names.append(f"clip {len(names) + 1}")

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        sources, durations = [], []
        for key in upload_keys:
            src = _resolve_source(key, tmp)
            sources.append(src)
            durations.append(_probe_duration(src))
        total = sum(durations)
        if total < 20:
            return {"error": "too_short", "duration": total}
        if total > TOTAL_INPUT_MAX_SECONDS:
            return {"error": "too_long", "duration": total}

        os.environ["HF_HOME"] = MODEL_DIR
        from faster_whisper import WhisperModel
        common = dict(
            # Word timestamps feed the caption burn at render time (words are
            # persisted server-side per clip - see _asm_words_path); moments
            # still snap to SEGMENT boundaries.
            language="he", word_timestamps=True,
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
        except Exception:
            m = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8",
                             download_root=MODEL_DIR)

        clips = []
        for key, src, dur, name in zip(upload_keys, sources, durations, names):
            # Silent clips (b-roll walkarounds shot without sound, muted
            # phone footage) have NO audio stream - the extract hard-fails.
            # They are legitimate story material: keep them as speechless
            # clips instead of 500ing the whole analysis (field bug,
            # 2026-08-13 - the second assembler 500).
            wav = tmp / f"audio_{len(clips)}.wav"
            segs = []
            try:
                _run(["ffmpeg", "-y", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000", str(wav)])
                raw, _info = m.transcribe(str(wav), **common)
                for s in raw:
                    nsp = float(getattr(s, "no_speech_prob", 0.0) or 0.0)
                    alp = float(getattr(s, "avg_logprob", 0.0) or 0.0)
                    if nsp >= _NO_SPEECH_MAX and alp <= _AVG_LOGPROB_MIN:
                        continue
                    text = (s.text or "").strip()
                    if text:
                        words = [[float(w.start), float(w.end), w.word.strip()]
                                 for w in (s.words or []) if w.word.strip()]
                        segs.append({"start": float(s.start), "end": float(s.end),
                                     "text": text, "words": words})
            except Exception as exc:
                print(f"[assembler] no transcribable audio in {name!r}: {exc!r}")
            # (Near-)speechless clip: the footage itself is the story material.
            # Haiku describes the scenes; the Sonnet pass below decides which
            # earn a storyboard slot. Wordless, so they never emit captions.
            if len(segs) < 2:
                segs += _visual_segments(src, dur, tmp, str(name))
                segs.sort(key=lambda s: s["start"])
            clips.append({"name": str(name)[:80], "duration": dur, "segs": segs})
            # Persist the word-level transcript next to the source so the
            # render can burn captions without a second transcription. Swept
            # with the other scratch files after 48h.
            _asm_words_path(key).write_text(
                json.dumps({"segments": segs}, ensure_ascii=False), encoding="utf-8")
        tmp_vol.commit()
        model_volume.commit()

        if sum(len(c["segs"]) for c in clips) < MIN_MOMENTS:
            return {"error": "no_speech", "duration": total}

        title, story, moments = _pick_moments(clips, total)
        if not moments:
            return {"error": "no_moments", "duration": total}
        for i, mo in enumerate(moments):
            mo["thumb"] = _thumb_b64(sources[mo["clip"]], mo["start"] + 0.5, tmp, i)

        return {"duration": round(total, 1), "title": title, "story": story,
                "moments": moments,
                "clips": [{"name": c["name"], "duration": round(c["duration"], 1),
                           "segments": [{"start": round(s["start"], 2),
                                         "end": round(s["end"], 2), "text": s["text"]}
                                        for s in c["segs"]]}
                          for c in clips]}


@app.function(
    image=burn_image,
    cpu=8,
    timeout=900,
    max_containers=2,
    volumes={TMP_DIR: tmp_vol},
    memory=4096,
)
def render_story(upload_keys, segments: list, filename: str = "story.mp4",
                 captions: bool = True, vo_key: str = None) -> dict:
    """Cut the kept moments (in the user's storyboard ORDER) from their
    respective clips, normalize every part onto a common canvas (mixed
    resolutions/orientations scale+pad; uniform fps + audio), and concat.
    `segments` items are [clip_index, start, end]; Phase-1 [start, end] pairs
    are accepted as clip 0."""
    from pipeline_fns import _record_job

    if isinstance(upload_keys, str):
        upload_keys = [upload_keys]
    upload_keys = list(upload_keys)[:MAX_CLIPS]

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        sources = [_resolve_source(k, tmp) for k in upload_keys]
        durations = [_probe_duration(s) for s in sources]

        windows = []
        for seg in list(segments)[:MAX_MOMENTS * 2]:
            try:
                if len(seg) >= 3:
                    ci, a, b = int(seg[0]), float(seg[1]), float(seg[2])
                else:
                    ci, a, b = 0, float(seg[0]), float(seg[1])
            except (TypeError, ValueError, IndexError):
                continue
            if not (0 <= ci < len(sources)):
                continue
            a, b = max(0.0, a), min(b, durations[ci])
            if b - a >= 0.5:
                windows.append((ci, a, b))
        if not windows:
            return {"error": "no_segments"}

        # Canvas = the first KEPT window's clip dimensions; every part is
        # scaled to fit and padded to exactly that frame, at uniform fps and
        # audio params, so the concat-demuxer copy is always safe.
        cw, ch = _probe_dims(sources[windows[0][0]])
        norm = (f"scale={cw}:{ch}:force_original_aspect_ratio=decrease,"
                f"pad={cw}:{ch}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30")

        # A part from a silent clip must still carry an audio STREAM (silence),
        # or the concat mixes audio-ful and audio-less parts and breaks - and
        # the VO ducking has no [0:a] to key on.
        def _has_audio(path):
            r = _run(["ffprobe", "-v", "error", "-select_streams", "a",
                      "-show_entries", "stream=codec_type", "-of", "csv=p=0",
                      str(path)], text=True)
            return bool(r.stdout.strip())

        clip_has_audio = {}
        parts = []
        for i, (ci, a, b) in enumerate(windows):
            if ci not in clip_has_audio:
                clip_has_audio[ci] = _has_audio(sources[ci])
            part = tmp / f"part_{i:02d}.mp4"
            cmd = ["ffmpeg", "-y", "-ss", f"{a:.2f}", "-to", f"{b:.2f}", "-i", str(sources[ci])]
            if not clip_has_audio[ci]:
                cmd += ["-f", "lavfi", "-t", f"{b - a:.2f}",
                        "-i", "anullsrc=r=48000:cl=stereo",
                        "-map", "0:v", "-map", "1:a", "-shortest"]
            cmd += ["-vf", norm,
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                    "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
                    "-movflags", "+faststart", str(part)]
            _run(cmd)
            parts.append(part)

        concat_list = tmp / "list.txt"
        concat_list.write_text("".join(f"file '{p}'\n" for p in parts))
        out_key = f"{upload_keys[0]}_out.mp4"
        out_path = Path(TMP_DIR) / out_key
        joined = tmp / "joined.mp4"
        _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list),
              "-c", "copy", "-movflags", "+faststart", str(joined)])

        # ── Captions: remap the analyze-time word transcript onto the output
        # timeline and burn through the SHARED build_caption_ass (same
        # builder, margins and defaults as the main pipeline's burn). Missing
        # transcript files (expired scratch, >48h) degrade to no captions
        # rather than failing the render.
        burned = False
        if captions:
            try:
                clip_segments = []
                for k in upload_keys:
                    wp = _asm_words_path(k)
                    clip_segments.append(
                        json.loads(wp.read_text(encoding="utf-8")).get("segments", [])
                        if wp.exists() else [])
                events = _captions_for_windows(windows, clip_segments)
                if events:
                    from pipeline_fns import build_caption_ass
                    ass_str = build_caption_ass(
                        cw, ch, "Heebo", 48,
                        max(25, cw // 14), int(0.08 * ch),
                        events, None)
                    ass_path = tmp / "captions.ass"
                    ass_path.write_text(ass_str, encoding="utf-8")
                    esc = str(ass_path).replace(":", r"\:")
                    captioned = tmp / "captioned.mp4"
                    _run(["ffmpeg", "-y", "-i", str(joined),
                          "-vf", f"subtitles='{esc}'",
                          "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                          "-pix_fmt", "yuv420p", "-c:a", "copy",
                          "-movflags", "+faststart", str(captioned)])
                    burned = True
            except Exception as exc:
                print(f"[assembler] caption burn failed - shipping clean cut: {exc!r}")
        visual = captioned if burned else joined

        # ── Voice-over (Phase 3): duck the original audio under the narration
        # and mix. Video stream is COPIED - this pass costs audio-encode only.
        # Filtergraph validated on synthetic media 2026-08-13: apad keeps a
        # shorter VO from truncating the cut (duration=first). A missing/
        # broken VO degrades to the un-narrated cut, never a failed render.
        mixed = False
        if vo_key:
            try:
                vo_src = _resolve_source(vo_key, tmp)
                _run(["ffmpeg", "-y", "-i", str(visual), "-i", str(vo_src),
                      "-filter_complex",
                      "[1:a]aformat=sample_rates=48000:channel_layouts=stereo,apad[vo];"
                      "[0:a][vo]sidechaincompress=threshold=0.05:ratio=8:attack=20:release=400[bg];"
                      "[bg][vo]amix=inputs=2:duration=first:normalize=0[aout]",
                      "-map", "0:v", "-map", "[aout]",
                      "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                      "-movflags", "+faststart", str(out_path)])
                mixed = True
            except Exception as exc:
                print(f"[assembler] VO mix failed - shipping without narration: {exc!r}")
        if not mixed:
            import shutil
            shutil.copy(visual, out_path)
        tmp_vol.commit()

        # Standard History record: download / share / thumbnail / retention all
        # come along for free. notify=False - the assembler page shows the
        # result itself; no push story here.
        _record_job(out_key, filename or "story", out_path, notify=False)
        total = sum(b - a for _ci, a, b in windows)
        return {"video_key": out_key, "duration": round(total, 1)}
