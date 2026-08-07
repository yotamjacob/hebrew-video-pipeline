"""Phonetic guard on the LLM proofread pass.

The proofread may only fix words to SAME-SOUNDING alternatives; a field
report caught 'צופה' becoming 'גולשת' (fits the sentence, sounds nothing
alike). The guard compares consonant skeletons and rejects low-overlap
swaps deterministically - prompt instructions alone are soft.
"""
from tests.backend.conftest import MODAL_SRC, _extract_fn

_helpers = _extract_fn(MODAL_SRC, "_consonant_key", "_phonetically_plausible")
_consonant_key = _helpers["_consonant_key"]
_plausible = _helpers["_phonetically_plausible"]


class TestPhoneticGuard:
    def test_identity_always_passes(self):
        assert _plausible("צופה", "צופה")

    def test_homophone_letter_swaps_pass(self):
        # The classic repairs the proofread exists for.
        assert _plausible("קולטה", "כל תא")       # merged-token expansion
        assert _plausible("אשמחה", "שמחה")        # extra letter
        assert _plausible("טוב", "תוב")           # ט/ת swap (either direction)

    def test_meaning_swap_with_different_sound_rejected(self):
        # The field report: same meaning-space, unrelated sound.
        assert not _plausible("צופה", "גולשת")

    def test_invented_phrase_rejected(self):
        # The prompt's own FORBIDDEN example.
        assert not _plausible("שמחה", "אשתה איתך")

    def test_short_tokens_are_trusted(self):
        # One-consonant words carry too little signal to judge - never block.
        assert _plausible("אה", "אז")

    def test_rejection_is_wired_into_proofread(self):
        # The guard must actually gate the per-word merge, not just exist.
        assert "rejected non-phonetic swap" in MODAL_SRC
        assert "_phonetically_plausible(orig, w)" in MODAL_SRC
