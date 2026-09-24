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
| Routes | `app_modal.py` api(): `/assembler/analyze[-poll]`, `/assembler/render[-poll]`, `/assembler/social-caption[-poll]` (2026-09-24) | Additive only. Keys are `_SAFE_KEY_RE`-validated and uid-prefix-scoped (`{uprefix}{key}`); polls enforce `_call_owned`; render segments bounded (1-16 windows, ≥0.5s each, ≤600s total). |
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

**Field fixes (2026-08-16, same day):** (a) the page's poll cap was a flat
200 x 3s = 10 min - a real 61-min podcast finished server-side in 632s and
the page had already shown a bare "הניתוח נכשל"; clips-mode analyze now
polls up to 900 ticks (45 min), story 400, with an elapsed-time counter
and a specific timeout message. (b) Pass 2 in ONE call for 9-12
candidates x 60-160s of word text overran the answer budget on the 1-hour
source and the whole rubric silently fell back to pass-1 (score 1, empty
hooks): pass 2 is now BATCHED 4 candidates per call (`_refine_batch`,
`PASS2_BATCH=4`, max_tokens 8000), the answer is parsed with
`_salvage_objects` (raw_decode every `{...}` carrying an `id` - survives
truncation / fences / one bad item), a failed batch only unscores ITS
candidates (`score: null`, `scored: false` - the UI shows "-" and a soft
note; they still render), and `issues` rides the result for diagnostics.
(c) Pass 1 sometimes proposes 2-3 min ranges: `_clamp_end` pulls the end
back to the last segment edge within `CLIP_MAX_SECONDS+10`. (d) The page
uploads via **R2 direct first** (`uploadFile` -> `r2Upload`: `/upload_r2/init` -> 4 parallel presigned part PUTs (XHR, ETag required, 35s stall watchdog, one retry per part) -> `/upload_r2/complete`, which lands the object as `chunk_0000` and spawns NOTHING because the assembler never registers a pending job - `_resolve_source` globs it up unchanged), falling back to `chunkUpload` (4 chunks in flight, same key) on any R2-specific failure (init 503/unreachable, no readable ETag, 4xx part) - the same fallback contract as app.js. Measured on production: the 527MB hour-long file landed in 163s at 29.5 Mbps vs ~25 min through the chunk path; an R2-landed file analyzed E2E fine. Specs stub `/upload_r2/init/` with 503 by default; the R2 happy path + the CORS-fallback path have their own tests in `assembler_clips.spec.js`. Verified
on the real 61-min file via an ephemeral `modal run` of `_pick_clips`:
8 clips, all scored 51-73, longest 77s, no issues, ~200s.

**Short sources + volume freshness (2026-08-16, evening):** a 1-minute
source 500ed twice. (1) `_pick_clips`' empty-candidates early return was a
2-tuple (analyze unpacks 3) - now `(summary, [], ["pass 1: no valid
candidates"])`, and pass 1 asks for 1-2 clips when the recording is short
(`want_lo/want_hi` scale with duration) instead of forcing 3-12. (2)
"No upload found" right after an R2 upload: `_r2_land_on_volume` never
committed (the /process spawn in the same request masked it) and the
assembler workers never `reload()`ed - a warm container kept a stale view.
Both fixed: commit after landing, `tmp_vol.reload()` at the top of
`_resolve_source`. Verified: two back-to-back 60s runs -> 3 scored clips.

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

## Branding: intro / outro / fade / watermark (2026-08-16)

Card "2 · מיתוג" (`#brandCard`, shown once an analysis is ready, both
modes; later cards renumbered 3-6). Everything is OPTIONAL and every input
degrades to "skipped" in the worker - the cut always ships.

| Piece | Where | Contract |
|---|---|---|
| Intro / outro | `render_story(..., intro_key, outro_key)` | User clips uploaded lazily at render time through `uploadFile` (R2 first) and cached per file (`brand.keys`) - a batch of N clips uploads each asset ONCE. First `INTRO_OUTRO_MAX_SECONDS=20` of each, normalized onto the body's canvas via the same `_encode_part` (scale+pad, 30 fps, aac 48k stereo; a silent clip gets `anullsrc`), then a stream-copy concat `[intro?] + captioned/watermarked body + [outro?]`. VO is delayed by the intro length (`adelay`) so narration still starts on the body. |
| Fade | `fade` (0-3 s, page offers 0.3/0.5/1) | `_fade_filters(length, in, out)` (pure, tested; each fade capped at half the part): video `fade` + audio `afade` on the FIRST body part (in) and LAST body part (out), and in+out on the intro and outro parts - so every start/end and intro|body|outro boundary fades; no fade inside the body's own tightened windows. The page auto-checks the toggle the first time an intro/outro is chosen (user can uncheck; the choice persists). |
| Watermark | `wm_key` + `wm {x, y, w, opacity}` | Logo uploaded through the same path (its cached copy is `{key}_src.mp4` whatever the bytes), renamed by magic bytes (`_image_ext`: png/jpg/webp - image2 picks the decoder from the EXTENSION), overlaid on the BODY only (intro/outro are the user's own branding) in the SAME encode pass as captions+hook (`_watermark_filter`, pure, tested: `[1:v]scale=w*W:-1,format=rgba,colorchannelmixer=aa=opacity[wm]; [v][wm]overlay=x='min(max(0,x*W),W-w)':y='min(max(0,y*H),H-h)'` - clamped inside the frame with overlay's own W/H/w/h since the logo's height is unknown until scaled). Geometry is NORMALIZED (x, y = top-left as fractions of the frame, w = width fraction), clamped in the route AND the worker (x,y 0-1, w 0.03-0.8, opacity 0.1-1). |
| Page | `.wm-stage` = a scaled preview of the output frame (aspect from the first source's `videoWidth/Height`, background = the first thumb), `.wm-img` dragged with POINTER events (mouse + touch, `setPointerCapture`, `touch-action:none`), size + opacity range inputs; drag/size are clamped inside the stage exactly like the server clamp. Persisted in localStorage `hebpipe_asm_brand` (logo data-URL if <400KB, geometry, fade choice); intro/outro files are not persisted. NEVER set `textContent` on `#wmBtn` - the file input lives inside it (`#wmBtnLabel` is the text). |
| Route | `/assembler/render` | `intro_key` / `outro_key` / `wm_key` validated by `_SAFE_KEY_RE` + uid-prefixed; `fade` float clamped; `wm` parsed only when `wm_key` is set. Passed positionally after `variant` - keep the order in sync with `render_story`'s signature. |

**Volume-lag lesson (same day):** the first production run silently skipped every asset - a render that follows its uploads by <1 s can read an EMPTY volume view because api-container writes publish in the background a few seconds later. `_resolve_source` now reloads + retries (6 x 2 s) before declaring "No upload found". Verified E2E on production: intro (landscape, letterboxed) fading from black -> body with hook + captions + watermark fading in -> silent outro fading in/out; 3 + 14.1 + 2.5 = 19.69 s.

Tests: `tests/backend/test_assembler_branding.py`, `tests/frontend/assembler_branding.spec.js`.

## Trim steppers, previews, export to the pipeline (2026-08-16, later)

- **Trim steppers** (clips mode, `.c-trim`): "לפני −/+" moves the START
  earlier/later, "אחרי −/+" moves the END, whole seconds, bounded by the
  source (`sourceDur` from `result.clips[].duration`) and a 3 s minimum;
  the render payload simply uses the adjusted `c.start/c.end`
  (`start0/end0` keep the model's proposal for the "+2 שנ'" readout).
- **Preview** (`.c-trim .prev` → `.c-preview`): analyze now returns
  `sources` = the uid-prefixed `_src.mp4` keys; the page streams that
  through `/media` (range-capable, media-token auth) with a media fragment
  `#t=start,end` plus a `timeupdate` guard that pauses at `end` (Safari
  ignores the fragment's end). One preview open at a time; the steppers
  re-sync an open preview.
- **Export to the pipeline = every render is a re-editable History job.**
  `render_story` now composes in this order: body parts (fades + WATERMARK
  inside the part encode) → intro/outro concat → VO mix → save the
  composed, UN-captioned video as `{key}{suffix}_cut.mp4` → captions +
  hook burned LAST on the full timeline (events and hook `start_seconds`
  shifted by `intro_len`) → `_out.mp4`. `_record_job(..., edit_state=
  {src_key: cut_key, captions: events, hook, broll: [], font "Heebo",
  font_size 48, margin_v_pct 0.08, caption_style {}})` - the exact shape a
  burn records (docs/outputs.md), so History shows the pencil, prune pins
  the cut for 30 days, and `/jobs/{key}/edit` rehydrates the main editor
  (restyle captions, zoom, B-roll, hooks, re-burn). Result tiles and the
  story result link to `/?edit=<out key>`; `app.js` `showApp()` consumes
  `?edit=` once (validated key, stripped via `replaceState`, wins over the
  auto-resume) and calls `editFromHistory({key})`. Verified on production:
  job `editable: true`, edit state served, first caption at 3.03 s after a
  3 s intro, cut source streams.
- **Upload robustness**: `/upload_r2/complete` is retried 5x with backoff
  (idempotent server-side; a dropped POST after a 500MB upload surfaced as
  "Failed to fetch") and the analyze POST 4x on network errors / 5xx, with
  a specific message when the upload finished but the analyze never
  started.

## Auto-reframe 16:9 -> 9:16 with speaker tracking (2026-08-16)

`render_story(..., reframe="9:16")` (route: only the literal "9:16" is
accepted; page: the "פורמט" segmented control at the top of the branding
card, persisted with the other brand choices, payload `reframe`). Only
sources wider than 9:16 by > 8 px are reframed (`_is_wide`).

> **Superseded in part by "Shot-aware v2 (2026-09-24)" below** - the
> original single-crop pan (steps 1-3 here) and the per-window split
> decision are history; `_reframe_plan` survives only as the easing engine
> for drifting subjects, and `_split_chain` / `_crop_dims` are gone.

Pipeline per BODY window (intro/outro are never cropped - they are
normalized/letterboxed onto the new canvas like before):
1. `_reframe_samples`: ffmpeg extracts frames at `REFRAME_SAMPLE_FPS=3`,
   960 px wide (NOT 480: a podcast face ~15% of frame height is ~40 px at
   480 and is missed when it turns; ~80 px detects reliably), YuNet
   (`_faces_in_image`, the same ONNX detector the punch-in zoom uses,
   score threshold 0.45 for recall - the planner absorbs false positives)
   -> `[(t_rel, [(cx, cy, w, h) fractions])]`.
2. `_reframe_plan` (pure, `test_assembler_reframe.py`): target per sample =
   group center when every face fits inside 90% of the crop (a two-shot
   stays a two-shot) else the LARGEST face, clamped so the crop stays
   inside the frame; detection GAPS bridged by linear interpolation
   between the last and next known positions (<= 20 s - between two known
   positions the straight line beats holding); 3-sample median; dead zone
   0.05 on the TARGET (nodding never starts a pan); then continuous
   exponential easing toward the target (tau 0.8 s, speed cap 0.30 W/s) -
   NO stepped moves (the first version dead-zoned the motion itself and
   produced "move 6% in 0.3 s, hold 1 s" stair-steps); no face for > 6 s
   -> ease back to the middle. Simplified to <= `REFRAME_MAX_KEYFRAMES=30`
   points (greedy: keep a point only if the straight line misses it by
   > 0.4% of width).
3. `_crop_x_expr` (pure): nested `if(lt(t,..),..)` piecewise-linear crop-x
   in pixels (part-relative `t`), applied as `crop=W:H:x='..':y=0` BEFORE
   the canvas normalize inside `_encode_part` (`pre=`); the canvas
   `(cw, ch)` becomes the first kept clip's crop dims, so watermark,
   captions margins, hook box and intro/outro follow automatically.
   Sampling failure -> centered static crop, never a failed render.
`_probe_dims` is now rotation-aware (rotate tag / display matrix), so a
phone portrait stored as landscape+90deg is not "reframed".

Verified on production with a synthetic drifting-subject landscape clip
(4K yoga source with a slow sinusoidal pan): 608x1080 output, the crop
follows her continuously (peak pan ~10% W/s), a bowed-face gap is bridged,
hook + fade + watermark ride the vertical canvas. Cost: ~3 samples/s of
YuNet (~40 ms each) + the same encodes as before.

**"fit" mode (2026-08-17, user: "it needs to contain all the frame - actually
convert it to portrait, not just cut the sides")** - `reframe="fit"`: the
WHOLE landscape frame is kept, fitted to the width of a portrait canvas
(`_fit_dims`: width = min(source width, 1080), height 16/9 of it -> a
1920x1080 talk becomes 1080x1920) and centered over a blurred, zoomed-to-
cover copy of itself (`_fit_chain`: split -> bg scaled to cover at 1/8
size, gblur sigma 4, -6% brightness, upscaled - same look as a full-res
blur at a fraction of the CPU -> fg scaled to width -> overlay centered).
Applied as the `pre` sub-graph of every body part AND of landscape
intro/outro (`_pre_for`), so the whole output shares one look; because it
is a labeled sub-graph, `_encode_part` now ALWAYS uses `-filter_complex`.
The page's format control is three-way (`#frameOrig` / `#frameFit` /
`#frameVert`), persisted; the watermark stage goes 9:16 for both vertical
modes. Verified on production: 1080x1920, hook + watermark on the canvas,
the landscape intro fitted the same way.

**Split-screen for two speakers (2026-08-17, user example: a Zoom-style
side-by-side interview -> the Instagram clip has one speaker stacked above
the other)** - "9:16" is now SMART: `_split_plan` (pure) 2-means-clusters
every sampled face center on x (seeds 0.3/0.7); when both groups are
present in >= 40% of samples AND too far apart to share one tracked crop
(separation > 0.9 x crop_frac - close two-shots keep the group crop) it
returns the LEFT/RIGHT median (cx, cy). `_split_chain` (pure) then stacks
two tiles of (canvas w x canvas h/2) = 9:8 on the 1080x1920 canvas: LEFT
speaker on TOP, RIGHT below; each tile's source crop is half the source
width (a Zoom pane) at the matching 9:8 height, x centered on the face, y
= face y - 45% of the crop height (face a little above center), clamped;
`crop,scale` per tile, `vstack`. Sampling runs ONCE per body window before
the canvas decision (`rf_samples` / `rf_split`); the FIRST window's verdict
picks the canvas (`split_mode` -> fit dims), later single-speaker windows
fall back to the tracked crop scaled onto it. The hook box moves to the
seam (`vertical_position` 46) in split mode - the default top-10% box sat
on the upper speaker's face (caught on the first render). Verified on
production with a synthetic two-pane clip: 1080x1920, both speakers framed,
hook on the seam. Not done: active-speaker emphasis (needs diarization).

**Previews follow the selection (2026-08-17):** the candidate preview is
rebuilt on every format change (`_applyPreviewFormat`, keeps the playhead):
plain video for "as source"; a 9:16 `.pv-box` for the vertical modes -
"fit" = main video contained over a muted blurred copy kept in step
(play/pause/seek/drift-sync), "9:16" = centered `object-fit: cover` with a
note that the render tracks the speaker. Trim steppers re-point the open
preview at the new window (explicit seek after the src change - some
browsers keep the old frame while paused).

**Shot-aware v2 (2026-09-24, user: "9:16 produces bad output on real
podcast recordings")** - four field causes on multi-cam podcasts (a wide
shot cut with close-ups): the crop eased ACROSS camera cuts, panned where
an editor holds, never zoomed (full height always), and followed the
largest face instead of the speaker. The planner is now per SHOT and emits
**segments** `{t0, t1, kind, role, cut, geometry}`; every segment boundary
is a HARD cut.

| Piece | Contract |
|---|---|
| Canvas | "9:16" is ALWAYS `REFRAME_CANVAS` 1080x1920 (the old 608x1080 / "canvas changes when the first window splits" rules are gone). Portrait windows are scaled onto it by `norm`; intro/outro letterboxed as before. |
| `_reframe_samples(src, a, b, workdir, tag, fps=6)` | ONE decode, two branches: faces at `fps` (960 px, YuNet) with per-face `mouth_d` / `upper_d` = change of the lower / upper third of the face box vs the nearest face of the previous sample - each patch 48x24 gray, z-normalized, compared at the best of +-2 px shifts; and ffmpeg's `scene` score for EVERY source frame (192 px branch, `select='gte(scene,0)',metadata=print`). Returns `(samples, scene)`. `REFRAME_SAMPLE_FPS` = **6** (user decision 2026-09-24 after the 3-vs-6 comparison below; 3 still works through the parameter / the preview's `--fps 3`). |
| `_shot_cuts` (pure) | score >= `REFRAME_SCENE_THR` 0.30 = a cut; cuts < `REFRAME_MIN_SHOT` 0.5 s apart (or from either end) collapse to the strongest. Times are the cut frame's own pts - frame-accurate, not the 1/fps grid. |
| `_plan_window` (pure) | shots = [0, cuts, length]; each planned on ITS OWN samples (samples within 0.05 s of a cut dropped; the first sample of a shot loses its mouth diff - it compared against the other camera) and stitched with exact shared boundaries; `_merge_segments` absorbs < 0.2 s slivers / enforces `REFRAME_MAX_SEGMENTS` 40 but never removes a cut boundary. |
| `_plan_shot` (pure) | (1) two persistent tracks (`_face_tracks`, presence >= 40%) too far apart for one crop (`_split_plan`) -> **split**: two 9:8 tiles, each `_zoom_box`ed on its face (height clamp(face_h x 3.8, 0.35 sh, sh/2); sh < 1080 -> floor = sh/2), LEFT on top, then PANE-BOUNDED (row below). (2) every face fits 90% of a full-height crop -> ONE zoomed **group** crop (`_group_box`: union box + 25% margin each side, 9:16, floor 0.45 sh). (3) else a static zoomed crop on the **active speaker** (or the only / largest face): `_zoom_box` = clamp(face_h x `REFRAME_ZOOM_K` 3.8, floor, sh) -> the face is ~26% of the output, face center 40% from the top. No face -> full-height center crop. |
| Static framing | `_hold_runs`: hold the median; re-aim only when the target stays outside 0.2 x crop width for MORE than `REFRAME_HOLD` 1.5 s, and then as a hard cut placed where it LEFT the band. `_drift_runs` (steady one-way motion >= `REFRAME_DRIFT_SECONDS` 3 s, net >= the band) are the only spans that ease (`kind: "ease"`, the original `_reframe_plan` at a fixed zoom) - the yoga case. |
| Split panes | `_refine_split_panes` (impure, in `_reframe_window`): 3 frames per split segment (25/50/75%) as 320 px raw RGB; `_find_pane` (pure) scans outward from each tile face for >= 2 **chrome** lines - rows over +-2 face widths (up, down), then columns over the found pane height (left, right); `_chrome_line` = near-black and flat (luma <= `REFRAME_PANE_DARK` 16, std <= `REFRAME_PANE_TOL` 5) or flat to synthetic precision (std <= 2.5) short of near-white. Per-bound median when >= 2 frames agree; `_fit_tile_in_pane` re-centers + clamps the tile inside the pane, and a pane smaller than the 9:8 crop SHRINKS it and sets `fill` (the tile's remainder = a blurred, darkened copy of the crop, the fit look - never black). No pane -> the old frame-bounded tile. Field (2026-09-24): the podcast's top tile ran into the black name bar; the first tolerance (luma 40 / std 8) also took a black stove pipe running the full pane height (luma 27-30, std 7-8) for a pane edge - the real bars measure luma 0-10, std 0-4. The pane finder does NOT see a boundary between two panes that touch with no divider (right edge = frame edge there); the tile then stays inside the frame as before. |
| Active speaker | `_speaker_timeline`: `_mouth_score` = mouth_d (the LOWER third only - field 2026-09-24: "lower - upper" on raw pixel diffs crowned the brighter, sharper, smiling listener from box jitter and blinks, 35% correct on a hand-labeled stretch; the normalized + registered lower third alone was 89-98%), averaged over a centered 1 s window; the first speaker is the first to reach `min_act` 0.15; a challenger needs HYSTERESIS - its 1 s score must beat the CURRENT speaker's by a normalized lead >= `REFRAME_SPEAKER_HYST` 0.15 - for 0.3 s, and switches are >= 1.5 s apart (a wanted earlier switch lands at last + 1.5; a lead that fades first counts as `suppressed`). Offline look-ahead: an A -> B -> A span shorter than `REFRAME_EXCURSION` 3 s (a laugh / interjection over ongoing speech) merges back into A (also `suppressed`). `margin` = mean normalized lead. Tracked crops switch speakers with a hard cut; in a split the NON-speaker's tile is dimmed (`eq=eval=frame`, -6% brightness, 85% saturation) unless margin < 0.1 or `REFRAME_DIM_LISTENER` is off. |
| Upscale guard | source height < 1080 -> tracked/group crop floor 0.60 sh (`_rf_floor`). `upscale_factor` (canvas px per source px) is reported per segment. |
| Confidence | `_reframe_confidence` = coverage x (0.5 + 0.5 x min(1, avg shot / 2 s)) x speaker stability (duration-weighted; split / group / single-face shots 1.0; speaker-TRACKED shots `_speaker_stability` = 0.7 + 0.3 x min(1, margin / 0.3) x (1 - min(0.5, 0.1 x suppressed)) - floored at 0.7 so a low speaker margin alone can never push a window with good coverage and shot length into fit; the first version, 0.4 + margin, did at margin 0). Below `REFRAME_MIN_CONFIDENCE` 0.45 (or > 40 segments) the whole window is ONE "fit" segment (blurred background). |
| `_segments_chain` (pure) | `split=N` -> per segment `trim` (`_rf_ts`: 0.1 ms before the boundary, so the cut frame opens the next segment) -> `setpts=PTS-STARTPTS` -> crop/ease/split/fit -> cw x ch -> `concat=n=N:v=1:a=0,tpad=stop_mode=clone:stop_duration=0.5,trim=duration=<length>`. It RAISES unless segments are contiguous (t1 == next t0), non-empty, cover [0, length], and every cut is a boundary (a cut inside a framing-free fit segment is allowed). The concat + clone-pad + exact-trim tail is always there, single segment included: on the image's ffmpeg 5.1 a split/fit last frame has no duration and the canvas `fps=30` dropped it (119/120 at 30 fps, 299/300 at 24/25 fps, measured; `fps eof_action=pass` only fixed 30 fps). With the tail: 300/300 for crop / split / fit at 24, 25 and 30 fps. |
| `_reframe_window` | sample + plan + validate; any failure -> centered full-height crop, `mode: "center"`, confidence 0. Shared by render_story and the preview script. |
| Report | render result `reframe_report` = `_reframe_summary`: `{canvas, windows: [{window, mode (track / speaker / group / split / fit / center / mixed), shots, speaker_switches, suppressed, margin, stability, coverage, confidence, fps, reason?, segments: [{t0, t1, kind, role, upscale_factor, pane_bounds? ([l, t, r, b] fractions or null per tile), tile_fill? (per tile)}]}], summary: {mode, shots, speaker_switches, margin, confidence (duration-weighted), min_confidence, max_upscale}}`, also logged as `[assembler] reframe_report`. Analyze does not carry it (the format is chosen at render time). The page does not show it yet (planned: a soft note). |
| Hook | moves to the seam (`vertical_position` 46) only when a split segment is on screen during the hook. |

Verified: frame-exact cut switching on the image's ffmpeg 5.1 (synthetic
red|green -> yellow|blue cut: 57 red + 63 blue frames of 120, switch on the
cut frame); mixed ease + split + fit chain 120/120 frames at 1080x1920.

**First real podcast (2026-09-24, "Daria Eyal - between science and
spirit", 72 min, 1280x720 25 fps, one camera, a branded two-pane Zoom
layout with title + name bars, no camera cuts - max scene score 0.008):**
all four 60 s windows (0:20 host talking, 10:00 / 30:00 / 55:00 guest
talking) plan as one split, confidence 1.0, upscale 2.67 (720p source).
Speaker switches per window, before -> after the fixes above: 12-23 ->
0 / 0 / 0 / 0 at 6 fps (the 30:00 host laugh is `suppressed`). 3 vs 6 fps
(before hysteresis): 2/5/2/2 vs 2/2/2/0 switches, margin 0.21-0.53 vs
0.17-0.58 -> 6 fps is the default. Panes (0, 0.25, 1, 0.75) for both
tiles - the black strip under the top tile is gone. Dimming never toggled
faster than the 1.5 s hold (measured in the preview mp4s), so it stays on.
Frame counts: 1800/1800 per 60 s preview. The 0.30 cut threshold is still
unconfirmed on a real multi-camera source - validation deferred by the
user (2026-09-24: "don't need to cover that case for now"); check the
`shots` counts in the first real multi-cam render's reframe_report.

**Preview tool:** `modal run scripts/reframe_preview.py --key <upload key>
--start 120 --end 180 [--windows "a-b,c-d"] [--fps 6 (default) | 3] [--thr 0.3]
[--no-dim] [--out DIR]` - ephemeral (nothing deployed), same
`_reframe_window` as render: a contact sheet per window (one row per shot:
mid-shot frame + face boxes with mouth scores + the crop / tiles + the
detected pane (cyan) and pane bounds / fill per tile, the
cropped result, the shot's segments with upscale) and a 360x640 mp4 of the
crop plan only (audio kept, no captions / branding), plus the report and
the top scene scores for tuning the threshold.

Tests: `tests/backend/test_assembler_reframe.py` (pure planners executed
from source: hard cut on the exact frame, static speaker = zero pan
keyframes, alternating speakers + the 1.5 s hold, low confidence -> fit,
zoom / group / tile sizing, segment integrity, contracts).

**Selection answers are parsed defensively (2026-08-17)** - a 1-hour
source failed with `Expecting ',' delimiter` (pass-1 answer truncated /
malformed). Both `_pick_moments` and `_pick_clips` pass 1 now go through
`_parse_answer(text, list_key, item_key, scalar_keys)`: strict JSON first,
else salvage every complete item object (`_salvage_objects`) plus the
scalar strings by regex, and log `stop_reason`; pass-1 `max_tokens` 8000
-> 12000. Same doctrine as pass 2: a broken answer degrades, never 500s.

## Social caption per clip + render with a saved caption style (2026-09-24)

Two additive features, both modes of the page (the social caption lives on
the clips-mode result tiles; the style picker is in both render cards).

**A. "כיתוב לרשתות" on every clip tile**

| Piece | Where | Contract |
|---|---|---|
| Button + editor | `site/assembler.html` `socialBlock(c)` (`.o-social` on each `.out` tile) | Posts `{upload_key, start, end, video_key, title, hook}` - the clip's CURRENT range (after the trim steppers) and the rendered clip's key once its render landed (else `""`, frames are optional). Result: editable `textarea.o-cap` (caption), editable `input.o-tags` (hashtags, space-separated), `.o-copy` copies `caption + blank line + hashtags`. The text is stored on the candidate (`c.social`, next to `hookText`), so a re-render - which rebuilds the tiles - keeps it. Soft errors: `no_transcript` (the `_asm_words.json` scratch expired, 48 h) and `no_words` get specific Hebrew messages. |
| Routes | `app_modal.py` `POST /assembler/social-caption`, `GET /assembler/social-caption-poll/{id}` | `/generate-caption` could not be reused: its transcript comes from the CLIENT and this page never has word timings. The key is `_SAFE_KEY_RE`-validated and uid-prefixed, the optional `video_key` must pass `_owned_key`, range `0 <= start < end`, <= 600 s; poll enforces `_call_owned`. Title + hook go as `context`. |
| Worker | `assembler_fns.assembler_social_caption` (light_image, Anthropic secret, max 4 containers) | Reloads the volume, reads `{key}_asm_words.json`, `_social_segments` (pure) keeps the words starting in `[start, end)` (50 ms grace) regrouped per source segment `{start, end, text}`, then calls the SHARED `generate_caption_options.local(..., flavor="assembler", context=...)`. |
| Prompt + post-step | `content_fns._assembler_social_prompt`, `_clean_social` (pure) | 2-3 Hebrew lines, GENDER-NEUTRAL address (plural imperative / infinitive), no hashtags inside the caption, <= 2 emojis, exactly 5 relevant hashtags returned separately; title + hook are context only ("do NOT copy them"). `_clean_social` does not trust the prompt: em / en dashes -> "-", <= 3 caption lines, hashtag-only lines moved to the tags, tags normalized (`#`, spaces -> `_`, de-duplicated, max 5). Spend logged as `assembler_social`. The main editor's default flavor (prompt, `post_caption` spend, `{caption}` shape) is unchanged - tested. |

**B. "סגנון כתוביות" - render with a saved profile or preset, no styling UI**

The main editor stays the styling tool (the tile's "עריכה בפייפליין" link
is the way to tweak). The page only PICKS:

| Piece | Where | Contract |
|---|---|---|
| Picker | `#clipCapStyle` (clips card) + `#capStyle` (storyboard card), kept in step | Options: the user's saved profiles (`GET /profiles`, the account storage app.js uses) -> the built-in presets -> "ברירת מחדל". The editor keeps the last-used profile in memory only (`_activeProfile`), so there is nothing to preselect: the default is "ברירת מחדל". The pick persists in `hebpipe_asm_brand.capStyle` (`profile:<name>` / `preset:<id>` / `default`); a saved profile that no longer exists falls back to the default QUIETLY (and re-saves); an unreachable `/profiles` renders with the default without overwriting the stored pick. Disabled while that mode's captions toggle is off. |
| Presets | `CAPTION_PRESETS` in the page | Mirrored VERBATIM from `site/app.js` (`TestPresetMirror` compares the array bodies); Hebrew labels = `i18n.js` `preset.*`. |
| Payload | `styleExtra()` | Preset -> `{font, caption_style}`. Profile -> `font`, `font_size`, `margin_v`, `caption_style` minus the editor-only EFFECTS (`progress_bar`, `progress_color`, `auto_zoom`, `zoom_strength`, `zooms` - the assembler burn does not render them), and `hook_style` from the profile's hook design in the editor's hook-payload shape (opacity / 100, size = % of the base hook size). "ברירת מחדל" -> nothing. |
| Validation | `_sanitize_caption_style` (pure), run by `/assembler/render` AND again in `render_story` | `/burn` validates nothing (the font name lands in the ASS Style line) - this is strict: font in `CAPTION_FONTS` (the editor's 9), size 12-240 (the builder's clamp), margin 0-0.8 (the editor's drag range; a real profile sits at 0.33 - the first 0.3 cap would have silently moved its captions), colours `#rrggbb`, border 0-20, opacity 0-1, mode classic / karaoke / word, hook font in `HOOK_FONTS` (the editor's hook select values), hook size 50-200 %. Unknown keys dropped, effects dropped silently. An invalid value falls back to its default and is reported as `{field, value, fallback}` in the render result's `style_warnings` (logged by the route and the worker); the page shows a soft `.style-note`. |
| Render | `render_story(..., reframe, font, font_size, margin_v_pct, caption_style, hook_style, style_warnings)` - positional AFTER `reframe`, in this order | `_caption_ass_args` builds the final `build_caption_ass` call; `_apply_hook_style` lays the hook design over the hook dict (timing / position never overwritten). All None = EXACTLY the historical call (Heebo, 48, 0.08, no caption_style) - byte-identical, tested against the old literal call. `_record_job` edit_state carries the chosen `font`, `font_size`, `margin_v_pct`, `caption_style` (and the styled `hook`), so the editor reopens the clip with the same style. |
| Word-timed modes | `_captions_for_windows` events | Every event's `words` has one timing per token of its `text`, so karaoke and word-pop render from the assembler's events (tested: per-word highlight / one word per event) - both stay in the picker. |
| Caption cues = the MAIN PIPELINE's rules (review fix, same day) | `_captions_for_windows(windows, segs, max_chars, min_sil=0.3)`, `_clean_caption_word`, `_caption_max_chars` (pure) | User review: "follow the rules like in the Hebrew pipeline captions". The cues are now built exactly like `generate_ass`: each word cleaned (commas / periods / dashes / colons stripped from the edges, `?` `!` kept), ONE line per cue of <= `max_chars` (generate_ass' formula at its reference 48 px: 32 on a 1080 canvas; the chosen size re-wraps in the builder like a pipeline job), a new cue on every pause >= 0.3 s, never across source segments, `_fix_rtl_punct` per line, cue end = its last word's end. (Before: one cue per whole Whisper segment, raw punctuation.) |
| Geresh repair (review fix) | `_glue_split_words`, `_glued_segments` (pure) - at analyze (persisted clean), render and the social caption (read-time, so older transcripts are repaired too) | User review: word-pop showed "׳ונסון" alone. Whisper splits loan words at the geresh ("ג" + "׳ונסון", "הצ" + "'יקונג" - 46 times in the 72-min podcast); the main pipeline repairs this at transcription (`_glue_split_tokens`, process_video) but the assembler's own transcription never did. Same rule: a token starting with ׳ ' ’ (and more) joins the previous token when that ends with a Hebrew letter, spanning both; a repaired segment's text is rebuilt from its words (the Sonnet selection reads it). Verified on the real transcript: 0 splits left. |
| Split-screen placement (review fix) | `render_story`, `_seam_margin_pct` (pure) | User review: "subtitles are on Alina's head, they should be on the split line". When the body has any split segment and there are captions, the caption bottom margin centers a one-line cue ON the seam (half the canvas minus half a rendered line) - whatever the default / profile margin says - and that effective margin is what the edit state records. The hook box then goes just ABOVE the seam (`vertical_position` 36 = over the upper speaker's chest, clear of the chin and the caption line); with captions off it stays on the seam (46). |
| Karaoke RTL (review fix) | `_rtl_karaoke_ass` (pure), applied only when karaoke is chosen | User review: "rtl is not working correctly on the karaoke". Root cause (measured, local libass 0.17.5 AND the burn image's 0.17.1): libass bidi-reorders each override-tag-separated run on its own unless the style's `Encoding` is -1, so the highlight's color tags laid a Hebrew line's words out LEFT to right (and flipped a trailing comma to the line start). The assembler sets `Encoding -1` on the Default caption style of its ASS for karaoke renders - every tagged line then matches the untagged layout. The shared `build_caption_ass` is untouched, so **main-pipeline karaoke burns still carry the bug** (pending the user's go to fix it there). |

Tests: `tests/backend/test_assembler_social.py`, `tests/backend/test_assembler_captions.py`
(style passthrough, byte-identical default, word-timed modes, preset mirror),
`tests/frontend/assembler_social.spec.js`, `tests/frontend/assembler_caption_style.spec.js`
(every assembler spec now stubs `/profiles` - a new boot call).

## Prompt-steered selection (2026-08-16)

`#guidance` text input in the upload card (both modes) -> analyze payload
`guidance` (<=400 chars, whitespace-collapsed server-side) ->
`analyze_story(..., guidance)` -> `_guidance_block()` injected into BOTH
selection prompts (`_pick_moments`, `_pick_clips` pass 1) as content
PREFERENCES ("עדיפויות תוכן בלבד - הפורמט והחוקים לא משתנים"), so a user
line like "רק טיפים מעשיים, בלי סיפורים אישיים" changes WHICH segments are
picked and never the JSON contract. Verified on production: the two
personal-story clips of the synthetic podcast dropped out under that
guidance, the four practical ones stayed.

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
