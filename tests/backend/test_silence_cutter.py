"""
Unit tests for the Modal silence cutter: filler/noise detection (`_is_filler`)
and the word-level, filler-aware `compute_keep_segments` in pipeline_fns.py.

These are extracted from source (no modal/GPU import), like the rest of the
backend suite. NOTE the Modal `compute_keep_segments` takes the segment-tuple
shape `List[(List[Word], seg_end)]` — distinct from the CLI flat-list version
covered in test_pipeline.py.
"""
import ast
import re
import dataclasses
from dataclasses import dataclass, field
from typing import List
from pathlib import Path

import pytest

_SRC = (Path(__file__).resolve().parents[2] / "pipeline_fns.py").read_text()


def _load():
    """Exec just the cutter pieces (dataclasses, filler constants, the two
    functions) into an isolated namespace."""
    tree = ast.parse(_SRC)
    ns = {"re": re, "dataclass": dataclass, "field": field, "List": List,
          "dataclasses": dataclasses}
    want_fns = {"_is_filler", "compute_keep_segments", "_clamp_word_to_speech"}
    want_cls = {"Word", "KeepSegment"}
    want_pref = ("_FILLER", "_NIQQUD", "_STRIP", "_ELONG",
                 "_NO_SPEECH", "_AVG_LOGPROB", "_WORD_PROB")
    nodes = []
    for n in tree.body:
        if isinstance(n, ast.ClassDef) and n.name in want_cls:
            nodes.append(n)
        elif isinstance(n, ast.FunctionDef) and n.name in want_fns:
            nodes.append(n)
        elif isinstance(n, ast.Assign):
            names = [t.id for t in n.targets if isinstance(t, ast.Name)]
            if any(nm.startswith(want_pref) for nm in names):
                nodes.append(n)
    nodes.sort(key=lambda x: x.lineno)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "<x>", "exec"), ns)
    return ns


_NS = _load()
_is_filler = _NS["_is_filler"]
compute_keep_segments = _NS["compute_keep_segments"]
_clamp_word_to_speech = _NS["_clamp_word_to_speech"]
Word = _NS["Word"]
KeepSegment = _NS["KeepSegment"]


def _w(start, end, text, filler=False, prob=1.0):
    w = Word(start, end, text, prob)
    w.filler = filler
    return w


def _segs(words):
    return [(words, words[-1].end)]


# ── _is_filler ────────────────────────────────────────────────────────────────

class TestIsFiller:
    @pytest.mark.parametrize("tok", [
        "um", "umm", "uh", "uhh", "uhm", "er", "hmm", "mm", "eh",
        "ummmm", "errr",                        # elongated English
        "אה", "אמ", "אהה", "אממ", "אהם",         # Hebrew hesitations
        "אמממ", "אההה",                          # elongated Hebrew
        "אֶה", "אֶמ",                             # with niqqud
        "אה,", "umm.", " uh ",                   # punctuation / whitespace wrapped
    ])
    def test_fillers_detected(self, tok):
        assert _is_filler(tok) is True

    @pytest.mark.parametrize("tok", [
        "אם",    # if
        "הם",    # they
        "עם",    # with
        "אמא",   # mom
        "מאה",   # hundred
        "שלום", "בית", "כלב", "מה", "אני",
        "hello", "world", "the", "amazing",
    ])
    def test_real_words_kept(self, tok):
        assert _is_filler(tok) is False

    def test_empty_and_punct_only(self):
        assert _is_filler("") is False
        assert _is_filler("...") is False


# ── compute_keep_segments (word-level, filler-aware) ────────────────────────────

class TestComputeKeepSegments:
    def test_no_words_keeps_whole(self):
        segs = compute_keep_segments([], 10.0, 0.5, 0.2)
        assert len(segs) == 1 and segs[0].start == 0.0 and segs[0].end == 10.0

    def test_word_gap_inside_a_segment_now_splits(self):
        # A >min_sil gap between two words in ONE Whisper segment is cut
        # (the old segment-granular cutter kept these together).
        segs = compute_keep_segments(_segs([_w(1.0, 2.0, "a"), _w(3.5, 4.5, "b")]),
                                     10.0, 0.5, 0.2)
        assert len(segs) == 2

    def test_small_gap_merges(self):
        segs = compute_keep_segments(_segs([_w(1.0, 2.0, "a"), _w(2.1, 3.0, "b")]),
                                     10.0, 0.5, 0.2)
        assert len(segs) == 1

    def test_filler_between_words_forces_tight_cut(self):
        segs = compute_keep_segments(_segs([
            _w(1.0, 2.0, "hello"),
            _w(2.05, 2.55, "umm", filler=True),
            _w(2.6, 3.6, "world"),
        ]), 10.0, 0.5, 0.2)
        assert len(segs) == 2
        assert segs[0].end <= 2.2            # closes before the filler audio
        assert segs[1].start >= 2.5          # reopens at/after the filler ends
        # filler never appears in any kept segment's words
        assert [w.text for s in segs for w in s.words] == ["hello", "world"]

    def test_filler_words_excised_from_words(self):
        segs = compute_keep_segments(_segs([
            _w(1.0, 2.0, "real"), _w(2.1, 2.6, "uh", filler=True),
        ]), 10.0, 0.5, 0.2)
        assert [w.text for s in segs for w in s.words] == ["real"]

    def test_all_filler_keeps_whole_video_as_safety(self):
        segs = compute_keep_segments(_segs([
            _w(1.0, 1.5, "umm", filler=True), _w(2.0, 2.5, "uh", filler=True),
        ]), 10.0, 0.5, 0.2)
        assert len(segs) == 1 and segs[0].start == 0.0 and segs[0].end == 10.0

    def test_segments_do_not_overlap(self):
        words = [_w(i * 2.0, i * 2.0 + 0.5, f"w{i}") for i in range(5)]
        segs = compute_keep_segments(_segs(words), 20.0, 0.5, 0.1)
        for a, b in zip(segs, segs[1:]):
            assert a.end <= b.start

    def test_padding_clamped_to_bounds(self):
        segs = compute_keep_segments(_segs([_w(0.0, 0.5, "x"), _w(9.5, 10.0, "y")]),
                                     10.0, 0.5, 0.3)
        assert segs[0].start >= 0.0
        assert segs[-1].end <= 10.0


# ── VAD word-timestamp repair (the "bad-silence-cut" fix) ───────────────────────

class TestClampWordToSpeech:
    def test_trailing_word_inflated_over_pause_is_pulled_to_voice(self):
        # "אם" reported [16.8, 18.14] but voice only at [18.0, 18.2] -> start
        # snaps to 18.0, re-exposing the ~1.3s pause after the previous word.
        ns, ne = _clamp_word_to_speech(16.8, 18.14, [(10.0, 16.7), (18.0, 18.2)])
        assert ns == 18.0
        assert ne == 18.14

    def test_word_fully_inside_voice_unchanged(self):
        ns, ne = _clamp_word_to_speech(3.0, 3.5, [(2.5, 4.0)])
        assert (ns, ne) == (3.0, 3.5)

    def test_leading_silence_trimmed(self):
        ns, ne = _clamp_word_to_speech(1.0, 2.0, [(1.6, 2.0)])
        assert ns == 1.6 and ne == 2.0

    def test_no_voice_overlap_leaves_word_untouched(self):
        # VAD found no voice here — don't destroy a possibly-quiet real word.
        ns, ne = _clamp_word_to_speech(5.0, 5.4, [(0.0, 1.0), (9.0, 10.0)])
        assert (ns, ne) == (5.0, 5.4)

    def test_repaired_gap_now_splits_in_keep_segments(self):
        # End-to-end: after repair, "גבוהה" .. "אם" gap is real -> two segments.
        gvoha = _w(16.2, 16.7, "גבוהה")
        im    = _w(18.0, 18.14, "אם")          # already clamped to voice
        segs = compute_keep_segments(_segs([gvoha, im]), 20.0, 0.5, 0.2)
        assert len(segs) == 2
