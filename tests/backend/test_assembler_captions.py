"""_captions_for_windows - the assembler's transcript->output-timeline remap.

The captions on a story cut are only right if every kept window's words are
shifted by exactly the accumulated output time before it (storyboard order,
not chronological). Extracted and EXECUTED from source (pure function)."""

from tests.backend.conftest import MODAL_SRC


def _fn():
    i = MODAL_SRC.index("def _captions_for_windows")
    j = MODAL_SRC.index("\ndef ", i)
    ns = {}
    exec(MODAL_SRC[i:j], ns)
    return ns["_captions_for_windows"]


SEGS_CLIP0 = [
    {"start": 10.0, "end": 14.0, "text": "שלום לכולם",
     "words": [[10.5, 11.0, "שלום"], [11.2, 12.0, "לכולם"]]},
    {"start": 30.0, "end": 33.0, "text": "מחוץ לחלון",
     "words": [[30.2, 31.0, "מחוץ"], [31.1, 32.0, "לחלון"]]},
]
SEGS_CLIP1 = [
    {"start": 5.0, "end": 8.0, "text": "בין הגפנים",
     "words": [[5.1, 6.0, "בין"], [6.2, 7.0, "הגפנים"]]},
]


class TestCaptionRemap:
    def test_single_window_shifts_to_zero_based_output_time(self):
        events = _fn()([(0, 10.0, 14.0)], [SEGS_CLIP0])
        assert len(events) == 1
        e = events[0]
        assert e["words"][0][0] == 0.5           # 10.5 - 10.0
        assert e["words"][1][1] == 2.0           # 12.0 - 10.0
        assert e["text"] == "שלום לכולם"
        assert e["end"] <= 4.0                   # clamped inside the window

    def test_storyboard_order_accumulates_offsets_across_clips(self):
        # Storyboard: clip1's moment FIRST (5-8), then clip0's (10-14).
        events = _fn()([(1, 5.0, 8.0), (0, 10.0, 14.0)],
                       [SEGS_CLIP0, SEGS_CLIP1])
        assert len(events) == 2
        assert events[0]["words"][0][0] == 0.1               # 5.1 - 5.0
        assert events[1]["words"][0][0] == 3.0 + 0.5         # win1 len + (10.5-10)
        assert events[1]["start"] >= 3.0                      # never overlaps window 1

    def test_words_outside_the_window_are_excluded(self):
        # Window covers only the first segment - the 30s segment must not leak.
        events = _fn()([(0, 10.0, 14.0)], [SEGS_CLIP0])
        assert all("לחלון" not in e["text"] for e in events)

    def test_empty_words_produce_no_event(self):
        events = _fn()([(0, 0.0, 5.0)], [[{"start": 1, "end": 2, "text": "x", "words": []}]])
        assert events == []
