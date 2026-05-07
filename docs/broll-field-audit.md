# B-Roll Moment Field Audit
_Phase 1 — read-only analysis. No code changes. Produced 2026-04-25._

---

## 1. Complete Field Catalog

### How to read the table

| Column | Meaning |
|--------|---------|
| **Bucket** | CORE / EDITORIAL / DEBUG / REDUNDANT |
| **Schema** | Does Sonnet currently output this field? |
| **Code use** | Does any pipeline code consume it beyond storing it? |
| **UI — default** | Shown in the always-visible part of the moment card? |
| **UI — debug** | Shown inside the `<details>` collapsible? |

---

### Sonnet output fields (21 total)

| Field | Bucket | Schema | Code use | UI default | UI debug | Justification |
|-------|--------|--------|----------|------------|----------|---------------|
| `moment_type` | **CORE** | ✓ | spacing filter, type badge | ✓ badge | ✓ row | Controls type-conditional variant rules; user needs to see concrete vs emotional vs coverage to evaluate. |
| `confidence` | **CORE** | ✓ | spacing filter | ✓ badge | — | The spacing filter's sort key; user's primary quality signal. Cannot cut. |
| `broll_start_seconds` | **CORE** | ✓ | canonical timing, clip search | ✓ time badge | — | Post-normalization canonical start; drives all downstream timing. |
| `broll_end_seconds` | **CORE** | ✓ | canonical timing, clip search | ✓ time badge | — | Post-normalization canonical end, clamped to ±5s. |
| `broll_duration_seconds` | **CORE** | ✓ | `add_clip_window`, dur badge | ✓ dur badge | — | Passed directly into `add_clip_window` and displayed on card. |
| `transcript_excerpt` | **CORE** | ✓ | UI fallback chain | ✓ excerpt | — | The primary Hebrew text source. User evaluates the moment on its content. |
| `search_variants` | **CORE** | ✓ | retrieval loop | — | ✓ rows | Drives the entire multi-variant clip retrieval pipeline. |
| `strict_eval_prompt` | **CORE** | ✓ | Haiku scoring, DISQUALIFY override | — | ✓ | Drives both primary scoring and the sanity-check override pass. |
| `intensity_score` | **EDITORIAL** | ✓ | UI tooltip only | tooltip only | ✓ | Informs Sonnet's confidence assignment; once confidence is set, score isn't used by pipeline code. Load-bearing for Sonnet's self-consistency — removing it would likely degrade moment selection quality. |
| `reasoning` | **EDITORIAL** | ✓ | UI only | ✓ div | — | Hebrew explanation for why the moment deserves B-roll. Users (Hebrew speakers) read this to decide whether to use the moment. Load-bearing for user decision. |
| `key_insight` | **EDITORIAL** | ✓ | UI only | — | ✓ | English specific claim. Anchors Sonnet's variant generation for concrete/hybrid moments — Sonnet reasons about "what noun phrase should appear in the variants" using this field implicitly. Removing it would likely degrade variant specificity. |
| `visual_anchor` | **EDITORIAL** | ✓ | UI only | — | ✓ | Guides Sonnet's symbolic clip convention for emotional moments. Shown in debug to diagnose abstract/wrong anchors. Load-bearing for emotional moment quality. |
| `intensity_markers` | **DEBUG** | ✓ | none | — | ✓ | Array of Hebrew words that triggered the emotional signal. Only useful when diagnosing false positives or missed emotional moments. |
| `duration_reasoning` | **DEBUG** | ✓ | UI tooltip only | tooltip | ✓ | Template sentence explaining the duration decision. Currently also shown as a tooltip on the duration badge — this is debug noise in the default view. Move to debug-only. |
| `verbatim_quote` | **REDUNDANT** | ✓ | fallback for `transcript_excerpt` | fallback | — | Explicit duplicate of `transcript_excerpt` from before the field was renamed. UI already falls back: `transcript_excerpt \|\| verbatim_quote`. **Cut.** Covered by: `transcript_excerpt`. |
| `broad_search_prompt` | **REDUNDANT** | ✓ | compat alias for `search_variants[0]` | — | — | Code comment says "(kept for compatibility)". Normalization sets it to `variants[0]`. Sonnet schema says "set to the first element of search_variants." **Cut from schema** (keep in normalization code as derived field). Covered by: `search_variants[0]`. |
| `start_seconds` | **REDUNDANT** | ✓ | normalization reads as fallback | — | — | Raw moment start before normalization. Post-normalization `broll_start_seconds` is canonical and always set. Normalization fallback chain reads it, but the field itself in the schema is redundant. **Cut.** Covered by: `broll_start_seconds`. |
| `speech_start_seconds` | **REDUNDANT** | ✓ | normalization reads → `broll_start_seconds` | — | — | Schema defines `broll_start_seconds := speech_start_seconds`. They're always equal. **Cut.** Covered by: `broll_start_seconds`. |
| `speech_end_seconds` | **REDUNDANT** | ✓ | normalization reads → `broll_end_seconds` | — | — | Pre-clamping speech boundary. After normalization, `broll_end_seconds` is the clamped canonical value. `speech_end_seconds` is preserved in the dict but nothing downstream reads it specifically. **Cut.** Covered by: `broll_end_seconds` (note: clamping difference logged if needed via `duration_reasoning`). |
| `topic` | **REDUNDANT** | ✓ | card label via `m.label = m.topic` | ✓ label | — | Short English topic phrase. `key_insight` is strictly more informative (it's the specific claim, not just the category). The card label role can be filled by `key_insight` truncated to ~6 words. **Cut.** Covered by: `key_insight`. |
| `subject_complexity` | **REDUNDANT** | ✓ | tooltip prefix on dur badge | tooltip | ✓ | Three-level enum used only to prefix the `duration_reasoning` tooltip. Once `duration_reasoning` moves to debug-only, its prefix becomes meaningless. The actual output of the complexity decision is `broll_duration_seconds` itself. **Cut.** Covered by: `broll_duration_seconds` + `duration_reasoning`. |

---

### Synthetic / post-normalization fields (not in Sonnet schema)

| Field | Bucket | Note |
|-------|--------|------|
| `start` | REDUNDANT | = `broll_start_seconds`. Code compat alias set during normalization. |
| `end` | REDUNDANT | = `broll_end_seconds`. Code compat alias. |
| `search_query` | REDUNDANT | = `broad_search_prompt`. Third alias for the same thing. |
| `label` | REDUNDANT | = `topic`. Set in normalization as `m.get("topic", m.get("label", ""))`. |
| `clips` | CORE | Retrieved stock clips. Not in schema — added by pipeline. |
| `weak_match` | CORE | Boolean: no clips passed scoring threshold. Drives UI "no clips" message. |
| `_variant_stats` | DEBUG | Per-variant retrieval counts. Only useful when diagnosing thin results. |

---

## 2. Suspected Redundancies — Explicit Checks

### topic vs key_insight
**Verdict: REDUNDANT — cut `topic`.**
- `topic`: "what the speaker is talking about, one short phrase" — e.g., "morning routine"
- `key_insight`: "the specific non-obvious claim being made, one sentence" — e.g., "she realised journaling before work tripled her creative output"
- `key_insight` is strictly a superset. The card label role currently filled by `topic` can be filled by `key_insight` truncated to the first 6–7 words (Phase 3 code change).

### transcript_excerpt vs verbatim_quote
**Verdict: REDUNDANT — cut `verbatim_quote`.**
- Both are Hebrew text of the speech segment. `transcript_excerpt` is built by the pipeline from edited captions (more authoritative); `verbatim_quote` is Sonnet's raw copy from context (can drift from edits). UI already uses `transcript_excerpt || verbatim_quote` as a fallback chain, which confirms `verbatim_quote` is already the secondary source. Remove the field from the schema; keep the `|| verbatim_quote` fallback in the UI until a full rotation cycle confirms it's never needed.

### broad_search_prompt vs search_variants[0]
**Verdict: REDUNDANT — cut from schema; keep as derived field in normalization.**
- The schema definition literally says "set to the first element of search_variants." Normalization does `broad = variants[0]` and then `m["broad_search_prompt"] = broad`. This is a pure alias.
- `broad_search_prompt` is also referenced in `momentCtx` (UI) and `search_stock_clips` (API). Those references should move to `search_variants[0]` in Phase 3. For now, the normalization code can continue deriving it — just remove it from the Sonnet schema definition.

### start_seconds vs speech_start_seconds vs broll_start_seconds
**Verdict: keep `broll_start_seconds` only; cut `start_seconds` and `speech_start_seconds`.**
- `start_seconds` is the raw moment anchor Sonnet chooses; no distinction from broll_start in practice.
- `speech_start_seconds` is defined as equal to `broll_start_seconds` in the schema ("broll_start_seconds: = speech_start_seconds").
- The normalization fallback chain (`broll_start_seconds` → `speech_start_seconds` → `start_seconds`) should be simplified to just read `broll_start_seconds` once the redundant fields are gone.

### subject_complexity + duration_reasoning vs speech timestamps
**Verdict: cut `subject_complexity`; keep `duration_reasoning` as DEBUG.**
- `subject_complexity` ("simple" | "moderate" | "complex") is input to `duration_reasoning` — it's Sonnet's internal label that explains why a particular duration was chosen.
- `duration_reasoning` captures the output of that reasoning ("3.2s speech window, subject is moderate...").
- `broll_duration_seconds` is the computed result.
- The user only needs to know the duration (from `broll_duration_seconds`). When debugging timing issues, `duration_reasoning` is useful — but `subject_complexity` alone adds nothing beyond what's already encoded in `duration_reasoning`'s text.

### intensity_score vs confidence
**Verdict: NOT fully redundant — keep both, but `intensity_score` is EDITORIAL.**
- `confidence` is the actionable 3-level bucket used by the spacing filter.
- `intensity_score` is the continuous 1–10 value that Sonnet uses to derive confidence.
- They're related but not identical: two moments can share `confidence: "medium"` while having `intensity_score` 5 vs 7. The score provides finer diagnostic resolution.
- Pipeline code only uses `confidence`. The UI shows `intensity_score` as a tooltip on the confidence badge.
- Classification: `confidence` = CORE; `intensity_score` = EDITORIAL (informs Sonnet self-check, tooltip only).

---

## 3. Proposed Reduced Schema

### Before (21 Sonnet fields)
```
moment_type, intensity_score, intensity_markers, start_seconds,
speech_start_seconds, speech_end_seconds, broll_start_seconds,
broll_end_seconds, broll_duration_seconds, subject_complexity,
duration_reasoning, transcript_excerpt, verbatim_quote, topic,
key_insight, visual_anchor, reasoning, search_variants,
broad_search_prompt, strict_eval_prompt, confidence
```

### After (14 Sonnet fields — 33% reduction)
```
moment_type, confidence, intensity_score, intensity_markers,
broll_start_seconds, broll_end_seconds, broll_duration_seconds,
duration_reasoning, transcript_excerpt, key_insight, visual_anchor,
reasoning, search_variants, strict_eval_prompt
```

### What was cut and why
| Cut field | Reason |
|-----------|--------|
| `verbatim_quote` | = `transcript_excerpt` |
| `broad_search_prompt` | = `search_variants[0]` — derived in normalization, not needed from Sonnet |
| `start_seconds` | = `broll_start_seconds` after normalization |
| `speech_start_seconds` | = `broll_start_seconds` by schema definition |
| `speech_end_seconds` | pre-clamp value; no downstream use after normalization |
| `topic` | superseded by `key_insight` (used as card label after truncation) |
| `subject_complexity` | only used as prefix to `duration_reasoning`; cut along with it from default view |

---

## 4. UI Split Proposal

### Toggle design: global, not per-card

**Recommendation: single global "Show debug info" toggle**, persisted to `localStorage`.

Rationale:
- When a developer investigates a quality issue, they want to scan across *all* moments simultaneously — not click into each card. Per-card state creates a confusing patchwork where three cards are open and two are closed with no clear reason.
- A global toggle is one click, one visual mode, one mental model.
- Users who never need debug info never see it.
- Implementation: a small `[Debug]` button in the B-roll analysis header row. On click, adds class `debug-mode` to the results container; all `<details class="moment-debug">` elements set `open` via CSS `[class~="debug-mode"] .moment-debug { }` + a brief JS loop.

---

### Default view — per card (CORE + directly user-facing EDITORIAL)

The question is: "Does this field help the user decide whether to use this moment's clips?"

| Element | Source field(s) | Why it stays |
|---------|----------------|--------------|
| Time badge | `broll_start_seconds`, `broll_end_seconds` | User needs to know when in the video |
| Duration badge | `broll_duration_seconds` | User needs to know how long the cut-away is |
| Type badge | `moment_type` | "rhythm" vs "emotional" vs "hybrid" tells the user what kind of moment this is |
| Confidence badge | `confidence` | Primary quality signal |
| Card label | `key_insight` (first ~6 words) | Brief English label identifying the moment |
| Hebrew excerpt | `transcript_excerpt` | What was actually being said — primary evaluation input |
| Hebrew reasoning | `reasoning` | Why Sonnet thinks this deserves B-roll — directly informs the user's decision |
| Clips row | `clips`, `weak_match` | The actual candidates |

**Remove from default view:**
- `subject_complexity` tooltip on dur badge → move to debug
- `duration_reasoning` tooltip on dur badge → move to debug

---

### Debug view (behind toggle)

| Debug row | Source field(s) |
|-----------|----------------|
| Pass (emphasis/coverage) | derived from `moment_type` |
| Type · intensity score | `moment_type`, `intensity_score` |
| Intensity markers | `intensity_markers` |
| Key insight (full) | `key_insight` |
| Visual anchor | `visual_anchor` |
| Subject complexity | `subject_complexity` (until cut from schema) |
| Search variants | `search_variants` |
| Variant retrieval | `_variant_stats` |
| Winning variant | `clips[0]._source_variant`, `clips[0].score` |
| Scoring target | `strict_eval_prompt` |
| Duration reasoning | `duration_reasoning` |

---

## 5. Impact Assessment for EDITORIAL Cuts

If any of these EDITORIAL fields were removed from the schema, here's the likely quality impact:

| Field | If cut from schema | Risk |
|-------|--------------------|------|
| `intensity_score` | Sonnet can no longer self-check confidence assignment; confidence level might be less calibrated | **Medium** — probably still good but harder to diagnose regressions |
| `reasoning` | User loses the Hebrew "why" explanation; less able to override bad selections | **Low pipeline, High UX** — model quality fine, user experience degrades |
| `key_insight` | Sonnet loses explicit English summary that anchors variant generation; abstract variants likely increase for concrete/hybrid moments | **High** — this is the abstraction-drift failure mode from Phase 2. Do not cut. |
| `visual_anchor` | Emotional moment variant generation loses the conventional symbolic anchor; variants become more random | **High for emotional moments** — the symbolic conventions (breaking glass, pensive face) depend on this field being explicit |

**Conclusion:** all four EDITORIAL fields are load-bearing. They should be moved to debug view only — not cut from the schema.

---

## 6. Summary

- **33% schema reduction** (21 → 14 Sonnet fields)
- **7 cuts** — all REDUNDANT: `verbatim_quote`, `broad_search_prompt`, `start_seconds`, `speech_start_seconds`, `speech_end_seconds`, `topic`, `subject_complexity`
- **4 EDITORIAL fields retained in schema** but moved to debug view: `intensity_score`, `intensity_markers`, `duration_reasoning` (already there); `key_insight`, `visual_anchor` (already in debug); `reasoning` stays in default view (user-facing)
- **UI change:** global debug toggle replacing the always-present `<details>` collapsible; `duration_reasoning` and `subject_complexity` tooltip removed from default view
- **Code changes needed in Phase 3:** normalization fallback chain simplification; `topic` → `key_insight` for card label; `broad_search_prompt` derived-only; UI toggle implementation
