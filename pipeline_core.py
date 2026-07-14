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
        "google-auth",   # FCM "video ready" push (HTTP v1 send)
    )
    .run_commands(
        "mkdir -p /usr/local/share/fonts/hebrew",
        'wget -q "https://github.com/google/fonts/raw/main/ofl/heebo/Heebo%5Bwght%5D.ttf" -O /usr/local/share/fonts/hebrew/Heebo.ttf',
        'wget -q "https://github.com/google/fonts/raw/main/ofl/assistant/Assistant%5Bwght%5D.ttf" -O /usr/local/share/fonts/hebrew/Assistant.ttf',
        'wget -q "https://github.com/google/fonts/raw/main/ofl/frankruhllibre/FrankRuhlLibre%5Bwght%5D.ttf" -O /usr/local/share/fonts/hebrew/FrankRuhlLibre.ttf',
        'wget -q "https://github.com/google/fonts/raw/main/ofl/secularone/SecularOne-Regular.ttf" -O /usr/local/share/fonts/hebrew/SecularOne.ttf',
        # Extra caption-editor faces (must stay in sync with site/index.html's
        # <select id="fontSelect"> and the Google Fonts <link> — preview == burn).
        'wget -q "https://github.com/google/fonts/raw/main/ofl/rubik/Rubik%5Bwght%5D.ttf" -O /usr/local/share/fonts/hebrew/Rubik.ttf',
        'wget -q "https://github.com/google/fonts/raw/main/ofl/suezone/SuezOne-Regular.ttf" -O /usr/local/share/fonts/hebrew/SuezOne.ttf',
        'wget -q "https://github.com/google/fonts/raw/main/ofl/karantina/Karantina-Regular.ttf" -O /usr/local/share/fonts/hebrew/Karantina.ttf',
        'wget -q "https://github.com/google/fonts/raw/main/ofl/playpensanshebrew/PlaypenSansHebrew%5Bwght%5D.ttf" -O /usr/local/share/fonts/hebrew/PlaypenSansHebrew.ttf',
        'wget -q "https://github.com/google/fonts/raw/main/ofl/miriamlibre/MiriamLibre%5Bwght%5D.ttf" -O /usr/local/share/fonts/hebrew/MiriamLibre.ttf',
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
    .pip_install("requests", "google-auth")   # google-auth: FCM "video ready" push
    .run_commands(
        "mkdir -p /usr/local/share/fonts/hebrew",
        'wget -q "https://github.com/google/fonts/raw/main/ofl/heebo/Heebo%5Bwght%5D.ttf" -O /usr/local/share/fonts/hebrew/Heebo.ttf',
        'wget -q "https://github.com/google/fonts/raw/main/ofl/assistant/Assistant%5Bwght%5D.ttf" -O /usr/local/share/fonts/hebrew/Assistant.ttf',
        'wget -q "https://github.com/google/fonts/raw/main/ofl/frankruhllibre/FrankRuhlLibre%5Bwght%5D.ttf" -O /usr/local/share/fonts/hebrew/FrankRuhlLibre.ttf',
        'wget -q "https://github.com/google/fonts/raw/main/ofl/secularone/SecularOne-Regular.ttf" -O /usr/local/share/fonts/hebrew/SecularOne.ttf',
        # Extra caption-editor faces (keep in sync with the full image + frontend).
        'wget -q "https://github.com/google/fonts/raw/main/ofl/rubik/Rubik%5Bwght%5D.ttf" -O /usr/local/share/fonts/hebrew/Rubik.ttf',
        'wget -q "https://github.com/google/fonts/raw/main/ofl/suezone/SuezOne-Regular.ttf" -O /usr/local/share/fonts/hebrew/SuezOne.ttf',
        'wget -q "https://github.com/google/fonts/raw/main/ofl/karantina/Karantina-Regular.ttf" -O /usr/local/share/fonts/hebrew/Karantina.ttf',
        'wget -q "https://github.com/google/fonts/raw/main/ofl/playpensanshebrew/PlaypenSansHebrew%5Bwght%5D.ttf" -O /usr/local/share/fonts/hebrew/PlaypenSansHebrew.ttf',
        'wget -q "https://github.com/google/fonts/raw/main/ofl/miriamlibre/MiriamLibre%5Bwght%5D.ttf" -O /usr/local/share/fonts/hebrew/MiriamLibre.ttf',
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
    .apt_install("ffmpeg", "fontconfig", "wget")
    .pip_install("requests", "anthropic>=0.40.0", "fastapi", "python-multipart", "boto3",
                 "google-auth")   # verify Google Sign-In ID tokens (/auth/google)
    # Hebrew caption/hook fonts so /preview_frame renders the WYSIWYG editor
    # preview through libass with the SAME faces the burn uses (keep in sync
    # with burn_image + the full image + site/index.html's #fontSelect).
    .run_commands(
        "mkdir -p /usr/local/share/fonts/hebrew",
        'wget -q "https://github.com/google/fonts/raw/main/ofl/heebo/Heebo%5Bwght%5D.ttf" -O /usr/local/share/fonts/hebrew/Heebo.ttf',
        'wget -q "https://github.com/google/fonts/raw/main/ofl/assistant/Assistant%5Bwght%5D.ttf" -O /usr/local/share/fonts/hebrew/Assistant.ttf',
        'wget -q "https://github.com/google/fonts/raw/main/ofl/frankruhllibre/FrankRuhlLibre%5Bwght%5D.ttf" -O /usr/local/share/fonts/hebrew/FrankRuhlLibre.ttf',
        'wget -q "https://github.com/google/fonts/raw/main/ofl/secularone/SecularOne-Regular.ttf" -O /usr/local/share/fonts/hebrew/SecularOne.ttf',
        'wget -q "https://github.com/google/fonts/raw/main/ofl/rubik/Rubik%5Bwght%5D.ttf" -O /usr/local/share/fonts/hebrew/Rubik.ttf',
        'wget -q "https://github.com/google/fonts/raw/main/ofl/suezone/SuezOne-Regular.ttf" -O /usr/local/share/fonts/hebrew/SuezOne.ttf',
        'wget -q "https://github.com/google/fonts/raw/main/ofl/karantina/Karantina-Regular.ttf" -O /usr/local/share/fonts/hebrew/Karantina.ttf',
        'wget -q "https://github.com/google/fonts/raw/main/ofl/playpensanshebrew/PlaypenSansHebrew%5Bwght%5D.ttf" -O /usr/local/share/fonts/hebrew/PlaypenSansHebrew.ttf',
        'wget -q "https://github.com/google/fonts/raw/main/ofl/miriamlibre/MiriamLibre%5Bwght%5D.ttf" -O /usr/local/share/fonts/hebrew/MiriamLibre.ttf',
        "fc-cache -f /usr/local/share/fonts/hebrew",
    )
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
# in the `hebpipe-auth` Modal Secret (AUTH_SECRET + INVITE_CODE).
# Migration note: to move to a managed provider (e.g. Supabase), swap
# _verify_token for provider-JWT verification mapping to the same uid.
# ---------------------------------------------------------------------------
users_store = modal.Dict.from_name("hebpipe-users", create_if_missing=True)   # email (legacy: username) → {uid, salt, pw, created}
calls_store = modal.Dict.from_name("hebpipe-calls", create_if_missing=True)   # call_id  → {uid, ts}
quota_store = modal.Dict.from_name("hebpipe-quota", create_if_missing=True)   # f"{uid}:{call_id}" → ts (one unique entry per consumed credit)
fcm_store   = modal.Dict.from_name("hebpipe-fcm", create_if_missing=True)     # uid → [device FCM tokens] for "video ready" push notifications
# Deferred processing jobs: full upload key → {params, uid, uname, uprefix,
# total_chunks, ts} registered BEFORE the upload, so the SERVER spawns
# process_video the moment the last byte lands - the app may be closed by then
# (its JS is frozen; a client-side spawn would wait for a reopen). After the
# spawn the entry moves to "done:<key>" → {call_id, uid, ts} for the client.
pending_store = modal.Dict.from_name("hebpipe-pending", create_if_missing=True)
codes_store = modal.Dict.from_name("hebpipe-codes", create_if_missing=True)   # normalized email → {salt, hash, exp, attempts, is_new, terms_ts} for passwordless login


def _send_fcm(uid, title, body, kind="video_ready"):
    """Best-effort push to a user's devices via FCM HTTP v1.
    `kind` rides in the data payload so the app can route the tap:
    video_ready → History tab; edit_ready → stay on the resumed editor.
    No-op unless the hebpipe-fcm secret (FCM_SERVICE_ACCOUNT = the Firebase
    service-account JSON) is configured. Prunes tokens the server rejects."""
    import os
    import json as _json
    raw = os.environ.get("FCM_SERVICE_ACCOUNT")
    if not raw or not uid:
        return
    try:
        tokens = fcm_store.get(uid) or []
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
                            "notification": {"channel_id": "video_ready"}},
            }})
            if r.status_code in (400, 404):   # unregistered / invalid token
                dead.append(tkn)
        if dead:
            fcm_store[uid] = [t for t in tokens if t not in dead]
    except Exception as e:
        print(f"[fcm] send failed: {e!r}")

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
    """Small branded HTML wrapper shared by verification + reset emails."""
    return (
        "<div style='font-family:system-ui,-apple-system,Segoe UI,Arial,sans-serif;"
        "background:#F2EEF8;padding:32px;text-align:center'>"
        "<div style='max-width:440px;margin:0 auto;background:#fff;border:1.5px solid #EDE9FE;"
        "border-radius:20px;padding:32px 28px'>"
        "<div style='font-weight:800;font-size:20px;color:#6D28D9;margin-bottom:14px'>פייפליין</div>"
        f"<h2 style='color:#1E1033;font-size:19px;margin:0 0 10px'>{title}</h2>"
        f"<p style='color:#6B7080;font-size:14px;line-height:1.6;margin:0 0 22px'>{body_line}</p>"
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
# real defense for password / invite-code guessing: failed attempts against a
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

