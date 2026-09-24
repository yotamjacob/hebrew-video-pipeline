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

# ── Auto-reframe 16:9 -> 9:16 (2026-08-16; shot-aware v2 2026-09-24) ─────────
REFRAME_SAMPLE_FPS = 6          # face-branch sample rate (a _reframe_samples param; 3 still works)
REFRAME_MAX_KEYFRAMES = 30
REFRAME_CANVAS = (1080, 1920)   # "9:16" output is ALWAYS this canvas
REFRAME_SCENE_THR = 0.30        # ffmpeg scene score that counts as a camera cut
REFRAME_MIN_SHOT = 0.5          # s - cuts closer than this are one event (flash / dissolve)
REFRAME_ZOOM_K = 3.8            # crop height = face height x k -> the face is ~26% of the output
REFRAME_FACE_TOP = 0.40         # face center 40% of the crop height from the top
REFRAME_HOLD = 1.5              # s - minimum hold before a re-aim / speaker switch
REFRAME_SPEAKER_HYST = 0.15     # a challenger must lead the CURRENT speaker by this normalized margin
REFRAME_EXCURSION = 3.0         # s - an A->B->A speaker span shorter than this is merged back into A
REFRAME_DRIFT_SECONDS = 3.0     # s of steady drift before the crop may pan (ease)
REFRAME_MIN_CONFIDENCE = 0.45   # a window below this renders as "fit"
REFRAME_MAX_SEGMENTS = 40       # per window (split=N sub-graph size)
REFRAME_DIM_LISTENER = True     # split tiles: dim the tile of whoever is NOT speaking
REFRAME_PANE_TOL = 5.0          # max per-channel std of a dark "chrome" line (pane edge / bar / divider)
REFRAME_PANE_DARK = 16.0        # max mean luma of a dark chrome line
REFRAME_PANE_WIDTH = 320        # analysis width of the pane-finder frames


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
    at 0.3 / 0.7); returns {"left": (cx, cy), "right": (cx, cy), "left_fh",
    "right_fh", "left_fw", "right_fw"} medians (face height / width
    fractions: fh sizes the tile zoom, fw bounds the pane finder's scan)
    when BOTH groups are present in >= `min_presence` of the samples and
    are too far apart to share one tracked crop (separation > 0.9 x
    crop_frac - when they fit, the tracked crop's group-center framing is
    the better picture). Otherwise None (-> tracked crop). Pure."""
    pts = [(f[0], f[1], f[3], f[2]) for _t, faces in samples for f in faces]
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
            "right": (_median([p[0] for p in right]), _median([p[1] for p in right])),
            "left_fh": _median([p[2] for p in left]),
            "right_fh": _median([p[2] for p in right]),
            "left_fw": _median([p[3] for p in left]),
            "right_fw": _median([p[3] for p in right])}


def _rf_median(vals):
    """Upper median (same convention as the planners above). Pure."""
    v = sorted(vals)
    return v[len(v) // 2]


def _rf_floor(sh):
    """Crop-height floor (fraction of source height) for a tracked / group
    crop. Upscale guard: a source under 1080 lines is already upscaled onto
    the 1920-tall canvas, so the zoom may not go as deep. Pure."""
    return 0.60 if sh < 1080 else 0.45


def _zoom_box(face_h, cx, cy, sw, sh, k=REFRAME_ZOOM_K, floor=0.45, cap=1.0,
              aspect=9 / 16.0, top=REFRAME_FACE_TOP):
    """Crop rectangle (x, y, w, h) in source pixels sized so a face of height
    `face_h` (fraction of the source height) fills ~1/k of it: h = clamp(
    face_h * k, floor, cap) x sh, w = h x aspect (never wider than the
    source), the face center `top` of the way down, clamped inside the
    frame, even w/h. Pure."""
    h = max(floor, min(cap, float(face_h) * k)) * sh
    h = min(h, float(sh))
    if h * aspect > sw:
        h = sw / aspect
    h = int(round(h))
    h = max(2, h - h % 2)
    w = int(round(h * aspect))
    w = max(2, min(w - w % 2, sw - sw % 2))
    x = int(round(min(max(0.0, cx * sw - w / 2.0), sw - w)))
    y = int(round(min(max(0.0, cy * sh - top * h), sh - h)))
    return (x, y, w, h)


def _group_box(bbox, sw, sh, floor=0.45, margin=0.25, top=REFRAME_FACE_TOP):
    """9:16 crop around a face GROUP: `bbox` = (left, top, right, bottom)
    fractions of the union of the face boxes, grown by `margin` of its own
    size on each side; the crop is the smallest 9:16 box that holds it
    (height floor `floor` x sh), group center `top` of the way down. Pure."""
    l, t, r, b = bbox
    bw, bh = r - l, b - t
    l, r = l - margin * bw, r + margin * bw
    t, b = t - margin * bh, b + margin * bh
    need_h = max((b - t) * sh, (r - l) * sw * 16 / 9.0)
    return _zoom_box(need_h / float(sh), (l + r) / 2.0, (t + b) / 2.0, sw, sh,
                     k=1.0, floor=floor, top=top)


def _center_spec(sw, sh):
    """Full-height centered 9:16 crop (no faces / sampling failure). Pure."""
    h = sh - sh % 2
    w = int(round(h * 9 / 16.0))
    w -= w % 2
    return {"kind": "crop", "role": "center", "box": ((sw - w) // 2, 0, w, h)}


def _shot_cuts(scores, length, thr=REFRAME_SCENE_THR, min_shot=REFRAME_MIN_SHOT):
    """Camera-cut times from per-frame ffmpeg scene scores [(t, score)] (t =
    pts of the FIRST frame of the new shot, part-relative). A score >= thr
    is a cut; cuts closer than `min_shot` to each other or to either end of
    the window collapse to the strongest (a flash / a dissolve is one
    event). The returned times are the exact frame pts. Pure."""
    kept = []
    for t, s in sorted((float(t), float(s)) for t, s in scores):
        if s < thr or t < min_shot or t > length - min_shot:
            continue
        if kept and t - kept[-1][0] < min_shot:
            if s > kept[-1][1]:
                kept[-1] = (t, s)
            continue
        kept.append((t, s))
    return [t for t, _s in kept]


def _face_tracks(samples, gate=0.08, forget=2.0):
    """Stable ids for faces across the samples of ONE shot: greedy
    nearest-center matching (gate = max(`gate`, 0.6 x face width)); a track
    unseen for `forget` s is retired. Returns (ids, n_tracks) with
    ids[i][j] = the track of face j in sample i. Pure."""
    tracks = []                     # [cx, cy, w, last_t]
    ids = []
    for t, faces in samples:
        row = [None] * len(faces)
        pairs = []
        for j, f in enumerate(faces):
            for k, tr in enumerate(tracks):
                if t - tr[3] > forget:
                    continue
                d = ((f[0] - tr[0]) ** 2 + (f[1] - tr[1]) ** 2) ** 0.5
                if d <= max(gate, 0.6 * max(f[2], tr[2])):
                    pairs.append((d, j, k))
        used_j, used_k = set(), set()
        for _d, j, k in sorted(pairs):
            if j in used_j or k in used_k:
                continue
            row[j] = k
            used_j.add(j)
            used_k.add(k)
        for j, f in enumerate(faces):
            if row[j] is None:
                tracks.append([f[0], f[1], f[2], t])
                row[j] = len(tracks) - 1
            else:
                tracks[row[j]][:] = [f[0], f[1], f[2], t]
        ids.append(row)
    return ids, len(tracks)


def _mouth_score(face):
    """Mouth motion of one detected face = its lower-third change (face[4],
    contrast-normalized and shift-registered by the sampler). The upper
    third (face[5]) is recorded for diagnostics but NOT subtracted: on the
    first real podcast (2026-09-24, hand-labeled filmstrips) blinks and
    glasses glints made "lower - upper" pick the wrong speaker, while the
    lower third alone was right 89-98% of seconds. None when unknown. Pure."""
    if len(face) < 5 or face[4] is None:
        return None
    return max(0.0, float(face[4]))


def _speaker_timeline(samples, ids, cands, length, win=1.0, hold=REFRAME_HOLD,
                      confirm=0.3, min_act=0.15, hyst=REFRAME_SPEAKER_HYST,
                      excursion=REFRAME_EXCURSION):
    """Active speaker among the candidate tracks of ONE shot, no diarization:
    per track, the mouth score averaged over a centered `win` s window.
    The first speaker is the first track to reach `min_act` (0.15 on the
    sampler's normalized scale: a still listener sits ~0.10, a talker
    ~0.14-0.28 - calibrated on the 2026-09-24 podcast). A challenger then
    needs HYSTERESIS: its 1 s score must beat the CURRENT speaker's by a
    normalized lead (c - cur) / (c + cur) >= `hyst` and reach `min_act`,
    hold that for `confirm` s, and switches are >= `hold` s apart (a switch
    wanted sooner lands at last + hold if the lead lasts). A lead that
    fades first - a backchannel "mm", a laugh - is counted in `suppressed`
    and never flips the tile. Offline look-ahead: an A -> B -> A span of B
    shorter than `excursion` s (a laugh / interjection over ongoing speech
    - the 2026-09-24 podcast had a 2 s one at a 0.24 lead) is merged back
    into A and also counted in `suppressed`. Returns {"spans": [(t0, t1, track)],
    "switches", "margin" (mean normalized lead (top - 2nd) / (top + 2nd)
    over multi-face samples, None if none), "suppressed"}. Pure."""
    times = [float(t) for t, _f in samples]
    n = len(times)
    cands = list(cands)
    score = {k: [None] * n for k in cands}
    for i, (_t, faces) in enumerate(samples):
        for j, f in enumerate(faces):
            k = ids[i][j]
            if k in score:
                m = _mouth_score(f)
                if m is not None:
                    score[k][i] = m if score[k][i] is None else max(score[k][i], m)
    avg = {k: [] for k in cands}
    for k in cands:
        for i in range(n):
            vals = [score[k][m] for m in range(n)
                    if score[k][m] is not None and abs(times[m] - times[i]) <= win / 2.0 + 1e-9]
            avg[k].append(sum(vals) / len(vals) if vals else None)
    margins = []
    for i in range(n):
        ranked = sorted((avg[k][i] for k in cands if avg[k][i] is not None), reverse=True)
        if len(ranked) > 1:
            margins.append((ranked[0] - ranked[1]) / (ranked[0] + ranked[1] + 1e-6))
    cur = None
    for i in range(n):
        vals = {k: avg[k][i] for k in cands if avg[k][i] is not None}
        if vals and max(vals.values()) >= min_act:
            cur = max(vals, key=vals.get)
            break
    if cur is None and cands:
        cur = max(cands, key=lambda k: sum(v for v in score[k] if v is not None))
    starts = [(0.0, cur)]
    last = 0.0
    pending = None
    suppressed = 0
    for i, t in enumerate(times):
        vals = {k: avg[k][i] for k in cands if avg[k][i] is not None}
        if not vals:
            continue
        kt = max(vals, key=vals.get)
        top, c = vals[kt], vals.get(cur, 0.0)
        wins = (kt != cur and top >= min_act
                and (top - c) / (top + c + 1e-6) >= hyst - 1e-9)
        if not wins:
            if pending is not None:
                suppressed += 1
            pending = None
            continue
        if pending is None or pending[0] != kt:
            if pending is not None:
                suppressed += 1
            pending = (kt, t)
        at = max(pending[1], last + hold)
        if t - pending[1] >= confirm - 1e-9 and t >= at - 1e-9 and at < length:
            starts.append((at, kt))
            cur, last, pending = kt, at, None
    if pending is not None:
        suppressed += 1
    spans = [(s, e, k) for (s, k), e in zip(starts, [x[0] for x in starts[1:]] + [float(length)])]
    merged = True
    while merged and len(spans) >= 3:
        merged = False
        for j in range(1, len(spans) - 1):
            a0, _a1, ka = spans[j - 1]
            b0, b1, _kb = spans[j]
            if spans[j + 1][2] == ka and b1 - b0 < excursion:
                spans[j - 1:j + 2] = [(a0, spans[j + 1][1], ka)]
                suppressed += 1
                merged = True
                break
    return {"spans": spans, "switches": len(spans) - 1,
            "margin": round(sum(margins) / len(margins), 3) if margins else None,
            "suppressed": suppressed}


def _drift_runs(times, xs, tol, min_len=REFRAME_DRIFT_SECONDS, min_speed=0.006):
    """Spans where the target moves STEADILY one way (a walking / swaying
    subject - the yoga case) for >= `min_len` s with a net displacement >=
    `tol`: median-3 smoothed, a step counts as motion at >= `min_speed`
    W/s, a reversal or a > 1 s pause ends the run, and >= 60% of the run's
    steps must be motion. [(t0, t1)]. Pure."""
    n = len(xs)
    if n < 3:
        return []
    sm = [_rf_median(xs[max(0, i - 1):i + 2]) for i in range(n)]
    runs = []

    def _close(a, b, moving):
        if (b > a and times[b] - times[a] >= min_len - 1e-9
                and abs(sm[b] - sm[a]) >= tol and moving >= 0.6 * (b - a)):
            runs.append((times[a], times[b]))
    s = e = None
    direction = moving = 0
    for i in range(1, n):
        v = (sm[i] - sm[i - 1]) / max(1e-3, times[i] - times[i - 1])
        sg = 0 if abs(v) < min_speed else (1 if v > 0 else -1)
        if sg == 0:
            if s is not None and times[i] - times[e] > 1.0:
                _close(s, e, moving)
                s = e = None
                direction = moving = 0
            continue
        if s is None or sg != direction:
            if s is not None:
                _close(s, e, moving)
            s, e, direction, moving = i - 1, i, sg, 1
        else:
            e, moving = i, moving + 1
    if s is not None:
        _close(s, e, moving)
    return runs


def _hold_runs(pts, tol, hold=REFRAME_HOLD):
    """Static framing inside a shot: pts [(t, cx, ...)] are split into runs
    that each hold ONE crop position. The anchor is the median of the first
    `hold` s; a run ends only when the target stays outside `tol` of the
    anchor for MORE than `hold` s - then the next run (a hard re-aim, no
    pan) starts where it left the band. Returns [[pts]]. Pure."""
    if not pts:
        return []
    runs, cur, out = [], [], []
    anchor = _rf_median([p[1] for p in pts if p[0] < pts[0][0] + hold])
    for p in pts:
        if abs(p[1] - anchor) <= tol:
            cur.extend(out)
            out = []
            cur.append(p)
            continue
        out.append(p)
        if out[-1][0] - out[0][0] > hold:
            if cur:
                runs.append(cur)
            cur, out = out, []
            anchor = _rf_median([q[1] for q in cur])
    cur.extend(out)
    if cur:
        runs.append(cur)
    return runs


def _track_pieces(pts, start, sw, sh, role, hold=REFRAME_HOLD):
    """Pieces [(t, spec)] framing ONE followed face (pts [(t, cx, cy, fh,
    fw)], shot-relative) from `start`: static zoomed crops (_hold_runs,
    hard re-aims) except steady-drift spans (_drift_runs), which ease with
    the original _reframe_plan at a fixed zoom. Pure."""
    if not pts:
        return []
    floor = _rf_floor(sh)
    box0 = _zoom_box(_rf_median([p[3] for p in pts]), 0.5, 0.5, sw, sh, floor=floor)
    tol = 0.2 * box0[2] / float(sw)
    runs = _drift_runs([p[0] for p in pts], [p[1] for p in pts], tol)

    def _label(p):
        return next((ri for ri, (r0, r1) in enumerate(runs) if r0 <= p[0] <= r1), None)
    groups = []
    for p in pts:
        lab = _label(p)
        if groups and groups[-1][0] == lab:
            groups[-1][1].append(p)
        else:
            groups.append((lab, [p]))
    pieces = []
    for lab, g in groups:
        at = start if not pieces else g[0][0]
        if lab is None:
            for ri, run in enumerate(_hold_runs(g, tol, hold)):
                box = _zoom_box(_rf_median([p[3] for p in run]), _rf_median([p[1] for p in run]),
                                _rf_median([p[2] for p in run]), sw, sh, floor=floor)
                pieces.append((at if ri == 0 else run[0][0], {"kind": "crop", "role": role, "box": box}))
        else:
            box = _zoom_box(_rf_median([p[3] for p in g]), _rf_median([p[1] for p in g]),
                            _rf_median([p[2] for p in g]), sw, sh, floor=floor)
            kf = _reframe_plan([(p[0] - at, [(p[1], p[2], p[4], p[3])]) for p in g],
                               box[2] / float(sw))
            if kf and kf[0][0] > 0:
                kf = [(0.0, kf[0][1])] + kf
            pieces.append((at, {"kind": "ease", "role": role, "box": box, "kf": kf}))
    return pieces


def _plan_shot(samples, length, sw, sh, hold=REFRAME_HOLD, min_presence=0.4):
    """Plan ONE camera shot. samples [(t, faces)] are shot-relative; nothing
    here ever looks across a cut. Returns (pieces [(t, spec)] with t=0 first,
    info {kind, speaker, switches, margin, suppressed}).
      1. two persistent faces too far apart for one crop -> "split": two 9:8
         tiles zoomed on each face, speaker timeline for the tile emphasis;
      2. every face fits in 90% of a full-height crop -> ONE zoomed "group"
         crop (union box + 25% margin);
      3. else a static zoomed crop on the ACTIVE speaker (mouth motion) or
         the only / largest face - hard re-aims, easing only for drift.
    No face at all -> full-height centered crop. Pure."""
    info = {"kind": "center", "speaker": False, "switches": 0, "margin": None, "suppressed": 0}
    if not any(faces for _t, faces in samples):
        return [(0.0, _center_spec(sw, sh))], info
    full_frac = (sh * 9 / 16.0) / sw
    ids, n_tr = _face_tracks(samples)
    pres = [0] * n_tr
    for row in ids:
        for k in set(row):
            pres[k] += 1
    persistent = [k for k in range(n_tr) if pres[k] / float(len(samples)) >= min_presence]

    def _faces_of(k):
        return [(t, f) for (t, faces), row in zip(samples, ids) for f, kk in zip(faces, row) if kk == k]

    def _pts(k, a=0.0, b=None):
        return [(t, f[0], f[1], f[3], f[2]) for t, f in _faces_of(k)
                if a <= t and (b is None or t < b)]
    if len(persistent) >= 2:
        psamples = [(t, [f for f, kk in zip(faces, row) if kk in persistent])
                    for (t, faces), row in zip(samples, ids)]
        sp = _split_plan(psamples, full_frac, min_presence)
        if sp:
            mid = (sp["left"][0] + sp["right"][0]) / 2.0
            side_of = {k: (0 if _rf_median([f[0] for _t, f in _faces_of(k)]) < mid else 1)
                       for k in persistent}
            tl = _speaker_timeline(samples, ids, persistent, length, hold=hold)
            speak = []
            for a, b, k in tl["spans"]:
                if k is None:
                    continue
                if speak and speak[-1][2] == side_of[k]:
                    speak[-1] = (speak[-1][0], b, side_of[k])
                else:
                    speak.append((a, b, side_of[k]))
            # A coin-flip speaker verdict must not dim anyone.
            if tl["margin"] is not None and tl["margin"] < 0.1:
                speak = []
            tile_floor = 0.5 if sh < 1080 else 0.35
            tiles = [_zoom_box(sp[s + "_fh"], sp[s][0], sp[s][1], sw, sh,
                               floor=tile_floor, cap=0.5, aspect=9 / 8.0)
                     for s in ("left", "right")]
            info.update(kind="split", switches=max(0, len(speak) - 1),
                        margin=tl["margin"], suppressed=tl["suppressed"])
            faces = [(sp[s][0], sp[s][1], sp[s + "_fw"], sp[s + "_fh"]) for s in ("left", "right")]
            return [(0.0, {"kind": "split", "role": "split", "tiles": tiles, "speak": speak,
                           "faces": faces, "panes": [None, None], "fill": [False, False]})], info
        boxes = [(min(f[0] - f[2] / 2.0 for f in fs), min(f[1] - f[3] / 2.0 for f in fs),
                  max(f[0] + f[2] / 2.0 for f in fs), max(f[1] + f[3] / 2.0 for f in fs))
                 for _t, fs in psamples if len(fs) >= 2]
        if boxes and _rf_median([b[2] - b[0] for b in boxes]) <= 0.9 * full_frac:
            bbox = tuple(_rf_median([b[i] for b in boxes]) for i in range(4))
            info["kind"] = "group"
            return [(0.0, {"kind": "crop", "role": "group",
                           "box": _group_box(bbox, sw, sh, _rf_floor(sh))})], info
        tl = _speaker_timeline(samples, ids, persistent, length, hold=hold)
        pieces = []
        for a, b, k in tl["spans"]:
            got = _track_pieces(_pts(k, a, b), a, sw, sh, "speaker", hold)
            if not got:
                got = [(a, dict(pieces[-1][1]) if pieces else _center_spec(sw, sh))]
                if got[0][1].get("kind") == "ease":
                    got = [(a, {"kind": "crop", "role": "speaker", "box": got[0][1]["box"]})]
            pieces += got
        info.update(kind="speaker", speaker=True, switches=tl["switches"],
                    margin=tl["margin"], suppressed=tl["suppressed"])
        return pieces, info
    # One (or no) persistent face: follow it where it is detected and the
    # LARGEST face elsewhere - a subject that jumps position becomes a new
    # track, and the old one must not frame empty space for the rest of it.
    pts = []
    for (t, faces), row in zip(samples, ids):
        if not faces:
            continue
        mine = [f for f, kk in zip(faces, row) if kk in persistent]
        f = max(mine or faces, key=lambda f: f[2] * f[3])
        pts.append((t, f[0], f[1], f[3], f[2]))
    info["kind"] = "track"
    return _track_pieces(pts, 0.0, sw, sh, "track", hold) or [(0.0, _center_spec(sw, sh))], info


def _speaker_stability(margin, suppressed):
    """Stability term of a speaker-TRACKED shot, in [0.7, 1]: 0.7 + 0.3 x
    quality, quality = min(1, margin / 0.3) x (1 - min(0.5, 0.1 x
    suppressed)). Floored at 0.7 on purpose (2026-09-24): an uncertain
    speaker verdict costs a wrong switch, not a broken frame - it must never
    push a window with good coverage and shot length into "fit" on its own
    (the first version, 0.4 + margin, did at margin 0). Pure."""
    q = min(1.0, max(0.0, (margin or 0.0) / 0.3)) * (1.0 - min(0.5, 0.1 * max(0, suppressed)))
    return round(0.7 + 0.3 * q, 3)


def _reframe_confidence(coverage, n_shots, length, stability=1.0):
    """Per-window confidence in [0, 1] = face coverage x shot-rate term
    (average shot >= 2 s = full marks, shorter shots = a frantic edit or
    detector noise, down to 0.5) x speaker stability. Pure."""
    avg = float(length) / max(1, n_shots)
    shot_term = min(1.0, avg / 2.0)
    c = max(0.0, min(1.0, coverage)) * (0.5 + 0.5 * shot_term) * max(0.0, min(1.0, stability))
    return round(c, 3)


def _seg_upscale(seg, sw, sh, cw, ch):
    """Canvas-pixels per source-pixel for one segment (1.0 = native). Pure."""
    if seg["kind"] == "fit":
        return round(cw / float(sw), 2)
    if seg["kind"] == "split":
        return round(max((ch // 2) / float(t[3]) for t in seg["tiles"]), 2)
    return round(ch / float(seg["box"][3]), 2)


def _merge_segments(segs, cuts, cap=REFRAME_MAX_SEGMENTS, min_len=0.2):
    """Absorb segments shorter than `min_len` (and, while over `cap`, the
    shortest) into a neighbour of the SAME shot - a boundary that is a
    camera cut is never removed. The kept neighbour's framing wins; an
    ease/split that grows at its front has its relative times shifted.
    Pure (returns a new list)."""
    segs = [dict(s) for s in segs]
    cutset = set(cuts)
    while len(segs) > 1:
        cand = []
        for j, s in enumerate(segs):
            d = s["t1"] - s["t0"]
            if d >= min_len and len(segs) <= cap:
                continue
            if j > 0 and s["t0"] not in cutset:
                cand.append((d, j, j - 1))
            if j + 1 < len(segs) and s["t1"] not in cutset:
                cand.append((d, j, j + 1))
        if not cand:
            break
        _d, j, n = min(cand)
        s, keep = segs[j], dict(segs[n])
        if n < j:
            keep["t1"] = s["t1"]
        else:
            shift = keep["t0"] - s["t0"]
            keep["t0"], keep["cut"] = s["t0"], s.get("cut", False)
            if keep["kind"] == "ease":
                keep["kf"] = [(0.0, keep["kf"][0][1])] + [(t + shift, c) for t, c in keep["kf"]]
            elif keep["kind"] == "split":
                sp = [(a + shift, b + shift, sd) for a, b, sd in keep.get("speak") or []]
                if sp:
                    sp[0] = (0.0, sp[0][1], sp[0][2])
                keep["speak"] = sp
        lo, hi = min(j, n), max(j, n)
        segs[lo:hi + 1] = [keep]
    return segs


def _plan_window(samples, cuts, length, sw, sh, cw=REFRAME_CANVAS[0], ch=REFRAME_CANVAS[1],
                 hold=REFRAME_HOLD, min_conf=REFRAME_MIN_CONFIDENCE, guard=0.05):
    """Whole-window plan: shots = [0, cuts..., length]; each shot planned on
    its OWN samples (those within `guard` s of a cut are dropped - the fps
    grid may straddle the cut; the first sample of a shot loses its mouth
    diff, which compared against the previous camera) and stitched with
    EXACT shared boundaries. Low confidence -> one "fit" segment. Returns
    (segments, report); a segment is {t0, t1, kind, role, cut, geometry}.
    Pure."""
    length = float(length)
    cuts = [c for c in cuts if 0.0 < c < length]
    bounds = [0.0] + cuts + [length]
    segs, infos = [], []
    for s0, s1 in zip(bounds, bounds[1:]):
        lo = s0 + guard if s0 > 0 else s0
        hi = s1 - guard if s1 < length else s1 + 1e-9
        shot = [(t - s0, list(faces)) for t, faces in samples if lo <= t < hi]
        if shot and s0 > 0:
            shot[0] = (shot[0][0], [tuple(f[:4]) for f in shot[0][1]])
        pieces, info = _plan_shot(shot, s1 - s0, sw, sh, hold=hold)
        infos.append((s1 - s0, info))
        starts = [s0] + [s0 + p[0] for p in pieces[1:]]
        ends = starts[1:] + [s1]
        for i, (st, en, (_r, spec)) in enumerate(zip(starts, ends, pieces)):
            seg = dict(spec)
            seg.update(t0=st, t1=en, cut=(i == 0 and s0 > 0))
            segs.append(seg)
    segs = _merge_segments(segs, cuts)
    coverage = sum(1 for _t, f in samples if f) / float(max(1, len(samples)))
    stab, dur = 0.0, 0.0
    for d, info in infos:
        p = _speaker_stability(info["margin"], info["suppressed"]) if info["speaker"] else 1.0
        stab += p * d
        dur += d
    stability = stab / dur if dur else 1.0
    conf = _reframe_confidence(coverage, len(bounds) - 1, length, stability)
    margins = [info["margin"] for _d, info in infos if info["margin"] is not None]
    report = {"shots": len(bounds) - 1,
              "speaker_switches": sum(info["switches"] for _d, info in infos),
              "suppressed": sum(info["suppressed"] for _d, info in infos),
              "margin": round(sum(margins) / len(margins), 3) if margins else None,
              "stability": round(stability, 3),
              "coverage": round(coverage, 3), "confidence": conf}
    if conf < min_conf or len(segs) > REFRAME_MAX_SEGMENTS:
        report["reason"] = "low_confidence" if conf < min_conf else "too_many_segments"
        segs = [{"kind": "fit", "role": "fit", "t0": 0.0, "t1": length, "cut": False}]
    roles = {s["role"] for s in segs}
    report["mode"] = roles.pop() if len(roles) == 1 else "mixed"
    report["segments"] = _report_segments(segs, sw, sh, cw, ch)
    return segs, report


def _report_segments(segs, sw, sh, cw, ch):
    """reframe_report rows: t0/t1/kind/role/upscale_factor, plus per-tile
    pane_bounds ([l, t, r, b] fractions or None) and tile_fill for splits.
    Pure."""
    out = []
    for s in segs:
        row = {"t0": round(s["t0"], 3), "t1": round(s["t1"], 3), "kind": s["kind"],
               "role": s["role"], "upscale_factor": _seg_upscale(s, sw, sh, cw, ch)}
        if s["kind"] == "split":
            row["pane_bounds"] = [None if pb is None else [round(v, 4) for v in pb]
                                  for pb in (s.get("panes") or [None, None])]
            row["tile_fill"] = list(s.get("fill") or [False, False])
        out.append(row)
    return out


def _reframe_summary(reports, lengths, canvas):
    """The render result's `reframe_report`: per-window reports + a clip
    summary (duration-weighted confidence, the dominant mode, totals, the
    worst upscale). Pure."""
    total = float(sum(lengths)) or 1.0
    by_mode = {}
    for r, d in zip(reports, lengths):
        by_mode[r["mode"]] = by_mode.get(r["mode"], 0.0) + d
    margins = [r["margin"] for r in reports if r.get("margin") is not None]
    ups = [s["upscale_factor"] for r in reports for s in r.get("segments") or []]
    return {"canvas": list(canvas), "windows": reports, "summary": {
        "mode": (max(by_mode, key=by_mode.get) if len(by_mode) == 1 else "mixed") if by_mode else None,
        "shots": sum(r["shots"] for r in reports),
        "speaker_switches": sum(r["speaker_switches"] for r in reports),
        "margin": round(sum(margins) / len(margins), 3) if margins else None,
        "confidence": round(sum(r["confidence"] * d for r, d in zip(reports, lengths)) / total, 3),
        "min_confidence": min((r["confidence"] for r in reports), default=None),
        "max_upscale": max(ups, default=None)}}


def _chrome_line(px, tol=REFRAME_PANE_TOL):
    """True when a line of (r, g, b) pixels is frame CHROME - a pane edge,
    a name / title bar, a gallery divider: flat (max per-channel std <=
    `tol`, compression noise allowed) AND near-black (luma <=
    REFRAME_PANE_DARK), or flat to synthetic precision (std <= 2.5) in any
    colour short of near-white (a blown-out window is content). Calibrated
    on the first real podcast (2026-09-24): its bars measure luma 0-10,
    std 0-4; a black stove pipe running the full pane height measured luma
    27-30, std 7-8 and must NOT read as a pane edge. Pure."""
    n = len(px)
    if n < 4:
        return False
    means = [sum(p[c] for p in px) / float(n) for c in range(3)]
    sd = max((sum((p[c] - means[c]) ** 2 for p in px) / float(n)) ** 0.5 for c in range(3))
    luma = 0.299 * means[0] + 0.587 * means[1] + 0.114 * means[2]
    return (sd <= tol and luma <= REFRAME_PANE_DARK) or (sd <= 2.5 and luma <= 235)


def _find_pane(buf, W, H, face, min_run=2, tol=REFRAME_PANE_TOL):
    """The video pane holding `face` ((cx, cy, w, h) fractions) in an RGB24
    frame `buf` (W x H bytes): scan outward from the face box for the first
    run of >= `min_run` chrome lines - rows over +-2 face widths (up, then
    down), then columns over the whole found pane height (left, right). A
    side with no chrome runs to the frame edge. Returns (l, t, r, b)
    fractions (r, b exclusive) or None when the result cannot hold the
    face (detection failure -> the caller keeps the frame-bounded crop).
    Pure."""
    def px(x, y):
        i = (y * W + x) * 3
        return (buf[i], buf[i + 1], buf[i + 2])
    cx, cy, fw, fh = face[0] * W, face[1] * H, face[2] * W, face[3] * H
    fx0, fx1 = max(0, int(cx - fw / 2)), min(W - 1, int(cx + fw / 2))
    fy0, fy1 = max(0, int(cy - fh / 2)), min(H - 1, int(cy + fh / 2))
    rx0, rx1 = max(0, int(cx - 2 * fw)), min(W, int(cx + 2 * fw))

    def row(y):
        return _chrome_line([px(x, y) for x in range(rx0, rx1)], tol)

    def scan(coords, test):
        run = 0
        for i, c in enumerate(coords):
            if test(c):
                run += 1
                if run >= min_run:
                    return coords[i - run + 1]
            else:
                run = 0
        return None
    up = scan(list(range(fy0 - 1, -1, -1)), row)
    top = up + 1 if up is not None else 0
    dn = scan(list(range(fy1 + 1, H)), row)
    bottom = dn if dn is not None else H

    def col(x):
        return _chrome_line([px(x, y) for y in range(top, bottom, 2)], tol)
    lf = scan(list(range(fx0 - 1, -1, -1)), col)
    left = lf + 1 if lf is not None else 0
    rt = scan(list(range(fx1 + 1, W)), col)
    right = rt if rt is not None else W
    if bottom - top < 1.5 * fh or right - left < 1.5 * fw or not (top <= cy < bottom and left <= cx < right):
        return None
    return (left / float(W), top / float(H), right / float(W), bottom / float(H))


def _fit_tile_in_pane(tile, pane, face, sw, sh, top=REFRAME_FACE_TOP):
    """Constrain a split tile's source crop (x, y, w, h px) to the speaker's
    pane ((l, t, r, b) fractions): re-centered on the face (`top` of the
    way down), clamped inside the pane; a pane smaller than the 9:8 crop
    SHRINKS the crop to it and flags `fill` (the tile's remainder becomes a
    blurred, darkened copy of the crop - never black). None pane -> the
    tile unchanged. Returns ((x, y, w, h), fill). Pure."""
    if pane is None:
        return tuple(tile), False
    x, y, w, h = tile
    l, t = int(round(pane[0] * sw)), int(round(pane[1] * sh))
    r, b = int(round(pane[2] * sw)), int(round(pane[3] * sh))
    fill = False
    if h > b - t:
        h = (b - t) - (b - t) % 2
        fill = True
    if w > r - l:
        w = (r - l) - (r - l) % 2
        fill = True
    fx, fy = face[0] * sw, face[1] * sh
    x = int(round(min(max(float(l), fx - w / 2.0), r - w)))
    y = int(round(min(max(float(t), fy - top * h), b - h)))
    return (x, y, w, h), fill


def _rf_ts(t):
    """trim boundary for a segment edge at frame pts `t`: 0.1 ms EARLIER,
    4 dp - trim keeps start <= pts < end, so the frame AT a cut opens the
    next segment and the previous frame closes this one. Pure."""
    return f"{max(0.0, t - 1e-4):.4f}"


def _fit_graph(cw, ch, p=""):
    """"fit" sub-graph: the WHOLE frame fitted to the canvas width, centered
    over a blurred, zoomed-to-cover copy of itself. Blur at 1/8 scale then
    upscale - the same look as a full-res gblur at a fraction of the CPU.
    `p` prefixes the labels so several can live in one graph. Pure."""
    bw, bh = max(2, (cw // 8) - (cw // 8) % 2), max(2, (ch // 8) - (ch // 8) % 2)
    return (f"split=2[{p}bg][{p}fg];"
            f"[{p}bg]scale={bw}:{bh}:force_original_aspect_ratio=increase,"
            f"crop={bw}:{bh},gblur=sigma=4,eq=brightness=-0.06,scale={cw}:{ch}[{p}bgb];"
            f"[{p}fg]scale={cw}:-2[{p}fgs];"
            f"[{p}bgb][{p}fgs]overlay=(W-w)/2:(H-h)/2")


def _seg_filter(seg, sw, cw, ch, p, dim):
    """Chainable filter for ONE segment, output exactly cw x ch. Split tiles:
    LEFT speaker on top; with `dim`, the tile of whoever is NOT speaking
    gets -6% brightness / 85% saturation over the speaker spans (segment-
    relative t). Pure."""
    kind = seg["kind"]
    if kind == "fit":
        return _fit_graph(cw, ch, p) + ",setsar=1"
    if kind == "split":
        th = ch // 2
        th -= th % 2
        parts = []
        for side, (x, y, w, h) in enumerate(seg["tiles"]):
            eqf = ""
            other = [(a, b) for a, b, sd in (seg.get("speak") or []) if sd != side]
            if dim and other:
                cond = "+".join(f"between(t\\,{a:.3f}\\,{b:.3f})" for a, b in other)
                eqf = (f",eq=eval=frame:brightness='if({cond}\\,-0.06\\,0)'"
                       f":saturation='if({cond}\\,0.85\\,1)'")
            ab = "ab"[side]
            if (seg.get("fill") or [False, False])[side]:
                # Pane smaller than the tile: the crop fitted inside the tile
                # over a blurred, darkened copy of itself (the "fit" look).
                q = f"{p}{ab}"
                bw, bh = max(2, (cw // 8) - (cw // 8) % 2), max(2, (th // 8) - (th // 8) % 2)
                parts.append(f"[{q}]crop={w}:{h}:{x}:{y},split=2[{q}b][{q}f];"
                             f"[{q}b]scale={bw}:{bh}:force_original_aspect_ratio=increase,crop={bw}:{bh},"
                             f"gblur=sigma=4,eq=brightness=-0.06,scale={cw}:{th}[{q}bb];"
                             f"[{q}f]scale={cw}:{th}:force_original_aspect_ratio=decrease:force_divisible_by=2[{q}fs];"
                             f"[{q}bb][{q}fs]overlay=(W-w)/2:(H-h)/2{eqf}[{p}t{ab}]")
            else:
                parts.append(f"[{p}{ab}]crop={w}:{h}:{x}:{y},scale={cw}:{th}{eqf}[{p}t{ab}]")
        return f"split=2[{p}a][{p}b];" + ";".join(parts) + f";[{p}ta][{p}tb]vstack,setsar=1"
    x, y, w, h = seg["box"]
    if kind == "ease":
        return f"crop={w}:{h}:x='{_crop_x_expr(seg['kf'], sw, w)}':y={y},scale={cw}:{ch},setsar=1"
    return f"crop={w}:{h}:{x}:{y},scale={cw}:{ch},setsar=1"


def _segments_chain(segs, sw, sh, cw, ch, length, cuts=(), dim=REFRAME_DIM_LISTENER):
    """The reframe `pre` sub-graph of one body part: every segment trimmed
    out of the part (split=N -> trim -> setpts -> its framing -> cw x ch)
    and concatenated back, so every boundary is a HARD cut (no easing
    across it) and each segment may have its own zoom. Integrity is
    enforced, not assumed: segments must be contiguous (t1 == next t0),
    non-empty, cover [0, length], and every in-window camera cut must BE a
    boundary (-> _rf_ts puts it on the cut's own frame) unless it falls
    inside a framing-free "fit" segment. Raises ValueError otherwise. Pure."""
    if not segs:
        raise ValueError("reframe: no segments")
    if abs(segs[0]["t0"]) > 1e-9 or abs(segs[-1]["t1"] - float(length)) > 1e-6:
        raise ValueError("reframe: segments do not cover the window")
    for s, nx in zip(segs, segs[1:]):
        if s["t1"] != nx["t0"]:
            raise ValueError(f"reframe: gap/overlap at {s['t1']} vs {nx['t0']}")
    for s in segs:
        if not s["t1"] > s["t0"]:
            raise ValueError(f"reframe: empty segment at {s['t0']}")
    bounds = {s["t0"] for s in segs[1:]}
    for c in cuts:
        # A cut inside a "fit" segment is framing-free (the whole frame is
        # shown either way) - every other cut must BE a boundary.
        if (0.0 < c < float(length) and c not in bounds
                and not any(s["kind"] == "fit" and s["t0"] < c < s["t1"] for s in segs)):
            raise ValueError(f"reframe: cut {c} is not a segment boundary")
    n = len(segs)
    # Always end in concat + clone-pad + trim to the exact part length: on
    # the image's ffmpeg 5.1 a vstack/overlay (split / fit) last frame
    # carries no duration and the canvas `fps=30` dropped it (119/120 at 30
    # fps, 299/300 at 24/25 fps - measured 2026-09-24; `fps eof_action=pass`
    # only fixed 30 fps sources). tpad clones the last frame, trim cuts the
    # stream at `length`, so every segment kind at every source rate keeps
    # its frame count.
    tail = (f"concat=n={n}:v=1:a=0,tpad=stop_mode=clone:stop_duration=0.5,"
            f"trim=duration={float(length):.4f}")
    if n == 1:
        return _seg_filter(segs[0], sw, cw, ch, "s0", dim) + "[g0];[g0]" + tail
    chains = []
    for i, s in enumerate(segs):
        if i == 0:
            tr = f"trim=end={_rf_ts(s['t1'])}"
        elif i == n - 1:
            tr = f"trim=start={_rf_ts(s['t0'])}"
        else:
            tr = f"trim=start={_rf_ts(s['t0'])}:end={_rf_ts(s['t1'])}"
        chains.append(f"[r{i}]{tr},setpts=PTS-STARTPTS,{_seg_filter(s, sw, cw, ch, f's{i}', dim)}[g{i}]")
    return (f"split={n}" + "".join(f"[r{i}]" for i in range(n)) + ";"
            + ";".join(chains) + ";"
            + "".join(f"[g{i}]" for i in range(n)) + tail)


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


def _reframe_samples(src, a, b, workdir, tag, fps=REFRAME_SAMPLE_FPS):
    """Sample [a, b] of src for the reframe planner in ONE decode:
    - faces: frames at `fps` (960 px wide - NOT 480: a podcast face ~15% of
      the frame tall is ~40 px at 480 and gets missed when it turns) ->
      YuNet -> per face (cx, cy, w, h, mouth_d, upper_d): the change of the
      lower / upper third of the face box vs the nearest face of the
      previous sample (None when there is none) - each patch 48x24 gray,
      z-normalized (a bright, sharp face must not out-score a dim one) and
      compared at the best of +-2 px shifts (detector box jitter is not
      mouth motion). mouth_d is the active-speaker signal.
    - scene: ffmpeg's scene score of EVERY source frame (192 px branch) ->
      [(pts_time, score)], part-relative like the samples, so a camera cut
      lands on its exact frame instead of the 1/fps grid.
    Returns (samples, scene); ([], []) on failure (-> centered crop)."""
    try:
        import re as _re
        import cv2
        import numpy as np
        fps = max(1, int(fps))
        pat = workdir / f"rf_{tag}_%05d.jpg"
        scene_txt = workdir / f"rf_{tag}_scene.txt"
        _run(["ffmpeg", "-y", "-ss", f"{a:.2f}", "-to", f"{b:.2f}", "-i", str(src),
              "-filter_complex",
              f"[0:v]split=2[fa][fb];[fa]fps={fps},scale=960:-2[fs];"
              f"[fb]scale=192:-2,select='gte(scene\\,0)',metadata=print:file={scene_txt}[fn]",
              "-map", "[fs]", "-q:v", "5", str(pat),
              "-map", "[fn]", "-f", "null", "-"])
        scene, t_cur = [], None
        for line in scene_txt.read_text(errors="ignore").splitlines():
            if line.startswith("frame:"):
                m = _re.search(r"pts_time:([-\d.]+)", line)
                t_cur = float(m.group(1)) if m else None
            elif line.startswith("lavfi.scene_score=") and t_cur is not None:
                scene.append((t_cur, float(line.split("=", 1)[1])))

        def _patches(gray, f):
            H, W = gray.shape[:2]
            cx, cy, w, h = f[0] * W, f[1] * H, f[2] * W, f[3] * H
            xa, xb = max(0, int(cx - w / 3)), min(W, int(cx + w / 3))

            def _p(ya, yb):
                ya, yb = max(0, int(ya)), min(H, int(yb))
                if yb - ya < 4 or xb - xa < 4:
                    return None
                p = cv2.resize(gray[ya:yb, xa:xb], (48, 24),
                               interpolation=cv2.INTER_AREA).astype(np.float32)
                return (p - p.mean()) / (p.std() + 4.0)
            return _p(cy + h / 6, cy + h / 2), _p(cy - h / 2, cy - h / 6)

        def _reg_diff(p, q, r=2):
            core = q[r:q.shape[0] - r, r:q.shape[1] - r]
            return min(float(np.mean(np.abs(p[r + dy:p.shape[0] - r + dy, r + dx:p.shape[1] - r + dx] - core)))
                       for dy in range(-r, r + 1) for dx in range(-r, r + 1))
        frames = sorted(workdir.glob(f"rf_{tag}_*.jpg"))
        cache = {}
        samples, prev = [], []
        for i, fp in enumerate(frames):
            faces = _faces_in_image(fp, cache)
            gray = cv2.imread(str(fp), cv2.IMREAD_GRAYSCALE) if faces else None
            cur, row = [], []
            for f in faces:
                lo, up = _patches(gray, f) if gray is not None else (None, None)
                md = ud = None
                if lo is not None and up is not None and prev:
                    q = min(prev, key=lambda p: (p[0][0] - f[0]) ** 2 + (p[0][1] - f[1]) ** 2)
                    if (q[1] is not None and q[2] is not None
                            and abs(q[0][0] - f[0]) <= 0.5 * max(f[2], q[0][2])
                            and abs(q[0][1] - f[1]) <= 0.5 * max(f[3], q[0][3])):
                        md = _reg_diff(lo, q[1])
                        ud = _reg_diff(up, q[2])
                cur.append((f, lo, up))
                row.append((f[0], f[1], f[2], f[3], md, ud))
            prev = cur
            samples.append((i / float(fps), row))
            try:
                fp.unlink()
            except Exception:
                pass
        return samples, scene
    except Exception as exc:
        print(f"[assembler] reframe sampling failed for {tag}: {exc!r}")
        return [], []


def _refine_split_panes(src, a, segs, sw, sh):
    """Pane-bound every split tile (the black-strip fix, 2026-09-24: a tile
    crop clamped only to the FRAME ran into a branded layout's name bar):
    3 frames per split segment (25 / 50 / 75%) at REFRAME_PANE_WIDTH as raw
    RGB, _find_pane per tile face, per-bound median when >= 2 frames agree,
    then _fit_tile_in_pane. Any failure leaves that tile frame-bounded.
    Mutates the split segments in place."""
    W = REFRAME_PANE_WIDTH
    H = int(round(W * sh / float(sw)))
    H -= H % 2
    for seg in segs:
        if seg["kind"] != "split" or not seg.get("faces"):
            continue
        found = [[], []]
        for q in (0.25, 0.5, 0.75):
            t = a + seg["t0"] + q * (seg["t1"] - seg["t0"])
            try:
                buf = _run(["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", str(src), "-frames:v", "1",
                            "-vf", f"scale={W}:{H}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]).stdout
            except Exception as exc:
                print(f"[assembler] pane frame at {t:.2f}s failed: {exc!r}")
                continue
            if len(buf) < W * H * 3:
                continue
            for side, face in enumerate(seg["faces"]):
                pb = _find_pane(buf, W, H, face)
                if pb:
                    found[side].append(pb)
        panes, tiles, fill = [], [], []
        for side in (0, 1):
            fs = found[side]
            pb = tuple(_rf_median([f[i] for f in fs]) for i in range(4)) if len(fs) >= 2 else None
            tile, fl = _fit_tile_in_pane(seg["tiles"][side], pb, seg["faces"][side], sw, sh)
            panes.append(pb)
            tiles.append(tile)
            fill.append(fl)
        seg["panes"], seg["tiles"], seg["fill"] = panes, tiles, fill


def _reframe_window(src, a, b, sw, sh, workdir, tag, cw=REFRAME_CANVAS[0], ch=REFRAME_CANVAS[1],
                    fps=REFRAME_SAMPLE_FPS, thr=REFRAME_SCENE_THR):
    """Sample + plan one body window (render_story and
    scripts/reframe_preview.py share this). The plan is validated through
    _segments_chain; any failure -> a full-height centered crop, reported
    as mode "center", confidence 0 - never a failed render. Returns
    {segs, cuts, report, samples, scene}."""
    length = b - a
    samples, scene = _reframe_samples(src, a, b, workdir, tag, fps=fps)
    try:
        if not samples:
            raise ValueError("no samples")
        cuts = _shot_cuts(scene, length, thr=thr)
        segs, report = _plan_window(samples, cuts, length, sw, sh, cw, ch)
        if any(sg["kind"] == "split" for sg in segs):
            _refine_split_panes(src, a, segs, sw, sh)
            report["segments"] = _report_segments(segs, sw, sh, cw, ch)
        _segments_chain(segs, sw, sh, cw, ch, length, cuts)
    except Exception as exc:
        print(f"[assembler] reframe plan failed for {tag} - centered crop: {exc!r}")
        cuts = []
        segs = [dict(_center_spec(sw, sh), t0=0.0, t1=length, cut=False)]
        report = {"mode": "center", "shots": 1, "speaker_switches": 0, "suppressed": 0,
                  "margin": None, "stability": None, "coverage": 0.0, "confidence": 0.0,
                  "reason": repr(exc)[:160],
                  "segments": [{"t0": 0.0, "t1": round(length, 3), "kind": "crop", "role": "center",
                                "upscale_factor": _seg_upscale(segs[0], sw, sh, cw, ch)}]}
    report["fps"] = fps
    return {"segs": segs, "cuts": cuts, "report": report, "samples": samples, "scene": scene}


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
    `reframe="9:16"` (2026-08-16, shot-aware v2 2026-09-24): the canvas is
    ALWAYS 1080x1920; each landscape body window is split at its camera
    cuts and every shot is framed on its own (_plan_window: split-screen /
    zoomed group / static zoomed crop on the active speaker, hard re-aims,
    easing only for drifting subjects, "fit" below the confidence floor),
    rendered by _segments_chain inside the part encode. The result carries
    `reframe_report` (per window: mode, shots, speaker switches, margin,
    confidence, per-segment upscale). Sampling failure -> a centered
    full-height crop, never a failed render."""
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

        # "9:16" (shot-aware v2, 2026-09-24): ALWAYS a 1080x1920 canvas; every
        # landscape body window is sampled once (faces + per-frame scene
        # score) and planned shot by shot (_reframe_window) - split-screen,
        # zoomed group / speaker crops, hard cuts at camera cuts, "fit" when
        # the window's confidence is low. Portrait windows are scaled onto
        # the canvas by `norm`. "fit" mode keeps its source-width canvas.
        first_ci = windows[0][0]
        rf_plans = {}
        if reframe == "9:16":
            cw, ch = REFRAME_CANVAS
            for i, (ci, a, b) in enumerate(windows):
                if _is_wide(src_dims[ci]):
                    sw, sh = src_dims[ci]
                    rf_plans[i] = _reframe_window(sources[ci], a, b, sw, sh, tmp, f"{i:02d}", cw, ch)
        elif reframe == "fit" and _is_wide(src_dims[first_ci]):
            cw, ch = _fit_dims(src_dims[first_ci])
        else:
            cw, ch = src_dims[first_ci]
        norm = (f"scale={cw}:{ch}:force_original_aspect_ratio=decrease,"
                f"pad={cw}:{ch}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30")

        def _pre_for(dims):
            """The reframe pre-filter for a source of `dims` in "fit" mode
            (used for body parts AND landscape intro/outro so the whole
            output shares one look); crop mode is per-window (needs the
            face plan) and handled in the loop."""
            if reframe == "fit" and _is_wide(dims):
                return _fit_graph(cw, ch)
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
            if i in rf_plans:
                sw, sh = src_dims[ci]
                pl = rf_plans[i]
                try:
                    pre = _segments_chain(pl["segs"], sw, sh, cw, ch, b - a, pl["cuts"])
                except Exception as exc:
                    print(f"[assembler] reframe chain rejected for window {i} - centered crop: {exc!r}")
                    pre = _segments_chain([dict(_center_spec(sw, sh), t0=0.0, t1=b - a)], sw, sh, cw, ch, b - a)
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
            # speaker's face - park it on the seam between the two tiles
            # when a split segment is on screen while the hook shows.
            h0 = 0.2
            h1 = h0 + hook["duration_seconds"]
            off = 0.0
            on_split = False
            for i, (_ci, a, b) in enumerate(windows):
                for sg in (rf_plans.get(i) or {}).get("segs") or []:
                    if sg["kind"] == "split" and off + sg["t0"] < h1 and off + sg["t1"] > h0:
                        on_split = True
                off += b - a
            if on_split:
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
        result = {"video_key": out_key, "duration": round(total, 1)}
        if reframe == "9:16":
            result["reframe_report"] = _reframe_summary(
                [dict(rf_plans[i]["report"], window=i) for i in sorted(rf_plans)],
                [b - a for i, (_ci, a, b) in enumerate(windows) if i in rf_plans], (cw, ch))
            print(f"[assembler] reframe_report {out_key}: {json.dumps(result['reframe_report'])[:4000]}")
        return result
