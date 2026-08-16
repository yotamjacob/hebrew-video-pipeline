"""Clips mode (long -> shorts, 2026-08-16) - the pure helpers behind the
assembler's clip candidates, extracted and EXECUTED from source:
_virality_score (composite estimate), _snap_to_words (word-boundary trims),
_tighten_windows (in-clip silence excision). Plus source-contract checks on
_pick_clips / analyze_story / render_story / the routes."""

from tests.backend.conftest import MODAL_SRC


def _fn(name):
    i = MODAL_SRC.index(f"def {name}")
    j = MODAL_SRC.index("\ndef ", i)
    ns = {}
    exec(MODAL_SRC[i:j], ns)
    return ns[name]


class TestViralityScore:
    def test_blends_model_score_and_rubric(self):
        f = _fn("_virality_score")
        v = {"hook": 8, "retention": 7, "emotion": 6, "clarity": 8, "shareability": 7, "score": 70}
        # rubric = (8*.3+7*.25+6*.15+8*.15+7*.15)*10 = 73.0 ; blend = 71.5 -> 72
        assert f(v, 40) == 72

    def test_duration_prior_penalizes_too_long_and_too_short(self):
        f = _fn("_virality_score")
        v = {"hook": 7, "retention": 7, "emotion": 7, "clarity": 7, "shareability": 7, "score": 70}
        base = f(v, 40)
        assert f(v, 80) == base - 8
        assert f(v, 65) == base - 4
        assert f(v, 10) == base - 6

    def test_clamped_and_robust_to_garbage(self):
        f = _fn("_virality_score")
        assert f({}, 30) == 1
        assert f({"score": 999, "hook": 99, "retention": 99, "emotion": 99,
                  "clarity": 99, "shareability": 99}, 30) == 99
        assert f({"score": "abc", "hook": None}, 30) == 1
        assert f(None, 30) == 1


class TestSnapToWords:
    WORDS = [[10.0, 10.4, "אז"], [10.5, 11.0, "הטעות"], [11.1, 11.8, "הכי"],
             [12.0, 12.6, "גדולה"], [30.0, 30.9, "נקודה"]]

    def test_snaps_onto_nearest_word_edges_with_pad(self):
        a, b = _fn("_snap_to_words")(10.6, 30.7, self.WORDS, 9.0, 32.0)
        assert a == round(10.5 - 0.12, 2)      # word start "הטעות" minus pad
        assert b == round(30.9 + 0.25, 2)      # word end "נקודה" plus pad

    def test_far_from_any_word_keeps_the_proposal(self):
        a, b = _fn("_snap_to_words")(20.0, 25.0, self.WORDS, 9.0, 32.0)
        assert a == 19.88 and b == 25.25

    def test_clamped_to_the_allowed_context_range(self):
        a, b = _fn("_snap_to_words")(0.0, 99.0, self.WORDS, 9.5, 31.0)
        assert a >= 9.5 and b <= 31.0

    def test_no_words_is_a_plain_clamp(self):
        a, b = _fn("_snap_to_words")(5.0, 8.0, [], 4.0, 9.0)
        assert (a, b) == (4.88, 8.25)


class TestTightenWindows:
    SEGS = [[{"start": 0, "end": 20, "text": "x",
              "words": [[1.0, 1.5, "a"], [1.7, 2.2, "b"],      # run 1
                        [4.0, 4.6, "c"],                       # gap 1.8 -> new run
                        [4.8, 5.3, "d"],
                        [9.0, 9.4, "e"]]}]]                    # gap 3.7 -> new run

    def test_splits_on_gaps_and_pads_each_run(self):
        out = _fn("_tighten_windows")([(0, 0.0, 12.0)], self.SEGS, min_gap=0.6, pad=0.18)
        assert out == [(0, 0.82, 2.38), (0, 3.82, 5.48), (0, 8.82, 9.58)]

    def test_runs_are_clamped_inside_the_window(self):
        out = _fn("_tighten_windows")([(0, 1.1, 5.0)], self.SEGS)
        assert out[0][1] == 1.1            # can't pad before the window start
        assert out[-1][2] == 5.0           # can't pad past the window end
        # word "e" (9.0) is outside the window and must not create a run
        assert len(out) == 2

    def test_windows_without_words_pass_through(self):
        out = _fn("_tighten_windows")([(0, 14.0, 18.0)], self.SEGS)
        assert out == [(0, 14.0, 18.0)]
        out = _fn("_tighten_windows")([(3, 1.0, 4.0)], self.SEGS)   # clip idx out of range
        assert out == [(3, 1.0, 4.0)]

    def test_small_gaps_merge(self):
        out = _fn("_tighten_windows")([(0, 0.0, 12.0)], self.SEGS, min_gap=5.0)
        assert out == [(0, 0.82, 9.58)]


class TestClipsContracts:
    def _pick(self):
        i = MODAL_SRC.index("def _pick_clips")
        return MODAL_SRC[i:i + 12000]

    def test_two_pass_selection_snaps_to_segments_then_words(self):
        block = self._pick()
        assert "from_seg" in block and "to_seg" in block
        assert "_snap_to_words(st, en, c[\"words\"], c[\"lo\"], c[\"hi\"])" in block
        # Both passes are cost-tracked separately.
        assert '"assembler_clips_pick"' in block and '"assembler_clips_score"' in block
        # The rubric fields the UI renders.
        for k in ('"hook"', '"retention"', '"emotion"', '"clarity"', '"shareability"',
                  '"reasoning"', '"tip"'):
            assert k in block
        # A broken pass 2 ships pass-1 trims - never a failed analysis.
        assert "shipping pass-1 trims" in block
        assert 'out.sort(key=lambda c: -c["score"])' in block

    def test_analyze_branches_on_mode_and_raises_the_input_cap(self):
        i = MODAL_SRC.index("def analyze_story")
        block = MODAL_SRC[i:i + 9000]
        assert 'mode: str = "clips"' not in block   # default stays story
        assert 'clips_mode = (mode == "clips")' in block
        assert "CLIPS_INPUT_MAX_SECONDS if clips_mode else TOTAL_INPUT_MAX_SECONDS" in block
        assert '"mode": "clips"' in block and '"candidates": candidates' in block
        assert "CLIPS_INPUT_MAX_SECONDS = 5400" in MODAL_SRC

    def test_render_tightens_hooks_and_suffixes_the_output_key(self):
        i = MODAL_SRC.index("def render_story")
        block = MODAL_SRC[i:i + 12000]
        assert "tighten: bool = False, hook_text: str = \"\"" in block
        assert "_tighten_windows(windows, clip_segments)" in block
        # Hook rides the shared builder's hook box, bounded duration.
        assert '"start_seconds": 0.2' in block
        assert "events, hook)" in block
        assert 'f"{upload_keys[0]}_{variant}_out.mp4" if variant' in block

    def test_routes_forward_and_bound_the_new_fields(self):
        i = MODAL_SRC.index('("/assembler/analyze"')
        ablock = MODAL_SRC[i:i + 3200]
        assert 'mode = "clips" if data.get("mode") == "clips" else "story"' in ablock
        assert "names, mode)" in ablock
        j = MODAL_SRC.index('("/assembler/render"')
        rblock = MODAL_SRC[j:j + 4200]
        assert "_SAFE_VARIANT_RE.match(variant)" in rblock
        assert '[:120]' in rblock and 'bool(data.get("tighten", False))' in rblock
        assert "tighten, hook_text, variant)" in rblock
        assert "_SAFE_VARIANT_RE = _re.compile(r'^[a-zA-Z0-9_\\-]{1,24}$')" in MODAL_SRC
