# Story Assembler (hidden beta) - /assembler

Phases 1+2 of the story-assembly feature (user request 2026-08-13, born from a
creator's pain: turning long raw footage - interviews, business reviews -
into a short story-driven Instagram cut). **The main pipeline is deliberately
untouched**: the assembler is a standalone page + its own backend module, and
every shared surface is reused by CALLING it, never by modifying it.

## Architecture

| Piece | Where | Notes |
|---|---|---|
| Page | `site/assembler.html` (served at `/assembler` via the ROOT vercel.json rewrite) | Standalone, no app.js, boho tokens inline, `noindex`, NOT linked from any UI (deliberate - hidden beta). Auth = reads the shared `hebpipe_token` from localStorage; signed-out users get a gate pointing to `/`. |
| Backend | `assembler_fns.py` (flat at repo root, registered on all three images in `pipeline_core`) | `analyze_story(upload_keys, filenames)` (GPU L4, max_containers=2 so the beta can never crowd out paying /process jobs): accepts 1-5 clips (≤40 min total raw) - assemble chunks per clip → 16k wav → faster-whisper SEGMENT-level (same model/VAD/gates as the pipeline, model loaded ONCE for all clips) → ONE Sonnet pass over all clip-tagged transcripts weaves 3-8 golden moments ACROSS clips as SEGMENT RANGES (`clip` + from_seg/to_seg - cuts always snap to speech boundaries) with role hook/story/gold/closing + quote + reason → 240px jpeg thumbnail per moment from its own clip. `render_story(upload_keys, [[clip,start,end],...])` (burn_image, cpu): every part is scaled+padded onto the FIRST kept window's clip dimensions at fps=30 + 48k stereo aac (mixed resolutions/orientations across clips would otherwise break concat), x264 CRF 18 per part → concat-demuxer copy → `{first_key}_out.mp4`. Both accept the Phase-1 single-key / [start,end] shapes for back-compat. |
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
- The analysis also returns `story` (≤2000 chars): the model's detailed
  narrative reading of the footage (who, what, arc, emotion, message),
  written to seed a voice-over script. Shown as card "2 · הסיפור" with a
  copy button before the storyboard; it is the input contract for Phase 3.

## Roadmap (agreed 2026-08-13)

Phase 2 (multi-clip) SHIPPED same day - up to 5 clips, cross-clip storyboard
with clip badges, canvas-normalized render. Phase 3: voice-over (in-page
recording, ffmpeg sidechaincompress ducking, VO-first assembly).

Tests: `TestAssemblerRoutes` in `tests/backend/test_asgi.py` (conftest's
`_MODAL_FILES` includes `assembler_fns.py`), `tests/frontend/assembler.spec.js`
(full flow with clock-driven polls).
