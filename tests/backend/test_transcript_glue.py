"""Split-token gluing (2026-08-09): Whisper can split a loan word at the
geresh ("פיצ׳רים" → "פיצ" + "׳רים"); the space-join later rendered
"פיצ ׳רים" mid-word. _glue_split_tokens re-attaches the continuation. Also
tripwires the proofread prompt's single-letter non-word example
("הפרטון" → "הסרטון" went uncorrected in the field)."""
from tests.backend.conftest import MODAL_SRC, _extract_fn

_glue = _extract_fn(MODAL_SRC, "_glue_split_tokens")["_glue_split_tokens"]


class W:
    def __init__(self, s, e, t, filler=False):
        self.start, self.end, self.text, self.filler = s, e, t, filler


def test_geresh_continuation_glues_to_previous_word():
    words = _glue([W(0, 1, "פיצ"), W(1, 1.4, "׳רים"), W(2, 3, "טובים")])
    assert [w.text for w in words] == ["פיצ׳רים", "טובים"]
    assert words[0].end == 1.4          # merged word spans both audio spans


def test_ascii_apostrophe_variant_glues_too():
    words = _glue([W(0, 1, "ג"), W(1, 1.5, "'ון")])
    assert [w.text for w in words] == ["ג'ון"]


def test_gershayim_quote_open_is_not_glued():
    # ״ can legitimately OPEN a quotation - never glue it.
    words = _glue([W(0, 1, "אמר"), W(1, 2, "״שלום")])
    assert [w.text for w in words] == ["אמר", "״שלום"]


def test_bare_geresh_and_non_hebrew_prev_left_alone():
    assert len(_glue([W(0, 1, "abc"), W(1, 2, "׳רים")])) == 2   # prev not Hebrew
    assert len(_glue([W(0, 1, "פיצ"), W(1, 2, "׳")])) == 2      # nothing after the geresh
    assert len(_glue([W(0, 1, "׳רים")])) == 1                    # no predecessor


def test_glue_keeps_filler_only_when_both_are_filler():
    words = _glue([W(0, 1, "פיצ", filler=False), W(1, 2, "׳רים", filler=True)])
    assert words[0].filler is False


def test_wired_into_segments_and_prompt_has_nonword_example():
    assert "seg_words = _glue_split_tokens(seg_words)" in MODAL_SRC
    assert "הפרטון" in MODAL_SRC and "הסרטון" in MODAL_SRC
