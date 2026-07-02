# Hebrew Video Pipeline — Claude Guide

## Module Map

| File | Purpose |
|------|---------|
| `hebrew_video_pipeline.py` | Local CLI — 6-step pipeline (extract → enhance → transcribe → segments → ASS → render) |
| `app_modal.py` | **Deploy entrypoint** — imports all backend modules + the `api()` ASGI router (all HTTP routes) |
| `pipeline_core.py` | Modal app/images/volumes, model constants, pure helpers (security, RTL text, rate limiting, `_poll_fn_call`) |
| `pipeline_fns.py` | `process_video` (GPU), `burn_captions_fn`, `burn_hook_fn`, `rerender_cuts_fn` (CPU cut-restore), job history + retention pruning |
| `stock_helpers.py` | Pure stock-footage helpers: Pexels/Pixabay fetch, frame sampling, `score_clips`, `add_clip_window` |
| `broll_fns.py` | Veo generation + `analyze_broll`, `analyze_stock_broll`, `search_stock_clips`, `_process_moment` |
| `content_fns.py` | `generate_hook_options`, `generate_caption_options` |
| `metricool_fns.py` | Metricool OAuth store, MCP client, `schedule_post_fn` |
| `site/index.html` | Vercel frontend markup — two tabs: **Hebrew Pipeline** (upload/process/download) and **Statistics** (Metricool snapshot); no framework |
| `site/app.js` | All frontend logic (upload, polling, editor, burn, debug panel, stats tab, scheduling) |
| `site/app.css` | All frontend styles |
| `site/sw.js` | Service worker — background job polling only (no asset caching) |
| `site/vercel.json` | SPA rewrite rule (all routes → index.html) |
| `site/stats.json` | Committed Metricool stats snapshot the Statistics tab renders (generated, not hand-edited) |
| `generate_stats.py` | Regenerates `site/stats.json` from a Metricool MCP pull — the refresh tool for the Statistics tab |
| `captions_template.ass` | ASS subtitle format reference and style examples |
| `requirements.txt` | `faster-whisper`, `requests` |
| `README.md` | Full usage docs, architecture, "how to add a stock source", "how to swap models" |
| `TODO.md` | Active feature roadmap |
| `test_api.py` | End-to-end API tests (upload → process → burn) — costs GPU time, run only when asked |
| `test_stock_helpers.py` | Unit tests for stock helpers — runs locally, no network or Modal |
| `tests/backend/`, `tests/frontend/` | pytest (AST-extraction, no Modal import) + Playwright suites |

**Token tip:** read only the module you're changing. Backend modules import shared
names from `pipeline_core`; new backend files must be added to
`add_local_python_source(...)` on **both** images in `pipeline_core.py`.

## Pipeline Steps (both CLI and Modal share this logic)

1. `extract_audio()` — ffmpeg extracts 48 kHz mono WAV
2. `enhance_*()` — ElevenLabs API / Adobe Podcast manual / skip
3. `transcribe()` — faster-whisper (`ivrit-ai/whisper-large-v3-turbo-ct2`), word-level timestamps
4. `compute_keep_segments()` — silence detection from word gaps; adds padding around speech
5. `generate_ass()` — builds ASS subtitle file; remaps timestamps to post-cut video
6. `final_render()` — single ffmpeg pass: trim → concat → burn captions → H.264 CRF 18

## Key Defaults

```python
DEFAULT_MIN_SILENCE = 0.3      # gap (s) to treat as silence
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
source .venv/bin/activate && modal deploy app_modal.py   # production
modal serve app_modal.py                                  # live dev

# Website (Vercel) — deploy from project root, NOT from site/
npx vercel deploy --prod
```

## Non-Obvious Details

**ffmpeg binary selection** — prefers Homebrew `ffmpeg-full` (`/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg`) over generic `ffmpeg` because `ffmpeg-full` includes libass (needed for burning subtitles).

**CUDA fallback** — `transcribe()` tries float16 on CUDA first; auto-downgrades to int8 on CPU. No manual config needed.

**ASS color format** — `&HAABBGGRR&` (reversed RGB, alpha first). Alpha `00` = opaque, `FF` = transparent.

**Burned caption ASS style (in `burn_captions_fn`)** — current values as of 2026-05-09:
- `BorderStyle=1, Outline=2, Shadow=0, Alignment=2` (bottom-center)
- `char_w = font_size * 0.60` for Python re-wrap — Hebrew Heebo/Rubik glyphs average ~60% of em-square. Using 0.50 caused under-wrapping; libass added unexpected extra line-breaks via smart-wrap, making captions appear taller/higher than intended.
- Caption shadow is disabled (`Shadow=0`) — the user prefers no shadow on caption text.
- Export font size = `round(sliderFontSize * 1.10)` (captions) — set in `site/app.js` burnUrl.

**Hook ASS rendering** — Unified box: use `{\q1}` soft-wrap (no `\N`), `MarginL=MarginR=h_fsize_base`. Hook export font size = `h_fsize_base * 1.30`.

**Hook border color** — Use `BorderStyle=3` when `border_size=0` (OutlineColour = box fill). Use `BorderStyle=4` when `border_size>0` (BackColour = box fill, OutlineColour = border around box, Outline = border_size). Never use `\3c` override in event text to set border color — with BorderStyle=3, `\3c` overrides OutlineColour which IS the box background, turning the whole box the border color instead of drawing a border. Put all colors in the Style line. Always populate both OutlineColour and BackColour with bg_color for BorderStyle=3 (different libass builds use different fields for the opaque box).

**Caption timestamp remapping** — ASS cues are generated assuming the full uncut timeline, then shifted by the accumulated duration of preceding cut-out segments. This must stay in sync with the ffmpeg trim list.

**Modal warmup pattern** — `GET /warmup` fires `process_video` with `filename="__warmup__"` asynchronously to pre-warm the T4 container. The real `/process` endpoint benefits on the next call.

**Whisper model cache** — first run downloads ~1.5 GB to the local faster-whisper cache; Modal uses a persistent volume so it survives redeployments.

**Web API base** — `https://yotamjacob--hebrew-video-pipeline-api.modal.run` (hardcoded in `site/app.js`).

**Font size auto-scaling** — the pipeline reads video resolution and adjusts `--font-size` and `--margin-v` proportionally so captions look consistent across 1080p and 4K.

**Prompt caching** — the Sonnet moment-selection system prompt is marked `cache_control: ephemeral`. The ~900-token stable prefix is cached; only the transcript (user message) changes per call. ~50 % cost reduction after first call in a warm container period.

**Stock search two-prompt split** — `broad_search_prompt` is sent to Pexels/Pixabay (optimized for recall); `strict_eval_prompt` is sent to Haiku vision scoring only (optimized for precision). Never swap them — the stock libraries can't handle the strict prompt and return zero results.

**Chunk streaming** — `upload_chunk` writes each chunk to a separate Volume file. `process_video` reassembles them with `shutil.copyfileobj` directly to the tempdir, one chunk at a time. This keeps RAM usage flat regardless of video size.

**Rate limiting scope** — `_check_rate_limit` uses an in-memory dict per Modal container instance (10 req/60 s per IP). Multiple concurrent container instances each have their own limit — effective limit is `10 × N_instances` per minute. Sufficient for abuse prevention; not a per-user quota.

**Job history & retention** — every successful `burn_captions_fn` records `{name, ts, size, duration}` in the `hebpipe-jobs` modal.Dict (key = `…_out.mp4` output key) and runs `prune_volume()`: burned outputs deleted after `JOB_RETENTION_DAYS` (30), scratch files (`_src.mp4`, `_words.json`, `_audio.wav`, `_cut.mp4`, chunks) after `SCRATCH_RETENTION_HOURS` (48). Nothing else deletes volume files — the old delete-on-download and delete-after-schedule behaviors are gone. History tab uses `GET /jobs`, `DELETE /jobs/{key}` and the existing `/thumbnail/` + `/download/` routes.

**Cut restore (re-render)** — `process_video` returns `cuts` (removed silence gaps, boundary-indexed) and persists `{upload_key}_words.json` (whisper segments + dims + min_silence/padding) and `{upload_key}_audio.wav` (enhanced audio). `POST /rerender {upload_key, restored:[i]}` spawns `rerender_cuts_fn` on the cheap `burn_image` (no GPU, no re-transcribe): recomputes keep-segments with `merge_restored()`, re-renders from `_src.mp4`, returns fresh `captions` + `video_key`. Cut `index` = boundary between keep-segments i and i+1 — stable because min_silence/padding come from the sidecar, not the request.

**Key validation pattern** — `_SAFE_KEY_RE` and `_SAFE_DOWNLOAD_KEY_RE` at module level validate upload keys and download keys before any filesystem access. Keys must be `[a-zA-Z0-9_\-]` (plus `.` for downloads), max 128 chars.

## Model Constants

Defined at the top of `pipeline_core.py` — change and redeploy, no other edits needed:

```python
SONNET_MODEL = "claude-sonnet-4-6"           # moment selection (analyze_stock_broll)
HAIKU_MODEL  = "claude-haiku-4-5-20251001"   # clip scoring (_score_clips)
OPUS_MODEL   = "claude-opus-4-7"             # Veo B-roll analysis (rarely used)

TRANSCRIPT_ANALYSIS_MODEL = "gemini-2.5-flash"
VIDEO_GENERATION_MODEL    = "veo-3.0-generate-001"
```

## Statistics Tab (site)

The site's second tab shows a social-media performance snapshot for the Yogalina brand, pulled from Metricool. Its goal: let Alina see at a glance **what performs and what doesn't** across her channels.

**Data flow (snapshot model, not live):**
1. `generate_stats.py` holds the raw per-network numbers (hardcoded arrays from a Metricool pull) + hand-written strategist verdicts, computes summaries, and writes `site/stats.json` (stamping `period.generatedAt` in Asia/Jerusalem).
2. `site/app.js` fetches `stats.json` on tab open and renders network cards (status pill + metrics + verdict), best time to post, and top posts.
3. Deploy publishes the new `stats.json`. The page shows a "🕒 Stats from <date>" timestamp + a note telling users to ask Yotam for a refresh.

**Why it's a snapshot, not live:** the account is on Metricool's **free tier** → no API token → no server-side pull possible (a Modal `/stats` proxy would need an Advanced-plan token). The Metricool MCP that produces the data is only available in an interactive Claude session — **not** in cloud/cron routines — so automatic refresh isn't possible either.

**To refresh the stats** (on request — "refresh the Yogalina stats"):
1. Re-pull via the Metricool MCP for brand `4497778` (see `[[project_metricool_stats]]` memory for IDs/field IDs): network `evolution` metrics over the trailing ~90 days, `brandSummary posts` for top posts, `getBestTimeToPostByNetwork` for timing.
2. Update the data arrays in `generate_stats.py`; rewrite a verdict/status only if a channel's standing materially changed.
3. `python3 generate_stats.py` → regenerates `site/stats.json`.
4. `npx vercel deploy --prod` from the project root, then commit + push.

- Verdicts are curated strategic prose — refresh the **numbers** each time, but don't auto-rewrite the prose unless a status pill flips.
- Card order is intentional: strongest channels first (currently Instagram, YouTube, then Facebook, TikTok).

## Conventions

- Both `hebrew_video_pipeline.py` (CLI) and `pipeline_fns.py` (Modal) contain copies of the core dataclasses (`Word`, `KeepSegment`) and pipeline helpers — keep them in sync when changing shared logic.
- **Stock search helpers** (`fetch_pexels`, `fetch_pixabay`, `score_clips`, `add_clip_window`) live in `stock_helpers.py`. Both `analyze_stock_broll` and `search_stock_clips` call them — do not re-inline them.
- Pass `http_session` (a `requests.Session`) to `fetch_pexels`/`fetch_pixabay` when making multiple calls in one function invocation — reuses TCP connections.
- Backend unit tests never import the Modal modules — they AST-extract functions from source (`tests/backend/conftest.py` concatenates all backend `.py` files into `MODAL_SRC`). Renaming/moving a backend file means updating the file lists in `conftest.py`, `test_ass_generation.py`, and `test_stock_helpers.py`.
- Temp files land in `hebpipe_<timestamp>/`; auto-deleted unless `--keep-tmp` is passed.
- Output files are named `<input_stem>_edited.mp4` by default.
- The site is pure HTML/CSS/JS — no build step, no bundler. `index.html` loads `app.css` + `app.js`.
- Run `python -m pytest test_stock_helpers.py tests/backend/` before deploying backend changes; run `npx playwright test` after `site/` changes.
