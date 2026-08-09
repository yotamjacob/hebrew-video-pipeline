# Hebrew Video Pipeline — Claude Guide

## Deep-contract docs — READ BEFORE TOUCHING

The always-loaded core below is deliberately lean. Every subsystem's load-bearing
contracts (the "why it's built this way" notes with dates) live in `docs/` and MUST
be read fully before changing that area — they encode field-tested invariants whose
violation has shipped real bugs before. Update them in place when behavior changes.

| Doc | Read before touching |
|-----|----------------------|
| `docs/captions.md` | Caption/hook rendering, ASS/libass, fonts, RTL/bidi, caption modes (word/karaoke), style presets, color picker, exact-frame preview |
| `docs/effects.md` | Auto punch-in zoom (windows, chips, smooth ramp, face framing) |
| `docs/uploads.md` | Chunked/R2/native uploads, resume/reconcile, deferred spawn, CORS |
| `docs/processing.md` | Transcription, silence cutter, audio-only mode, enhance/upscale, progress stages, push notifications, Anthropic API usage |
| `docs/broll-hooks.md` | Stock B-roll search/scoring/validation, hook generation, generator polling |
| `docs/outputs.md` | Job history/retention, re-edit, downloads (web+native), SRT, preview player source, metadata backup, cost tracking, Metricool scheduling |
| `docs/auth-billing.md` | Auth/sessions/tokens, signup, quota/credits, Play billing, throttles, rate limits, admin surfaces |
| `docs/frontend.md` | Design system, footer, tab caching, celebrations, error telemetry, frontend TEST conventions (boot-call stubbing), site module details |

## Module Map

| File | Purpose |
|------|---------|
| `app_modal.py` | **Deploy entrypoint** — imports all backend modules + the `api()` ASGI router (all HTTP routes) |
| `pipeline_core.py` | Modal app/images/volumes, model constants, pure helpers (security, RTL text, rate limiting, face detection, `_poll_fn_call`) |
| `pipeline_fns.py` | `process_video` (GPU), `burn_captions_fn` (captions + hook + B-roll burn), `build_caption_ass` (shared ASS builder), `_zoom_filters`, `_peak_window`, job history + retention pruning |
| `stock_helpers.py` | Pure stock-footage helpers: Pexels/Pixabay fetch, frame sampling, `score_clips`, `add_clip_window` |
| `broll_fns.py` | Stock B-roll: `analyze_stock_broll`, `search_stock_clips`, `_get_video_context`, `_process_moment` |
| `content_fns.py` | `generate_hook_options`, `generate_caption_options` |
| `metricool_fns.py` | Metricool OAuth store, MCP client, `schedule_post_fn` |
| `site/index.html` | Frontend markup — "פייפליין" branding; tabs: Create, History, Guide, Admin. No framework, no build step |
| `site/app.js` | ALL frontend logic (~9k lines). `APP_VERSION` at top. Details: `docs/frontend.md` |
| `site/i18n.js` | EN/HE dictionary + engine: `t(key, vars)`, `data-i18n` attrs, `setLang`; toggle `#langToggle` |
| `site/app.css` | All styles. Warm-boho design system, no emoji anywhere — contract in `docs/frontend.md` |
| `site/sw.js` | Service worker — background job polling only (no asset caching) |
| `site/legal.html` | Bilingual privacy+terms, shown in an on-page modal — see `docs/frontend.md` |
| `site/vercel.json` | SPA rewrite rule (all routes → index.html) |
| `test_api.py` | End-to-end API tests (upload → process → burn) — costs GPU time, run only when asked |
| `test_stock_helpers.py` | Unit tests for stock helpers — local, no network |
| `tests/backend/`, `tests/frontend/` | pytest (AST-extraction, no Modal import) + Playwright suites |
| `TODO.md` / `LAUNCH_IDEAS.md` | Roadmap / marketing-feature research |

**Token tip:** read only the module you're changing, plus its `docs/` file. Backend
modules import shared names from `pipeline_core`; new backend files must be added to
`add_local_python_source(...)` on **all three** images in `pipeline_core.py`
(`image` = full ML, `burn_image` = ffmpeg+fonts+opencv, `light_image` = ffmpeg+anthropic+boto3+opencv
for hooks/captions/stock B-roll, the `api()` router, and the daily metadata backup).

## Pipeline Steps

1. `extract_audio()` — ffmpeg extracts 48 kHz mono WAV
2. `enhance_*()` — ElevenLabs API / Adobe Podcast manual / skip
3. `transcribe()` — faster-whisper (`ivrit-ai/whisper-large-v3-ct2`), word-level timestamps; on Modal a Sonnet proofread pass fixes Hebrew mishearings (array length/order preserved)
4. `compute_keep_segments()` — word-level speech-keep detection (silence + filler excision)
5. `generate_ass()` — builds ASS subtitle file; remaps timestamps to post-cut video
6. `final_render()` — single ffmpeg pass: trim → concat → burn captions → H.264 CRF 18

## Key Defaults

```python
DEFAULT_MIN_SILENCE = 0.3      # gap (s) to treat as silence
DEFAULT_PADDING     = 0.20     # breathing room around words
DEFAULT_FONT        = "Rubik"  # Hebrew-friendly
DEFAULT_FONT_SIZE   = 48
DEFAULT_CRF         = 18       # H.264 quality
DEFAULT_WHISPER_MODEL = "ivrit-ai/whisper-large-v3-ct2"
```

## Build / Run / Deploy

```bash
# Modal (GPU cloud) — backend deploy
source .venv/bin/activate && modal deploy app_modal.py   # production
modal serve app_modal.py                                  # live dev

# Website (Vercel) — deploy from project root, NOT from site/
npx vercel deploy --prod
```

**Web API base** — `https://yotamjacob--hebrew-video-pipeline-api.modal.run` (hardcoded in `site/app.js`).

**Version stamp on EVERY site deploy** — bump `APP_VERSION` at the top of `site/app.js` before `vercel deploy --prod` and tell the user the new version; it renders in every footer + `window.__APP_VERSION`. The Android app loads this frontend remotely, so the footer tag is the only on-device proof of which build runs. Backend-only changes: no bump, say so. Test: `tests/frontend/version_tag.spec.js`.

**Standing publish preference (2026-07-29)** — after every completed, tested fix, commit and push directly to GitHub `main`, then deploy the production Vercel frontend. If the fix changes backend behavior, deploy Modal BEFORE Vercel so the new frontend never targets an older API. No feature branches or PRs unless explicitly requested.

## Model Constants

Defined at the top of `pipeline_core.py` — change and redeploy, no other edits needed:

```python
SONNET_MODEL = "claude-sonnet-5"             # moment selection, hooks, captions, video context
HAIKU_MODEL  = "claude-haiku-4-5-20251001"   # stock clip frame scoring
```

## Conventions

- **ffmpeg local dev**: prefer Homebrew `ffmpeg-full` (`/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg`) — it includes libass.
- Stock search helpers live in `stock_helpers.py` — never re-inline them; pass `http_session` for multi-call reuse.
- Backend unit tests never import the Modal modules — they AST-extract from source (`tests/backend/conftest.py` concatenates backend `.py` files into `MODAL_SRC`). Renaming/moving a backend file means updating the file lists in `conftest.py`, `test_ass_generation.py`, and `test_stock_helpers.py`.
- Backend modules stay FLAT at repo root (Modal flat-import + AST tests depend on it).
- The site is pure HTML/CSS/JS — no build step, no bundler.
- Frontend tests MUST stub every boot/error side-call (see `docs/frontend.md`) — unmocked calls 401 against the real API and flake the suite.
- Run `python -m pytest test_stock_helpers.py tests/backend/` before deploying backend changes; `npx playwright test` after `site/` changes (or the affected specs during a fix batch, full suite at the end).
- No em dashes in user-facing copy (EN or HE) — use regular dashes.
- **UI layout rule (user directive, 2026-08-09):** every new or changed control layout must be symmetrical or clearly UX-motivated — no stretched full-width buttons/controls where a compact one belongs, no half-empty grid cells, reuse the shared components (`.design-field` rows, color swatches, chip patterns). Screenshot-verify layout changes before shipping (see `docs/frontend.md`).
