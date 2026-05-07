"""
Standalone Veo B-roll generator.

Usage:
    from broll_veo import generate_broll
    paths = generate_broll(["a calm ocean wave, cinematic"], api_key="AIza...")

Or as a CLI:
    python broll_veo.py "a calm ocean wave, cinematic" --api-key AIza...

Requires: google-genai>=1.9.0, ffmpeg in PATH
"""

from __future__ import annotations

import argparse
import random
import subprocess
import time
import uuid
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
VIDEO_GENERATION_MODEL      = "veo-3.0-generate-001"
VIDEO_GENERATION_MODEL_FAST = "veo-3.0-fast-generate-001"

_MAX_ATTEMPTS    = 5
_BASE_DELAY      = 2.0    # seconds
_CAP_DELAY       = 60.0   # seconds
_JOB_TIMEOUT     = 480    # seconds per job (8-minute hard limit)
_POLL_INTERVAL   = 10     # seconds between polls


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

def _build_client(api_key: str):
    from google import genai
    return genai.Client(api_key=api_key)


def _build_video_cfg(aspect_ratio: str):
    """Return a GenerateVideoConfig, or None if the SDK is too old."""
    try:
        from google.genai import types
        return types.GenerateVideoConfig(aspect_ratio=aspect_ratio, number_of_videos=1)
    except AttributeError:
        return None


def _veo_call(client, prompt: str, video_cfg, model: str = VIDEO_GENERATION_MODEL):
    """Start a Veo generation with exponential back-off + jitter on 429/5xx."""
    for attempt in range(_MAX_ATTEMPTS):
        try:
            print(f"[veo] attempt {attempt+1}/{_MAX_ATTEMPTS}: {prompt[:80]!r}")
            return client.models.generate_videos(
                model=model,
                prompt=prompt,
                **({"config": video_cfg} if video_cfg else {}),
            )
        except Exception as exc:
            msg = str(exc)
            is_quota  = "429" in msg or "RESOURCE_EXHAUSTED" in msg
            is_server = any(k in msg for k in ("503", "502", "500", "unavailable"))
            is_auth   = any(k in msg for k in ("401", "403", "PERMISSION_DENIED", "api_key"))
            is_invalid = "400" in msg or "INVALID_ARGUMENT" in msg or "not found" in msg.lower()

            if is_auth:
                raise RuntimeError(f"Auth error — check your API key: {msg}") from exc
            if is_invalid:
                raise RuntimeError(f"Invalid request: {msg}") from exc
            if (is_quota or is_server) and attempt < _MAX_ATTEMPTS - 1:
                delay = min(_BASE_DELAY * 2**attempt + random.uniform(0, 1), _CAP_DELAY)
                kind  = "quota (429)" if is_quota else "server error"
                print(f"[veo] {kind} on attempt {attempt+1}, retrying in {delay:.1f}s — {msg[:120]}")
                time.sleep(delay)
                continue
            raise

    raise RuntimeError("_veo_call: exhausted all retry attempts")


def _veo_poll(client, operation):
    """Poll operation every 10 s until done or _JOB_TIMEOUT seconds."""
    deadline = time.time() + _JOB_TIMEOUT
    polls    = 0
    while not operation.done:
        if time.time() > deadline:
            raise TimeoutError(f"Veo job timed out after {_JOB_TIMEOUT}s ({polls} polls)")
        time.sleep(_POLL_INTERVAL)
        operation = client.operations.get(operation)
        polls += 1
        print(f"[veo] polling... {polls} poll(s), {int(time.time() - (deadline - _JOB_TIMEOUT))}s elapsed")
    return operation


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_broll(
    prompts: list[str],
    *,
    api_key: str,
    output_dir: str | Path = "./output",
    aspect_ratio: str = "9:16",
    model: str = VIDEO_GENERATION_MODEL,
    clip_duration: int = 3,
) -> list[Path]:
    """Generate one B-roll video per prompt and download to output_dir.

    Videos are generated sequentially (one at a time) to stay under Veo's
    per-minute rate limit.  Each call carries full exponential back-off and
    an 8-minute hard timeout.

    Returns a list of Paths (same order as prompts).  If a generation fails
    its slot is None in the returned list.
    """
    client    = _build_client(api_key)
    video_cfg = _build_video_cfg(aspect_ratio)
    out_dir   = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    results: list[Optional[Path]] = []

    for i, prompt in enumerate(prompts):
        print(f"\n[veo] === Job {i+1}/{len(prompts)} ===")
        try:
            operation = _veo_call(client, prompt, video_cfg, model=model)
            operation = _veo_poll(client, operation)

            if not getattr(operation.response, "generated_videos", None):
                raise RuntimeError("Veo response contained no generated_videos.")

            gen_video = operation.response.generated_videos[0]
            uri       = getattr(gen_video.video, "uri", "?")
            print(f"[veo] generation complete, uri={uri}")

            key      = uuid.uuid4().hex
            raw_path = out_dir / f"broll_raw_{key}.mp4"
            out_path = out_dir / f"broll_{key}.mp4"

            # Download
            try:
                client.files.download(file=gen_video.video, path=str(raw_path))
            except TypeError:
                raw_path.write_bytes(client.files.download(file=gen_video.video))

            # Trim to clip_duration, H.264, no audio, faststart for browser
            subprocess.run(
                ["ffmpeg", "-y", "-i", str(raw_path),
                 "-t", str(clip_duration),
                 "-c:v", "libx264", "-crf", "23", "-preset", "fast",
                 "-pix_fmt", "yuv420p", "-an",
                 "-movflags", "+faststart", str(out_path)],
                check=True, capture_output=True,
            )
            raw_path.unlink(missing_ok=True)
            print(f"[veo] saved → {out_path} ({out_path.stat().st_size // 1024} KB)")
            results.append(out_path)

        except Exception as exc:
            print(f"[veo] ERROR on job {i+1}: {exc}")
            results.append(None)

    return results


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate B-roll videos with Veo")
    parser.add_argument("prompts", nargs="+", help="One or more video prompts")
    parser.add_argument("--api-key", required=True, help="Google AI / Gemini API key")
    parser.add_argument("--output-dir", default="./output", help="Output directory")
    parser.add_argument("--aspect-ratio", default="9:16", choices=["16:9", "9:16", "1:1"])
    parser.add_argument("--fast", action="store_true", help="Use veo-3.1-fast-generate-001")
    args = parser.parse_args()

    model = VIDEO_GENERATION_MODEL_FAST if args.fast else VIDEO_GENERATION_MODEL
    paths = generate_broll(
        args.prompts,
        api_key=args.api_key,
        output_dir=args.output_dir,
        aspect_ratio=args.aspect_ratio,
        model=model,
    )
    print("\n=== Results ===")
    for p, prompt in zip(paths, args.prompts):
        status = str(p) if p else "FAILED"
        print(f"  {prompt[:60]!r} → {status}")
