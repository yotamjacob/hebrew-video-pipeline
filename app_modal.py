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
    jobs_store, progress_store, users_store, calls_store, quota_store, purchases_store, fcm_store,
    pending_store, errors_store, _alert_admins, _send_push, costs_store, _cost_summary, stats_store,
    _record_ai_spend, _face_center_from_image,
    codes_store, _normalize_email, _gen_login_code,
    _verify_google_id_token, GOOGLE_WEB_CLIENT_ID,
    app, image, tmp_vol, TMP_DIR,
    _SAFE_KEY_RE, _SAFE_DOWNLOAD_KEY_RE, _SAFE_VARIANT_RE, _check_rate_limit, _get_client_ip,
    throttle_store, _throttle_allowed, _throttle_record_fail, _throttle_clear,
    _poll_fn_call,
    _hash_password, _verify_password, _sign_token, _verify_token,
    _sign_media_token, _verify_media_token, MEDIA_TOKEN_TTL_SECONDS, API_BASE_URL,
    _sanitize_error_context, _count_similar_errors,
    _error_alert_title, _error_alert_body,
    _EMAIL_RE, _sign_scoped_token, _verify_scoped_token, _send_email, _email_html,
    EMAIL_VERIFY_TTL_SECONDS,
    _user_prefix, _owned_key, _broll_item_safe,
    SONNET_MODEL,
    _is_review_email, _review_login_ok, REVIEW_VIDEO_LIMIT,
    DEFAULT_VIDEO_LIMIT, LEGACY_VIDEO_LIMIT, _quota_state, _quota_allows,
    _count_quota_used, _credit_cost, _upscale_allowed, _charge_credits,
    _refund_credits, UPSCALE_MAX_SECONDS,
    PLAY_PACKAGE_NAME, PLAY_CREDIT_PRODUCTS, _billing_account_id,
    _purchase_ledger_key, _count_purchased_credits, _parse_play_purchase,
    _apply_voided_play_purchases,
    APNS_TOKEN_PREFIX,
    APPLE_BUNDLE_ID, APPLE_CREDIT_PRODUCTS, _apple_account_token,
    _apple_ledger_key, _decode_jws_payload, _parse_apple_transaction,
    _revoke_purchase_grant, PURCHASE_KEY_PREFIXES,
)

# Public base URLs used to build email links: API_BASE_URL (imported from
# pipeline_core) is stable; SITE_URL can be overridden via env once a custom
# domain is set.


def _google_play_api(method: str, url: str):
    """Authenticated Android Publisher request using the mounted service account."""
    import json as _json
    import os as _os
    import requests as _requests
    from google.oauth2 import service_account
    from google.auth.transport.requests import Request

    raw = _os.environ.get("PLAY_SERVICE_ACCOUNT_JSON") or _os.environ.get("FCM_SERVICE_ACCOUNT")
    if not raw:
        raise RuntimeError("play_billing_credentials_missing")
    info = _json.loads(raw)
    creds = service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/androidpublisher"])
    creds.refresh(Request())
    response = _requests.request(
        method, url, headers={"Authorization": f"Bearer {creds.token}"},
        timeout=20)
    if not response.ok:
        raise RuntimeError(f"play_api_{response.status_code}")
    return response.json() if response.content else {}


def _fetch_google_play_purchase(product_id: str, purchase_token: str):
    from urllib.parse import quote
    package = quote(PLAY_PACKAGE_NAME, safe="")
    token = quote(purchase_token, safe="")
    return _google_play_api(
        "GET",
        f"https://androidpublisher.googleapis.com/androidpublisher/v3/"
        f"applications/{package}/purchases/productsv2/tokens/{token}")


def _consume_google_play_purchase(product_id: str, purchase_token: str):
    from urllib.parse import quote
    package = quote(PLAY_PACKAGE_NAME, safe="")
    product = quote(product_id, safe="")
    token = quote(purchase_token, safe="")
    return _google_play_api(
        "POST",
        f"https://androidpublisher.googleapis.com/androidpublisher/v3/"
        f"applications/{package}/purchases/products/{product}/tokens/{token}:consume")


# ── Apple App Store Server API ────────────────────────────────────────────────
# Apple's counterpart to the Android Publisher calls above. Same trust model:
# the client hands over an identifier, the SERVER asks Apple what that purchase
# actually is, and only Apple's answer can grant credits.
APPLE_API_HOSTS = (
    "https://api.storekit.itunes.apple.com",
    "https://api.storekit-sandbox.itunes.apple.com",
)


def _appstore_jwt() -> str:
    """ES256 bearer token for the App Store Server API.

    Signed with an App Store Connect API key (Key ID + Issuer ID + .p8). The
    `bid` claim scopes it to our bundle, so a leaked token cannot read another
    app's transactions.
    """
    import os as _os
    import time as _time
    import jwt as _jwt

    key_id = (_os.environ.get("APPLE_KEY_ID") or "").strip()
    issuer_id = (_os.environ.get("APPLE_ISSUER_ID") or "").strip()
    private_key = _os.environ.get("APPLE_PRIVATE_KEY") or ""
    if not (key_id and issuer_id and private_key.strip()):
        raise RuntimeError("apple_billing_credentials_missing")
    # A .p8 pasted into a secret through a shell often arrives with literal
    # backslash-n instead of real newlines; both forms must work.
    if "\\n" in private_key and "\n" not in private_key.strip("\n"):
        private_key = private_key.replace("\\n", "\n")
    now = int(_time.time())
    return _jwt.encode(
        {"iss": issuer_id, "iat": now, "exp": now + 900,
         "aud": "appstoreconnect-v1", "bid": APPLE_BUNDLE_ID},
        private_key,
        algorithm="ES256",
        headers={"kid": key_id, "typ": "JWT"})


def _fetch_apple_transaction(transaction_id: str) -> dict:
    """Decoded transaction info straight from Apple, production first.

    Apple documents no way to know up front whether a transaction is a
    production or a Sandbox one (App Review and TestFlight are Sandbox), so the
    recommended pattern is production first and sandbox on 404.
    """
    from urllib.parse import quote
    import requests as _requests

    token = _appstore_jwt()
    txn = quote(str(transaction_id), safe="")
    for host in APPLE_API_HOSTS:
        response = _requests.get(
            f"{host}/inApps/v1/transactions/{txn}",
            headers={"Authorization": f"Bearer {token}"}, timeout=20)
        if response.status_code == 404:
            continue          # not in this environment - try the next host
        if not response.ok:
            raise RuntimeError(f"apple_api_{response.status_code}")
        signed = (response.json() or {}).get("signedTransactionInfo")
        if not signed:
            raise RuntimeError("apple_api_empty")
        return _decode_jws_payload(signed)
    raise ValueError("purchase_not_found")


@app.function(image=light_image, schedule=modal.Period(hours=6),
              secrets=[modal.Secret.from_name("hebpipe-fcm")])
def reconcile_voided_play_purchases() -> dict:
    """Revoke credits for Play refunds/chargebacks (last 30 days, paginated)."""
    import time
    from urllib.parse import quote

    package = quote(PLAY_PACKAGE_NAME, safe="")
    base = (
        "https://androidpublisher.googleapis.com/androidpublisher/v3/"
        f"applications/{package}/purchases/voidedpurchases"
        "?type=0&pageSelection.maxResults=1000"
    )
    rows = []
    page_token = None
    try:
        while True:
            url = base
            if page_token:
                url += "&pageSelection.token=" + quote(page_token, safe="")
            result = _google_play_api("GET", url)
            rows.extend(result.get("voidedPurchases") or [])
            page_token = (result.get("tokenPagination") or {}).get("nextPageToken")
            if not page_token:
                break
        revoked = _apply_voided_play_purchases(
            purchases_store, rows, time.time())
        print(f"[billing] reconciled {len(rows)} voided purchase(s); revoked {revoked}")
        return {"ok": True, "seen": len(rows), "revoked": revoked}
    except Exception as exc:
        # A deploy before Play Console permission is configured must stay
        # healthy. The next scheduled pass retries automatically.
        print(f"[billing] voided-purchase reconciliation unavailable: {exc!r}")
        return {"ok": False, "error": str(exc)[:120]}


async def _receive_stream_upload(receive, write_chunk, flush_bytes=4 * 1024 * 1024):
    """Copy one ASGI request body without mistaking a disconnect for EOF.

    Returns (complete, bytes_received). Only an ``http.request`` message with
    ``more_body=False`` is a successful end-of-file. Android's uploader retries
    weak-network disconnects; publishing a disconnected attempt would expose a
    truncated video to ffprobe while the retry was still uploading.
    """
    buffered = bytearray()
    received = 0
    while True:
        message = await receive()
        if message.get("type") == "http.disconnect":
            return False, received
        if message.get("type") != "http.request":
            return False, received
        body = message.get("body", b"")
        if body:
            buffered += body
            received += len(body)
        if len(buffered) >= flush_bytes:
            await write_chunk(bytes(buffered))
            buffered.clear()
        if not message.get("more_body", False):
            if buffered:
                await write_chunk(bytes(buffered))
            return True, received


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
from assembler_fns import analyze_story, render_story
from metricool_fns import (
    schedule_post_fn, oauth_store, _mc_refresh_access_token, _mcp_tool_call,
    _build_metricool_info,
    MC_MCP_URL, MC_AUTHZ_URL, MC_TOKEN_URL, MC_CLIENT_ID, MC_REDIRECT,
    MC_SCOPE, MC_BLOG_ID, MC_PROTO,
)

# ---------------------------------------------------------------------------
def _spawn_pending_job_impl(full_key, expect_uid=None, fallback_uprefix=None):
    """Claim + spawn the DEFERRED processing job for a completed upload.

    Called from the upload endpoints the moment the last byte lands, so
    processing starts even when the app was closed mid-upload (its JS -
    which used to do the spawn - is frozen then). The atomic pop is the
    claim: two racing completion signals can't double-spawn/double-bill.
    Returns the call_id, or None when there is nothing (left) to spawn.

    expect_uid: on the request path, the caller must be the registrant.
    The R2 sweep passes None - it acts ON the registration record itself
    (written by an authenticated /process defer call), so the record's own
    uid is the authority there.
    """
    import time as _time
    try:
        rec = pending_store.pop(full_key)
    except KeyError:
        return None
    except Exception:
        return None
    if not isinstance(rec, dict) or "params" not in rec:
        return None
    if expect_uid is not None and rec.get("uid") != expect_uid:
        try:
            pending_store[full_key] = rec
        except Exception:
            pass
        return None
    try:
        tmp_vol.commit()           # flush chunks before the worker reads
        call = process_video.spawn(
            upload_key=full_key,
            key_prefix=rec.get("uprefix", fallback_uprefix),
            # Admins are not charged, so there is nothing for the
            # worker to verify the source length against.
            credits_charged=(0 if rec.get("is_admin")
                             else int(rec.get("credit_cost") or 1)),
            **rec["params"])
    except Exception as e:
        print(f"[pending] spawn failed for {full_key!r}: {e!r}")
        try:
            pending_store[full_key] = rec   # restore so a retry can claim
        except Exception:
            pass
        return None
    try:
        calls_store[call.object_id] = {"uid": rec["uid"], "ts": _time.time()}
    except Exception:
        pass
    if not rec.get("is_admin") and rec.get("uname"):
        try:
            _cost = int(rec.get("credit_cost") or 1)
            _charge_credits(quota_store, rec["uid"], call.object_id, _cost)
            _urec = users_store.get(rec["uname"])
            if isinstance(_urec, dict):
                _urec["videos_used"] = int(_urec.get("videos_used") or 0) + _cost
                users_store[rec["uname"]] = _urec
        except Exception:
            pass
    try:
        pending_store["done:" + full_key] = {
            "call_id": call.object_id, "uid": rec["uid"], "ts": _time.time()}
    except Exception:
        pass
    print(f"[pending] spawned {call.object_id} for {full_key!r}")
    return call.object_id


def _r2_client_env():
    """boto3 client + bucket from the hebpipe-backup secret env, or (None, None)."""
    import os as _os
    if not _os.environ.get("S3_ENDPOINT_URL") or not _os.environ.get("S3_BUCKET"):
        return None, None
    import boto3
    client = boto3.client(
        "s3",
        endpoint_url=_os.environ["S3_ENDPOINT_URL"],
        aws_access_key_id=_os.environ["S3_ACCESS_KEY_ID"],
        aws_secret_access_key=_os.environ["S3_SECRET_ACCESS_KEY"],
        region_name=_os.environ.get("S3_REGION", "auto"),
    )
    return client, _os.environ["S3_BUCKET"]


def _r2_land_on_volume(s3, bucket, obj_key, full_key):
    """Stream a completed R2 upload object onto the volume as chunk 0000
    (staging + atomic replace - the /upload_stream shape). Idempotent: an
    already-landed chunk file short-circuits. Deletes the object on success."""
    import os as _os
    import uuid as _uuid
    from pathlib import Path as _Path
    chunk_path = _Path(TMP_DIR) / f"{full_key}_chunk_0000"
    if chunk_path.exists():
        return True
    staging = _Path(TMP_DIR) / f".{full_key}.{_uuid.uuid4().hex}.uploading"
    try:
        obj = s3.get_object(Bucket=bucket, Key=obj_key)
        with open(staging, "wb") as fh:
            for part in iter(lambda: obj["Body"].read(8 * 1024 * 1024), b""):
                fh.write(part)
        _os.replace(staging, chunk_path)
        # Commit HERE, in the container that wrote the file. The next request
        # (assembler /analyze, or a /process spawn) may land on a DIFFERENT
        # api container whose commit() can't publish this one's writes - the
        # worker then sees no chunk ("No upload found", field 2026-08-16,
        # R2 upload followed immediately by /assembler/analyze).
        tmp_vol.commit()
    except BaseException:
        staging.unlink(missing_ok=True)
        raise
    try:
        s3.delete_object(Bucket=bucket, Key=obj_key)
    except Exception:
        pass   # lifecycle sweeps orphans
    return True


@app.function(image=light_image, schedule=modal.Period(minutes=5),
              volumes={TMP_DIR: tmp_vol},
              secrets=[modal.Secret.from_name("hebpipe-backup")])
def sweep_r2_uploads():
    """Close the close-and-forget gap for native R2 uploads: the Android
    foreground service PUTs the file to R2 and the object appears atomically,
    but if the app never returns to the foreground nobody calls
    /upload_r2/complete - the registered job would sit pending forever and the
    'upload done' push would never fire. Every 5 minutes: for each pending
    registration whose bytes are not on the volume yet, check R2 for the
    completed object; if present, land it and spawn. Races with an in-app
    /complete are safe: the volume copy is idempotent and the spawn claim is
    an atomic pop."""
    s3, bucket = _r2_client_env()
    if s3 is None:
        return
    from pathlib import Path as _Path
    import time as _time
    swept = 0
    try:
        keys = [k for k in pending_store.keys() if not k.startswith("done:")]
    except Exception as e:
        print(f"[r2sweep] pending scan failed: {e!r}")
        return
    for full_key in keys:
        rec = pending_store.get(full_key)
        if not isinstance(rec, dict) or "params" not in rec:
            continue
        # Give a just-registered upload time to land through the normal path.
        if _time.time() - float(rec.get("ts") or 0) < 60:
            continue
        if (_Path(TMP_DIR) / f"{full_key}_chunk_0000").exists():
            continue   # landed - the request path's spawn is in flight
        obj_key = f"uploads/{full_key}.src"
        try:
            s3.head_object(Bucket=bucket, Key=obj_key)
        except Exception:
            continue   # not uploaded (or chunked path) - nothing to do
        try:
            _r2_land_on_volume(s3, bucket, obj_key, full_key)
            call_id = _spawn_pending_job_impl(full_key)
            if call_id:
                swept += 1
                print(f"[r2sweep] landed + spawned {call_id} for {full_key!r}")
        except Exception as e:
            print(f"[r2sweep] failed for {full_key!r}: {e!r}")
    if swept:
        print(f"[r2sweep] recovered {swept} upload(s)")


# HTTP API — raw ASGI, zero external dependencies, dispatches to process_video
#
# POST /process?filename=x&cut_silences=true&burn_captions=true&min_silence=0.5&padding=0.2
#   Body: raw video bytes
#   Returns: processed video bytes (video/mp4)
# ---------------------------------------------------------------------------
@app.function(image=light_image, timeout=900, volumes={TMP_DIR: tmp_vol},
              # Keep a served container alive 5 min after its last request
              # (default is 60s). The boot spinner "waking up the server" is
              # ONE /auth/me call waiting on a cold container: measured 4.5s
              # cold vs 0.67s warm (2026-08-10). A 5-minute window means a
              # user session - and any second visit soon after - never pays
              # that again, while the app still scales to zero when genuinely
              # idle. Only the router gets this: keeping the L4 (process_video)
              # or the 8-core burn container warm would cost dollars per hour
              # for no user-visible gain, since both are entered from a screen
              # that is already showing progress.
              # A cold start is also worst right AFTER a deploy - every deploy
              # is a new image version the first container must pull.
              scaledown_window=300,
              secrets=[modal.Secret.from_name("hebpipe-auth"),
                       # FCM: admin error-alert pushes from /error-report and
                       # the terminal-failure poll path.
                       modal.Secret.from_name("hebpipe-fcm"),
                       # REVIEW_EMAIL + REVIEW_CODE — the Play-review demo login.
                       # Its OWN secret, not a key inside hebpipe-auth: adding a
                       # key there needs a `--force` rewrite that REPLACES every
                       # key, and the existing values can't be read back. To
                       # disable the demo login, blank REVIEW_CODE (anything
                       # under 6 chars is refused) - don't delete the secret, or
                       # the deploy loses a reference it still expects.
                       modal.Secret.from_name("hebpipe-review"),
                       # APPLE_KEY_ID + APPLE_ISSUER_ID + APPLE_PRIVATE_KEY:
                       # the App Store Connect API key that authenticates
                       # /billing/apple/verify against Apple. Its own secret for
                       # the same reason as hebpipe-review. Absent/blank simply
                       # makes the Apple billing routes answer 503, so a deploy
                       # before the key exists stays healthy.
                       modal.Secret.from_name("hebpipe-apple"),
                       # S3/R2 creds: presigned direct-upload URLs (/upload_r2/*)
                       # use the same bucket as the metadata backup.
                       modal.Secret.from_name("hebpipe-backup")])
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
        # content-range + content-length let the frontend's parallel range
        # downloader (share warm-up) read the file size from a 206 reply.
        (b"access-control-expose-headers", b"content-disposition, content-range, content-length, accept-ranges"),
    ]

    async def _read_body(receive):
        body = b""
        while True:
            msg = await receive()
            body += msg.get("body", b"")
            if not msg.get("more_body", False):
                return body

    # /stats result, cached per container (the landing page polls anonymously;
    # a full quota_store scan per hit would be silly).
    _stats_cache = {"ts": 0.0, "videos": 0}

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

        def _purge_user_data(t_uid, t_uname, t_urec):
            """Blocking full-account purge, shared by self-service
            /account/delete and admin /admin/delete-user. Everything keyed to
            the uid goes: rendered videos on the volume, job history, cost
            rows, consumed-credit keys, spawn ownership, push tokens, deferred
            uploads, error reports, and the account record with both of its
            index entries. Purchase grants are ANONYMIZED rather than dropped
            (uid removed, state revoked) - they are financial records tied to a
            store order, and once revoked they hold no personal data and can
            never grant credits again. The email itself stays free, so the
            person can sign up again fresh. Run via asyncio.to_thread."""
            from pathlib import Path as _Path
            import time as _time
            removed_files = 0
            prefix = _user_prefix(t_uid)
            # 1. Rendered/uploaded media on the volume + its history + costs.
            try:
                root = _Path(TMP_DIR)
                for fp in root.glob(f"{prefix}*"):
                    try:
                        if fp.is_file():
                            fp.unlink()
                            removed_files += 1
                    except Exception:
                        pass
                if removed_files:
                    tmp_vol.commit()
            except Exception as exc:
                print(f"[delete] volume sweep failed: {exc!r}")
            for store, matches in (
                (jobs_store, lambda k: k.startswith(prefix)),
                (costs_store, lambda k: k.startswith(prefix)),
                (progress_store, lambda k: k.startswith(prefix)),
                (pending_store, lambda k: k.startswith(prefix) or k.startswith("done:" + prefix)),
                (quota_store, lambda k: k.startswith(f"{t_uid}:")),
                # Error reports are keyed e:{ms}:{uid[:8]}.
                (errors_store, lambda k: k.startswith("e:") and k.endswith(":" + t_uid[:8])),
            ):
                try:
                    for key in [k for k in store.keys()
                                if isinstance(k, str) and matches(k)]:
                        try:
                            store.pop(key)
                        except Exception:
                            pass
                except Exception as exc:
                    print(f"[delete] store sweep failed: {exc!r}")
            # 2. Spawn ownership rows for this user.
            try:
                for key in [k for k in calls_store.keys()
                            if isinstance(calls_store.get(k), dict)
                            and calls_store.get(k, {}).get("uid") == t_uid]:
                    try:
                        calls_store.pop(key)
                    except Exception:
                        pass
            except Exception as exc:
                print(f"[delete] calls sweep failed: {exc!r}")
            # 3. Push tokens.
            try:
                fcm_store.pop(t_uid)
            except Exception:
                pass
            # 4. Purchase grants → anonymized, never resurrectable.
            try:
                for key in [k for k in purchases_store.keys()
                            if isinstance(k, str) and k.startswith(PURCHASE_KEY_PREFIXES)]:
                    rec = purchases_store.get(key)
                    if isinstance(rec, dict) and rec.get("uid") == t_uid:
                        anon = dict(rec)
                        anon.update({"uid": None, "state": "revoked",
                                     "revoked_ts": _time.time(),
                                     "voided_reason": "account_deleted"})
                        purchases_store[key] = anon
            except Exception as exc:
                print(f"[delete] purchase anonymization failed: {exc!r}")
            # 5. The account itself, last, so a failure above still leaves a
            #    reachable account to retry from.
            for key in [f"uid:{t_uid}", t_uname,
                        f"email:{(t_urec or {}).get('email', '')}".lower() if (t_urec or {}).get("email") else None,
                        f"email:{t_uname}".lower() if t_uname else None]:
                if not key:
                    continue
                try:
                    users_store.pop(key)
                except Exception:
                    pass
            return removed_files

        def _signup_src(data):
            """Sanitized campaign source. The site captures ?src=/?utm_source=
            from a shared link (e.g. /?src=linkedin) and sends it with account
            creation; recorded on NEW records only and surfaced in /admin/users
            so each campaign's signups are attributable."""
            import re as _re
            s = str(data.get("src") or "").strip().lower()[:32]
            return s if _re.fullmatch(r"[a-z0-9_-]+", s) else None

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

        # ── Public lifetime video counter for the /welcome landing page ──
        # One quota entry is written per credit-charged processing run; multi-
        # credit runs add "#n"-suffixed extras, so counting only unsuffixed keys
        # counts VIDEOS, not credits. Admin/load-test runs never consume and
        # failed runs are refunded, so neither inflates the number. A monotonic
        # high-water mark in stats_store keeps the public count from shrinking
        # when an account deletion removes its quota entries.
        if path in ("/stats", "/stats/") and method == "GET":
            import time as _time
            _now = _time.time()
            if _now - _stats_cache["ts"] > 60:
                try:
                    count = sum(1 for k in quota_store.keys() if "#" not in k)
                    hwm = int(stats_store.get("total_videos") or 0)
                    if count > hwm:
                        stats_store["total_videos"] = count
                    _stats_cache["videos"] = max(count, hwm)
                except Exception:
                    pass   # keep serving the last good value on a store hiccup
                _stats_cache["ts"] = _now
            body = json.dumps({"videos": _stats_cache["videos"]}).encode()
            await send({"type": "http.response.start", "status": 200,
                        "headers": CORS + [(b"content-type", b"application/json"),
                                           (b"cache-control", b"public, max-age=60")]})
            await send({"type": "http.response.body", "body": body})
            return

        # ── Password registration is RETIRED (2026-07-13) ──
        # New accounts are created only via verified email codes (/auth/request-
        # code + /auth/verify-code) or Google, so every account is tied to an
        # address its owner can actually receive mail at — that verification, not
        # an invite code, is what limits free-credit farming (the invite gate was
        # removed 2026-08-01 to open signup for the Play review). Password
        # /auth/login stays as a dormant fallback for legacy accounts.
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
            # The Play-review address short-circuits everything below: no mail is
            # sent (its code is fixed), and BOTH lanes are answered "ok, existing
            # account" so the reviewer reaches the code screen no matter which
            # button they pressed and whether or not the account exists yet.
            if _is_review_email(_raw, os.environ.get("REVIEW_EMAIL", "")):
                body = json.dumps({"ok": True, "is_new": False}).encode()
                await send({"type": "http.response.start", "status": 200,
                            "headers": CORS + [(b"content-type", b"application/json")]})
                await send({"type": "http.response.body", "body": body})
                return
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
            # NEW accounts must accept the Terms + Privacy Policy (a legal gate,
            # so it stays); existing accounts skip it (they accepted at signup).
            # Signup is otherwise OPEN — no invite code (removed 2026-08-01). The
            # abuse ceiling is the emailed code itself plus the per-email and
            # per-IP send throttles below.
            if is_new and not data.get("terms_accepted"):
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
            # The Play-review address signs in with a FIXED code, so there is no
            # stored code to look up. The throttles above still bound guessing.
            # A miss falls through to the normal path, which has no live code for
            # this address and answers "expired" — so a wrong guess reveals
            # nothing about whether a review login exists.
            _review = _review_login_ok(_raw, _code, os.environ.get("REVIEW_EMAIL", ""),
                                       os.environ.get("REVIEW_CODE", ""))
            if _review:
                crec = {"terms_ts": _now}
            else:
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
                                      "signup_src": _signup_src(data),
                                      "video_limit": DEFAULT_VIDEO_LIMIT, "videos_used": 0}
                users_store[f"uid:{new_uid}"] = ident
                users_store[f"email:{ident}"] = ident
                if _raw != ident:
                    users_store[f"email:{_raw}"] = ident   # also index the raw form
                _uname = _raw
            # Keep REVIEW_VIDEO_LIMIT credits AVAILABLE to the reviewer on every
            # login, so a half-finished review can't strand them at zero. The
            # limit is raised past what they've already spent rather than
            # resetting videos_used, because the authoritative consumed count is
            # the unique-quota-key scan, not that display counter. Deliberately
            # NOT role="admin": the Admin tab lists every user's email.
            if _review:
                _rkey = _acct_key or ident   # legacy accounts key on the username
                rec = users_store.get(_rkey)
                if isinstance(rec, dict) and rec.get("uid"):
                    rec["video_limit"] = _count_quota_used(quota_store, rec["uid"]) + REVIEW_VIDEO_LIMIT
                    rec["review_account"] = True
                    users_store[_rkey] = rec
            token = _sign_token(new_uid, os.environ["AUTH_SECRET"])
            body = json.dumps({"token": token, "username": _uname}).encode()
            await send({"type": "http.response.start", "status": 200,
                        "headers": CORS + [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": body})
            return

        # ── Google Sign-In: verify the ID token → session token (public) ──
        # Same normalized-email keying as the code flow, so signing in with
        # Google and with a code for the same address land on ONE account (no
        # double credits). NEW accounts only have to accept the Terms; the
        # response carries `need_terms:true` so the app reveals that checkbox
        # and retries with the ID token it already holds.
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
            # record gets a clear "no account" answer instead of a silent signup.
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
                # NEW account via Google. Signup is OPEN — no invite code (removed
                # 2026-08-01); Google has already verified the address. The only
                # remaining gate is Terms acceptance, and `need_terms` tells the
                # app to reveal that checkbox and retry with the same ID token.
                # `need_invite` is sent alongside it purely so an older installed
                # shell (which only knows that flag) still reveals the block.
                if not data.get("terms_accepted"):
                    _nb = json.dumps({"error": "Please accept the Privacy Policy and Terms of Use.",
                                      "need_terms": True, "need_invite": True,
                                      "code": "terms_required"}).encode()
                    await send({"type": "http.response.start", "status": 400,
                                "headers": CORS + [(b"content-type", b"application/json")]})
                    await send({"type": "http.response.body", "body": _nb})
                    return
                new_uid = _secrets.token_hex(16)
                users_store[ident] = {"uid": new_uid, "created": _now, "email": _raw,
                                      "email_verified": True, "auth": "google", "google_sub": _sub,
                                      "terms_accepted_ts": _now,
                                      "signup_src": _signup_src(data),
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

        # ── Apple App Store Server Notifications V2 (public webhook) ──
        # Apple's counterpart to the Play voided-purchases sweep, except Apple
        # pushes instead of us polling. The POST body is NOT trusted: it is only
        # used to learn WHICH transaction to ask Apple about, and the revocation
        # decision comes from that authenticated App Store Server API read. A
        # forged notification therefore achieves nothing.
        if path in ("/billing/apple/notify", "/billing/apple/notify/") and method == "POST":
            import time as _time
            try:
                _body = json.loads((await _read_body(receive)).decode("utf-8"))
                _outer = _decode_jws_payload(str(_body.get("signedPayload") or ""))
            except Exception:
                # Malformed payloads are never retriable - swallow with a 200 so
                # Apple stops resending, and log for the record.
                print("[billing] apple notification: unparseable payload")
                await send({"type": "http.response.start", "status": 200,
                            "headers": CORS + [(b"content-type", b"application/json")]})
                await send({"type": "http.response.body", "body": b'{"ok":true}'})
                return
            _kind = str(_outer.get("notificationType") or "")
            _revoked = 0
            if _kind in ("REFUND", "REVOKE"):
                try:
                    _txn_info = _decode_jws_payload(
                        str((_outer.get("data") or {}).get("signedTransactionInfo") or ""))
                    _txn_id = str(_txn_info.get("transactionId") or "")
                    _authoritative = await asyncio.to_thread(
                        _fetch_apple_transaction, _txn_id)
                    if _authoritative.get("revocationDate"):
                        if _revoke_purchase_grant(
                                purchases_store, _apple_ledger_key(_txn_id), _time.time(),
                                str(_authoritative.get("revocationReason") or "refund")):
                            _revoked = 1
                except Exception as exc:
                    # A transient Apple/API failure must be retried, and Apple
                    # retries on a non-2xx - so surface it as a 500.
                    print(f"[billing] apple notification {_kind} failed: {exc!r}")
                    await send_error("Notification handling failed", 500)
                    return
            print(f"[billing] apple notification {_kind or 'unknown'}: revoked {_revoked}")
            await send({"type": "http.response.start", "status": 200,
                        "headers": CORS + [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": b'{"ok":true}'})
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

        def _spawn_pending_job(full_key):
            """Claim + spawn the deferred job (request path: the caller must
            be the registrant). Body lives at module level so the R2 sweep can
            spawn too - see _spawn_pending_job_impl."""
            return _spawn_pending_job_impl(full_key, expect_uid=uid,
                                           fallback_uprefix=uprefix)

        def _pending_complete(full_key, total_chunks):
            """True when every chunk of a deferred upload is on the volume."""
            from pathlib import Path as _Path
            try:
                if not total_chunks:
                    return False
                present = len(list(_Path(TMP_DIR).glob(f"{full_key}_chunk_*")))
                return present >= int(total_chunks)
            except Exception:
                return False

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
            purchased = _count_purchased_credits(purchases_store, uid)
            is_admin, used, limit = _quota_state(
                urec, _os.environ.get("ADMIN_USERS"), uname, purchased)
            if not is_admin:
                used = max(used, _count_quota_used(quota_store, uid))
            raw_base_limit = (urec or {}).get("video_limit", LEGACY_VIDEO_LIMIT)
            base_limit = -1 if raw_base_limit is None else int(raw_base_limit)
            body = json.dumps({"username": uname,
                               "role": "admin" if is_admin else "user",
                               "videos_used": used,
                               "video_limit": None if is_admin else limit,
                               "base_video_limit": None if is_admin else base_limit,
                               "purchased_credits": purchased,
                               "billing_account_id": _billing_account_id(uid),
                               # StoreKit needs a UUID, Play needs a free-form
                               # hash, so the two bindings are different values
                               # derived from the same uid.
                               "apple_account_token": _apple_account_token(uid),
                               "email": (urec or {}).get("email", ""),
                               "email_verified": bool((urec or {}).get("email_verified", False)),
                               "marketing_consent": bool((urec or {}).get("marketing_consent", False))}).encode()
            await send({"type": "http.response.start", "status": 200,
                        "headers": CORS + [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": body})
            return

        # ── Google Play consumable credit packs ──
        # The client supplies only the Play token + immutable product id. Play
        # is the authority for purchase state, product, quantity, and the
        # obfuscated account binding. The ledger key hashes the token and the
        # total quota is derived from unique grants, making retries idempotent.
        if path in ("/billing/play/verify", "/billing/play/verify/") and method == "POST":
            import os as _os
            import time as _time
            if not _check_rate_limit(_get_client_ip(scope)):
                await send_error("Rate limit exceeded", 429, code="rate_limited")
                return
            try:
                data = json.loads((await _read_body(receive)).decode("utf-8"))
            except Exception:
                await send_error("Invalid request body", 400, code="invalid_purchase")
                return
            product_id = str(data.get("product_id") or "").strip()
            purchase_token = str(data.get("purchase_token") or "").strip()
            if product_id not in PLAY_CREDIT_PRODUCTS or not (16 <= len(purchase_token) <= 4096):
                await send_error("Invalid Play purchase", 400, code="invalid_purchase")
                return

            ledger_key = _purchase_ledger_key(purchase_token)
            grant = purchases_store.get(ledger_key)
            if grant:
                if grant.get("uid") != uid or grant.get("product_id") != product_id:
                    await send_error("Purchase belongs to another account", 409,
                                     code="purchase_account_mismatch")
                    return
            else:
                try:
                    play_payload = await asyncio.to_thread(
                        _fetch_google_play_purchase, product_id, purchase_token)
                    verified = _parse_play_purchase(
                        play_payload, product_id, _billing_account_id(uid))
                except ValueError as exc:
                    code = str(exc)
                    status = 409 if code == "purchase_account_mismatch" else 400
                    await send_error("Play purchase is not ready", status, code=code)
                    return
                except Exception as exc:
                    print(f"[billing] verification unavailable: {exc!r}")
                    await send_error("Play purchase verification is temporarily unavailable",
                                     503, code="billing_unavailable")
                    return
                credits = PLAY_CREDIT_PRODUCTS[product_id] * verified["quantity"]
                grant = {
                    "uid": uid, "provider": "google_play",
                    "product_id": product_id, "credits": credits,
                    "quantity": verified["quantity"],
                    "order_id": verified["order_id"],
                    "region": verified["region"], "test": verified["test"],
                    "state": "granted", "ts": _time.time(),
                }
                # Grant first, consume second. If consumption has a transient
                # failure, the next restore safely retries without re-granting.
                purchases_store[ledger_key] = grant

            consume_pending = False
            try:
                await asyncio.to_thread(
                    _consume_google_play_purchase, product_id, purchase_token)
            except Exception as exc:
                consume_pending = True
                print(f"[billing] consume retry needed for {ledger_key[-12:]}: {exc!r}")

            uname, urec = _user_by_uid()
            purchased = _count_purchased_credits(purchases_store, uid)
            is_admin, used, limit = _quota_state(
                urec, _os.environ.get("ADMIN_USERS"), uname, purchased)
            if not is_admin:
                used = max(used, _count_quota_used(quota_store, uid))
            body = json.dumps({
                "ok": True, "credits_granted": grant["credits"],
                "purchased_credits": purchased, "videos_used": used,
                "video_limit": None if is_admin else limit,
                "consume_pending": consume_pending,
            }).encode()
            await send({"type": "http.response.start", "status": 200,
                        "headers": CORS + [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": body})
            return

        # ── Apple in-app purchase: consumable credit packs ──
        # Mirrors the Play route above. The client sends ONLY the StoreKit
        # transaction id; the server asks Apple what that transaction is, so a
        # forged or replayed id grants nothing. There is no consume step - a
        # StoreKit consumable is finished on the device once we have granted,
        # and an unfinished transaction is simply re-offered to the app on next
        # launch, which restores itself through this same idempotent route.
        if path in ("/billing/apple/verify", "/billing/apple/verify/") and method == "POST":
            import os as _os
            import time as _time
            if not _check_rate_limit(_get_client_ip(scope)):
                await send_error("Rate limit exceeded", 429, code="rate_limited")
                return
            try:
                data = json.loads((await _read_body(receive)).decode("utf-8"))
            except Exception:
                await send_error("Invalid request body", 400, code="invalid_purchase")
                return
            transaction_id = str(data.get("transaction_id") or "").strip()
            try:
                ledger_key = _apple_ledger_key(transaction_id)
            except ValueError:
                await send_error("Invalid App Store purchase", 400, code="invalid_purchase")
                return

            grant = purchases_store.get(ledger_key)
            if grant and grant.get("uid") != uid:
                await send_error("Purchase belongs to another account", 409,
                                 code="purchase_account_mismatch")
                return
            if not grant:
                try:
                    payload = await asyncio.to_thread(
                        _fetch_apple_transaction, transaction_id)
                    verified = _parse_apple_transaction(
                        payload, APPLE_CREDIT_PRODUCTS, _apple_account_token(uid))
                except ValueError as exc:
                    code = str(exc)
                    status = 409 if code == "purchase_account_mismatch" else 400
                    await send_error("App Store purchase is not valid", status, code=code)
                    return
                except Exception as exc:
                    print(f"[billing] apple verification unavailable: {exc!r}")
                    await send_error("App Store purchase verification is temporarily unavailable",
                                     503, code="billing_unavailable")
                    return
                credits = APPLE_CREDIT_PRODUCTS[verified["product_id"]] * verified["quantity"]
                grant = {
                    "uid": uid, "provider": "apple",
                    "product_id": verified["product_id"], "credits": credits,
                    "quantity": verified["quantity"],
                    "order_id": verified["order_id"],
                    "region": verified["region"], "test": verified["test"],
                    "state": "granted", "ts": _time.time(),
                }
                purchases_store[ledger_key] = grant

            uname, urec = _user_by_uid()
            purchased = _count_purchased_credits(purchases_store, uid)
            is_admin, used, limit = _quota_state(
                urec, _os.environ.get("ADMIN_USERS"), uname, purchased)
            if not is_admin:
                used = max(used, _count_quota_used(quota_store, uid))
            body = json.dumps({
                "ok": True, "credits_granted": grant["credits"],
                "purchased_credits": purchased, "videos_used": used,
                "video_limit": None if is_admin else limit,
                "consume_pending": False,
            }).encode()
            await send({"type": "http.response.start", "status": 200,
                        "headers": CORS + [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": body})
            return

        # ── Account deletion, initiated in-app ──
        # App Store Guideline 5.1.1(v): an app that lets you create an account
        # must let you delete it from INSIDE the app - a "email us to delete"
        # page (delete-account.html) is not sufficient on iOS. Google Play's
        # data-deletion policy asks for the same thing, so this replaces the
        # mailto flow on every platform.
        #
        # Everything keyed to the uid goes: rendered videos on the volume, job
        # history, cost rows, consumed-credit keys, spawn ownership, push
        # tokens, deferred uploads, error reports, and the account record with
        # both of its index entries. Purchase grants are ANONYMIZED rather than
        # dropped (uid removed, state revoked) - they are financial records tied
        # to a store order, and once revoked they hold no personal data and can
        # never grant credits again.
        if path in ("/account/delete", "/account/delete/") and method == "POST":
            if not _check_rate_limit(_get_client_ip(scope)):
                await send_error("Rate limit exceeded", 429, code="rate_limited")
                return
            try:
                _d = json.loads((await _read_body(receive)).decode("utf-8"))
            except Exception:
                _d = {}
            if _d.get("confirm") is not True:
                await send_error("Confirmation required", 400, code="confirm_required")
                return
            uname, urec = _user_by_uid()

            try:
                removed = await asyncio.to_thread(_purge_user_data, uid, uname, urec)
            except Exception as exc:
                print(f"[delete] account deletion failed for {uid[:8]}: {exc!r}")
                await send_error("Account deletion failed", 500, code="delete_failed")
                return
            print(f"[delete] account {uid[:8]} deleted ({removed} file(s))")
            body = json.dumps({"ok": True, "files_removed": removed}).encode()
            await send({"type": "http.response.start", "status": 200,
                        "headers": CORS + [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": body})
            return

        # ── Admin: delete a user account entirely ──
        # Same purge as self-service /account/delete (shared _purge_user_data):
        # videos, history, costs, quota keys, spawn ownership, push tokens,
        # pending uploads, error reports, the account record + index entries;
        # purchase grants anonymized. The email stays free to sign up again
        # fresh. Guards: never yourself (use account deletion), never another
        # admin (demote first) - both protect against a mis-tap wiping the
        # owner's own account from a list UI.
        if path in ("/admin/delete-user", "/admin/delete-user/") and method == "POST":
            import os as _os
            uname, urec = _user_by_uid()
            caller_admin, _, _ = _quota_state(urec, _os.environ.get("ADMIN_USERS"), uname)
            if not caller_admin:
                await send_error("Forbidden", 403)
                return
            try:
                data = json.loads((await _read_body(receive)).decode("utf-8"))
                target = (data.get("username") or "").strip().lower()
            except Exception:
                await send_error("Invalid request body", 400)
                return
            if data.get("confirm") is not True:
                await send_error("Confirmation required", 400, code="confirm_required")
                return
            trec = users_store.get(target)
            if not isinstance(trec, dict):
                await send_error("Unknown user", 404)
                return
            t_uid = trec.get("uid")
            if target == uname or t_uid == uid:
                await send_error("Use account deletion for your own account", 400, code="self_delete")
                return
            target_admin, _, _ = _quota_state(trec, _os.environ.get("ADMIN_USERS"), target)
            if target_admin:
                await send_error("Cannot delete an admin account", 400, code="admin_target")
                return
            try:
                removed = await asyncio.to_thread(_purge_user_data, t_uid, target, trec)
            except Exception as exc:
                print(f"[admin-delete] failed for {target}: {exc!r}")
                await send_error("Deletion failed", 500, code="delete_failed")
                return
            print(f"[admin-delete] {uname} deleted account {target} ({removed} file(s))")
            body = json.dumps({"ok": True, "username": target, "files_removed": removed}).encode()
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
            # No commit here — all chunks are committed once at spawn time.
            # Deferred job registered for this upload? Spawn processing the
            # moment the LAST chunk lands - the app may be closed by now.
            spawned_call = None
            try:
                _pend = pending_store.get(f"{uprefix}{key}")
                if isinstance(_pend, dict) and _pending_complete(f"{uprefix}{key}", _pend.get("total_chunks")):
                    spawned_call = await asyncio.to_thread(_spawn_pending_job, f"{uprefix}{key}")
            except Exception as e:
                print(f"[pending] chunk-complete check failed: {e!r}")
            body = json.dumps({"ok": True, **({"call_id": spawned_call} if spawned_call else {})}).encode()
            await send({"type": "http.response.start", "status": 200,
                        "headers": CORS + [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": body})
            return

        # Stream upload — receive into a unique staging file and publish chunk
        # 0000 atomically ONLY after a clean ASGI EOF and byte-count match. A
        # weak-network retry sends `http.disconnect`; treating that as EOF used
        # to spawn ffprobe on a partial MP4 while Android kept uploading.
        if path in ("/upload_stream", "/upload_stream/") and method in ("POST", "PUT"):
            qs  = parse_qs(scope.get("query_string", b"").decode())
            _uh = {bytes(k).lower(): bytes(v).decode() for k, v in scope.get("headers", [])}
            key = _uh.get(b"x-upload-key") or qs.get("key", [""])[0]
            if not key or not _SAFE_KEY_RE.match(key):
                await send_error("Invalid or missing key", 400)
                return
            from pathlib import Path as _Path
            import os as _os
            import uuid as _uuid
            chunk_path = _Path(TMP_DIR) / f"{uprefix}{key}_chunk_0000"
            staging_path = _Path(TMP_DIR) / (
                f".{uprefix}{key}.{_uuid.uuid4().hex}.uploading")
            expected_raw = _uh.get(b"x-upload-size") or _uh.get(b"content-length")
            try:
                expected_bytes = int(expected_raw) if expected_raw else None
                if expected_bytes is not None and expected_bytes < 0:
                    expected_bytes = None
            except (TypeError, ValueError):
                expected_bytes = None
            try:
                fh = await asyncio.to_thread(open, staging_path, "wb")
                try:
                    async def _write_upload(data):
                        await asyncio.to_thread(fh.write, data)
                    complete, received_bytes = await _receive_stream_upload(
                        receive, _write_upload)
                finally:
                    await asyncio.to_thread(fh.close)
            except BaseException:
                await asyncio.to_thread(staging_path.unlink, missing_ok=True)
                raise
            if not complete:
                await asyncio.to_thread(staging_path.unlink, missing_ok=True)
                print(f"[upload_stream] disconnected after {received_bytes} bytes "
                      f"for {uprefix}{key}; waiting for Android retry")
                return
            if expected_bytes is not None and received_bytes != expected_bytes:
                await asyncio.to_thread(staging_path.unlink, missing_ok=True)
                await send_error(
                    f"Incomplete upload: received {received_bytes} of "
                    f"{expected_bytes} bytes", 409, code="upload_incomplete")
                return
            await asyncio.to_thread(_os.replace, staging_path, chunk_path)
            # Deferred job registered? The whole file just landed (single
            # stream) - spawn processing NOW, app state irrelevant. This is
            # what lets a user close the app mid-upload and still get the
            # "ready to edit" push when processing completes.
            spawned_call = None
            try:
                if isinstance(pending_store.get(f"{uprefix}{key}"), dict):
                    spawned_call = await asyncio.to_thread(_spawn_pending_job, f"{uprefix}{key}")
            except Exception as e:
                print(f"[pending] stream-complete spawn failed: {e!r}")
            body = json.dumps({"ok": True, **({"call_id": spawned_call} if spawned_call else {})}).encode()
            await send({"type": "http.response.start", "status": 200,
                        "headers": CORS + [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": body})
            return

        # ── R2 direct upload ──
        # Web uploads to the Modal ingress are capped at ~3-4 Mbps: the browser
        # multiplexes every concurrent chunk POST onto ONE HTTP/2 connection
        # (per-origin coalescing) and the h2 upload flow-control window on a
        # ~120 ms path (ingress is us-east-1) is the ceiling - no client-side
        # knob changes that (measured 2026-08-06). The R2 S3 endpoint speaks
        # HTTP/1.1 only, so the browser opens REAL parallel TCP connections and
        # the same uplink measured 21 Mbps. Flow: /init presigns multipart part
        # URLs, the browser PUTs parts straight to R2, /complete assembles the
        # object and streams it onto the volume as chunk 0000 (the same shape
        # /upload_stream publishes, so processing/pending-spawn/retention are
        # untouched), then deletes the R2 object. The deferred registration's
        # total_chunks stays the CHUNKED count - one landed file never trips
        # _pending_complete early, and /complete spawns explicitly instead.
        # Requires the hebpipe-backup secret (same bucket as the metadata
        # backup, uploads/ prefix); absent creds → 503 and the frontend falls
        # back to the chunked path.
        R2_PART_SIZE  = 8 * 1024 * 1024
        R2_MAX_SIZE   = 1024 * 1024 * 1024 + R2_PART_SIZE   # client cap + slack
        R2_URL_TTL    = 4 * 3600

        def _r2_client():
            if not os.environ.get("S3_ENDPOINT_URL") or not os.environ.get("S3_BUCKET"):
                return None, None
            import boto3
            client = boto3.client(
                "s3",
                endpoint_url=os.environ["S3_ENDPOINT_URL"],
                aws_access_key_id=os.environ["S3_ACCESS_KEY_ID"],
                aws_secret_access_key=os.environ["S3_SECRET_ACCESS_KEY"],
                region_name=os.environ.get("S3_REGION", "auto"),
            )
            return client, os.environ["S3_BUCKET"]

        if path in ("/upload_r2/init", "/upload_r2/init/") and method == "POST":
            s3, bucket = await asyncio.to_thread(_r2_client)
            if s3 is None:
                await send_error("R2 not configured", 503, code="r2_unavailable")
                return
            try:
                data = json.loads((await _read_body(receive)).decode("utf-8"))
                key  = data.get("key") or ""
                size = int(data.get("size") or 0)
            except Exception:
                await send_error("Invalid body", 400)
                return
            if not _SAFE_KEY_RE.match(key):
                await send_error("Invalid or missing key", 400)
                return
            if not (0 < size <= R2_MAX_SIZE):
                await send_error("Invalid size", 400)
                return
            obj_key = f"uploads/{uprefix}{key}.src"
            # single=true: ONE presigned PUT for the whole file - the native
            # Android foreground-service uploader sends one binary PUT (it
            # can't slice parts). An S3 PUT is atomic, so the object existing
            # == the whole file arrived (what the sweep relies on).
            if data.get("single"):
                try:
                    url = await asyncio.to_thread(
                        lambda: s3.generate_presigned_url(
                            "put_object", Params={"Bucket": bucket, "Key": obj_key},
                            ExpiresIn=R2_URL_TTL))
                except Exception as e:
                    print(f"[r2] single init failed: {e!r}")
                    await send_error("R2 init failed", 503, code="r2_unavailable")
                    return
                body = json.dumps({"url": url}).encode()
                await send({"type": "http.response.start", "status": 200,
                            "headers": CORS + [(b"content-type", b"application/json")]})
                await send({"type": "http.response.body", "body": body})
                return
            n_parts = max(1, -(-size // R2_PART_SIZE))
            try:
                def _init():
                    mp = s3.create_multipart_upload(Bucket=bucket, Key=obj_key)
                    urls = [s3.generate_presigned_url(
                                "upload_part",
                                Params={"Bucket": bucket, "Key": obj_key,
                                        "UploadId": mp["UploadId"], "PartNumber": i + 1},
                                ExpiresIn=R2_URL_TTL)
                            for i in range(n_parts)]
                    return mp["UploadId"], urls
                upload_id, urls = await asyncio.to_thread(_init)
                extra = {}
                # parallel=true: the native ParallelUploader service finishes
                # the multipart DIRECTLY against R2 (presigned complete/abort,
                # no auth token inside the service) - the assembled object then
                # exists even if the app dies before /complete, and the 5-min
                # sweep lands + spawns it. The XML body of the complete POST is
                # not signed (presigned = unsigned payload), so this works from
                # any HTTP client.
                if data.get("parallel"):
                    def _ctl_urls():
                        return (
                            s3.generate_presigned_url(
                                "complete_multipart_upload",
                                Params={"Bucket": bucket, "Key": obj_key,
                                        "UploadId": upload_id},
                                ExpiresIn=R2_URL_TTL, HttpMethod="POST"),
                            s3.generate_presigned_url(
                                "abort_multipart_upload",
                                Params={"Bucket": bucket, "Key": obj_key,
                                        "UploadId": upload_id},
                                ExpiresIn=R2_URL_TTL, HttpMethod="DELETE"))
                    complete_url, abort_url = await asyncio.to_thread(_ctl_urls)
                    extra = {"complete_url": complete_url, "abort_url": abort_url}
            except Exception as e:
                print(f"[r2] init failed: {e!r}")
                await send_error("R2 init failed", 503, code="r2_unavailable")
                return
            body = json.dumps({"upload_id": upload_id, "part_size": R2_PART_SIZE,
                               "urls": urls, **extra}).encode()
            await send({"type": "http.response.start", "status": 200,
                        "headers": CORS + [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": body})
            return

        if path in ("/upload_r2/complete", "/upload_r2/complete/") and method == "POST":
            s3, bucket = await asyncio.to_thread(_r2_client)
            if s3 is None:
                await send_error("R2 not configured", 503, code="r2_unavailable")
                return
            try:
                data      = json.loads((await _read_body(receive)).decode("utf-8"))
                key       = data.get("key") or ""
                upload_id = data.get("upload_id") or ""
                etags     = data.get("etags") or []
                single    = bool(data.get("single"))
            except Exception:
                await send_error("Invalid body", 400)
                return
            if not _SAFE_KEY_RE.match(key) or not isinstance(etags, list) \
               or (not single and not upload_id):
                await send_error("Invalid or missing key", 400)
                return
            from pathlib import Path as _Path
            obj_key    = f"uploads/{uprefix}{key}.src"
            chunk_path = _Path(TMP_DIR) / f"{uprefix}{key}_chunk_0000"
            # Idempotent retry: if a previous /complete already landed the file
            # on the volume, skip straight to the spawn check - the client can
            # safely re-POST after a network blip.
            already = await asyncio.to_thread(chunk_path.exists)
            if not already:
                try:
                    def _assemble_and_fetch():
                        if single:
                            # A single PUT is atomic: existing == fully uploaded.
                            s3.head_object(Bucket=bucket, Key=obj_key)
                        else:
                            try:
                                s3.complete_multipart_upload(
                                    Bucket=bucket, Key=obj_key, UploadId=upload_id,
                                    MultipartUpload={"Parts": [
                                        {"ETag": e, "PartNumber": i + 1}
                                        for i, e in enumerate(etags)]})
                            except Exception:
                                # NoSuchUpload after a completed earlier attempt
                                # is fine IF the object exists; else it's real.
                                s3.head_object(Bucket=bucket, Key=obj_key)
                        _r2_land_on_volume(s3, bucket, obj_key, f"{uprefix}{key}")
                    await asyncio.to_thread(_assemble_and_fetch)
                except Exception as e:
                    print(f"[r2] complete failed for {uprefix}{key}: {e!r}")
                    await send_error("R2 complete failed", 502, code="r2_complete_failed")
                    return
            # Whole file just landed - spawn a deferred job NOW, exactly like
            # /upload_stream (registration total_chunks is the chunked count,
            # so the chunk-counting paths never spawn this one).
            spawned_call = None
            try:
                if isinstance(pending_store.get(f"{uprefix}{key}"), dict):
                    spawned_call = await asyncio.to_thread(_spawn_pending_job, f"{uprefix}{key}")
            except Exception as e:
                print(f"[pending] r2-complete spawn failed: {e!r}")
            body = json.dumps({"ok": True, **({"call_id": spawned_call} if spawned_call else {})}).encode()
            await send({"type": "http.response.start", "status": 200,
                        "headers": CORS + [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": body})
            return

        if path in ("/upload_r2/abort", "/upload_r2/abort/") and method == "POST":
            s3, bucket = await asyncio.to_thread(_r2_client)
            if s3 is not None:
                try:
                    data      = json.loads((await _read_body(receive)).decode("utf-8"))
                    key       = data.get("key") or ""
                    upload_id = data.get("upload_id") or ""
                except Exception:
                    key, upload_id = "", ""
                if _SAFE_KEY_RE.match(key):
                    obj_key = f"uploads/{uprefix}{key}.src"
                    def _abort():
                        if upload_id:
                            try:
                                s3.abort_multipart_upload(Bucket=bucket, Key=obj_key,
                                                          UploadId=upload_id)
                            except Exception:
                                pass
                        try:
                            s3.delete_object(Bucket=bucket, Key=obj_key)
                        except Exception:
                            pass
                    await asyncio.to_thread(_abort)
            await send({"type": "http.response.start", "status": 200,
                        "headers": CORS + [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": b'{"ok": true}'})
            return

        # Uplink probe target for the R2 path: presigned PUTs (and matching
        # DELETEs so the client can clean up) for tiny scratch objects. The
        # estimate must measure the path the real upload takes.
        if path in ("/upload_r2/probe", "/upload_r2/probe/") and method == "GET":
            s3, bucket = await asyncio.to_thread(_r2_client)
            if s3 is None:
                await send_error("R2 not configured", 503, code="r2_unavailable")
                return
            def _probe_urls():
                puts, deletes = [], []
                for _ in range(4):
                    import uuid as _uuid2
                    k = f"uploads/_probe/{uprefix}{_uuid2.uuid4().hex}"
                    puts.append(s3.generate_presigned_url(
                        "put_object", Params={"Bucket": bucket, "Key": k}, ExpiresIn=600))
                    deletes.append(s3.generate_presigned_url(
                        "delete_object", Params={"Bucket": bucket, "Key": k}, ExpiresIn=600))
                return puts, deletes
            puts, deletes = await asyncio.to_thread(_probe_urls)
            body = json.dumps({"puts": puts, "deletes": deletes}).encode()
            await send({"type": "http.response.start", "status": 200,
                        "headers": CORS + [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": body})
            return

        # Deferred-job status: the client (fresh page or resumed app) asks what
        # became of its pre-registered processing job. done → {call_id};
        # registered but upload incomplete → {pending: true}; unknown → 404.
        if path in ("/process_pending", "/process_pending/") and method == "GET":
            qs  = parse_qs(scope.get("query_string", b"").decode())
            key = qs.get("key", [""])[0]
            if not key or not _SAFE_KEY_RE.match(key):
                await send_error("Invalid or missing key", 400)
                return
            full_key = uprefix + key
            done = pending_store.get("done:" + full_key)
            if isinstance(done, dict) and done.get("uid") == uid:
                body = json.dumps({"call_id": done["call_id"]}).encode()
            elif isinstance(pending_store.get(full_key), dict):
                # Belt-and-braces: if every chunk is already here (the upload
                # finished but its completion-side spawn failed transiently),
                # spawn now instead of reporting pending forever.
                _pend = pending_store.get(full_key)
                spawned = None
                if _pend and _pend.get("uid") == uid and _pending_complete(full_key, _pend.get("total_chunks")):
                    spawned = await asyncio.to_thread(_spawn_pending_job, full_key)
                body = json.dumps({"call_id": spawned} if spawned else {"pending": True}).encode()
            else:
                await send_error("no pending job", 404)
                return
            await send({"type": "http.response.start", "status": 200,
                        "headers": CORS + [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": body})
            return

        # Cancel a pre-registered native stream upload before processing starts.
        # The client stops Android's foreground service first, then calls this
        # endpoint so no deferred job or partial staging file survives Start Over.
        if path in ("/cancel_upload", "/cancel_upload/") and method in ("POST", "DELETE"):
            qs = parse_qs(scope.get("query_string", b"").decode())
            key = qs.get("key", [""])[0]
            if not key or not _SAFE_KEY_RE.match(key):
                await send_error("Invalid or missing key", 400)
                return
            full_key = uprefix + key
            try:
                pending_store.pop(full_key)
            except Exception:
                pass
            try:
                pending_store.pop("done:" + full_key)
            except Exception:
                pass
            try:
                progress_store.pop(full_key)
            except Exception:
                pass
            from pathlib import Path as _Path
            cleanup_paths = [
                _Path(TMP_DIR) / f"{full_key}_chunk_0000",
                *_Path(TMP_DIR).glob(f".{full_key}.*.uploading"),
            ]
            for cleanup_path in cleanup_paths:
                try:
                    await asyncio.to_thread(cleanup_path.unlink, missing_ok=True)
                except Exception:
                    pass
            body = json.dumps({"status": "cancelled"}).encode()
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
            # iOS registers a raw APNs device token, Android an FCM one, and
            # they are not interchangeable. Namespace here rather than trusting
            # the client to send a prefixed token, and strip any prefix the
            # client did send so it cannot forge the other transport's
            # namespace. Absent/unknown platform = FCM, matching every token
            # written before iOS existed.
            if str(data.get("platform") or "").lower() == "ios":
                token = APNS_TOKEN_PREFIX + token.removeprefix(APNS_TOKEN_PREFIX)
            else:
                token = token.removeprefix(APNS_TOKEN_PREFIX)
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

        # ── Processing nudge (2026-08-29) ──
        # The "upload finished - processing on the server" push fires at
        # process_video start, while the user is still watching the checklist,
        # and a FOREGROUNDED Android app never displays an FCM notification.
        # So a user who minimizes seconds later has NOTHING in the shade for
        # the whole processing phase (the upload notification is long gone -
        # a typical clip uploads in a few seconds). The client calls this when
        # it goes hidden mid-processing; we re-send the same message (same
        # tag, so the completion push still replaces it) only while the job is
        # genuinely still running, at most once a minute per job.
        if path in ("/push/processing", "/push/processing/") and method == "POST":
            if not uid:
                await send_error("unauthorized", 401)
                return
            qs  = parse_qs(scope.get("query_string", b"").decode())
            key = qs.get("key", [""])[0]
            if not key or not _SAFE_KEY_RE.match(key):
                await send_error("Invalid or missing key", 400)
                return
            full_key = uprefix + key
            sent = False
            try:
                done = pending_store.get("done:" + full_key)
                call_id = done.get("call_id") if isinstance(done, dict) and done.get("uid") == uid else None
                last = pending_store.get("nudge:" + full_key)
                fresh = isinstance(last, (int, float)) and (_time.time() - last) < 60
                if call_id and not fresh:
                    _, running = _poll_fn_call(modal.FunctionCall.from_id(call_id))
                    if running:
                        pending_store["nudge:" + full_key] = _time.time()
                        await asyncio.to_thread(
                            _send_push, uid, "ההעלאה הסתיימה",
                            "הסרטון בעיבוד בשרת - אפשר לסגור את האפליקציה, נעדכן כשיהיה מוכן.",
                            "processing", "hebpipe-job")
                        sent = True
            except Exception as e:
                print(f"[push] processing nudge skipped: {e!r}")
            body = json.dumps({"sent": sent}).encode()
            await send({"type": "http.response.start", "status": 200,
                        "headers": CORS + [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": body})
            return

        # ── Client error report → stored + IMMEDIATE admin push ──
        # The site posts here from every user-facing error surface, so the
        # owner learns about field failures the moment they happen (FCM push
        # to admin devices via the app) instead of waiting for a bug report.
        if path in ("/error-report", "/error-report/") and method == "POST":
            if not uid:
                await send_error("unauthorized", 401)
                return
            if not _check_rate_limit(_get_client_ip(scope)):
                await send_error("Rate limit exceeded", 429, code="rate_limited")
                return
            import time as _time
            try:
                data = json.loads((await _read_body(receive)).decode("utf-8"))
            except Exception:
                data = {}
            stage = str(data.get("stage") or "?")[:40]
            msg   = str(data.get("message") or "")[:400]
            ver   = str(data.get("version") or "?")[:20]
            _uname = users_store.get(f"uid:{uid}")
            uname  = _uname if isinstance(_uname, str) else uid[:8]
            # Client-side context: which job, which options, what the user did
            # just before it broke. Bounded so a hostile or buggy client cannot
            # bloat the store, and readable ONLY through /admin/errors.
            ctx = _sanitize_error_context(data.get("context"))
            rec = {"ts": _time.time(), "user": uname, "uid": uid[:8], "stage": stage,
                   "message": msg, "version": ver,
                   "ua": str(data.get("ua") or "")[:160],
                   "context": ctx}
            # "Is this the one-off I already know about, or is it happening to
            # everyone right now?" is the first question a 3am alert has to
            # answer, so count recent look-alikes before pushing.
            seen = 1
            try:
                errors_store[f"e:{int(_time.time() * 1000)}:{uid[:8]}"] = rec
                _keys = sorted(k for k in errors_store.keys() if k.startswith("e:"))
                for _k in _keys[:-300]:          # bound the store
                    errors_store.pop(_k)
                seen = _count_similar_errors(errors_store, stage, msg, _time.time())
            except Exception as _ee:
                print(f"[errors] store failed: {_ee!r}")
            _alert_admins(_error_alert_title(stage, seen),
                          _error_alert_body(uname, stage, ver, msg, ctx, seen))
            body = json.dumps({"ok": True}).encode()
            await send({"type": "http.response.start", "status": 200,
                        "headers": CORS + [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": body})
            return

        # ── Admin: recent error reports (newest first) ──
        if path in ("/admin/errors", "/admin/errors/") and method == "GET":
            import os as _os
            uname, urec = _user_by_uid()
            caller_admin, _, _ = _quota_state(urec, _os.environ.get("ADMIN_USERS"), uname)
            if not caller_admin:
                await send_error("Forbidden", 403)
                return
            out = []
            try:
                for _k in errors_store.keys():
                    if not _k.startswith("e:"):
                        continue
                    _r = errors_store.get(_k)
                    if isinstance(_r, dict):
                        out.append(_r)
            except Exception:
                pass
            out.sort(key=lambda r: r.get("ts") or 0, reverse=True)
            body = json.dumps({"errors": out[:100]}).encode()
            await send({"type": "http.response.start", "status": 200,
                        "headers": CORS + [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": body})
            return

        # Measured compute cost per video — the evidence behind credit pricing.
        # `days` defaults to a week so a single quiet day can't read as a trend.
        if path in ("/admin/costs", "/admin/costs/") and method == "GET":
            import os as _os, time as _ct
            uname, urec = _user_by_uid()
            caller_admin, _, _ = _quota_state(urec, _os.environ.get("ADMIN_USERS"), uname)
            if not caller_admin:
                await send_error("Forbidden", 403)
                return
            _cqs = parse_qs(scope.get("query_string", b"").decode())
            try:
                _days = max(1, min(90, int(_cqs.get("days", ["7"])[0])))
            except Exception:
                _days = 7
            summary = _cost_summary(costs_store, _ct.time() - _days * 86400)
            body = json.dumps({"days": _days, **summary}).encode()
            await send({"type": "http.response.start", "status": 200,
                        "headers": CORS + [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": body})
            return

        # ── Per-user settings profiles ──
        # ONE bounded array on the user record (client owns the payload shape,
        # like the hook.lines contract): [{name, data}] plus the auto-applied
        # default's name. Account-level so profiles sync across web + Android,
        # and they ride the daily metadata backup for free.
        if path in ("/profiles", "/profiles/") and method in ("GET", "POST"):
            if not uid:
                await send_error("unauthorized", 401)
                return
            uname, urec = _user_by_uid()
            if not isinstance(urec, dict):
                await send_error("unauthorized", 401)
                return
            if method == "POST":
                try:
                    data = json.loads((await _read_body(receive)).decode("utf-8"))
                except Exception:
                    data = None
                profs = (data or {}).get("profiles")
                default = (data or {}).get("default")
                ok = isinstance(profs, list) and len(profs) <= 6
                if ok:
                    for p in profs:
                        if not (isinstance(p, dict) and isinstance(p.get("name"), str)
                                and 1 <= len(p["name"]) <= 40
                                and isinstance(p.get("data"), dict)):
                            ok = False
                            break
                if not ok or len(json.dumps(profs, ensure_ascii=False)) > 16384:
                    await send_error("bad_request", 400)
                    return
                urec["profiles"] = profs
                urec["default_profile"] = default if (
                    isinstance(default, str)
                    and any(p["name"] == default for p in profs)) else None
                users_store[uname] = urec
            body = json.dumps({"profiles": urec.get("profiles") or [],
                               "default": urec.get("default_profile")}).encode()
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
            # Audit what the CLIENT sent (raw), next to what we parsed - the
            # worker logs its own [params] line, so one run splits sender vs
            # worker for "toggle ignored" field reports.
            print(f"[process] key={upload_key} defer={qs.get('defer', ['-'])[0]} "
                  f"raw_cut={qs.get('cut_silences', ['<absent>'])[0]} parsed_cut={cut_silences} "
                  f"burn={burn_captions} is_audio={is_audio}")

            # What this run costs. The client reports the source duration it
            # already measured; process_video re-checks against its own probe,
            # so understating it buys nothing.
            try:
                src_duration = float(qs.get("duration", ["0"])[0])
            except (ValueError, TypeError):
                src_duration = 0.0
            if enhance_video == "esrgan" and not _upscale_allowed(src_duration):
                await send_error("upscale_too_long", 400)
                return
            credit_cost = _credit_cost(src_duration, enhance_video)

            import os as _os, time as _time
            uname, urec = _user_by_uid()
            # A session token outlives the account it was minted for (it is a
            # stateless 30-day HMAC), so a user who deleted their account could
            # otherwise keep spawning jobs on the free-tier fallback limit and
            # write new data under a uid that is supposed to be gone.
            if not urec:
                await send_error("Authentication required", 401, code="account_deleted")
                return
            purchased = _count_purchased_credits(purchases_store, uid)
            is_admin, used, limit = _quota_state(
                urec, _os.environ.get("ADMIN_USERS"), uname, purchased)
            if not is_admin:
                # Authoritative count from unique per-credit keys (race-proof);
                # the record's videos_used is a floor so existing testers'
                # usage isn't reset when this migrates in.
                used = max(used, _count_quota_used(quota_store, uid))
                # Every credit this run will consume must be available up front:
                # charging 2 to a user holding 1 would leave a negative balance.
                if not _quota_allows(is_admin, used + credit_cost - 1, limit):
                    await send_error("limit_reached", 402)
                    return
            # DEFERRED registration: called BEFORE the upload with the same
            # params. The upload endpoints spawn the job server-side the moment
            # the last byte lands, so processing starts even if the app (whose
            # JS used to do this spawn) is closed by then. Quota is CHECKED here
            # (fail fast, before any bytes fly) but CONSUMED at the real spawn.
            if qs.get("defer", ["false"])[0].lower() == "true":
                if not upload_key or not _SAFE_KEY_RE.match(upload_key):
                    await send_error("Invalid key", 400)
                    return
                try:
                    total_chunks = max(1, min(10000, int(qs.get("total_chunks", ["1"])[0])))
                except (ValueError, TypeError):
                    total_chunks = 1
                # A re-run of the SAME file reuses its signature-derived upload
                # key. Kill any done-marker from the previous run BEFORE the new
                # registration exists - /process_pending checks "done:" first,
                # so a stale one short-circuits every resume/reconnect poll to
                # the OLD call_id and serves the old result rendered with the
                # OLD toggles (e.g. silences cut although the toggle is now
                # off), while the new job spawns and burns a credit unseen.
                try:
                    pending_store.pop("done:" + uprefix + upload_key)
                except Exception:
                    pass
                pending_store[uprefix + upload_key] = {
                    "params": {
                        "filename": filename, "cut_silences": cut_silences,
                        "burn_captions": burn_captions, "min_silence": min_silence,
                        "padding": padding, "enhance_audio": enhance_audio,
                        "transcribe_for_broll": transcribe_for_broll,
                        "enhance_video": enhance_video, "is_audio": is_audio,
                    },
                    "uid": uid, "uname": uname, "is_admin": is_admin,
                    "uprefix": uprefix, "total_chunks": total_chunks,
                    # Quota is CHECKED here and CONSUMED at the real spawn, so
                    # the price computed from these params has to travel with
                    # them - the upload endpoint has no query string to re-read.
                    "credit_cost": credit_cost,
                    "ts": _time.time(),
                }
                body = json.dumps({"pending": True}).encode()
                await send({"type": "http.response.start", "status": 202,
                            "headers": CORS + [(b"content-type", b"application/json")]})
                await send({"type": "http.response.body", "body": body})
                return
            try:
                if upload_key:
                    if not _SAFE_KEY_RE.match(upload_key):
                        await send_error("Invalid key", 400)
                        return
                    # An unconsumed deferred registration for this key would
                    # double-spawn when a late chunk arrives - claim it away.
                    # Same for a previous run's done-marker (see the defer
                    # branch above): this spawn supersedes the old story.
                    try:
                        pending_store.pop(uprefix + upload_key)
                    except Exception:
                        pass
                    try:
                        pending_store.pop("done:" + uprefix + upload_key)
                    except Exception:
                        pass
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
                        credits_charged=(0 if is_admin else credit_cost),
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
                        credits_charged=(0 if is_admin else credit_cost),
                    )
                _record_call(call)
                if not is_admin and uname:
                    # One unique key per consumed credit; atomic, so concurrent
                    # spawns can't clobber each other's increment. A long video
                    # or a 4K upscale costs more than one (see _credit_cost).
                    _charge_credits(quota_store, uid, call.object_id, credit_cost)
                    urec["videos_used"] = used + credit_cost
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
                _raw_base_limit = _r.get("video_limit", LEGACY_VIDEO_LIMIT)
                _base_limit = -1 if _raw_base_limit is None else int(_raw_base_limit)
                _purchased = _count_purchased_credits(
                    purchases_store, str(_r.get("uid") or ""))
                _adm, _used, _limit = _quota_state(
                    _r, _os.environ.get("ADMIN_USERS"), _u, _purchased)
                out.append({"username": _u, "role": "admin" if _adm else "user",
                            "videos_used": _used,
                            "video_limit": None if _adm else _limit,
                            "base_video_limit": None if _adm else _base_limit,
                            "purchased_credits": _purchased,
                            "src": _r.get("signup_src"),
                            "created": _r.get("created")})
            # Newest signups first (user directive 2026-08-16) - the list is
            # read to see who just joined; legacy records without `created`
            # sink to the bottom.
            out.sort(key=lambda r: -(r.get("created") or 0))
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
                # A run can cost more than one credit (long source, 4K upscale),
                # so refund every key it wrote, not just the first.
                _back = _refund_credits(quota_store, uid, call_id)
                if _back:
                    try:
                        _uname_r, _urec_r = _user_by_uid()
                        if _urec_r:
                            _urec_r["videos_used"] = max(
                                0, int(_urec_r.get("videos_used", _back)) - _back)
                            users_store[_uname_r] = _urec_r
                    except Exception:
                        pass
                # Immediate owner alert: a terminal JOB failure is the worst
                # user experience - push it even if the user never reports.
                # This one is SERVER-side, so unlike a client report it can
                # carry the exception type and the tail of the traceback. It is
                # stored in the same admin-only errors feed, which is the only
                # place any of it is ever readable.
                try:
                    # Imported locally on purpose: every route body shares one
                    # function scope, so `_time` is a local bound only by the
                    # branch that imports it. Relying on another route having
                    # run first would make this alert vanish with an
                    # UnboundLocalError that the except below would swallow.
                    import time as _time
                    import traceback as _tb
                    _an = users_store.get(f"uid:{uid}")
                    _who = _an if isinstance(_an, str) else uid[:8]
                    _ctx = {"platform": "server", "call_id": str(call_id)[:60],
                            "exception": type(e).__name__,
                            "traceback": "".join(
                                _tb.format_exception(type(e), e, e.__traceback__))[-1200:]}
                    _erec = {"ts": _time.time(), "user": _who, "uid": uid[:8],
                             "stage": "process", "message": str(e)[:400],
                             "version": "server", "ua": "",
                             "context": _sanitize_error_context(_ctx)}
                    try:
                        errors_store[f"e:{int(_time.time() * 1000)}:{uid[:8]}"] = _erec
                    except Exception:
                        pass
                    _seen = _count_similar_errors(errors_store, "process", str(e), _time.time())
                    _alert_admins(
                        _error_alert_title("process", _seen),
                        _error_alert_body(_who, "process", "server", str(e),
                                          _erec["context"], _seen))
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
                    caption_style_json = ""
                else:
                    captions_json = json.dumps(parsed.get("captions", []))
                    broll_json    = json.dumps(parsed.get("broll", []))
                    hook_json     = json.dumps(parsed.get("hook", {})) if parsed.get("hook") else ""
                    caption_style_json = json.dumps(parsed.get("caption_style", {})) if parsed.get("caption_style") else ""
            except Exception:
                captions_json = raw
                broll_json    = "[]"
                hook_json     = ""
                caption_style_json = ""

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
                call = burn_captions_fn.spawn(video_key, captions_json, font, margin_v_pct, broll_json, font_size, hook_json, caption_style_json=caption_style_json, source_name=filename)
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
                                 "duration": meta.get("duration", 0),
                                 # Burns recorded since the re-edit feature carry
                                 # their editor state; older jobs never will.
                                 "editable": bool(meta.get("edit"))})
                jobs.sort(key=lambda j: j["ts"], reverse=True)
                body = json.dumps({"jobs": jobs}).encode()
                await send({"type": "http.response.start", "status": 200,
                            "headers": CORS + [(b"content-type", b"application/json")]})
                await send({"type": "http.response.body", "body": body})
            except Exception as e:
                await send_error(str(e))
            return

        # ── Job history: reopen a burned export in the editor ──
        # Returns the captions/hook/B-roll/styling that produced it plus the
        # cut key to burn onto again. The source is pinned by prune_volume for
        # the job's lifetime, but verify it anyway - a job recorded before that
        # pin existed, or any manual cleanup, would leave the record orphaned.
        if method == "GET" and path.startswith("/jobs/") and path.rstrip("/").endswith("/edit"):
            from pathlib import Path as _Path
            key = path.rstrip("/")[len("/jobs/"):-len("/edit")].rstrip("/")
            if not key or not _SAFE_DOWNLOAD_KEY_RE.match(key):
                await send_error("Invalid key", 400)
                return
            if not _owned_key(key, uid):
                await send_error("Forbidden", 403)
                return
            meta = jobs_store.get(key) or {}
            edit = meta.get("edit") or {}
            if not edit:
                await send_error("This export has no saved edit state", 404,
                                 code="not_editable")
                return
            src_key = edit.get("src_key") or ""
            if not src_key or not _owned_key(src_key, uid):
                await send_error("Source unavailable", 410, code="source_gone")
                return
            try:
                tmp_vol.reload()
            except Exception:
                pass
            if not (_Path(TMP_DIR) / src_key).exists():
                await send_error("Source video no longer available", 410,
                                 code="source_gone")
                return
            body = json.dumps({
                "key":           key,
                "name":          meta.get("name", "video"),
                "src_key":       src_key,
                "captions":      edit.get("captions") or [],
                "hook":          edit.get("hook") or {},
                "broll":         edit.get("broll") or [],
                "font":          edit.get("font") or "Heebo",
                "font_size":     edit.get("font_size") or 48,
                "margin_v_pct":  edit.get("margin_v_pct", 0.08),
                "caption_style": edit.get("caption_style") or {},
            }).encode()
            await send({"type": "http.response.start", "status": 200,
                        "headers": CORS + [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": body})
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
                caption_style = data.get("caption_style") or {}
                caps_in      = data.get("captions") or []
                # Punch-in zoom factor at the paused instant. The client sends
                # the numeric factor (window membership is its call - the
                # WYSIWYG contract); the still is scaled + center-cropped
                # BEFORE the ASS burn, exactly like the real render chain.
                try:
                    zoom = float(data.get("zoom") or 0)
                except (TypeError, ValueError):
                    zoom = 0.0
                zoom = zoom if 1.001 < zoom <= 2.0 else 0.0

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
                        # `hl` (karaoke highlight token range) must survive the
                        # retime - absolute word timings can't, so the client
                        # pre-computes the range for the paused instant.
                        caps = ([{"start": 0.0, "end": 9.0, "text": active[0].get("text", ""),
                                  **({"hl": active[0]["hl"]}
                                     if isinstance(active[0].get("hl"), list) else {}),
                                  **({"kw": active[0]["kw"]}
                                     if isinstance(active[0].get("kw"), list) else {})}]
                                if active else [])
                        hk = {}
                        if hook.get("text"):
                            hs = float(hook.get("start_seconds", 1.0))
                            hd = float(hook.get("duration_seconds", 4.0))
                            if hs <= t <= hs + hd:
                                hk = dict(hook); hk["start_seconds"] = 0.0; hk["duration_seconds"] = 9.0
                        ass = build_caption_ass(W, Hh, font, font_size, margin_h, margin_v, caps, hk, caption_style=caption_style)
                        ap.write_text(ass, encoding="utf-8")
                        # Zoom before ass= mirrors the burn's filter order
                        # (zoom → B-roll → subtitles): captions never zoom.
                        # Same face framing as the burn: Haar-detect on THIS
                        # still (the burn detects per window - both are "the
                        # speaker's face"), upper-third default when none.
                        zoom_vf = ""
                        if zoom:
                            fx, fy = _face_center_from_image(fr) or (0.5, 0.30)
                            zoom_vf = (f"scale=trunc(iw*{zoom}/2)*2:trunc(ih*{zoom}/2)*2,"
                                       f"crop={W}:{Hh}:(iw-ow)*{fx:.4f}:(ih-oh)*{fy:.4f},")
                        _sp.run(["ffmpeg", "-y", "-i", str(fr), "-vf", f"{zoom_vf}ass={ap}",
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
                    # Small range replies (preview seeking) use a small chunk so
                    # playback starts fast; full downloads (200) AND the share
                    # warm-up's big parallel ranges (multi-MB 206s) use a bigger
                    # chunk to cut per-chunk await overhead and lift throughput.
                    CHUNK = (1024 * 1024) if (status == 200 or content_length > 2 * 1024 * 1024) else (256 * 1024)
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

        # ── Story Assembler (standalone /assembler page, hidden beta) ────────
        # Additive routes only - the main pipeline is untouched (user
        # directive 2026-08-13). Uploads reuse /upload_chunk; the render
        # output lands in History via the standard _out.mp4 + _record_job
        # conventions inside assembler_fns. NO credits charged while the page
        # is unlinked - price the render spawn before making it public.
        if path in ("/assembler/analyze", "/assembler/analyze/") and method == "POST":
            if not _check_rate_limit(_get_client_ip(scope)):
                await send_error("Rate limit exceeded. Try again in a minute.", 429, code="rate_limited")
                return
            try:
                data = json.loads((await _read_body(receive)).decode("utf-8"))
            except Exception:
                await send_error("Invalid request body", 400)
                return
            # Multi-clip (Phase 2): `upload_keys` list, capped at 5; the old
            # single `upload_key` shape is still accepted.
            keys = data.get("upload_keys")
            if not isinstance(keys, list):
                keys = [data.get("upload_key") or ""]
            keys = [str(k or "").strip() for k in keys][:5]
            if not keys or not all(k and _SAFE_KEY_RE.match(k) for k in keys):
                await send_error("Invalid or missing key", 400)
                return
            names = data.get("filenames")
            if not isinstance(names, list):
                names = [data.get("filename") or "video.mp4"]
            names = [str(n or "clip")[:120] for n in names][:len(keys)]
            # "clips" = long -> shorts candidates (2026-08-16); anything else
            # is the original story storyboard.
            mode = "clips" if data.get("mode") == "clips" else "story"
            # Optional steering text for the selection prompt (bounded).
            guidance = " ".join(str(data.get("guidance") or "").split())[:400]
            try:
                # Chunks are written WITHOUT a commit (upload_chunk defers it
                # to spawn time) - flush before the worker reads, exactly like
                # the /process spawn does. Missing this = worker sees no
                # chunks = instant "No upload found" 500 (field bug, 2026-08-13).
                await asyncio.to_thread(tmp_vol.commit)
                call = analyze_story.spawn([f"{uprefix}{k}" for k in keys], names, mode, guidance)
                _record_call(call)
                resp = json.dumps({"call_id": call.object_id}).encode()
                await send({"type": "http.response.start", "status": 202,
                            "headers": CORS + [(b"content-type", b"application/json")]})
                await send({"type": "http.response.body", "body": resp})
            except Exception as e:
                await send_error(str(e))
            return

        if path.startswith("/assembler/analyze-poll/") and method == "GET":
            call_id = path[len("/assembler/analyze-poll/"):].rstrip("/")
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

        if path in ("/assembler/render", "/assembler/render/") and method == "POST":
            if not _check_rate_limit(_get_client_ip(scope)):
                await send_error("Rate limit exceeded. Try again in a minute.", 429, code="rate_limited")
                return
            try:
                data = json.loads((await _read_body(receive)).decode("utf-8"))
            except Exception:
                await send_error("Invalid request body", 400)
                return
            keys = data.get("upload_keys")
            if not isinstance(keys, list):
                keys = [data.get("upload_key") or ""]
            keys = [str(k or "").strip() for k in keys][:5]
            if not keys or not all(k and _SAFE_KEY_RE.match(k) for k in keys):
                await send_error("Invalid or missing key", 400)
                return
            segs = data.get("segments")
            # Bounded: 1-16 windows of [clip, start, end] (bare [start, end] =
            # clip 0), numeric, each ≥0.5s, clip in range, total ≤ 10 minutes.
            ok = isinstance(segs, list) and 1 <= len(segs) <= 16
            total = 0.0
            norm_segs = []
            if ok:
                for seg in segs:
                    try:
                        if len(seg) >= 3:
                            ci, a, b = int(seg[0]), float(seg[1]), float(seg[2])
                        else:
                            ci, a, b = 0, float(seg[0]), float(seg[1])
                    except (TypeError, ValueError, IndexError):
                        ok = False
                        break
                    if not (0 <= ci < len(keys)) or not (0 <= a < b and b - a >= 0.5):
                        ok = False
                        break
                    total += b - a
                    norm_segs.append([ci, a, b])
            if not ok or total > 600:
                await send_error("Invalid segments", 400, code="bad_segments")
                return
            # Optional narration audio, uploaded through /upload_chunk like the
            # clips - validated and uid-prefixed the same way.
            vo_key = (data.get("vo_key") or "").strip() or None
            if vo_key and not _SAFE_KEY_RE.match(vo_key):
                await send_error("Invalid vo_key", 400)
                return
            # Clips mode (2026-08-16): `variant` suffixes the output key so N
            # clips rendered from ONE source each get their own History row;
            # `hook_text` is burned as the on-screen hook; `tighten` excises
            # in-clip silence gaps. All bounded here, defaults keep the story
            # render byte-identical.
            variant = str(data.get("variant") or "").strip()
            if variant and not _SAFE_VARIANT_RE.match(variant):
                await send_error("Invalid variant", 400)
                return
            hook_text = str(data.get("hook_text") or "").strip()[:120]
            tighten = bool(data.get("tighten", False))
            # Branding (2026-08-16): intro/outro/watermark are uploads too -
            # validated + uid-prefixed like every key; fade and the watermark
            # geometry are clamped here AND in the worker.
            brand_keys = {}
            for fld in ("intro_key", "outro_key", "wm_key"):
                v = str(data.get(fld) or "").strip()
                if v and not _SAFE_KEY_RE.match(v):
                    await send_error(f"Invalid {fld}", 400)
                    return
                brand_keys[fld] = f"{uprefix}{v}" if v else None
            try:
                fade = max(0.0, min(3.0, float(data.get("fade") or 0)))
            except (TypeError, ValueError):
                fade = 0.0
            wm_raw = data.get("wm")
            wm = None
            if isinstance(wm_raw, dict) and brand_keys["wm_key"]:
                try:
                    wm = {"x": max(0.0, min(1.0, float(wm_raw.get("x", 0.02)))),
                          "y": max(0.0, min(1.0, float(wm_raw.get("y", 0.02)))),
                          "w": max(0.03, min(0.8, float(wm_raw.get("w", 0.18)))),
                          "opacity": max(0.1, min(1.0, float(wm_raw.get("opacity", 0.85))))}
                except (TypeError, ValueError):
                    wm = None
            # Auto-reframe (2026-08-16): only "9:16" is a valid value; the
            # worker ignores it for sources that are already vertical.
            reframe = data.get("reframe") if data.get("reframe") in ("9:16", "fit") else None
            try:
                await asyncio.to_thread(tmp_vol.commit)   # flush before the worker reads
                call = render_story.spawn([f"{uprefix}{k}" for k in keys],
                                          norm_segs,
                                          str(data.get("filename") or "story.mp4")[:120],
                                          bool(data.get("captions", True)),
                                          f"{uprefix}{vo_key}" if vo_key else None,
                                          tighten, hook_text, variant,
                                          brand_keys["intro_key"], brand_keys["outro_key"],
                                          fade, brand_keys["wm_key"] if wm else None, wm,
                                          reframe)
                _record_call(call)
                resp = json.dumps({"call_id": call.object_id}).encode()
                await send({"type": "http.response.start", "status": 202,
                            "headers": CORS + [(b"content-type", b"application/json")]})
                await send({"type": "http.response.body", "body": resp})
            except Exception as e:
                await send_error(str(e))
            return

        if path.startswith("/assembler/render-poll/") and method == "GET":
            call_id = path[len("/assembler/render-poll/"):].rstrip("/")
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
