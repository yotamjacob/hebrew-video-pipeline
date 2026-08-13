# Story Assembler (hidden beta) - /assembler

Phase 1 of the story-assembly feature (user request 2026-08-13, born from a
creator's pain: turning long raw footage - interviews, business reviews -
into a short story-driven Instagram cut). **The main pipeline is deliberately
untouched**: the assembler is a standalone page + its own backend module, and
every shared surface is reused by CALLING it, never by modifying it.

## Architecture

| Piece | Where | Notes |
|---|---|---|
| Page | `site/assembler.html` (served at `/assembler` via the ROOT vercel.json rewrite) | Standalone, no app.js, boho tokens inline, `noindex`, NOT linked from any UI (deliberate - hidden beta). Auth = reads the shared `hebpipe_token` from localStorage; signed-out users get a gate pointing to `/`. |
| Backend | `assembler_fns.py` (flat at repo root, registered on all three images in `pipeline_core`) | `analyze_story` (GPU L4, max_containers=2 so the beta can never crowd out paying /process jobs): assemble chunks → 16k wav → faster-whisper SEGMENT-level (same model/VAD/gates as the pipeline, no word timestamps) → Sonnet picks 3-8 golden moments as SEGMENT RANGES (from_seg/to_seg - cuts always snap to speech boundaries) with role hook/story/gold/closing + quote + reason → 240px jpeg thumbnail per moment (base64 in the poll payload). `render_story` (burn_image, cpu): per-window accurate re-encode (x264 CRF 18) → concat-demuxer copy → `{key}_out.mp4`. |
| Routes | `app_modal.py` api(): `/assembler/analyze[-poll]`, `/assembler/render[-poll]` | Additive only. Keys are `_SAFE_KEY_RE`-validated and uid-prefix-scoped (`{uprefix}{key}`); polls enforce `_call_owned`; render segments bounded (1-16 windows, ≥0.5s each, ≤600s total). |
| Upload | existing `/upload_chunk` (unchanged) | The page chunk-uploads with `X-Upload-Key`/`X-Upload-Index` exactly like app.js. No pending_store registration → the deferred-spawn path never fires for assembler uploads. |
| Output | standard History conventions | `_out.mp4` key + `_record_job(..., notify=False)` → History row, /media preview, /download, thumbnails, 30-day retention all work unchanged. |

## Contracts / gotchas

- **NO credits are charged** while the page is unlinked (analyze AND render).
  Before making the page public: price the render spawn like /process does
  (`_credit_cost` + `_charge_credits`) and add a quota check.
- The storyboard ORDER is the render order (may differ from chronological) -
  `render_story` cuts windows in the order received.
- Moments snap to Whisper segment boundaries by construction (the model picks
  segment ids, never free timestamps) - keeps every cut on a speech edge.
- `_resolve_source` caches assembled chunks as `{key}_src.mp4` (same
  convention as process_video); the src is scratch (48h retention), so
  analyze→render must happen within that window.
- analyze errors are soft: `{"error": "too_short"|"no_speech"|"no_moments"}`
  with Hebrew messages mapped client-side.

## Roadmap (agreed 2026-08-13)

Phase 2: multi-clip projects (content map per clip, cross-clip EDL, same
storyboard UI). Phase 3: voice-over (in-page recording, ffmpeg
sidechaincompress ducking, VO-first assembly).

Tests: `TestAssemblerRoutes` in `tests/backend/test_asgi.py` (conftest's
`_MODAL_FILES` includes `assembler_fns.py`), `tests/frontend/assembler.spec.js`
(full flow with clock-driven polls).
