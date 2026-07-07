#!/usr/bin/env python3
"""
Hebrew Video Pipeline
=====================
Takes an unedited Hebrew-language video and outputs a clean edited video with
Hebrew captions burned in, minimal quality loss, and silence gaps removed.

Pipeline:
  1. Extract audio (ffmpeg)
  2. Enhance audio (DeepFilterNet / ElevenLabs Voice Isolator / manual Adobe Podcast / skip)
  3. Transcribe with word-level timestamps (ivrit-ai Whisper via faster-whisper)
  4. Compute "keep segments" from word timestamps (smart silence cutting)
  5. Generate ASS subtitle file with Hebrew-friendly styling
  6. Single ffmpeg pass: trim + concat + burn captions -> one re-encode

Requirements:
  pip install faster-whisper requests
  ffmpeg + ffprobe in PATH
  Optional: an NVIDIA GPU. Falls back to CPU automatically.
  Optional: Rubik or Heebo font installed (or pass --font to pick another)

Usage:
  # Default: DeepFilterNet (free, local, no API key needed)
  python hebrew_video_pipeline.py input.mp4

  # Fully automated via ElevenLabs
  export ELEVENLABS_API_KEY=sk_xxx
  python hebrew_video_pipeline.py input.mp4 --enhance elevenlabs

  # Skip enhancement (if audio is already clean)
  python hebrew_video_pipeline.py input.mp4 --enhance skip
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

_RLM                = chr(0x200F)  # Unicode Right-to-Left Mark — invisible, strong RTL
_RTL_LEAD_PUNCT_RE  = re.compile(r'^([?!.،؟]+)\s+(.+)$', re.DOTALL)
_RTL_TRAIL_PUNCT_RE = re.compile(r'([?!]+)(\\N|$)')

def _fix_rtl_punct(line: str) -> str:
    """Move leading sentence-final punctuation to the end of an RTL caption line."""
    m = _RTL_LEAD_PUNCT_RE.match(line.strip())
    return (m.group(2) + m.group(1)) if m else line

def _rtl_ass_text(text: str) -> str:
    """Wrap caption text with Unicode RTL markers for correct libass rendering."""
    marked = _RTL_TRAIL_PUNCT_RE.sub(lambda m: m.group(1) + _RLM + (m.group(2) or ''), text)
    return _RLM + marked

# ------------------------- Defaults -------------------------
DEFAULT_MIN_SILENCE = 0.5       # seconds of gap before we consider cutting
DEFAULT_PADDING = 0.20          # seconds kept before/after speech (breathing room)
DEFAULT_MAX_CHARS_PER_LINE = 38
DEFAULT_MAX_LINES_PER_CUE = 1
DEFAULT_FONT = "Rubik"          # "Heebo", "Assistant", "Noto Sans Hebrew" also great
DEFAULT_FONT_SIZE = 48          # relative to PlayResX/Y = video dimensions
DEFAULT_CRF = 18                # 17-19 is visually lossless for H.264
DEFAULT_WHISPER_MODEL = "ivrit-ai/whisper-large-v3-ct2"
# Biases decoding toward well-formed, correctly spelled standard Hebrew.
WHISPER_INITIAL_PROMPT = "תמלול בעברית תקנית, במשפטים מלאים ובכתיב מלא ותקין."
# ------------------------------------------------------------

# Prefer ffmpeg-full (has libass for ASS subtitle burning) if installed via Homebrew.
_FFMPEG_FULL = Path("/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg")
FFMPEG  = str(_FFMPEG_FULL)                    if _FFMPEG_FULL.exists() else "ffmpeg"
FFPROBE = str(_FFMPEG_FULL.with_name("ffprobe")) if _FFMPEG_FULL.exists() else "ffprobe"


def run(cmd, **kwargs):
    """Run a subprocess command, echoing it first, raising on failure."""
    print("  $", " ".join(str(c) for c in cmd))
    return subprocess.run(cmd, check=True, **kwargs)


@dataclass
class Word:
    start: float
    end: float
    text: str


@dataclass
class KeepSegment:
    start: float
    end: float
    words: List[Word] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return self.end - self.start


# ============ Step 1: extract audio ============
def extract_audio(video: Path, out_wav: Path) -> None:
    print("\n[1/6] Extracting audio...")
    run([
        FFMPEG, "-y", "-i", str(video),
        "-vn", "-acodec", "pcm_s16le", "-ar", "48000", "-ac", "1",
        str(out_wav),
    ])


# ============ Step 2: enhance audio ============
def enhance_elevenlabs(in_wav: Path, out_wav: Path, api_key: str) -> None:
    print("\n[2/6] Enhancing audio (ElevenLabs Voice Isolator)...")
    import requests  # lazy import so users without it can still run other modes
    with open(in_wav, "rb") as f:
        resp = requests.post(
            "https://api.elevenlabs.io/v1/audio-isolation",
            headers={"xi-api-key": api_key},
            files={"audio": (in_wav.name, f, "audio/wav")},
            timeout=600,
        )
    resp.raise_for_status()
    out_wav.write_bytes(resp.content)
    print(f"  Saved cleaned audio to {out_wav.name}")


def enhance_manual(in_wav: Path, out_wav: Path) -> None:
    print("\n[2/6] Manual enhancement step")
    print( "  1. Upload this file to Adobe Podcast Enhance:")
    print( "       https://podcast.adobe.com/enhance")
    print(f"       File: {in_wav.resolve()}")
    print( "  2. Download the enhanced audio")
    print(f"  3. Save it as: {out_wav.resolve()}")
    input("  Press Enter when the enhanced file is in place...")
    if not out_wav.exists():
        raise FileNotFoundError(f"Expected enhanced file at {out_wav}")


def enhance_deepfilter(in_wav: Path, out_wav: Path) -> None:
    print("\n[2/6] Enhancing audio (DeepFilterNet noise reduction)...")
    from df.enhance import enhance, init_df, load_audio, save_audio
    model, df_state, _ = init_df()
    audio, _ = load_audio(str(in_wav), sr=df_state.sr())
    enhanced = enhance(model, df_state, audio)
    save_audio(str(out_wav), enhanced, df_state.sr())
    print(f"  Saved cleaned audio to {out_wav.name}")


def enhance_skip(in_wav: Path, out_wav: Path) -> None:
    print("\n[2/6] Skipping enhancement (copying raw audio through)")
    shutil.copy(in_wav, out_wav)


# ============ Step 3: transcribe ============
def transcribe(wav: Path, model_name: str) -> List[Word]:
    print(f"\n[3/6] Transcribing with {model_name}...")
    from faster_whisper import WhisperModel

    try:
        model = WhisperModel(model_name, device="cuda", compute_type="float16")
        print("  Using CUDA / float16")
    except Exception as e:
        print(f"  CUDA unavailable ({e.__class__.__name__}); using CPU / int8")
        model = WhisperModel(model_name, device="cpu", compute_type="int8")

    segments, info = model.transcribe(
        str(wav),
        language="he",
        word_timestamps=True,
        vad_filter=True,          # built-in silero-VAD pass cleans up further
        beam_size=5,
        condition_on_previous_text=True,
        initial_prompt=WHISPER_INITIAL_PROMPT,
    )

    words: List[Word] = []
    for seg in segments:
        for w in (seg.words or []):
            # faster-whisper sometimes includes leading spaces - trim for display
            txt = w.word.strip()
            if txt:
                words.append(Word(start=w.start, end=w.end, text=txt))

    print(f"  Detected {len(words)} words, ~{info.duration:.1f}s audio")
    return words


# ============ Step 4: smart silence cutting ============
def compute_keep_segments(
    words: List[Word],
    min_silence: float,
    padding: float,
    total_duration: float,
) -> List[KeepSegment]:
    print(f"\n[4/6] Computing keep segments "
          f"(min_silence={min_silence}s, padding={padding}s)...")

    if not words:
        print("  No speech detected -> keeping entire video")
        return [KeepSegment(0.0, total_duration)]

    segments: List[KeepSegment] = []
    current = KeepSegment(start=max(0.0, words[0].start - padding), end=0.0)
    current.words.append(words[0])

    for prev_w, next_w in zip(words, words[1:]):
        gap = next_w.start - prev_w.end
        if gap >= min_silence:
            # Close the current segment
            current.end = prev_w.end + padding
            segments.append(current)
            # Start a fresh segment, never overlapping the one we just closed
            new_start = max(current.end, next_w.start - padding)
            current = KeepSegment(start=new_start, end=0.0)
        current.words.append(next_w)

    current.end = min(total_duration, words[-1].end + padding)
    segments.append(current)

    kept = sum(s.duration for s in segments)
    print(f"  {len(segments)} segments | keeping {kept:.1f}s / {total_duration:.1f}s "
          f"({100 * kept / total_duration:.0f}%)")
    return segments


# ============ Step 5: generate ASS ============
def seconds_to_ass(t: float) -> str:
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60
    return f"{h:d}:{m:02d}:{s:05.2f}"


def generate_ass(
    segments: List[KeepSegment],
    out_path: Path,
    video_width: int,
    video_height: int,
    font: str,
    font_size: int,
    max_chars: int,
    max_lines: int,
    margin_v: int,
) -> None:
    """
    Build an ASS subtitle file whose timestamps are already remapped to the
    post-cut timeline. We pack words into lines (by character budget) and
    lines into cues (by max_lines), flushing each cue as a Dialogue event.
    """
    print("\n[5/6] Generating ASS subtitles...")

    events = []  # list of (new_start, new_end, text)
    cumulative = 0.0  # total duration of segments we've already placed

    for seg in segments:
        line_buffer: List[Word] = []
        line_chars = 0
        lines_in_cue: List[List[Word]] = []

        def flush_cue():
            if not lines_in_cue:
                return
            first = lines_in_cue[0][0]
            last = lines_in_cue[-1][-1]
            new_start = cumulative + (first.start - seg.start)
            new_end = cumulative + (last.end - seg.start)
            # Strip leading/trailing punctuation from each word before joining.
            # Commas and hyphens from Whisper are weak bidi chars that render in
            # wrong position; Hebrew letters are strong-RTL and need no override.
            def _clean(t: str) -> str:
                return t.strip("،,.-–—!?;:")

            text = r"\N".join(
                _fix_rtl_punct(" ".join(_clean(w.text) for w in ln).strip())
                for ln in lines_in_cue
            )
            events.append((new_start, new_end, text))
            lines_in_cue.clear()

        for w in seg.words:
            # +1 for the space before the word (unless first word on the line)
            projected = line_chars + len(w.text) + (1 if line_buffer else 0)
            if projected > max_chars and line_buffer:
                lines_in_cue.append(line_buffer)
                line_buffer = []
                line_chars = 0
                if len(lines_in_cue) >= max_lines:
                    flush_cue()
            line_buffer.append(w)
            line_chars += len(w.text) + (1 if len(line_buffer) > 1 else 0)

        if line_buffer:
            lines_in_cue.append(line_buffer)
        flush_cue()

        cumulative += seg.duration

    # ASS colours: &HAABBGGRR&  (AA=00 means fully opaque)
    # Style: white fill, 3px black outline, no shadow, bottom-centre aligned.
    margin_h = max(25, video_width // 14)
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {video_width}\n"
        f"PlayResY: {video_height}\n"
        "WrapStyle: 2\n"
        "ScaledBorderAndShadow: yes\n"
        "YCbCr Matrix: TV.709\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{font},{font_size},"
        "&H00FFFFFF,&H000000FF,&H00000000,&HFF000000,"
        f"-1,0,0,0,100,100,0,0,1,3,0,2,"
        f"{margin_h},{margin_h},{margin_v},1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
        "Effect, Text\n"
    )

    body_lines = []
    for start, end, text in events:
        body_lines.append(
            f"Dialogue: 0,{seconds_to_ass(start)},{seconds_to_ass(end)},"
            f"Default,,0,0,0,,{_rtl_ass_text(text)}\n"
        )

    out_path.write_text(header + "".join(body_lines), encoding="utf-8")
    print(f"  Wrote {len(events)} cues to {out_path.name}")


# ============ Step 6: final render ============
def probe_video(video: Path):
    result = subprocess.run(
        [
            FFPROBE, "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-show_entries", "format=duration",
            "-of", "json", str(video),
        ],
        capture_output=True, text=True, check=True,
    )
    data = json.loads(result.stdout)
    stream = data["streams"][0]
    duration = float(data["format"]["duration"])
    return int(stream["width"]), int(stream["height"]), duration


def final_render(
    video: Path,
    clean_wav: Path,
    segments: List[KeepSegment],
    ass: Path,
    out: Path,
    crf: int,
) -> None:
    """
    One ffmpeg pass:
      - trim each segment from the original video
      - trim matching segments from the cleaned audio
      - concatenate them in order
      - burn the (already-remapped) ASS subtitles
      - encode once at CRF 18 (visually lossless)
    """
    print(f"\n[6/6] Final render (cut + burn captions, CRF {crf})...")

    v_parts, a_parts = [], []
    for i, s in enumerate(segments):
        v_parts.append(
            f"[0:v]trim=start={s.start:.3f}:end={s.end:.3f},"
            f"setpts=PTS-STARTPTS[v{i}]"
        )
        a_parts.append(
            f"[1:a]atrim=start={s.start:.3f}:end={s.end:.3f},"
            f"asetpts=PTS-STARTPTS[a{i}]"
        )
    concat_in = "".join(f"[v{i}][a{i}]" for i in range(len(segments)))
    concat = f"{concat_in}concat=n={len(segments)}:v=1:a=1[vc][ac]"

    # Escape path for ffmpeg's subtitles filter (colons need backslash-escaping).
    ass_escaped = str(ass.resolve()).replace("\\", "/").replace(":", r"\:")
    burn = f"[vc]subtitles='{ass_escaped}'[vout]"

    filter_complex = ";".join(v_parts + a_parts + [concat, burn])

    run([
        FFMPEG, "-y",
        "-i", str(video),
        "-i", str(clean_wav),
        "-filter_complex", filter_complex,
        "-map", "[vout]", "-map", "[ac]",
        "-c:v", "libx264", "-crf", str(crf), "-preset", "slow",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        str(out),
    ])


# ============ Main ============
def main():
    ap = argparse.ArgumentParser(
        description="Hebrew video pipeline: clean, cut silences, burn captions."
    )
    ap.add_argument("input", type=Path, help="Input video file")
    ap.add_argument("-o", "--output", type=Path, default=None,
                    help="Output path (default: <input>_edited.mp4)")
    ap.add_argument("--enhance", choices=["deepfilter", "elevenlabs", "manual", "skip"],
                    default="deepfilter",
                    help="Audio enhancement method (default: deepfilter)")
    ap.add_argument("--api-key", default=os.getenv("ELEVENLABS_API_KEY"),
                    help="ElevenLabs API key (or set ELEVENLABS_API_KEY)")
    ap.add_argument("--model", default=DEFAULT_WHISPER_MODEL,
                    help="Whisper model name or path")
    ap.add_argument("--min-silence", type=float, default=DEFAULT_MIN_SILENCE,
                    help="Minimum silence gap (seconds) to cut")
    ap.add_argument("--padding", type=float, default=DEFAULT_PADDING,
                    help="Padding around speech (seconds)")
    ap.add_argument("--font", default=DEFAULT_FONT)
    ap.add_argument("--font-size", type=int, default=DEFAULT_FONT_SIZE)
    ap.add_argument("--max-chars", type=int, default=None,
                    help="Max chars per caption line (default: auto from width/font-size)")
    ap.add_argument("--max-lines", type=int, default=DEFAULT_MAX_LINES_PER_CUE)
    ap.add_argument("--margin-v", type=int, default=None,
                    help="Bottom margin in pixels (default: video_height // 4)")
    ap.add_argument("--crf", type=int, default=DEFAULT_CRF,
                    help="x264 CRF (lower = better quality, 17-19 is great)")
    ap.add_argument("--keep-tmp", action="store_true",
                    help="Keep the temp working directory for inspection")
    args = ap.parse_args()

    if not args.input.exists():
        sys.exit(f"Input not found: {args.input}")
    if args.output is None:
        args.output = args.input.with_name(args.input.stem + "_edited.mp4")
    if args.enhance == "elevenlabs" and not args.api_key:
        sys.exit("--enhance elevenlabs requires --api-key or ELEVENLABS_API_KEY")

    tmpdir = Path(tempfile.mkdtemp(prefix="hebpipe_"))
    print(f"Working directory: {tmpdir}")

    try:
        raw_wav = tmpdir / "raw.wav"
        clean_wav = tmpdir / "clean.wav"
        ass_file = tmpdir / "captions.ass"

        width, height, duration = probe_video(args.input)
        print(f"Video: {width}x{height}, {duration:.1f}s")

        if args.max_chars is None:
            _mh = max(25, width // 14)          # same formula as generate_ass margins
            args.max_chars = max(8, int((width - 2 * _mh) / (args.font_size * 0.48)))
        if args.margin_v is None:
            args.margin_v = height // 4

        extract_audio(args.input, raw_wav)

        if args.enhance == "deepfilter":
            enhance_deepfilter(raw_wav, clean_wav)
        elif args.enhance == "elevenlabs":
            enhance_elevenlabs(raw_wav, clean_wav, args.api_key)
        elif args.enhance == "manual":
            enhance_manual(raw_wav, clean_wav)
        else:
            enhance_skip(raw_wav, clean_wav)

        words = transcribe(clean_wav, args.model)
        segments = compute_keep_segments(words, args.min_silence,
                                         args.padding, duration)
        generate_ass(segments, ass_file, width, height,
                     args.font, args.font_size,
                     args.max_chars, args.max_lines, args.margin_v)
        final_render(args.input, clean_wav, segments,
                     ass_file, args.output, args.crf)

        print(f"\n✔ Done -> {args.output}")
    finally:
        if args.keep_tmp:
            print(f"Temp files kept at: {tmpdir}")
        else:
            shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()
