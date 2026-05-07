# Hebrew Video Pipeline — Claude Guide

## Module Map

| File | Purpose |
|------|---------|
| `hebrew_video_pipeline.py` | Local CLI — 6-step pipeline (extract → enhance → transcribe → segments → ASS → render) |
| `app_modal.py` | Modal serverless API — same pipeline running on T4 GPU, exposed as HTTP |
| `site/index.html` | Single-page Vercel frontend — upload, process, download; no framework |
| `site/vercel.json` | SPA rewrite rule (all routes → index.html) |
| `captions_template.ass` | ASS subtitle format reference and style examples |
| `requirements.txt` | `faster-whisper`, `requests` |
| `README.md` | Full usage docs, architecture, "how to add a stock source", "how to swap models" |
| `TODO.md` | Active feature roadmap |
| `test_api.py` | End-to-end API tests (upload → process → burn) |
| `test_stock_helpers.py` | Unit tests for stock helpers — runs locally, no network or Modal |
| `docs/optimization-audit.md` | Phase 0 audit — 18 findings across security, tokens, code quality, memory |

## Pipeline Steps (both CLI and Modal share this logic)

1. `extract_audio()` — ffmpeg extracts 48 kHz mono WAV
2. `enhance_*()` — ElevenLabs API / Adobe Podcast manual / skip
3. `transcribe()` — faster-whisper (`ivrit-ai/whisper-large-v3-turbo-ct2`), word-level timestamps
4. `compute_keep_segments()` — silence detection from word gaps; adds padding around speech
5. `generate_ass()` — builds ASS subtitle file; remaps timestamps to post-cut video
6. `final_render()` — single ffmpeg pass: trim → concat → burn captions → H.264 CRF 18

## Key Defaults

```python
DEFAULT_MIN_SILENCE = 0.5      # gap (s) to treat as silence
DEFAULT_PADDING     = 0.20     # breathing room around words
DEFAULT_FONT        = "Rubik"  # Hebrew-friendly
DEFAULT_FONT_SIZE   = 48
DEFAULT_CRF         = 18       # H.264 quality (lower = better; 17-19 lossless-ish)
DEFAULT_WHISPER_MODEL = "ivrit-ai/whisper-large-v3-turbo-ct2"
```

## Build / Run Commands

```bash
# Local CLI
source .venv/bin/activate
python hebrew_video_pipeline.py input.mp4                          # deepfilter by default
python hebrew_video_pipeline.py input.mp4 --enhance skip           # bypass enhancement
python hebrew_video_pipeline.py input.mp4 --enhance elevenlabs --api-key sk_xxx
python hebrew_video_pipeline.py input.mp4 --font Heebo --font-size 72 --keep-tmp

# Modal (GPU cloud)
modal deploy app_modal.py      # production
modal serve  app_modal.py      # live dev

# Website (Vercel)
cd site && vercel --prod        # deploy — always run this after any site/ change
```

## Non-Obvious Details

**ffmpeg binary selection** — prefers Homebrew `ffmpeg-full` (`/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg`) over generic `ffmpeg` because `ffmpeg-full` includes libass (needed for burning subtitles).

**CUDA fallback** — `transcribe()` tries float16 on CUDA first; auto-downgrades to int8 on CPU. No manual config needed.

**ASS color format** — `&HAABBGGRR&` (reversed RGB, alpha first). Alpha `00` = opaque, `FF` = transparent.

**Caption timestamp remapping** — ASS cues are generated assuming the full uncut timeline, then shifted by the accumulated duration of preceding cut-out segments. This must stay in sync with the ffmpeg trim list.

**Modal warmup pattern** — `GET /warmup` fires `process_video` with `filename="__warmup__"` asynchronously to pre-warm the T4 container. The real `/process` endpoint benefits on the next call.

**Whisper model cache** — first run downloads ~1.5 GB to the local faster-whisper cache; Modal uses a persistent volume so it survives redeployments.

**Web API base** — `https://yotamjacob--hebrew-video-pipeline-api.modal.run` (hardcoded in `site/index.html`).

**Font size auto-scaling** — the pipeline reads video resolution and adjusts `--font-size` and `--margin-v` proportionally so captions look consistent across 1080p and 4K.

**Prompt caching** — the Sonnet moment-selection system prompt is marked `cache_control: ephemeral`. The ~900-token stable prefix is cached; only the transcript (user message) changes per call. ~50 % cost reduction after first call in a warm container period.

**Stock search two-prompt split** — `broad_search_prompt` is sent to Pexels/Pixabay (optimized for recall); `strict_eval_prompt` is sent to Haiku vision scoring only (optimized for precision). Never swap them — the stock libraries can't handle the strict prompt and return zero results.

**Chunk streaming** — `upload_chunk` writes each chunk to a separate Volume file. `process_video` reassembles them with `shutil.copyfileobj` directly to the tempdir, one chunk at a time. This keeps RAM usage flat regardless of video size.

**Rate limiting scope** — `_check_rate_limit` uses an in-memory dict per Modal container instance (10 req/60 s per IP). Multiple concurrent container instances each have their own limit — effective limit is `10 × N_instances` per minute. Sufficient for abuse prevention; not a per-user quota.

**Key validation pattern** — `_SAFE_KEY_RE` and `_SAFE_DOWNLOAD_KEY_RE` at module level validate upload keys and download keys before any filesystem access. Keys must be `[a-zA-Z0-9_\-]` (plus `.` for downloads), max 128 chars.

## Model Constants

Defined at the top of `app_modal.py` — change and redeploy, no other edits needed:

```python
SONNET_MODEL = "claude-sonnet-4-5"          # moment selection (analyze_stock_broll)
HAIKU_MODEL  = "claude-haiku-4-5-20251001"   # clip scoring (_score_clips)
OPUS_MODEL   = "claude-opus-4-7"             # Veo B-roll analysis (rarely used)

TRANSCRIPT_ANALYSIS_MODEL = "gemini-2.5-flash"
VIDEO_GENERATION_MODEL    = "veo-3.0-generate-001"
```

## Conventions

- Both `hebrew_video_pipeline.py` and `app_modal.py` contain copies of the core dataclasses (`Word`, `KeepSegment`) and pipeline helpers — keep them in sync when changing shared logic.
- **Stock search helpers** (`fetch_pexels`, `fetch_pixabay`, `score_clips`, `add_clip_window`) are module-level in `app_modal.py`. Both `analyze_stock_broll` and `search_stock_clips` call them — do not re-inline them.
- Pass `http_session` (a `requests.Session`) to `fetch_pexels`/`fetch_pixabay` when making multiple calls in one function invocation — reuses TCP connections.
- Temp files land in `hebpipe_<timestamp>/`; auto-deleted unless `--keep-tmp` is passed.
- Output files are named `<input_stem>_edited.mp4` by default.
- The site is pure HTML/CSS/JS — no build step, no bundler.
- Run `python -m pytest test_stock_helpers.py` before deploying after any change to stock helpers, moment normalization, or `_sanitize_transcript`.
