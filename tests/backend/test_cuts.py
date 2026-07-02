"""
Unit tests for the cut-restore helpers (compute_cuts / merge_restored).

Extracted from pipeline_fns.py source without importing Modal.
"""
from tests.backend.conftest import (
    compute_cuts,
    merge_restored,
    ModalWord as Word,
    ModalKeepSegment as KeepSegment,
)


def _seg(start, end, words=()):
    return KeepSegment(start=start, end=end,
                       words=[Word(s, e, t) for s, e, t in words])


class TestComputeCuts:
    def test_no_segments(self):
        assert compute_cuts([]) == []

    def test_single_segment_has_no_cuts(self):
        assert compute_cuts([_seg(0, 5)]) == []

    def test_basic_gap(self):
        segs = [_seg(0, 2.0, [(1.5, 1.9, "שלום")]),
                _seg(3.0, 5.0, [(3.1, 3.4, "עולם")])]
        cuts = compute_cuts(segs)
        assert len(cuts) == 1
        c = cuts[0]
        assert c["index"] == 0
        assert c["start"] == 2.0 and c["end"] == 3.0
        assert c["duration"] == 1.0
        assert c["before"] == "שלום" and c["after"] == "עולם"

    def test_tiny_gap_filtered_but_index_stays_boundary(self):
        segs = [_seg(0, 2.0), _seg(2.01, 4.0), _seg(5.0, 6.0)]
        cuts = compute_cuts(segs)
        assert len(cuts) == 1
        # gap between segs[1] and segs[2] → boundary index 1, not list position 0
        assert cuts[0]["index"] == 1

    def test_multiple_gaps(self):
        segs = [_seg(0, 1), _seg(2, 3), _seg(4, 5)]
        assert [c["index"] for c in compute_cuts(segs)] == [0, 1]


class TestMergeRestored:
    def test_empty_restore_is_noop(self):
        segs = [_seg(0, 1), _seg(2, 3)]
        assert len(merge_restored(segs, [])) == 2

    def test_single_merge_keeps_silence_and_words(self):
        segs = [_seg(0, 1, [(0.2, 0.8, "a")]), _seg(2, 3, [(2.1, 2.9, "b")])]
        out = merge_restored(segs, [0])
        assert len(out) == 1
        assert out[0].start == 0 and out[0].end == 3
        assert [w.text for w in out[0].words] == ["a", "b"]

    def test_adjacent_restores_merge_chain(self):
        segs = [_seg(0, 1), _seg(2, 3), _seg(4, 5)]
        out = merge_restored(segs, [0, 1])
        assert len(out) == 1
        assert out[0].start == 0 and out[0].end == 5

    def test_second_gap_only(self):
        segs = [_seg(0, 1), _seg(2, 3), _seg(4, 5)]
        out = merge_restored(segs, [1])
        assert len(out) == 2
        assert out[1].start == 2 and out[1].end == 5

    def test_out_of_range_ignored(self):
        segs = [_seg(0, 1), _seg(2, 3)]
        out = merge_restored(segs, [-1, 5, 1])
        assert len(out) == 2

    def test_duplicate_and_string_indexes(self):
        segs = [_seg(0, 1), _seg(2, 3)]
        out = merge_restored(segs, ["0", 0])
        assert len(out) == 1

    def test_restore_then_recompute_cuts_drops_gap(self):
        segs = [_seg(0, 1), _seg(2, 3), _seg(4, 5)]
        merged = merge_restored(segs, [0])
        cuts = compute_cuts(merged)
        assert len(cuts) == 1
        assert cuts[0]["start"] == 3 and cuts[0]["end"] == 4
