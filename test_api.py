#!/usr/bin/env python3
"""
End-to-end API test against the LIVE production API (auth + chunked upload +
video_key burn). Costs real GPU time and consumes ONE video quota credit — run
only when you mean to.

Auth (pick one, via env):
  HEBPIPE_TOKEN=...                         # a session token, or
  HEBPIPE_USER=... HEBPIPE_PASS=...         # logs in, or
  HEBPIPE_USER/PASS + HEBPIPE_INVITE + HEBPIPE_EMAIL   # registers first

Optional:
  HEBPIPE_VIDEO=input_vids/original.mp4     # test clip (default shown)
  API_BASE=https://...modal.run             # override target

  python test_api.py
"""
import json
import os
import sys
import time
import uuid
from pathlib import Path

import requests

API_BASE = os.environ.get("API_BASE", "https://yotamjacob--hebrew-video-pipeline-api.modal.run")
VIDEO = Path(os.environ.get("HEBPIPE_VIDEO", "input_vids/original.mp4"))
CHUNK = 5 * 1024 * 1024

PASS = "\033[32m✓\033[0m"
FAIL = "\033[31m✗\033[0m"


def check(label, cond, detail=""):
    print(f"  {PASS if cond else FAIL}  {label}" + (f" — {detail}" if (detail and not cond) else ""))
    if not cond:
        sys.exit(1)


def get_token():
    tok = os.environ.get("HEBPIPE_TOKEN")
    if tok:
        return tok
    user, pw = os.environ.get("HEBPIPE_USER"), os.environ.get("HEBPIPE_PASS")
    if not (user and pw):
        print("No credentials. Set HEBPIPE_TOKEN, or HEBPIPE_USER + HEBPIPE_PASS "
              "(+ HEBPIPE_INVITE + HEBPIPE_EMAIL to register).")
        sys.exit(2)
    invite, email = os.environ.get("HEBPIPE_INVITE"), os.environ.get("HEBPIPE_EMAIL")
    if invite and email:
        r = requests.post(f"{API_BASE}/auth/register", timeout=30,
                          json={"username": user, "password": pw, "invite": invite, "email": email})
        if r.status_code == 200:
            return r.json()["token"]
        # 409 = already exists → fall through to login
        if r.status_code != 409:
            check("register", False, f"{r.status_code}: {r.text[:200]}")
    r = requests.post(f"{API_BASE}/auth/login", timeout=30, json={"username": user, "password": pw})
    check("login", r.status_code == 200, f"{r.status_code}: {r.text[:200]}")
    return r.json()["token"]


def poll(url, hdrs, poll_interval=5, timeout=1200):
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = requests.get(url, headers=hdrs, timeout=60)
        if r.status_code == 200:
            return r
        if r.status_code == 202:
            time.sleep(poll_interval)
            continue
        check("poll status", False, f"got {r.status_code}: {r.text[:200]}")
    check("poll timeout", False, f"still running after {timeout}s")


# ── 0. Auth ───────────────────────────────────────────────────────────────────
print("\n[0/5] Auth")
token = get_token()
H = {"Authorization": f"Bearer {token}"}
check("got a session token", bool(token))
check(f"test video exists ({VIDEO})", VIDEO.exists(), str(VIDEO))

# ── 1. Health check ────────────────────────────────────────────────────────────
print("\n[1/5] Health check")
r = requests.get(f"{API_BASE}/", timeout=60)
check("status 200", r.status_code == 200)
check('body {"status":"ok"}', r.json().get("status") == "ok")

# ── 2. Chunked upload ──────────────────────────────────────────────────────────
data = VIDEO.read_bytes()
key = uuid.uuid4().hex
n_chunks = (len(data) + CHUNK - 1) // CHUNK
print(f"\n[2/5] Upload  ({len(data)//1024//1024} MB in {n_chunks} chunks)")
for i in range(n_chunks):
    part = data[i * CHUNK:(i + 1) * CHUNK]
    r = requests.post(f"{API_BASE}/upload_chunk/?key={key}&index={i}", headers=H,
                      data=part, timeout=120)
    check(f"chunk {i + 1}/{n_chunks}", r.status_code == 200, f"{r.status_code}: {r.text[:120]}")

# ── 3. /process ─────────────────────────────────────────────────────────────────
print("\n[3/5] /process")
t0 = time.time()
r = requests.post(f"{API_BASE}/process/", headers=H, timeout=60, params=dict(
    key=key, filename=VIDEO.name, cut_silences="true", burn_captions="true",
    enhance_audio="false", min_silence="0.5", padding="0.2"))
check("spawn 202", r.status_code == 202, f"{r.status_code}: {r.text[:200]}")
call_id = r.json().get("call_id", "")
check("call_id present", bool(call_id))
print(f"  polling {call_id}…")
pr = poll(f"{API_BASE}/process_poll/{call_id}/?key={key}", H, timeout=1200)
res = pr.json()
captions = res.get("captions", [])
video_key = res.get("video_key", "")
check(f"process done ({time.time()-t0:.0f}s)", True)
check(f"captions returned ({len(captions)} cues)", isinstance(captions, list) and len(captions) > 0)
check("video_key returned", bool(video_key), json.dumps(res)[:200])

# ── 4. /burn ─────────────────────────────────────────────────────────────────────
print("\n[4/5] /burn")
t0 = time.time()
r = requests.post(f"{API_BASE}/burn/", headers={**H, "Content-Type": "application/json"},
                  params=dict(video_key=video_key, filename=VIDEO.name),
                  data=json.dumps(captions), timeout=60)
check("burn spawn 202", r.status_code == 202, f"{r.status_code}: {r.text[:200]}")
burn_id = r.json().get("call_id", "")
check("burn call_id present", bool(burn_id))
print(f"  polling {burn_id}…")
br = poll(f"{API_BASE}/burn_poll/{burn_id}/", H, timeout=600)
check(f"burn done ({time.time()-t0:.0f}s)", br.status_code == 200)
burn_res = br.json()
output_key = burn_res.get("output_key", "")
check("burn returned output_key", bool(output_key), json.dumps(burn_res)[:200])

# ── 5. Download the burned video ────────────────────────────────────────────
# The first read right after a burn can stall while the container reloads the
# volume — retry once before failing.
print(f"\n[5/5] /download  ({output_key})")
dl = None
for attempt in (1, 2):
    try:
        dl = requests.get(f"{API_BASE}/download/{output_key}/", headers=H, timeout=300)
        break
    except requests.exceptions.RequestException as e:
        if attempt == 2:
            check("download 200", False, f"{type(e).__name__}: {e}")
        print(f"  download attempt {attempt} failed ({type(e).__name__}) — retrying…")
        time.sleep(5)
check(f"download 200", dl.status_code == 200, f"{dl.status_code}: {dl.text[:200]}")
final = dl.content
check(f"final video non-empty ({len(final):,} bytes)", len(final) > 1000)
check("final looks like MP4", final[4:8] == b"ftyp", f"got {final[4:8]!r}")
out = Path("test_output_edited.mp4")
out.write_bytes(final)
check(f"saved to {out}", out.exists())

print("\n\033[32mAll e2e tests passed.\033[0m\n")
