"""
Hebrew Video Pipeline — Modal API backend (deploy entrypoint).

Deploy:  modal deploy app_modal.py
Dev:     modal serve app_modal.py

This module holds only the ASGI router (api). The pipeline itself lives in:
  pipeline_core.py   app/images/constants + pure helpers
  pipeline_fns.py    process_video, burn_captions_fn
  stock_helpers.py   stock-footage pure helpers
  broll_fns.py       Veo + stock B-roll Modal functions
  content_fns.py     hook/caption generation
  metricool_fns.py   scheduling via Metricool MCP
"""

import modal

from pipeline_core import (
    light_image,
    jobs_store, progress_store, users_store, calls_store, quota_store, fcm_store,
    codes_store, _normalize_email, _gen_login_code,
    _verify_google_id_token, GOOGLE_WEB_CLIENT_ID,
    app, image, tmp_vol, TMP_DIR,
    _SAFE_KEY_RE, _SAFE_DOWNLOAD_KEY_RE, _check_rate_limit, _get_client_ip,
    throttle_store, _throttle_allowed, _throttle_record_fail, _throttle_clear,
    _poll_fn_call,
    _hash_password, _verify_password, _sign_token, _verify_token,
    _sign_media_token, _verify_media_token, MEDIA_TOKEN_TTL_SECONDS,
    _EMAIL_RE, _sign_scoped_token, _verify_scoped_token, _send_email, _email_html,
    EMAIL_VERIFY_TTL_SECONDS,
    _user_prefix, _owned_key, _broll_item_safe,
    DEFAULT_VIDEO_LIMIT, _quota_state, _quota_allows, _count_quota_used,
)

# Public base URLs used to build email links. SITE_URL can be overridden via
# env once a custom domain is set; the API base is stable.
API_BASE_URL = "https://yotamjacob--hebrew-video-pipeline-api.modal.run"


def _code_email_html(code: str) -> str:
    """Branded HTML for the passwordless login code (on-brand terracotta/sand)."""
    return (
        "<div style='font-family:system-ui,-apple-system,Segoe UI,Arial,sans-serif;"
        "background:#E8DFD3;padding:32px;text-align:center'>"
        "<div style='max-width:440px;margin:0 auto;background:#F4ECE0;border:1.5px solid #DDD2C0;"
        "border-radius:20px;padding:32px 28px'>"
        "<div style='font-weight:800;font-size:20px;color:#C4703F;margin-bottom:14px'>פייפליין</div>"
        "<h2 style='color:#3E3A34;font-size:19px;margin:0 0 10px'>Your login code</h2>"
        "<p style='color:#6B6455;font-size:14px;line-height:1.6;margin:0 0 22px'>"
        "Enter this code in the app to sign in. It expires in 10 minutes.</p>"
        # direction:ltr pins digit order in RTL mail clients; the right padding is
        # 10px smaller than the left to swallow the phantom letter-spacing after
        # the LAST digit, so the code sits visually centered in its box (was
        # noticeably off-center on mobile Gmail).
        f"<div style='display:inline-block;direction:ltr;background:#fff;border:1.5px solid #DDD2C0;border-radius:14px;"
        f"padding:14px 18px 14px 28px;font-size:34px;font-weight:800;letter-spacing:10px;color:#3E3A34'>{code}</div>"
        "<p style='color:#9A927F;font-size:12px;margin:22px 0 0'>"
        "If you didn't request this, you can ignore this email.</p>"
        "</div></div>"
    )
from pipeline_fns import process_video, burn_captions_fn, backup_dicts, restore_dicts, build_caption_ass
from broll_fns import analyze_stock_broll, search_stock_clips
from content_fns import generate_hook_options, generate_caption_options
from metricool_fns import (
    schedule_post_fn, oauth_store, _mc_refresh_access_token, _mcp_tool_call,
    _build_metricool_info,
    MC_MCP_URL, MC_AUTHZ_URL, MC_TOKEN_URL, MC_CLIENT_ID, MC_REDIRECT,
    MC_SCOPE, MC_BLOG_ID, MC_PROTO,
)

# ---------------------------------------------------------------------------
# HTTP API — raw ASGI, zero external dependencies, dispatches to process_video
#
# POST /process?filename=x&cut_silences=true&burn_captions=true&min_silence=0.5&padding=0.2
#   Body: raw video bytes
#   Returns: processed video bytes (video/mp4)
# ---------------------------------------------------------------------------
@app.function(image=light_image, timeout=900, volumes={TMP_DIR: tmp_vol},
              secrets=[modal.Secret.from_name("hebpipe-auth")])
@modal.concurrent(max_inputs=50)   # headroom: chunk uploads + their CORS preflights + polls
@modal.asgi_app()
def api():
    import asyncio
    import json
    from urllib.parse import parse_qs

    CORS = [
        (b"access-control-allow-origin",  b"*"),
        (b"access-control-allow-methods", b"GET, POST, PUT, DELETE, OPTIONS"),
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

        async def send_error(msg: str, status: int = 500, code: str = None, **extra):
            # `code` is a machine-readable id so the frontend can translate the
            # message (i18n); `extra` carries structured params (e.g. retry_min).
            payload = {"error": msg}
            if code:
                payload["code"] = code
            payload.update(extra)
            body = json.dumps(payload).encode()
            await send({"type": "http.response.start", "status": status,
                        "headers": CORS + [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": body})

        def _resolve_account_key(nident, raw):
            """Return the users_store key of an existing account for this email,
            or None. Checks the normalized key, the raw lowercased key (legacy /
            pre-normalization accounts), then the email reverse index — so an
            alias never spawns a second account for the same person."""
            for k in (nident, raw):
                if isinstance(users_store.get(k), dict):
                    return k
            for k in (f"email:{raw}", f"email:{nident}"):
                idx = users_store.get(k)
                if isinstance(idx, str) and isinstance(users_store.get(idx), dict):
                    return idx
            return None

        method = scope["method"]
        path   = scope["path"]

        # OPTIONS preflight — cache it for a day so the browser doesn't re-fly
        # before every request. Chunk uploads POST to a FIXED url with key/index
        # in headers (not the query string) specifically so one cached preflight
        # covers the whole upload instead of one per 2 MB chunk.
        if method == "OPTIONS":
            await send({"type": "http.response.start", "status": 204,
                        "headers": CORS + [(b"access-control-max-age", b"86400")]})
            await send({"type": "http.response.body",  "body": b""})
            return

        # Health check
        if path == "/" and method == "GET":
            body = json.dumps({"status": "ok"}).encode()
            await send({"type": "http.response.start", "status": 200,
                        "headers": CORS + [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": body})
            return

        # ── Password registration is RETIRED (2026-07-13) ──
        # New accounts are created only via verified email codes (/auth/request-
        # code + /auth/verify-code) or Google — closing the "any email + shared
        # invite = unlimited free-credit accounts" hole. Password /auth/login
        # stays as a dormant fallback for legacy accounts.
        if path in ("/auth/register", "/auth/register/") and method == "POST":
            await send_error("Registration has moved - sign in with your email and the 6-digit code we send you.", 410)
            return

        # ── Auth: login (legacy password fallback) ──
        if path in ("/auth/login", "/auth/login/") and method == "POST":
            _ip = _get_client_ip(scope)
            if not _check_rate_limit(_ip):
                await send_error("Rate limit exceeded. Try again in a minute.", 429, code="rate_limited")
                return
            import os, secrets as _secrets, time as _time
            _now = _time.time()
            try:
                data = json.loads((await _read_body(receive)).decode("utf-8"))
            except Exception:
                await send_error("Invalid request body", 400)
                return
            # The account identifier is an email address. `username` is still
            # accepted as the field name so older clients keep working, and
            # login resolves legacy username-keyed accounts (pre-email era).
            ident = (data.get("email") or data.get("username") or "").strip().lower()
            password = data.get("password") or ""
            # ":" is rejected so an identifier can never collide with the
            # store's index namespaces (`uid:*`, `email:*`).
            if not ident or len(ident) > 254 or ":" in ident:
                await send_error("A valid email address is required", 400)
                return
            if path.startswith("/auth/register"):
                if not _EMAIL_RE.match(ident):
                    await send_error("A valid email address is required", 400)
                    return
                # Throttle invite-code guessing per source IP.
                _ok, _retry = _throttle_allowed(throttle_store, f"invite:{_ip}", _now)
                if not _ok:
                    await send_error(f"Too many attempts. Try again in {_retry // 60 + 1} min.", 429)
                    return
                # Invite is matched case-insensitively and whitespace-trimmed, so
                # a stray capital or space doesn't reject a valid code.
                if (data.get("invite") or "").strip().casefold() != os.environ.get("INVITE_CODE", "").strip().casefold():
                    _throttle_record_fail(throttle_store, f"invite:{_ip}", _now)
                    await send_error("Invalid invite code", 403)
                    return
                _throttle_clear(throttle_store, f"invite:{_ip}")
                if len(password) < 8:
                    await send_error("Password must be at least 8 characters", 400)
                    return
                # Accepting the Terms + Privacy Policy is mandatory to register.
                if not data.get("terms_accepted"):
                    await send_error("You must accept the Privacy Policy and Terms of Use", 400)
                    return
                # The email must be free both as an account key (new accounts)
                # and in the reverse index (legacy username accounts that
                # attached this address).
                if users_store.contains(ident) or users_store.contains(f"email:{ident}"):
                    await send_error("An account with this email already exists", 409)
                    return
                salt, ph = _hash_password(password)
                new_uid = _secrets.token_hex(16)
                # Marketing consent: explicit opt-in only (unchecked by default in
                # the UI). Transactional mail (verify/reset) is sent regardless;
                # promotional mail may only go to accounts with this set true.
                _mkt = bool(data.get("marketing_consent"))
                users_store[ident] = {"uid": new_uid, "salt": salt, "pw": ph, "created": _time.time(),
                                      "email": ident, "email_verified": False,
                                      "marketing_consent": _mkt,
                                      "marketing_consent_ts": _time.time() if _mkt else None,
                                      "terms_accepted_ts": _time.time(),
                                      "video_limit": DEFAULT_VIDEO_LIMIT, "videos_used": 0}
                users_store[f"uid:{new_uid}"] = ident
                users_store[f"email:{ident}"] = ident   # reverse index for password reset
                # Fire the verification email (best-effort — a failure must not
                # block sign-up, since verification is a nudge, not a gate).
                try:
                    _vtok = _sign_scoped_token(new_uid, "verify", os.environ["AUTH_SECRET"], EMAIL_VERIFY_TTL_SECONDS)
                    _send_email(ident, "Verify your email - פייפליין",
                                _email_html("Verify your email",
                                            "Tap the button to confirm this address so you can recover your account later.",
                                            "Verify email", f"{API_BASE_URL}/auth/verify?token={_vtok}"))
                except Exception as _mail_err:
                    print(f"[email] verification send failed: {_mail_err}")
            else:
                # Throttle password guessing per identifier AND per source IP.
                _ok_u, _retry_u   = _throttle_allowed(throttle_store, f"login:{ident}", _now)
                _ok_ip, _retry_ip = _throttle_allowed(throttle_store, f"loginip:{_ip}", _now)
                if not (_ok_u and _ok_ip):
                    await send_error(f"Too many attempts. Try again in {max(_retry_u, _retry_ip) // 60 + 1} min.", 429)
                    return
                # Resolve the account: direct key hit (email-keyed accounts and
                # legacy username-keyed accounts), then the email reverse index
                # (legacy accounts that attached this address). The isinstance
                # guard keeps index entries (`uid:*`/`email:*` → strings) from
                # ever being treated as a user record.
                _acct_key = ident
                rec = users_store.get(ident)
                if not isinstance(rec, dict):
                    _key = users_store.get(f"email:{ident}")
                    _acct_key = _key if isinstance(_key, str) else None
                    rec = users_store.get(_key) if isinstance(_key, str) else None
                if not isinstance(rec, dict) or not _verify_password(password, rec["salt"], rec["pw"]):
                    _throttle_record_fail(throttle_store, f"login:{ident}", _now)
                    _throttle_record_fail(throttle_store, f"loginip:{_ip}", _now)
                    await send_error("Invalid email or password", 401)
                    return
                _throttle_clear(throttle_store, f"login:{ident}")
                _throttle_clear(throttle_store, f"loginip:{_ip}")
                new_uid = rec["uid"]
                users_store[f"uid:{new_uid}"] = _acct_key   # backfill reverse index
            token = _sign_token(new_uid, os.environ["AUTH_SECRET"])
            body = json.dumps({"token": token, "username": ident}).encode()
            await send({"type": "http.response.start", "status": 200,
                        "headers": CORS + [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": body})
            return

        # ── Passwordless login: request a 6-digit code by email (public) ──
        if path in ("/auth/request-code", "/auth/request-code/") and method == "POST":
            import os, time as _time
            _ip = _get_client_ip(scope)
            if not _check_rate_limit(_ip):
                await send_error("Rate limit exceeded. Try again in a minute.", 429, code="rate_limited")
                return
            _now = _time.time()
            try:
                data = json.loads((await _read_body(receive)).decode("utf-8"))
            except Exception:
                await send_error("Invalid request body", 400)
                return
            _raw = (data.get("email") or "").strip().lower()
            if not _raw or len(_raw) > 254 or ":" in _raw or not _EMAIL_RE.match(_raw):
                await send_error("A valid email address is required", 400, code="invalid_email")
                return
            ident = _normalize_email(_raw)
            is_new = _resolve_account_key(ident, _raw) is None
            # Explicit flow from the two-panel UI: 'login' (existing user) or
            # 'register' (new user). A mismatch is answered up front - no code is
            # sent - so an existing email can't "register" a duplicate and a
            # nonexistent email gets a clear "no account" instead of a silent code.
            _mode = (data.get("mode") or "").strip().lower()
            if _mode == "login" and is_new:
                _nb = json.dumps({"error": "No account found for this email.", "no_account": True}).encode()
                await send({"type": "http.response.start", "status": 403,
                            "headers": CORS + [(b"content-type", b"application/json")]})
                await send({"type": "http.response.body", "body": _nb})
                return
            if _mode == "register" and not is_new:
                _nb = json.dumps({"error": "This email already has an account.", "exists": True}).encode()
                await send({"type": "http.response.start", "status": 409,
                            "headers": CORS + [(b"content-type", b"application/json")]})
                await send({"type": "http.response.body", "body": _nb})
                return
            # NEW accounts still need a valid invite (kept as an extra gate) and
            # Terms acceptance; existing accounts skip both (they're already in).
            if is_new:
                _ok, _retry = _throttle_allowed(throttle_store, f"invite:{_ip}", _now)
                if not _ok:
                    await send_error(f"Too many attempts. Try again in {_retry // 60 + 1} min.", 429,
                                     code="throttled", retry_min=_retry // 60 + 1)
                    return
                if (data.get("invite") or "").strip().casefold() != os.environ.get("INVITE_CODE", "").strip().casefold():
                    _throttle_record_fail(throttle_store, f"invite:{_ip}", _now)
                    await send_error("Invalid invite code", 403, code="invalid_invite")
                    return
                _throttle_clear(throttle_store, f"invite:{_ip}")
                if not data.get("terms_accepted"):
                    await send_error("You must accept the Privacy Policy and Terms of Use", 400, code="terms_required")
                    return
            # Throttle code SENDS per email + per IP so an inbox can't be spammed.
            _oke, _ree = _throttle_allowed(throttle_store, f"code:{ident}", _now)
            _oki, _rii = _throttle_allowed(throttle_store, f"codeip:{_ip}", _now)
            if not (_oke and _oki):
                await send_error(f"Too many code requests. Try again in {max(_ree, _rii) // 60 + 1} min.", 429,
                                 code="throttled", retry_min=max(_ree, _rii) // 60 + 1)
                return
            _throttle_record_fail(throttle_store, f"code:{ident}", _now)
            _throttle_record_fail(throttle_store, f"codeip:{_ip}", _now)
            _code = _gen_login_code()
            _csalt, _chash = _hash_password(_code)
            codes_store[ident] = {"salt": _csalt, "hash": _chash, "exp": _now + 600,
                                  "attempts": 0, "is_new": is_new,
                                  "terms_ts": (_now if is_new else None), "raw": _raw}
            try:
                _send_email(_raw, f"{_code} is your Pipeline login code", _code_email_html(_code))
            except Exception as _mail_err:
                print(f"[email] login code send failed: {_mail_err}")
            body = json.dumps({"ok": True, "is_new": is_new}).encode()
            await send({"type": "http.response.start", "status": 200,
                        "headers": CORS + [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": body})
            return

        # ── Passwordless login: verify the code → session token (public) ──
        if path in ("/auth/verify-code", "/auth/verify-code/") and method == "POST":
            import os, secrets as _secrets, time as _time
            _ip = _get_client_ip(scope)
            if not _check_rate_limit(_ip):
                await send_error("Rate limit exceeded. Try again in a minute.", 429, code="rate_limited")
                return
            _now = _time.time()
            try:
                data = json.loads((await _read_body(receive)).decode("utf-8"))
            except Exception:
                await send_error("Invalid request body", 400)
                return
            _raw  = (data.get("email") or "").strip().lower()
            _code = (data.get("code") or "").strip()
            if not _EMAIL_RE.match(_raw) or not _code.isdigit() or len(_code) != 6:
                await send_error("Enter the 6-digit code from your email.", 400, code="code_format")
                return
            ident = _normalize_email(_raw)
            # Throttle verify attempts per email AND per IP (guessing defense).
            _okv, _rev = _throttle_allowed(throttle_store, f"codev:{ident}", _now)
            _oki, _rii = _throttle_allowed(throttle_store, f"codevip:{_ip}", _now)
            if not (_okv and _oki):
                await send_error(f"Too many attempts. Try again in {max(_rev, _rii) // 60 + 1} min.", 429,
                                 code="throttled", retry_min=max(_rev, _rii) // 60 + 1)
                return
            crec = codes_store.get(ident)
            if not isinstance(crec, dict) or crec.get("exp", 0) < _now:
                _throttle_record_fail(throttle_store, f"codev:{ident}", _now)
                _throttle_record_fail(throttle_store, f"codevip:{_ip}", _now)
                await send_error("This code has expired. Request a new one.", 400, code="code_expired")
                return
            # Per-code attempt cap so a single live code can't be brute-forced.
            crec["attempts"] = crec.get("attempts", 0) + 1
            if crec["attempts"] > 5:
                codes_store.pop(ident, None)
                await send_error("Too many wrong tries. Request a new code.", 400, code="code_tries")
                return
            codes_store[ident] = crec
            if not _verify_password(_code, crec["salt"], crec["hash"]):
                _throttle_record_fail(throttle_store, f"codev:{ident}", _now)
                _throttle_record_fail(throttle_store, f"codevip:{_ip}", _now)
                await send_error("Incorrect code. Try again.", 401, code="code_incorrect")
                return
            # Correct — consume the code and clear throttles.
            codes_store.pop(ident, None)
            _throttle_clear(throttle_store, f"codev:{ident}")
            _throttle_clear(throttle_store, f"codevip:{_ip}")
            _throttle_clear(throttle_store, f"code:{ident}")
            # Find-or-create the account, keyed by the NORMALIZED email.
            _acct_key = _resolve_account_key(ident, _raw)
            if _acct_key:
                rec = users_store.get(_acct_key)
                rec["email_verified"] = True
                if not rec.get("email"):
                    rec["email"] = _raw
                users_store[_acct_key] = rec
                new_uid = rec["uid"]
                _uname = rec.get("email") or _acct_key
            else:
                new_uid = _secrets.token_hex(16)
                users_store[ident] = {"uid": new_uid, "created": _now, "email": _raw,
                                      "email_verified": True, "auth": "code",
                                      "terms_accepted_ts": crec.get("terms_ts") or _now,
                                      "video_limit": DEFAULT_VIDEO_LIMIT, "videos_used": 0}
                users_store[f"uid:{new_uid}"] = ident
                users_store[f"email:{ident}"] = ident
                if _raw != ident:
                    users_store[f"email:{_raw}"] = ident   # also index the raw form
                _uname = _raw
            token = _sign_token(new_uid, os.environ["AUTH_SECRET"])
            body = json.dumps({"token": token, "username": _uname}).encode()
            await send({"type": "http.response.start", "status": 200,
                        "headers": CORS + [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": body})
            return

        # ── Google Sign-In: verify the ID token → session token (public) ──
        # Same normalized-email keying as the code flow, so signing in with
        # Google and with a code for the same address land on ONE account (no
        # double credits). NEW accounts require the invite + Terms (the consent
        # screen is published, so Google no longer gates signups); the response
        # carries `need_invite:true` so the app reveals those fields and retries.
        if path in ("/auth/google", "/auth/google/") and method == "POST":
            import os, secrets as _secrets, time as _time
            _ip = _get_client_ip(scope)
            if not _check_rate_limit(_ip):
                await send_error("Rate limit exceeded. Try again in a minute.", 429, code="rate_limited")
                return
            _now = _time.time()
            try:
                data = json.loads((await _read_body(receive)).decode("utf-8"))
            except Exception:
                await send_error("Invalid request body", 400)
                return
            claims = _verify_google_id_token(data.get("id_token") or data.get("credential") or "")
            _raw = ((claims.get("email") if claims else "") or "").strip().lower()
            if not claims or not _EMAIL_RE.match(_raw):
                await send_error("Google sign-in failed. Please try again.", 401, code="google_failed")
                return
            ident = _normalize_email(_raw)
            _sub = claims.get("sub")
            _acct_key = _resolve_account_key(ident, _raw)
            # Two-panel UI: in the "existing user" panel a Google account with no
            # record gets a clear "no account" answer instead of the invite dance.
            # (register + existing account just signs in - it's the same person,
            # Google already verified the email.)
            if (data.get("mode") or "").strip().lower() == "login" and not _acct_key:
                _nb = json.dumps({"error": "No account found for this Google account.", "no_account": True}).encode()
                await send({"type": "http.response.start", "status": 403,
                            "headers": CORS + [(b"content-type", b"application/json")]})
                await send({"type": "http.response.body", "body": _nb})
                return
            if _acct_key:
                rec = users_store.get(_acct_key)
                rec["email_verified"] = True
                if not rec.get("email"):
                    rec["email"] = _raw
                if _sub:
                    rec["google_sub"] = _sub
                users_store[_acct_key] = rec
                new_uid = rec["uid"]
                _uname = rec.get("email") or _acct_key
            else:
                # NEW account via Google. The consent screen is PUBLISHED (Google
                # no longer restricts who can sign in), so gate new signups with
                # the invite + Terms just like the email-code path. `need_invite`
                # tells the app to reveal those fields and retry.
                _iok, _iretry = _throttle_allowed(throttle_store, f"invite:{_ip}", _now)
                if not _iok:
                    await send_error(f"Too many attempts. Try again in {_iretry // 60 + 1} min.", 429,
                                     code="throttled", retry_min=_iretry // 60 + 1)
                    return
                if (data.get("invite") or "").strip().casefold() != os.environ.get("INVITE_CODE", "").strip().casefold():
                    _throttle_record_fail(throttle_store, f"invite:{_ip}", _now)
                    _nb = json.dumps({"error": "Enter your invite code to create an account.", "need_invite": True, "code": "invalid_invite"}).encode()
                    await send({"type": "http.response.start", "status": 403,
                                "headers": CORS + [(b"content-type", b"application/json")]})
                    await send({"type": "http.response.body", "body": _nb})
                    return
                if not data.get("terms_accepted"):
                    _nb = json.dumps({"error": "Please accept the Privacy Policy and Terms of Use.", "need_invite": True, "code": "terms_required"}).encode()
                    await send({"type": "http.response.start", "status": 400,
                                "headers": CORS + [(b"content-type", b"application/json")]})
                    await send({"type": "http.response.body", "body": _nb})
                    return
                _throttle_clear(throttle_store, f"invite:{_ip}")
                new_uid = _secrets.token_hex(16)
                users_store[ident] = {"uid": new_uid, "created": _now, "email": _raw,
                                      "email_verified": True, "auth": "google", "google_sub": _sub,
                                      "terms_accepted_ts": _now,
                                      "video_limit": DEFAULT_VIDEO_LIMIT, "videos_used": 0}
                users_store[f"uid:{new_uid}"] = ident
                users_store[f"email:{ident}"] = ident
                if _raw != ident:
                    users_store[f"email:{_raw}"] = ident
                _uname = _raw
            token = _sign_token(new_uid, os.environ["AUTH_SECRET"])
            body = json.dumps({"token": token, "username": _uname}).encode()
            await send({"type": "http.response.start", "status": 200,
                        "headers": CORS + [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": body})
            return

        # ── Email verification (public link from the verification email) ──
        if path in ("/auth/verify", "/auth/verify/") and method == "GET":
            import os as _os
            _vt = parse_qs(scope.get("query_string", b"").decode()).get("token", [""])[0]
            _vuid = _verify_scoped_token(_vt, "verify", _os.environ["AUTH_SECRET"]) if _vt else None

            def _vhtml(msg, ok=True):
                color = "#059669" if ok else "#DC2626"
                return (f"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
                        f"<body style='font-family:system-ui;background:#F2EEF8;color:#1E1033;display:flex;"
                        f"align-items:center;justify-content:center;height:100vh;margin:0;text-align:center'>"
                        f"<div style='background:#fff;border:1.5px solid #EDE9FE;border-radius:20px;padding:36px 28px;max-width:360px'>"
                        f"<div style='font-size:44px'>{'✅' if ok else '⚠️'}</div>"
                        f"<h2 style='color:{color};margin:12px 0 6px'>{'Email verified' if ok else 'Link invalid or expired'}</h2>"
                        f"<p style='color:#6B7080;font-size:14px'>{msg}</p></div></body>").encode()

            if _vuid:
                _vuname = users_store.get(f"uid:{_vuid}")
                _vrec = users_store.get(_vuname) if _vuname else None
                if _vrec:
                    _vrec["email_verified"] = True
                    users_store[_vuname] = _vrec
                page, status = _vhtml("You can close this tab and head back to the app."), 200
            else:
                page, status = _vhtml("Please request a fresh verification email from the app.", ok=False), 400
            await send({"type": "http.response.start", "status": status,
                        "headers": [(b"content-type", b"text/html; charset=utf-8")]})
            await send({"type": "http.response.body", "body": page})
            return

        # ── Password reset routes REMOVED (2026-07-13): auth is passwordless
        # (email codes / Google), so there are no passwords to reset. Old
        # /auth/forgot + /auth/reset now 410 via the fallthrough below.
        if path in ("/auth/forgot", "/auth/forgot/", "/auth/reset", "/auth/reset/") and method == "POST":
            await send_error("Passwords are gone - sign in with your email code or Google.", 410)
            return

        # ── Session guard — every route below requires a valid token. ──
        # Exceptions: the Metricool OAuth callback (called by Metricool) and
        # /media (fetched by Metricool's ingester; keys are unguessable).
        # Identity (uid) comes ONLY from the verified token — never from the
        # client — and every volume key is namespaced u{uid}__ from it.
        uid, uprefix = None, None
        if not (path.startswith("/oauth/callback") or path.startswith("/media/")):
            import os
            _tok = None
            for _hk, _hv in scope.get("headers", []):
                if _hk == b"authorization":
                    _h = _hv.decode()
                    if _h.lower().startswith("bearer "):
                        _tok = _h[7:].strip()
                    break
            if not _tok:
                _tok = parse_qs(scope.get("query_string", b"").decode()).get("token", [""])[0] or None
            uid = _verify_token(_tok, os.environ["AUTH_SECRET"]) if _tok else None
            # A short-lived, GET-only media token may stand in for the session
            # token on read requests (img/video src, downloads) so the 30-day
            # token never has to ride in a query string. Never accept it for
            # mutations.
            if not uid and _tok and method == "GET":
                uid = _verify_media_token(_tok, os.environ["AUTH_SECRET"])
            if not uid:
                await send_error("Authentication required", 401)
                return
            uprefix = _user_prefix(uid)

        def _record_call(call):
            import time as _time
            try:
                calls_store[call.object_id] = {"uid": uid, "ts": _time.time()}
            except Exception:
                pass

        def _call_owned(call_id):
            meta = calls_store.get(call_id)
            return bool(meta) and meta.get("uid") == uid

        def _user_by_uid():
            """(username, record) for the authenticated uid.

            Fast path: reverse index key `uid:<uid>` → username (2 Dict RPCs).
            Fallback: legacy full scan (one RPC per user), backfilling the
            index so the next call is fast."""
            uname = users_store.get(f"uid:{uid}")
            if isinstance(uname, str):
                rec = users_store.get(uname)
                if rec and rec.get("uid") == uid:
                    return uname, rec
            for _u in users_store.keys():
                # Skip index entries — `uid:<uid>` and `email:<addr>` map to
                # username STRINGS, not user records; only dict values are records.
                if _u.startswith("uid:") or _u.startswith("email:"):
                    continue
                _r = users_store.get(_u)
                if not isinstance(_r, dict):
                    continue
                if _r.get("uid") == uid:
                    try:
                        users_store[f"uid:{uid}"] = _u
                    except Exception:
                        pass
                    return _u, _r
            return None, None

        # ── Session restore ──
        if path in ("/auth/me", "/auth/me/") and method == "GET":
            import os as _os
            uname, urec = _user_by_uid()
            is_admin, used, limit = _quota_state(urec, _os.environ.get("ADMIN_USERS"), uname)
            body = json.dumps({"username": uname,
                               "role": "admin" if is_admin else "user",
                               "videos_used": used,
                               "video_limit": None if is_admin else limit,
                               "email": (urec or {}).get("email", ""),
                               "email_verified": bool((urec or {}).get("email_verified", False)),
                               "marketing_consent": bool((urec or {}).get("marketing_consent", False))}).encode()
            await send({"type": "http.response.start", "status": 200,
                        "headers": CORS + [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": body})
            return

        # ── Verification email: (re)send, optionally setting the address ──
        if path in ("/auth/request-verification", "/auth/request-verification/") and method == "POST":
            import os as _os
            uname, urec = _user_by_uid()
            if not urec:
                await send_error("Unknown user", 400)
                return
            try:
                _d = json.loads((await _read_body(receive)).decode("utf-8"))
            except Exception:
                _d = {}
            _newemail = (_d.get("email") or "").strip().lower()
            if _newemail:
                if not _EMAIL_RE.match(_newemail):
                    await send_error("A valid email address is required", 400)
                    return
                _old = urec.get("email")
                if _old and _old != _newemail:
                    try:
                        del users_store[f"email:{_old}"]
                    except KeyError:
                        pass
                urec["email"] = _newemail
                urec["email_verified"] = False
                users_store[uname] = urec
                users_store[f"email:{_newemail}"] = uname
            _addr = urec.get("email")
            if not _addr:
                await send_error("Add an email address first", 400)
                return
            _vtok = _sign_scoped_token(uid, "verify", _os.environ["AUTH_SECRET"], EMAIL_VERIFY_TTL_SECONDS)
            _sent = _send_email(_addr, "Verify your email - פייפליין",
                                _email_html("Verify your email",
                                            "Tap the button to confirm this address so you can recover your account later.",
                                            "Verify email", f"{API_BASE_URL}/auth/verify?token={_vtok}"))
            body = json.dumps({"ok": True, "sent": bool(_sent), "email": _addr}).encode()
            await send({"type": "http.response.start", "status": 200,
                        "headers": CORS + [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": body})
            return

        # ── Mint a short-lived media token for GET media URLs ──
        if path in ("/auth/media-token", "/auth/media-token/") and method == "GET":
            import os as _os
            mtok = _sign_media_token(uid, _os.environ["AUTH_SECRET"])
            body = json.dumps({"token": mtok, "expires_in": MEDIA_TOKEN_TTL_SECONDS}).encode()
            await send({"type": "http.response.start", "status": 200,
                        "headers": CORS + [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": body})
            return

        # ── Metricool OAuth: start (redirect user to authorize) ──
        if path in ("/oauth/start", "/oauth/start/") and method == "GET":
            import secrets, hashlib, base64
            import urllib.parse as _up
            verifier = secrets.token_urlsafe(64)
            challenge = base64.urlsafe_b64encode(
                hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
            state = secrets.token_urlsafe(24)
            # Bind the flow to the initiating user — the callback is public
            # (no session), so the uid must travel with the state.
            oauth_store[f"pkce:{state}"] = {"verifier": verifier, "uid": uid}
            q = _up.urlencode({
                "response_type": "code", "client_id": MC_CLIENT_ID,
                "redirect_uri": MC_REDIRECT, "scope": MC_SCOPE, "state": state,
                "code_challenge": challenge, "code_challenge_method": "S256",
            })
            await send({"type": "http.response.start", "status": 302,
                        "headers": [(b"location", f"{MC_AUTHZ_URL}?{q}".encode())]})
            await send({"type": "http.response.body", "body": b""})
            return

        # ── Metricool OAuth: callback (exchange code, store refresh token) ──
        if path in ("/oauth/callback", "/oauth/callback/") and method == "GET":
            # NOTE: don't re-import parse_qs here — binding it locally would shadow
            # the api()-scope closure and UnboundLocalError every other route.
            import urllib.request, urllib.parse as _up
            qs = parse_qs(scope.get("query_string", b"").decode())
            code = qs.get("code", [""])[0]
            state = qs.get("state", [""])[0]
            pkce = oauth_store.get(f"pkce:{state}") if state else None
            # Backward-compat: older entries stored the bare verifier string.
            if isinstance(pkce, str):
                verifier, flow_uid = pkce, None
            elif isinstance(pkce, dict):
                verifier, flow_uid = pkce.get("verifier"), pkce.get("uid")
            else:
                verifier, flow_uid = None, None

            def _html(msg, ok=True):
                color = "#059669" if ok else "#DC2626"
                return (f"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
                        f"<body style='font-family:system-ui;background:#F2EEF8;color:#1E1033;display:flex;"
                        f"align-items:center;justify-content:center;height:100vh;margin:0;text-align:center'>"
                        f"<div style='background:#fff;border:1.5px solid #EDE9FE;border-radius:20px;padding:36px 28px;max-width:360px'>"
                        f"<div style='font-size:44px'>{'✅' if ok else '⚠️'}</div>"
                        f"<h2 style='color:{color};margin:12px 0 6px'>{'Metricool connected' if ok else 'Connection failed'}</h2>"
                        f"<p style='color:#6B7080;font-size:14px'>{msg}</p></div></body>").encode()

            if not code or not verifier or not flow_uid:
                await send({"type": "http.response.start", "status": 400,
                            "headers": [(b"content-type", b"text/html; charset=utf-8")]})
                await send({"type": "http.response.body", "body": _html("Missing or expired authorization. Please try connecting again.", ok=False)})
                return
            try:
                data = _up.urlencode({
                    "grant_type": "authorization_code", "code": code,
                    "redirect_uri": MC_REDIRECT, "client_id": MC_CLIENT_ID,
                    "code_verifier": verifier,
                }).encode()
                req = urllib.request.Request(MC_TOKEN_URL, data=data,
                                             headers={"Content-Type": "application/x-www-form-urlencoded"})
                tr = json.loads(await asyncio.to_thread(lambda: urllib.request.urlopen(req, timeout=30).read()))
                if not tr.get("refresh_token"):
                    raise RuntimeError("no refresh_token in response")
                oauth_store[f"tokens:{flow_uid}"] = {"refresh_token": tr["refresh_token"],
                                                     "access_token": tr.get("access_token")}
                try:
                    del oauth_store[f"pkce:{state}"]
                except KeyError:
                    pass
                page = _html("You can close this tab. Scheduling from the app is now enabled.")
            except Exception as e:
                page = _html(f"Token exchange failed: {str(e)[:120]}", ok=False)
            await send({"type": "http.response.start", "status": 200,
                        "headers": [(b"content-type", b"text/html; charset=utf-8")]})
            await send({"type": "http.response.body", "body": page})
            return

        # ── Metricool connection status ──
        if path in ("/oauth/status", "/oauth/status/") and method == "GET":
            connected = bool(oauth_store.get(f"tokens:{uid}"))
            body = json.dumps({"connected": connected}).encode()
            await send({"type": "http.response.start", "status": 200,
                        "headers": CORS + [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": body})
            return

        # ── Disconnect Metricool (drop this user's stored tokens) ──
        if path in ("/oauth/disconnect", "/oauth/disconnect/") and method == "POST":
            try:
                del oauth_store[f"tokens:{uid}"]
            except KeyError:
                pass
            await send({"type": "http.response.start", "status": 200,
                        "headers": CORS + [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": b'{"ok":true}'})
            return

        # ── Schedule a post (spawn) ──
        if path in ("/schedule", "/schedule/") and method == "POST":
            if not _check_rate_limit(_get_client_ip(scope)):
                await send_error("Rate limit exceeded. Try again in a minute.", 429, code="rate_limited")
                return
            if not oauth_store.get(f"tokens:{uid}"):
                await send_error("not_connected", 400)
                return
            body = await _read_body(receive)
            try:
                data = json.loads(body.decode("utf-8"))
                for _k in ("output_key", "video_key", "download_key"):
                    _v = data.get(_k)
                    if _v and not _owned_key(_v, uid):
                        await send_error("Forbidden", 403)
                        return
                call = schedule_post_fn.spawn(json.dumps(data), uid)
                _record_call(call)
                resp = json.dumps({"call_id": call.object_id}).encode()
                await send({"type": "http.response.start", "status": 202,
                            "headers": CORS + [(b"content-type", b"application/json")]})
                await send({"type": "http.response.body", "body": resp})
            except Exception as e:
                await send_error(str(e))
            return

        if path.startswith("/schedule-poll/") and method == "GET":
            call_id = path[len("/schedule-poll/"):].rstrip("/")
            if not _call_owned(call_id):
                await send_error("Forbidden", 403)
                return
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
            # key/index come from headers (fixed URL → one cached CORS preflight
            # for the whole upload). Fall back to the query string so an
            # old-frontend / new-backend deploy window still works.
            qs  = parse_qs(scope.get("query_string", b"").decode())
            _uh = {bytes(k).lower(): bytes(v).decode() for k, v in scope.get("headers", [])}
            key     = _uh.get(b"x-upload-key")   or qs.get("key",   [""])[0]
            idx_raw = _uh.get(b"x-upload-index") or qs.get("index", ["0"])[0]
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
            chunk_path = _Path(TMP_DIR) / f"{uprefix}{key}_chunk_{idx:04d}"
            # asyncio.to_thread so the blocking FUSE write doesn't freeze the event loop
            # (which would serialize all concurrent chunk requests despite max_inputs=20)
            await asyncio.to_thread(chunk_path.write_bytes, chunk_bytes)
            # No commit here — all chunks are committed once in /process before job spawn
            body = json.dumps({"ok": True}).encode()
            await send({"type": "http.response.start", "status": 200,
                        "headers": CORS + [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": body})
            return

        # Stream upload — a single whole-file upload written straight to the
        # Volume as chunk 0000, so the native background uploader (@capgo, raw
        # binary body) can hand off a large file in one request and it survives
        # the app being minimized. process_video's chunk glob finds this single
        # chunk and reassembles it exactly like the web multi-chunk path — no
        # worker change. The body is streamed to disk in ~4 MB flushes so a
        # hundreds-of-MB upload never buffers in RAM (unlike _read_body).
        if path in ("/upload_stream", "/upload_stream/") and method in ("POST", "PUT"):
            qs  = parse_qs(scope.get("query_string", b"").decode())
            _uh = {bytes(k).lower(): bytes(v).decode() for k, v in scope.get("headers", [])}
            key = _uh.get(b"x-upload-key") or qs.get("key", [""])[0]
            if not key or not _SAFE_KEY_RE.match(key):
                await send_error("Invalid or missing key", 400)
                return
            from pathlib import Path as _Path
            chunk_path = _Path(TMP_DIR) / f"{uprefix}{key}_chunk_0000"
            fh = await asyncio.to_thread(open, chunk_path, "wb")
            try:
                buf = bytearray()
                FLUSH = 4 * 1024 * 1024
                while True:
                    msg = await receive()
                    buf += msg.get("body", b"")
                    if len(buf) >= FLUSH:
                        data = bytes(buf); buf.clear()
                        await asyncio.to_thread(fh.write, data)
                    if not msg.get("more_body", False):
                        if buf:
                            await asyncio.to_thread(fh.write, bytes(buf))
                        break
            finally:
                await asyncio.to_thread(fh.close)
            body = json.dumps({"ok": True}).encode()
            await send({"type": "http.response.start", "status": 200,
                        "headers": CORS + [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": body})
            return

        # How many bytes of a streamed upload have landed (chunk 0000). Lets the
        # native client confirm a background upload finished even if its WebView
        # was frozen and missed the uploader's 'completed' event.
        if path in ("/upload_check", "/upload_check/") and method == "GET":
            qs  = parse_qs(scope.get("query_string", b"").decode())
            _uh = {bytes(k).lower(): bytes(v).decode() for k, v in scope.get("headers", [])}
            key = _uh.get(b"x-upload-key") or qs.get("key", [""])[0]
            if not key or not _SAFE_KEY_RE.match(key):
                await send_error("Invalid or missing key", 400)
                return
            from pathlib import Path as _Path
            cp = _Path(TMP_DIR) / f"{uprefix}{key}_chunk_0000"
            nbytes = cp.stat().st_size if cp.exists() else 0
            body = json.dumps({"bytes": nbytes}).encode()
            await send({"type": "http.response.start", "status": 200,
                        "headers": CORS + [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": body})
            return

        # Register this device's FCM token for "video ready" push notifications.
        if path in ("/push/register", "/push/register/") and method == "POST":
            if not uid:
                await send_error("unauthorized", 401)
                return
            raw = await _read_body(receive)
            try:
                data = json.loads(raw or b"{}")
            except Exception:
                data = {}
            token = (data.get("token") or "").strip()
            if not token or len(token) > 4096:
                await send_error("invalid token", 400)
                return
            try:
                toks = fcm_store.get(uid) or []
                if token not in toks:
                    fcm_store[uid] = ([token] + toks)[:10]   # cap tokens per user
            except Exception as e:
                print(f"[push] register failed: {e}")
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
            enhance_video        = qs.get("enhance_video",        ["none"])[0].lower()
            if enhance_video not in ("none", "filters", "esrgan"):
                enhance_video = "none"
            transcribe_for_broll = qs.get("transcribe_for_broll", ["false"])[0].lower() == "true"
            is_audio             = qs.get("is_audio", ["false"])[0].lower() == "true"
            min_silence          = float(qs.get("min_silence", ["0.5"])[0])
            padding              = float(qs.get("padding",     ["0.2"])[0])

            import os as _os, time as _time
            uname, urec = _user_by_uid()
            is_admin, used, limit = _quota_state(urec, _os.environ.get("ADMIN_USERS"), uname)
            if not is_admin:
                # Authoritative count from unique per-credit keys (race-proof);
                # the record's videos_used is a floor so existing testers'
                # usage isn't reset when this migrates in.
                used = max(used, _count_quota_used(quota_store, uid))
                if not _quota_allows(is_admin, used, limit):
                    await send_error("limit_reached", 402)
                    return
            try:
                if upload_key:
                    if not _SAFE_KEY_RE.match(upload_key):
                        await send_error("Invalid key", 400)
                        return
                    # Flush all chunks to persistent storage before spawning the worker
                    tmp_vol.commit()
                    call = process_video.spawn(
                        upload_key=uprefix + upload_key,
                        filename=filename,
                        cut_silences=cut_silences,
                        burn_captions=burn_captions,
                        min_silence=min_silence,
                        padding=padding,
                        enhance_audio=enhance_audio,
                        transcribe_for_broll=transcribe_for_broll,
                        key_prefix=uprefix,
                        enhance_video=enhance_video,
                        is_audio=is_audio,
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
                        key_prefix=uprefix,
                        enhance_video=enhance_video,
                        is_audio=is_audio,
                    )
                _record_call(call)
                if not is_admin and uname:
                    # Unique key = one consumed credit; atomic, so concurrent
                    # spawns can't clobber each other's increment.
                    try:
                        quota_store[f"{uid}:{call.object_id}"] = _time.time()
                    except Exception:
                        pass
                    urec["videos_used"] = used + 1
                    users_store[uname] = urec
                body = json.dumps({"call_id": call.object_id}).encode()
                await send({"type": "http.response.start", "status": 202,
                            "headers": CORS + [(b"content-type", b"application/json")]})
                await send({"type": "http.response.body", "body": body})
            except Exception as e:
                await send_error(str(e))
            return

        # ── Admin: usage overview + per-user limit control ──
        if path in ("/admin/users", "/admin/users/") and method == "GET":
            import os as _os
            uname, urec = _user_by_uid()
            caller_admin, _, _ = _quota_state(urec, _os.environ.get("ADMIN_USERS"), uname)
            if not caller_admin:
                await send_error("Forbidden", 403)
                return
            out = []
            for _u in users_store.keys():
                # Skip index entries (uid:<uid>, email:<addr>) — they map to
                # username STRINGS, not user records.
                if _u.startswith("uid:") or _u.startswith("email:"):
                    continue
                _r = users_store.get(_u)
                if not isinstance(_r, dict):
                    continue
                _adm, _used, _limit = _quota_state(_r, _os.environ.get("ADMIN_USERS"), _u)
                out.append({"username": _u, "role": "admin" if _adm else "user",
                            "videos_used": _used,
                            "video_limit": None if _adm else _limit,
                            "created": _r.get("created")})
            out.sort(key=lambda r: r.get("created") or 0)
            body = json.dumps({"users": out}).encode()
            await send({"type": "http.response.start", "status": 200,
                        "headers": CORS + [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": body})
            return

        if path in ("/admin/limit", "/admin/limit/") and method == "POST":
            import os as _os
            uname, urec = _user_by_uid()
            caller_admin, _, _ = _quota_state(urec, _os.environ.get("ADMIN_USERS"), uname)
            if not caller_admin:
                await send_error("Forbidden", 403)
                return
            try:
                data = json.loads((await _read_body(receive)).decode("utf-8"))
                target = (data.get("username") or "").strip().lower()
                new_limit = int(data.get("limit"))
            except Exception:
                await send_error("Invalid request body", 400)
                return
            if not users_store.contains(target):
                await send_error("Unknown user", 404)
                return
            if not (-1 <= new_limit <= 100000):
                await send_error("Limit must be between -1 (unlimited) and 100000", 400)
                return
            rec = users_store.get(target)
            rec["video_limit"] = new_limit
            users_store[target] = rec
            body = json.dumps({"ok": True, "username": target, "video_limit": new_limit}).encode()
            await send({"type": "http.response.start", "status": 200,
                        "headers": CORS + [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": body})
            return

        # ── Admin: reset a user's password ──
        if path in ("/admin/reset-password", "/admin/reset-password/") and method == "POST":
            import os as _os
            uname, urec = _user_by_uid()
            caller_admin, _, _ = _quota_state(urec, _os.environ.get("ADMIN_USERS"), uname)
            if not caller_admin:
                await send_error("Forbidden", 403)
                return
            try:
                data = json.loads((await _read_body(receive)).decode("utf-8"))
                target_raw = (data.get("username") or "").strip()
                new_password = data.get("new_password") or ""
            except Exception:
                await send_error("Invalid request body", 400)
                return
            # Resolve the record key: exact hit (legacy username accounts), then
            # lowercased (email-keyed accounts store a lowercased-email key).
            target = target_raw if users_store.contains(target_raw) else target_raw.lower()
            rec = users_store.get(target)
            if not isinstance(rec, dict):
                await send_error("Unknown user", 404)
                return
            if len(new_password) < 8:
                await send_error("Password must be at least 8 characters", 400)
                return
            salt, ph = _hash_password(new_password)
            rec["salt"] = salt
            rec["pw"] = ph
            users_store[target] = rec   # Dict has no partial update — write the whole record
            body = json.dumps({"ok": True, "username": target}).encode()
            await send({"type": "http.response.start", "status": 200,
                        "headers": CORS + [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": body})
            return

        # Poll process result — 200+binary when done, 202+JSON while running
        if path.startswith("/process_poll/") and method == "GET":
            call_id  = path[len("/process_poll/"):].rstrip("/")
            qs       = parse_qs(scope.get("query_string", b"").decode())
            filename = qs.get("filename", ["video.mp4"])[0]
            if not _call_owned(call_id):
                await send_error("Forbidden", 403)
                return
            try:
                import modal as _modal
                fn_call = _modal.functions.FunctionCall.from_id(call_id)
                result, still_running = _poll_fn_call(fn_call)
                if still_running:
                    running = {"status": "running"}
                    # Live stage progress, keyed by upload key (?key=)
                    upload_key = qs.get("key", [""])[0]
                    if upload_key and _SAFE_KEY_RE.match(upload_key):
                        try:
                            prog = progress_store.get(uprefix + upload_key)
                            if prog:
                                running["progress"] = prog
                        except Exception:
                            pass
                    body = json.dumps(running).encode()
                    await send({"type": "http.response.start", "status": 202,
                                "headers": CORS + [(b"content-type", b"application/json")]})
                    await send({"type": "http.response.body", "body": body})
                    return
                body = json.dumps(result).encode()
                await send({"type": "http.response.start", "status": 200,
                            "headers": CORS + [(b"content-type", b"application/json")]})
                await send({"type": "http.response.body", "body": body})
            except Exception as e:
                # A poll reaching here means the job terminally FAILED (_poll_fn_call
                # only raises on real failures; "still running" returns cleanly).
                # Refund the credit spent at spawn so a failed job never costs the
                # user a video. Deleting the unique quota key is the authoritative
                # refund (_count_quota_used scans these); videos_used is the display
                # floor. Idempotent: a repeat poll finds the key gone and no-ops, so
                # concurrent polls can't double-refund. Best-effort throughout.
                try:
                    del quota_store[f"{uid}:{call_id}"]
                    refunded = True
                except Exception:
                    refunded = False
                if refunded:
                    try:
                        _uname_r, _urec_r = _user_by_uid()
                        if _urec_r:
                            _urec_r["videos_used"] = max(0, int(_urec_r.get("videos_used", 1)) - 1)
                            users_store[_uname_r] = _urec_r
                    except Exception:
                        pass
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

            if not _owned_key(video_key, uid):
                await send_error("Forbidden", 403)
                return
            # Every B-roll item is fetched/composited server-side by the worker.
            # Validate each one here so a crafted item can't reach an arbitrary
            # file (path traversal / another tenant's video) or make the worker
            # fetch an arbitrary URL (SSRF).
            try:
                _broll_items = json.loads(broll_json)
            except Exception:
                _broll_items = []
            if not isinstance(_broll_items, list) or not all(_broll_item_safe(it) for it in _broll_items):
                await send_error("Forbidden", 403)
                return
            try:
                call = burn_captions_fn.spawn(video_key, captions_json, font, margin_v_pct, broll_json, font_size, hook_json, source_name=filename)
                _record_call(call)
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
            if not _call_owned(call_id):
                await send_error("Forbidden", 403)
                return
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


        # ── Job history: list burned videos ──
        if path in ("/jobs", "/jobs/") and method == "GET":
            try:
                jobs = []
                for key in list(jobs_store.keys()):
                    # Burned outputs (_out.mp4), terminal cut-only videos
                    # (_cut.mp4) AND audio-only outputs (_cut.m4a) are all
                    # recorded in jobs_store and belong in History.
                    if not key.endswith(("_out.mp4", "_cut.mp4", "_cut.m4a")) or not _owned_key(key, uid):
                        continue
                    meta = jobs_store.get(key) or {}
                    jobs.append({"key": key, "name": meta.get("name", "video"),
                                 "ts": meta.get("ts", 0), "size": meta.get("size", 0),
                                 "duration": meta.get("duration", 0)})
                jobs.sort(key=lambda j: j["ts"], reverse=True)
                body = json.dumps({"jobs": jobs}).encode()
                await send({"type": "http.response.start", "status": 200,
                            "headers": CORS + [(b"content-type", b"application/json")]})
                await send({"type": "http.response.body", "body": body})
            except Exception as e:
                await send_error(str(e))
            return

        # ── Job history: delete one entry + its file ──
        if path.startswith("/jobs/") and method == "DELETE":
            from pathlib import Path as _Path
            key = path[len("/jobs/"):].rstrip("/")
            if not key or not _SAFE_DOWNLOAD_KEY_RE.match(key) or not key.endswith(("_out.mp4", "_cut.mp4", "_cut.m4a")):
                await send_error("Invalid key", 400)
                return
            if not _owned_key(key, uid):
                await send_error("Forbidden", 403)
                return
            try:
                import asyncio as _asyncio
                def _rm():
                    fp = _Path(TMP_DIR) / key
                    if fp.exists():
                        fp.unlink()
                        tmp_vol.commit()
                    try:
                        jobs_store.pop(key)
                    except KeyError:
                        pass
                await _asyncio.to_thread(_rm)
                body = json.dumps({"ok": True}).encode()
                await send({"type": "http.response.start", "status": 200,
                            "headers": CORS + [(b"content-type", b"application/json")]})
                await send({"type": "http.response.body", "body": body})
            except Exception as e:
                await send_error(str(e))
            return

        # Render ONE WYSIWYG editor-preview frame through libass — the SAME
        # build_caption_ass + ffmpeg/libass path as the final burn, so the editor
        # preview is pixel-identical to the exported video (no browser-vs-libass
        # font-metric / outline / weight drift). Debounced client-side.
        if path in ("/preview_frame", "/preview_frame/") and method == "POST":
            from pathlib import Path as _Path
            import subprocess as _sp, tempfile as _tf, asyncio as _aio
            try:
                data = json.loads((await _read_body(receive)).decode("utf-8"))
                vk = data.get("video_key", "")
                if not (isinstance(vk, str) and _SAFE_DOWNLOAD_KEY_RE.match(vk)):
                    await send_error("Invalid key", 400); return
                if not _owned_key(vk, uid):
                    await send_error("Forbidden", 403); return
                src = _Path(TMP_DIR) / vk
                for _a in range(6):
                    tmp_vol.reload()
                    if src.exists():
                        break
                    await _aio.sleep(0.5)
                if not src.exists():
                    await send_error("File not found", 404); return

                t            = max(0.0, float(data.get("t", 0.0)))
                font         = str(data.get("font", "Heebo"))
                font_size    = int(data.get("font_size", 48))
                margin_v_pct = float(data.get("margin_v", 0.08))
                hook         = data.get("hook") or {}
                caps_in      = data.get("captions") or []

                def _render():
                    import json as _json
                    with _tf.TemporaryDirectory() as td:
                        td = _Path(td)
                        fr, ap, out = td / "f.png", td / "p.ass", td / "o.png"
                        # Extract the display-oriented frame (ffmpeg auto-applies
                        # any rotation), then size the ASS to the PNG's real dims —
                        # sidesteps rotation-metadata handling entirely.
                        _sp.run(["ffmpeg", "-y", "-ss", str(t), "-i", str(src),
                                 "-frames:v", "1", str(fr)],
                                stdout=_sp.DEVNULL, stderr=_sp.PIPE, timeout=60, check=True)
                        pr = _sp.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                                      "-show_entries", "stream=width,height", "-of", "json", str(fr)],
                                     capture_output=True, text=True, check=True)
                        st = _json.loads(pr.stdout)["streams"][0]
                        W, Hh = int(st["width"]), int(st["height"])
                        margin_h = max(25, W // 14)
                        margin_v = int(margin_v_pct * Hh)
                        # Only the caption active at t (retimed onto the still frame).
                        active = [c for c in caps_in
                                  if float(c.get("start", 0)) <= t <= float(c.get("end", 0)) + 0.05]
                        caps = [{"start": 0.0, "end": 9.0, "text": active[0].get("text", "")}] if active else []
                        hk = {}
                        if hook.get("text"):
                            hs = float(hook.get("start_seconds", 1.0))
                            hd = float(hook.get("duration_seconds", 4.0))
                            if hs <= t <= hs + hd:
                                hk = dict(hook); hk["start_seconds"] = 0.0; hk["duration_seconds"] = 9.0
                        ass = build_caption_ass(W, Hh, font, font_size, margin_h, margin_v, caps, hk)
                        ap.write_text(ass, encoding="utf-8")
                        _sp.run(["ffmpeg", "-y", "-i", str(fr), "-vf", f"ass={ap}",
                                 "-frames:v", "1", str(out)],
                                stdout=_sp.DEVNULL, stderr=_sp.PIPE, timeout=60, check=True)
                        return out.read_bytes()

                png = await _aio.to_thread(_render)
                await send({"type": "http.response.start", "status": 200,
                            "headers": CORS + [(b"content-type", b"image/png"),
                                               (b"cache-control", b"no-store")]})
                await send({"type": "http.response.body", "body": png})
            except Exception as e:
                print(f"[preview_frame] ERROR: {e!r}")
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
            if not _owned_key(key, uid):
                await send_error("Forbidden", 403)
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
                _ctype = b"audio/mp4" if key.endswith(".m4a") else b"video/mp4"
                resp_headers = CORS + [
                    (b"content-type",        _ctype),
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
                    # Range replies (206, preview seeking) stay small so playback
                    # starts fast; a full attachment download (200) uses a bigger
                    # chunk to cut per-chunk await overhead and lift throughput.
                    CHUNK = (1024 * 1024) if status == 200 else (256 * 1024)
                    while remaining > 0:
                        chunk = f.read(min(CHUNK, remaining))
                        if not chunk:
                            break
                        remaining -= len(chunk)
                        await send({"type": "http.response.body",
                                    "body": chunk, "more_body": remaining > 0})
                # NOTE: do NOT delete _out.mp4 here. The burned video must survive the
                # browser download so Metricool can still fetch it for scheduling. Cleanup
                # happens after a successful schedule (schedule_post_fn).
            except Exception as e:
                print(f"[download] ERROR key={key!r} response_started={response_started} err={e!r}")
                if not response_started:
                    await send_error(str(e))
                # If response already started, the connection will close ungracefully —
                # nothing useful we can send at this point.
            return

        # Media — serve a burned video INLINE (video/mp4), no attachment, no delete.
        # Used as the public URL Metricool ingests when scheduling. URL ends in the
        # key (…_out.mp4) so external fetchers see a proper .mp4 extension.
        if path.startswith("/media/") and method == "GET":
            from pathlib import Path as _Path
            import asyncio as _asyncio
            key = path[len("/media/"):].rstrip("/")
            if not key or not _SAFE_DOWNLOAD_KEY_RE.match(key):
                await send_error("Invalid key", 400)
                return
            response_started = False
            try:
                _base = str(_Path(TMP_DIR).resolve())
                file_path = _Path(TMP_DIR) / key
                if not str(file_path.resolve()).startswith(_base + "/") and \
                        str(file_path.resolve()) != _base:
                    raise ValueError("Forbidden path")
                for _attempt in range(10):
                    try:
                        tmp_vol.reload()
                        if file_path.exists():
                            break
                    except RuntimeError as _ve:
                        if "open files" not in str(_ve):
                            raise
                    if _attempt < 9:
                        await _asyncio.sleep(1)
                else:
                    raise FileNotFoundError(f"File not found: {key}")

                file_size = file_path.stat().st_size
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
                    end = min(end, file_size - 1)
                    status = 206
                content_length = end - start + 1
                resp_headers = CORS + [
                    (b"content-type",   b"video/mp4"),
                    (b"accept-ranges",  b"bytes"),
                    (b"content-length", str(content_length).encode()),
                    (b"content-disposition", b"inline"),
                ]
                if status == 206:
                    resp_headers.append((b"content-range", f"bytes {start}-{end}/{file_size}".encode()))
                response_started = True
                await send({"type": "http.response.start", "status": status, "headers": resp_headers})
                with open(file_path, "rb") as f:
                    f.seek(start)
                    remaining = content_length
                    while remaining > 0:
                        chunk = f.read(min(256 * 1024, remaining))
                        if not chunk:
                            break
                        remaining -= len(chunk)
                        await send({"type": "http.response.body", "body": chunk, "more_body": remaining > 0})
            except Exception as e:
                print(f"[media] ERROR key={key!r} err={e!r}")
                if not response_started:
                    await send_error(str(e))
            return

        # Thumbnail — extract a single JPEG frame at 1s for preview use
        if path.startswith("/thumbnail/") and method == "GET":
            from pathlib import Path as _Path
            import time as _time_mod
            key = path[len("/thumbnail/"):].rstrip("/")
            if not key or not _SAFE_DOWNLOAD_KEY_RE.match(key):
                await send_error("Invalid key", 400)
                return
            if not _owned_key(key, uid):
                await send_error("Forbidden", 403)
                return
            # Audio outputs have no video frame to grab — the frontend shows an
            # audio glyph instead and should not call this, but be defensive.
            if key.endswith(".m4a"):
                await send_error("No thumbnail for audio", 404)
                return
            try:
                import subprocess as _sp, asyncio as _asyncio
                file_path = _Path(TMP_DIR) / key
                # Cached thumbnail from a previous request — serve instantly
                # instead of re-running ffmpeg (~5s on a large file).
                cache_path = _Path(TMP_DIR) / (key + ".jpg")
                _thumb_headers = CORS + [(b"content-type", b"image/jpeg"),
                                         (b"cache-control", b"max-age=86400")]
                if cache_path.exists():
                    await send({"type": "http.response.start", "status": 200,
                                "headers": _thumb_headers})
                    await send({"type": "http.response.body", "body": cache_path.read_bytes()})
                    return
                for _attempt in range(10):
                    try:
                        tmp_vol.reload()
                        if cache_path.exists():
                            await send({"type": "http.response.start", "status": 200,
                                        "headers": _thumb_headers})
                            await send({"type": "http.response.body", "body": cache_path.read_bytes()})
                            return
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
                try:
                    cache_path.write_bytes(r.stdout)
                    tmp_vol.commit()
                except Exception as _ce:
                    print(f"[thumbnail] cache write skipped: {_ce!r}")
                await send({"type": "http.response.start", "status": 200,
                            "headers": _thumb_headers})
                await send({"type": "http.response.body", "body": r.stdout})
            except Exception as e:
                print(f"[thumbnail] ERROR key={key!r} err={e!r}")
                await send_error(str(e))
            return


        if path.startswith("/cancel/") and method in ("GET", "POST", "DELETE"):
            call_id = path[len("/cancel/"):].rstrip("/")
            if not _call_owned(call_id):
                await send_error("Forbidden", 403)
                return
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
                await send_error("Rate limit exceeded. Try again in a minute.", 429, code="rate_limited")
                return
            body = await _read_body(receive)
            data = json.loads(body.decode("utf-8"))
            try:
                _vk = data.get("video_key", "")
                if _vk and not _owned_key(_vk, uid):
                    await send_error("Forbidden", 403)
                    return
                call = analyze_stock_broll.spawn(
                    data.get("captions_json", "[]"),
                    _vk,
                    data.get("orientation", "portrait"),
                )
                _record_call(call)
                resp = json.dumps({"call_id": call.object_id}).encode()
                await send({"type": "http.response.start", "status": 202,
                            "headers": CORS + [(b"content-type", b"application/json")]})
                await send({"type": "http.response.body", "body": resp})
            except Exception as e:
                await send_error(str(e))
            return

        if path.startswith("/stock-broll-poll/") and method == "GET":
            call_id = path[len("/stock-broll-poll/"):].rstrip("/")
            if not _call_owned(call_id):
                await send_error("Forbidden", 403)
                return
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
                    data.get("orientation", "portrait"),
                )
                _record_call(call)
                resp = json.dumps({"call_id": call.object_id}).encode()
                await send({"type": "http.response.start", "status": 202,
                            "headers": CORS + [(b"content-type", b"application/json")]})
                await send({"type": "http.response.body", "body": resp})
            except Exception as e:
                await send_error(str(e))
            return

        if path.startswith("/stock-broll-clips-poll/") and method == "GET":
            call_id = path[len("/stock-broll-clips-poll/"):].rstrip("/")
            if not _call_owned(call_id):
                await send_error("Forbidden", 403)
                return
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
                await send_error("Rate limit exceeded. Try again in a minute.", 429, code="rate_limited")
                return
            body = await _read_body(receive)
            data = json.loads(body.decode("utf-8"))
            try:
                _vk = data.get("video_key", "")
                if _vk and not _owned_key(_vk, uid):
                    await send_error("Forbidden", 403)
                    return
                call = generate_hook_options.spawn(
                    data.get("captions_json", "[]"),
                    _vk,
                )
                _record_call(call)
                resp = json.dumps({"call_id": call.object_id}).encode()
                await send({"type": "http.response.start", "status": 202,
                            "headers": CORS + [(b"content-type", b"application/json")]})
                await send({"type": "http.response.body", "body": resp})
            except Exception as e:
                await send_error(str(e))
            return

        if path.startswith("/generate-hook-poll/") and method == "GET":
            call_id = path[len("/generate-hook-poll/"):].rstrip("/")
            if not _call_owned(call_id):
                await send_error("Forbidden", 403)
                return
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

        # Caption generation (suggested social caption from transcript)
        if path in ("/generate-caption", "/generate-caption/") and method == "POST":
            if not _check_rate_limit(_get_client_ip(scope)):
                await send_error("Rate limit exceeded. Try again in a minute.", 429, code="rate_limited")
                return
            body = await _read_body(receive)
            data = json.loads(body.decode("utf-8"))
            try:
                _vk = data.get("video_key", "")
                if _vk and not _owned_key(_vk, uid):
                    await send_error("Forbidden", 403)
                    return
                call = generate_caption_options.spawn(
                    data.get("captions_json", "[]"),
                    _vk,
                    data.get("platforms", ""),
                )
                _record_call(call)
                resp = json.dumps({"call_id": call.object_id}).encode()
                await send({"type": "http.response.start", "status": 202,
                            "headers": CORS + [(b"content-type", b"application/json")]})
                await send({"type": "http.response.body", "body": resp})
            except Exception as e:
                await send_error(str(e))
            return

        if path.startswith("/generate-caption-poll/") and method == "GET":
            call_id = path[len("/generate-caption-poll/"):].rstrip("/")
            if not _call_owned(call_id):
                await send_error("Forbidden", 403)
                return
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
