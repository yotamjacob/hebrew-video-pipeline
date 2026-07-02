"""
Core video pipeline Modal functions: process_video (GPU transcribe/cut),
burn_captions_fn and burn_hook_fn (ffmpeg + libass burn workers).
"""

from pipeline_core import (
    app, burn_image, model_volume, MODEL_DIR,
    WHISPER_MODEL, tmp_vol, TMP_DIR, _fix_rtl_punct,
    _rtl_ass_text, _censor_caption_text, _SAFE_KEY_RE,
    jobs_store, JOB_RETENTION_DAYS, SCRATCH_RETENTION_HOURS,
)

# ---------------------------------------------------------------------------
# Shared pure helpers — used by process_video and rerender_cuts_fn
# ---------------------------------------------------------------------------
import json
import subprocess
from dataclasses import dataclass, field
from typing import List

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
    def duration(self):
        return self.end - self.start

def run(cmd):
    result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    if result.returncode != 0:
        stderr_tail = result.stderr.decode("utf-8", errors="replace")[-3000:]
        raise RuntimeError(f"ffmpeg exited {result.returncode}:\n{stderr_tail}")

def probe_video(path):
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,side_data_list",
         "-show_entries", "stream_tags=rotate",
         "-show_entries", "format=duration",
         "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    )
    d = json.loads(r.stdout)
    s = d["streams"][0]
    # Detect rotation from stream tags (older) or side_data_list (newer ffprobe)
    rotation = 0
    tag_rot = s.get("tags", {}).get("rotate")
    if tag_rot:
        rotation = int(tag_rot)
    else:
        for sd in s.get("side_data_list", []):
            if "rotation" in sd:
                rotation = (-int(sd["rotation"])) % 360
                break
    w, h = int(s["width"]), int(s["height"])
    # Swap to display dimensions when rotated 90/270
    if rotation in (90, 270):
        w, h = h, w
    return w, h, float(d["format"]["duration"]), rotation

def compute_keep_segments(whisper_segs, total_dur, min_sil, pad):
    """Cut only between Whisper segments, never within one.

    whisper_segs: List[List[Word]] — each inner list is one Whisper segment.
    Cuts happen only when the gap between two consecutive segments >= min_sil.
    Words within a segment are always kept together, preventing mid-word cuts
    caused by Whisper tokenization or VAD boundary artefacts.
    """
    if not whisper_segs:
        return [KeepSegment(0.0, total_dur)]
    out = []
    cur = None
    prev_end = None  # raw (unpadded) end timestamp of last added Whisper segment

    # Whisper (especially CUDA) consistently underestimates segment end
    # timestamps for Hebrew trailing vowels (ו, י, ה) by 150–300ms.
    # Add a fixed trailing buffer on top of pad to compensate.
    TRAIL = 0.25

    for seg_words, seg_end in whisper_segs:
        seg_start = seg_words[0].start

        if cur is None:
            cur = KeepSegment(start=max(0.0, seg_start - pad), end=0.0)
        else:
            gap = seg_start - prev_end
            if gap >= min_sil:
                cur.end = min(total_dur, prev_end + pad + TRAIL)
                out.append(cur)
                cur = KeepSegment(start=max(cur.end, seg_start - pad), end=0.0)

        cur.words.extend(seg_words)
        prev_end = seg_end  # segment-level end, not last word's end

    cur.end = min(total_dur, prev_end + pad + TRAIL)
    out.append(cur)
    return out

def seconds_to_ass(t):
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"

def generate_ass(segs, path, w, h, font_size=48, min_sil=0.3):
    margin_h = max(25, w // 14)
    margin_v = h // 4
    max_chars = max(8, int((w - 2 * margin_h) / (font_size * 0.60)))

    def _clean(t):
        return t.strip("،,.-–—;:")

    events = []
    cumulative = 0.0
    for seg in segs:
        line_buf, line_chars = [], 0
        lines_in_cue = []

        def flush(cumulative=cumulative, seg=seg):
            if not lines_in_cue:
                return
            first, last = lines_in_cue[0][0], lines_in_cue[-1][-1]
            ns = cumulative + (first.start - seg.start)
            ne = cumulative + (last.end - seg.start)
            text = r"\N".join(
                _fix_rtl_punct(" ".join(_clean(ww.text) for ww in ln).strip())
                for ln in lines_in_cue
            )
            events.append((ns, ne, text))
            lines_in_cue.clear()

        prev_end = None
        for ww in seg.words:
            # Split on silence gap — necessary when cut_silences=False puts all words
            # in one segment; also catches any sub-min_sil gaps left in segments.
            if prev_end is not None and (ww.start - prev_end) >= min_sil and line_buf:
                lines_in_cue.append(line_buf)
                line_buf, line_chars = [], 0
                flush()
            prev_end = ww.end

            proj = line_chars + len(ww.text) + (1 if line_buf else 0)
            if proj > max_chars and line_buf:
                lines_in_cue.append(line_buf)
                line_buf, line_chars = [], 0
                flush()
            line_buf.append(ww)
            line_chars += len(ww.text) + (1 if len(line_buf) > 1 else 0)
        if line_buf:
            lines_in_cue.append(line_buf)
        flush()
        cumulative += seg.duration

    header = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {w}\nPlayResY: {h}\n"
        "WrapStyle: 2\nScaledBorderAndShadow: yes\nYCbCr Matrix: TV.709\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,Rubik,{font_size},"
        "&H00FFFFFF,&H000000FF,&H00000000,&HFF000000,"
        f"-1,0,0,0,100,100,0,0,1,3,0,2,"
        f"{margin_h},{margin_h},{margin_v},1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    lines = [
        f"Dialogue: 0,{seconds_to_ass(s)},{seconds_to_ass(e)},"
        f"Default,,0,0,0,,{_rtl_ass_text(t)}\n"
        for s, e, t in events
    ]
    path.write_text(header + "".join(lines), encoding="utf-8")
    return events

def _rotation_vf(rotation):
    """Return an ffmpeg vf filter string (with trailing comma) to bake in rotation metadata.
    rotate=90  → portrait iPhone selfie → needs 90° CCW (transpose=cclock)
    rotate=270 → needs 90° CW (transpose=clock)
    rotate=180 → flip both axes
    """
    r = rotation % 360
    if r == 90:  return "transpose=cclock,"
    if r == 270: return "transpose=clock,"
    if r == 180: return "hflip,vflip,"
    return ""

def render(video, audio, segs, ass_file, out, crf=18, rotation=0):
    v_parts, a_parts = [], []
    for i, s in enumerate(segs):
        v_parts.append(f"[0:v]trim=start={s.start:.3f}:end={s.end:.3f},setpts=PTS-STARTPTS[v{i}]")
        a_parts.append(f"[1:a]atrim=start={s.start:.3f}:end={s.end:.3f},asetpts=PTS-STARTPTS[a{i}]")
    cin = "".join(f"[v{i}][a{i}]" for i in range(len(segs)))
    concat = f"{cin}concat=n={len(segs)}:v=1:a=1[vc][ac]"
    rot_vf = _rotation_vf(rotation)
    if ass_file:
        esc = str(ass_file).replace(":", r"\:")
        last = f"[vc]{rot_vf}subtitles='{esc}'[vout]"
    else:
        last = f"[vc]{rot_vf}copy[vout]"
    fc = ";".join(v_parts + a_parts + [concat, last])
    # -noautorotate: disable implicit rotation so our explicit transpose is the sole source of truth
    input_args = ["-noautorotate"] if rotation else []
    meta_args  = ["-metadata:s:v:0", "rotate=0"] if rotation else []
    run(["ffmpeg", "-y"] + input_args + ["-i", str(video), "-i", str(audio),
         "-filter_complex", fc,
         "-map", "[vout]", "-map", "[ac]",
         "-c:v", "libx264", "-crf", str(crf), "-preset", "veryfast",
         "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
         "-movflags", "+faststart"] + meta_args + [str(out)])


def compute_cuts(segs):
    """Describe gaps removed between consecutive keep segments (original timeline).

    ``index`` is the boundary position (between segs[i] and segs[i+1]) so it stays
    stable for merge_restored even when tiny gaps are filtered from the list.
    """
    cuts = []
    for i in range(len(segs) - 1):
        gap_start, gap_end = segs[i].end, segs[i + 1].start
        if gap_end - gap_start < 0.05:
            continue
        cuts.append({
            "index": i,
            "start": round(gap_start, 3),
            "end": round(gap_end, 3),
            "duration": round(gap_end - gap_start, 2),
            "before": segs[i].words[-1].text if segs[i].words else "",
            "after": segs[i + 1].words[0].text if segs[i + 1].words else "",
        })
    return cuts


def merge_restored(segs, restored):
    """Merge keep segments across restored gap boundaries so the silence stays in."""
    for i in sorted({int(r) for r in restored}, reverse=True):
        if 0 <= i < len(segs) - 1:
            a, b = segs[i], segs[i + 1]
            segs[i] = KeepSegment(a.start, b.end, a.words + b.words)
            del segs[i + 1]
    return segs


# ---------------------------------------------------------------------------
# Core processing — GPU worker
# ---------------------------------------------------------------------------
@app.function(
    gpu="T4",
    timeout=900,
    volumes={MODEL_DIR: model_volume, TMP_DIR: tmp_vol},
    memory=4096,
)
def process_video(
    upload_key: str = None,
    video_bytes: bytes = None,
    filename: str = "video.mp4",
    cut_silences: bool = True,
    burn_captions: bool = True,
    min_silence: float = 0.3,
    padding: float = 0.2,
    enhance_audio: bool = True,
    transcribe_for_broll: bool = False,
) -> dict:
    # Warmup call — just starts the container, no real work
    if filename == "__warmup__":
        return {"captions": [], "video_key": ""}

    import json
    import shutil
    import tempfile
    from pathlib import Path

    # Validate input before entering the tempdir
    if upload_key is not None:
        tmp_vol.reload()
        chunk_paths = sorted(Path(TMP_DIR).glob(f"{upload_key}_chunk_*"))
        if not chunk_paths and not (Path(TMP_DIR) / f"{upload_key}_src.mp4").exists():
            raise ValueError(f"No upload chunks found for key {upload_key}")
    elif not video_bytes:
        raise ValueError("No video data provided")

    def extract_audio(video, out_wav):
        run(["ffmpeg", "-y", "-i", str(video),
             "-vn", "-af", "loudnorm", "-acodec", "pcm_s16le", "-ar", "48000", "-ac", "1",
             str(out_wav)])

    def enhance_deepfilter(in_wav, out_wav):
        import os, sys, types
        import torch
        import soundfile as sf
        os.environ["XDG_CACHE_HOME"] = MODEL_DIR

        # DeepFilterNet 0.5.x imports torchaudio APIs removed in torchaudio 2.x.
        # Patch all missing symbols before importing df.enhance, then bypass
        # load_audio/save_audio entirely (they use torchaudio.load which requires
        # TorchCodec in 2.5+). We do I/O with soundfile and call enhance() directly.
        import torchaudio

        class _AudioMetaData:
            def __init__(self, sample_rate, num_frames, num_channels,
                         bits_per_sample, encoding):
                self.sample_rate = sample_rate
                self.num_frames = num_frames
                self.num_channels = num_channels
                self.bits_per_sample = bits_per_sample
                self.encoding = encoding

        if not hasattr(torchaudio, "backend"):
            _common = types.ModuleType("torchaudio.backend.common")
            _common.AudioMetaData = _AudioMetaData
            _backend = types.ModuleType("torchaudio.backend")
            _backend.common = _common
            sys.modules["torchaudio.backend"] = _backend
            sys.modules["torchaudio.backend.common"] = _common
            torchaudio.backend = _backend
        torchaudio.AudioMetaData = _AudioMetaData
        torchaudio.info = lambda path, *a, **kw: (
            lambda i: _AudioMetaData(i.samplerate, i.frames, i.channels, 16, "PCM_S")
        )(sf.info(str(path)))
        torchaudio.load = lambda path, *a, **kw: (
            lambda d, sr: (torch.from_numpy(d.T), sr)
        )(*sf.read(str(path), dtype="float32", always_2d=True))
        torchaudio.save = lambda path, src, sr, *a, **kw: sf.write(
            str(path), src.numpy().T, sr
        )

        from df.enhance import enhance, init_df
        model, df_state, _ = init_df()

        # Load with soundfile; audio is already 48 kHz mono from extract_audio
        audio_np, _ = sf.read(str(in_wav), dtype="float32", always_2d=True)
        audio_t = torch.from_numpy(audio_np.T)       # [channels, frames]
        if audio_t.shape[0] > 1:
            audio_t = audio_t.mean(0, keepdim=True)  # force mono

        enhanced = enhance(model, df_state, audio_t)

        # DeepFilterNet attenuates the signal alongside the noise; restore
        # peak level to match the original so the output isn't too quiet.
        orig_peak = audio_t.abs().max().item()
        enh_peak  = enhanced.abs().max().item()
        if enh_peak > 1e-6:
            target = min(max(orig_peak, 0.25), 0.95)  # at least -12 dBFS, headroom at -0.4 dBFS
            enhanced = enhanced * (target / enh_peak)

        sf.write(str(out_wav), enhanced.numpy().T, df_state.sr())
        model_volume.commit()

    def transcribe(wav):
        import os
        os.environ["HF_HOME"] = MODEL_DIR
        from faster_whisper import WhisperModel

        def _segments(segs):
            # Return List[(List[Word], seg_end)] — one tuple per Whisper segment.
            # seg_end is Whisper's segment-level end timestamp, which is more
            # generous than the last word's end — it captures trailing vowels
            # (e.g. the "oooo" of ו) that fall below word-detection threshold.
            result = []
            for seg in segs:
                seg_words = [Word(w.start, w.end, w.word.strip())
                             for w in (seg.words or []) if w.word.strip()]
                if seg_words:
                    result.append((seg_words, seg.end))
            return result

        # Less aggressive VAD: lower threshold + longer speech padding so weak
        # Hebrew consonants (ע, ח, ה at word ends) aren't mistaken for silence.
        vad_params = {
            "threshold": 0.35,           # default 0.5 — lower catches quieter phonemes
            "min_silence_duration_ms": 600,  # default 2000 — allow shorter pauses within speech
            "speech_pad_ms": 600,            # default 400 — more buffer around speech boundaries
        }
        try:
            m = WhisperModel(WHISPER_MODEL, device="cuda", compute_type="float16",
                             download_root=MODEL_DIR)
            segs, _ = m.transcribe(
                str(wav), language="he", word_timestamps=True,
                vad_filter=True, vad_parameters=vad_params,
                beam_size=5, condition_on_previous_text=True,
            )
            whisper_segs = _segments(segs)
        except Exception:
            m = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8",
                             download_root=MODEL_DIR)
            segs, _ = m.transcribe(
                str(wav), language="he", word_timestamps=True,
                vad_filter=True, vad_parameters=vad_params,
                beam_size=5, condition_on_previous_text=True,
            )
            whisper_segs = _segments(segs)
        model_volume.commit()
        return whisper_segs

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        src = tmp / filename
        if upload_key is not None:
            src_cache = Path(TMP_DIR) / f"{upload_key}_src.mp4"
            if chunk_paths:
                # First run: assemble chunks, cache assembled video, delete chunks
                with open(src, "wb") as _out:
                    for p in chunk_paths:
                        with open(p, "rb") as _in:
                            shutil.copyfileobj(_in, _out)
                        p.unlink()
                shutil.copy(src, src_cache)
                tmp_vol.commit()
            else:
                # Re-run: use cached assembled video from volume
                shutil.copy(src_cache, src)
        else:
            src.write_bytes(video_bytes)

        width, height, duration, rotation = probe_video(src)
        raw_wav = tmp / "raw.wav"
        clean_wav = tmp / "clean.wav"
        ass_file = tmp / "captions.ass"
        out_file = tmp / ("out_" + filename)

        extract_audio(src, raw_wav)
        if enhance_audio:
            enhance_deepfilter(raw_wav, clean_wav)
        else:
            shutil.copy(raw_wav, clean_wav)

        need_transcription = cut_silences or burn_captions or transcribe_for_broll
        whisper_segs = transcribe(clean_wav) if need_transcription else []
        flat_words   = [w for seg_words, _end in whisper_segs for w in seg_words]

        segs = (
            compute_keep_segments(whisper_segs, duration, min_silence, padding)
            if cut_silences and whisper_segs
            else [KeepSegment(0.0, duration, flat_words)]
        )

        captions_list = []
        if (burn_captions or transcribe_for_broll) and flat_words:
            events = generate_ass(segs, ass_file, width, height, min_sil=min_silence)
            captions_list = [{"start": s, "end": e, "text": _censor_caption_text(t)} for s, e, t in events]

        if cut_silences and whisper_segs:
            render(src, clean_wav, segs, None, out_file, rotation=rotation)
        elif rotation:
            # No silence cut but source has rotation metadata — bake it in so the browser
            # gets correctly-oriented pixels without relying on display matrix hints.
            rot_vf = _rotation_vf(rotation).rstrip(",")
            run(["ffmpeg", "-y", "-noautorotate", "-i", str(src),
                 "-vf", rot_vf, "-c:v", "libx264", "-crf", "18", "-preset", "veryfast",
                 "-pix_fmt", "yuv420p", "-c:a", "copy",
                 "-metadata:s:v:0", "rotate=0", "-movflags", "+faststart", str(out_file)])
        else:
            shutil.copy(src, out_file)

        cuts = compute_cuts(segs) if (cut_silences and whisper_segs) else []
        if upload_key is not None and cuts:
            # Persist what rerender_cuts_fn needs to restore cuts without re-transcribing
            sidecar = {
                "segments": [{"words": [[w.start, w.end, w.text] for w in seg_words],
                              "seg_end": seg_end}
                             for seg_words, seg_end in whisper_segs],
                "duration": duration, "width": width, "height": height,
                "rotation": rotation, "min_silence": min_silence, "padding": padding,
            }
            (Path(TMP_DIR) / f"{upload_key}_words.json").write_text(json.dumps(sidecar))
            shutil.copy(clean_wav, Path(TMP_DIR) / f"{upload_key}_audio.wav")

        import uuid
        video_key = uuid.uuid4().hex + "_cut.mp4"
        shutil.copy(out_file, Path(TMP_DIR) / video_key)
        tmp_vol.commit()
        return {"captions": captions_list, "video_key": video_key,
                "cuts": cuts if upload_key is not None else []}


# ---------------------------------------------------------------------------
# Caption-burn worker — CPU only, no GPU needed
# ---------------------------------------------------------------------------
@app.function(image=burn_image, timeout=600, volumes={TMP_DIR: tmp_vol})
def burn_captions_fn(video_key: str, captions_json: str, font: str = "Heebo", margin_v_pct: float = 0.08, broll_json: str = "[]", font_size: int = 48, hook_json: str = "", source_name: str = "") -> dict:
    import json, subprocess, tempfile, uuid, shutil
    from pathlib import Path

    tmp_vol.reload()
    captions = json.loads(captions_json)
    broll_items = json.loads(broll_json) if broll_json else []
    hook = json.loads(hook_json) if hook_json else {}

    def run(cmd):
        result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=480)
        if result.returncode != 0:
            stderr_tail = result.stderr.decode("utf-8", errors="replace")[-3000:]
            raise RuntimeError(f"ffmpeg exited {result.returncode}:\n{stderr_tail}")

    def probe_dims(path):
        """Return (width, height, rotation) with w/h already swapped for 90/270°."""
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height,side_data_list",
             "-show_entries", "stream_tags=rotate",
             "-of", "json", str(path)],
            capture_output=True, text=True, check=True,
        )
        s = json.loads(r.stdout)["streams"][0]
        w, h = int(s["width"]), int(s["height"])
        rot = 0
        tag_rot = s.get("tags", {}).get("rotate")
        if tag_rot:
            rot = int(tag_rot)
        else:
            for sd in s.get("side_data_list", []):
                if "rotation" in sd:
                    rot = (-int(sd["rotation"])) % 360
                    break
        if rot in (90, 270):
            w, h = h, w
        return w, h, rot

    def seconds_to_ass(t):
        h = int(t // 3600); m = int((t % 3600) // 60); s = t % 60
        return f"{h}:{m:02d}:{s:05.2f}"

    def hex_to_ass(hex_color: str, alpha_byte: int = 0) -> str:
        h = hex_color.lstrip("#")
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return f"&H{alpha_byte:02X}{b:02X}{g:02X}{r:02X}&"

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        video_in  = tmp / "input.mp4"
        video_out = tmp / "output.mp4"
        ass_path  = tmp / "captions.ass"
        shutil.copy(Path(TMP_DIR) / video_key, video_in)

        width, height, _rotation = probe_dims(video_in)
        # Build rotation vf prefix so output is always baked-portrait regardless of metadata
        if _rotation == 90:
            _rot_vf = "transpose=cclock,"
        elif _rotation == 270:
            _rot_vf = "transpose=clock,"
        elif _rotation == 180:
            _rot_vf = "vflip,hflip,"
        else:
            _rot_vf = ""
        _rot_input  = ["-noautorotate"] if _rotation else []
        _rot_meta   = ["-metadata:s:v:0", "rotate=0"] if _rotation else []
        font_size = max(12, min(200, font_size))
        margin_h  = max(25, width  // 14)
        margin_v  = int(margin_v_pct * height)

        # Re-wrap caption text to match the CSS preview's word-wrap behaviour.
        # The preview uses max-width = (videoWidth - 2*marginH)/videoWidth% with
        # the browser's actual font metrics; we approximate char width at 0.60×fontSize
        # for Hebrew (Heebo/Rubik glyphs average ~60% of em-square). Using 0.50 caused
        # Python to under-wrap, leaving lines too long for libass → libass added unexpected
        # extra wraps, making captions appear taller and "too high" than intended.
        def _rewrap_cap(text: str) -> str:
            words = text.replace(r"\N", " ").split()
            if not words:
                return text
            avail = width - 2 * margin_h
            char_w = font_size * 0.60
            lines: list[str] = []
            cur: list[str] = []
            cur_w = 0.0
            for word in words:
                ww = len(word) * char_w
                gap = char_w if cur else 0.0
                if cur and cur_w + gap + ww > avail:
                    lines.append(" ".join(cur))
                    cur, cur_w = [word], ww
                else:
                    cur.append(word)
                    cur_w += gap + ww
            if cur:
                lines.append(" ".join(cur))
            return r"\N".join(lines) if lines else text

        def _fix_cap_lines(t):
            return r'\N'.join(_fix_rtl_punct(l) for l in t.split(r'\N'))
        captions = [{"start": c["start"], "end": c["end"], "text": _fix_cap_lines(_rewrap_cap(_censor_caption_text(c["text"])))} for c in captions]

        # Build hook style + event if a hook was selected
        hook_style_line = ""
        hook_event_line = ""
        if hook.get("text"):
            h_font         = hook.get("font", "Heebo")
            h_size_pct     = max(50, min(200, int(hook.get("font_size_pct", 100))))
            h_fsize_base   = max(24, int(min(width, height) * 0.075 * h_size_pct / 100))
            h_fsize        = max(24, int(h_fsize_base * 1.30))
            h_primary      = hex_to_ass(hook.get("font_color", "#FFFFFF"), 0)
            h_bg_alpha     = int((1.0 - max(0.0, min(1.0, float(hook.get("bg_opacity", 0.6))))) * 255)
            h_back         = hex_to_ass(hook.get("bg_color", "#000000"), h_bg_alpha)
            h_border_size  = max(0, min(20, int(hook.get("border_size", 0))))
            h_border_color = hook.get("border_color", "#000000")
            h_vpos      = max(0, min(100, int(hook.get("vertical_position", 10))))
            h_pad       = h_fsize_base   # use unbumped size for position, matching preview edgePad
            h_y         = int(h_pad + (height - 2 * h_pad) * (h_vpos / 100))
            h_start     = float(hook.get("start_seconds", 1.0))
            h_dur       = float(hook.get("duration_seconds", 4.0))
            # Strip leading/trailing weak BiDi chars (commas, hyphens) — same fix as captions.
            # These weak chars can disrupt libass's RTL paragraph detection.
            def _hclean(w: str) -> str:
                return w.strip("،,.-–—;:")

            # Estimate line count for block-height positioning.
            # Use h_fsize_base (unbumped) for available width — matches canvas edgePad so
            # libass wraps at the same points as the canvas preview.
            h_avail_w = width - 2 * h_fsize_base
            char_w = h_fsize * 0.50
            raw_words = [_hclean(w) for w in hook["text"].split() if _hclean(w)]
            hook_lines, cur, cur_w = [], [], 0.0
            for word in raw_words:
                ww = len(word) * char_w
                gap = char_w if cur else 0.0
                if cur and cur_w + gap + ww > h_avail_w:
                    hook_lines.append(" ".join(cur))
                    cur, cur_w = [word], ww
                else:
                    cur.append(word)
                    cur_w += gap + ww
            if cur:
                hook_lines.append(" ".join(cur))
            # \q1 (simple end-of-line wrap) lets libass wrap as one block → BorderStyle=3/4
            # draws a SINGLE unified background box across all lines.  Using \N hard-breaks
            # caused libass to draw a separate box per line (two disjointed rectangles).
            #
            # BorderStyle=3: OutlineColour = box fill; no outer border — use when border_size=0.
            # BorderStyle=4: BackColour = box fill, OutlineColour = outline around box, Outline=width
            #   — use when border_size>0 so box color and border color are independent fields.
            #   With BorderStyle=3, \3c would override OutlineColour (= box background) instead of
            #   drawing a separate border, making the box turn the border color rather than bg_color.
            h_override = "\\q1"
            if h_border_size > 0:
                hbc = h_border_color.lstrip("#")
                if len(hbc) == 3: hbc = "".join(c * 2 for c in hbc)
                hr, hg, hb = int(hbc[0:2], 16), int(hbc[2:4], 16), int(hbc[4:6], 16)
                h_border_ass = f"&H{hb:02X}{hg:02X}{hr:02X}&"
                h_border_style = 4  # box(BackColour) + outline(OutlineColour)
                # Style carries all colors; no per-event override tags needed.
                h_outline_color = h_border_ass
                h_box_color     = h_back   # bg_color goes in BackColour for BorderStyle=4
                hook_outline    = h_border_size
            else:
                h_border_style  = 3  # opaque box only; OutlineColour = box fill
                h_outline_color = h_back   # bg_color in OutlineColour for BorderStyle=3
                h_box_color     = h_back   # also in BackColour for libass compat
                hook_outline    = max(10, int(h_fsize * 0.25))
            h_text_raw = " ".join(raw_words) if raw_words else hook["text"]
            h_text = ("{" + h_override + "}"
                      + h_text_raw.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}"))
            n_hook_lines = len(hook_lines) if hook_lines else 1
            block_h      = int(n_hook_lines * h_fsize * 1.10)
            h_margin_v   = max(0, h_y - block_h // 2)
            hook_style_line = (
                f"Style: Hook,{h_font},{h_fsize},"
                f"{h_primary},&H000000FF,{h_outline_color},{h_box_color},"
                f"-1,0,0,0,100,100,0,0,{h_border_style},{hook_outline},0,8,0,0,0,1\n"
            )
            # MarginL/R = h_fsize_base constrains libass wrap width to match canvas maxW.
            hook_event_line = (
                f"Dialogue: 1,{seconds_to_ass(h_start)},{seconds_to_ass(h_start + h_dur)},"
                f"Hook,,{h_fsize_base},{h_fsize_base},{h_margin_v},,{h_text}\n"
            )

        header = (
            "[Script Info]\nScriptType: v4.00+\n"
            f"PlayResX: {width}\nPlayResY: {height}\n"
            "WrapStyle: 0\nScaledBorderAndShadow: yes\nYCbCr Matrix: TV.709\n\n"
            "[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
            "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
            "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
            "Alignment, MarginL, MarginR, MarginV, Encoding\n"
            f"Style: Default,{font},{font_size},"
            "&H00FFFFFF,&H000000FF,&H00000000,&H80000000,"
            f"-1,0,0,0,100,100,0,0,1,2,0,2,"
            f"{margin_h},{margin_h},{margin_v},1\n"
            + hook_style_line +
            "\n[Events]\n"
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        )
        lines = [
            f"Dialogue: 0,{seconds_to_ass(c['start'])},{seconds_to_ass(c['end'])},"
            f"Default,,0,0,0,,{_rtl_ass_text(c['text'])}\n"
            for c in captions
        ]
        ass_path.write_text(header + "".join(lines) + hook_event_line, encoding="utf-8")

        # Copy / download selected B-roll clips into temp dir
        import requests as _req_burn
        broll_files = []
        for idx, item in enumerate(broll_items):
            dst = tmp / f"broll_{idx}.mp4"
            vid_key      = item.get("video_key")
            download_url = item.get("download_url") or item.get("preview_url")
            if vid_key:
                src = Path(TMP_DIR) / vid_key
                if not src.exists():
                    continue
                shutil.copy(src, dst)
            elif download_url:
                try:
                    r = _req_burn.get(download_url, timeout=30, stream=True)
                    r.raise_for_status()
                    with open(dst, "wb") as fh:
                        for chunk in r.iter_content(65536):
                            fh.write(chunk)
                except Exception as dl_err:
                    print(f"[burn] b-roll {idx} download failed: {dl_err}")
                    continue
            else:
                continue
            start         = float(item["start"])
            end           = float(item.get("end", start + 3.0))
            clip_in_start = float(item.get("clip_use_start_seconds", 0.0))
            clip_in_end   = item.get("clip_use_end_seconds")
            if clip_in_end is not None:
                clip_in_end = float(clip_in_end)
            broll_files.append((dst, start, end, clip_in_start, clip_in_end))

        esc = str(ass_path).replace(":", r"\:")

        if not broll_files:
            # Simple path: just subtitle burn (or copy if no captions)
            if captions:
                run(["ffmpeg", "-y"] + _rot_input + ["-i", str(video_in),
                     "-vf", f"{_rot_vf}subtitles='{esc}'",
                     "-c:v", "libx264", "-crf", "18", "-preset", "veryfast",
                     "-pix_fmt", "yuv420p", "-c:a", "copy",
                     "-movflags", "+faststart"] + _rot_meta + [str(video_out)])
            elif _rotation:
                run(["ffmpeg", "-y"] + _rot_input + ["-i", str(video_in),
                     "-vf", _rot_vf.rstrip(","),
                     "-c:v", "libx264", "-crf", "18", "-preset", "veryfast",
                     "-pix_fmt", "yuv420p", "-c:a", "copy",
                     "-movflags", "+faststart"] + _rot_meta + [str(video_out)])
            else:
                shutil.copy(video_in, video_out)
        else:
            # Complex path: B-roll video overlays first, captions burned on top
            cmd = ["ffmpeg", "-y"] + _rot_input + ["-i", str(video_in)]
            for vid_path, *_ in broll_files:
                cmd += ["-i", str(vid_path)]  # finite video clip — no -loop

            filters = []
            prev = "0:v"
            # Apply rotation to main track first if needed
            if _rotation:
                filters.append(f"[0:v]{_rot_vf.rstrip(',')}[vrot]")
                prev = "vrot"
            for idx, (_, start, end, clip_in_start, clip_in_end) in enumerate(broll_files):
                vid_idx = idx + 1
                out_label = f"vbr{idx}"
                # Trim to desired window within the stock clip, then scale to frame
                if clip_in_end is not None:
                    trim_f = f"trim=start={clip_in_start:.3f}:end={clip_in_end:.3f},"
                elif clip_in_start > 0:
                    trim_f = f"trim=start={clip_in_start:.3f},"
                else:
                    trim_f = ""
                filters.append(
                    f"[{vid_idx}:v]{trim_f}"
                    f"scale={width}:{height}:"
                    f"force_original_aspect_ratio=increase,crop={width}:{height},"
                    f"setpts=PTS-STARTPTS+{start:.3f}/TB[bri{idx}]"
                )
                # eof_action=pass: after clip ends, pass through main video (don't stop)
                filters.append(
                    f"[{prev}][bri{idx}]overlay=0:0:"
                    f"enable='between(t,{start:.3f},{end:.3f})':eof_action=pass[{out_label}]"
                )
                prev = out_label

            # Captions burned last so they appear over B-roll video
            if captions:
                filters.append(f"[{prev}]subtitles='{esc}'[vout]")
                map_out = "[vout]"
            else:
                map_out = f"[{prev}]"

            cmd += [
                "-filter_complex", ";".join(filters),
                "-map", map_out, "-map", "0:a",
                "-c:v", "libx264", "-crf", "18", "-preset", "veryfast",
                "-pix_fmt", "yuv420p", "-c:a", "copy",
                "-movflags", "+faststart",
                str(video_out),
            ]
            run(cmd)

        output_key = uuid.uuid4().hex + "_out.mp4"
        out_path = Path(TMP_DIR) / output_key
        shutil.copy(video_out, out_path)
        try:
            _record_job(output_key, source_name, out_path)
            prune_volume()
        except Exception as _je:
            print(f"[jobs] record/prune skipped: {_je!r}")
        tmp_vol.commit()
        return {"output_key": output_key}



# ---------------------------------------------------------------------------
# Job history + volume retention
# ---------------------------------------------------------------------------
def _record_job(output_key, source_name, out_path):
    """Add a burned output to the History manifest."""
    import time
    duration = 0.0
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(out_path)],
            capture_output=True, text=True, timeout=15)
        duration = float(r.stdout.strip() or 0)
    except Exception:
        pass
    jobs_store[output_key] = {
        "name": source_name or "video",
        "ts": time.time(),
        "size": out_path.stat().st_size,
        "duration": round(duration, 1),
    }


def prune_volume():
    """Retention sweep, best-effort: burned outputs past JOB_RETENTION_DAYS and
    scratch files (_src/_words/_audio/_cut/chunks) past SCRATCH_RETENTION_HOURS."""
    import time
    from pathlib import Path
    now = time.time()
    try:
        for key in list(jobs_store.keys()):
            meta = jobs_store.get(key) or {}
            if now - meta.get("ts", 0) > JOB_RETENTION_DAYS * 86400:
                (Path(TMP_DIR) / key).unlink(missing_ok=True)
                jobs_store.pop(key)
        protected = set(jobs_store.keys())
        for p in Path(TMP_DIR).iterdir():
            if p.name in protected or not p.is_file():
                continue
            if now - p.stat().st_mtime > SCRATCH_RETENTION_HOURS * 3600:
                p.unlink(missing_ok=True)
    except Exception as e:
        print(f"[prune] skipped: {e!r}")


# ---------------------------------------------------------------------------
# Cut restore — re-render from cached source with selected silences kept in.
# CPU-only: reuses the persisted transcription, no GPU / no re-transcribe.
# ---------------------------------------------------------------------------
@app.function(image=burn_image, timeout=600, volumes={TMP_DIR: tmp_vol})
def rerender_cuts_fn(upload_key: str, restored_json: str = "[]") -> dict:
    import shutil, tempfile, uuid
    from pathlib import Path

    if not upload_key or not _SAFE_KEY_RE.match(upload_key):
        raise ValueError("Invalid upload key")

    tmp_vol.reload()
    src_path   = Path(TMP_DIR) / f"{upload_key}_src.mp4"
    words_path = Path(TMP_DIR) / f"{upload_key}_words.json"
    audio_path = Path(TMP_DIR) / f"{upload_key}_audio.wav"
    if not (src_path.exists() and words_path.exists() and audio_path.exists()):
        raise ValueError("Source files for this session have expired — run the pipeline again to restore cuts.")

    meta = json.loads(words_path.read_text())
    whisper_segs = [([Word(s, e, t) for s, e, t in seg["words"]], seg["seg_end"])
                    for seg in meta["segments"]]
    segs = compute_keep_segments(whisper_segs, meta["duration"],
                                 meta["min_silence"], meta["padding"])
    cuts = compute_cuts(segs)
    merge_restored(segs, json.loads(restored_json))

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        ass_file = tmp / "captions.ass"
        out_file = tmp / "out.mp4"
        events = generate_ass(segs, ass_file, meta["width"], meta["height"],
                              min_sil=meta["min_silence"])
        captions = [{"start": s, "end": e, "text": _censor_caption_text(t)}
                    for s, e, t in events]
        render(src_path, audio_path, segs, None, out_file, rotation=meta["rotation"])
        video_key = uuid.uuid4().hex + "_cut.mp4"
        shutil.copy(out_file, Path(TMP_DIR) / video_key)
        tmp_vol.commit()
    return {"captions": captions, "video_key": video_key, "cuts": cuts}


# ---------------------------------------------------------------------------
# Hook burn — overlay hook text on processed video using ASS subtitle
# ---------------------------------------------------------------------------

@app.function(image=burn_image, timeout=180, volumes={TMP_DIR: tmp_vol})
def burn_hook_fn(
    video_key: str,
    hook_text: str,
    font: str = "Heebo",
    font_color: str = "#FFFFFF",
    bg_color: str = "#000000",
    bg_opacity: float = 0.6,
    start_seconds: float = 1.0,
    duration_seconds: float = 4.0,
    vertical_position: int = 10,
    border_color: str = "#000000",
    border_size: int = 0,
) -> dict:
    import json, subprocess, tempfile, shutil
    from pathlib import Path

    tmp_vol.reload()

    def run(cmd):
        r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=150)
        if r.returncode != 0:
            raise RuntimeError(f"ffmpeg {r.returncode}:\n{r.stderr.decode('utf-8', errors='replace')[-2000:]}")

    def probe_dims(path):
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height,side_data_list",
             "-show_entries", "stream_tags=rotate",
             "-of", "json", str(path)],
            capture_output=True, text=True, check=True,
        )
        s = json.loads(r.stdout)["streams"][0]
        w, h = int(s["width"]), int(s["height"])
        rot = 0
        tag_rot = s.get("tags", {}).get("rotate")
        if tag_rot:
            rot = int(tag_rot)
        else:
            for sd in s.get("side_data_list", []):
                if "rotation" in sd:
                    rot = (-int(sd["rotation"])) % 360
                    break
        if rot in (90, 270):
            w, h = h, w
        return w, h, rot

    def seconds_to_ass(t):
        h = int(t // 3600); m = int((t % 3600) // 60); s = t % 60
        return f"{h}:{m:02d}:{s:05.2f}"

    def hex_to_ass(hex_color: str, alpha_byte: int = 0) -> str:
        """Convert #RRGGBB to ASS &HAABBGGRR&. alpha_byte: 0=opaque, 255=transparent."""
        h = hex_color.lstrip("#")
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return f"&H{alpha_byte:02X}{b:02X}{g:02X}{r:02X}&"

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path  = Path(tmp)
        video_in  = tmp_path / "input.mp4"
        video_out = tmp_path / "output.mp4"
        ass_path  = tmp_path / "hook.ass"
        shutil.copy(Path(TMP_DIR) / video_key, video_in)

        width, height, _rotation = probe_dims(video_in)
        font_size = max(36, min(96, int(height * 0.075)))

        primary_col = hex_to_ass(font_color, alpha_byte=0)
        bg_alpha    = int((1.0 - max(0.0, min(1.0, bg_opacity))) * 255)
        back_col    = hex_to_ass(bg_color, alpha_byte=bg_alpha)

        # Vertical center of text; vertical_position: 0=top, 100=bottom
        pad = font_size
        y   = int(pad + (height - 2 * pad) * (max(0, min(100, vertical_position)) / 100))

        safe_text = hook_text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")
        override  = "{\\an5\\pos(" + str(width // 2) + "," + str(y)
        border_size = max(0, min(20, int(border_size)))
        if border_size > 0:
            bc = border_color.lstrip("#")
            if len(bc) == 3: bc = "".join(c * 2 for c in bc)
            br, bg_, bb = int(bc[0:2], 16), int(bc[2:4], 16), int(bc[4:6], 16)
            override += "\\bord" + str(border_size) + "\\3c&H" + f"{bb:02X}{bg_:02X}{br:02X}" + "&"
        override += "}"

        ass_content = (
            "[Script Info]\nScriptType: v4.00+\n"
            f"PlayResX: {width}\nPlayResY: {height}\n"
            "WrapStyle: 1\nScaledBorderAndShadow: yes\n\n"
            "[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
            "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
            "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
            "Alignment, MarginL, MarginR, MarginV, Encoding\n"
            f"Style: Hook,{font},{font_size},"
            f"{primary_col},&H000000FF,&H00000000,{back_col},"
            "-1,0,0,0,100,100,2,0,3,10,0,5,"
            "20,20,20,1\n\n"
            "[Events]\n"
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
            f"Dialogue: 0,{seconds_to_ass(start_seconds)},{seconds_to_ass(start_seconds + duration_seconds)},"
            f"Hook,,0,0,0,,{override}{safe_text}\n"
        )
        ass_path.write_text(ass_content, encoding="utf-8")

        run([
            "ffmpeg", "-y", "-i", str(video_in),
            "-vf", f"ass={ass_path}",
            "-c:v", "libx264", "-crf", "18", "-preset", "veryfast",
            "-c:a", "copy", str(video_out),
        ])

        out_key = f"hook_{video_key}"
        shutil.copy(video_out, Path(TMP_DIR) / out_key)
        tmp_vol.commit()

    return {"download_key": out_key}


