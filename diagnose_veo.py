"""
Veo rate-limit diagnostic.

Makes ONE raw HTTP call to the Gemini video-generation endpoint and dumps
the complete response — status, headers, and JSON body — without any SDK
wrapping, retry logic, or error massaging.

Usage:
    GEMINI_API_KEY=AIza... python diagnose_veo.py

Optional overrides:
    GEMINI_API_KEY=... VEO_MODEL=veo-2.0-generate-001 python diagnose_veo.py
"""

from __future__ import annotations

import json
import os
import sys
import textwrap
import urllib.request
import urllib.error
import urllib.parse

# ---------------------------------------------------------------------------
# Config — read from environment, match what app_modal.py uses
# ---------------------------------------------------------------------------
API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
MODEL   = os.environ.get("VEO_MODEL", "veo-3.1-generate-001")

BASE_URL = "https://generativelanguage.googleapis.com"
ENDPOINT = f"{BASE_URL}/v1beta/models/{MODEL}:generateVideos"

PROMPT = "a calm ocean wave, 4 seconds, 720p, cinematic"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _banner(title: str) -> None:
    print(f"\n{'='*64}")
    print(f"  {title}")
    print(f"{'='*64}")


def _check_preview(model: str) -> None:
    _banner("MODEL CHECK")
    print(f"  Model ID : {model}")
    if "-preview" in model:
        print("  ⚠  WARNING: -preview variant detected.")
        print("     Preview models typically have 0.1–0.3× the rate limits")
        print("     of the production model.  Use the non-preview ID.")
    elif "-exp" in model:
        print("  ⚠  WARNING: -exp (experimental) variant detected.")
        print("     Experimental models may have very low or no SLA quotas.")
    elif "-flash-exp" in model:
        print("  ⚠  WARNING: -flash-exp variant detected (very low quota).")
    else:
        print("  ✓  No -preview/-exp suffix found — looks like a production ID.")


def _check_key(key: str) -> None:
    _banner("API KEY CHECK")
    if not key:
        print("  ERROR: GEMINI_API_KEY is not set.")
        print("  Run: GEMINI_API_KEY=AIza... python diagnose_veo.py")
        sys.exit(1)

    # API keys are opaque — they do NOT encode the project ID.
    # The key format is: AIzaSy<base64-ish 33 chars>
    print(f"  Key prefix : {key[:8]}{'*' * (len(key) - 8)}")
    print(f"  Key length : {len(key)} chars")

    if not key.startswith("AIza"):
        print("  ⚠  Does not start with 'AIza' — may not be a Google AI API key.")
    else:
        print("  ✓  Starts with 'AIza' as expected for a Google API key.")

    print()
    print("  NOTE: Google API keys are OPAQUE — the project ID is not encoded")
    print("  inside the key string itself.  To find the linked project:")
    print("    1. Open https://console.cloud.google.com/apis/credentials")
    print("    2. Find the key whose prefix matches the one above.")
    print("    3. Confirm that billing IS enabled on that project:")
    print("       https://console.cloud.google.com/billing")
    print("    4. Confirm Vertex AI / Generative Language API is enabled:")
    print("       https://console.cloud.google.com/apis/library/generativelanguage.googleapis.com")
    print()
    print("  Alternatively, this endpoint reveals the key's project name:")
    print(f"    GET {BASE_URL}/v1beta/models?key={key[:12]}...")

    # Try listing models — a cheap call that reveals key validity and quota
    _banner("MODEL LIST CHECK (cheap quota probe)")
    models_url = f"{BASE_URL}/v1beta/models?key={key}"
    req = urllib.request.Request(models_url, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read().decode())
            model_names = [m.get("name", "") for m in body.get("models", [])]
            veo_models  = [n for n in model_names if "veo" in n.lower()]
            print(f"  HTTP status : 200 OK")
            print(f"  Total models visible : {len(model_names)}")
            if veo_models:
                print(f"  Veo models accessible:")
                for n in veo_models:
                    print(f"    • {n}")
            else:
                print(f"  ⚠  NO Veo models found in the model list.")
                print(f"     This key may not have Veo access, or Veo models are")
                print(f"     not listed via /v1beta/models for your account.")
    except urllib.error.HTTPError as err:
        body_bytes = err.read()
        try:
            body = json.loads(body_bytes)
        except Exception:
            body = body_bytes.decode(errors="replace")
        print(f"  HTTP status : {err.code}")
        print(f"  Raw body    : {json.dumps(body, indent=2) if isinstance(body, dict) else body}")


def _make_video_call(key: str, model: str) -> None:
    _banner("VIDEO GENERATION CALL — raw HTTP, no SDK, no retry")

    url = f"{ENDPOINT}?key={key}"
    payload = {
        "prompt": PROMPT,
        "config": {
            "aspectRatio": "16:9",
            "numberOfVideos": 1
        }
    }
    body_bytes = json.dumps(payload).encode()

    print(f"  POST {ENDPOINT}?key=<redacted>")
    print(f"  Body : {json.dumps(payload, indent=2)}")
    print()

    req = urllib.request.Request(
        url,
        data=body_bytes,
        headers={
            "Content-Type": "application/json",
            "User-Agent":   "diagnose_veo/1.0",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            status  = resp.status
            headers = dict(resp.headers)
            raw     = resp.read().decode(errors="replace")
            try:
                body = json.loads(raw)
            except Exception:
                body = raw

            _banner("RESPONSE — SUCCESS")
            print(f"  HTTP status : {status}")
            print(f"  Headers     :")
            for k, v in headers.items():
                print(f"    {k}: {v}")
            print(f"  Body:")
            print(textwrap.indent(json.dumps(body, indent=2) if isinstance(body, dict) else body, "    "))

            # Surface the operation name for diagnosis
            if isinstance(body, dict):
                op_name = body.get("name", "")
                done    = body.get("done", None)
                print()
                print(f"  Operation name : {op_name!r}")
                print(f"  done           : {done}")
                if not op_name:
                    print("  ⚠  No operation name returned — possible validation rejection.")

    except urllib.error.HTTPError as err:
        raw_bytes = err.read()
        try:
            body = json.loads(raw_bytes)
        except Exception:
            body = raw_bytes.decode(errors="replace")

        _banner("RESPONSE — HTTP ERROR")
        print(f"  HTTP status   : {err.code}")
        print(f"  Reason        : {err.reason}")
        print(f"  URL           : {err.url}")
        print(f"  Headers       :")
        for k, v in err.headers.items():
            print(f"    {k}: {v}")
        print(f"  Raw body:")
        if isinstance(body, dict):
            print(textwrap.indent(json.dumps(body, indent=2), "    "))
        else:
            print(textwrap.indent(str(body), "    "))

        # Structured diagnosis
        if isinstance(body, dict) and "error" in body:
            err_obj = body["error"]
            print()
            print(f"  --- Structured error ---")
            print(f"  code    : {err_obj.get('code')}")
            print(f"  status  : {err_obj.get('status')!r}")
            print(f"  message : {err_obj.get('message')!r}")
            details = err_obj.get("details", [])
            if details:
                print(f"  details ({len(details)} item(s)):")
                for d in details:
                    print(f"    {json.dumps(d, indent=4)}")
            _diagnose_error_code(err.code, err_obj)

    except urllib.error.URLError as err:
        _banner("RESPONSE — NETWORK ERROR")
        print(f"  URLError reason : {err.reason}")
        print(f"  This is a network-level failure, not a Gemini API error.")

    except Exception as err:
        _banner("RESPONSE — UNEXPECTED ERROR")
        print(f"  type    : {type(err).__name__}")
        print(f"  message : {err}")
        import traceback; traceback.print_exc()


def _diagnose_error_code(http_code: int, err_obj: dict) -> None:
    status  = err_obj.get("status", "")
    message = err_obj.get("message", "")
    details = err_obj.get("details", [])

    print()
    print(f"  --- Diagnosis ---")

    if http_code == 429 or status == "RESOURCE_EXHAUSTED":
        print(f"  CONFIRMED 429 / RESOURCE_EXHAUSTED rate-limit.")
        print(f"  The error is real — NOT a SDK misclassification.")

        # Try to pull quota limit info from details
        for d in details:
            if d.get("@type", "").endswith("QuotaFailure"):
                for v in d.get("violations", []):
                    print(f"  Quota subject   : {v.get('subject')}")
                    print(f"  Quota desc      : {v.get('description')}")

        # Classify sub-type
        if "per-minute" in message.lower() or "rpm" in message.lower():
            print(f"  Sub-type: per-minute RPM limit hit.")
        elif "daily" in message.lower() or "per day" in message.lower():
            print(f"  Sub-type: daily quota exhausted.")
        elif "concurrent" in message.lower():
            print(f"  Sub-type: concurrent-requests limit hit.")
        else:
            print(f"  Sub-type: unspecified — check message and details above.")

    elif http_code == 403 or status == "PERMISSION_DENIED":
        print(f"  PERMISSION DENIED — not a rate limit.")
        print(f"  Possible causes:")
        print(f"    • API key is valid but Veo / video generation is not enabled")
        print(f"    • The key belongs to a project without billing")
        print(f"    • The model requires allowlist access your project doesn't have")
        print(f"    • Wrong API: key is for Cloud AI Platform, not Generative Language API")

    elif http_code == 404 or status == "NOT_FOUND":
        print(f"  MODEL NOT FOUND — the model ID '{MODEL}' does not exist or")
        print(f"  is not accessible on your API key.")
        print(f"  Verify the exact model ID at:")
        print(f"    https://ai.google.dev/api/generate-content#v1beta.models.list")

    elif http_code == 400 or status == "INVALID_ARGUMENT":
        print(f"  BAD REQUEST — the request payload was rejected before any quota")
        print(f"  was consumed.  This is NOT a rate limit.")

    elif http_code in (500, 502, 503):
        print(f"  SERVER ERROR — transient Google infrastructure issue.")
        print(f"  Not a rate limit; retrying with backoff is appropriate.")

    else:
        print(f"  Unrecognised status {http_code} / {status!r}.")
        print(f"  Inspect the full body above to determine the cause.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print()
    print("Veo diagnostic  —  one raw HTTP call, no retry, no SDK wrapping")
    print(f"Python {sys.version}")

    _check_preview(MODEL)
    _check_key(API_KEY)
    _make_video_call(API_KEY, MODEL)

    print()
    print("Diagnostic complete.")
