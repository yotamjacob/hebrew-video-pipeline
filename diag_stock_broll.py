#!/usr/bin/env python3
"""
Stock B-roll regression diagnostic.

Usage:
  python diag_stock_broll.py                # processes test video, runs analysis
  python diag_stock_broll.py --cached       # skips video processing, uses cached captions
"""
import json, sys, time, argparse
from pathlib import Path

import requests

API_BASE = "https://yotamjacob--hebrew-video-pipeline-api.modal.run"
VIDEO    = Path("input_vids/WhatsApp Video 2026-04-22 at 11.11.43.mp4")
CACHE    = Path(".diag_captions_cache.json")

W = "\033[33m"   # yellow
R = "\033[31m"   # red
G = "\033[32m"   # green
B = "\033[34m"   # blue
X = "\033[0m"    # reset
HR = "─" * 72


def poll(url, label="polling", interval=5, timeout=900):
    deadline = time.time() + timeout
    print(f"  {label}", end="", flush=True)
    while time.time() < deadline:
        r = requests.get(url, timeout=60)
        if r.status_code == 200:
            print(f" done", flush=True)
            return r
        if r.status_code == 202:
            print(".", end="", flush=True)
            time.sleep(interval)
            continue
        print(f"\n{R}ERROR: {r.status_code}: {r.text[:200]}{X}")
        sys.exit(1)
    print(f"\n{R}TIMEOUT{X}")
    sys.exit(1)


def get_captions(force=False):
    if not force and CACHE.exists():
        captions = json.loads(CACHE.read_text())
        print(f"{G}[1] Loaded {len(captions)} captions from cache ({CACHE}){X}")
        return captions

    if not VIDEO.exists():
        print(f"{R}[1] Video not found: {VIDEO}{X}")
        sys.exit(1)

    size_mb = VIDEO.stat().st_size / 1024 / 1024
    print(f"[1] Processing {VIDEO.name} ({size_mb:.1f} MB)…")
    video_bytes = VIDEO.read_bytes()
    r = requests.post(
        f"{API_BASE}/process/",
        params=dict(
            filename=VIDEO.name,
            cut_silences="true",
            burn_captions="true",
            enhance_audio="false",
            transcribe_for_broll="true",
        ),
        data=video_bytes,
        headers={"Content-Type": "application/octet-stream"},
        timeout=120,
    )
    if r.status_code != 202:
        print(f"{R}spawn failed: {r.status_code} {r.text[:200]}{X}")
        sys.exit(1)
    call_id = r.json()["call_id"]
    pr = poll(f"{API_BASE}/process_poll/{call_id}/", label=f"  transcribing (job {call_id[:8]}…)")
    result = pr.json()
    captions = result.get("captions", [])
    if not captions:
        print(f"{R}No captions in response. Keys: {list(result.keys())}{X}")
        sys.exit(1)
    CACHE.write_text(json.dumps(captions, ensure_ascii=False))
    print(f"  {len(captions)} cues — saved to {CACHE}")
    return captions


def run_stock_analysis(captions):
    print(f"\n[2] Running /stock-broll on {len(captions)} captions…")
    r = requests.post(
        f"{API_BASE}/stock-broll/",
        json={"captions_json": json.dumps(captions)},
        timeout=60,
    )
    if r.status_code == 429:
        print(f"{R}Rate limited: {r.text}{X}")
        sys.exit(1)
    if r.status_code != 202:
        print(f"{R}spawn failed: {r.status_code} {r.text[:200]}{X}")
        sys.exit(1)
    call_id = r.json()["call_id"]
    result = poll(f"{API_BASE}/stock-broll-poll/{call_id}/", label=f"  analyzing (job {call_id[:8]}…)")
    return result.json().get("moments", [])


def report(moments):
    print(f"\n{HR}")
    print("DIAGNOSTIC REPORT")
    print(HR)
    print(f"Total moments returned: {len(moments)}")

    any_clips = False
    all_weak  = True
    search_regression_evidence = []
    disqualify_evidence = []

    for i, m in enumerate(moments):
        broad  = m.get("broad_search_prompt", "")
        strict = m.get("strict_eval_prompt", "")
        clips  = m.get("clips", [])
        weak   = m.get("weak_match", False)

        if clips:
            any_clips = True
        if not weak:
            all_weak = False

        broad_words = len(broad.split()) if broad else 0

        print(f"\n{B}── Moment {i+1} ──────────────────────────────────────────────{X}")
        print(f"  start:                 {m.get('start', '?')}s")
        print(f"  broll_duration:        {m.get('broll_duration_seconds', '?')}s")
        print(f"  subject_complexity:    {m.get('subject_complexity', '?')}")
        print(f"  weak_match:            {W if weak else G}{weak}{X}")
        print(f"  clips_returned:        {len(clips)}")
        print()
        # Search prompt audit
        broad_ok = broad_words <= 6
        print(f"  broad_search_prompt  [{broad_words} words, {'OK' if broad_ok else 'TOO LONG'}]:")
        print(f"    {G if broad_ok else R}{broad!r}{X}")
        if not broad_ok:
            search_regression_evidence.append(
                f"Moment {i+1}: broad_search_prompt is {broad_words} words long: {broad!r}"
            )

        has_disqualify = "DISQUALIFY" in strict.upper()
        print(f"  strict_eval_prompt   [DISQUALIFY={'YES' if has_disqualify else 'NO'}]:")
        # Wrap for display
        for line in (strict or "(empty)").split(". "):
            print(f"    {line.strip()}")

        disqualify_clause = ""
        if "DISQUALIFY:" in strict:
            disqualify_clause = strict[strict.index("DISQUALIFY:"):].split(".")[0]
            # Check if disqualifier is visually verifiable
            unverifiable = any(w in disqualify_clause.lower() for w in [
                "verbal", "audio", "says", "speaking", "sound", "tone",
                "without showing", "not visible",
            ])
            if unverifiable:
                disqualify_evidence.append(
                    f"Moment {i+1}: DISQUALIFY clause may be unverifiable from thumbnail: "
                    f"{disqualify_clause!r}"
                )

        # Clips detail
        if clips:
            print(f"\n  Clips (score ≥ 6):")
            for j, c in enumerate(clips):
                print(f"    [{j+1}] score={c.get('score')} "
                      f"source={c.get('source')} "
                      f"strategy={c.get('clip_window_strategy')}")
                print(f"         reason:  {c.get('score_reason', '')!r}")
                print(f"         title:   {c.get('title', '')!r}")
                print(f"         tags:    {c.get('tags', [])[:4]}")
                print(f"         thumb:   {c.get('thumbnail', '')[:70]}")

    # Classification
    print(f"\n{HR}")
    print("CLASSIFICATION")
    print(HR)

    if search_regression_evidence:
        print(f"\n{R}CASE A — SEARCH REGRESSION:{X}")
        for e in search_regression_evidence:
            print(f"  !! {e}")
    else:
        print(f"{G}Case A (search regression): RULED OUT{X}")
        print(f"  broad_search_prompts are all short (≤ 6 words)")

    if all_weak and not any_clips:
        print(f"\n{W}All moments have weak_match=True, clips=[]{X}")
        print(f"  → Candidates were found but scored < 6, OR 0 candidates found.")
        print(f"  → Check Modal function logs for '[stock] no portrait candidates'")
        print(f"    vs '[stock] Haiku scoring failed' to distinguish.")
        print(f"\n  Most likely: {W}CASE B or D — DISQUALIFY over-rejection{X}")
        print(f"  The DISQUALIFY clause in strict_eval_prompt is either:")
        print(f"    B: correct clause but Haiku applies it too aggressively")
        print(f"    D: clause is not verifiable from thumbnail/metadata")
    elif not any_clips:
        print(f"\n{W}Some moments returned clips, some did not.{X}")
    else:
        print(f"\n{G}Some clips passed scoring (≥ 6). Partial regression.{X}")

    if disqualify_evidence:
        print(f"\n{W}CASE D evidence — unverifiable DISQUALIFY clauses:{X}")
        for e in disqualify_evidence:
            print(f"  {e}")

    print(f"\n{HR}")
    print("NEXT STEPS")
    print(HR)
    print("1. Check Modal function logs for the job above:")
    print("   modal app logs hebrew-video-pipeline")
    print("   Look for: '[stock] no portrait candidates' vs '[stock] Haiku scoring'")
    print("2. If logs show candidates found but filtered: CASE B or D confirmed")
    print("3. Share this output + Modal logs for final classification")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cached", action="store_true",
                        help="Skip video processing, use cached captions")
    parser.add_argument("--force-process", action="store_true",
                        help="Re-process video even if cache exists")
    args = parser.parse_args()

    captions = get_captions(force=args.force_process)
    moments  = run_stock_analysis(captions)
    report(moments)


if __name__ == "__main__":
    main()
