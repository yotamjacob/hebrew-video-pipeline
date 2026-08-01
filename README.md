# פייפליין · Pipeline

Hebrew video editing at the click of a button. Upload a raw Hebrew talking
video and get back a polished cut: word-level Hebrew captions burned in,
silences trimmed, audio cleaned, optional AI upscale, attention hooks, stock
B-roll suggestions, and one-click scheduling to social platforms.

A branded, RTL-first Hebrew **web app** (accounts, upload, edit, download,
schedule) on a Modal GPU backend + Vercel static frontend.

The core idea: silence gaps are cut using **word-level timestamps**
(not dB thresholds), so words are never clipped, and there's exactly **one**
final ffmpeg re-encode, so quality stays high.

---

## Web app features

- **Accounts & quota** — passwordless: continue with Google, or an emailed
  6-digit code (stateless HMAC sessions; legacy password login still resolves
  old accounts). Signup is open — no invite code — and the only gate on a new
  account is accepting the Terms. Free tier is 3 credits for new accounts (5 for
  accounts predating 2026-07-31); admins are unlimited. Per-user data isolation.
- **Captions** — `ivrit-ai/whisper-large-v3-turbo-ct2` word-level transcription,
  then a Sonnet 5 proofread pass that fixes Hebrew ASR typos without changing
  word count/order (so caption timings stay valid). Editable before burn.
- **Silence cutting** — gap-based, word-safe, with an adjustable aggressiveness.
- **Audio enhance** — DeepFilterNet noise reduction.
- **Video enhance** — off · light filters · **AI upscale** (Real-ESRGAN to a
  true-4K master).
- **Hooks** — Sonnet-generated caption hook options, burned as an opening card.
- **Stock B-roll** — Sonnet picks moments, Pexels/Pixabay fetch clips, Haiku
  vision-scores them; composited server-side into the burn.
- **Scheduling** — per-user Metricool connection (OAuth) to schedule the
  finished video straight to Instagram/TikTok/YouTube/Facebook.
- **History** — finished videos kept 30 days, re-downloadable and schedulable.

## Files

| Path | Purpose |
|------|---------|
| `app_modal.py` | Modal deploy entrypoint — imports backend modules + the `api()` ASGI router (all HTTP routes) |
| `pipeline_core.py` | Modal app/images/volumes, model constants, security/RTL/auth/quota/throttle helpers |
| `pipeline_fns.py` | `process_video` (GPU), `burn_captions_fn`, `build_caption_ass`, job history + retention, daily spend digest, daily metadata backup (`backup_dicts`/`restore_dicts` → R2/B2) |
| `stock_helpers.py` | Pure stock helpers: Pexels/Pixabay fetch, frame sampling, `score_clips`, `add_clip_window` |
| `broll_fns.py` | `analyze_stock_broll`, `search_stock_clips`, `_get_video_context`, `_process_moment` |
| `content_fns.py` | `generate_hook_options`, `generate_caption_options` |
| `metricool_fns.py` | Metricool OAuth store, MCP client, `schedule_post_fn` |
| `site/index.html` | Single-page web UI markup (no framework) |
| `site/app.js` | All frontend logic (auth, chunked upload, polling, editor, burn, history, scheduling, monstera vine) |
| `site/app.css` | All styles + the design tokens (`:root`) |
| `site/i18n.js` | EN/HE dictionary + engine (RTL-first, `data-i18n`) |
| `site/legal.html` | Standalone bilingual privacy policy + terms |
| `site/img/logo.svg` | Favicon / wordmark badge |
| `captions_template.ass` | Standalone caption-style reference |
| `test_stock_helpers.py`, `tests/backend/` | Unit tests (no network, no Modal — AST-extracted) |
| `tests/frontend/` | Playwright UI tests |
| `test_api.py` | End-to-end API tests (upload → process → burn; costs GPU) |

---

## Design system

"Modern boho" — warm, organic, hand-crafted, RTL-first. All color tokens live
in `:root` in `site/app.css`; the whole app recolors from there.

- **Palette** — warm sand background (`#E8DFD3`) with a subtle paper grain,
  cream surfaces (`#F4ECE0`, never pure white / never pure black), muted
  earth accents: olive/sage primary (`#7E8E6A` / `#A3B196`), **terracotta**
  (`#C4703F`) as the sparing accent (CTA + logo), forest green foliage.
- **Type** — Frank Ruhl Libre for the Hebrew wordmark; Assistant (rounded
  humanist) for everything else. No mono, no heavy weights, no gradient text.
- **Shape & motion** — soft radii, warm-tinted soft shadows, no gradients, no
  glassmorphism. Thin-stroke olive line icons (no emoji in the chrome).
- **Decorative layer** (`.boho-bg`, fixed, behind content, RTL-anchored): two
  soft organic blobs plus a **scroll-grown monstera vine** — it draws down as
  you scroll and retracts on the way up (scrubbed by scroll position;
  disabled under `prefers-reduced-motion`).

---

## Cloud / Modal architecture

```
browser → POST /auth/login|register       → HMAC session token
       → POST /upload_chunk                → modal.Volume (per-chunk files)
       → POST /process                     → process_video.spawn() → call_id
       → GET  /process_poll/               → poll → {captions, video_key}
       → POST /burn                        → burn_captions_fn.spawn() → call_id
       → GET  /burn_poll/                  → poll → {output_key}
       → GET  /download/{key}              → stream video from Volume
       → POST /stock-broll[-clips]         → analyze_stock_broll / search_stock_clips
       → POST /hooks|/captions             → generate_hook/caption_options
       → GET  /jobs, DELETE /jobs/{key}    → history
       → /oauth/*  ,  POST /schedule       → per-user Metricool
```

Every volume key is namespaced `u{uid}__`; downloads/thumbnails/jobs/burn
enforce ownership. GET-media routes use a short-lived 1h media token, never the
session token.

**Key Modal functions**

| Function | Timeout | Resources | Purpose |
|----------|---------|-----------|---------|
| `process_video` | 1800s | L4 GPU | transcribe → Sonnet proofread → cut silences → optional Real-ESRGAN upscale |
| `burn_captions_fn` | 300s | CPU (ffmpeg) | burn captions + composite B-roll |
| `analyze_stock_broll` | 300s | CPU | Sonnet moments + Pexels/Pixabay + Haiku scoring |
| `search_stock_clips` | 120s | CPU | "find different clips" (next page) |
| `generate_hook_options` / `generate_caption_options` | — | CPU | Sonnet content |
| `schedule_post_fn` | — | CPU | Metricool MCP scheduling |
| `daily_usage_report` | cron 1/day | CPU | GPU-spend digest email (alerts over `USAGE_ALERT_THRESHOLD`) |
| `api` | 900s | CPU | ASGI router |

Stock B-roll uses a two-prompt split — a `broad_search_prompt` (recall,
Pexels/Pixabay) and a `strict_eval_prompt` (precision, Haiku scoring). The
Sonnet system prompt is `cache_control: ephemeral`. See `CLAUDE.md` for the
per-source and per-model swap guides.

### Swapping AI models

Model IDs are module-level constants at the top of `pipeline_core.py` — change
and redeploy, no other edits:

```python
SONNET_MODEL = "claude-sonnet-5"            # moment selection, hooks, captions, proofread
HAIKU_MODEL  = "claude-haiku-4-5-20251001"  # stock clip scoring
```

---

## Build / deploy

```bash
source .venv/bin/activate
modal deploy app_modal.py            # backend → https://yotamjacob--hebrew-video-pipeline-api.modal.run
npx vercel deploy --prod             # frontend (run from project root, NOT site/)
```

## Tests

```bash
source .venv/bin/activate
python -m pytest test_stock_helpers.py tests/backend/   # fast, no network/Modal
npx playwright test                                     # frontend UI suite
python test_api.py                                      # end-to-end (costs GPU — run only when needed)
```
