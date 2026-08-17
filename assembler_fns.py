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

CLIPS MODE (long -> shorts, 2026-08-16): the same analyze function with
`mode="clips"` turns ONE long recording (podcast, lecture, long interview -
up to 90 min) into 6-12 self-contained short-clip candidates, each with a
precise word-snapped trim, an on-screen hook line, and an honest virality
estimate (sub-scores + reasoning). Render is per clip through render_story
with `tighten` (silence-gap excision inside the clip) + `hook_text` (burned
via the shared ASS builder's hook box). See docs/assembler.md.
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

# Clips mode (long -> shorts) guardrails.
CLIPS_INPUT_MAX_SECONDS = 5400   # 90 min of source (podcast / lecture)
MAX_CANDIDATES = 12
MIN_CANDIDATES = 3
CLIP_MIN_SECONDS = 8
CLIP_MAX_SECONDS = 90


def _run(cmd, **kw):
    return subprocess.run(cmd, check=True, capture_output=True, **kw)


def _resolve_source(upload_key: str, workdir: Path) -> Path:
    """Assemble chunk files into the cached `{key}_src.mp4` (same layout and
    cache convention as process_video) or reuse an existing cache."""
    # A WARM container keeps the volume view it started with - without a
    # reload it never sees chunks committed after it spun up (process_video
    # and burn_captions_fn reload on entry for the same reason). Field:
    # back-to-back assembler runs, the second one "No upload found"
    # (2026-08-16).
    # Volume writes from the api containers publish in the BACKGROUND (a
    # few seconds); a render that follows its uploads immediately (branding
    # assets, VO, a just-landed clip) can otherwise read an empty view.
    # Reload + short backoff before declaring the upload missing.
    import time as _t
    cache = Path(TMP_DIR) / f"{upload_key}_src.mp4"
    chunk_paths = []
    for attempt in range(6):
        try:
            tmp_vol.reload()
        except Exception:
            pass
        if cache.exists():
            return cache
        chunk_paths = sorted(Path(TMP_DIR).glob(f"{upload_key}_chunk_*"))
        if chunk_paths:
            break
        _t.sleep(2)
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
    """(w, h) of the FIRST video stream as ffmpeg will decode it - i.e. with a
    90/270 rotation tag or display matrix already applied (phone footage
    stores portrait as landscape + rotate=90; every ffmpeg pass autorotates,
    so the canvas and the reframe decision must see the rotated size)."""
    r = _run(["ffprobe", "-v", "error", "-select_streams", "v:0",
              "-show_entries", "stream=width,height,side_data_list:stream_tags=rotate",
              "-of", "json", str(path)], text=True)
    w = h = 0
    rot = 0
    try:
        st = json.loads(r.stdout)["streams"][0]
        w, h = int(st.get("width") or 0), int(st.get("height") or 0)
        tag = (st.get("tags") or {}).get("rotate")
        if tag not in (None, ""):
            rot = int(float(tag))
        else:
            for sd in st.get("side_data_list") or []:
                if "rotation" in sd:
                    rot = int(float(sd["rotation"]))
                    break
    except Exception:
        pass
    if abs(rot) % 180 == 90:
        w, h = h, w
    # Even dimensions for yuv420p.
    return max(2, w - w % 2), max(2, h - h % 2)


def _guidance_block(guidance):
    """User steering for the selection prompts (2026-08-16): free text such
    as "רק קטעים על גיוס כספים" or "בלי הפרסומת בהתחלה". Bounded, wrapped so
    the model treats it as PREFERENCES about which content to pick - never
    as a change to the output format or rules."""
    g = " ".join(str(guidance or "").split())[:400]
    if not g:
        return ""
    return ("\n\nהנחיות מהיוצר לבחירת הקטעים (עדיפויות תוכן בלבד - הפורמט והחוקים למטה לא משתנים): "
            f'"{g}"\n')


def _parse_answer(text, list_key, item_key, scalar_keys=()):
    """Parse a model answer that SHOULD be one JSON object with a list under
    `list_key`. Strict first; when that fails (truncated by max_tokens, a
    stray quote inside one item, text around the object - all seen on
    1-hour transcripts, 2026-08-17: "Expecting ',' delimiter") fall back to
    salvaging every complete `{... item_key ...}` object plus the scalar
    string fields by regex. Returns (data_dict, salvaged: bool)."""
    import re as _re
    try:
        start, end = text.find("{"), text.rfind("}")
        data = json.loads(text[start:end + 1])
        if isinstance(data, dict):
            return data, False
    except Exception:
        pass
    data = {list_key: _salvage_objects(text, item_key)}
    for k in scalar_keys:
        m = _re.search(r'"' + _re.escape(k) + r'"\s*:\s*"((?:[^"\\]|\\.)*)"', text)
        if m:
            try:
                data[k] = json.loads('"' + m.group(1) + '"')
            except Exception:
                data[k] = m.group(1)
    return data, True


def _pick_moments(clips, total_duration, guidance=""):
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

{_guidance_block(guidance)}
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
    data, salvaged = _parse_answer(text, "moments", "from_seg", ("title", "story"))
    if salvaged:
        print(f"[assembler] story answer salvaged (stop={getattr(resp, 'stop_reason', '?')}, "
              f"{len(data.get('moments') or [])} moments)")

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


# ── Clips mode (long -> shorts) ──────────────────────────────────────────────

def _virality_score(v, duration):
    """Composite 1-99 virality ESTIMATE from the model's holistic score + its
    five sub-scores, with a duration prior (short-form sweet spot 15-60s).
    Deterministic and pure - the model reasons, this blends. Half the weight
    is the model's own holistic call, half the weighted rubric, so a great
    hook can't carry a clip nobody will finish and vice versa."""
    def _n(k, hi):
        try:
            return max(0.0, min(float(hi), float((v or {}).get(k, 0) or 0)))
        except (TypeError, ValueError):
            return 0.0
    rubric = (_n("hook", 10) * 0.30 + _n("retention", 10) * 0.25
              + _n("emotion", 10) * 0.15 + _n("clarity", 10) * 0.15
              + _n("shareability", 10) * 0.15) * 10.0
    score = 0.5 * _n("score", 99) + 0.5 * rubric
    if duration > 75:
        score -= 8
    elif duration > 60:
        score -= 4
    elif duration < 12:
        score -= 6
    return int(max(1, min(99, round(score))))


def _snap_to_words(start, end, words, lo, hi):
    """Snap a model-proposed [start, end] onto the nearest word start / word
    end (within 0.6s) inside the allowed context range [lo, hi], then add a
    small breathing pad. words: [[s, e, text], ...] sorted. Returns (a, b)."""
    start = max(lo, min(hi, float(start)))
    end = max(lo, min(hi, float(end)))
    if words:
        ws = min((float(w[0]) for w in words), key=lambda t: abs(t - start))
        we = min((float(w[1]) for w in words), key=lambda t: abs(t - end))
        if abs(ws - start) <= 0.6:
            start = ws
        if abs(we - end) <= 0.6:
            end = we
    a = max(lo, start - 0.12)
    b = min(hi, end + 0.25)
    return round(a, 2), round(b, 2)


def _tighten_windows(windows, clip_segments, min_gap=0.6, pad=0.18):
    """Excise silence gaps INSIDE each window: consecutive words closer than
    `min_gap` merge into one run; every run keeps `pad` breathing room and is
    clamped to its window. Windows with no words (visual footage) pass
    through untouched. Pure - the assembler's lightweight stand-in for the
    main pipeline's cutter, so a rendered clip is already tight."""
    out = []
    for ci, a, b in windows:
        a, b = float(a), float(b)
        words = []
        for seg in (clip_segments[ci] if ci < len(clip_segments) else []):
            for w in (seg.get("words") or []):
                s, e = float(w[0]), float(w[1])
                if e > a and s < b:
                    words.append((max(a, s), min(b, e)))
        words.sort()
        if not words:
            out.append((ci, a, b))
            continue
        runs, cur = [], [words[0][0], words[0][1]]
        for s, e in words[1:]:
            if s - cur[1] >= min_gap:
                runs.append(cur)
                cur = [s, e]
            else:
                cur[1] = max(cur[1], e)
        runs.append(cur)
        for s, e in runs:
            s2, e2 = max(a, s - pad), min(b, e + pad)
            if e2 - s2 >= 0.3:
                out.append((ci, round(s2, 3), round(e2, 3)))
    return out


def _salvage_objects(text, key="id"):
    """Recover every complete `{... "<key>": ...}` object from a model answer
    whose outer JSON is broken or truncated (max_tokens mid-array, a stray
    fence, an unescaped quote in one item). raw_decode from each candidate
    '{' - objects that fail to decode are skipped, the rest survive. Pure
    (in-function import so the AST test extraction runs it standalone)."""
    import json as _json
    dec = _json.JSONDecoder()
    out, i, n = [], 0, len(text)
    while i < n:
        j = text.find("{", i)
        if j < 0:
            break
        try:
            obj, end = dec.raw_decode(text, j)
        except ValueError:
            i = j + 1
            continue
        if isinstance(obj, dict) and key in obj:
            out.append(obj)
            i = j + max(1, end - j)
        elif isinstance(obj, dict) and isinstance(obj.get("clips"), list):
            out.extend(o for o in obj["clips"] if isinstance(o, dict) and key in o)
            i = j + max(1, end - j)
        else:
            i = j + 1
    return out


def _clamp_end(segs, a, b, max_len):
    """If [a, b] is longer than max_len, pull b back to the LAST segment end
    that keeps the clip within max_len (a speech edge, never mid-word); if
    no segment end fits, hard-cut at a + max_len. Pure."""
    if b - a <= max_len:
        return b
    best = None
    for s in segs:
        e = float(s["end"])
        if a < e <= a + max_len:
            best = e
    return round(best if best is not None else a + max_len, 2)


def _pick_clips(clips, total_duration, guidance=""):
    """Long -> shorts. Two Sonnet passes:
      1. the FULL segment-level transcript -> 6-12 self-contained candidate
         ranges (segment ids, never free timestamps) + a one-line summary;
      2. every candidate's WORD-level transcript (with one segment of context
         on each side) -> precise word-snapped trim (open on the hook word,
         close on the payoff), title, on-screen hook line, quote, and an
         honest virality rubric (5 sub-scores + holistic + reasoning + tip).
    Returns (summary, candidates) sorted by composite score desc."""
    import os
    from anthropic import Anthropic
    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    # ── Pass 1: candidates from the whole transcript ──
    blocks = []
    for ci, clip in enumerate(clips):
        lines = "\n".join(
            f"[{ci}:{i}] {s['start']:.1f}-{s['end']:.1f}: "
            f"{'[ויזואלי] ' if s.get('visual') else ''}{s['text']}"
            for i, s in enumerate(clip["segs"]))
        blocks.append(f'### קליפ {ci} - "{clip["name"]}" ({clip["duration"]:.0f} שניות)\n'
                      + (lines or "(אין דיבור בקליפ הזה)"))
    # Short recordings (a 1-3 minute clip) can't yield 3-12 distinct shorts -
    # scale the ask so the model returns 1-2 real candidates instead of
    # padding with junk or nothing.
    want_hi = max(1, min(MAX_CANDIDATES, int(total_duration // 240) + 1))
    want_lo = 1 if total_duration < 600 else MIN_CANDIDATES
    prompt1 = f"""לפניך תמלול מתוזמן של הקלטה ({total_duration:.0f} שניות): פודקאסט, הרצאה, ראיון או שיחה. המטרה: לאתר {want_lo}-{want_hi} קטעים שכל אחד מהם יכול לעמוד בפני עצמו כסרטון קצר (Reels / TikTok / Shorts), באורך {CLIP_MIN_SECONDS + 7}-{CLIP_MAX_SECONDS} שניות כל אחד. אם ההקלטה קצרה, החזר קטע אחד או שניים בלבד - אל תמציא קטעים חלשים כדי למלא מכסה, ואל תחזיר רשימה ריקה אם יש קטע אחד סביר.

מה הופך קטע לחזק: הוא נפתח בשורה שעוצרת גלילה תוך 3 שניות (טענה חדה, שאלה, סיפור, מספר מפתיע, "הטעות ש..."), הוא מובן לגמרי גם למי שלא צפה בשאר, יש בו מתח ותשלום (payoff) - תובנה, פאנץ', רגע רגשי, טיפ ישים - והוא נגמר במשפט חזק, לא באמצע מחשבה. חפש גיוון: לא שמונה גרסאות של אותו רעיון. אל תבחר קטעי מעבר, פרסומות, הצגות עצמיות ארוכות, דיבור טכני/מנהלי או "אז על מה נדבר היום".

כל שורה היא מקטע: [קליפ:מספר] התחלה-סוף: טקסט. קטע = טווח מקטעים רצוף בתוך קליפ אחד (from_seg עד to_seg, כולל). עדיף שהקטעים לא יחפפו זה את זה.
{_guidance_block(guidance)}
החזר JSON בלבד:
{{"summary": "משפט-שניים: על מה ההקלטה ומי מדבר", "candidates": [{{"clip": 0, "from_seg": 12, "to_seg": 19, "title": "כותרת קצרה", "angle": "למה הקטע הזה עומד בפני עצמו ומה התשלום שלו"}}]}}

התמלול:
{chr(10).join(blocks)}"""
    resp = client.messages.create(model=SONNET_MODEL, max_tokens=12000,
                                  messages=[{"role": "user", "content": prompt1}])
    _record_ai_spend(costs_store, "assembler_clips_pick", SONNET_MODEL, resp.usage)
    text = _msg_text(resp)
    data, salvaged = _parse_answer(text, "candidates", "from_seg", ("summary",))
    if salvaged:
        print(f"[assembler] clips pass-1 answer salvaged (stop={getattr(resp, 'stop_reason', '?')}, "
              f"{len(data.get('candidates') or [])} candidates)")
    summary = str(data.get("summary") or "")[:400]

    cands = []
    for c in (data.get("candidates") or [])[:MAX_CANDIDATES]:
        try:
            ci = int(c.get("clip", 0))
            a, b = int(c["from_seg"]), int(c["to_seg"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (0 <= ci < len(clips)):
            continue
        segs = clips[ci]["segs"]
        if not (0 <= a <= b < len(segs)):
            continue
        # Context: one segment on each side, so pass 2 can move the cut
        # onto the real hook word / payoff word without leaving the range.
        lo_i, hi_i = max(0, a - 1), min(len(segs) - 1, b + 1)
        lo, hi = float(segs[lo_i]["start"]), float(segs[hi_i]["end"])
        words = [w for s in segs[lo_i:hi_i + 1] for w in (s.get("words") or [])]
        cands.append({
            "clip": ci, "from_seg": a, "to_seg": b,
            "start": float(segs[a]["start"]), "end": float(segs[b]["end"]),
            "lo": lo, "hi": hi, "words": words,
            "title": str(c.get("title") or "")[:80],
            "angle": str(c.get("angle") or "")[:300],
            "visual": bool(segs[a].get("visual")),
        })
    if not cands:
        # Same 3-tuple shape as the success path (a 2-tuple here crashed
        # analyze on a 1-minute source: "not enough values to unpack",
        # field 2026-08-16).
        return summary, [], ["pass 1: no valid candidates"]

    # ── Pass 2: precise trims + hook + virality rubric. BATCHED (4 per
    # call): one call for 9-12 candidates with 60-160s of word-level text
    # each overran the answer budget on a 1-hour source and the whole
    # rubric was lost (field, 2026-08-16). Smaller calls + salvage keep a
    # single bad item from taking the rest down.
    refined, pass2_errors = {}, []
    PASS2_BATCH = 4
    for b0 in range(0, len(cands), PASS2_BATCH):
        batch = list(range(b0, min(len(cands), b0 + PASS2_BATCH)))
        try:
            got = _refine_batch(client, cands, batch)
            refined.update(got)
            missing = [i for i in batch if i not in got]
            if missing:
                pass2_errors.append(f"batch {b0}: no answer for {missing}")
        except Exception as exc:
            print(f"[assembler] clips pass-2 batch {b0} failed - shipping pass-1 trims: {exc!r}")
            pass2_errors.append(f"batch {b0}: {type(exc).__name__}: {str(exc)[:160]}")

    out = []
    for i, c in enumerate(cands):
        r = refined.get(i) or {}
        segs = clips[c["clip"]]["segs"]
        try:
            st, en = float(r.get("start", c["start"])), float(r.get("end", c["end"]))
        except (TypeError, ValueError):
            st, en = c["start"], c["end"]
        a, b = _snap_to_words(st, en, c["words"], c["lo"], c["hi"])
        if b - a < CLIP_MIN_SECONDS:
            a, b = round(c["start"], 2), round(c["end"], 2)   # fall back to pass-1 range
        # Never ship a "short" longer than the ceiling - pull the end back to
        # a segment edge (pass 1 sometimes proposes 2-3 min ranges).
        b = _clamp_end(segs, a, b, CLIP_MAX_SECONDS + 10)
        v = r.get("virality") if isinstance(r.get("virality"), dict) else {}
        dur = b - a
        out.append({
            "clip": c["clip"], "start": a, "end": b, "duration": round(dur, 1),
            "title": str(r.get("title") or c["title"] or "")[:80],
            "hook": str(r.get("hook") or "")[:120],
            "quote": str(r.get("quote") or c["angle"] or "")[:300],
            "visual": c["visual"],
            "score": _virality_score(v, dur) if v else None,
            "scored": bool(v),
            "virality": {
                "hook": v.get("hook"), "retention": v.get("retention"),
                "emotion": v.get("emotion"), "clarity": v.get("clarity"),
                "shareability": v.get("shareability"),
                "reasoning": str(v.get("reasoning") or "")[:600],
                "tip": str(v.get("tip") or "")[:300],
            },
        })
    # Scored first (best first), unscored after in pass-1 order.
    out.sort(key=lambda c: (-(c["score"] or 0), c["start"]))
    if pass2_errors:
        print(f"[assembler] clips pass-2 issues: {pass2_errors}")
    return summary, out, pass2_errors


def _refine_batch(client, cands, idxs):
    """Pass 2 for a batch of candidate indexes -> {idx: refined dict}."""
    parts = []
    for i in idxs:
        c = cands[i]
        toks = []
        for w in c["words"]:
            s = float(w[0])
            mark = ""
            if abs(s - c["start"]) < 0.01:
                mark = "<<< "
            toks.append(f"{mark}[{s:.2f}]{w[2]}")
        body = " ".join(toks) if toks else "(קטע ויזואלי ללא דיבור)"
        parts.append(
            f'### קטע {i} · "{c["title"]}" · טווח מותר {c["lo"]:.2f}-{c["hi"]:.2f} · '
            f'הצעה ראשונית {c["start"]:.2f}-{c["end"]:.2f}\n{c["angle"]}\n{body} >>>')
    prompt2 = f"""לפניך {len(idxs)} קטעים מועמדים מתוך הקלטה ארוכה, כל אחד עם התמלול שלו ברמת מילה בפורמט [שנייה]מילה. כל קטע מגיע עם מעט הקשר לפני ואחרי ההצעה הראשונית (ההצעה מתחילה ב-<<< ונגמרת ב->>>). מותר להזיז את גבולות החיתוך רק בתוך "הטווח המותר".

לכל קטע החזר:
- start / end: זמני חיתוך מדויקים בשניות. start = תחילת המילה הראשונה שכדאי לפתוח בה (דלג על "אז", "אה", "אוקיי", "כן", חצאי משפטים ותשובות ל"מה?"), end = סוף המילה האחרונה של המשפט שסוגר חזק. אורך יעד {CLIP_MIN_SECONDS + 7}-{CLIP_MAX_SECONDS} שניות (מותר {CLIP_MIN_SECONDS}+ אם הפאנץ' קצר ומושלם).
- title: כותרת קצרה (עד 8 מילים).
- hook: שורת הוק שתוצג על המסך ב-4 השניות הראשונות (עד 8 מילים, בלי סימני קריאה מיותרים) - הבטחה, שאלה או טענה שגורמת להישאר. לא ציטוט מילולי בהכרח.
- quote: הציטוט המרכזי כפי שנאמר בפועל.
- virality: הערכה כנה ומנומקת של הפוטנציאל לפלטפורמות קצרות בעברית, לקהל ישראלי. אל תחמיא: 40 זה קליפ סביר, 60 טוב, 80+ נדיר. תתי-ציונים 0-10:
  - hook: כמה 3 השניות הראשונות עוצרות גלילה
  - retention: קצב, מתח, האם יש סיבה להישאר עד הסוף
  - emotion: עוצמת רגש, הפתעה, הזדהות
  - clarity: מובן בלי הקשר, רעיון אחד ברור
  - shareability: ירצו לשלוח, לשמור, להתווכח בתגובות (דעה חדה, טיפ ישים, ויכוח)
  - score (0-99): ההערכה הכוללת שלך
  - reasoning: 2-3 משפטים - למה הציון הזה, מה החוזקה ומה החולשה
  - tip: המלצה אחת קונקרטית שתעלה את הסיכוי (מה לחתוך, איזה כיתוב להוסיף, מה לחדד)

החזר JSON בלבד, בלי טקסט לפני או אחרי ובלי גדרות קוד:
{{"clips": [{{"id": {idxs[0]}, "start": 12.34, "end": 45.67, "title": "...", "hook": "...", "quote": "...", "virality": {{"hook": 7, "retention": 6, "emotion": 5, "clarity": 8, "shareability": 6, "score": 58, "reasoning": "...", "tip": "..."}}}}]}}
שדה id חייב להיות מספר הקטע כפי שמופיע בכותרת שלו.

הקטעים:
{chr(10).join(parts)}"""
    resp2 = client.messages.create(model=SONNET_MODEL, max_tokens=8000,
                                   messages=[{"role": "user", "content": prompt2}])
    _record_ai_spend(costs_store, "assembler_clips_score", SONNET_MODEL, resp2.usage)
    text2 = _msg_text(resp2)
    got = {}
    for r in _salvage_objects(text2, "id"):
        try:
            k = int(r.get("id"))
        except (TypeError, ValueError):
            continue
        if k in idxs and k not in got:
            got[k] = r
    if not got:
        raise ValueError(f"unparseable answer (stop={getattr(resp2, 'stop_reason', '?')}): "
                         f"{text2[:120]!r}...{text2[-80:]!r}")
    return got


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
    # 1800 (was 900): clips mode transcribes up to 90 min of source.
    timeout=1800,
    # Hidden beta: keep the assembler's GPU ceiling tiny and independent of
    # the main pipeline's cap so it can never crowd out paying /process jobs.
    max_containers=2,
    volumes={MODEL_DIR: model_volume, TMP_DIR: tmp_vol},
    memory=4096,
    secrets=[modal.Secret.from_name("anthropic-secret")],
)
def analyze_story(upload_keys, filenames=None, mode: str = "story", guidance: str = "") -> dict:
    """Transcribe every clip (model loaded once) + cross-clip golden-moment
    selection + per-moment thumbnails. `upload_keys` may be a single string
    (Phase-1 clients) or a list of up to MAX_CLIPS keys.
    `mode="clips"` (long -> shorts): same transcription, then _pick_clips
    instead of _pick_moments - returns `candidates` (see docstring)."""
    import os
    clips_mode = (mode == "clips")
    max_total = CLIPS_INPUT_MAX_SECONDS if clips_mode else TOTAL_INPUT_MAX_SECONDS

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
        if total > max_total:
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

        clips_meta = [{"name": c["name"], "duration": round(c["duration"], 1),
                       "segments": [{"start": round(s["start"], 2),
                                     "end": round(s["end"], 2), "text": s["text"]}
                                    for s in c["segs"]]}
                      for c in clips]

        if clips_mode:
            summary, candidates, pass2_errors = _pick_clips(clips, total, guidance)
            if not candidates:
                return {"error": "no_moments", "duration": total}
            for i, c in enumerate(candidates):
                c["thumb"] = _thumb_b64(sources[c["clip"]], c["start"] + 0.5, tmp, i)
            return {"mode": "clips", "duration": round(total, 1), "summary": summary,
                    "candidates": candidates, "clips": clips_meta,
                    # Streamable source keys (uid-prefixed `_src.mp4`) for the
                    # page's in-place clip previews via /media (range-capable).
                    "sources": [f"{k}_src.mp4" for k in upload_keys],
                    # Diagnostics only (page shows a soft note when non-empty).
                    "issues": pass2_errors}

        title, story, moments = _pick_moments(clips, total, guidance)
        if not moments:
            return {"error": "no_moments", "duration": total}
        for i, mo in enumerate(moments):
            mo["thumb"] = _thumb_b64(sources[mo["clip"]], mo["start"] + 0.5, tmp, i)

        return {"duration": round(total, 1), "title": title, "story": story,
                "moments": moments, "clips": clips_meta,
                "sources": [f"{k}_src.mp4" for k in upload_keys]}


INTRO_OUTRO_MAX_SECONDS = 20.0

# ── Auto-reframe 16:9 -> 9:16 (2026-08-16) ───────────────────────────────────
REFRAME_SAMPLE_FPS = 3
REFRAME_MAX_KEYFRAMES = 30


def _reframe_plan(samples, crop_frac, dead=0.05, max_speed=0.30, hold=6.0, tau=0.8):
    """Turn per-sample face detections into a smooth pan of a full-height
    vertical crop. samples: [(t, faces)] with faces = [(cx, cy, w, h)] as
    FRACTIONS of the frame; crop_frac = crop width / frame width. Returns
    keyframes [(t, cx)] of the crop CENTER (fraction of frame width),
    piecewise-linear, simplified to <= REFRAME_MAX_KEYFRAMES.
    Rules (all in width-fractions): if every face fits inside 90% of the
    crop -> center the group (a two-shot stays a two-shot); else follow the
    LARGEST face; no face -> HOLD the last position (a talking head that
    turns away or a missed detection must not yank the frame), drifting
    back to the middle only after `hold` seconds; a 3-sample median kills one-frame
    detector glitches; a dead zone of `dead` stops micro-pans; motion is
    rate-limited to `max_speed` (fraction of width per second). Pure."""
    half = crop_frac / 2.0
    lo, hi = half, 1.0 - half

    def _target(faces):
        if not faces:
            return None
        left = min(f[0] - f[2] / 2.0 for f in faces)
        right = max(f[0] + f[2] / 2.0 for f in faces)
        if right - left <= crop_frac * 0.9:
            c = (left + right) / 2.0
        else:
            c = max(faces, key=lambda f: f[2] * f[3])[0]
        return min(hi, max(lo, c))

    raw = [(float(t), _target(faces)) for t, faces in samples]
    if not raw:
        return [(0.0, 0.5)]
    # Offline advantage: a detection GAP (face bowed / turned / occluded)
    # that ends with the face found again is bridged by interpolating
    # between the last and next known positions (gap <= `bridge` s) - a
    # human editor's pan, not a freeze-then-jump.
    bridge = 20.0
    known = [i for i, (_t, v) in enumerate(raw) if v is not None]
    for a_i, b_i in zip(known, known[1:]):
        if b_i - a_i > 1 and raw[b_i][0] - raw[a_i][0] <= bridge:
            ta, va = raw[a_i]
            tb, vb = raw[b_i]
            for j in range(a_i + 1, b_i):
                tj = raw[j][0]
                raw[j] = (tj, va + (vb - va) * (tj - ta) / max(1e-6, tb - ta))
    # Median-of-3 on the detected targets (None stays None unless neighbours agree).
    med = []
    for i, (t, v) in enumerate(raw):
        win = [x[1] for x in raw[max(0, i - 1):i + 2] if x[1] is not None]
        med.append((t, sorted(win)[len(win) // 2] if len(win) >= 2 else v))
    import math
    first = next((v for _t, v in med if v is not None), 0.5)
    cur = min(hi, max(lo, first))
    held = cur                      # the target we are easing toward
    out = [(med[0][0], cur)]
    last_seen = med[0][0]
    prev_t = med[0][0]
    for t, v in med[1:]:
        dt = max(1e-3, t - prev_t)
        if v is not None:
            last_seen = t
            # Dead zone on the TARGET: re-aim only when the face has really
            # moved, so breathing/nodding never starts a pan...
            if abs(v - held) > dead:
                held = v
        elif t - last_seen > hold:
            held = min(hi, max(lo, 0.5))
        # ...then ease toward it continuously (exponential, time constant
        # `tau`) with a speed cap - no bursts, no stair-steps.
        alpha = 1.0 - math.exp(-dt / tau)
        step = (held - cur) * alpha
        step = max(-max_speed * dt, min(max_speed * dt, step))
        cur = cur + step
        out.append((t, cur))
        prev_t = t
    # Simplify: drop points on straight runs (constant or same slope), then
    # cap by keeping the largest-error points (RDP-lite).
    # Simplify: greedy - keep a point only when the straight line from the
    # last kept point to the NEXT sample would miss it by > 0.4% of width.
    simp = [out[0]]
    for i in range(1, len(out) - 1):
        a, b, c = simp[-1], out[i], out[i + 1]
        interp = a[1] + (c[1] - a[1]) * ((b[0] - a[0]) / max(1e-6, c[0] - a[0]))
        if abs(interp - b[1]) > 0.004:
            simp.append(b)
    if len(out) > 1:
        simp.append(out[-1])
    while len(simp) > REFRAME_MAX_KEYFRAMES:
        # remove the interior point whose removal changes the path least
        best_i, best_err = 1, None
        for i in range(1, len(simp) - 1):
            a, b, c = simp[i - 1], simp[i], simp[i + 1]
            interp = a[1] + (c[1] - a[1]) * ((b[0] - a[0]) / max(1e-6, c[0] - a[0]))
            err = abs(interp - b[1])
            if best_err is None or err < best_err:
                best_i, best_err = i, err
        simp.pop(best_i)
    return [(round(t, 3), round(c, 4)) for t, c in simp]


def _crop_x_expr(keyframes, src_w, crop_w):
    """ffmpeg `crop` x-expression (pixels, part-relative `t`) for piecewise-
    linear keyframes of the crop CENTER fraction. Constant when there is a
    single keyframe. Always inside [0, src_w - crop_w]. Pure."""
    max_x = max(0, src_w - crop_w)

    def px(c):
        return int(round(min(max_x, max(0, c * src_w - crop_w / 2.0))))
    if not keyframes:
        return str(max_x // 2)
    if len(keyframes) == 1:
        return str(px(keyframes[0][1]))
    expr = str(px(keyframes[-1][1]))
    for i in range(len(keyframes) - 2, -1, -1):
        t0, c0 = keyframes[i]
        t1, c1 = keyframes[i + 1]
        x0, x1 = px(c0), px(c1)
        seg = f"{x0}+({x1}-{x0})*(t-{t0:.3f})/{max(1e-3, t1 - t0):.3f}"
        expr = f"if(lt(t\,{t1:.3f})\,{seg}\,{expr})"
    return expr


def _split_plan(samples, crop_frac, min_presence=0.4):
    """Two-speaker detector for the stacked split-screen. Clusters every
    detected face center into a LEFT and a RIGHT group (2-means on x, seeded
    at 0.3 / 0.7); returns {"left": (cx, cy), "right": (cx, cy)} medians
    when BOTH groups are present in >= `min_presence` of the samples and
    are too far apart to share one tracked crop (separation > 0.9 x
    crop_frac - when they fit, the tracked crop's group-center framing is
    the better picture). Otherwise None (-> tracked crop). Pure."""
    pts = [(f[0], f[1]) for _t, faces in samples for f in faces]
    if len(samples) < 3 or len(pts) < 4:
        return None
    c1, c2 = 0.3, 0.7
    for _ in range(8):
        g1 = [p for p in pts if abs(p[0] - c1) <= abs(p[0] - c2)]
        g2 = [p for p in pts if abs(p[0] - c1) > abs(p[0] - c2)]
        if not g1 or not g2:
            return None
        c1 = sum(p[0] for p in g1) / len(g1)
        c2 = sum(p[0] for p in g2) / len(g2)
    if abs(c1 - c2) <= 0.9 * crop_frac:
        return None
    left, right = (g1, g2) if c1 < c2 else (g2, g1)
    mid = (c1 + c2) / 2.0
    n = len(samples)
    pres_l = sum(1 for _t, faces in samples if any(f[0] < mid for f in faces)) / float(n)
    pres_r = sum(1 for _t, faces in samples if any(f[0] >= mid for f in faces)) / float(n)
    if pres_l < min_presence or pres_r < min_presence:
        return None

    def _median(vals):
        v = sorted(vals)
        return v[len(v) // 2]
    return {"left": (_median([p[0] for p in left]), _median([p[1] for p in left])),
            "right": (_median([p[0] for p in right]), _median([p[1] for p in right]))}


def _split_chain(plan, sw, sh, cw, ch):
    """filter fragment (chainable) that stacks two speaker crops: the LEFT
    speaker on top, the RIGHT below. Each tile is (cw x ch/2) - 9:8 on a
    9:16 canvas; the source crop for a tile is half the source width (a
    Zoom participant's pane) at the matching 9:8 height, positioned around
    the speaker's median face (a little above center), clamped inside the
    frame. Pure."""
    tile_h = ch // 2
    tile_h -= tile_h % 2
    crop_w = sw // 2
    crop_w -= crop_w % 2
    crop_h = int(round(crop_w * tile_h / float(cw)))
    if crop_h > sh:
        crop_h = sh - sh % 2
        crop_w = int(round(crop_h * cw / float(tile_h)))
        crop_w -= crop_w % 2
    crop_h -= crop_h % 2

    def _xy(c):
        cx, cy = c
        x = int(round(min(max(0.0, cx * sw - crop_w / 2.0), sw - crop_w)))
        y = int(round(min(max(0.0, cy * sh - crop_h * 0.45), sh - crop_h)))
        return x, y
    xl, yl = _xy(plan["left"])
    xr, yr = _xy(plan["right"])
    return (f"split=2[sl][sr];"
            f"[sl]crop={crop_w}:{crop_h}:{xl}:{yl},scale={cw}:{tile_h}[tl];"
            f"[sr]crop={crop_w}:{crop_h}:{xr}:{yr},scale={cw}:{tile_h}[tr];"
            f"[tl][tr]vstack")


def _faces_in_image(img_path, det_cache):
    """All faces in a frame as (cx, cy, w, h) fractions via YuNet (the same
    detector the punch-in zoom uses); [] on any failure. det_cache reuses
    one detector per frame size."""
    try:
        import os
        import cv2
        model = os.environ.get("YUNET_MODEL", "/usr/local/share/models/yunet.onnx")
        if not os.path.exists(model):
            return []
        img = cv2.imread(str(img_path))
        if img is None:
            return []
        h, w = img.shape[:2]
        det = det_cache.get((w, h))
        if det is None:
            # 0.5 (the zoom's helper uses 0.6): a tracker wants recall - a
            # small or three-quarter face still anchors the crop; the
            # median + dead zone absorb the occasional false positive.
            det = cv2.FaceDetectorYN.create(model, "", (w, h), score_threshold=0.45)
            det_cache[(w, h)] = det
        _, faces = det.detect(img)
        if faces is None:
            return []
        return [((float(f[0]) + float(f[2]) / 2) / w, (float(f[1]) + float(f[3]) / 2) / h,
                 float(f[2]) / w, float(f[3]) / h) for f in faces]
    except Exception:
        return []


def _reframe_samples(src, a, b, workdir, tag):
    """Sample [a, b] of src at REFRAME_SAMPLE_FPS (downscaled) and detect
    faces per frame -> [(t_rel, faces)]. Best-effort: [] on failure."""
    try:
        pat = workdir / f"rf_{tag}_%04d.jpg"
        _run(["ffmpeg", "-y", "-ss", f"{a:.2f}", "-to", f"{b:.2f}", "-i", str(src),
              # 960 wide (not 480): a podcast face 15% of the frame tall is
              # ~40px at 480 and gets missed when it turns; ~80px detects
              # reliably. YuNet at 960x540 is still ~40ms/frame.
              "-vf", f"fps={REFRAME_SAMPLE_FPS},scale=960:-2", "-q:v", "5", str(pat)])
        frames = sorted(workdir.glob(f"rf_{tag}_*.jpg"))
        cache = {}
        out = []
        for i, fp in enumerate(frames):
            out.append((i / float(REFRAME_SAMPLE_FPS), _faces_in_image(fp, cache)))
            try:
                fp.unlink()
            except Exception:
                pass
        return out
    except Exception as exc:
        print(f"[assembler] reframe sampling failed for {tag}: {exc!r}")
        return []


def _fade_filters(length, fade_in=0.0, fade_out=0.0):
    """Video + audio fade filter fragments for one encoded part of `length`
    seconds. Each fade is capped at half the part so in+out never overlap.
    Returns (vf_suffix, af) - empty strings when no fade applies. Pure."""
    fi = max(0.0, min(float(fade_in or 0), length / 2.0))
    fo = max(0.0, min(float(fade_out or 0), length / 2.0))
    vf, af = [], []
    if fi > 0:
        vf.append(f"fade=t=in:st=0:d={fi:.2f}")
        af.append(f"afade=t=in:st=0:d={fi:.2f}")
    if fo > 0:
        st = max(0.0, length - fo)
        vf.append(f"fade=t=out:st={st:.2f}:d={fo:.2f}")
        af.append(f"afade=t=out:st={st:.2f}:d={fo:.2f}")
    return ("," + ",".join(vf)) if vf else "", ",".join(af)


def _watermark_filter(wm, canvas_w, in_label="[0:v]", out_label="[vout]"):
    """filter_complex fragment overlaying input 1 (the logo) on `in_label`:
    scaled to `w` x canvas width, alpha multiplied by `opacity`, placed at the
    normalized top-left (x, y) and clamped inside the frame with overlay's
    own W/H/w/h expressions (the logo's height is unknown until scaled).
    Pure - the numeric clamps mirror the route's validation."""
    x = max(0.0, min(1.0, float(wm.get("x", 0.02))))
    y = max(0.0, min(1.0, float(wm.get("y", 0.02))))
    w = max(0.03, min(0.8, float(wm.get("w", 0.18))))
    op = max(0.1, min(1.0, float(wm.get("opacity", 0.85))))
    px = max(8, int(round(w * canvas_w)))
    px -= px % 2
    return (f"[1:v]scale={px}:-1,format=rgba,colorchannelmixer=aa={op:.2f}[wm];"
            f"{in_label}[wm]overlay=x='min(max(0\,{x:.4f}*W)\,W-w)':"
            f"y='min(max(0\,{y:.4f}*H)\,H-h)':format=auto{out_label}")


def _image_ext(path):
    """png / jpg / webp by magic bytes (image2 picks the decoder from the
    file EXTENSION, so an uploaded logo must be renamed to match its bytes)."""
    with open(path, "rb") as fh:
        head = fh.read(16)
    if head.startswith(b"\x89PNG"):
        return "png"
    if head[:3] == b"\xff\xd8\xff":
        return "jpg"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    return None


@app.function(
    image=burn_image,
    cpu=8,
    timeout=900,
    max_containers=2,
    volumes={TMP_DIR: tmp_vol},
    memory=4096,
)
def render_story(upload_keys, segments: list, filename: str = "story.mp4",
                 captions: bool = True, vo_key: str = None,
                 tighten: bool = False, hook_text: str = "",
                 variant: str = "", intro_key: str = None, outro_key: str = None,
                 fade: float = 0.0, wm_key: str = None, wm: dict = None,
                 reframe: str = None) -> dict:
    """Cut the kept moments (in the user's storyboard ORDER) from their
    respective clips, normalize every part onto a common canvas (mixed
    resolutions/orientations scale+pad; uniform fps + audio), and concat.
    `segments` items are [clip_index, start, end]; Phase-1 [start, end] pairs
    are accepted as clip 0.
    Clips mode extras: `tighten` excises silence gaps inside every window
    (word-transcript driven, see _tighten_windows); `hook_text` burns an
    on-screen hook line for the first seconds through the shared ASS
    builder's hook box; `variant` (validated by the route) makes the output
    key `{key}_{variant}_out.mp4` so N clips from ONE source never overwrite
    each other in History.
    Branding (2026-08-16): `intro_key` / `outro_key` are user clips (first
    INTRO_OUTRO_MAX_SECONDS each) normalized onto the same canvas and
    concatenated around the body; `fade` seconds of video+audio fade at the
    very start/end and at every intro|body|outro boundary; `wm_key` + `wm`
    {x, y, w, opacity} overlay a logo on the BODY (intro/outro clips are the
    user's own branding and stay untouched), in the same encode pass as
    the captions. Every branding input degrades to "skipped" on failure -
    the cut always ships.
    `reframe="9:16"` (2026-08-16): landscape sources are cropped to a
    full-height vertical window that FOLLOWS THE SPEAKER - faces sampled at
    3 fps (YuNet), planned by _reframe_plan (group-center / largest face /
    hold / dead zone / rate limit), applied as a piecewise-linear `crop` x
    expression inside each body-part encode; the canvas becomes 9:16 so
    intro/outro/watermark/captions follow. Portrait sources are untouched.
    Sampling failure -> a centered static crop, never a failed render."""
    from pipeline_fns import _record_job
    # "9:16" = speaker-tracked full-height CROP; "fit" = the WHOLE landscape
    # frame kept, centered on a portrait canvas over a blurred, zoomed copy
    # of itself (2026-08-17 - "convert it to portrait, not just cut the sides").
    reframe = reframe if reframe in ("9:16", "fit") else None
    fade = max(0.0, min(3.0, float(fade or 0)))
    wm = wm if isinstance(wm, dict) else None

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

        # Word transcript persisted at analyze time - drives both the
        # in-clip silence tightening and the caption remap. Missing files
        # (expired scratch, >48h) degrade both to "no-op", never a failure.
        clip_segments = []
        for k in upload_keys:
            try:
                wp = _asm_words_path(k)
                clip_segments.append(
                    json.loads(wp.read_text(encoding="utf-8")).get("segments", [])
                    if wp.exists() else [])
            except Exception:
                clip_segments.append([])
        if tighten:
            tightened = _tighten_windows(windows, clip_segments)
            if tightened:
                windows = tightened

        # Canvas = the first KEPT window's clip dimensions; every part is
        # scaled to fit and padded to exactly that frame, at uniform fps and
        # audio params, so the concat-demuxer copy is always safe.
        src_dims = {ci: _probe_dims(sources[ci]) for ci in {w[0] for w in windows}}

        def _is_wide(dims):
            """Wider than 9:16 by a margin - i.e. there is something to reframe."""
            sw, sh = dims
            return sw > int(round(sh * 9 / 16.0)) + 8

        def _crop_dims(ci):
            """(crop_w, crop_h) of a full-height 9:16 window for source ci,
            or None when it is not wider than 9:16 (nothing to reframe)."""
            sw, sh = src_dims[ci]
            want = int(round(sh * 9 / 16.0))
            want -= want % 2
            return (want, sh) if reframe == "9:16" and _is_wide((sw, sh)) else None

        def _fit_dims(dims):
            """Portrait canvas for "fit": width = the source width capped at
            1080 (a 1920x1080 talk becomes 1080x1920, a 1280x720 one
            720x1280), height = 16/9 of it."""
            sw, sh = dims
            w = min(1080, sw)
            w -= w % 2
            h = int(round(w * 16 / 9.0))
            h -= h % 2
            return (w, h)

        # "9:16" is SMART (2026-08-17): two persistent speakers too far apart
        # for one crop (a Zoom side-by-side) -> stacked split-screen on the
        # 1080x1920 canvas; otherwise the speaker-tracked crop. Sampled once
        # per body window (cached) - the FIRST window's decision picks the
        # canvas, later windows follow it (a Zoom interview is consistently
        # a two-shot; a lone different window is scaled onto the canvas).
        first_ci = windows[0][0]
        rf_samples = {}
        rf_split = {}
        if reframe == "9:16":
            for i, (ci, a, b) in enumerate(windows):
                if _is_wide(src_dims[ci]):
                    smp = _reframe_samples(sources[ci], a, b, tmp, f"{i:02d}")
                    rf_samples[i] = smp
                    cwc, _chc = _crop_dims(ci) or (0, 0)
                    rf_split[i] = _split_plan(smp, cwc / float(src_dims[ci][0])) if smp and cwc else None
        split_mode = bool(rf_split.get(0))
        if (reframe == "fit" or split_mode) and _is_wide(src_dims[first_ci]):
            cw, ch = _fit_dims(src_dims[first_ci])
        else:
            cw, ch = _crop_dims(first_ci) or src_dims[first_ci]
        norm = (f"scale={cw}:{ch}:force_original_aspect_ratio=decrease,"
                f"pad={cw}:{ch}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30")

        def _fit_chain():
            """Filter fragment (ends with a comma-less overlay, ready to be
            chained) that keeps the WHOLE frame: a blurred, zoomed-to-cover
            copy fills the canvas, the untouched frame is fitted to the
            canvas width and centered."""
            # Blur at 1/8 scale then upscale: identical look to a full-res
            # gblur at a fraction of the CPU (1080x1920 x 30fps x sigma 32
            # would dominate the encode).
            bw, bh = max(2, (cw // 8) - (cw // 8) % 2), max(2, (ch // 8) - (ch // 8) % 2)
            return (f"split=2[bg][fg];"
                    f"[bg]scale={bw}:{bh}:force_original_aspect_ratio=increase,"
                    f"crop={bw}:{bh},gblur=sigma=4,eq=brightness=-0.06,scale={cw}:{ch}[bgb];"
                    f"[fg]scale={cw}:-2[fgs];"
                    f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2")

        def _pre_for(dims):
            """The reframe pre-filter for a source of `dims` in "fit" mode
            (used for body parts AND landscape intro/outro so the whole
            output shares one look); crop mode is per-window (needs the
            face plan) and handled in the loop."""
            if reframe == "fit" and _is_wide(dims):
                return _fit_chain()
            return ""

        # A part from a silent clip must still carry an audio STREAM (silence),
        # or the concat mixes audio-ful and audio-less parts and breaks - and
        # the VO ducking has no [0:a] to key on.
        def _has_audio(path):
            r = _run(["ffprobe", "-v", "error", "-select_streams", "a",
                      "-show_entries", "stream=codec_type", "-of", "csv=p=0",
                      str(path)], text=True)
            return bool(r.stdout.strip())

        # Watermark logo (body only): uploaded through the same chunk/R2 path
        # as clips (its cached copy is `{key}_src.mp4` whatever the bytes),
        # renamed by magic bytes because image2 picks the decoder from the
        # extension. Applied INSIDE every body-part encode (one input, no
        # extra pass); any problem = no watermark, never a failed render.
        wm_path = None
        if wm_key and wm:
            try:
                raw = _resolve_source(wm_key, tmp)
                ext = _image_ext(raw)
                if ext:
                    import shutil as _sh
                    wm_path = tmp / f"wm.{ext}"
                    _sh.copy(raw, wm_path)
                else:
                    print("[assembler] watermark is not png/jpg/webp - skipped")
            except Exception as exc:
                print(f"[assembler] watermark unavailable - skipped: {exc!r}")

        def _encode_part(src, out, a, b, has_audio, fade_in=0.0, fade_out=0.0, logo=None, pre=""):
            """One normalized, uniformly-encoded part of [a, b] from src.
            Inputs: 0 = src, 1 = logo (if any), last = anullsrc (if silent).
            `pre` = filter(s) applied BEFORE the canvas normalize (the
            reframe crop)."""
            length = b - a
            vf_fade, af = _fade_filters(length, fade_in, fade_out)
            # `pre` may be a labeled sub-graph (the "fit" split/overlay), so
            # every part goes through -filter_complex; a linear pre (crop)
            # simply chains.
            norm_here = (pre + "," if pre else "") + norm
            cmd = ["ffmpeg", "-y", "-ss", f"{a:.2f}", "-to", f"{b:.2f}", "-i", str(src)]
            n_in = 1
            if logo:
                cmd += ["-i", str(logo)]
                n_in += 1
            if not has_audio:
                cmd += ["-f", "lavfi", "-t", f"{length:.2f}", "-i", "anullsrc=r=48000:cl=stereo"]
                a_map = f"{n_in}:a"
                n_in += 1
            else:
                a_map = "0:a"
            if logo:
                graph = f"[0:v]{norm_here}{vf_fade}[v];" + _watermark_filter(wm, cw, "[v]", "[vout]")
                cmd += ["-filter_complex", graph, "-map", "[vout]", "-map", a_map]
            else:
                cmd += ["-filter_complex", f"[0:v]{norm_here}{vf_fade}[vout]", "-map", "[vout]", "-map", a_map]
            if not has_audio:
                cmd += ["-shortest"]
            if af:
                cmd += ["-af", af]
            cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                    "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
                    "-movflags", "+faststart", str(out)]
            _run(cmd)
            return out

        clip_has_audio = {}
        parts = []
        last = len(windows) - 1
        for i, (ci, a, b) in enumerate(windows):
            if ci not in clip_has_audio:
                clip_has_audio[ci] = _has_audio(sources[ci])
            pre = _pre_for(src_dims[ci])
            cd = _crop_dims(ci)
            if cd:
                crop_w, crop_h = cd
                sw, sh = src_dims[ci]
                samples = rf_samples.get(i) or []
                if split_mode and rf_split.get(i):
                    pre = _split_chain(rf_split[i], sw, sh, cw, ch)
                elif split_mode:
                    # Canvas is the split canvas but this window has one
                    # speaker: track them and let `norm` fit the crop onto it.
                    plan = _reframe_plan(samples, crop_w / float(sw)) if samples else [(0.0, 0.5)]
                    pre = f"crop={crop_w}:{crop_h}:x='{_crop_x_expr(plan, sw, crop_w)}':y=0"
                else:
                    plan = _reframe_plan(samples, crop_w / float(sw)) if samples else [(0.0, 0.5)]
                    pre = f"crop={crop_w}:{crop_h}:x='{_crop_x_expr(plan, sw, crop_w)}':y=0"
            parts.append(_encode_part(sources[ci], tmp / f"part_{i:02d}.mp4", a, b,
                                      clip_has_audio[ci],
                                      fade if i == 0 else 0.0,
                                      fade if i == last else 0.0,
                                      logo=wm_path, pre=pre))

        concat_list = tmp / "list.txt"
        concat_list.write_text("".join(f"file '{p}'\n" for p in parts))
        suffix = f"_{variant}" if variant else ""
        out_key = f"{upload_keys[0]}{suffix}_out.mp4"
        cut_key = f"{upload_keys[0]}{suffix}_cut.mp4"
        out_path = Path(TMP_DIR) / out_key
        joined = tmp / "joined.mp4"
        _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list),
              "-c", "copy", "-movflags", "+faststart", str(joined)])
        body_len = sum(b - a for _ci, a, b in windows)

        # ── Intro / outro: the user's own clips around the body, normalized
        # onto the same canvas (first INTRO_OUTRO_MAX_SECONDS each), with
        # the fade at every boundary, NO watermark (they are the user's own
        # branding). Concat is a stream copy - every part shares the body's
        # codec params. A missing/broken clip is skipped.
        wrap = []
        for tag, k in (("intro", intro_key), ("outro", outro_key)):
            if not k:
                continue
            try:
                src = _resolve_source(k, tmp)
                dur = min(_probe_duration(src), INTRO_OUTRO_MAX_SECONDS)
                if dur < 0.5:
                    continue
                wrap.append((tag, _encode_part(src, tmp / f"{tag}.mp4", 0.0, dur,
                                               _has_audio(src), fade, fade,
                                               pre=_pre_for(_probe_dims(src)))))
            except Exception as exc:
                print(f"[assembler] {tag} unavailable - skipped: {exc!r}")
        composed = joined
        intro_len = 0.0
        if wrap:
            order = ([p for t, p in wrap if t == "intro"] + [joined]
                     + [p for t, p in wrap if t == "outro"])
            wrap_list = tmp / "wrap.txt"
            wrap_list.write_text("".join(f"file '{p}'\n" for p in order))
            wrapped = tmp / "wrapped.mp4"
            try:
                _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(wrap_list),
                      "-c", "copy", "-movflags", "+faststart", str(wrapped)])
                composed = wrapped
                intro_len = sum(_probe_duration(p) for t, p in wrap if t == "intro")
            except Exception as exc:
                print(f"[assembler] intro/outro concat failed - shipping body only: {exc!r}")

        # ── Voice-over (Phase 3): duck the original audio under the narration
        # and mix. Video stream is COPIED - this pass costs audio-encode only.
        # Filtergraph validated on synthetic media 2026-08-13: apad keeps a
        # shorter VO from truncating the cut (duration=first). A missing/
        # broken VO degrades to the un-narrated cut, never a failed render.
        # With an intro in front, the narration is delayed so it still
        # starts on the body.
        if vo_key:
            try:
                vo_src = _resolve_source(vo_key, tmp)
                intro_ms = int(round(intro_len * 1000))
                delay = f"adelay={intro_ms}|{intro_ms}," if intro_ms > 0 else ""
                mixed_path = tmp / "mixed.mp4"
                _run(["ffmpeg", "-y", "-i", str(composed), "-i", str(vo_src),
                      "-filter_complex",
                      f"[1:a]aformat=sample_rates=48000:channel_layouts=stereo,{delay}apad[vo];"
                      "[0:a][vo]sidechaincompress=threshold=0.05:ratio=8:attack=20:release=400[bg];"
                      "[bg][vo]amix=inputs=2:duration=first:normalize=0[aout]",
                      "-map", "0:v", "-map", "[aout]",
                      "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                      "-movflags", "+faststart", str(mixed_path)])
                composed = mixed_path
            except Exception as exc:
                print(f"[assembler] VO mix failed - shipping without narration: {exc!r}")

        # `composed` = everything except captions/hook: intro/outro, fades,
        # watermark, narration. It is ALSO the re-edit source: saved as
        # `_cut.mp4` and pinned by the job record so History's pencil can
        # reopen this clip in the main editor (restyle captions, zoom,
        # B-roll, hooks) and burn again - "export to the pipeline".
        import shutil as _shutil
        cut_path = Path(TMP_DIR) / cut_key
        _shutil.copy(composed, cut_path)

        # ── Captions + hook LAST, on the full timeline (events shifted by the
        # intro length), through the SHARED build_caption_ass. Missing
        # transcript files (expired scratch, >48h) degrade to no captions.
        events, hook = [], None
        hook_text = str(hook_text or "").strip()[:120]
        if captions:
            try:
                events = _captions_for_windows(windows, clip_segments)
            except Exception as exc:
                print(f"[assembler] caption remap failed - shipping without: {exc!r}")
                events = []
        if intro_len > 0 and events:
            events = [{"start": round(e["start"] + intro_len, 3), "end": round(e["end"] + intro_len, 3),
                       "text": e["text"],
                       "words": [[round(w[0] + intro_len, 3), round(w[1] + intro_len, 3), w[2]]
                                 for w in (e.get("words") or [])]}
                      for e in events]
        if hook_text:
            hook = {"text": hook_text, "start_seconds": round(intro_len + 0.2, 2),
                    "duration_seconds": max(1.5, min(4.5, body_len - 0.5))}
            # Split-screen: the default top-10% box lands on the upper
            # speaker's face - park it on the seam between the two tiles.
            if split_mode:
                hook["vertical_position"] = 46
        burned = False
        if events or hook:
            try:
                from pipeline_fns import build_caption_ass
                ass_str = build_caption_ass(
                    cw, ch, "Heebo", 48,
                    max(25, cw // 14), int(0.08 * ch),
                    events, hook)
                ass_path = tmp / "captions.ass"
                ass_path.write_text(ass_str, encoding="utf-8")
                esc = str(ass_path).replace(":", r"\:")
                _run(["ffmpeg", "-y", "-i", str(composed),
                      "-vf", f"subtitles='{esc}'",
                      "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                      "-pix_fmt", "yuv420p", "-c:a", "copy",
                      "-movflags", "+faststart", str(out_path)])
                burned = True
            except Exception as exc:
                print(f"[assembler] caption burn failed - shipping clean cut: {exc!r}")
        if not burned:
            _shutil.copy(composed, out_path)
        tmp_vol.commit()

        # Standard History record + re-edit state (the same shape a burn
        # records - see docs/outputs.md): download / share / thumbnail /
        # retention / the pencil all come along. notify=False - the page
        # shows the result itself.
        _record_job(out_key, filename or "story", out_path, notify=False, edit_state={
            "src_key":       cut_key,
            "captions":      events,
            "hook":          hook or {},
            "broll":         [],
            "font":          "Heebo",
            "font_size":     48,
            "margin_v_pct":  0.08,
            "caption_style": {},
        })
        total = body_len
        return {"video_key": out_key, "duration": round(total, 1)}
