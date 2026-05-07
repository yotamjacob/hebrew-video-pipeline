# Hebrew Video Pipeline

Takes an unedited Hebrew-language video, produces a cleaned edited video with
Hebrew captions burned in. Silence gaps are cut using word-level timestamps
(not dB thresholds), so words never get clipped. One ffmpeg re-encode at the
end keeps quality high.

Two deployment modes:
- **Local CLI** (`hebrew_video_pipeline.py`) — run on your own machine
- **Cloud / Web** (`app_modal.py` + `site/index.html`) — Modal GPU backend + Vercel frontend

## Files

- `hebrew_video_pipeline.py` — local CLI orchestrator
- `app_modal.py` — Modal serverless API (GPU pipeline + stock B-roll finder)
- `site/index.html` — single-page web UI (upload, process, download, B-roll)
- `site/vercel.json` — Vercel SPA rewrite rule
- `captions_template.ass` — standalone caption template (reference / override)
- `test_api.py` — end-to-end API tests (upload → process → burn)
- `test_stock_helpers.py` — unit tests for stock helpers (no network, no Modal)

## One-time setup

### 1. Install ffmpeg
```bash
# macOS
brew install ffmpeg
# Ubuntu/Debian
sudo apt install ffmpeg
# Windows
# download from https://ffmpeg.org/download.html and add to PATH
```

### 2. Install Python dependencies
```bash
pip install faster-whisper requests
```
On first run, `faster-whisper` will download the ivrit-ai model (~1.5 GB for
the turbo variant) from Hugging Face and cache it locally.

### 3. Install a Hebrew-friendly font
Either install **Rubik** (default), **Heebo**, or **Assistant** system-wide,
or pass `--font "DejaVu Sans"` (ships with Ubuntu; not as pretty but works).

### 4. (Optional) ElevenLabs API key
For automated audio cleanup instead of the manual Adobe Podcast step:
```bash
export ELEVENLABS_API_KEY=sk_your_key_here
```

## Usage

```bash
# Manual enhancement (upload to https://podcast.adobe.com/enhance when prompted)
python hebrew_video_pipeline.py input.mp4

# Fully automated
python hebrew_video_pipeline.py input.mp4 --enhance elevenlabs

# Skip enhancement (source is already clean)
python hebrew_video_pipeline.py input.mp4 --enhance skip

# Custom output path + tighter silence cutting
python hebrew_video_pipeline.py input.mp4 \
  -o output.mp4 \
  --min-silence 0.4 \
  --padding 0.15

# Different caption styling
python hebrew_video_pipeline.py input.mp4 \
  --font Heebo \
  --font-size 72 \
  --max-chars 32 \
  --max-lines 2
```

## Tuning guide

| If you see...                                  | Adjust...                            |
|------------------------------------------------|--------------------------------------|
| Words getting chopped at cut points            | `--padding 0.3` (more breathing room)|
| Cuts feel too aggressive / choppy pace         | `--min-silence 0.8`                  |
| Captions too small / too big                   | `--font-size`                        |
| Captions wrap too early / too late             | `--max-chars`                        |
| Quality looks soft                             | `--crf 15` (bigger file, better quality)|
| Transcription slow on CPU                      | Use turbo model (default) or get GPU |

## How the quality is preserved

There is exactly **one** re-encode: the final ffmpeg pass trims segments with
`trim`/`atrim`, concatenates them, and burns captions — all in a single
`filter_complex` graph. Output is H.264 at CRF 18 with `preset slow`, which is
visually lossless. The audio track is the *enhanced* WAV, not the original,
and gets encoded once to 192 kbps AAC.

## Architecture (local CLI)

Each pipeline step is a separate function — clean boundaries, easy to swap:

```
probe_video()           → (width, height, duration)
extract_audio()         → raw.wav
enhance_*()             → clean.wav   (3 implementations)
transcribe()            → List[Word]  (ivrit-ai Whisper, word-level)
compute_keep_segments() → List[KeepSegment]
generate_ass()          → captions.ass (timestamps pre-remapped to cut timeline)
final_render()          → output.mp4 (single ffmpeg pass)
```

Want a different transcription backend (ElevenLabs Scribe, Speechmatics, etc.)?
Replace `transcribe()`. The rest of the pipeline only cares that it returns a
`List[Word]` with `start`, `end`, `text`.

## Architecture (cloud / Modal)

```
browser → POST /upload_chunk  → modal.Volume (chunks)
       → POST /process        → process_video.spawn() → call_id
       → GET  /process_poll/  → poll until done → {captions, video_key}
       → POST /burn           → burn_captions_fn.spawn() → call_id
       → GET  /burn_poll/     → poll until done → {output_key}
       → GET  /download/{key} → stream video from Volume

browser → POST /stock-broll   → analyze_stock_broll.spawn() → call_id
       → GET  /stock-broll-poll/ → {moments: [{clips: [...]}]}
       → POST /stock-broll-clips → search_stock_clips.spawn() → call_id  ("Find different clips")
       → GET  /stock-broll-clips-poll/ → {clips: [...]}
```

**Key Modal functions:**

| Function | Timeout | Resources | Purpose |
|----------|---------|-----------|---------|
| `process_video` | 900s | T4 GPU | transcribe + cut silences |
| `burn_captions_fn` | 300s | CPU | burn captions + composite B-roll |
| `analyze_stock_broll` | 300s | CPU | Sonnet moment-selection + Pexels/Pixabay search + Haiku scoring |
| `search_stock_clips` | 120s | CPU | "Find different clips" — page 2+ of results |
| `api` | 900s | CPU | ASGI router, max 20 concurrent |

**Module-level helpers** (shared across `analyze_stock_broll` and `search_stock_clips`):
- `fetch_pexels(query, page, key, session?)` — Pexels video search, portrait-filtered
- `fetch_pixabay(query, page, key, session?)` — Pixabay video search, portrait-filtered
- `score_clips(clips, strict_eval, anthropic_client)` — Haiku vision scoring, returns clips ≥ 6
- `add_clip_window(clip, broll_duration)` — selects middle window of long clips

## Stock B-roll finder

The stock B-roll flow uses a two-prompt architecture to solve the precision/recall tradeoff:

1. **Sonnet** reads the full transcript and identifies 3 B-roll moments. For each moment it outputs:
   - `broad_search_prompt` — 2–5 keywords for stock library retrieval (optimizes recall)
   - `strict_eval_prompt` — full sentence with a `DISQUALIFY:` clause for scoring (optimizes precision)
   - Duration fields derived from actual speech timestamps, clamped to 2–5 s

2. **Pexels + Pixabay** are queried with `broad_search_prompt`. Portrait clips only.
   If 0 results, retries with the first 2 keywords.

3. **Haiku** scores each clip (1–10) against `strict_eval_prompt` using thumbnail + metadata.
   Only clips scoring ≥ 6 are shown.

The Sonnet system prompt is cached with `cache_control: ephemeral` — ~50 % cheaper after the first call per container warm period.

## How to add a stock source

All stock-fetch logic is in two module-level functions at the top of `app_modal.py`:
`fetch_pexels` and `fetch_pixabay`. Both return a uniform clip dict:

```python
{
    "source": "pexels",       # or "pixabay"
    "id": ...,
    "preview_url": "...",     # direct video URL
    "thumbnail": "...",       # image URL for Haiku scoring
    "page_url": "...",
    "author": "...",
    "author_url": "...",
    "attribution": "...",
    "title": "...",
    "tags": [...],
    "duration": 12.0,         # seconds
}
```

To add a third source (e.g. Storyblocks):

1. Add `fetch_storyblocks(query, page, key, session=None) -> list` in `app_modal.py`
   following the same return shape.

2. In both `analyze_stock_broll` and `search_stock_clips`, add the new fetch call to
   the interleaving loop:
   ```python
   sb  = fetch_storyblocks(broad, 1, storyblocks_key, http_session)
   for i in range(max(len(pex), len(pix), len(sb))):
       if i < len(pex): clips.append(pex[i])
       if i < len(pix): clips.append(pix[i])
       if i < len(sb):  clips.append(sb[i])
   ```

3. Add a `modal.Secret.from_name("storyblocks-secret")` to both `@app.function` decorators.

4. Add the source badge colour to `site/index.html` (search for `source === 'pexels'`).

## How to swap AI models

Anthropic model IDs are defined as module-level constants near the top of `app_modal.py`:

```python
SONNET_MODEL = "claude-sonnet-4-5"       # moment selection
HAIKU_MODEL  = "claude-haiku-4-5-20251001"  # clip scoring
OPUS_MODEL   = "claude-opus-4-7"         # Veo B-roll analysis (rarely used)
```

Gemini/Veo constants are also at module level:
```python
TRANSCRIPT_ANALYSIS_MODEL = "gemini-2.5-flash"
VIDEO_GENERATION_MODEL    = "veo-3.0-generate-001"
```

Change any constant and redeploy — no other code changes needed.

## Standalone caption template

`captions_template.ass` shows the styling independently — useful if you want
to preview the look with a dummy video, or hand-edit captions from a different
source and still get the same visual style:

```bash
ffmpeg -i any_video.mp4 -vf "subtitles=captions_template.ass" \
  -c:v libx264 -crf 18 -preset slow -c:a copy preview.mp4
```

## Running tests

```bash
source .venv/bin/activate

# Fast unit tests — no network, no Modal
python -m pytest test_stock_helpers.py -v

# End-to-end API tests — requires deployed Modal app
python test_api.py
```
