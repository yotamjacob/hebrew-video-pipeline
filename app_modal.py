"""
Hebrew Video Pipeline — Modal API backend (deploy entrypoint).

Deploy:  modal deploy app_modal.py
Dev:     modal serve app_modal.py

This module holds only the ASGI router (api). The pipeline itself lives in:
  pipeline_core.py   app/images/constants + pure helpers
  pipeline_fns.py    process_video, burn_captions_fn, burn_hook_fn
  stock_helpers.py   stock-footage pure helpers
  broll_fns.py       Veo + stock B-roll Modal functions
  content_fns.py     hook/caption generation
  metricool_fns.py   scheduling via Metricool MCP
"""

import modal

from pipeline_core import (
    light_image,
    jobs_store,
    app, image, tmp_vol, TMP_DIR,
    _SAFE_KEY_RE, _SAFE_DOWNLOAD_KEY_RE, _check_rate_limit, _get_client_ip,
    _poll_fn_call,
)
from pipeline_fns import process_video, burn_captions_fn, burn_hook_fn
from broll_fns import generate_broll_video, analyze_broll, analyze_stock_broll, search_stock_clips
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
@app.function(image=light_image, timeout=900, volumes={TMP_DIR: tmp_vol})
@modal.concurrent(max_inputs=20)
@modal.asgi_app()
def api():
    import asyncio
    import json
    from urllib.parse import parse_qs

    CORS = [
        (b"access-control-allow-origin",  b"*"),
        (b"access-control-allow-methods", b"GET, POST, DELETE, OPTIONS"),
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

        # ── Metricool OAuth: start (redirect user to authorize) ──
        if path in ("/oauth/start", "/oauth/start/") and method == "GET":
            import secrets, hashlib, base64
            import urllib.parse as _up
            verifier = secrets.token_urlsafe(64)
            challenge = base64.urlsafe_b64encode(
                hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
            state = secrets.token_urlsafe(24)
            oauth_store[f"pkce:{state}"] = verifier
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
            verifier = oauth_store.get(f"pkce:{state}") if state else None

            def _html(msg, ok=True):
                color = "#059669" if ok else "#DC2626"
                return (f"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
                        f"<body style='font-family:system-ui;background:#F2EEF8;color:#1E1033;display:flex;"
                        f"align-items:center;justify-content:center;height:100vh;margin:0;text-align:center'>"
                        f"<div style='background:#fff;border:1.5px solid #EDE9FE;border-radius:20px;padding:36px 28px;max-width:360px'>"
                        f"<div style='font-size:44px'>{'✅' if ok else '⚠️'}</div>"
                        f"<h2 style='color:{color};margin:12px 0 6px'>{'Metricool connected' if ok else 'Connection failed'}</h2>"
                        f"<p style='color:#6B7080;font-size:14px'>{msg}</p></div></body>").encode()

            if not code or not verifier:
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
                oauth_store["tokens"] = {"refresh_token": tr["refresh_token"],
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
            connected = bool(oauth_store.get("tokens"))
            body = json.dumps({"connected": connected}).encode()
            await send({"type": "http.response.start", "status": 200,
                        "headers": CORS + [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": body})
            return

        # ── Schedule a post (spawn) ──
        if path in ("/schedule", "/schedule/") and method == "POST":
            if not _check_rate_limit(_get_client_ip(scope)):
                await send_error("Rate limit exceeded. Try again in a minute.", 429)
                return
            if not oauth_store.get("tokens"):
                await send_error("not_connected", 400)
                return
            body = await _read_body(receive)
            try:
                data = json.loads(body.decode("utf-8"))
                call = schedule_post_fn.spawn(json.dumps(data))
                resp = json.dumps({"call_id": call.object_id}).encode()
                await send({"type": "http.response.start", "status": 202,
                            "headers": CORS + [(b"content-type", b"application/json")]})
                await send({"type": "http.response.body", "body": resp})
            except Exception as e:
                await send_error(str(e))
            return

        if path.startswith("/schedule-poll/") and method == "GET":
            call_id = path[len("/schedule-poll/"):].rstrip("/")
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
                call = burn_captions_fn.spawn(video_key, captions_json, font, margin_v_pct, broll_json, font_size, hook_json, source_name=filename)
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


        # ── Job history: list burned videos ──
        if path in ("/jobs", "/jobs/") and method == "GET":
            try:
                jobs = []
                for key in list(jobs_store.keys()):
                    if not key.endswith("_out.mp4"):
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
            if not key or not _SAFE_DOWNLOAD_KEY_RE.match(key) or not key.endswith("_out.mp4"):
                await send_error("Invalid key", 400)
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

        # Caption generation (suggested social caption from transcript)
        if path in ("/generate-caption", "/generate-caption/") and method == "POST":
            if not _check_rate_limit(_get_client_ip(scope)):
                await send_error("Rate limit exceeded. Try again in a minute.", 429)
                return
            body = await _read_body(receive)
            data = json.loads(body.decode("utf-8"))
            try:
                call = generate_caption_options.spawn(
                    data.get("captions_json", "[]"),
                    data.get("video_key", ""),
                    data.get("platforms", ""),
                )
                resp = json.dumps({"call_id": call.object_id}).encode()
                await send({"type": "http.response.start", "status": 202,
                            "headers": CORS + [(b"content-type", b"application/json")]})
                await send({"type": "http.response.body", "body": resp})
            except Exception as e:
                await send_error(str(e))
            return

        if path.startswith("/generate-caption-poll/") and method == "GET":
            call_id = path[len("/generate-caption-poll/"):].rstrip("/")
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
