"""
Unit tests for the core pipeline helpers.

All functions are extracted from source without importing modal/GPU libs.
"""
import pytest
from tests.backend.conftest import (
    compute_keep_segments,
    seconds_to_ass,
    sanitize_transcript,
    add_clip_window,
    make_word,
    KeepSegment,
)


# ─────────────────────────────────────────────────────────────────────────────
# seconds_to_ass
# ─────────────────────────────────────────────────────────────────────────────

class TestSecondsToAss:
    def test_zero(self):
        assert seconds_to_ass(0) == "0:00:00.00"

    def test_one_second(self):
        assert seconds_to_ass(1) == "0:00:01.00"

    def test_one_minute(self):
        assert seconds_to_ass(60) == "0:01:00.00"

    def test_one_hour(self):
        assert seconds_to_ass(3600) == "1:00:00.00"

    def test_fractional_seconds(self):
        result = seconds_to_ass(1.5)
        assert result == "0:00:01.50"

    def test_complex(self):
        # 1h 23m 45.67s
        t = 3600 + 23 * 60 + 45.67
        assert seconds_to_ass(t) == "1:23:45.67"

    def test_returns_string(self):
        assert isinstance(seconds_to_ass(10), str)


# ─────────────────────────────────────────────────────────────────────────────
# compute_keep_segments
# ─────────────────────────────────────────────────────────────────────────────

class TestComputeKeepSegments:
    """
    Covers the silence-detection algorithm that decides which stretches of
    video to keep.  This is the logic most likely to break when threshold /
    padding defaults change.
    """

    def _seg(self, start, end):
        return KeepSegment(start=start, end=end)

    def test_empty_words_keeps_whole_video(self):
        # No speech detected → keep the entire video as one segment
        result = compute_keep_segments([], min_silence=0.5, padding=0.2, total_duration=10.0)
        assert len(result) == 1
        assert result[0].start == pytest.approx(0.0)
        assert result[0].end   == pytest.approx(10.0)

    def test_single_word_returns_one_segment(self):
        words = [make_word("hello", 1.0, 2.0)]
        segs = compute_keep_segments(words, min_silence=0.5, padding=0.2, total_duration=10.0)
        assert len(segs) == 1
        assert segs[0].start == pytest.approx(0.8, abs=0.01)   # 1.0 - 0.2 padding
        assert segs[0].end   == pytest.approx(2.2, abs=0.01)   # 2.0 + 0.2 padding

    def test_consecutive_words_merged(self):
        # Gap between words is 0.1s < min_silence=0.5 → one segment
        words = [
            make_word("a", 1.0, 2.0),
            make_word("b", 2.1, 3.0),
        ]
        segs = compute_keep_segments(words, min_silence=0.5, padding=0.2, total_duration=10.0)
        assert len(segs) == 1

    def test_silent_gap_splits_segments(self):
        # Gap of 1.0s > min_silence=0.5 → two segments
        words = [
            make_word("first",  1.0, 2.0),
            make_word("second", 3.5, 4.5),
        ]
        segs = compute_keep_segments(words, min_silence=0.5, padding=0.2, total_duration=10.0)
        assert len(segs) == 2

    def test_padding_does_not_go_below_zero(self):
        words = [make_word("start", 0.0, 0.5)]
        segs = compute_keep_segments(words, min_silence=0.5, padding=0.3, total_duration=10.0)
        assert segs[0].start >= 0.0

    def test_padding_does_not_exceed_total_duration(self):
        words = [make_word("end", 9.5, 10.0)]
        segs = compute_keep_segments(words, min_silence=0.5, padding=0.3, total_duration=10.0)
        assert segs[-1].end <= 10.0

    def test_three_clusters(self):
        # Three word clusters separated by long silences
        words = [
            make_word("a", 0.0, 0.5),
            make_word("b", 0.6, 1.0),
            make_word("c", 5.0, 5.5),
            make_word("d", 5.6, 6.0),
            make_word("e", 10.0, 10.5),
        ]
        segs = compute_keep_segments(words, min_silence=0.5, padding=0.1, total_duration=15.0)
        assert len(segs) == 3

    def test_segments_are_sorted(self):
        words = [
            make_word("a", 1.0, 2.0),
            make_word("b", 5.0, 6.0),
            make_word("c", 10.0, 11.0),
        ]
        segs = compute_keep_segments(words, min_silence=0.5, padding=0.1, total_duration=15.0)
        starts = [s.start for s in segs]
        assert starts == sorted(starts)

    def test_segments_do_not_overlap(self):
        words = [make_word(f"w{i}", i * 2.0, i * 2.0 + 0.5) for i in range(5)]
        segs = compute_keep_segments(words, min_silence=0.5, padding=0.1, total_duration=20.0)
        for a, b in zip(segs, segs[1:]):
            assert a.end <= b.start

    def test_tight_min_silence_keeps_all_words_together(self):
        # min_silence=5.0 → even 1s gap is not silence, everything merged
        words = [
            make_word("a", 0.0, 0.5),
            make_word("b", 1.5, 2.0),   # 1s gap < 5s threshold
            make_word("c", 3.5, 4.0),
        ]
        segs = compute_keep_segments(words, min_silence=5.0, padding=0.0, total_duration=10.0)
        assert len(segs) == 1


# ─────────────────────────────────────────────────────────────────────────────
# _sanitize_transcript
# ─────────────────────────────────────────────────────────────────────────────

class TestSanitizeTranscript:
    def test_plain_string_unchanged(self):
        assert sanitize_transcript("hello world") == "hello world"

    def test_strips_null_bytes(self):
        result = sanitize_transcript("hello\x00world")
        assert "\x00" not in result

    def test_strips_control_characters(self):
        result = sanitize_transcript("line\x01\x02\x03end")
        assert "\x01" not in result

    def test_caps_at_max_chars(self):
        long_text = "א" * 100_000
        result = sanitize_transcript(long_text, max_chars=50_000)
        assert len(result) <= 50_001   # allow for any appended ellipsis

    def test_empty_string(self):
        assert sanitize_transcript("") == ""

    def test_unicode_hebrew_preserved(self):
        text = "שלום עולם זה בדיקה"
        assert sanitize_transcript(text) == text

    def test_newlines_preserved(self):
        text = "line one\nline two"
        result = sanitize_transcript(text)
        assert "\n" in result


# ─────────────────────────────────────────────────────────────────────────────
# add_clip_window
# ─────────────────────────────────────────────────────────────────────────────

class TestAddClipWindow:
    """
    add_clip_window decides the usable window within a stock clip given the
    required B-roll duration.  Keys are clip_use_start_seconds / clip_use_end_seconds.
    """

    def _clip(self, duration: float) -> dict:
        return {"duration": duration, "url": "https://example.com/clip.mp4"}

    def test_short_clip_uses_full_duration(self):
        clip = self._clip(4.0)
        result = add_clip_window(clip, broll_duration=4.0)
        assert result["clip_use_start_seconds"] == pytest.approx(0.0)
        assert result["clip_use_end_seconds"]   == pytest.approx(4.0, abs=0.1)

    def test_long_clip_windowed_to_broll_duration(self):
        clip = self._clip(30.0)
        result = add_clip_window(clip, broll_duration=5.0)
        window = result["clip_use_end_seconds"] - result["clip_use_start_seconds"]
        assert window == pytest.approx(5.0, abs=0.1)

    def test_clip_start_non_negative(self):
        clip = self._clip(10.0)
        result = add_clip_window(clip, broll_duration=3.0)
        assert result["clip_use_start_seconds"] >= 0.0

    def test_clip_end_not_exceed_duration(self):
        clip = self._clip(10.0)
        result = add_clip_window(clip, broll_duration=3.0)
        assert result["clip_use_end_seconds"] <= 10.01   # allow rounding

    def test_returns_dict_with_required_keys(self):
        clip = self._clip(8.0)
        result = add_clip_window(clip, broll_duration=4.0)
        assert "clip_use_start_seconds" in result
        assert "clip_use_end_seconds"   in result
        assert "clip_window_strategy"   in result

    def test_original_keys_preserved(self):
        clip = self._clip(8.0)
        result = add_clip_window(clip, broll_duration=4.0)
        assert result["url"] == clip["url"]
        assert result["duration"] == clip["duration"]
