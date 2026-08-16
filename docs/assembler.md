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
- **Visual moments (2026-08-13):** (near-)speechless clips (<2 speech
  segments) get `_visual_segments` - ≤8 frames sampled, ONE Haiku vision call
  returns scene ranges + Hebrew descriptions (`assembler_vision` in cost
  tracking; division of labor is deliberate - Haiku DESCRIBES, the Sonnet
  story pass JUDGES; swap the describer via HAIKU_MODEL if descriptions feel
  flat). They enter the story prompt marked `[ויזואלי]`, may be woven as
  atmosphere/hook/transition moments (`visual: true` on the moment → the page
  tags it and drops the quote marks), are wordless by construction (never
  captioned), and make ALL-SILENT inputs analyzable (verified by production
  E2E on audio-less clips). Vision failure degrades to a speechless clip.
- **Captions (2026-08-13, solo-app - no pipeline handoff by user directive):**
  analyze runs whisper WITH word timestamps and persists the per-clip word
  transcript at `{key}_asm_words.json` (scratch, 48h sweep). Render (toggle
  `captions`, default on, `#capToggle` on the page) remaps kept windows onto
  the output timeline via the pure `_captions_for_windows` (unit-tested in
  `test_assembler_captions.py` - storyboard-order offsets, window clamping)
  and burns via the SHARED `build_caption_ass` (Heebo 48 reference px, same
  margins as burn_captions_fn) in a final subtitles pass. Missing transcript
  (expired scratch) degrades to a clean cut, never a failed render. Style
  controls / word-karaoke modes are a later knob - the words are already in
  the events.
- The analysis also returns `story` (≤2000 chars): the model's detailed
  narrative reading of the footage (who, what, arc, emotion, message),
  written to seed a voice-over script. Shown as card "2 · הסיפור" with a
  copy button before the storyboard; it is the input contract for Phase 3.

## Clips mode - long -> shorts (2026-08-16)

Same page, second mode (`#modeClips` in the upload card, single file, up to
90 min / 2GB): one long recording (podcast, lecture, long interview) ->
6-12 self-contained short-clip candidates, each with an honest virality
estimate. Built to beat OpusClip on CLIP QUALITY for Hebrew, deliberately
NOT on their moat items (reframe/tracking, diarization, teams/API, 25
languages - user directive 2026-08-16: don't build those).

| Piece | Where | Contract |
|---|---|---|
| Analyze | `analyze_story(keys, names, mode="clips")` - same GPU function, `timeout=1800`, input cap `CLIPS_INPUT_MAX_SECONDS=5400` | Transcription identical to story mode (words persisted at `_asm_words.json`), then `_pick_clips` instead of `_pick_moments`. Returns `{"mode":"clips","summary","candidates":[...],"clips"}`. |
| `_pick_clips` | two Sonnet passes | **Pass 1** (`assembler_clips_pick`): FULL segment transcript -> `MIN_CANDIDATES..MAX_CANDIDATES` (3..12) candidate segment ranges + a summary; the prompt encodes the rubric (hook in 3s, self-contained, payoff, ends on a strong line, diverse, no intros/ads/admin). **Pass 2** (`assembler_clips_score`, ONE call for all): each candidate's WORD-level transcript with one segment of context each side (`lo..hi`, initial proposal marked `<<< >>>`) -> precise `start/end`, `title`, on-screen `hook` (<=8 words), `quote`, and `virality` {hook, retention, emotion, clarity, shareability (0-10), score (0-99), reasoning, tip}. Pass-2 parse failure ships pass-1 trims (never a failed analysis). |
| `_snap_to_words` | pure, tested | Model trims snap to the nearest word start / word end within 0.6s inside `[lo, hi]`, then pad 0.12s before / 0.25s after. Out-of-bounds durations (<`CLIP_MIN_SECONDS`=8 or >`CLIP_MAX_SECONDS`+15) fall back to the pass-1 segment range. |
| `_virality_score` | pure, tested | Composite 1-99 = 0.5 x model holistic + 0.5 x weighted rubric (hook .30, retention .25, emotion/clarity/shareability .15 each) with a duration prior (-4 >60s, -8 >75s, -6 <12s). Candidates are sorted by it. It is an EDITORIAL estimate and the UI says so ("הערכה עריכתית, לא נבואה") - never market it as prediction. Tiers in the UI: >=70 forest, >=50 olive, >=30 amber. |
| Render | `render_story(..., tighten, hook_text, variant)` via `/assembler/render` | One render per picked clip (the page runs 2 in flight): `segments=[[clip,start,end]]`, `tighten` -> `_tighten_windows` (pure, tested: words closer than 0.6s merge into runs, 0.18s pad, clamped to the window; wordless windows pass through) splits the window on silence gaps BEFORE the canvas/concat step; `hook_text` (<=120) rides the SAME ASS pass as captions via `build_caption_ass`'s hook box (`start_seconds 0.2`, `duration min(4.5, len-0.5)`) - the burn happens if there are caption events OR a hook; `variant` (`_SAFE_VARIANT_RE`, <=24 chars, page sends `c{n}_{batch6}`) suffixes the output key `{key}_{variant}_out.mp4` so N clips from ONE source each get their own History row. Defaults (`False`, `""`, `""`) keep the story render byte-identical. |
| Page | `site/assembler.html` | `MODE_COPY` drives drop/hint copy + file limits per mode; `renderCands` draws `.cand` cards (CSS grid: score+thumb+title on row 1, `.c-body` under the title on wide screens and full-width on <=520px - screenshot-verified both); per-card `.hook-input` (editable, prefilled from the model), `.pick-box`, rubric `.bars`, `<details class="c-why">` reasoning + tip. Options: `#clipCapToggle`, `#clipTightenToggle`, `#clipHookToggle` (all default on). `#renderClipsBtn` batch-renders picked clips into `#outs` tiles (video + download link per clip). Story-mode cards are hidden in clips mode and vice versa. |

Verified E2E on production 2026-08-16 with a synthetic 2-min Hebrew TTS
"podcast": 6 candidates in 160s, trims skipped the intro/outro exactly,
Hebrew hooks/reasoning/tips sensible; a rendered clip showed the hook box
for the first ~4.5s + synced captions and landed in History under
`_c1_..._out.mp4`. Analyze cost = 2 Sonnet calls (~30-40k input tokens for
a 60-min transcript) - price it before linking the page.

Tests: `tests/backend/test_assembler_clips.py` (pure helpers executed +
contracts), `tests/frontend/assembler_clips.spec.js`.

Not built (by decision): reframe 16:9->9:16, speaker diarization, URL
import, brand kit, teams/API. Natural next steps: keyword highlight in the
clip captions, per-clip Metricool scheduling from the results tiles,
pricing + linking the page.

## Roadmap (agreed 2026-08-13)

Phase 2 (multi-clip) SHIPPED same day - up to 5 clips, cross-clip storyboard
with clip badges, canvas-normalized render. Phase 3 record-and-duck SHIPPED
same day: VO card (`#voCard`, card 4) records via MediaRecorder (audio/mp4 on
Safari, webm elsewhere) or accepts an audio-file upload (≤100MB); the blob is
chunk-uploaded under its own key at render time and rides the render as
`vo_key` (validated + uid-prefixed like every key). `render_story` mixes it as
a VIDEO-COPY pass: `[1:a]aformat,apad[vo]; [0:a][vo]sidechaincompress
threshold=0.05 ratio=8 attack=20 release=400[bg]; [bg][vo]amix duration=first
normalize=0` (filtergraph validated on synthetic media 2026-08-13) - the
original audio ducks under the narration; a broken/missing VO ships the
un-narrated cut. Remaining Phase-3 ambition: VO-FIRST assembly (cut footage
to match narration beats) - not started.

Tests: `TestAssemblerRoutes` in `tests/backend/test_asgi.py` (conftest's
`_MODAL_FILES` includes `assembler_fns.py`), `tests/frontend/assembler.spec.js`
(full flow with clock-driven polls).
