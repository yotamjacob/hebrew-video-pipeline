"""
Core video pipeline Modal functions: process_video (GPU transcribe/cut),
burn_captions_fn (ffmpeg + libass caption/hook/B-roll burn worker).
"""

import modal

from pipeline_core import (
    app, burn_image, light_image, model_volume, MODEL_DIR,
    WHISPER_MODEL, WHISPER_INITIAL_PROMPT, tmp_vol, TMP_DIR, _fix_rtl_punct,
    _rtl_ass_text, _censor_caption_text, _RLE, _PDF, _SAFE_KEY_RE,
    _BROLL_KEY_RE, _is_allowed_broll_url,
    jobs_store, JOB_RETENTION_DAYS, SCRATCH_RETENTION_HOURS, pending_store,
    progress_store, calls_store, CALL_RETENTION_SECONDS, _UID_PREFIX_RE,
    quota_store, _usage_since, _send_email, _email_html, SONNET_MODEL,
    _send_fcm, costs_store, COST_RETENTION_DAYS, _cost_summary,
    _credit_cost, _upscale_allowed, UPSCALE_MAX_SECONDS,
)


# ---------------------------------------------------------------------------
# Daily GPU-spend digest — a cost guardrail. Counts credits consumed in the
# last 24h and emails the admin, flagging days over USAGE_ALERT_THRESHOLD.
# Video count is a good proxy for GPU spend (each /process is the expensive
# stage). Best-effort: no ADMIN_EMAIL/RESEND_API_KEY just logs.
# ---------------------------------------------------------------------------
@app.function(image=light_image, schedule=modal.Period(days=1),
              secrets=[modal.Secret.from_name("hebpipe-auth"),
                       modal.Secret.from_name("hebpipe-usage")])
def daily_usage_report() -> dict:
    import os, time
    since = time.time() - 24 * 3600
    count, users = _usage_since(quota_store, since)
    cost = _cost_summary(costs_store, since)
    threshold = int(os.environ.get("USAGE_ALERT_THRESHOLD", "50") or "50")
    admin_email = os.environ.get("ADMIN_EMAIL")
    over = count > threshold
    print(f"[usage] 24h: {count} videos, {users} users (threshold {threshold})")
    print(f"[cost] 24h: ${cost['usd']} over {cost['videos']} job(s), "
          f"${cost['usd_per_video']}/video, modes={cost['by_mode']}")
    if admin_email:
        site = os.environ.get("SITE_URL", "https://hebrew-pipeline.app")
        flag = " - OVER THRESHOLD" if over else ""
        # Cost per video is the number that decides whether a credit is priced
        # right, so it rides along with the volume digest.
        cost_line = (f" Compute: ${cost['usd']} total, ${cost['usd_per_video']} per video "
                     f"({cost['gpu_secs']}s GPU, {cost['cpu_secs']}s CPU)."
                     if cost["videos"] else "")
        _send_email(admin_email, f"Pipeline usage: {count} videos in 24h{flag}",
                    _email_html("Daily usage digest",
                                f"{count} videos processed by {users} user(s) in the last 24 hours "
                                f"(alert threshold: {threshold}).{cost_line}",
                                "Open the app", site))
    else:
        print("[usage] ADMIN_EMAIL not set — digest logged only")
    return {"count": count, "users": users, "over_threshold": over, "cost": cost}

# ---------------------------------------------------------------------------
# Off-site metadata backup — the critical Dicts (accounts, History manifest,
# quota ledger, Metricool tokens) are the only non-reconstructable state and
# have NO native Modal backup. A daily JSON snapshot is pushed to an
# S3-compatible bucket (Cloudflare R2 / Backblaze B2) so a cleared/corrupted
# Dict — or total loss of the Modal account — is recoverable. Videos are NOT
# backed up (30-day retention by design; users keep their own copies).
# Best-effort: no creds → logs and skips, so a deploy without the secret never
# fails. NOTE: the snapshot contains PBKDF2 password hashes + OAuth tokens —
# the bucket MUST be private.
# ---------------------------------------------------------------------------
_BACKUP_STORES = ("jobs", "users", "quota", "purchases", "metricool", "costs")

def _backup_stores_map():
    from pipeline_core import users_store, quota_store, purchases_store, jobs_store
    from metricool_fns import oauth_store
    return {"jobs": jobs_store, "users": users_store,
            "quota": quota_store, "purchases": purchases_store,
            "metricool": oauth_store, "costs": costs_store}

def _s3_client():
    import os, boto3
    return boto3.client(
        "s3",
        endpoint_url=os.environ["S3_ENDPOINT_URL"],
        aws_access_key_id=os.environ["S3_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["S3_SECRET_ACCESS_KEY"],
        region_name=os.environ.get("S3_REGION", "auto"),
    )

@app.function(image=light_image, schedule=modal.Period(days=1),
              secrets=[modal.Secret.from_name("hebpipe-backup")])
def backup_dicts() -> dict:
    import os, json, datetime
    if not all(os.environ.get(k) for k in
               ("S3_ENDPOINT_URL", "S3_BUCKET", "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY")):
        print("[backup] hebpipe-backup S3 creds not set — skipping")
        return {"ok": False, "reason": "no_creds"}
    snapshot = {"_meta": {"ts": datetime.datetime.utcnow().isoformat() + "Z"}}
    counts = {}
    for name, store in _backup_stores_map().items():
        try:
            data = {k: store.get(k) for k in list(store.keys())}
            snapshot[name], counts[name] = data, len(data)
        except Exception as e:
            snapshot[name], counts[name] = {"_error": repr(e)}, -1
    payload = json.dumps(snapshot, ensure_ascii=False, default=str).encode("utf-8")
    bucket = os.environ["S3_BUCKET"]
    stamp = datetime.datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    key = f"backups/hebpipe-{stamp}.json"
    s3 = _s3_client()
    s3.put_object(Bucket=bucket, Key=key, Body=payload, ContentType="application/json")
    try:  # rotation — keep the 30 most recent snapshots
        objs = s3.list_objects_v2(Bucket=bucket, Prefix="backups/hebpipe-").get("Contents", [])
        for o in sorted(objs, key=lambda x: x["Key"])[:-30]:
            s3.delete_object(Bucket=bucket, Key=o["Key"])
    except Exception as e:
        print(f"[backup] rotation skipped: {e!r}")
    print(f"[backup] wrote {key} ({len(payload)} bytes) counts={counts}")
    return {"ok": True, "key": key, "bytes": len(payload), "counts": counts}

@app.function(image=light_image, secrets=[modal.Secret.from_name("hebpipe-backup")])
def restore_dicts(key: str, which: str = "", apply: bool = False) -> dict:
    """Restore Dicts from a snapshot. MERGES snapshot entries into the live Dict
    (never deletes keys added since the snapshot). Dry-run by default.
    Run: modal run app_modal.py::restore_dicts --key backups/hebpipe-....json \\
             --which users --apply
    """
    import json
    stores = _backup_stores_map()
    snap = json.loads(_s3_client().get_object(
        Bucket=__import__("os").environ["S3_BUCKET"], Key=key)["Body"].read())
    targets = [w.strip() for w in which.split(",") if w.strip()] or list(_BACKUP_STORES)
    report = {}
    for name in targets:
        data = snap.get(name)
        if not isinstance(data, dict) or "_error" in data:
            report[name] = "skipped (missing/errored in snapshot)"
            continue
        if apply:
            for k, v in data.items():
                stores[name][k] = v
            report[name] = f"restored {len(data)} keys"
        else:
            report[name] = f"would restore {len(data)} keys (dry-run — pass --apply)"
    print(f"[restore] {key} apply={apply}: {report}")
    return report

# ---------------------------------------------------------------------------
# Shared pure helpers
# ---------------------------------------------------------------------------
import json
import re
import subprocess
from dataclasses import dataclass, field
from typing import List

@dataclass
class Word:
    start: float
    end: float
    text: str
    prob: float = 1.0       # Whisper per-word confidence (0-1); 1.0 = unknown/CLI
    filler: bool = False    # hesitation ("umm"/"אמ") or noise fragment -> excise

@dataclass
class KeepSegment:
    start: float
    end: float
    words: List[Word] = field(default_factory=list)

    @property
    def duration(self):
        return self.end - self.start


# ── Speech-vs-noise gate for the silence cutter ───────────────────────────────
# The cutter keeps ONLY confident, real speech; ambient noise (birds, traffic,
# wind) that Whisper hallucinates words over, and vocalized hesitations
# ("umm", "אה"), are treated as cuttable. These are the tuning knobs — raise to
# cut more aggressively, lower to keep more.
_NO_SPEECH_HARD  = 0.85   # drop a Whisper segment this likely to be non-speech outright
_NO_SPEECH_MAX   = 0.60   # ...or this likely AND low-confidence (the classic hallucination combo)
_AVG_LOGPROB_MIN = -1.00  # segment avg token logprob below this => low-confidence => drop
_WORD_PROB_MIN   = 0.15   # excise a token only if THIS unconfident (noise fragment); real
                          # quiet/fast Hebrew words score well above this, so they're kept

# Hesitation / filler tokens to excise from BOTH the cut audio and the captions.
# Kept conservative for Hebrew: only forms that are NOT real content words.
# NOTE: real words deliberately excluded — אם (if), הם (they), עם (with).
_FILLER_EXACT = {
    # English vocalized pauses
    "um", "umm", "uh", "uhh", "uhm", "uhmm", "er", "err", "erm", "ermm",
    "hmm", "hm", "mm", "mmm", "mhm", "eh",
    # Hebrew vocalized pauses / interjections (medial-mem hesitation forms,
    # never the final-mem real word אם):
    "אמ", "אה", "אהה", "אממ", "אמם", "אהמ", "אהם", "אא", "עמ",
}
_NIQQUD_RE = re.compile(r"[֑-ׇ]")           # Hebrew points / cantillation
_STRIP_EDGE = "‎‏.,!?;:־–—…\"'`׳״ ()[]{}"    # punct/marks to trim before matching
_ELONG_RE = re.compile(r"(.)\1\1")                    # any character tripled = elongated hesitation

def _is_filler(text):
    """True if `text` is a vocalized hesitation / filler (not real speech)."""
    t = _NIQQUD_RE.sub("", text or "").strip().strip(_STRIP_EDGE).lower()
    if not t:
        return False
    if t in _FILLER_EXACT:
        return True
    # Elongated single-syllable hesitation: a letter tripled in a short token
    # ("אההה", "אמממ", "ummm", "errr"). Real words don't triple a letter.
    if len(t) <= 6 and _ELONG_RE.search(t):
        return True
    return False

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

def has_audio_stream(path):
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a",
         "-show_entries", "stream=index", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )
    return bool(r.stdout.strip())

def probe_duration(path):
    """Container duration only — for audio-only inputs that have no video
    stream (probe_video selects v:0 and would raise)."""
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(json.loads(r.stdout)["format"]["duration"])

def has_video_stream(path):
    """True if the file has a real (moving) video stream. Album art in an audio
    file is a video stream with disposition attached_pic=1 — excluded here so
    an MP3/M4A with cover art is still treated as audio."""
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v",
         "-show_entries", "stream=codec_type:stream_disposition=attached_pic",
         "-of", "json", str(path)],
        capture_output=True, text=True,
    )
    try:
        streams = json.loads(r.stdout).get("streams", [])
    except Exception:
        return True   # if unsure, assume video (safer default: full pipeline)
    return any(s.get("disposition", {}).get("attached_pic", 0) != 1 for s in streams)

# ── Proofread phonetic guard ────────────────────────────────────────────────
# The proofread prompt forbids replacing a word with a DIFFERENT-sounding one,
# but that is a soft instruction - a field report caught 'צופה' becoming
# 'גולשת' (plausible in context, phonetically unrelated). This deterministic
# check rejects any per-word fix whose consonant skeleton barely overlaps the
# original's: homophone repairs (קולטה→כל תא) share nearly all consonants,
# meaning-swaps share almost none.
def _consonant_key(word):
    """Multiset of phonetically-normalized consonants (vowel-letters dropped).
    Tables live in-function so the AST-extraction tests get them for free."""
    phon_eq = {"ת": "ט", "כ": "ק", "ך": "ק", "ע": "א", "ה": "א", "ס": "ש",
               "ם": "מ", "ן": "נ", "ף": "פ", "ץ": "צ", "ב": "ו"}
    vowelish = set("אהוי")
    out = []
    for ch in word:
        if not ("א" <= ch <= "ת"):
            continue
        ch = phon_eq.get(ch, ch)
        if ch in vowelish:
            continue
        out.append(ch)
    return out

def _phonetically_plausible(orig, fixed):
    """True when `fixed` could plausibly SOUND like `orig` (or either is too
    short to judge). Compares consonant-skeleton overlap; spaces in `fixed`
    are fine (merged-token expansions keep the same sounds)."""
    if orig == fixed:
        return True
    a = _consonant_key(orig)
    b = _consonant_key(fixed.replace(" ", ""))
    if len(a) <= 1 or len(b) <= 1:
        return True   # too little signal to judge - trust the model
    from collections import Counter
    ca, cb = Counter(a), Counter(b)
    inter = sum((ca & cb).values())
    union = sum((ca | cb).values())
    return union == 0 or (inter / union) >= 0.4


def proofread_words(texts, client, model, chunk_size=400, max_tokens=8000):
    """Fix Hebrew ASR mishearings via one context-aware LLM pass.

    Whisper's Hebrew errors are homophone letter swaps (ט/ת, כ/ק, א/ע/ה), wrong
    conjugations, and same-sounding non-words that only sentence context can
    catch (e.g. 'קולטה' → 'כל תא', 'אשמחה' → 'שמחה'). Words are sent in
    fixed-size chunks and the model returns a JSON array of exactly the same
    LENGTH (order never changes), so each element still maps 1:1 to its audio
    span and the word-level timestamps stay valid. A single element MAY hold a
    multi-word phrase when the recognizer merged words — the whole phrase then
    shares that token's timestamp (fine for caption lines). Any failure (API
    error, parse error, wrong count) keeps that chunk's original words. Never
    raises.
    """
    import json
    out = list(texts)
    for start in range(0, len(out), chunk_size):
        chunk = out[start:start + chunk_size]
        prompt = (
            "The JSON array below is a continuous Hebrew transcript from "
            "speech recognition - one spoken token per element, in order. Some "
            "tokens are mis-heard. Your job is to fix ONLY genuine recognition "
            "errors while staying faithful to what was actually said.\n"
            "IRON RULE - PHONETIC FAITHFULNESS: a replacement must sound "
            "essentially the SAME as the original token (same syllables/"
            "consonants), just spelled or split correctly. Never change a token "
            "to a different-sounding word or phrase, even if it would read more "
            "fluently. Fidelity to the sound beats fluency, always.\n"
            "How to choose the fix: among the options that SOUND like the "
            "token, pick the one that makes the most sense in the surrounding "
            "context - NOT the one with the fewest letter changes. A bigger "
            "edit that fits the sentence beats a tiny edit that doesn't.\n"
            "Rules:\n"
            "- Fix homophone letter swaps (ט/ת, כ/ק, א/ע/ה, ש/ס), a missing or "
            "extra letter, or a wrong conjugation (e.g. 'אשמחה' -> 'שמחה').\n"
            "- If the recognizer merged words into one token, expand it into "
            "the same-sounding phrase that fits context, keeping the space(s) "
            "INSIDE that one element. Choose by meaning, not edit size: e.g. in "
            "a sentence about the body, 'קולטה' -> 'כל תא' (fits), NOT 'קולטת' "
            "(sounds close, a smaller edit, but makes no sense there).\n"
            "- EVERY element you return must be real Hebrew: a valid word, or a "
            "legitimate proper name, foreign/loan word, or interjection. NEVER "
            "leave a garbled non-word in place - if a token is not real Hebrew "
            "(e.g. 'קולטה', 'אשמחה'), you MUST replace it with the best "
            "same-sounding real word(s) that fit the context, even if you are "
            "not fully certain.\n"
            "- If a token is ALREADY valid Hebrew and fits the context, LEAVE "
            "IT UNCHANGED - 'keep the original' applies only to already-valid "
            "tokens, never as an excuse to keep a non-word. Do not rewrite for "
            "style. Keep slang/colloquial speech as spoken.\n"
            "- FORBIDDEN example (different sound, invented): 'שמחה' -> "
            "'אשתה איתך'. Never do this.\n"
            "- Do NOT reorder elements and do NOT change the array length - it "
            "must stay exactly the same (each element = one span of audio; any "
            "merge stays inside its own element).\n"
            f"Return ONLY a JSON array of strings with exactly {len(chunk)} "
            "elements - no explanations.\n\n"
            f"{json.dumps(chunk, ensure_ascii=False)}"
        )
        try:
            raw = client.messages.create(
                model=model, max_tokens=max_tokens,
                thinking={"type": "disabled"},
                messages=[{"role": "user", "content": prompt}],
            ).content[0].text.strip()
            if "```" in raw:
                for part in raw.split("```"):
                    part = part.strip().lstrip("json").strip()
                    if part.startswith("["):
                        raw = part
                        break
            fixed = json.loads(raw)
            if (isinstance(fixed, list) and len(fixed) == len(chunk)
                    and all(isinstance(w, str) and w.strip() for w in fixed)):
                cleaned = []
                for orig, w in zip(chunk, fixed):
                    w = w.strip()
                    if w != orig and not _phonetically_plausible(orig, w):
                        print(f"[proofread] rejected non-phonetic swap "
                              f"{orig!r} -> {w!r} - kept original")
                        w = orig
                    cleaned.append(w)
                out[start:start + len(chunk)] = cleaned
            else:
                print(f"[proofread] chunk at {start}: bad shape - kept original")
        except Exception as e:
            print(f"[proofread] chunk at {start}: {e!r} - kept original")
    return out


def compute_keep_segments(whisper_segs, total_dur, min_sil, pad):
    """Keep only spans of real speech; cut silence AND filler/noise words.

    whisper_segs: List[(List[Word], seg_end)] — Whisper segments, already
    noise-gated in transcribe(). Here we work on the flat word stream:

      * words flagged `filler` (hesitations, sub-threshold-confidence noise
        fragments) are EXCISED — a tight cut is forced around each one so the
        "umm" / "אה" audio is actually removed, not merely hidden from captions;
      * a gap >= min_sil between consecutive real words is a silence cut;
      * everything else is merged into a kept segment with `pad` breathing room.

    This is word-granular (unlike the old segment-granular cut) so hallucinated
    noise and fillers no longer keep a whole segment alive — the #1 reason a
    noisy clip "barely got cut".
    """
    # Whisper (especially CUDA) underestimates segment-end timestamps for Hebrew
    # trailing vowels (ו, י, ה) by 150-300ms — buffer real speech ends. Trimmed
    # 0.25 -> 0.18 (2026-07-10): the VAD word-timestamp repair now recovers most
    # word-ends accurately, so a smaller safety buffer shortens the leftover
    # pause on every cut (e.g. the ~0.5s residual at undetectable room-tone
    # pauses) with little added clipping risk.
    TRAIL = 0.18
    # Minimal buffer kept around a tight filler excision so we don't clip the
    # consonant of the neighbouring real word.
    EDGE = 0.06

    words = [w for seg_words, _e in whisper_segs for w in seg_words]
    # Partition into kept (real) words, remembering the span of any filler/noise
    # run sitting immediately before each kept word (so we can excise it tightly).
    kept, drop_before = [], []
    pending = None  # [start, end] of the dropped run before the next kept word
    for w in words:
        if w.filler:
            if pending is None:
                pending = [w.start, w.end]
            else:
                pending[1] = max(pending[1], w.end)
            continue
        kept.append(w)
        drop_before.append(pending)
        pending = None

    if not kept:
        # No confident speech anywhere — don't nuke the whole video; keep it.
        return [KeepSegment(0.0, total_dur, words)]

    out = []
    cur = KeepSegment(start=max(0.0, kept[0].start - pad), end=0.0, words=[kept[0]])
    prev_end = kept[0].end
    for i in range(1, len(kept)):
        w = kept[i]
        dropped = drop_before[i]
        if dropped is not None:
            # A filler/noise word sat between the two real words — cut tightly
            # around it, excising its audio (and surrounding dead air),
            # regardless of min_sil.
            ds, de = dropped
            cur.end = min(total_dur, max(cur.start, min(prev_end + EDGE, ds)))
            out.append(cur)
            nstart = max(cur.end, de, w.start - EDGE)
            cur = KeepSegment(start=min(nstart, w.start), end=0.0, words=[w])
        elif (w.start - prev_end) >= min_sil:
            cur.end = min(total_dur, prev_end + pad + TRAIL)
            out.append(cur)
            cur = KeepSegment(start=max(cur.end, w.start - pad), end=0.0, words=[w])
        else:
            cur.words.append(w)
        prev_end = w.end

    cur.end = min(total_dur, prev_end + pad + TRAIL)
    out.append(cur)
    return out

def _clamp_word_to_speech(ws, we, speech):
    """Trim a word's [ws,we] span to the voice regions it overlaps.

    Whisper's word timestamps routinely inflate to swallow a pause — e.g. a
    trailing "אם" reported as [16.8, 18.14] when the voice is only at
    [18.0, 18.14] and 16.8-18.0 is a room-tone pause. VAD knows where the voice
    actually is; clamping to it re-exposes the gap so the word-gap cutter (and
    caption timing) see the real pause. `speech` is a sorted list of (s,e)
    voice intervals. Returns the (possibly trimmed) (start, end); leaves the
    word untouched if VAD found no voice overlapping it (avoid destroying a
    quiet word VAD may have missed)."""
    overlap = [(s, e) for (s, e) in speech if e > ws and s < we]
    if not overlap:
        return ws, we
    ns = max(ws, overlap[0][0])
    ne = min(we, overlap[-1][1])
    if ne - ns < 0.02:          # degenerate after clamp — keep original
        return ws, we
    return ns, ne

def _repair_word_timestamps_with_vad(whisper_segs, wav_path, min_sil):
    """Shrink each word's timestamps to the VAD voice regions (see
    _clamp_word_to_speech). Best-effort: any failure leaves timings untouched.
    Uses faster-whisper's bundled Silero VAD (voice presence, not loudness) so
    high-room-tone pauses Whisper hides are still detected."""
    from faster_whisper.audio import decode_audio
    from faster_whisper.vad import get_speech_timestamps, VadOptions
    sr = 16000
    audio = decode_audio(str(wav_path), sampling_rate=sr)
    opts = VadOptions(threshold=0.5,
                      min_silence_duration_ms=max(200, int(min_sil * 1000)),
                      speech_pad_ms=100)
    chunks = get_speech_timestamps(audio, opts)
    speech = [(c["start"] / sr, c["end"] / sr) for c in chunks]
    if not speech:
        return 0
    n = 0
    for seg_words, _e in whisper_segs:
        for w in seg_words:
            ns, ne = _clamp_word_to_speech(w.start, w.end, speech)
            if abs(ns - w.start) > 0.05 or abs(ne - w.end) > 0.05:
                n += 1
            w.start, w.end = ns, ne
    return n

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
            # Per-word timings (post-cut timeline) ride with the event - the
            # word-by-word caption style needs them in the editor and burn.
            words = [
                [round(cumulative + (ww.start - seg.start), 3),
                 round(cumulative + (ww.end - seg.start), 3),
                 _clean(ww.text)]
                for ln in lines_in_cue for ww in ln
            ]
            events.append((ns, ne, text, words))
            lines_in_cue.clear()

        prev_end = None
        for ww in seg.words:
            # Never show hesitations/noise fragments in captions (they're also
            # excised from the audio when silence-cut is on).
            if getattr(ww, "filler", False):
                continue
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
        for s, e, t, _w in events
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

def _upscale_target(w: int, h: int, cap_short: int = 2160):
    """Output size for AI upscale: up to 4× the input, capped at a 2160px short
    side (true-4K master — platforms downscaling it keep the supersampled
    crispness), never below input, even dimensions."""
    short = min(w, h)
    scale = min(4.0, cap_short / short) if short < cap_short else 1.0
    scale = max(1.0, scale)
    return int(w * scale) // 2 * 2, int(h * scale) // 2 * 2


# "Enhance video" filter chain — light temporal denoise, mild sharpen, gentle
# color lift. Deliberately subtle: this must never visibly change the look of
# well-lit footage, only clean up noise and soften compression mush.
ENHANCE_VIDEO_VF = "hqdn3d=1.5:1.5:6:6,unsharp=5:5:0.5:5:5:0.0,eq=contrast=1.02:saturation=1.05"


def render(video, audio, segs, ass_file, out, crf=18, rotation=0, extra_vf=""):
    v_parts, a_parts = [], []
    for i, s in enumerate(segs):
        v_parts.append(f"[0:v]trim=start={s.start:.3f}:end={s.end:.3f},setpts=PTS-STARTPTS[v{i}]")
        a_parts.append(f"[1:a]atrim=start={s.start:.3f}:end={s.end:.3f},asetpts=PTS-STARTPTS[a{i}]")
    cin = "".join(f"[v{i}][a{i}]" for i in range(len(segs)))
    concat = f"{cin}concat=n={len(segs)}:v=1:a=1[vc][ac]"
    rot_vf = _rotation_vf(rotation)
    enh_vf = f"{extra_vf}," if extra_vf else ""
    if ass_file:
        esc = str(ass_file).replace(":", r"\:")
        last = f"[vc]{rot_vf}{enh_vf}subtitles='{esc}'[vout]"
    else:
        last = f"[vc]{rot_vf}{enh_vf}copy[vout]"
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


def render_audio(audio_in, segs, out):
    """Audio-only deliverable: trim to keep-segments (if any) and encode to AAC
    in an .m4a container. `segs=None`/empty just re-encodes the whole track.
    Mirrors render() but with no video stream."""
    if segs:
        a_parts = [f"[0:a]atrim=start={s.start:.3f}:end={s.end:.3f},asetpts=PTS-STARTPTS[a{i}]"
                   for i, s in enumerate(segs)]
        cin = "".join(f"[a{i}]" for i in range(len(segs)))
        concat = f"{cin}concat=n={len(segs)}:v=0:a=1[aout]"
        fc = ";".join(a_parts + [concat])
        run(["ffmpeg", "-y", "-i", str(audio_in),
             "-filter_complex", fc, "-map", "[aout]",
             "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out)])
    else:
        run(["ffmpeg", "-y", "-i", str(audio_in), "-vn",
             "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out)])


# ---------------------------------------------------------------------------
# Core processing — GPU worker
# ---------------------------------------------------------------------------
@app.function(
    gpu="L4",
    timeout=1800,
    # Hard ceiling on simultaneous L4 containers. Modal otherwise autoscales
    # GPU containers without bound — this caps peak GPU spend during a burst
    # (excess /process calls queue rather than spin up unbounded GPUs).
    max_containers=12,
    volumes={MODEL_DIR: model_volume, TMP_DIR: tmp_vol},
    memory=4096,
    secrets=[modal.Secret.from_name("anthropic-secret"),
             modal.Secret.from_name("hebpipe-fcm")],
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
    key_prefix: str = "",
    enhance_video: str = "none",   # none | filters | esrgan
    is_audio: bool = False,        # audio-only input: no video, output a clean .m4a
    credits_charged: int = 0,      # what the router billed; 0 = unpriced (admin/legacy)
) -> dict:
    # Warmup call — just starts the container, no real work
    if filename == "__warmup__":
        return {"captions": [], "video_key": ""}

    import json
    import shutil
    import tempfile
    import time as _time
    from pathlib import Path

    _t0 = _time.time()   # whole-run wall clock, recorded as this job's GPU seconds

    # One-line audit of the received toggles - the definitive record of what
    # this job was ASKED to do (field reports of "it cut although the toggle
    # was off" need this to split sender vs worker).
    print(f"[params] key={upload_key} cut_silences={cut_silences} burn_captions={burn_captions} "
          f"enhance_audio={enhance_audio} enhance_video={enhance_video} is_audio={is_audio} "
          f"min_silence={min_silence} padding={padding} broll={transcribe_for_broll}")

    # Real per-stage timing, published to progress_store so /process_poll can
    # report live progress and the site checklist shows actual numbers.
    _stage_start = {}
    _step_times = {}
    def _mark(stage=None, done=None):
        now = _time.time()
        if done and done in _stage_start:
            _step_times[done] = round(now - _stage_start[done], 1)
        if stage:
            _stage_start[stage] = now
        if upload_key:
            try:
                progress_store[upload_key] = {"stage": stage, "done": dict(_step_times), "ts": now}
            except Exception:
                pass

    # Validate input before entering the tempdir
    if upload_key is not None:
        tmp_vol.reload()
        chunk_paths = sorted(Path(TMP_DIR).glob(f"{upload_key}_chunk_*"))
        if not chunk_paths and not (Path(TMP_DIR) / f"{upload_key}_src.mp4").exists():
            raise ValueError(f"No upload chunks found for key {upload_key}")
    elif not video_bytes:
        raise ValueError("No video data provided")

    # Push story is EXACTLY TWO messages (user request, 2026-07-16): this one =
    # "upload done" (process_video starting means the last byte landed), and one
    # completion push (edit_ready / video_ready below). Same tag, so the second
    # REPLACES this in the shade. The post-burn push was removed - the export
    # runs while the user is in the editor. Foregrounded apps don't display FCM
    # notifications, so a user watching the checklist never sees it. Best-effort.
    try:
        _uid0 = upload_key[1:33] if upload_key and _UID_PREFIX_RE.match(upload_key) else None
        if _uid0:
            _send_fcm(_uid0, "ההעלאה הסתיימה",
                      "מעבד את הסרטון בשרת - אפשר לסגור את האפליקציה, נעדכן כשיהיה מוכן.",
                      kind="processing", tag="hebpipe-job")
    except Exception:
        pass

    def extract_audio(video, out_wav):
        run(["ffmpeg", "-y", "-i", str(video),
             "-vn", "-af", "loudnorm", "-acodec", "pcm_s16le", "-ar", "48000", "-ac", "1",
             str(out_wav)])

    def enhance_esrgan(video_in, video_out):
        """Real-ESRGAN (realesr-general-x4v3) upscale/detail pass.

        The SRVGGNetCompact architecture is implemented inline with plain torch —
        the realesrgan/basicsr packages are avoided on purpose (basicsr imports
        torchvision.transforms.functional_tensor, removed in torchvision 0.17+).
        Frames are piped raw through ffmpeg: decode → model (4×, fp16) → area
        downscale to the target (2× capped at 1080 short side) → encode.
        """
        import subprocess as _sp
        import urllib.request
        import torch
        import torch.nn as nn
        import torch.nn.functional as F

        # ── weights (cached on the persistent model volume) ──
        wpath = Path(MODEL_DIR) / "realesr-general-x4v3.pth"
        if not wpath.exists():
            urllib.request.urlretrieve(
                "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesr-general-x4v3.pth",
                str(wpath))
            model_volume.commit()

        class SRVGGNetCompact(nn.Module):
            def __init__(self, num_feat=64, num_conv=32, upscale=4):
                super().__init__()
                self.upscale = upscale
                body = [nn.Conv2d(3, num_feat, 3, 1, 1), nn.PReLU(num_parameters=num_feat)]
                for _ in range(num_conv):
                    body += [nn.Conv2d(num_feat, num_feat, 3, 1, 1), nn.PReLU(num_parameters=num_feat)]
                body += [nn.Conv2d(num_feat, 3 * upscale * upscale, 3, 1, 1)]
                self.body = nn.ModuleList(body)
                self.upsampler = nn.PixelShuffle(upscale)

            def forward(self, x):
                out = x
                for m in self.body:
                    out = m(out)
                out = self.upsampler(out)
                return out + F.interpolate(x, scale_factor=self.upscale, mode="nearest")

        device = "cuda" if torch.cuda.is_available() else "cpu"
        dtype = torch.float16 if device == "cuda" else torch.float32
        sd = torch.load(str(wpath), map_location="cpu")
        sd = sd.get("params_ema", sd.get("params", sd))
        model = SRVGGNetCompact()
        model.load_state_dict(sd, strict=True)
        model = model.eval().to(device, dtype)

        # ── geometry ──
        pr = _sp.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                      "-show_entries", "stream=width,height,avg_frame_rate",
                      "-of", "json", str(video_in)], capture_output=True, text=True, check=True)
        s = json.loads(pr.stdout)["streams"][0]
        w, h = int(s["width"]), int(s["height"])
        num, den = (s.get("avg_frame_rate") or "30/1").split("/")
        fps = (float(num) / float(den)) if float(den) else 30.0
        tw, th = _upscale_target(w, h)
        # model input: pre-scale huge sources down so 4× stays in memory
        dw, dh = w, h
        if min(w, h) > 1080:
            ds = 1080 / min(w, h)
            dw, dh = int(w * ds) // 2 * 2, int(h * ds) // 2 * 2

        dec = _sp.Popen(
            ["ffmpeg", "-v", "error", "-i", str(video_in),
             "-vf", f"scale={dw}:{dh}", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"],
            stdout=_sp.PIPE)
        enc = _sp.Popen(
            ["ffmpeg", "-y", "-v", "error",
             "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{tw}x{th}", "-r", f"{fps:.4f}", "-i", "pipe:0",
             "-i", str(video_in),
             "-map", "0:v", "-map", "1:a?",
             # cas: contrast-adaptive sharpen — the "smart sharpen" pop on the
             # final delivery frames, applied after the model + downscale
             "-vf", "cas=0.4",
             "-c:v", "libx264", "-crf", "18", "-preset", "veryfast",
             "-pix_fmt", "yuv420p", "-c:a", "copy",
             "-movflags", "+faststart", str(video_out)],
            stdin=_sp.PIPE, stdout=_sp.DEVNULL, stderr=_sp.PIPE)

        frame_bytes = dw * dh * 3
        try:
            with torch.inference_mode():
                while True:
                    buf = dec.stdout.read(frame_bytes)
                    if not buf or len(buf) < frame_bytes:
                        break
                    x = torch.frombuffer(bytearray(buf), dtype=torch.uint8).view(dh, dw, 3)
                    x = x.to(device).permute(2, 0, 1).unsqueeze(0).to(dtype) / 255.0
                    y = model(x)
                    if (y.shape[3], y.shape[2]) != (tw, th):
                        y = F.interpolate(y, size=(th, tw), mode="area")
                    y = (y.clamp(0, 1) * 255.0).round().to(torch.uint8)
                    enc.stdin.write(y.squeeze(0).permute(1, 2, 0).contiguous().cpu().numpy().tobytes())
        finally:
            dec.stdout.close()
            enc.stdin.close()
            dec.wait(timeout=60)
            rc = enc.wait(timeout=300)
        if rc != 0:
            raise RuntimeError(f"esrgan encode failed ({rc}): "
                               f"{enc.stderr.read().decode(errors='replace')[-1500:]}")

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
            #
            # Noise gate: ambient sound (birds, traffic, wind) makes Whisper
            # emit low-confidence, high-"no_speech" segments. Drop those whole
            # segments so their audio becomes a cut rather than kept "speech".
            # Per word we keep the confidence and pre-flag hesitations / very
            # low-confidence tokens as `filler` for the cutter to excise.
            result = []
            for seg in segs:
                nsp = float(getattr(seg, "no_speech_prob", 0.0) or 0.0)
                alp = float(getattr(seg, "avg_logprob", 0.0) or 0.0)
                if nsp >= _NO_SPEECH_HARD or (nsp >= _NO_SPEECH_MAX and alp <= _AVG_LOGPROB_MIN):
                    continue  # non-speech / hallucination — treat as silence
                seg_words = []
                for w in (seg.words or []):
                    txt = w.word.strip()
                    if not txt:
                        continue
                    # float() strips numpy scalars — the api router runs on
                    # light_image (no numpy) and can't unpickle np.float64.
                    prob = float(getattr(w, "probability", 1.0) or 1.0)
                    word = Word(float(w.start), float(w.end), txt, prob)
                    # Excise only genuine fillers, or a token so unconfident it's
                    # almost certainly a noise fragment (very low bar so real but
                    # quiet/fast Hebrew words are NOT clipped).
                    word.filler = _is_filler(txt) or prob < _WORD_PROB_MIN
                    seg_words.append(word)
                # Segment that is entirely filler / noise fragments — drop it.
                if not any(not w.filler for w in seg_words):
                    continue
                if seg_words:
                    result.append((seg_words, float(seg.end)))
            return result

        # VAD gate (Silero, tuned for human voice) is the first noise filter.
        # speech_pad_ms keeps a buffer around detected speech so weak Hebrew
        # consonants (ע, ח, ה) at word edges survive.
        vad_params = {
            "threshold": 0.5,                # default — rejects non-voice (wind/traffic/birds score low)
            "min_silence_duration_ms": 500,
            "speech_pad_ms": 400,
        }
        # condition_on_previous_text=False is deliberate: feeding prior text back
        # is the main driver of runaway hallucination over long noisy/quiet
        # stretches (the model "keeps talking"). The Sonnet proofread pass
        # restores cross-sentence coherence, so we lose little accuracy and gain
        # far cleaner speech/noise separation. no_speech/logprob/compression
        # thresholds let faster-whisper itself drop non-speech segments.
        common = dict(
            language="he", word_timestamps=True,
            vad_filter=True, vad_parameters=vad_params,
            beam_size=5, condition_on_previous_text=False,
            no_speech_threshold=_NO_SPEECH_MAX,
            log_prob_threshold=_AVG_LOGPROB_MIN,
            compression_ratio_threshold=2.4,
            initial_prompt=WHISPER_INITIAL_PROMPT,
        )
        try:
            m = WhisperModel(WHISPER_MODEL, device="cuda", compute_type="float16",
                             download_root=MODEL_DIR)
            segs, _ = m.transcribe(str(wav), **common)
            whisper_segs = _segments(segs)
        except Exception:
            m = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8",
                             download_root=MODEL_DIR)
            segs, _ = m.transcribe(str(wav), **common)
            whisper_segs = _segments(segs)
        model_volume.commit()
        return whisper_segs

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

        # Trust the frontend's audio flag, but also treat a file with no real
        # video stream as audio so probe_video (which selects v:0) never raises.
        is_audio = is_audio or not has_video_stream(src)
        if is_audio:
            width = height = rotation = 0
            duration = probe_duration(src)
        else:
            width, height, duration, rotation = probe_video(src)
        # Price verification, against OUR probe rather than the client's claim.
        # The router charged credits using the duration the client reported, so
        # this is where understating it stops paying off. Checked immediately
        # after the probe, before any expensive stage runs, and a raise here is
        # terminal - which refunds every credit the run charged.
        if credits_charged and _credit_cost(duration, enhance_video) > credits_charged:
            raise RuntimeError(
                f"credit_mismatch: {duration:.0f}s source with enhance={enhance_video} "
                f"needs {_credit_cost(duration, enhance_video)} credits, {credits_charged} charged")
        if enhance_video == "esrgan" and not _upscale_allowed(duration):
            raise RuntimeError(
                f"upscale_too_long: {duration:.0f}s source exceeds the "
                f"{UPSCALE_MAX_SECONDS}s upscale limit")
        # The whole pipeline is speech-driven (transcribe -> cut silence ->
        # captions), so an input with no audio track can't be processed. Fail
        # fast with a stable marker the frontend maps to a friendly message,
        # instead of letting extract_audio die with an opaque ffmpeg error.
        if not has_audio_stream(src):
            raise RuntimeError("no_audio: upload has no audio track")
        raw_wav = tmp / "raw.wav"
        clean_wav = tmp / "clean.wav"
        ass_file = tmp / "captions.ass"
        out_file = tmp / ("audio_out.m4a" if is_audio else ("out_" + filename))

        if enhance_audio:
            _mark(stage="enhance")
        extract_audio(src, raw_wav)
        if enhance_audio:
            enhance_deepfilter(raw_wav, clean_wav)
        else:
            shutil.copy(raw_wav, clean_wav)

        # Audio inputs always transcribe (captions/SRT are the whole point).
        need_transcription = cut_silences or burn_captions or transcribe_for_broll or is_audio
        # 'cut' covers transcription, keep-segment math and the trim/concat render
        _mark(stage="cut" if need_transcription else None,
              done="enhance" if enhance_audio else None)
        whisper_segs = transcribe(clean_wav) if need_transcription else []

        # LLM proofread - fixes Hebrew ASR homophone/spelling errors in place.
        # Word count and order are preserved so timestamps stay valid; any
        # failure keeps the raw transcript.
        if whisper_segs:
            try:
                import os as _os, time as _time, anthropic as _anthropic
                if _os.environ.get("ANTHROPIC_API_KEY"):
                    _t0 = _time.time()
                    _texts = [w.text for seg_words, _e in whisper_segs for w in seg_words]
                    _fixed = proofread_words(_texts, _anthropic.Anthropic(max_retries=5), SONNET_MODEL)
                    _n, _it = 0, iter(_fixed)
                    for seg_words, _e in whisper_segs:
                        for w in seg_words:
                            _new = next(_it)
                            _n += (_new != w.text)
                            w.text = _new
                    print(f"[proofread] {_n}/{len(_texts)} words corrected "
                          f"in {_time.time() - _t0:.1f}s")
            except Exception as _pe:
                print(f"[proofread] skipped: {_pe!r}")

        # Repair Whisper word timestamps against VAD voice regions so pauses it
        # hid (inflated word spans over room-tone) become real, cuttable gaps.
        # Best-effort - any failure keeps the raw timings.
        if whisper_segs:
            try:
                _t0 = _time.time()
                _nfix = _repair_word_timestamps_with_vad(whisper_segs, clean_wav, min_silence)
                print(f"[vad-repair] adjusted {_nfix} word timestamps in {_time.time() - _t0:.1f}s")
            except Exception as _ve:
                print(f"[vad-repair] skipped: {_ve!r}")

        flat_words   = [w for seg_words, _end in whisper_segs for w in seg_words]

        segs = (
            compute_keep_segments(whisper_segs, duration, min_silence, padding)
            if cut_silences and whisper_segs
            else [KeepSegment(0.0, duration, flat_words)]
        )
        _kept = sum(s.end - s.start for s in segs)
        print(f"[cut] cut_silences={cut_silences} segments={len(segs)} "
              f"kept={_kept:.1f}s of {duration:.1f}s")

        captions_list = []
        if (burn_captions or transcribe_for_broll or is_audio) and flat_words:
            # Audio has no video dims; feed generate_ass synthetic portrait dims
            # so caption wrapping is sane. The .ass file is unused for audio (no
            # burn) - we only want the timed events for the editor / SRT.
            _cw, _ch = (1080, 1920) if is_audio else (width, height)
            events = generate_ass(segs, ass_file, _cw, _ch, min_sil=min_silence)
            captions_list = [
                {"start": s, "end": e, "text": _censor_caption_text(t),
                 "words": [[ws, we, _censor_caption_text(wt)] for ws, we, wt in wds]}
                for s, e, t, wds in events]

        enh_vf = ENHANCE_VIDEO_VF if enhance_video == "filters" else ""
        if is_audio:
            # Clean sound file: cut from the enhanced mono track when enhancing,
            # else from the original audio (untouched levels/channels). Trim to
            # keep-segments only when silence-cut is on and we have speech.
            audio_src = clean_wav if enhance_audio else src
            cut_segs = segs if (cut_silences and whisper_segs) else None
            render_audio(audio_src, cut_segs, out_file)
        elif cut_silences and whisper_segs:
            render(src, clean_wav, segs, None, out_file, rotation=rotation, extra_vf=enh_vf)
        elif rotation or enh_vf:
            # Re-encode: bake rotation into pixels and/or apply the enhancement chain.
            # When not already covered by the 'cut' stage, report it as the
            # enhancement stage so the checklist shows a real line for it.
            _standalone_enh = bool(enh_vf) and not need_transcription
            if _standalone_enh:
                _mark(stage="upscale")
            rot_vf = _rotation_vf(rotation)
            vf = (rot_vf + enh_vf).strip(",") or "null"
            input_args = ["-noautorotate"] if rotation else []
            meta_args  = ["-metadata:s:v:0", "rotate=0"] if rotation else []
            run(["ffmpeg", "-y"] + input_args + ["-i", str(src),
                 "-vf", vf, "-c:v", "libx264", "-crf", "18", "-preset", "veryfast",
                 "-pix_fmt", "yuv420p", "-c:a", "copy"] + meta_args +
                ["-movflags", "+faststart", str(out_file)])
            if _standalone_enh:
                _mark(done="upscale")
        else:
            shutil.copy(src, out_file)

        _mark(done="cut" if need_transcription else None)

        if enhance_video == "esrgan":
            _mark(stage="upscale")
            enhanced = tmp / ("up_" + filename)
            enhance_esrgan(out_file, enhanced)
            out_file = enhanced
            _mark(done="upscale")

        import uuid
        video_key = key_prefix + uuid.uuid4().hex + ("_cut.m4a" if is_audio else "_cut.mp4")
        cut_path = Path(TMP_DIR) / video_key
        shutil.copy(out_file, cut_path)
        tmp_vol.commit()
        # A cut-only result (no captions to burn, no B-roll pending) is the final
        # deliverable - the site sends the user straight to download/schedule and
        # no burn will follow. Record it in History so it's retained for
        # JOB_RETENTION_DAYS (protected from the 48h scratch prune) and can be
        # re-downloaded or scheduled later, exactly like a burned output. Caption
        # / B-roll jobs skip this: their burn step records the _out.mp4 instead.
        # Audio is always terminal (no burn follows) even though it produces
        # captions, so record it here too.
        if is_audio or (not captions_list and not transcribe_for_broll):
            try:
                _record_job(video_key, filename, cut_path)
                tmp_vol.commit()
                prune_volume()
            except Exception:
                pass
        else:
            # A burn will follow (captions/B-roll flow) - the user may have
            # backgrounded the app during processing, so tell them the editor
            # is ready NOW rather than waiting for them to check. kind
            # "edit_ready" keeps the notification tap on the resumed editor
            # (video_ready taps jump to History instead).
            try:
                _uid = video_key[1:33] if _UID_PREFIX_RE.match(video_key or "") else None
                if _uid:
                    _send_fcm(_uid, "הסרטון מוכן לעריכה",
                              "העיבוד הסתיים - היכנסו לערוך כתוביות ולסיים את הסרטון.",
                              kind="edit_ready", tag="hebpipe-job")
            except Exception:
                pass
        if upload_key is not None:
            try:
                progress_store.pop(upload_key)
            except Exception:
                pass
        # What this job actually burned. One credit is priced per video while
        # the compute varies by ~40x with length and by an order of magnitude
        # with the 4K upscale, so the margin has to be measured, not assumed.
        try:
            costs_store[video_key] = {
                "ts": _time.time(),
                "uid": video_key[1:33] if _UID_PREFIX_RE.match(video_key or "") else "",
                "dur": round(duration or 0, 1),
                "enhance": enhance_video,
                "audio": bool(is_audio),
                "gpu_secs": round(_time.time() - _t0, 1),
                "steps": dict(_step_times),
            }
        except Exception:
            pass
        return {"captions": captions_list, "video_key": video_key,
                "step_times": _step_times}


# ---------------------------------------------------------------------------
# Shared caption + hook ASS builder
# ---------------------------------------------------------------------------
# The SINGLE source of truth for how captions and the hook are rendered. Both
# burn_captions_fn (final video) and the /preview_frame endpoint (WYSIWYG editor
# preview) call this, so the preview is drawn by the EXACT same libass path as
# the burn — pixel-identical (no browser-vs-libass font-metric/outline drift).
def build_caption_ass(width, height, font, font_size, margin_h, margin_v, captions, hook,
                      caption_style=None, duration=None):
    font_size = max(12, min(200, int(font_size)))
    # libass sizes glyphs by the font's LINE metrics (hhea ascent-descent),
    # while CSS font-size uses the em square - so at the same nominal size
    # libass renders (asc-desc)/upem SMALLER (Heebo: /1.469 ≈ 32% smaller;
    # measured pixel-wise vs Chrome, 2026-07-16 - the "font changes when the
    # preview pauses" report). Scale the ASS Fontsize by the per-family
    # factor so the burn (and the exact preview frame - same builder) renders
    # captions at the size every editor preview shows. Factor = (hhea.ascent
    # - hhea.descent) / unitsPerEm of the family's Google-Fonts TTF; MUST
    # mirror LIBASS_FONT_SCALE in site/app.js; recompute when adding a font.
    # The WRAP estimate (char_w below) stays on the NOMINAL size: with
    # compensation, libass advances equal the CSS advances of the nominal
    # size, which is what char_w models. Defined in-function so the AST test
    # extraction stays self-contained.
    _LIBASS_FONT_SCALE = {
        "Heebo": 1.469, "Assistant": 1.308, "Frank Ruhl Libre": 1.291,
        "Secular One": 1.455, "Rubik": 1.185, "Suez One": 1.306,
        "Karantina": 1.012, "Playpen Sans Hebrew": 1.530, "Miriam Libre": 1.313,
    }
    render_fs = max(12, int(round(font_size * _LIBASS_FONT_SCALE.get(font, 1.0))))
    # Caption styling (hook-parity): text colour, outline colour/width and an
    # optional background box. Defaults reproduce the historical style EXACTLY
    # (white text, black outline 2, no box), so omitting caption_style is a
    # byte-identical no-op for existing callers/tests.
    _cs = caption_style or {}

    def seconds_to_ass(t):
        h = int(t // 3600); m = int((t % 3600) // 60); s = t % 60
        return f"{h}:{m:02d}:{s:05.2f}"

    def _ibgr(hx):
        h = hx.lstrip("#")
        if len(h) == 3:
            h = "".join(ch * 2 for ch in h)
        return f"{int(h[4:6], 16):02X}{int(h[2:4], 16):02X}{int(h[0:2], 16):02X}"

    def hex_to_ass(hex_color: str, alpha_byte: int = 0) -> str:
        h = hex_color.lstrip("#")
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return f"&H{alpha_byte:02X}{b:02X}{g:02X}{r:02X}&"

    def _rewrap_cap(text: str) -> str:
        words = text.replace(r"\N", " ").split()
        if not words:
            return text
        avail = width - 2 * margin_h
        # Measured Heebo advance ~0.46/char (400 weight, incl. spaces). 0.50 packs
        # lines close to full while staying just above the real width, so libass
        # (rendering at the same margins) never re-wraps. 0.60 wrapped ~25% early,
        # leaving the allowed width underfilled.
        char_w = font_size * 0.50
        lines, cur, cur_w = [], [], 0.0
        for word in words:
            ww = len(word) * char_w
            gap = char_w if cur else 0.0
            if cur and cur_w + gap + ww > avail:
                lines.append(" ".join(cur)); cur, cur_w = [word], ww
            else:
                cur.append(word); cur_w += gap + ww
        if cur:
            lines.append(" ".join(cur))
        return r"\N".join(lines) if lines else text

    def _fix_cap_lines(t):
        return r'\N'.join(_fix_rtl_punct(l) for l in t.split(r'\N'))

    def _word_events(c):
        """Word-by-word mode: per-word display cues for one caption line.
        MIRRORED by _wordCuesFor in site/app.js (moving preview + exact-frame
        payload) - keep the redistribution and the merge threshold in sync."""
        text = _censor_caption_text(c["text"]).replace(r"\N", " ")
        toks = [w for w in text.split() if w]
        if not toks:
            return []
        c_start, c_end = float(c["start"]), float(c["end"])
        times = None
        ws = c.get("words")
        # Word timings survive editing only while the token count still
        # matches; otherwise redistribute the line's span across the edited
        # words proportionally to their length.
        if isinstance(ws, list) and len(ws) == len(toks):
            try:
                times = [(float(w[0]), float(w[1])) for w in ws]
            except (TypeError, ValueError, IndexError):
                times = None
        if times is None:
            span = max(0.001, c_end - c_start)
            total = sum(len(t2) for t2 in toks) or 1
            acc, times = c_start, []
            for t2 in toks:
                d = span * len(t2) / total
                times.append((acc, acc + d)); acc += d
        # Display window per word = its start → the NEXT word's start (no
        # flicker gaps inside the line); words shorter than MIN merge with the
        # next so fast speech doesn't strobe.
        MIN_WORD_SECS = 0.15
        cues, i, n = [], 0, len(toks)
        while i < n:
            s0 = times[i][0] if i > 0 else c_start
            j = i
            while True:
                e0 = times[j + 1][0] if j + 1 < n else c_end
                if e0 - s0 >= MIN_WORD_SECS or j + 1 >= n:
                    break
                j += 1
            cues.append({"start": s0, "end": max(e0, s0 + 0.05), "i0": i, "i1": j,
                         "text": _fix_rtl_punct(" ".join(toks[i:j + 1]))})
            i = j + 1
        return cues

    def _karaoke_events(c):
        """Karaoke mode: the full (classic-wrapped) line stays on screen and
        the current word changes COLOR - never weight: bold changes glyph
        widths and re-flows the RTL line per word (jitter). Inline \c tags
        are metric-neutral, so the line is pixel-stable across events. A
        caption carrying hl=[i0,i1] (the exact-preview path - timings can't
        survive the still-frame retime) highlights that token range directly."""
        text = _censor_caption_text(c["text"])
        wrapped = _rewrap_cap(text)
        tok_lines = [ln.split() for ln in wrapped.split(r"\N")]
        hl_tag   = "{\\c&H" + _ibgr(_cs.get("highlight_color", "#FFD400")) + "&}"
        base_tag = "{\\c&H" + _ibgr(_cs.get("font_color", "#FFFFFF")) + "&}"

        def _tagged(i0, i1):
            k = 0
            out = []
            for ln in tok_lines:
                parts = []
                for w in ln:
                    parts.append(hl_tag + w + base_tag if i0 <= k <= i1 else w)
                    k += 1
                out.append(" ".join(parts))
            return r"\N".join(out)

        hl = c.get("hl")
        if isinstance(hl, (list, tuple)) and len(hl) == 2:
            return [{"start": c["start"], "end": c["end"],
                     "text": _tagged(int(hl[0]), int(hl[1]))}]
        return [{"start": q["start"], "end": q["end"],
                 "text": _tagged(q["i0"], q["i1"])} for q in _word_events(c)]

    _cap_mode = str(_cs.get("mode") or "classic")
    if _cap_mode == "word":
        # One event per word: no wrapping, no reflow - the single word renders
        # centered with the exact same style the classic mode uses.
        _cued = []
        for c in captions:
            _cued.extend(_word_events(c))
        captions = _cued
    elif _cap_mode == "karaoke":
        _cued = []
        for c in captions:
            _cued.extend(_karaoke_events(c))
        captions = _cued
    else:
        captions = [{"start": c["start"], "end": c["end"],
                     "text": _fix_cap_lines(_rewrap_cap(_censor_caption_text(c["text"])))}
                    for c in captions]

    hook = hook or {}
    hook_style_line  = ""
    hook_event_lines = ""
    if hook.get("text"):
        def _bgr(hx: str) -> str:
            h = hx.lstrip("#")
            if len(h) == 3:
                h = "".join(c * 2 for c in h)
            r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
            return f"{b:02X}{g:02X}{r:02X}"

        h_font     = hook.get("font", "Heebo")
        h_size_pct = max(50, min(200, int(hook.get("font_size_pct", 100))))
        h_fsize    = max(24, int(min(width, height) * 0.075 * h_size_pct / 100))
        fcol_hex   = hook.get("font_color", "#FFFFFF")
        bgcol_hex  = hook.get("bg_color", "#000000")
        h_bg_alpha = int((1.0 - max(0.0, min(1.0, float(hook.get("bg_opacity", 0.6))))) * 255)
        h_vpos     = max(0, min(100, int(hook.get("vertical_position", 10)))) / 100.0
        h_start    = float(hook.get("start_seconds", 1.0))
        h_dur      = float(hook.get("duration_seconds", 4.0))
        b_size     = max(0, min(20, int(hook.get("border_size", 0))))

        # Side margin: 0.7·fs (was 1.0·fs) so the hook fills more of the width -
        # the box stays inside the frame since padH (0.55·fs) < edge. MUST match
        # the frontend drawHookPreview edgePad so the sent lines fit the box.
        edge  = h_fsize * 0.7
        maxW  = width - 2 * edge
        padH  = round(h_fsize * 0.55)
        padV  = round(h_fsize * 0.35)
        lineH = round(h_fsize * 1.10)

        def _hclean(w: str) -> str:
            return w.strip("،,.-–—;:")

        sent = hook.get("lines")
        if isinstance(sent, list) and any(isinstance(x, str) and x.strip() for x in sent):
            hook_lines = [str(x) for x in sent if str(x).strip()]
        else:
            char_w = h_fsize * 0.50
            raw_words = [_hclean(w) for w in hook["text"].split() if _hclean(w)]
            hook_lines, cur, cur_w = [], [], 0.0
            for word in raw_words:
                ww = len(word) * char_w
                gap = char_w if cur else 0.0
                if cur and cur_w + gap + ww > maxW:
                    hook_lines.append(" ".join(cur)); cur, cur_w = [word], ww
                else:
                    cur.append(word); cur_w += gap + ww
            if cur:
                hook_lines.append(" ".join(cur))
        if not hook_lines:
            hook_lines = [hook["text"]]
        hook_lines = [_fix_rtl_punct(l) for l in hook_lines]

        n_lines = len(hook_lines)
        block_h = n_lines * lineH
        box_w   = maxW + 2 * padH
        box_h   = block_h + 2 * padV
        raw_cy  = edge + (height - 2 * edge) * h_vpos
        cy      = max(box_h / 2 + 4, min(height - box_h / 2 - 4, raw_cy))
        bx      = width / 2 - box_w / 2
        by      = cy - box_h / 2

        if b_size > 0:
            text_bord = max(0.5, h_fsize * b_size * 0.10)
            text_tags = f"\\bord{text_bord:.1f}\\3c&H{_bgr(hook.get('border_color', '#000000'))}&\\shad0"
        else:
            text_bord = round(h_fsize * 0.035, 2)
            text_tags = f"\\bord{text_bord}\\3c&H{_bgr(fcol_hex)}&\\shad0"

        h_primary = hex_to_ass(fcol_hex, 0)
        hook_style_line = (
            f"Style: Hook,{h_font},{h_fsize},{h_primary},&H000000FF,&H00000000,&H00000000,"
            f"0,0,0,0,100,100,0,0,1,0,0,5,0,0,0,1\n"
        )
        bw_i, bh_i = int(round(box_w)), int(round(box_h))
        box_draw = f"m 0 0 l {bw_i} 0 l {bw_i} {bh_i} l 0 {bh_i}"
        box_ev = (
            f"Dialogue: 0,{seconds_to_ass(h_start)},{seconds_to_ass(h_start + h_dur)},Hook,,0,0,0,,"
            f"{{\\an7\\pos({bx:.1f},{by:.1f})\\c&H{_bgr(bgcol_hex)}&\\1a&H{h_bg_alpha:02X}&\\bord0\\shad0\\p1}}{box_draw}\n"
        )
        # Wrap each hook line in an RTL embedding (RLE…PDF) so mixed
        # Hebrew/English + punctuation renders correctly (same fix as captions).
        text_blk = "\\N".join(_RLE + l.replace("{", "\\{").replace("}", "\\}") + _PDF for l in hook_lines)
        text_ev = (
            f"Dialogue: 1,{seconds_to_ass(h_start)},{seconds_to_ass(h_start + h_dur)},Hook,,0,0,0,,"
            f"{{\\an5\\q2\\pos({width / 2:.1f},{cy:.1f})\\c&H{_bgr(fcol_hex)}&{text_tags}}}{text_blk}\n"
        )
        hook_event_lines = box_ev + text_ev

    # Resolve caption style → ASS Style fields. With a background box we use
    # libass BorderStyle=4 (background pad box from BackColour, outline kept);
    # the box pad comes from the Outline value, so guarantee a minimum pad even
    # when the user sets outline width 0.
    _cs_primary     = hex_to_ass(_cs.get("font_color", "#FFFFFF"), 0)[:-1]
    _cs_outline_col = hex_to_ass(_cs.get("border_color", "#000000"), 0)[:-1]
    _cs_outline_w   = max(0, min(8, int(_cs.get("border_size", 2))))
    _cs_bg_op       = max(0.0, min(1.0, float(_cs.get("bg_opacity", 0.0))))
    if _cs_bg_op > 0:
        _cs_border_style = 4
        _cs_back = hex_to_ass(_cs.get("bg_color", "#000000"), int(round((1.0 - _cs_bg_op) * 255)))[:-1]
        _cs_outline_w = max(_cs_outline_w, max(1, round(render_fs * 0.12)))
    else:
        _cs_border_style = 1
        _cs_back = "&H80000000"

    header = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {width}\nPlayResY: {height}\n"
        "WrapStyle: 0\nScaledBorderAndShadow: yes\nYCbCr Matrix: TV.709\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{font},{render_fs},"
        f"{_cs_primary},&H000000FF,{_cs_outline_col},{_cs_back},"
        f"-1,0,0,0,100,100,0,0,{_cs_border_style},{_cs_outline_w},0,2,"
        f"{margin_h},{margin_h},{margin_v},1\n"
        + hook_style_line +
        "\n[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    # Weight parity is REAL bold now (2026-07-17): the images install static
    # Regular+Bold instances (see _FONT_CMDS in pipeline_core) - the same
    # fonts.gstatic files the css2 <link> serves the browser - and Style
    # Bold=-1 resolves the true 700 via fontconfig. The old faux-bold
    # (fill-colored outline layers) overshot real 700 and read as "bulky with
    # a thick border" next to the overlay. Single-weight fonts (Secular One,
    # Suez One) stay regular on BOTH sides (libass doesn't synthesize;
    # #playerCap sets font-synthesis: none).
    # {\q2}: captions are pre-wrapped by _rewrap_cap - libass must NEVER
    # re-wrap them (the char_w estimate can run slightly under the real
    # advance; a reflow would diverge from the preview's line breaks).
    lines = [
        f"Dialogue: 0,{seconds_to_ass(c['start'])},{seconds_to_ass(c['end'])},"
        f"Default,,0,0,0,,{{\\q2}}{_rtl_ass_text(c['text'])}\n"
        for c in captions
    ]

    # Retention progress bar: a thin vector rectangle along the top edge that
    # fills over the video's duration (\fscx 0->100 from a top-left anchor -
    # drawing coords aren't animatable, scale is). The exact-preview path
    # can't ride the animation on a still frame, so it sends progress_frac
    # and gets a static bar at that fill instead. \pos ignores margins.
    pb_events = ""
    if _cs.get("progress_bar"):
        bar_h = max(6, round(height * 0.012))
        pb_col = _ibgr(_cs.get("progress_color", "#C26D4B"))
        frac = _cs.get("progress_frac")
        if frac is not None:
            w = max(1, int(width * max(0.0, min(1.0, float(frac)))))
            draw = f"m 0 0 l {w} 0 l {w} {bar_h} l 0 {bar_h}"
            pb_events = ("Dialogue: 2,0:00:00.00,0:00:09.00,Default,,0,0,0,,"
                         f"{{\\an7\\pos(0,0)\\bord0\\shad0\\p1\\c&H{pb_col}&}}{draw}{{\\p0}}\n")
        elif duration and float(duration) > 0:
            dur = float(duration)
            draw = f"m 0 0 l {width} 0 l {width} {bar_h} l 0 {bar_h}"
            pb_events = (f"Dialogue: 2,0:00:00.00,{seconds_to_ass(dur)},Default,,0,0,0,,"
                         f"{{\\an7\\pos(0,0)\\bord0\\shad0\\p1\\c&H{pb_col}&"
                         f"\\fscx0\\t(0,{int(dur * 1000)},\\fscx100)}}{draw}{{\\p0}}\n")
    return header + "".join(lines) + hook_event_lines + pb_events


# ---------------------------------------------------------------------------
# B-roll "peak moment" window picker
# ---------------------------------------------------------------------------
def _peak_window(path, want_dur):
    """Return (start, end) of the highest-MOTION `want_dur`-second window in a
    stock clip, so the short B-roll shows the peak action instead of a static
    middle/edge. Motion = mean absolute frame-difference luma (via ffmpeg
    tblend+signalstats), sampled at 8fps on a tiny 64px decode (fast — clips are
    short). Best-effort: returns None on any failure so the caller can fall back
    to the pre-computed middle window."""
    import subprocess as _sp, re as _re2
    try:
        pr = _sp.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                      "-of", "default=nw=1:nk=1", str(path)],
                     capture_output=True, text=True, timeout=20)
        total = float((pr.stdout or "0").strip() or 0)
        if total <= 0:
            return None
        if total <= want_dur + 0.1:
            return (0.0, round(min(total, want_dur), 3))
        r = _sp.run(["ffmpeg", "-nostats", "-i", str(path), "-vf",
                     "fps=8,scale=64:-2,tblend=all_mode=difference,signalstats,"
                     "metadata=print:key=lavfi.signalstats.YAVG:file=-",
                     "-an", "-f", "null", "-"],
                    capture_output=True, text=True, timeout=60)
        times, energy, t = [], [], None
        for line in r.stdout.splitlines():
            m = _re2.search(r'pts_time:([0-9.]+)', line)
            if m:
                t = float(m.group(1)); continue
            m = _re2.search(r'YAVG=([0-9.]+)', line)
            if m and t is not None:
                times.append(t); energy.append(float(m.group(1))); t = None
        if len(times) < 4:
            return None
        max_start = total - want_dur
        best_s, best_e, s = 0.0, -1.0, 0.0
        while s <= max_start + 1e-6:
            e = sum(en for ti, en in zip(times, energy) if s <= ti < s + want_dur)
            if e > best_e:
                best_e, best_s = e, s
            s += 0.25
        return (round(best_s, 3), round(best_s + want_dur, 3))
    except Exception as _pe:
        print(f"[peak] window detect failed: {_pe!r}")
        return None


# ---------------------------------------------------------------------------
# Caption-burn worker — CPU only, no GPU needed
# ---------------------------------------------------------------------------
@app.function(image=burn_image, timeout=600, volumes={TMP_DIR: tmp_vol},
              secrets=[modal.Secret.from_name("hebpipe-fcm")])
def burn_captions_fn(video_key: str, captions_json: str, font: str = "Heebo", margin_v_pct: float = 0.08, broll_json: str = "[]", font_size: int = 48, hook_json: str = "", caption_style_json: str = "", source_name: str = "") -> dict:
    import json, subprocess, tempfile, uuid, shutil
    import time as _btime
    from pathlib import Path

    _bt0 = _btime.time()   # wall clock for this burn, recorded as its CPU seconds
    tmp_vol.reload()
    captions = json.loads(captions_json)
    broll_items = json.loads(broll_json) if broll_json else []
    hook = json.loads(hook_json) if hook_json else {}
    caption_style = json.loads(caption_style_json) if caption_style_json else {}

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

        # Captions + hook ASS are built by the shared builder so the editor
        # preview (/preview_frame) is rendered by the identical libass path.
        ass_str = build_caption_ass(width, height, font, font_size, margin_h, margin_v, captions, hook,
                                    caption_style=caption_style,
                                    duration=probe_duration(video_in))
        ass_path.write_text(ass_str, encoding="utf-8")

        # Copy / download selected B-roll clips into temp dir
        import requests as _req_burn
        broll_files = []
        for idx, item in enumerate(broll_items):
            dst = tmp / f"broll_{idx}.mp4"
            vid_key      = item.get("video_key")
            download_url = item.get("download_url") or item.get("preview_url")
            cached = None
            if vid_key:
                # Defense in depth (the /burn route validates too): a broll key
                # must match broll_<uuid>.mp4 — never an arbitrary path.
                if not (isinstance(vid_key, str) and _BROLL_KEY_RE.match(vid_key)):
                    continue
                _c = Path(TMP_DIR) / vid_key
                # A missing cache is not fatal: re-fetch from the stock source
                # below instead of silently dropping the insert (a re-edit from
                # History can outlive the cached clip).
                cached = _c if _c.exists() else None
            if cached is not None:
                shutil.copy(cached, dst)
            elif download_url:
                # Defense in depth: only fetch trusted stock hosts (anti-SSRF).
                if not _is_allowed_broll_url(download_url):
                    continue
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
            # Pick the PEAK-motion window of the clip (want = the B-roll duration),
            # instead of the pre-computed middle. Best-effort: keep the middle on
            # failure. Overrides the window while preserving its length.
            want_dur = (clip_in_end - clip_in_start) if clip_in_end is not None else (end - start)
            if want_dur and want_dur > 0:
                peak = _peak_window(dst, round(want_dur, 3))
                if peak:
                    clip_in_start, clip_in_end = peak
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

        _kp = _UID_PREFIX_RE.match(video_key)
        output_key = (_kp.group(0) if _kp else "") + uuid.uuid4().hex + "_out.mp4"
        out_path = Path(TMP_DIR) / output_key
        shutil.copy(video_out, out_path)
        try:
            # Everything the editor needs to reopen this export (History → edit).
            _record_job(output_key, source_name, out_path, notify=False, edit_state={
                "src_key":       video_key,
                "captions":      captions,
                "hook":          hook,
                "broll":         broll_items,
                "font":          font,
                "font_size":     font_size,
                "margin_v_pct":  margin_v_pct,
                "caption_style": caption_style,
            })
            prune_volume()
        except Exception as _je:
            print(f"[jobs] record/prune skipped: {_je!r}")
        # CPU-side half of the per-video cost (see _cost_summary). A re-burn is
        # quota-free but not compute-free, so it lands as its own entry.
        try:
            costs_store[output_key] = {
                "ts": _btime.time(),
                "uid": _kp.group(0)[1:33] if _kp else "",
                "burn_secs": round(_btime.time() - _bt0, 1),
                "broll": len(broll_items),
            }
        except Exception:
            pass
        tmp_vol.commit()
        return {"output_key": output_key}



# ---------------------------------------------------------------------------
# Job history + volume retention
# ---------------------------------------------------------------------------
def _record_job(output_key, source_name, out_path, notify=True, edit_state=None):
    """Add a burned output to the History manifest.

    `edit_state` (burns only) is everything needed to REOPEN the editor on this
    job later: the cut source key plus the captions/hook/B-roll/styling that
    produced it. Captions are baked into the output permanently, so a re-edit
    burns onto `src_key` again - which is why prune_volume protects that file
    (and any cached B-roll clips) for as long as this record lives.

    `notify=False` for burn results: the push story is capped at two messages
    (upload done + processing done), and a burn is started from inside the
    editor - its completion is announced by the in-app success banner, not a
    third push. Terminal cut-only jobs keep notify=True: for them THIS is the
    "processing done" message (no edit_ready fired)."""
    import time
    duration = 0.0
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(out_path)],
            capture_output=True, text=True, timeout=15)
        duration = float(r.stdout.strip() or 0)
    except Exception:
        pass
    record = {
        "name": source_name or "video",
        "ts": time.time(),
        "size": out_path.stat().st_size,
        "duration": round(duration, 1),
    }
    if edit_state:
        record["edit"] = edit_state
    jobs_store[output_key] = record
    # "Your video is ready" push (best-effort; no-op unless FCM is configured).
    uid = output_key[1:33] if _UID_PREFIX_RE.match(output_key) else None
    if uid and notify:
        _send_fcm(uid, "הסרטון שלך מוכן", "העיבוד הסתיים - הסרטון מוכן להורדה ולשיתוף.",
                  kind="video_ready", tag="hebpipe-job")


def prune_volume():
    """Retention sweep, best-effort: burned outputs past JOB_RETENTION_DAYS and
    scratch files (_src/_cut/chunks) past SCRATCH_RETENTION_HOURS."""
    import time
    from pathlib import Path
    now = time.time()
    # Deferred-spawn bookkeeping: registrations whose upload never finished and
    # consumed done-markers both expire with the 48h scratch window.
    try:
        for key in list(pending_store.keys()):
            meta = pending_store.get(key) or {}
            if now - (meta.get("ts") or 0) > SCRATCH_RETENTION_HOURS * 3600:
                try:
                    pending_store.pop(key)
                except Exception:
                    pass
    except Exception:
        pass
    # Cost records outlive their videos on purpose: pricing is judged over
    # months, the videos are gone in 30 days, and each entry is a few hundred
    # bytes of numbers with no media attached.
    try:
        for key in list(costs_store.keys()):
            rec = costs_store.get(key) or {}
            if now - (rec.get("ts") or 0) > COST_RETENTION_DAYS * 86400:
                try:
                    costs_store.pop(key)
                except Exception:
                    pass
    except Exception:
        pass
    try:
        for key in list(jobs_store.keys()):
            meta = jobs_store.get(key) or {}
            if now - meta.get("ts", 0) > JOB_RETENTION_DAYS * 86400:
                (Path(TMP_DIR) / key).unlink(missing_ok=True)
                (Path(TMP_DIR) / (key + ".jpg")).unlink(missing_ok=True)  # thumbnail cache
                jobs_store.pop(key)
        protected = set(jobs_store.keys())
        # A re-editable job also needs its SOURCE alive: History → edit burns
        # new captions onto the cut, which cannot be re-derived from the burned
        # output. Same for cached B-roll clips it composited. Both are normally
        # scratch (48h), so pin them for as long as the job record lives; they
        # become sweepable again the moment the job expires above.
        for _jk in list(jobs_store.keys()):
            _edit = (jobs_store.get(_jk) or {}).get("edit") or {}
            if _edit.get("src_key"):
                protected.add(_edit["src_key"])
            for _it in (_edit.get("broll") or []):
                if isinstance(_it, dict) and _it.get("video_key"):
                    protected.add(_it["video_key"])
        # Fail-safe: the scratch sweep deletes every unprotected file older than
        # SCRATCH_RETENTION_HOURS, using jobs_store as the sole source of truth
        # for "keep this". If jobs_store is unexpectedly empty (Dict lost /
        # cleared / corrupted) it would wipe EVERY user output past 48h. An empty
        # store while output videos still sit on the volume is that anomaly — skip
        # the sweep and alert rather than risk catastrophic data loss.
        vol_files = list(Path(TMP_DIR).iterdir())
        has_outputs = any(f.name.endswith(("_out.mp4", "_cut.mp4", "_cut.m4a")) for f in vol_files)
        if not protected and has_outputs:
            print("[prune] ABORT scratch sweep: jobs_store empty but output "
                  "videos present on volume — suspected Dict loss, not deleting.")
        else:
            for p in vol_files:
                if p.name in protected or not p.is_file():
                    continue
                # Thumbnail caches live as `<job key>.jpg` — keep them as long as
                # their video is protected.
                if p.name.endswith(".jpg") and p.name[:-4] in protected:
                    continue
                if now - p.stat().st_mtime > SCRATCH_RETENTION_HOURS * 3600:
                    p.unlink(missing_ok=True)
        # Progress entries left behind by crashed jobs
        for key in list(progress_store.keys()):
            entry = progress_store.get(key) or {}
            if now - entry.get("ts", 0) > 6 * 3600:
                progress_store.pop(key)
        # Call-ownership entries past retention
        for cid in list(calls_store.keys()):
            entry = calls_store.get(cid) or {}
            if now - entry.get("ts", 0) > CALL_RETENTION_SECONDS:
                calls_store.pop(cid)
    except Exception as e:
        print(f"[prune] skipped: {e!r}")
