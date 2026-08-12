"""
Hebrew Video Pipeline — shared Modal app, images, volumes, constants,
and pure helpers (security, RTL text, rate limiting, Veo-free).

Imported by every other backend module. No @app.function lives here.
"""


import modal

# ---------------------------------------------------------------------------
# Modal image
# ---------------------------------------------------------------------------
# Static per-weight caption fonts (fonts.gstatic instances, pinned). REAL Bold
# faces matter: libass cannot embolden variable fonts and does NOT synthesize
# bold from a lone Regular - the old `[wght]` variable TTFs rendered every
# "bold" caption at ~400, so the burn/exact frame never matched the browser
# overlay's true 700 ("bulky vs thin" reports, 2026-07-17). With these
# installed, caption Style Bold=-1 resolves the real 700 via fontconfig -
# identical instances to what the css2 <link> serves the browser.
# Single-weight display fonts (Secular One, Suez One) have no bold anywhere;
# the overlay disables synthetic bold (font-synthesis: none) to match.
# Shared by ALL THREE images below - keep it one list.
_FONT_DIR = "/usr/local/share/fonts/hebrew"
_FONT_CMDS = [
    f"mkdir -p {_FONT_DIR}",
    f'wget -q "https://fonts.gstatic.com/s/heebo/v28/NGSpv5_NC0k9P_v6ZUCbLRAHxK1EiSyccg.ttf" -O {_FONT_DIR}/Heebo-Regular.ttf',
    f'wget -q "https://fonts.gstatic.com/s/heebo/v28/NGSpv5_NC0k9P_v6ZUCbLRAHxK1Ebiuccg.ttf" -O {_FONT_DIR}/Heebo-Bold.ttf',
    f'wget -q "https://fonts.gstatic.com/s/assistant/v24/2sDPZGJYnIjSi6H75xkZZE1I0yCmYzzQtuZnEGE.ttf" -O {_FONT_DIR}/Assistant-Regular.ttf',
    f'wget -q "https://fonts.gstatic.com/s/assistant/v24/2sDPZGJYnIjSi6H75xkZZE1I0yCmYzzQtgFgEGE.ttf" -O {_FONT_DIR}/Assistant-Bold.ttf',
    f'wget -q "https://fonts.gstatic.com/s/frankruhllibre/v23/j8_96_fAw7jrcalD7oKYNX0QfAnPcbzNEEB7OoicBw7FYVqQ.ttf" -O {_FONT_DIR}/FrankRuhlLibre-Regular.ttf',
    f'wget -q "https://fonts.gstatic.com/s/frankruhllibre/v23/j8_96_fAw7jrcalD7oKYNX0QfAnPcbzNEEB7OoicBw4iZlqQ.ttf" -O {_FONT_DIR}/FrankRuhlLibre-Bold.ttf',
    f'wget -q "https://fonts.gstatic.com/s/rubik/v31/iJWZBXyIfDnIV5PNhY1KTN7Z-Yh-B4i1UA.ttf" -O {_FONT_DIR}/Rubik-Regular.ttf',
    f'wget -q "https://fonts.gstatic.com/s/rubik/v31/iJWZBXyIfDnIV5PNhY1KTN7Z-Yh-4I-1UA.ttf" -O {_FONT_DIR}/Rubik-Bold.ttf',
    f'wget -q "https://fonts.gstatic.com/s/miriamlibre/v19/DdT0798HsHwubBAqfkcBTL_1a7sPlXcE8PJjH9P3k9s.ttf" -O {_FONT_DIR}/MiriamLibre-Regular.ttf',
    f'wget -q "https://fonts.gstatic.com/s/miriamlibre/v19/DdT0798HsHwubBAqfkcBTL_1a7sPlXcE8PJjHzTwk9s.ttf" -O {_FONT_DIR}/MiriamLibre-Bold.ttf',
    f'wget -q "https://fonts.gstatic.com/s/playpensanshebrew/v8/lJws-okuj29wT-AN6RvLx8QqjkKhL7eAjoL9jK7L4vstDnnp55C6.ttf" -O {_FONT_DIR}/PlaypenSansHebrew-Regular.ttf',
    f'wget -q "https://fonts.gstatic.com/s/playpensanshebrew/v8/lJws-okuj29wT-AN6RvLx8QqjkKhL7eAjoL9jK7L4vstDnkO4JC6.ttf" -O {_FONT_DIR}/PlaypenSansHebrew-Bold.ttf',
    f'wget -q "https://fonts.gstatic.com/s/karantina/v13/buE0po24ccnh31GVMABJ8A.ttf" -O {_FONT_DIR}/Karantina-Regular.ttf',
    f'wget -q "https://fonts.gstatic.com/s/karantina/v13/buExpo24ccnh31GVMABxTC8f-A.ttf" -O {_FONT_DIR}/Karantina-Bold.ttf',
    f'wget -q "https://fonts.gstatic.com/s/secularone/v14/8QINdiTajsj_87rMuMdKypDl.ttf" -O {_FONT_DIR}/SecularOne-Regular.ttf',
    f'wget -q "https://fonts.gstatic.com/s/suezone/v15/taiJGmd_EZ6rqscQgNFJ.ttf" -O {_FONT_DIR}/SuezOne-Regular.ttf',
    f"fc-cache -f {_FONT_DIR}",
]

# YuNet face-detection ONNX (232 KB) for the punch-in zoom's face framing.
# media.githubusercontent resolves opencv_zoo's Git-LFS pointer to the real
# binary (raw.githubusercontent would fetch the pointer text file).
_YUNET_CMD = [
    "mkdir -p /usr/local/share/models",
    'wget -q "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx"'
    " -O /usr/local/share/models/yunet.onnx",
]

image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("ffmpeg", "git", "libsndfile1", "fontconfig", "wget")
    # "video" exposes libnvidia-encode in the container so ffmpeg's h264_nvenc
    # (hardware encoder on the L4) works - the default capability set is
    # compute+utility only. See _nvenc_available() in pipeline_fns.py.
    .env({"NVIDIA_DRIVER_CAPABILITIES": "compute,utility,video"})
    # CUDA torch: needed by the Real-ESRGAN video-upscale pass (fp16 on the L4).
    # DeepFilterNet's Python layer also uses torch (CPU path, CUDA build is a
    # superset); Whisper is unaffected (CTranslate2 brings its own CUDA).
    .pip_install("torch", "torchaudio", extra_options="--index-url https://download.pytorch.org/whl/cu124")
    .pip_install(
        "faster-whisper>=1.1.0",   # ≥1.1: BatchedInferencePipeline (parallel chunk decode)
        "nvidia-cublas-cu12",
        "nvidia-cudnn-cu12",
        "deepfilternet>=0.5",
        "soundfile",
        "requests",
        "fastapi",
        "python-multipart",
        "google-genai>=1.9.0",
        "anthropic>=0.40.0",
        "google-auth",   # FCM "video ready" push (HTTP v1 send)
        # iOS "video ready" push goes straight to APNs (no Firebase in the
        # iOS app): ES256 JWT auth + an HTTP/2 client, since APNs refuses
        # HTTP/1.1 and requests cannot speak HTTP/2. _record_job runs here.
        "pyjwt[crypto]>=2.8.0",
        "httpx[http2]>=0.27.0",
    )
    .run_commands("apt-get install -y fonts-noto-hinted", *_FONT_CMDS)
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
    # opencv-headless: YuNet face detection for the punch-in zoom framing
    .pip_install("requests", "google-auth", "opencv-python-headless",
                 # _record_job pushes from here too - see the note on `image`.
                 "pyjwt[crypto]>=2.8.0", "httpx[http2]>=0.27.0")
    .run_commands(*_FONT_CMDS, *_YUNET_CMD)
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
    .apt_install("ffmpeg", "fontconfig", "wget")
    .pip_install("requests", "anthropic>=0.40.0", "fastapi", "python-multipart", "boto3",
                 "google-auth",              # verify Google Sign-In ID tokens (/auth/google)
                 "pyjwt[crypto]>=2.8.0",     # ES256 bearer for the App Store Server API + APNs
                 "httpx[http2]>=0.27.0",     # APNs is HTTP/2 only - requests cannot reach it
                 "opencv-python-headless")   # face-framed zoom in /preview_frame
    # Hebrew caption/hook fonts so /preview_frame renders the WYSIWYG editor
    # preview through libass with the SAME faces the burn uses (see _FONT_CMDS).
    .run_commands(*_FONT_CMDS, *_YUNET_CMD)
    .add_local_python_source(
        "pipeline_core", "pipeline_fns", "stock_helpers",
        "broll_fns", "content_fns", "metricool_fns",
    )
)

app = modal.App("hebrew-video-pipeline", image=image)

model_volume = modal.Volume.from_name("heb-whisper-model", create_if_missing=True)
MODEL_DIR = "/model-cache"
# Full (non-turbo) ivrit-ai Hebrew Whisper: measurably more accurate than the
# turbo variant on clean audio (turbo is distilled for speed and mis-hears more,
# incl. long-form). Same CT2/faster-whisper format → drop-in. ~2x slower + a
# one-time ~1.5 GB re-download to the model volume; well within the timeouts.
WHISPER_MODEL = "ivrit-ai/whisper-large-v3-ct2"
# Biases decoding toward well-formed, correctly spelled standard Hebrew. Short
# on purpose (Whisper caps the prompt) and domain-neutral so it never injects
# vocabulary that isn't in the audio.
WHISPER_INITIAL_PROMPT = "תמלול בעברית תקנית, במשפטים מלאים ובכתיב מלא ותקין."

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
# in the `hebpipe-auth` Modal Secret (AUTH_SECRET). Signup is open — the
# INVITE_CODE gate was removed 2026-08-01; a verified email is the only gate.
# Migration note: to move to a managed provider (e.g. Supabase), swap
# _verify_token for provider-JWT verification mapping to the same uid.
# ---------------------------------------------------------------------------
users_store = modal.Dict.from_name("hebpipe-users", create_if_missing=True)   # email (legacy: username) → {uid, salt, pw, created}
calls_store = modal.Dict.from_name("hebpipe-calls", create_if_missing=True)   # call_id  → {uid, ts}
quota_store = modal.Dict.from_name("hebpipe-quota", create_if_missing=True)   # f"{uid}:{call_id}" → ts (one unique entry per consumed credit)
purchases_store = modal.Dict.from_name("hebpipe-purchases", create_if_missing=True)  # play:<token hash> → immutable verified credit grant
fcm_store   = modal.Dict.from_name("hebpipe-fcm", create_if_missing=True)     # uid → [device FCM tokens] for "video ready" push notifications
errors_store = modal.Dict.from_name("hebpipe-errors", create_if_missing=True)  # e:{ts}:{uid} → error report (admin alerting + /admin/errors)
costs_store = modal.Dict.from_name("hebpipe-costs", create_if_missing=True)   # output key → per-job compute record (see _cost_summary)
# Deferred processing jobs: full upload key → {params, uid, uname, uprefix,
# total_chunks, ts} registered BEFORE the upload, so the SERVER spawns
# process_video the moment the last byte lands - the app may be closed by then
# (its JS is frozen; a client-side spawn would wait for a reopen). After the
# spawn the entry moves to "done:<key>" → {call_id, uid, ts} for the client.
pending_store = modal.Dict.from_name("hebpipe-pending", create_if_missing=True)
codes_store = modal.Dict.from_name("hebpipe-codes", create_if_missing=True)   # normalized email → {salt, hash, exp, attempts, is_new, terms_ts} for passwordless login


# ── Push token namespacing ────────────────────────────────────────────────────
# One `hebpipe-fcm` Dict holds every device token a user has, across platforms,
# and the two transports are NOT interchangeable: Android registers an FCM
# token, while @capacitor/push-notifications on iOS hands back a raw APNs
# device token (verified in the plugin source - there is no Firebase in it).
# Sending an APNs token to FCM just fails, so iOS tokens are stored with an
# `apns:` prefix and each sender takes only its own.
# Legacy entries are bare strings and are therefore FCM, which is correct.
APNS_TOKEN_PREFIX = "apns:"
APNS_BUNDLE_ID = "com.heb.pipeline"

# Public API base (also hardcoded in site/app.js). Lives here so workers can
# probe their own public routes (readiness-gated completion push).
API_BASE_URL = "https://yotamjacob--hebrew-video-pipeline-api.modal.run"


def _split_push_tokens(tokens):
    """(fcm_tokens, apns_tokens) from one mixed token list."""
    fcm, apns = [], []
    for token in tokens or []:
        if not isinstance(token, str) or not token:
            continue
        if token.startswith(APNS_TOKEN_PREFIX):
            stripped = token[len(APNS_TOKEN_PREFIX):]
            if stripped:
                apns.append(stripped)
        else:
            fcm.append(token)
    return fcm, apns


def _apns_payload(title, body, kind="video_ready"):
    """The APNs body for a pipeline push, mirroring the FCM notification.

    `kind` rides alongside `aps` as a custom key so the tap handler routes the
    same way it does on Android. `content-available` is deliberately absent -
    these are user-visible alerts, not silent background pushes, and claiming
    otherwise would require a background mode we do not declare.
    """
    return {
        "aps": {
            "alert": {"title": title, "body": body},
            "sound": "default",
        },
        "kind": kind,
    }


def _send_apns(uid, title, body, kind="video_ready", tag=None):
    """Best-effort push to a user's iOS devices, straight to APNs.

    No Firebase on the iOS side: the app has no Firebase SDK (deliberately -
    see the includePlugins note in capacitor.config.js), so there is no FCM
    token to send to. Authentication is an ES256 JWT signed with the APNs auth
    key, the same signing scheme the App Store Server API uses.

    Requires APNS_KEY_ID + APNS_TEAM_ID + APNS_PRIVATE_KEY in the hebpipe-apple
    secret; a silent no-op without them, exactly like _send_fcm without its
    service account. Prunes tokens APNs reports as unregistered.
    """
    import os
    import time as _time

    key_id = (os.environ.get("APNS_KEY_ID") or "").strip()
    team_id = (os.environ.get("APNS_TEAM_ID") or "").strip()
    private_key = os.environ.get("APNS_PRIVATE_KEY") or ""
    if not (key_id and team_id and private_key.strip() and uid):
        return
    if "\\n" in private_key and "\n" not in private_key.strip("\n"):
        private_key = private_key.replace("\\n", "\n")
    try:
        all_tokens = fcm_store.get(uid) or []
        _, tokens = _split_push_tokens(all_tokens)
        if not tokens:
            return
        import jwt as _jwt
        import httpx as _httpx

        now = int(_time.time())
        bearer = _jwt.encode({"iss": team_id, "iat": now}, private_key,
                             algorithm="ES256", headers={"kid": key_id})
        headers = {
            "authorization": f"bearer {bearer}",
            "apns-topic": APNS_BUNDLE_ID,
            "apns-push-type": "alert",
            "apns-priority": "10",
        }
        # The Android story is ONE notification slot that updates in place
        # (upload -> processing -> ready). apns-collapse-id is the iOS
        # equivalent, so the same `tag` gives the same behaviour.
        if tag:
            headers["apns-collapse-id"] = str(tag)[:64]
        payload = _apns_payload(title, body, kind)
        dead = []
        # APNs is HTTP/2 only - `requests` cannot talk to it at all, which is
        # why this uses httpx.
        with _httpx.Client(http2=True, timeout=15) as client:
            for token in tokens:
                sent = False
                # A build signed for development gets a sandbox token and a
                # TestFlight/App Store build a production one, with no way to
                # tell them apart from here. Production first, sandbox on the
                # BadDeviceToken that a mismatch produces.
                for host in ("api.push.apple.com", "api.sandbox.push.apple.com"):
                    response = client.post(f"https://{host}/3/device/{token}",
                                           headers=headers, json=payload)
                    if response.status_code == 200:
                        sent = True
                        break
                    reason = ""
                    try:
                        reason = (response.json() or {}).get("reason", "")
                    except Exception:
                        pass
                    if response.status_code == 410 or reason == "Unregistered":
                        break            # genuinely gone; try no other host
                    if reason != "BadDeviceToken":
                        break            # a real error, not an environment mismatch
                if not sent:
                    dead.append(APNS_TOKEN_PREFIX + token)
        if dead:
            fcm_store[uid] = [t for t in all_tokens if t not in dead]
    except Exception as e:
        print(f"[apns] send failed: {e!r}")


def _send_push(uid, title, body, kind="video_ready", tag=None):
    """Fan one pipeline notification out to every platform the user is on."""
    _send_fcm(uid, title, body, kind=kind, tag=tag)
    _send_apns(uid, title, body, kind=kind, tag=tag)


def _send_fcm(uid, title, body, kind="video_ready", tag=None):
    """Best-effort push to a user's devices via FCM HTTP v1.
    `kind` rides in the data payload so the app can route the tap:
    video_ready → History tab; edit_ready/processing → stay on the resumed
    flow. `tag`: Android REPLACES a notification bearing the same tag, so the
    pipeline's pushes ("מעבד…" → "מוכן") occupy ONE notification slot that
    updates in place instead of stacking.
    No-op unless the hebpipe-fcm secret (FCM_SERVICE_ACCOUNT = the Firebase
    service-account JSON) is configured. Prunes tokens the server rejects."""
    import os
    import json as _json
    raw = os.environ.get("FCM_SERVICE_ACCOUNT")
    if not raw or not uid:
        return
    try:
        all_tokens = fcm_store.get(uid) or []
        tokens, _ = _split_push_tokens(all_tokens)   # iOS tokens go to APNs
        if not tokens:
            return
        info = _json.loads(raw)
        project_id = info["project_id"]
        from google.oauth2 import service_account
        from google.auth.transport.requests import Request as _GReq
        creds = service_account.Credentials.from_service_account_info(
            info, scopes=["https://www.googleapis.com/auth/firebase.messaging"])
        creds.refresh(_GReq())
        import requests as _rq
        url = f"https://fcm.googleapis.com/v1/projects/{project_id}/messages:send"
        headers = {"Authorization": f"Bearer {creds.token}"}
        dead = []
        for tkn in tokens:
            r = _rq.post(url, headers=headers, timeout=15, json={"message": {
                "token": tkn,
                "notification": {"title": title, "body": body},
                "data": {"kind": kind},
                # Target the client-created no-vibration channel (user request).
                "android": {"priority": "high",
                            "notification": {"channel_id": "video_ready",
                                             **({"tag": tag} if tag else {})}},
            }})
            if r.status_code in (400, 404):   # unregistered / invalid token
                dead.append(tkn)
        if dead:
            # Prune from the FULL list - `tokens` excludes this user's iOS
            # tokens, and rewriting from it would silently unregister them.
            fcm_store[uid] = [t for t in all_tokens if t not in dead]
    except Exception as e:
        print(f"[fcm] send failed: {e!r}")


# ── Error-report enrichment ───────────────────────────────────────────────────
# An admin push is a headline, not a report. These shape what the headline says
# and what the admin-only Errors panel gets to show. Everything here is pure and
# unit-tested; none of it is ever returned to a non-admin caller.
ERROR_CONTEXT_MAX_KEYS = 24
ERROR_CONTEXT_MAX_CHARS = 2000
ERROR_SIMILAR_WINDOW_S = 3600      # "is this spreading?" looks back one hour


def _sanitize_error_context(raw) -> dict:
    """Bound a client-supplied context blob before it becomes durable state.

    The client is not trusted to be small or well-behaved: cap the key count,
    the nesting, and the total size, and keep only JSON-ish scalars plus the
    two shapes the client actually sends (a flat dict, and the breadcrumb list
    of small dicts).
    """
    import json as _json
    if not isinstance(raw, dict):
        return {}

    def _scalar(v):
        if isinstance(v, bool) or v is None:
            return v
        if isinstance(v, (int, float)):
            return v
        return str(v)[:160]

    out = {}
    for key, value in list(raw.items())[:ERROR_CONTEXT_MAX_KEYS]:
        name = str(key)[:32]
        if isinstance(value, dict):
            out[name] = {str(k)[:32]: _scalar(v)
                         for k, v in list(value.items())[:ERROR_CONTEXT_MAX_KEYS]}
        elif isinstance(value, list):
            items = []
            for entry in value[:20]:
                if isinstance(entry, dict):
                    items.append({str(k)[:16]: _scalar(v)
                                  for k, v in list(entry.items())[:6]})
                else:
                    items.append(_scalar(entry))
            out[name] = items
        else:
            out[name] = _scalar(value)
    # Final belt-and-braces cap: a pathological blob gets dropped rather than
    # stored, since one report must never be able to fill the Dict.
    try:
        if len(_json.dumps(out, ensure_ascii=False)) > ERROR_CONTEXT_MAX_CHARS:
            trimmed = dict(list(out.items())[:8])
            trimmed["_truncated"] = True
            return trimmed
    except Exception:
        return {}
    return out


def _error_signature(stage: str, message: str) -> str:
    """What counts as "the same error" for repeat-counting.

    Digits are collapsed so that ids, sizes and timestamps inside a message do
    not make every occurrence look unique.
    """
    text = _re.sub(r"\d+", "#", str(message or ""))[:120]
    return f"{str(stage or '?')[:40]}|{text}".lower()


def _count_similar_errors(store, stage: str, message: str, now: float) -> int:
    """How many look-alike reports landed in the last hour, this one included."""
    target = _error_signature(stage, message)
    total = 0
    try:
        for key in store.keys():
            if not isinstance(key, str) or not key.startswith("e:"):
                continue
            rec = store.get(key)
            if not isinstance(rec, dict):
                continue
            if float(rec.get("ts", 0) or 0) < now - ERROR_SIMILAR_WINDOW_S:
                continue
            if _error_signature(rec.get("stage"), rec.get("message")) == target:
                total += 1
    except Exception:
        return max(1, total)
    return max(1, total)


def _error_alert_title(stage: str, seen: int = 1) -> str:
    """A repeat gets a louder headline - that is the one worth waking up for."""
    if seen >= 3:
        return f"שגיאה חוזרת ({seen}) - {str(stage or '?')[:24]}"
    return f"שגיאה אצל משתמש - {str(stage or '?')[:24]}"


def _error_alert_body(user: str, stage: str, version: str, message: str,
                      context: dict = None, seen: int = 1) -> str:
    """Everything that fits in a notification, ordered by what is acted on first.

    Line 1 identifies WHO and on WHAT (platform/version), line 2 the failing
    job, line 3 the message itself. The full context lives in the Errors panel;
    this only has to be enough to decide whether to get out of bed.
    """
    context = context or {}
    who = str(user or "?")[:48]
    platform = str(context.get("platform") or "").strip()
    where = " ".join(part for part in (platform, str(version or "").strip()) if part)
    lines = [f"{who}{(' · ' + where) if where else ''}"]

    job_bits = []
    duration = context.get("duration")
    if isinstance(duration, (int, float)) and duration:
        job_bits.append(f"{int(duration) // 60}:{int(duration) % 60:02d}")
    options = context.get("options")
    if isinstance(options, dict):
        enabled = [name for name, flag in (("cut", options.get("cut")),
                                           ("captions", options.get("captions")),
                                           ("broll", options.get("broll")),
                                           ("hook", options.get("hook")))
                   if flag is True]
        enhance = options.get("enhance_video")
        if enhance and enhance != "none":
            enabled.append(str(enhance)[:12])
        if enabled:
            job_bits.append("+".join(enabled))
    if context.get("online") is False:
        job_bits.append("offline")
    net = context.get("net")
    if net:
        job_bits.append(str(net)[:8])
    if job_bits:
        lines.append(" · ".join(job_bits))

    lines.append(str(message or "")[:160])
    if seen >= 3:
        lines.append(f"({seen} כאלה בשעה האחרונה)")
    return "\n".join(lines)


def _alert_admins(title, body):
    """Best-effort FCM push to every ADMIN's devices - the immediate "a user
    hit an error" signal on the owner's phone. Admins = records with
    role=='admin' or usernames in the ADMIN_USERS env (hebpipe-auth secret).
    Requires the hebpipe-fcm secret on the calling function; silent no-op
    without it. Never raises."""
    import os
    try:
        admin_names = {u.strip().lower()
                       for u in (os.environ.get("ADMIN_USERS") or "").split(",") if u.strip()}
        for k in users_store.keys():
            if k.startswith("uid:") or k.startswith("email:"):
                continue
            r = users_store.get(k)
            if not isinstance(r, dict):
                continue
            if r.get("role") == "admin" or k.lower() in admin_names:
                _send_push(r.get("uid"), title, body, kind="admin_alert")
    except Exception as e:
        print(f"[alert] admin push failed: {e!r}")

import re as _auth_re

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


# ── Scoped one-time tokens (email verification, password reset) ──
# Same HMAC construction as the media token but with a purpose scope baked into
# the signed message, so a verify token can't be replayed as a reset token (or
# vice-versa) and neither is accepted where a session token is expected.
EMAIL_VERIFY_TTL_SECONDS   = 7 * 24 * 3600   # a week to click "verify"
PASSWORD_RESET_TTL_SECONDS = 3600            # reset links lapse in an hour


def _sign_scoped_token(uid: str, scope: str, secret: str, ttl: int, now: float = None) -> str:
    import hmac, hashlib, time
    exp = int((now if now is not None else time.time()) + ttl)
    msg = f"{scope}.{uid}.{exp}"
    sig = hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()
    return f"{msg}.{sig}"


def _verify_scoped_token(token: str, scope: str, secret: str, now: float = None):
    """Return uid for a valid, unexpired token of exactly this scope — else None."""
    import hmac, hashlib, time, re
    try:
        sc, uid, exp_s, sig = token.split(".")
        if sc != scope:
            return None
        expect = hmac.new(secret.encode(), f"{sc}.{uid}.{exp_s}".encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expect):
            return None
        if (now if now is not None else time.time()) > int(exp_s):
            return None
        if not re.match(r"^[0-9a-f]{32}$", uid):
            return None
        return uid
    except Exception:
        return None


_EMAIL_RE = _auth_re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _normalize_email(email: str) -> str:
    """Canonical form used as the ACCOUNT KEY, so aliases that reach the same
    inbox collapse to one account (anti multi-signup / free-credit farming).
    Lowercases + trims, and for Gmail/Googlemail strips dots and any '+tag' in
    the local part (both are ignored by Gmail delivery). Non-Gmail hosts keep
    their local part intact (many providers DO treat dots as significant)."""
    email = (email or "").strip().lower()
    if "@" not in email:
        return email
    local, _, domain = email.partition("@")
    local = local.split("+", 1)[0]
    if domain in ("gmail.com", "googlemail.com"):
        local = local.replace(".", "")
        domain = "gmail.com"   # googlemail is an alias of gmail
    return f"{local}@{domain}"


def _gen_login_code() -> str:
    """A 6-digit numeric login code (leading zeros preserved)."""
    import secrets
    return f"{secrets.randbelow(1_000_000):06d}"


# ── Play-review demo login ──
# Sign-in is passwordless (Google, or a 6-digit code we email), and a Google
# Play reviewer can read neither — which is why the app came back as "not
# ready". ONE address, configured as REVIEW_EMAIL + REVIEW_CODE in the
# hebpipe-auth secret, may therefore sign in with a FIXED code instead of an
# emailed one. It is a normal user record and must NEVER be an admin (the Admin
# tab exposes every user's email). Rotate or delete the pair once Play has
# granted production access. Both helpers are no-ops when the secret is unset,
# so a deploy without it simply has no demo login.
REVIEW_VIDEO_LIMIT = 25   # credits kept AVAILABLE to the reviewer on every login


def _is_review_email(email: str, review_email: str) -> bool:
    """True when `email` is the configured Play-review address."""
    if not review_email or not email:
        return False
    return _normalize_email(email) == _normalize_email(review_email)


def _review_login_ok(email: str, code: str, review_email: str, review_code: str) -> bool:
    """True when (email, code) is the configured Play-review demo login.

    The code compare is constant-time, and a short/blank REVIEW_CODE is refused
    outright so a half-configured secret can never become a trivial bypass."""
    import hmac as _hmac
    if not review_email or not review_code or not code:
        return False
    review_code = review_code.strip()
    if len(review_code) < 6:
        return False
    if not _is_review_email(email, review_email):
        return False
    return _hmac.compare_digest(code.strip(), review_code)


# Google Sign-In: the OAuth **Web** client ID is the token audience. It is PUBLIC
# (it ships inside the Android APK and the web page), so hardcoding is fine and
# avoids a risky --force rewrite of the hebpipe-auth secret. The native plugin
# uses this same value as its serverClientId so the ID token's aud matches.
GOOGLE_WEB_CLIENT_ID = "229326610541-4870rqhqu4sckii6rv15sjm3bqogeku1.apps.googleusercontent.com"


def _verify_google_id_token(token: str):
    """Verify a Google Sign-In ID token and return its claims dict, else None.
    Checks the RS256 signature against Google's certs, the audience (our Web
    client ID), issuer and expiry. Best-effort: any failure returns None so the
    route can answer a clean 401 instead of 500-ing."""
    if not token:
        return None
    try:
        from google.oauth2 import id_token as _gid
        from google.auth.transport import requests as _greq
        claims = _gid.verify_oauth2_token(token, _greq.Request(), GOOGLE_WEB_CLIENT_ID)
        if claims.get("iss") not in ("accounts.google.com", "https://accounts.google.com"):
            return None
        if not claims.get("email") or not claims.get("email_verified"):
            return None
        return claims
    except Exception as e:
        print(f"[google] id-token verify failed: {e}")
        return None


def _send_email(to: str, subject: str, html: str) -> bool:
    """Send one transactional email via Resend. Best-effort: returns False and
    logs on any failure (a missing RESEND_API_KEY, network error, etc.) so
    callers never fail a request just because mail didn't go out."""
    import os, json, urllib.request
    api_key = os.environ.get("RESEND_API_KEY")
    if not api_key:
        print("[email] RESEND_API_KEY not set — skipping send")
        return False
    frm = os.environ.get("EMAIL_FROM", "Pipeline <onboarding@resend.dev>")
    body = json.dumps({"from": frm, "to": [to], "subject": subject, "html": html}).encode()
    req = urllib.request.Request(
        "https://api.resend.com/emails", data=body,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json",
                 # A real User-Agent is REQUIRED: api.resend.com sits behind
                 # Cloudflare, which blocks urllib's default UA with a 1010.
                 "User-Agent": "hebrew-video-pipeline/1.0", "Accept": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=15).read()
        return True
    except Exception as e:
        detail = ""
        try:
            detail = " — " + e.read().decode()[:200]   # HTTPError carries the API message
        except Exception:
            pass
        print(f"[email] send failed: {e}{detail}")
        return False


def _email_html(title: str, body_line: str, button_label: str, url: str) -> str:
    """Small branded HTML wrapper shared by verification + reset + digest
    emails. The body slot is a <div>, not a <p>: the digest passes block-level
    section markup, and a <p> would auto-close in front of the first inner
    <div> and break the layout."""
    return (
        "<div style='font-family:system-ui,-apple-system,Segoe UI,Arial,sans-serif;"
        "background:#F2EEF8;padding:32px;text-align:center'>"
        "<div style='max-width:440px;margin:0 auto;background:#fff;border:1.5px solid #EDE9FE;"
        "border-radius:20px;padding:32px 28px'>"
        "<div style='font-weight:800;font-size:20px;color:#6D28D9;margin-bottom:14px'>פייפליין</div>"
        f"<h2 style='color:#1E1033;font-size:19px;margin:0 0 10px'>{title}</h2>"
        f"<div style='color:#6B7080;font-size:14px;line-height:1.6;margin:0 0 22px'>{body_line}</div>"
        f"<a href='{url}' style='display:inline-block;background:#7C3AED;color:#fff;text-decoration:none;"
        f"font-weight:700;font-size:15px;padding:12px 26px;border-radius:999px'>{button_label}</a>"
        f"<p style='color:#9AA0AC;font-size:12px;margin:22px 0 0;word-break:break-all'>{url}</p>"
        "</div></div>"
    )


def _user_prefix(uid: str) -> str:
    return f"u{uid}__"


def _owned_key(key: str, uid: str) -> bool:
    """True iff key belongs to uid. The only path to a user's data."""
    return isinstance(key, str) and key.startswith(_user_prefix(uid))


# ── Video quota (free tier) ──
# Each /process run consumes one credit. Admins are unlimited; a user is an
# admin if their username is in the ADMIN_USERS env var (comma-separated, in
# the hebpipe-auth secret) OR their record has role == "admin".
DEFAULT_VIDEO_LIMIT = 3
# Records created before the free tier moved to 3 have no explicit video_limit,
# so the FALLBACK in _quota_state must stay at the old value - lowering it would
# retroactively take credits from existing users (and lock out anyone already
# past 3). New records always get an explicit video_limit, so nothing else is
# needed to grandfather them and no data migration is required.
LEGACY_VIDEO_LIMIT = 5

# What one credit buys. A credit is sold per video, but the compute behind one
# varies enormously with length and with the 4K upscale, so both carry a
# surcharge instead of being absorbed silently.
CREDIT_SECONDS = 600         # one credit covers this much source (10 min)
UPSCALE_CREDIT_COST = 1      # the 4K upscale costs this much on top
# Above this the upscale is refused outright: at 15-30x realtime on an L4 it
# would exceed process_video's 1800s timeout, and a timeout refunds the credit
# AFTER the GPU time was already spent - we would pay and collect nothing.
UPSCALE_MAX_SECONDS = 180
# Clients report duration as a float; a hair over the boundary must not silently
# double the price of a video the user believes is exactly 10 minutes.
CREDIT_DURATION_TOLERANCE = 5.0


def _credit_cost(duration: float = 0.0, enhance_video: str = "none") -> int:
    """Credits consumed by one /process spawn."""
    n = 1
    try:
        dur = float(duration or 0)
    except (TypeError, ValueError):
        dur = 0.0
    if dur > CREDIT_SECONDS + CREDIT_DURATION_TOLERANCE:
        n += 1
    if enhance_video == "esrgan":
        n += UPSCALE_CREDIT_COST
    return n


def _upscale_allowed(duration: float = 0.0) -> bool:
    """False when a 4K upscale of this source would outrun the job timeout."""
    try:
        dur = float(duration or 0)
    except (TypeError, ValueError):
        return True          # unknown duration: let the worker's probe decide
    return dur <= UPSCALE_MAX_SECONDS + CREDIT_DURATION_TOLERANCE


def _charge_credits(store, uid: str, call_id: str, n: int = 1) -> int:
    """Write one unique quota key per consumed credit. Returns the count written.

    The first key keeps the historical `{uid}:{call_id}` form so existing
    records and the refund path are unaffected; extras are suffixed. Unique
    keys (rather than a mutable counter) are what makes concurrent spawns
    race-proof - see _count_quota_used."""
    import time as _t
    written = 0
    for i in range(max(1, int(n or 1))):
        key = f"{uid}:{call_id}" if i == 0 else f"{uid}:{call_id}#{i + 1}"
        try:
            store[key] = _t.time()
            written += 1
        except Exception:
            pass
    return written


def _refund_credits(store, uid: str, call_id: str, max_n: int = 4) -> int:
    """Delete every credit key for a call. Returns how many were removed.

    Idempotent: a repeat call finds them gone and removes nothing, so
    concurrent failure polls can't double-refund."""
    removed = 0
    for i in range(max(1, int(max_n or 1))):
        key = f"{uid}:{call_id}" if i == 0 else f"{uid}:{call_id}#{i + 1}"
        try:
            del store[key]
            removed += 1
        except Exception:
            pass
    return removed


PLAY_PACKAGE_NAME = "com.heb.pipeline"
# The credit map is FORWARD-ONLY: each grant stores its credit count at
# purchase time (see _count_purchased_credits), so editing this never revalues
# a past purchase. Retired IDs must stay listed for as long as the product is
# still live in Play, or a purchase of it would grant nothing.
PLAY_CREDIT_PRODUCTS = {
    "pipeline_credits_10": 10,
    "pipeline_credits_30": 30,
    "pipeline_credits_100": 100,
    # Retired 2026-07-31 (replaced by the 10/30/100 ladder), still honoured.
    "pipeline_credits_5": 5,
    "pipeline_credits_20": 20,
    "pipeline_credits_50": 50,
}


# ── Apple In-App Purchase (StoreKit 2 consumables) ────────────────────────────
# Apple's App Store equivalent of the Play ladder above. Same forward-only rule:
# a grant stores its credit count at purchase time, so editing this map never
# revalues a past purchase. Product IDs are per-app on Apple, so they can (and
# do) reuse the Play names. There are no retired Apple IDs yet - the iOS app
# launched straight on the 10/30/100 ladder.
APPLE_BUNDLE_ID = "com.heb.pipeline"
APPLE_CREDIT_PRODUCTS = {
    "pipeline_credits_10": 10,
    "pipeline_credits_30": 30,
    "pipeline_credits_100": 100,
}
# StoreKit transaction IDs are decimal strings. Validated before they become a
# durable Dict key so a hostile client cannot write outside the apple: namespace.
_APPLE_TXN_RE = _auth_re.compile(r"^[0-9]{1,32}$")


def _apple_account_token(uid: str) -> str:
    """Opaque, stable `appAccountToken` binding for StoreKit purchases.

    StoreKit requires a UUID (unlike Play's free-form obfuscated account id), so
    the sha256 of the uid is folded into UUID shape. Deterministic: the same
    account always produces the same token, which is what lets the server prove
    a transaction belongs to the signed-in user.
    """
    import hashlib
    digest = hashlib.sha256(f"hebpipe-apple:{uid}".encode("utf-8")).hexdigest()
    return "-".join((digest[0:8], digest[8:12], digest[12:16],
                     digest[16:20], digest[20:32]))


def _apple_ledger_key(transaction_id: str) -> str:
    """Durable ledger key for an Apple transaction.

    The Play key hashes its token because a Play purchase token is a bearer
    credential; a StoreKit transaction id is not secret, so it is stored as-is
    (after `_APPLE_TXN_RE`) - easier to trace back to an App Store Connect
    order when a user disputes a grant.
    """
    txn = str(transaction_id or "")
    if not _APPLE_TXN_RE.match(txn):
        raise ValueError("invalid_purchase")
    return "apple:" + txn


def _decode_jws_payload(jws: str) -> dict:
    """Decode the payload of an Apple JWS WITHOUT verifying its signature.

    Only ever called on bytes the App Store Server API returned to us over TLS
    (`/billing/apple/verify`) or on a notification payload that is immediately
    re-fetched from that same API before anything is granted or revoked
    (`/billing/apple/notify`). Apple's response IS the authority here, exactly
    like the Play flow trusts ProductPurchaseV2 - so no x5c chain validation is
    needed and none is implied. Never call this on client-supplied bytes and
    act on the result.
    """
    import base64
    import json as _json
    parts = str(jws or "").split(".")
    if len(parts) != 3:
        raise ValueError("invalid_purchase")
    segment = parts[1]
    segment += "=" * (-len(segment) % 4)
    try:
        payload = _json.loads(base64.urlsafe_b64decode(segment.encode("ascii")))
    except Exception:
        raise ValueError("invalid_purchase")
    if not isinstance(payload, dict):
        raise ValueError("invalid_purchase")
    return payload


def _parse_apple_transaction(payload: dict, expected_products,
                             expected_account_token: str) -> dict:
    """Validate a decoded JWSTransaction payload and return grant data.

    Raises ValueError with the same vocabulary as `_parse_play_purchase` so the
    route can map both providers onto one set of client error codes.
    """
    if not isinstance(payload, dict):
        raise ValueError("invalid_purchase")
    if payload.get("bundleId") != APPLE_BUNDLE_ID:
        raise ValueError("purchase_bundle_mismatch")
    product_id = payload.get("productId")
    if product_id not in (expected_products or {}):
        raise ValueError("purchase_product_mismatch")
    # Credit packs are consumables. Anything else reaching this route means the
    # client sent a transaction from a product we never sold as credits.
    if payload.get("type") not in (None, "Consumable"):
        raise ValueError("purchase_product_mismatch")
    # A refunded/revoked transaction must never grant, even on a first sight of
    # it (a slow client retry can arrive after the refund).
    if payload.get("revocationDate"):
        raise ValueError("purchase_refunded")
    token = str(payload.get("appAccountToken") or "").lower()
    if token != str(expected_account_token or "").lower():
        raise ValueError("purchase_account_mismatch")
    quantity = max(1, min(100, int(payload.get("quantity", 1) or 1)))
    return {
        "product_id": product_id,
        "quantity": quantity,
        "transaction_id": str(payload.get("transactionId") or "")[:64],
        "order_id": str(payload.get("originalTransactionId") or "")[:64],
        "region": str(payload.get("storefront") or "")[:8],
        "test": str(payload.get("environment") or "") == "Sandbox",
    }


def _revoke_purchase_grant(store, ledger_key: str, now: float,
                           reason: str = "") -> bool:
    """Mark one grant revoked (Apple refund notification). Idempotent."""
    rec = store.get(ledger_key)
    if not isinstance(rec, dict) or rec.get("state") != "granted":
        return False
    updated = dict(rec)
    updated.update({"state": "revoked", "revoked_ts": now,
                    "voided_reason": str(reason or "")[:64]})
    store[ledger_key] = updated
    return True


def _quota_state(rec: dict, admin_users: str = None, username: str = None,
                 purchased_credits: int = 0):
    """(is_admin, used, limit) for a user record. limit -1 = unlimited."""
    admins = {u.strip().lower() for u in (admin_users or "").split(",") if u.strip()}
    is_admin = bool(username and username.lower() in admins) or (rec or {}).get("role") == "admin"
    used = int((rec or {}).get("videos_used", 0) or 0)
    limit = (rec or {}).get("video_limit", LEGACY_VIDEO_LIMIT)
    limit = -1 if limit is None else int(limit)
    if limit >= 0:
        limit += max(0, int(purchased_credits or 0))
    return is_admin, used, limit


def _quota_allows(is_admin: bool, used: int, limit: int) -> bool:
    return is_admin or limit < 0 or used < limit


def _count_quota_used(store, uid: str) -> int:
    """Authoritative consumed-credit count for a uid.

    Each /process spawn writes a unique `{uid}:{call_id}` key, so counting
    them is immune to the lost-update race a single mutable `videos_used`
    counter suffers when concurrent /process calls read-modify-write it (they
    would all read the same value and clobber each other, yielding many videos
    for one credit). Per-user entries are bounded by the limit, so the scan
    stays small."""
    prefix = f"{uid}:"
    try:
        return sum(1 for k in store.keys() if isinstance(k, str) and k.startswith(prefix))
    except Exception:
        return 0


def _billing_account_id(uid: str) -> str:
    """Opaque, stable Play Billing account binding (never exposes the uid)."""
    import hashlib
    return hashlib.sha256(f"hebpipe-play:{uid}".encode("utf-8")).hexdigest()


def _purchase_ledger_key(purchase_token: str) -> str:
    """Hash the Play token before using it as a durable ledger key."""
    import hashlib
    return "play:" + hashlib.sha256(purchase_token.encode("utf-8")).hexdigest()


# Every durable purchase grant lives under one of these ledger namespaces.
# A uid's credits are the sum across ALL of them: a user who bought on Android
# and then installs the iOS app keeps the credits they already paid for.
PURCHASE_KEY_PREFIXES = ("play:", "apple:")


def _count_purchased_credits(store, uid: str) -> int:
    """Authoritative total from unique, server-verified purchase grants."""
    total = 0
    try:
        for key in store.keys():
            if not isinstance(key, str) or not key.startswith(PURCHASE_KEY_PREFIXES):
                continue
            rec = store.get(key)
            if isinstance(rec, dict) and rec.get("uid") == uid and rec.get("state") == "granted":
                total += max(0, int(rec.get("credits", 0) or 0))
    except Exception:
        return total
    return total


def _apply_voided_play_purchases(store, voided_purchases: list,
                                 now: float) -> int:
    """Revoke grants matched by Play's refund/chargeback purchase tokens."""
    revoked = 0
    for voided in voided_purchases or []:
        token = voided.get("purchaseToken") if isinstance(voided, dict) else None
        if not token:
            continue
        key = _purchase_ledger_key(str(token))
        rec = store.get(key)
        if not isinstance(rec, dict) or rec.get("state") != "granted":
            continue
        updated = dict(rec)
        updated.update({
            "state": "revoked",
            "revoked_ts": now,
            "voided_reason": voided.get("voidedReason"),
            "voided_source": voided.get("voidedSource"),
        })
        store[key] = updated
        revoked += 1
    return revoked


def _parse_play_purchase(payload: dict, expected_product: str,
                         expected_account_id: str) -> dict:
    """Validate a ProductPurchaseV2 response and return normalized grant data."""
    if not isinstance(payload, dict):
        raise ValueError("invalid_purchase")
    state = (payload.get("purchaseStateContext") or {}).get("purchaseState")
    if state != "PURCHASED":
        raise ValueError("purchase_pending" if state == "PENDING" else "purchase_not_completed")
    if payload.get("obfuscatedExternalAccountId") != expected_account_id:
        raise ValueError("purchase_account_mismatch")
    items = payload.get("productLineItem") or []
    item = next((line for line in items
                 if isinstance(line, dict) and line.get("productId") == expected_product), None)
    if not item:
        raise ValueError("purchase_product_mismatch")
    # ProductPurchaseV2 nests quantity under productOfferDetails (not directly
    # on ProductLineItem). Multi-quantity is not enabled in the Android flow,
    # but validating it here keeps the server correct if it is enabled later.
    offer = item.get("productOfferDetails") or {}
    quantity = max(1, min(100, int(offer.get("quantity", 1) or 1)))
    return {
        "product_id": expected_product,
        "quantity": quantity,
        "order_id": str(payload.get("orderId") or "")[:160],
        "region": str(payload.get("regionCode") or "")[:8],
        "test": bool(payload.get("testPurchaseContext")),
    }


def _usage_since(quota_store, since_ts: float):
    """(video_count, distinct_user_count) for credits consumed since since_ts.
    Backs the daily GPU-spend digest. Each quota key is `{uid}:{call_id}` → ts."""
    n, uids = 0, set()
    try:
        for k in quota_store.keys():
            if not isinstance(k, str) or ":" not in k:
                continue
            ts = quota_store.get(k)
            if isinstance(ts, (int, float)) and ts >= since_ts:
                n += 1
                uids.add(k.split(":", 1)[0])
    except Exception:
        pass
    return n, len(uids)

# ── Per-job compute cost ────────────────────────────────────────────────────
# Credits are priced per VIDEO while the compute behind one varies by ~40x with
# length and by an order of magnitude with the 4K upscale, so the margin on a
# credit was never measured - only estimated. Every job now records what it
# actually burned, and the daily digest turns that into dollars.
# Rates are Modal list prices (2026-07): L4 $0.000222/s, CPU $0.0000131/core/s,
# memory $0.00000222/GiB/s. process_video holds an L4 with 4 GiB + 8 cores for
# its whole run; burn_captions_fn is CPU-only with 8 cores (2026-08-09: both
# request cpu=8 so the ffmpeg x264 encodes get guaranteed threads instead of
# the 0.125-core default - pennies per job, roughly halves encode wall time).
# Both are approximations of the billed amount (container startup and idle are
# not attributed here), so treat the output as a floor for comparing jobs, not
# as an invoice.
COST_RETENTION_DAYS = 90
GPU_USD_PER_SEC = 0.000222 + 0.00000222 * 4 + 0.0000131 * 8
CPU_USD_PER_SEC = 0.0000131 * 8 + 0.00000222 * 4


# YuNet face-detection model, baked into burn_image + light_image (see
# _YUNET_CMD). OpenCV 5 removed the legacy Haar CascadeClassifier, so the
# modern ONNX detector is the only (and better) option.
_YUNET_MODEL_PATH = "/usr/local/share/models/yunet.onnx"


def _face_center_from_image(img_path):
    """Best-effort face center for the auto punch-in zoom, as (fx, fy)
    fractions of the frame. YuNet (cv2.FaceDetectorYN, CPU, ~50ms on a
    downscaled frame); the LARGEST face wins (the speaker in a talking-head
    video). Returns None when no face is found, cv2 is missing, or the model
    file is absent - callers fall back to the fixed upper-third framing
    (0.5, 0.30). Fractions are clamped away from the edges so the crop
    window never pins to a corner."""
    try:
        import cv2, os
        model = os.environ.get("YUNET_MODEL", _YUNET_MODEL_PATH)
        if not os.path.exists(model):
            return None
        img = cv2.imread(str(img_path))
        if img is None:
            return None
        h, w = img.shape[:2]
        scale = 480.0 / max(w, h) if max(w, h) > 480 else 1.0
        small = cv2.resize(img, (max(1, int(w * scale)), max(1, int(h * scale)))) if scale < 1.0 else img
        sh, sw = small.shape[:2]
        det = cv2.FaceDetectorYN.create(model, "", (sw, sh), score_threshold=0.6)
        _, faces = det.detect(small)
        if faces is None or len(faces) == 0:
            return None
        x, y, fw, fh = max(faces, key=lambda f: float(f[2]) * float(f[3]))[:4]
        fx = (x + fw / 2.0) / sw
        fy = (y + fh / 2.0) / sh
        return (min(0.9, max(0.1, float(fx))), min(0.85, max(0.1, float(fy))))
    except Exception:
        return None


# Anthropic list prices per MTok (input, output). Cache reads bill at 0.1x
# input, cache writes at 1.25x. Matched by model-id PREFIX so dated ids
# (claude-haiku-4-5-20251001) hit their family row. Dashboard-only figures -
# update when models change; unknown models assume Sonnet-tier.
AI_USD_PER_MTOK = {
    "claude-sonnet-5": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
}


def _ai_usd(model, tin, tout, cache_read=0, cache_write=0):
    rates = next((r for p, r in AI_USD_PER_MTOK.items() if str(model).startswith(p)),
                 (3.0, 15.0))
    rin, rout = rates
    return (tin * rin + cache_read * rin * 0.1 + cache_write * rin * 1.25
            + tout * rout) / 1_000_000.0


def _record_ai_spend(costs_store, kind, model, usage):
    """Ledger entry for ONE Anthropic API call (feeds the admin Costs tab -
    per-video compute alone understated the true cost since AI spend is not
    tied to a video key). `usage` is the SDK response usage object.
    Best-effort: a metering failure must never fail the feature call."""
    try:
        import time as _t
        tin = int(getattr(usage, "input_tokens", 0) or 0)
        tout = int(getattr(usage, "output_tokens", 0) or 0)
        cr = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
        cw = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
        costs_store[f"ai:{int(_t.time() * 1000)}:{kind}"] = {
            "ts": _t.time(), "kind": str(kind)[:24], "model": str(model)[:48],
            "tin": tin, "tout": tout, "cr": cr, "cw": cw,
            "usd": round(_ai_usd(model, tin, tout, cr, cw), 6),
        }
    except Exception:
        pass


def _cost_summary(costs_store, since_ts: float):
    """Aggregate recorded job costs since `since_ts`.

    Each entry is `{ts, uid, dur, enhance, gpu_secs}` (process_video) or
    `{ts, uid, burn_secs, broll}` (burn_captions_fn). Returns totals plus a
    per-enhance-mode breakdown, so the cost of the 4K upscale option is
    visible next to a plain run rather than buried in the average."""
    out = {"videos": 0, "burns": 0, "gpu_secs": 0.0, "cpu_secs": 0.0,
           "src_secs": 0.0, "broll_jobs": 0, "by_mode": {}, "usd": 0.0,
           "ai_usd": 0.0, "ai_calls": 0, "ai_tokens": 0, "ai_by_kind": {}}
    try:
        keys = list(costs_store.keys())
    except Exception:
        return out
    for k in keys:
        try:
            rec = costs_store.get(k)
        except Exception:
            continue
        if not isinstance(rec, dict):
            continue
        ts = rec.get("ts")
        if not isinstance(ts, (int, float)) or ts < since_ts:
            continue
        # AI-call ledger entries (see _record_ai_spend) aggregate separately -
        # they carry no video key and must never count as a "video".
        if isinstance(k, str) and k.startswith("ai:"):
            usd = float(rec.get("usd") or 0)
            out["ai_calls"] += 1
            out["ai_usd"] += usd
            out["ai_tokens"] += int(rec.get("tin") or 0) + int(rec.get("tout") or 0)
            kk = out["ai_by_kind"].setdefault(rec.get("kind") or "?", {"n": 0, "usd": 0.0})
            kk["n"] += 1
            kk["usd"] += usd
            continue
        gpu = float(rec.get("gpu_secs") or 0)
        cpu = float(rec.get("burn_secs") or 0)
        out["gpu_secs"] += gpu
        out["cpu_secs"] += cpu
        out["usd"] += gpu * GPU_USD_PER_SEC + cpu * CPU_USD_PER_SEC
        if rec.get("burn_secs") is not None:
            out["burns"] += 1
            if rec.get("broll"):
                out["broll_jobs"] += 1
            continue
        out["videos"] += 1
        out["src_secs"] += float(rec.get("dur") or 0)
        mode = rec.get("enhance") or "none"
        m = out["by_mode"].setdefault(mode, {"n": 0, "gpu_secs": 0.0, "src_secs": 0.0})
        m["n"] += 1
        m["gpu_secs"] += gpu
        m["src_secs"] += float(rec.get("dur") or 0)
    out["usd"] = round(out["usd"], 4)
    out["gpu_secs"] = round(out["gpu_secs"], 1)
    out["cpu_secs"] = round(out["cpu_secs"], 1)
    out["src_secs"] = round(out["src_secs"], 1)
    # Cost of one delivered video: the burn belongs to the same video as the
    # process run, so divide by videos (not by videos + burns).
    out["usd_per_video"] = round(out["usd"] / out["videos"], 4) if out["videos"] else 0.0
    # The pricing-decision figures: compute + AI together, per delivered video.
    out["ai_usd"] = round(out["ai_usd"], 4)
    for kk in out["ai_by_kind"].values():
        kk["usd"] = round(kk["usd"], 4)
    out["all_usd"] = round(out["usd"] + out["ai_usd"], 4)
    out["all_per_video"] = round(out["all_usd"] / out["videos"], 4) if out["videos"] else 0.0
    for m in out["by_mode"].values():
        m["gpu_secs"] = round(m["gpu_secs"], 1)
        m["src_secs"] = round(m["src_secs"], 1)
        # GPU seconds burned per second of source: the number that says whether
        # a length-based credit rule is needed.
        m["gpu_per_src"] = round(m["gpu_secs"] / m["src_secs"], 2) if m["src_secs"] else 0.0
    return out


SONNET_MODEL = "claude-sonnet-5"             # moment selection, hooks, captions, video context
HAIKU_MODEL  = "claude-haiku-4-5-20251001"   # stock clip frame scoring

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
_RLE                = chr(0x202B)  # Right-to-Left Embedding — forces RTL base direction
_PDF                = chr(0x202C)  # Pop Directional Formatting — ends the embedding
_RTL_LEAD_PUNCT_RE  = _re.compile(r'^([?!.،؟]+)\s+(.+)$', _re.DOTALL)

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
    """Wrap each caption line in an explicit RTL embedding for correct libass bidi.

    libass renders with an LTR paragraph base by default, so a mixed
    Hebrew/English line like "אני אוהב JavaScript, זה ממש כיף" comes out with the
    two Hebrew segments SWAPPED around the English word, and commas/`?`/`!` land
    on the wrong side. A leading RLM (mark) is not enough — it doesn't change the
    paragraph base level. Wrapping each line in RLE…PDF (U+202B…U+202C) forces an
    RTL embedding level, so the Unicode Bidi Algorithm resolves the line exactly
    like a browser `dir=rtl` element does (verified pixel-for-pixel against Chrome
    for mixed Hebrew/English, commas, numbers, `?`/`!`, and `ו-CSS`).

    Applied per `\\N` segment (multi-line captions) and only to the ASS text, not
    the HTML editor copy. `_fix_rtl_punct` still runs first to repair the Whisper
    leading-punctuation token-order quirk (a separate, logical-order issue).
    """
    return r'\N'.join(_RLE + seg + _PDF for seg in text.split(r'\N'))

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


# ── Brute-force throttle (persistent, cross-container) ──
# _check_rate_limit above is a coarse per-container burst cap. This is the
# real defense for password / login-code guessing: failed attempts against a
# key (a username, or an IP) accumulate in a Modal Dict, and once they cross a
# threshold within a rolling window the key is locked out for the rest of that
# window. It survives container recycling and is shared across instances, so
# an attacker can't reset it by spreading requests over containers.
throttle_store = modal.Dict.from_name("hebpipe-throttle", create_if_missing=True)
THROTTLE_MAX_FAILS      = 8
THROTTLE_WINDOW_SECONDS = 15 * 60


def _throttle_allowed(store, key: str, now: float,
                      max_fails: int = THROTTLE_MAX_FAILS,
                      window: float = THROTTLE_WINDOW_SECONDS):
    """(allowed, retry_after_seconds) for an attempt against `key`."""
    try:
        rec = store.get(f"t:{key}") or {}
    except Exception:
        return True, 0
    first = rec.get("first", now)
    if now - first > window:          # window elapsed → clean slate
        return True, 0
    if rec.get("fails", 0) >= max_fails:
        return False, int(window - (now - first)) + 1
    return True, 0


def _throttle_record_fail(store, key: str, now: float,
                          window: float = THROTTLE_WINDOW_SECONDS):
    """Count a failed attempt, starting a fresh window if the last one aged out."""
    try:
        rec = store.get(f"t:{key}") or {}
        first = rec.get("first", now)
        fails = rec.get("fails", 0)
        if now - first > window:
            first, fails = now, 0
        store[f"t:{key}"] = {"fails": fails + 1, "first": first}
    except Exception:
        pass


def _throttle_clear(store, key: str):
    """Reset the counter after a successful attempt."""
    try:
        store.pop(f"t:{key}")
    except Exception:
        pass


def _anthropic_client(api_key=None, max_retries=5):
    """Anthropic client hardened for transient API failures: 429/5xx/529
    ("Overloaded") retry with the SDK's exponential backoff. The SDK default
    of 2 retries gave up during a real 529 burst (2026-07-16) and failed the
    user's hook + B-roll jobs."""
    import os
    import anthropic
    return anthropic.Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY", ""),
                               max_retries=max_retries)


def _plain_anthropic_errors(fn):
    """Re-raise Anthropic SDK exceptions escaping a Modal worker as PLAIN
    RuntimeErrors. The SDK's exceptions carry live httpx request/response
    objects that fail to deserialize on the calling side - the client then
    sees "Could not deserialize remote exception due to local error" instead
    of the real cause. The "ai_busy:<code>" message is matched by the frontend
    and shown as a friendly "AI overloaded - try again in a minute"."""
    import functools

    @functools.wraps(fn)
    def _wrapped(*args, **kwargs):
        import anthropic
        try:
            return fn(*args, **kwargs)
        except anthropic.APIStatusError as e:
            raise RuntimeError(f"ai_busy:{e.status_code}") from None
        except anthropic.APIError as e:
            raise RuntimeError(f"ai_busy:{type(e).__name__}") from None
    return _wrapped


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
