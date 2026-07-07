# פייפליין · Pipeline

Hebrew video editing at the click of a button. Upload a raw Hebrew talking
video and get back a polished cut: word-level Hebrew captions burned in,
silences trimmed, audio cleaned, optional AI upscale, attention hooks, stock
B-roll suggestions, and one-click scheduling to social platforms.

Two ways to run it:
- **Web app** — a branded, RTL-first, Hebrew UI (accounts, upload, edit,
  download, schedule). Modal GPU backend + Vercel static frontend.
- **Local CLI** (`hebrew_video_pipeline.py`) — the core edit pipeline on your
  own machine, no account needed.

The core idea in both: silence gaps are cut using **word-level timestamps**
(not dB thresholds), so words are never clipped, and there's exactly **one**
final ffmpeg re-encode, so quality stays high.

---

## Web app features

- **Accounts & quota** — username/password (PBKDF2, stateless HMAC sessions,
  invite-gated registration, email verify + password reset via Resend). Free
  tier is 5 videos; admins are unlimited. Per-user data isolation.
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
| `hebrew_video_pipeline.py` | Local CLI orchestrator (6-step pipeline) |
| `app_modal.py` | Modal deploy entrypoint — imports backend modules + the `api()` ASGI router (all HTTP routes) |
| `pipeline_core.py` | Modal app/images/volumes, model constants, security/RTL/auth/quota/throttle helpers |
| `pipeline_fns.py` | `process_video` (GPU), `burn_captions_fn`, `burn_hook_fn`, job history + retention, daily spend digest, daily metadata backup (`backup_dicts`/`restore_dicts` → R2/B2) |
| `stock_helpers.py` | Pure stock helpers: Pexels/Pixabay fetch, frame sampling, `score_clips`, `add_clip_window` |
| `broll_fns.py` | `analyze_stock_broll`, `search_stock_clips`, `_process_moment` (Veo is dead code) |
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

## Local CLI

### Setup

```bash
# 1. ffmpeg (Homebrew ffmpeg-full preferred — it includes libass for burning subs)
brew install ffmpeg           # macOS  ·  apt install ffmpeg on Debian/Ubuntu

# 2. Python deps
pip install faster-whisper requests
# first run downloads the ivrit-ai model (~1.5 GB) and caches it locally

# 3. A Hebrew-friendly font installed system-wide (Rubik / Heebo / Assistant),
#    or pass --font "DejaVu Sans"

# 4. (optional) automated audio cleanup
export ELEVENLABS_API_KEY=sk_your_key_here
```

### Usage

```bash
python hebrew_video_pipeline.py input.mp4                 # manual enhance (Adobe Podcast prompt)
python hebrew_video_pipeline.py input.mp4 --enhance elevenlabs   # fully automated
python hebrew_video_pipeline.py input.mp4 --enhance skip         # source already clean
python hebrew_video_pipeline.py input.mp4 -o out.mp4 --min-silence 0.4 --padding 0.15
python hebrew_video_pipeline.py input.mp4 --font Heebo --font-size 72 --max-chars 32
```

Note: the CLI does not run the Sonnet proofread pass — that's Modal-only.

### Tuning

| If you see...                          | Adjust...                             |
|----------------------------------------|---------------------------------------|
| Words chopped at cut points            | `--padding 0.3`                       |
| Cuts feel too choppy                   | `--min-silence 0.8`                   |
| Captions too small / big               | `--font-size`                         |
| Captions wrap too early / late         | `--max-chars`                         |
| Quality looks soft                     | `--crf 15`                            |

### Quality preservation

Exactly **one** re-encode: the final ffmpeg pass trims segments
(`trim`/`atrim`), concatenates, and burns captions in a single
`filter_complex` — H.264 CRF 18 `preset slow` (visually lossless), audio from
the *enhanced* WAV encoded once to 192 kbps AAC.

### Pipeline steps

```
probe_video()           → (width, height, duration)
extract_audio()         → raw.wav
enhance_*()             → clean.wav   (elevenlabs / manual / skip)
transcribe()            → List[Word]  (ivrit-ai Whisper, word-level)
compute_keep_segments() → List[KeepSegment]
generate_ass()          → captions.ass (timestamps remapped to the cut timeline)
final_render()          → output.mp4 (single ffmpeg pass)
```

Swap the transcription backend by replacing `transcribe()` — the rest of the
pipeline only needs a `List[Word]` with `start`, `end`, `text`.

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
| `burn_hook_fn` | 300s | CPU (ffmpeg) | burn the opening hook card |
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
HAIKU_MODEL  = "claude-haiku-4-5-20251001"  # clip scoring
TRANSCRIPT_ANALYSIS_MODEL = "gemini-2.5-flash"   # (Veo path — currently disabled)
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
