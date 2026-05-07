"""
Smoke test: generate ONE 4-second clip with a trivial prompt.

Usage:
    GEMINI_API_KEY=AIza... python test_broll_smoke.py

The test is intentionally minimal — it verifies the full generate→poll→
download→trim path completes without error and produces a non-empty MP4.
"""

import os
import sys
from pathlib import Path

from broll_veo import generate_broll


def run_smoke_test():
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        print("ERROR: set GEMINI_API_KEY environment variable before running this test.")
        sys.exit(1)

    prompt = "a calm ocean wave, cinematic, slow motion, photorealistic"
    output_dir = Path("./test_broll_output")

    print("=" * 60)
    print("Veo B-roll smoke test")
    print(f"  prompt : {prompt!r}")
    print(f"  output : {output_dir}")
    print("=" * 60)

    paths = generate_broll(
        [prompt],
        api_key=api_key,
        output_dir=output_dir,
        aspect_ratio="16:9",
        clip_duration=4,
    )

    assert len(paths) == 1, f"Expected 1 result, got {len(paths)}"
    result = paths[0]

    print("\n" + "=" * 60)
    if result is None:
        print("FAIL: generation returned None (see errors above)")
        sys.exit(1)

    if not result.exists():
        print(f"FAIL: output file does not exist: {result}")
        sys.exit(1)

    size_bytes = result.stat().st_size
    if size_bytes < 1024:
        print(f"FAIL: output file suspiciously small ({size_bytes} bytes): {result}")
        sys.exit(1)

    # Confirm it's a valid MP4 (starts with ftyp box or moov atom preamble)
    header = result.read_bytes()[:12]
    is_mp4 = b"ftyp" in header or b"moov" in header or header[4:8] in (b"ftyp", b"free")
    if not is_mp4:
        print(f"FAIL: output does not look like MP4. Header bytes: {header.hex()}")
        sys.exit(1)

    print(f"PASS: {result} — {size_bytes // 1024} KB, looks like valid MP4")
    print("=" * 60)


if __name__ == "__main__":
    run_smoke_test()
