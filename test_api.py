#!/usr/bin/env python3
"""
End-to-end API test (async polling pattern):
  1. Health check
  2. POST /process  → 202 with call_id
     GET  /process_poll/{id} until 200 → binary (captions JSON + cut video)
  3. POST /burn     → 202 with call_id
     GET  /burn_poll/{id}    until 200 → final video with captions
"""
import json
import struct
import sys
import time
from pathlib import Path

import requests

API_BASE = "https://yotamjacob--hebrew-video-pipeline-api.modal.run"
VIDEO = Path("input_vids/WhatsApp Video 2026-04-22 at 11.11.43.mp4")

PASS = "\033[32m✓\033[0m"
FAIL = "\033[31m✗\033[0m"

def check(label, cond, detail=""):
    if cond:
        print(f"  {PASS}  {label}")
    else:
        print(f"  {FAIL}  {label}" + (f" — {detail}" if detail else ""))
        sys.exit(1)

def poll(url, poll_interval=5, timeout=900):
    """Poll url every poll_interval seconds until 200. Returns response."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = requests.get(url, timeout=60)
        if r.status_code == 200:
            return r
        if r.status_code == 202:
            time.sleep(poll_interval)
            continue
        check(f"poll status", False, f"got {r.status_code}: {r.text[:200]}")
    check("poll timeout", False, f"still running after {timeout}s")

# ── 1. Health check ──────────────────────────────────────────────────────────
print("\n[1/3] Health check")
r = requests.get(f"{API_BASE}/", timeout=60)
check("status 200",          r.status_code == 200)
check('body {"status":"ok"}', r.json().get("status") == "ok")

# ── 2. /process ─────────────────────────────────────────────────────────────
print(f"\n[2/3] /process  (uploading {VIDEO.stat().st_size // 1024 // 1024} MB, please wait…)")
video_bytes = VIDEO.read_bytes()

t0 = time.time()
r = requests.post(
    f"{API_BASE}/process/",
    params=dict(
        filename=VIDEO.name,
        cut_silences="true",
        burn_captions="true",
        enhance_audio="true",
        min_silence="0.5",
        padding="0.2",
    ),
    data=video_bytes,
    headers={"Content-Type": "application/octet-stream"},
    timeout=60,
)
check(f"spawn status 202", r.status_code == 202, f"got {r.status_code}: {r.text[:200]}")
call_id = r.json().get("call_id", "")
check("call_id present", bool(call_id), f"response: {r.text}")

print(f"  Job spawned: {call_id}  — polling…")
poll_url = f"{API_BASE}/process_poll/{call_id}/?filename={requests.utils.quote(VIDEO.name)}"
pr = poll(poll_url, poll_interval=5, timeout=1200)
elapsed = time.time() - t0
check(f"process done (took {elapsed:.0f}s)", pr.status_code == 200)

body = pr.content
check(f"response not empty ({len(body):,} bytes)", len(body) > 8)

json_len = struct.unpack("<I", body[:4])[0]
check(f"json_len plausible ({json_len} bytes)", 0 < json_len < len(body) - 4)

captions_json = body[4:4 + json_len].decode("utf-8")
captions = json.loads(captions_json)
check(f"captions valid JSON list ({len(captions)} cues)", isinstance(captions, list) and len(captions) > 0)

cut_video = body[4 + json_len:]
check(f"cut video non-empty ({len(cut_video):,} bytes)", len(cut_video) > 1000)
check("cut video looks like MP4", cut_video[4:8] == b"ftyp", f"got {cut_video[4:8]!r}")
print(f"  Sample captions: {captions[:2]}")

# ── 3. /burn ─────────────────────────────────────────────────────────────────
print(f"\n[3/3] /burn  (sending {len(cut_video):,} bytes + {len(captions)} captions…)")

captions_bytes = captions_json.encode("utf-8")
packed = struct.pack("<I", len(captions_bytes)) + captions_bytes + cut_video

t0 = time.time()
r = requests.post(
    f"{API_BASE}/burn/",
    params=dict(filename=VIDEO.name),
    data=packed,
    headers={"Content-Type": "application/octet-stream"},
    timeout=60,
)
check(f"burn spawn status 202", r.status_code == 202, f"got {r.status_code}: {r.text[:200]}")
burn_id = r.json().get("call_id", "")
check("burn call_id present", bool(burn_id))

print(f"  Burn job spawned: {burn_id}  — polling…")
burn_poll_url = f"{API_BASE}/burn_poll/{burn_id}/?filename={requests.utils.quote(VIDEO.name)}"
br = poll(burn_poll_url, poll_interval=5, timeout=600)
elapsed = time.time() - t0
check(f"burn done (took {elapsed:.0f}s)", br.status_code == 200)

final = br.content
check(f"final video non-empty ({len(final):,} bytes)", len(final) > 1000)
check("final video looks like MP4", final[4:8] == b"ftyp", f"got {final[4:8]!r}")

out = Path("test_output_edited.mp4")
out.write_bytes(final)
check(f"saved to {out}", out.exists())

print("\n\033[32mAll tests passed.\033[0m\n")
