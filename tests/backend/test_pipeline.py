"""
Unit tests for the core pipeline helpers.

All functions are extracted from source without importing modal/GPU libs.
"""
import pytest
from tests.backend.conftest import (
    seconds_to_ass,
    sanitize_transcript,
    add_clip_window,
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
