"""RTL bidi (mixed Hebrew/English/punctuation) + B-roll duration/peak.

The RTL functions are extracted from pipeline_core and executed directly, so we
assert real behaviour. The B-roll changes are asserted against source (they live
inside big Modal functions that can't be imported without Modal).
No network / no Modal.
"""
import ast
import re
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
CORE = (ROOT / "pipeline_core.py").read_text()
FNS  = (ROOT / "pipeline_fns.py").read_text()
BROLL = (ROOT / "broll_fns.py").read_text()

RLE = chr(0x202B)
PDF = chr(0x202C)


def _load_rtl():
    """Exec the RTL helpers + constants from pipeline_core into a namespace."""
    tree = ast.parse(CORE)
    want_fn = {"_fix_rtl_punct", "_rtl_ass_text"}
    want_as = {"_RLM", "_RLE", "_PDF", "_RTL_LEAD_PUNCT_RE"}
    nodes = []
    for n in tree.body:
        if isinstance(n, ast.FunctionDef) and n.name in want_fn:
            nodes.append(n)
        elif isinstance(n, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id in want_as for t in n.targets):
            nodes.append(n)
    ns = {"_re": re, "chr": chr}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "<core>", "exec"), ns)
    return ns


class TestRtlEmbedding:
    def test_each_line_wrapped_in_rtl_embedding(self):
        ns = _load_rtl()
        out = ns["_rtl_ass_text"]("אני אוהב JavaScript, זה ממש כיף")
        # Whole line wrapped so libass gets an RTL base direction (not LTR).
        assert out.startswith(RLE) and out.endswith(PDF)

    def test_multiline_caption_wraps_each_segment(self):
        ns = _load_rtl()
        out = ns["_rtl_ass_text"](r"שלום עולם\Nזה עולה 50 שקל, לא 100")
        segs = out.split(r"\N")
        assert len(segs) == 2
        assert all(s.startswith(RLE) and s.endswith(PDF) for s in segs)

    def test_no_stale_trailing_punct_marks(self):
        # The old approach (RLM after ?/!) is gone; embedding handles bidi.
        ns = _load_rtl()
        out = ns["_rtl_ass_text"]("שאלה? כן!")
        assert out.count(RLE) == 1 and out.count(PDF) == 1
        assert chr(0x200F) not in out   # no leftover RLM injection

    def test_fix_rtl_punct_still_repairs_leading_punct(self):
        # Whisper quirk repair (logical-order, separate from bidi) must remain.
        ns = _load_rtl()
        assert ns["_fix_rtl_punct"]("? שלום") == "שלום?"


class TestBrollDuration:
    def test_clamp_caps_at_three_seconds(self):
        # The validate clamp must cap B-roll at 3s (was 5s).
        assert "elif dur > 3.0: broll_end = broll_start + 3.0" in BROLL
        assert "broll_start + 5.0" not in BROLL

    def test_no_example_or_guidance_over_three_seconds(self):
        # Prompt example durations must all be <= 3.0.
        for val in re.findall(r"broll_duration_seconds: ([0-9.]+)", BROLL):
            assert float(val) <= 3.0, f"prompt example {val}s exceeds 3s cap"

    def test_peak_window_helper_present(self):
        # Burn picks the peak-motion window of the clip.
        assert "def _peak_window(" in FNS
        assert "_peak_window(dst" in FNS   # wired into the burn b-roll loop
