"""
Unit tests for module-level stock helpers in app_modal.py.
Run with:  python -m pytest test_stock_helpers.py -v
No network calls, no Modal runtime needed.
"""
import sys
import os
import types
import unittest

# ---------------------------------------------------------------------------
# Isolate helpers without importing Modal or GPU deps
# ---------------------------------------------------------------------------

def _load_helpers():
    """Extract specific module-level helper functions from app_modal.py using ast."""
    import ast as _ast
    import re as _re

    src_path = os.path.join(os.path.dirname(__file__), "app_modal.py")
    src = open(src_path).read()
    lines = src.splitlines()

    tree = _ast.parse(src)
    target_names = {"add_clip_window", "_sanitize_transcript", "fetch_pexels",
                    "fetch_pixabay", "score_clips", "extract_disqualify_clause"}
    extracted = []

    for node in _ast.walk(tree):
        if isinstance(node, _ast.FunctionDef) and node.name in target_names:
            func_src = "\n".join(lines[node.lineno - 1:node.end_lineno])
            extracted.append(func_src)

    # Also extract module-level constants (HAIKU_MODEL etc.)
    scoring_constants = {
        "HAIKU_MODEL", "SONNET_MODEL", "OPUS_MODEL",
        "HAIKU_SCORING_TEMPERATURE", "SONNET_MOMENT_TEMPERATURE", "HAIKU_SANITY_TEMPERATURE",
        "HAIKU_SCORING_MAX_TOKENS", "HAIKU_SCORING_BATCH_SIZE", "HAIKU_SCORING_CONCURRENCY",
        "HAIKU_SANITY_MAX_TOKENS", "HAIKU_SANITY_MIN_SCORE",
    }
    constants_src = ""
    for node in _ast.walk(tree):
        if isinstance(node, _ast.Assign):
            for t in node.targets:
                if isinstance(t, _ast.Name) and t.id in scoring_constants:
                    constants_src += "\n".join(lines[node.lineno - 1:node.end_lineno]) + "\n"

    stub_src = constants_src + "\nimport re as _re\nimport json\n" + "\n\n".join(extracted)

    ns: dict = {}
    try:
        exec(compile(stub_src, "app_modal.py", "exec"), ns)  # noqa: S102
    except Exception as exc:
        raise RuntimeError(f"Failed to load helpers:\n{stub_src[:500]}\n{exc}") from exc

    return ns


_ns = _load_helpers()
add_clip_window = _ns["add_clip_window"]


# ---------------------------------------------------------------------------
# add_clip_window tests
# ---------------------------------------------------------------------------

class TestAddClipWindow(unittest.TestCase):

    def _clip(self, duration):
        return {"duration": duration, "preview_url": "http://x", "id": 1}

    def test_whole_when_stock_equals_broll(self):
        c = add_clip_window(self._clip(3.0), 3.0)
        self.assertEqual(c["clip_use_start_seconds"], 0.0)
        self.assertEqual(c["clip_use_end_seconds"], 3.0)
        self.assertEqual(c["clip_window_strategy"], "whole")

    def test_middle_window_centers_correctly(self):
        # 10s clip, 3s broll → should use 3.5s–6.5s
        c = add_clip_window(self._clip(10.0), 3.0)
        self.assertAlmostEqual(c["clip_use_start_seconds"], 3.5)
        self.assertAlmostEqual(c["clip_use_end_seconds"], 6.5)
        self.assertEqual(c["clip_window_strategy"], "middle")

    def test_padded_when_stock_shorter_than_broll(self):
        c = add_clip_window(self._clip(2.0), 4.0)
        self.assertEqual(c["clip_use_start_seconds"], 0.0)
        self.assertEqual(c["clip_use_end_seconds"], 2.0)
        self.assertEqual(c["clip_window_strategy"], "padded")

    def test_whole_when_stock_zero_duration(self):
        c = add_clip_window(self._clip(0), 3.0)
        self.assertEqual(c["clip_use_start_seconds"], 0.0)
        self.assertAlmostEqual(c["clip_use_end_seconds"], 3.0)
        self.assertEqual(c["clip_window_strategy"], "whole")

    def test_end_equals_start_plus_broll_duration(self):
        for stock_dur in (5.0, 7.5, 12.0, 30.0):
            for broll_dur in (2.0, 3.0, 4.5, 5.0):
                c = add_clip_window(self._clip(stock_dur), broll_dur)
                if c["clip_window_strategy"] == "middle":
                    self.assertAlmostEqual(
                        c["clip_use_end_seconds"] - c["clip_use_start_seconds"],
                        broll_dur,
                        places=5,
                        msg=f"stock={stock_dur} broll={broll_dur}",
                    )

    def test_none_duration_treated_as_zero(self):
        c = add_clip_window({"duration": None, "id": 1}, 3.0)
        self.assertEqual(c["clip_use_start_seconds"], 0.0)
        self.assertEqual(c["clip_window_strategy"], "whole")

    def test_string_duration_parsed(self):
        c = add_clip_window(self._clip("8"), 3.0)
        self.assertEqual(c["clip_window_strategy"], "middle")


# ---------------------------------------------------------------------------
# Moment normalization helpers (inline, mirrors app_modal.py logic)
# ---------------------------------------------------------------------------

def _normalize_moment(m: dict) -> dict:
    """Mirror of the normalization block inside analyze_stock_broll."""
    import copy
    m = copy.deepcopy(m)
    start = float(m.get("start_seconds", m.get("start", 0)))
    broll_start = float(m.get("broll_start_seconds",
                              m.get("speech_start_seconds",
                                    m.get("start_seconds", start))))
    broll_end   = float(m.get("broll_end_seconds",
                              m.get("speech_end_seconds", broll_start + 3.0)))
    broll_dur   = round(max(2.0, min(5.0, broll_end - broll_start)), 3)
    m["start"]              = start
    m["end"]                = broll_start + broll_dur
    m["broll_duration_seconds"] = broll_dur
    return m


class TestMomentNormalization(unittest.TestCase):

    def test_broll_duration_clamped_min(self):
        m = _normalize_moment({"start_seconds": 10, "broll_start_seconds": 10,
                                "broll_end_seconds": 10.5})
        self.assertEqual(m["broll_duration_seconds"], 2.0)

    def test_broll_duration_clamped_max(self):
        m = _normalize_moment({"start_seconds": 10, "broll_start_seconds": 10,
                                "broll_end_seconds": 20})
        self.assertEqual(m["broll_duration_seconds"], 5.0)

    def test_broll_duration_within_range_unchanged(self):
        m = _normalize_moment({"start_seconds": 10, "broll_start_seconds": 10,
                                "broll_end_seconds": 13.2})
        self.assertAlmostEqual(m["broll_duration_seconds"], 3.2)

    def test_legacy_speech_fields_used_as_fallback(self):
        m = _normalize_moment({"start_seconds": 5, "speech_start_seconds": 5,
                                "speech_end_seconds": 8})
        self.assertAlmostEqual(m["broll_duration_seconds"], 3.0)

    def test_start_below_3_seconds_should_be_filtered(self):
        # Normalization doesn't filter — that's a guard in the outer loop
        m = _normalize_moment({"start_seconds": 1, "broll_start_seconds": 1,
                                "broll_end_seconds": 4})
        self.assertEqual(m["start"], 1.0)


# ---------------------------------------------------------------------------
# _sanitize_transcript tests
# ---------------------------------------------------------------------------

sanitize = _ns.get("_sanitize_transcript")


@unittest.skipIf(sanitize is None, "_sanitize_transcript not found in module")
class TestSanitizeTranscript(unittest.TestCase):

    def test_null_bytes_stripped(self):
        out = sanitize("hello\x00world")
        self.assertNotIn("\x00", out)

    def test_newlines_preserved(self):
        out = sanitize("line one\nline two")
        self.assertIn("\n", out)

    def test_length_capped(self):
        out = sanitize("x" * 100_000, max_chars=1000)
        self.assertEqual(len(out), 1000)

    def test_clean_text_unchanged(self):
        text = "שלום עולם\nHello world"
        self.assertEqual(sanitize(text), text)

    def test_control_chars_stripped(self):
        out = sanitize("before\x1bafter")  # ESC
        self.assertEqual(out, "beforeafter")


# ---------------------------------------------------------------------------
# extract_disqualify_clause tests
# ---------------------------------------------------------------------------

extract_disqualify_clause = _ns.get("extract_disqualify_clause")


@unittest.skipIf(extract_disqualify_clause is None, "extract_disqualify_clause not found in module")
class TestExtractDisqualifyClause(unittest.TestCase):

    def test_well_formed_prompt_returns_clause(self):
        prompt = (
            "yoga instructor guiding student with minimal physical contact. "
            "DISQUALIFY: hands-on adjustments, instructor touching student."
        )
        result = extract_disqualify_clause(prompt)
        self.assertEqual(result, "hands-on adjustments, instructor touching student.")

    def test_no_disqualify_returns_empty(self):
        prompt = "person looking tired and exhausted at a desk."
        self.assertEqual(extract_disqualify_clause(prompt), "")

    def test_empty_string_returns_empty(self):
        self.assertEqual(extract_disqualify_clause(""), "")

    def test_disqualify_at_start_of_string(self):
        prompt = "DISQUALIFY: welcoming gestures, high five, celebration."
        result = extract_disqualify_clause(prompt)
        self.assertEqual(result, "welcoming gestures, high five, celebration.")

    def test_lowercase_disqualify_keyword(self):
        prompt = "burnout imagery. disqualify: energetic, productive, celebratory."
        result = extract_disqualify_clause(prompt)
        self.assertEqual(result, "energetic, productive, celebratory.")

    def test_mixed_case_disqualify_keyword(self):
        prompt = "pensive face close-up. Disqualify: smiling, confident."
        result = extract_disqualify_clause(prompt)
        self.assertEqual(result, "smiling, confident.")

    def test_only_disqualify_keyword_no_clause(self):
        # "DISQUALIFY:" with nothing after → returns empty string
        prompt = "something. DISQUALIFY:"
        result = extract_disqualify_clause(prompt)
        self.assertEqual(result, "")

    def test_whitespace_stripped(self):
        prompt = "scene description. DISQUALIFY:   lots of spaces   "
        result = extract_disqualify_clause(prompt)
        self.assertEqual(result, "lots of spaces")


if __name__ == "__main__":
    unittest.main()
