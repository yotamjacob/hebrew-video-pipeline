# Hebrew Video Pipeline — Claude Guide

## Module Map

| File | Purpose |
|------|---------|
| `hebrew_video_pipeline.py` | Local CLI — 6-step pipeline (extract → enhance → transcribe → segments → ASS → render) |
| `app_modal.py` | **Deploy entrypoint** — imports all backend modules + the `api()` ASGI router (all HTTP routes) |
| `pipeline_core.py` | Modal app/images/volumes, model constants, pure helpers (security, RTL text, rate limiting, `_poll_fn_call`) |
| `pipeline_fns.py` | `process_video` (GPU), `burn_captions_fn`, `burn_hook_fn`, job history + retention pruning |
| `stock_helpers.py` | Pure stock-footage helpers: Pexels/Pixabay fetch, frame sampling, `score_clips`, `add_clip_window` |
| `broll_fns.py` | Veo generation + `analyze_broll`, `analyze_stock_broll`, `search_stock_clips`, `_process_moment` |
| `content_fns.py` | `generate_hook_options`, `generate_caption_options` |
| `metricool_fns.py` | Metricool OAuth store, MCP client, `schedule_post_fn` |
| `site/index.html` | Vercel frontend markup — "פייפליין" branding (catchphrase: עריכות וידאו בלחיצת כפתור); tabs: **Create** (upload/process/download) and **History**; no framework |
| `DESIGN_LEGACY.md` | The pre-2026-07 Yogalina design, preserved at tag `design-yogalina-classic` — how to revert |
| `site/app.js` | All frontend logic (upload, polling, editor, burn, debug panel, stats tab, scheduling). Metricool **connection** is an account-level topbar chip (`#metricoolChip`, `refreshMetricoolChip`) surfaced up front — when connected the chip itself is the disconnect button (confirm modal → `POST /oauth/disconnect`); **scheduling** is a shared modal (`#scheduleOverlay`, `openScheduleModal(video)`) opened from BOTH the post-burn `#openScheduleBtn` and each History row's 📅. Suggest-caption only shows for fresh videos (needs the in-memory transcript, not stored per job). |
| `site/i18n.js` | EN/HE dictionary + engine: `t(key, vars)`, `data-i18n` attrs, `setLang` (sets `dir=rtl`, persists `hebpipe_lang`, fires `langchange`); toggle button `#langToggle` in hero |
| `site/app.css` | All frontend styles |
| `site/sw.js` | Service worker — background job polling only (no asset caching) |
| `site/legal.html` | Standalone bilingual privacy policy + terms (HE default, EN toggle, shares `hebpipe_lang`); linked from every footer (`footer.legal`); served as a real file, bypassing the SPA rewrite |
| `site/vercel.json` | SPA rewrite rule (all routes → index.html) |
| `captions_template.ass` | ASS subtitle format reference and style examples |
| `requirements.txt` | `faster-whisper`, `requests` |
| `README.md` | Full usage docs, architecture, "how to add a stock source", "how to swap models" |
| `TODO.md` | Active feature roadmap |
| `test_api.py` | End-to-end API tests (upload → process → burn) — costs GPU time, run only when asked |
| `test_stock_helpers.py` | Unit tests for stock helpers — runs locally, no network or Modal |
| `tests/backend/`, `tests/frontend/` | pytest (AST-extraction, no Modal import) + Playwright suites |

**Token tip:** read only the module you're changing. Backend modules import shared
names from `pipeline_core`; new backend files must be added to
`add_local_python_source(...)` on **all three** images in `pipeline_core.py`
(`image` = full ML, `burn_image` = ffmpeg+fonts, `light_image` = ffmpeg+anthropic
for hooks/captions/stock B-roll and the `api()` router — boots in seconds).

## Pipeline Steps (both CLI and Modal share this logic)

1. `extract_audio()` — ffmpeg extracts 48 kHz mono WAV
2. `enhance_*()` — ElevenLabs API / Adobe Podcast manual / skip
3. `transcribe()` — faster-whisper (`ivrit-ai/whisper-large-v3-turbo-ct2`), word-level timestamps. On Modal, `process_video` then runs `proofread_words` (Sonnet 5) over the word list to fix Hebrew ASR typos — word count/order preserved so timestamps stay valid; best-effort (any failure keeps the raw transcript; local CLI has no proofread pass)
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

**Modal warmup pattern** — `GET /warmup` fires `process_video` with `filename="__warmup__"` asynchronously to pre-warm the L4 GPU container. The real `/process` endpoint benefits on the next call.

**Whisper model cache** — first run downloads ~1.5 GB to the local faster-whisper cache; Modal uses a persistent volume so it survives redeployments.

**Web API base** — `https://yotamjacob--hebrew-video-pipeline-api.modal.run` (hardcoded in `site/app.js`).

**Font size auto-scaling** — the pipeline reads video resolution and adjusts `--font-size` and `--margin-v` proportionally so captions look consistent across 1080p and 4K.

**Prompt caching** — the Sonnet moment-selection system prompt is marked `cache_control: ephemeral`. The ~5-6k-token stable prefix is cached (comfortably above the model's minimum cacheable size); only the transcript (user message) changes per call.

**Sonnet 5 API constraints** — `temperature`/`top_p`/`top_k` are REJECTED (400) on `claude-sonnet-5`; all Sonnet call sites pass `thinking={"type": "disabled"}` instead (omitting `thinking` would run adaptive thinking, eating into `max_tokens`). Variety in hooks/captions comes from prompting, not temperature.

**`_process_moment` is a plain function by design** — it runs in a ThreadPoolExecutor inside `analyze_stock_broll`'s container. Do NOT add `@app.function` to it: Modal Function objects are not directly callable, which silently broke all stock B-roll clip fetching until 2026-07-02.

**Stock search two-prompt split** — `broad_search_prompt` is sent to Pexels/Pixabay (optimized for recall); `strict_eval_prompt` is sent to Haiku vision scoring only (optimized for precision). Never swap them — the stock libraries can't handle the strict prompt and return zero results.

**Mobile upload resilience (design, 2026-07-05)** - chunk POSTs use **XHR, not fetch** (`_postChunkBytes`): fetch reports no upload progress (bar frozen at 0% until a whole 5 MB chunk lands) and has no timeout (a stalled connection hangs forever). XHR gives byte-level `upload.onprogress` (summed across concurrent chunks via `loadedByChunk[]`) and a **stall watchdog** - abort+retry only when NO bytes move for `CHUNK_STALL_MS` (20 s; overridable via `window.__CHUNK_STALL_MS` for tests), so a slow-but-moving upload is never killed, only a truly dead one. Retry policy: a chunk gives up only after 6 network failures **that happen while the page is VISIBLE** - failures coinciding with backgrounding (the OS killing in-flight XHRs) are detected via a `_hiddenEpoch` counter (bumped on every `visibilitychange`-to-hidden; captured before send, compared at failure) and do NOT count, so an upload interrupted by minimizing the browser repeatedly never exhausts its budget. Retries also park on `_whenVisible()` while hidden. `MAX_TOTAL=60` is an absolute backstop. Server 408/429/5xx bounded to 4; everything else (401, etc.) terminal immediately. NOTE: a killed in-flight chunk re-sends fully (no server-side resume), so its progress-bar slice resets - completed chunks stay done. `pollForJSON` likewise treats pure network errors as non-fatal until the deadline. Every chunk reads `slice.arrayBuffer()` BEFORE sending: on Android a gallery-picked file can become unreadable (Google Photos cloud-sync / file changed = ERR_UPLOAD_FILE_CHANGED), which otherwise looks like "Failed to fetch" forever - the explicit read turns it into a terminal "select the file again" error. The error card shows a stage+raw-error detail line and an expandable console log (`consoleLog` ring buffer) for mobile bug reports.

**Chunk upload tuning & resume (2026-07-05)** - `CHUNK_SIZE=2 MB`, `UPLOAD_CONCURRENCY=4` (was 5 MB / 6): smaller chunks stick faster on slow uplinks and cap in-flight (losable-on-background) bytes at 8 MB; throughput is uplink-bound so this doesn't slow healthy connections. **Resume**: completed chunk indices are saved to `localStorage` keyed by a file signature (`name_size_lastModified`, `UPLOAD_RESUME_TTL=24h` < server's 48h scratch prune); `chunkedUpload` reuses the saved upload key and skips already-sent chunks, so a "try again" (same File) or reload+re-pick resumes instead of restarting. Cleared on full success. A `#uploadNote` ("keep the window open while uploading; you can switch away once processing starts") shows during the upload phase only - uploads run on the device via JS and CANNOT progress while the tab is backgrounded (the OS freezes it); processing/burn are server-side and survive backgrounding.

**Chunk streaming** — `upload_chunk` writes each chunk to a separate Volume file. `process_video` reassembles them with `shutil.copyfileobj` directly to the tempdir, one chunk at a time. This keeps RAM usage flat regardless of video size.

**Rate limiting scope** — `_check_rate_limit` uses an in-memory dict per Modal container instance (10 req/60 s per IP). Multiple concurrent container instances each have their own limit — effective limit is `10 × N_instances` per minute. Sufficient for abuse prevention; not a per-user quota.

**Job history & retention** — every successful `burn_captions_fn` records `{name, ts, size, duration}` in the `hebpipe-jobs` modal.Dict (key = `…_out.mp4` output key) and runs `prune_volume()`: burned outputs deleted after `JOB_RETENTION_DAYS` (30), scratch files (`_src.mp4`, `_cut.mp4`, chunks) after `SCRATCH_RETENTION_HOURS` (48). Nothing else deletes volume files — the old delete-on-download and delete-after-schedule behaviors are gone. History tab uses `GET /jobs`, `DELETE /jobs/{key}` and the existing `/thumbnail/` + `/download/` routes. `/thumbnail` caches its JPEG as `<key>.jpg` on the volume (first hit ~5 s ffmpeg, then instant); `prune_volume` keeps the `.jpg` while its video is protected and deletes them together.

**Enhance video modes** — `enhance_video` on `/process` is `none | filters | esrgan` (mutually exclusive selector in Options). `filters` = `ENHANCE_VIDEO_VF` ffmpeg chain inside the existing render. `esrgan` = Real-ESRGAN `realesr-general-x4v3` as a separate tracked stage (`upscale`) after the cut: SRVGGNetCompact implemented INLINE with plain torch (basicsr is deliberately avoided — it imports `torchvision.transforms.functional_tensor`, removed in torchvision 0.17+); weights cached on the model volume; frames piped raw through ffmpeg; output up to 4× capped at 2160 short side — a true-4K master (`_upscale_target`) — plus `cas=0.4` contrast-adaptive sharpening on delivery frames; ~0.5 s/frame fp16 on the L4 (`process_video` timeout is 1800s for this). The GPU image installs CUDA torch (cu124) for this pass — do not switch it back to the CPU wheel.

**Real checklist progress** — `process_video` writes real stage transitions to the `hebpipe-progress` modal.Dict (key = upload key, value = `{stage, done:{step: secs}, ts}`; stages: `enhance`, `cut`). `GET /process_poll/{id}/?key=` includes it as `progress` while running, and the final result carries `step_times`. The site checklist shows only real numbers — never estimated splits (the old T4-calibrated fake timers are gone). Entries are popped on completion; stale ones pruned by `prune_volume()` after 6 h.

**Auth & per-user data isolation** — username+password accounts (`hebpipe-users` Dict, PBKDF2-600k hashes), stateless HMAC session tokens signed with `AUTH_SECRET` from the `hebpipe-auth` Modal Secret (30-day expiry; `INVITE_CODE` in the same secret gates self-registration). The uid comes ONLY from the verified token. Every volume key is namespaced `u{uid}__` by the router; workers derive output-key prefixes from their input keys (`_UID_PREFIX_RE`); `/download`, `/thumbnail`, `/jobs`, `/burn` etc. enforce `_owned_key`; every spawn records `call_id → uid` in `hebpipe-calls` and all `*_poll`/`/cancel` routes verify it. Public (tokenless) routes: `/`, `/auth/register`, `/auth/login`, `/oauth/callback`, `/media/*` (Metricool's ingester fetches it; keys are unguessable). GET requests may pass the token as `?token=` — but NOT the session token: they pass a short-lived **media token** (see below). Frontend: `apiFetch()` adds the Bearer header and drops to the login view on 401; token in localStorage. Metricool OAuth connection remains global (single brand account), not per-user. Supabase migration path: swap `_verify_token` for provider-JWT verification mapping to the same uid.

**Media token (GET-media-scoped)** — the 30-day session token must never ride in a query string (logs/history leak). `_sign_media_token`/`_verify_media_token` in `pipeline_core.py` mint a 1h token (format `m.<uid>.<exp>.<sig>` — 4 parts, disjoint from the 3-part session token so the verifiers can't be confused). `GET /auth/media-token` (session-authed) returns one; the site caches it (`mediaToken` in `app.js`, refreshed every 45 min via `refreshMediaToken()` called from `showApp()`), and `_withToken()` uses it, falling back to the session token only until the first one arrives. The session guard accepts a media token via `?token=` ONLY on GET requests — never for mutations.

**Email layer (Resend)** — accounts collect an email at registration (`_EMAIL_RE`) and get a verification email; verification is a **nudge, not a gate** (`email_verified` on the record; `/auth/me` returns `email` + `email_verified`; the site shows a dismissible banner). Routes: `/auth/verify` (public GET, HTML page), `/auth/request-verification` (authed POST, also sets/updates the address), `/auth/forgot` (public POST, always 200 — never reveals account existence), `/auth/reset` (public POST). Reset/verify links carry a scoped one-time token (`_sign_scoped_token`/`_verify_scoped_token`, scope `verify`|`reset` baked into the HMAC message so they're not interchangeable; verify TTL 7d, reset TTL 1h). `_send_email` posts to Resend and is **best-effort** — no `RESEND_API_KEY` just logs and skips, so sign-up never fails on mail. Secret keys needed in `hebpipe-auth`: `RESEND_API_KEY`, `EMAIL_FROM` (verified-domain sender), optional `SITE_URL` (reset links; defaults to the Vercel alias). `email:{addr}` reverse index in `hebpipe-users` powers forgot-by-email.

**Video quota (free tier)** — every `/process` spawn consumes one credit. `_quota_state`/`_quota_allows` in `pipeline_core.py` (`DEFAULT_VIDEO_LIMIT = 5`; `videos_used`/`video_limit` live on the `hebpipe-users` record; `video_limit = -1` = unlimited). Admins bypass: `role: "admin"` on the record OR username in the optional `ADMIN_USERS` env var (hebpipe-auth secret). `/auth/me` returns role + usage (drives the site's quota pill + Admin tab visibility); `/admin/users` + `/admin/limit` (admin-only) power the Admin tab. A 402 `limit_reached` is mapped client-side to a friendly i18n message that never names a person. Increment happens at spawn (no refund on failed jobs — bump the user's limit manually if needed).

**Key validation pattern** — `_SAFE_KEY_RE` and `_SAFE_DOWNLOAD_KEY_RE` at module level validate upload keys and download keys before any filesystem access. Keys must be `[a-zA-Z0-9_\-]` (plus `.` for downloads), max 128 chars.

**Quota is race-proof via unique keys** — the `videos_used` counter on the user record is display-only; the authoritative consumed-credit count is `_count_quota_used(quota_store, uid)`, a scan of unique `{uid}:{call_id}` keys in the `hebpipe-quota` Dict written one-per-spawn. This avoids the lost-update race a mutable counter has under concurrent `/process` calls (modal.Dict has no atomic incr/CAS). The gate uses `max(record_used, scan_count)` so the record is a floor (existing usage survives migration). Overshoot is bounded to genuine concurrency, never unlimited.

**Brute-force throttle** — `_check_rate_limit` is only a per-container burst cap. Real password/invite protection is `_throttle_allowed`/`_throttle_record_fail`/`_throttle_clear` over the persistent `hebpipe-throttle` Dict (8 fails / 15 min rolling window, cross-container). Login throttles on `login:{username}` AND `loginip:{ip}`; registration on `invite:{ip}`. Fails **open** if the store is unavailable. Cleared on success.

**Metricool is per-user** — tokens are namespaced `tokens:{uid}` in the `metricool-oauth` Dict; the PKCE state entry carries `{verifier, uid}` so the public `/oauth/callback` attributes the connection. Each user's brand/blog id is discovered lazily in `schedule_post_fn` via MCP `tools/list` (tool name matched by containing "brand"/"blog", not hardcoded) and cached on the token record — **never** falls back to a shared brand (`no_brand` error instead). `_mcp_open`/`_mcp_parse` are the shared MCP handshake. NOTE: brand auto-discovery is unverified against the live Metricool MCP — confirm with a real connected account.

**B-roll item validation (SSRF/IDOR guard)** — `/burn` composites/fetches each B-roll item server-side, so `_broll_item_safe` (in `pipeline_core.py`) validates every item in the route BEFORE spawn, and `burn_captions_fn` re-checks at its sinks (defense in depth). Rules mirror the worker's precedence (a `video_key` wins over a URL): a local clip's `video_key` must match `_BROLL_KEY_RE` = `^broll_[0-9a-f]{32}\.mp4$` — NOTE broll clips are stored `broll_<uuid>.mp4` on the shared volume and are NOT `u{uid}__`-namespaced, so `_owned_key` can't gate them; the strict format regex is what blocks `../` traversal and cross-tenant/other-file reads. A remote clip's `download_url`/`preview_url` must pass `_is_allowed_broll_url` (https + host in `_BROLL_URL_HOSTS`: pexels.com, pixabay.com, vimeocdn.com, vimeo.com) so the worker can't be pointed at internal/metadata endpoints.

## Model Constants

Defined at the top of `pipeline_core.py` — change and redeploy, no other edits needed:

```python
SONNET_MODEL = "claude-sonnet-5"             # moment selection, hooks, captions, video context
HAIKU_MODEL  = "claude-haiku-4-5-20251001"   # clip scoring (_score_clips)
OPUS_MODEL   = "claude-opus-4-7"             # Veo B-roll analysis (dead code — VEO disabled)

TRANSCRIPT_ANALYSIS_MODEL = "gemini-2.5-flash"
VIDEO_GENERATION_MODEL    = "veo-3.0-generate-001"
```

## Conventions

- Both `hebrew_video_pipeline.py` (CLI) and `pipeline_fns.py` (Modal) contain copies of the core dataclasses (`Word`, `KeepSegment`) and pipeline helpers — keep them in sync when changing shared logic.
- **Stock search helpers** (`fetch_pexels`, `fetch_pixabay`, `score_clips`, `add_clip_window`) live in `stock_helpers.py`. Both `analyze_stock_broll` and `search_stock_clips` call them — do not re-inline them.
- Pass `http_session` (a `requests.Session`) to `fetch_pexels`/`fetch_pixabay` when making multiple calls in one function invocation — reuses TCP connections.
- Backend unit tests never import the Modal modules — they AST-extract functions from source (`tests/backend/conftest.py` concatenates all backend `.py` files into `MODAL_SRC`). Renaming/moving a backend file means updating the file lists in `conftest.py`, `test_ass_generation.py`, and `test_stock_helpers.py`.
- Temp files land in `hebpipe_<timestamp>/`; auto-deleted unless `--keep-tmp` is passed.
- Output files are named `<input_stem>_edited.mp4` by default.
- The site is pure HTML/CSS/JS — no build step, no bundler. `index.html` loads `app.css` + `app.js`.
- Run `python -m pytest test_stock_helpers.py tests/backend/` before deploying backend changes; run `npx playwright test` after `site/` changes.
