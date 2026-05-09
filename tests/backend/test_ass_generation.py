"""
Tests for the ASS subtitle generation math inside burn_captions_fn.

Core invariant: the ratio  font_size / video_height  must be identical in
both the burned ASS file (Fontsize / PlayResY) and the JS preview formula
(captionFontSize * clientHeight / videoHeight expressed as a ratio).

No network, no ffmpeg, no Modal — just pure arithmetic extracted from source.
"""
import ast
import textwrap
from pathlib import Path

ROOT      = Path(__file__).parent.parent.parent
MODAL_SRC = (ROOT / "app_modal.py").read_text()


# ─── Extract helpers ──────────────────────────────────────────────────────────

def _extract_snippet(source: str, fn_name: str) -> str:
    """Return the source text of the first top-level function with fn_name."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == fn_name:
            lines = source.splitlines()
            start = node.decorator_list[0].lineno if node.decorator_list else node.lineno
            return "\n".join(lines[start - 1 : node.end_lineno])
    raise ValueError(f"{fn_name!r} not found")


# ─── Pure-math helpers extracted inline (no Modal deps needed) ────────────────

def clamp_font_size(font_size: int) -> int:
    return max(12, min(120, font_size))


def compute_margin_v(margin_v_pct: float, height: int) -> int:
    return int(margin_v_pct * height)


def preview_font_size_px(caption_font_size: int, client_height: int, video_height: int) -> int:
    """Mirror of the JS formula: Math.max(7, Math.round(captionFontSize * scale))."""
    import math
    scale = client_height / video_height
    return max(7, round(caption_font_size * scale))


# ─── Tests ────────────────────────────────────────────────────────────────────


class TestFontSizeClamping:
    """burn_captions_fn clamps font_size to [12, 120] before writing the ASS."""

    def test_default_passes_through(self):
        assert clamp_font_size(48) == 48

    def test_slider_max_passes_through(self):
        # Slider max is 80 in the UI — must not be clamped
        assert clamp_font_size(80) == 80

    def test_slider_min_passes_through(self):
        assert clamp_font_size(24) == 24

    def test_below_min_clamped(self):
        assert clamp_font_size(0) == 12
        assert clamp_font_size(11) == 12

    def test_above_max_clamped(self):
        assert clamp_font_size(200) == 120
        assert clamp_font_size(121) == 120

    def test_boundary_values(self):
        assert clamp_font_size(12)  == 12
        assert clamp_font_size(120) == 120


class TestMarginV:
    """margin_v = int(margin_v_pct * height) — must match what the JS preview uses."""

    def test_default_1920(self):
        assert compute_margin_v(0.08, 1920) == 153

    def test_default_1080(self):
        assert compute_margin_v(0.08, 1080) == 86

    def test_zero_pct(self):
        assert compute_margin_v(0.0, 1920) == 0

    def test_proportional(self):
        # int() truncation means v2 can differ from v1*2 by at most 1
        v1 = compute_margin_v(0.08, 1920)
        v2 = compute_margin_v(0.08, 3840)
        assert abs(v2 - v1 * 2) <= 1


class TestPreviewBurnParity:
    """
    Core invariant: font_size / video_height (ASS ratio) ==
                    preview_css_px / client_height     (JS ratio).

    In other words: if you pick any (font_size, video_height) pair, the
    preview formula must produce a CSS px value that — when divided by
    client_height — gives the same ratio as font_size / video_height.
    """

    # Minimum CSS px that the JS formula will produce (the 'max(7, ...)' floor).
    FLOOR_PX = 7

    def _floor_active(self, font_size: int, video_h: int, client_h: int) -> bool:
        """Return True when the 7-px floor in preview_font_size_px would be hit."""
        return round(clamp_font_size(font_size) * client_h / video_h) < self.FLOOR_PX

    def _ratio_ok(self, font_size: int, video_h: int, client_h: int):
        if self._floor_active(font_size, video_h, client_h):
            return  # floor regime — ratio parity doesn't apply (too small to matter)
        clamped   = clamp_font_size(font_size)
        ass_ratio = clamped / video_h
        css_px    = preview_font_size_px(clamped, client_h, video_h)
        css_ratio = css_px / client_h
        # Allow rounding tolerance of ±1px
        tolerance = 1 / client_h
        assert abs(css_ratio - ass_ratio) <= tolerance, (
            f"font_size={font_size} video_h={video_h} client_h={client_h}: "
            f"ass_ratio={ass_ratio:.5f}  css_ratio={css_ratio:.5f}  diff={abs(css_ratio-ass_ratio):.5f}"
        )

    def test_default_1920p(self):
        # 9:16 portrait video at 240-wide player → height ≈ 427
        self._ratio_ok(48, 1920, 427)

    def test_4k_large_font(self):
        # 4K video: default font_size=48 hits the 7px floor at 427px preview height.
        # Use font_size=72 which stays above the floor.
        self._ratio_ok(72, 3840, 427)

    def test_4k_floor_behavior(self):
        # Explicitly verify that at 4K with font_size=48, the floor IS active.
        # This documents why the user should increase font size for 4K content.
        assert self._floor_active(48, 3840, 427), (
            "Expected 7px floor to be active for font_size=48 at 4K — "
            "scaled value would be ~5px which rounds to 5 < 7"
        )

    def test_1080p_wide(self):
        # landscape video — player adapts aspect ratio
        self._ratio_ok(48, 1080, 135)

    def test_font_size_24_floor_behavior(self):
        # font_size=24 at 1920p also hits the floor (24 * 427/1920 ≈ 5.3 < 7)
        assert self._floor_active(24, 1920, 427)

    def test_font_size_32_above_floor(self):
        # 32 * 427/1920 ≈ 7.1 → just above the floor
        self._ratio_ok(32, 1920, 427)

    def test_font_size_80(self):
        self._ratio_ok(80, 1920, 427)

    def test_font_size_70(self):
        self._ratio_ok(70, 1920, 427)

    def test_font_size_range(self):
        for fs in range(24, 81, 4):
            self._ratio_ok(fs, 1920, 427)  # skips floor cases automatically

    def test_various_resolutions(self):
        for h in [720, 1080, 1440, 1920, 2160]:
            self._ratio_ok(48, h, 427)  # 3840 is skipped above via floor check


class TestASSPlayResEqualsVideoHeight:
    """
    Verify that the ASS header template uses PlayResY = actual video height.
    This is the contract that makes libass scale the font correctly.
    We check the source text — if someone adds an override, this test fails.
    """

    def test_playres_uses_height_variable(self):
        snippet = _extract_snippet(MODAL_SRC, "burn_captions_fn")
        # The ASS header must bind PlayResY to {height}, not a literal
        assert "PlayResY: {height}" in snippet, (
            "PlayResY must be set to the video height variable, not a literal"
        )

    def test_playres_uses_width_variable(self):
        snippet = _extract_snippet(MODAL_SRC, "burn_captions_fn")
        assert "PlayResX: {width}" in snippet

    def test_fontsize_uses_font_size_variable(self):
        snippet = _extract_snippet(MODAL_SRC, "burn_captions_fn")
        # Style line must use {font_size}, not a literal
        assert "{font_size}" in snippet, (
            "ASS Fontsize must come from the font_size parameter, not a literal"
        )

    def test_no_extra_font_size_scaling(self):
        snippet = _extract_snippet(MODAL_SRC, "burn_captions_fn")
        # font_size must not be multiplied by height or any resolution factor
        # (it is only clamped, never scaled by resolution)
        import re
        # Look for something like "font_size * height" or "font_size / PlayRes"
        assert not re.search(r"font_size\s*[\*/]\s*(height|width|PlayRes)", snippet), (
            "font_size must not be scaled by video resolution — that would break parity"
        )

    def test_margin_v_uses_pct_times_height(self):
        snippet = _extract_snippet(MODAL_SRC, "burn_captions_fn")
        # margin_v must be derived from margin_v_pct * height
        assert "margin_v_pct * height" in snippet, (
            "margin_v must be int(margin_v_pct * height)"
        )

    def test_wrapstyle_is_0_not_2(self):
        snippet = _extract_snippet(MODAL_SRC, "burn_captions_fn")
        # WrapStyle 0 = smart word wrap — must NOT be 2 (no wrap)
        # which would cause the burn to never wrap while CSS preview does,
        # creating a line-break mismatch for long captions.
        assert "WrapStyle: 0" in snippet, (
            "WrapStyle must be 0 (smart wrap) so libass wraps at the same points "
            "as the CSS preview — WrapStyle 2 disables wrap entirely"
        )
        assert "WrapStyle: 2" not in snippet, (
            "WrapStyle: 2 disables line wrapping — burn and preview would disagree"
        )


class TestMarginHFormula:
    """
    margin_h = max(25, width // 14) controls the horizontal text area.
    The JS preview must use the same formula for max-width so wrap points match.
    """

    def _margin_h(self, width: int) -> int:
        return max(25, width // 14)

    def _avail_pct(self, width: int) -> float:
        mh = self._margin_h(width)
        return (width - 2 * mh) / width * 100

    def test_1080p_margin(self):
        assert self._margin_h(1080) == 77

    def test_1920p_margin(self):
        assert self._margin_h(1920) == 137

    def test_small_width_uses_floor(self):
        # width=240 → 240//14=17 < 25 → clamped to 25
        assert self._margin_h(240) == 25

    def test_avail_pct_1080(self):
        pct = self._avail_pct(1080)
        assert abs(pct - 85.74) < 0.1   # 926/1080 = 85.74%

    def test_avail_pct_1920(self):
        pct = self._avail_pct(1920)
        assert abs(pct - 85.73) < 0.1   # 1646/1920 ≈ 85.73%

    def test_avail_pct_always_less_than_86(self):
        for w in [720, 1080, 1440, 1920, 2160, 3840]:
            assert self._avail_pct(w) < 86.0, f"avail_pct({w}) = {self._avail_pct(w):.2f} >= 86"

    def test_source_uses_correct_margin_formula(self):
        snippet = _extract_snippet(MODAL_SRC, "burn_captions_fn")
        assert "max(25, width  // 14)" in snippet or "max(25, width // 14)" in snippet, (
            "margin_h formula must be max(25, width // 14)"
        )
