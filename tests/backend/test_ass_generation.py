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
MODAL_SRC = "\n".join((ROOT / f).read_text() for f in ("pipeline_core.py", "pipeline_fns.py"))


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
        # ASS assembly lives in the shared build_caption_ass (used by both the
        # burn and the /preview_frame WYSIWYG endpoint).
        snippet = _extract_snippet(MODAL_SRC, "build_caption_ass")
        # The ASS header must bind PlayResY to {height}, not a literal
        assert "PlayResY: {height}" in snippet, (
            "PlayResY must be set to the video height variable, not a literal"
        )

    def test_playres_uses_width_variable(self):
        snippet = _extract_snippet(MODAL_SRC, "build_caption_ass")
        assert "PlayResX: {width}" in snippet

    def test_fontsize_uses_font_size_variable(self):
        snippet = _extract_snippet(MODAL_SRC, "build_caption_ass")
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
        snippet = _extract_snippet(MODAL_SRC, "build_caption_ass")
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


# ─── Caption word-wrap (rewrap) parity ───────────────────────────────────────

def _rewrap(text: str, video_w: int, video_h: int, font_size: int) -> str:
    """Mirror of the _rewrap_cap closure in burn_captions_fn."""
    margin_h = max(25, video_w // 14)
    avail    = video_w - 2 * margin_h
    char_w   = font_size * 0.50
    words    = text.replace(r"\N", " ").split()
    if not words:
        return text
    lines: list[str] = []
    cur: list[str]   = []
    cur_w = 0.0
    for word in words:
        ww  = len(word) * char_w
        gap = char_w if cur else 0.0
        if cur and cur_w + gap + ww > avail:
            lines.append(" ".join(cur))
            cur, cur_w = [word], ww
        else:
            cur.append(word)
            cur_w += gap + ww
    if cur:
        lines.append(" ".join(cur))
    return r"\N".join(lines) if lines else text


def _css_approx_lines(text: str, video_w: int, video_h: int, font_size: int,
                      client_h: int) -> int:
    """Estimate the number of CSS lines the preview would show.

    Uses the same 0.50 coefficient so the comparison is apples-to-apples.
    """
    margin_h   = max(25, video_w // 14)
    avail_pct  = (video_w - 2 * margin_h) / video_w  # e.g. 0.857
    client_w   = round(client_h * video_w / video_h)  # player display width
    avail_px   = client_w * avail_pct
    scale      = client_h / video_h
    fs_css     = max(7, round(font_size * scale))
    char_w_css = fs_css * 0.50
    words      = text.replace(r"\N", " ").split()
    if not words:
        return 0
    lines = cur = 0
    cur_w = 0.0
    for word in words:
        ww  = len(word) * char_w_css
        gap = char_w_css if cur else 0.0
        if cur and cur_w + gap + ww > avail_px:
            lines += 1
            cur = 0
            cur_w = ww
        else:
            cur_w += gap + ww
        cur += 1
    return lines + (1 if cur else 0)


class TestCaptionRewrap:
    # The burned video must show the same number of lines as the CSS preview.
    # Strategy: count literal-backslash-N breaks that _rewrap inserts and
    # compare to the CSS-based line-count estimate (both use 0.50 coefficient).

    PORTRAIT_W, PORTRAIT_H = 1080, 1920
    LANDSCAPE_W, LANDSCAPE_H = 1920, 1080
    CLIENT_H = 427  # typical player height for portrait video

    def _burn_lines(self, text, w, h, fs) -> int:
        return _rewrap(text, w, h, fs).count(r"\N") + 1

    def _preview_lines(self, text, w, h, fs) -> int:
        return _css_approx_lines(text, w, h, fs, self.CLIENT_H)

    # ── portrait video cases ──────────────────────────────────────────────────

    def test_short_caption_single_line_portrait(self):
        text = "שלום"
        assert self._burn_lines(text, self.PORTRAIT_W, self.PORTRAIT_H, 48) == 1

    def test_long_caption_wraps_portrait_fs48(self):
        text = "בדיוק שבת עוד לא יצא אז אני"
        burn = self._burn_lines(text, self.PORTRAIT_W, self.PORTRAIT_H, 48)
        prev = self._preview_lines(text, self.PORTRAIT_W, self.PORTRAIT_H, 48)
        assert burn == prev, f"burn={burn} preview={prev}"

    def test_long_caption_wraps_portrait_fs80(self):
        text = "בדיוק שבת עוד לא יצא אז אני"
        burn = self._burn_lines(text, self.PORTRAIT_W, self.PORTRAIT_H, 80)
        prev = self._preview_lines(text, self.PORTRAIT_W, self.PORTRAIT_H, 80)
        assert burn == prev, f"burn={burn} preview={prev}"

    def test_existing_N_stripped_and_rewrapped(self):
        # Text already has \N from a previous process_video pass with different font_size.
        # burn must strip it and re-wrap at the CURRENT font_size.
        old_wrapped = r"בדיוק שבת\Nעוד לא\Nיצא אז אני"
        bare        = "בדיוק שבת עוד לא יצא אז אני"
        fs = 60
        assert (_rewrap(old_wrapped, self.PORTRAIT_W, self.PORTRAIT_H, fs) ==
                _rewrap(bare,        self.PORTRAIT_W, self.PORTRAIT_H, fs)), (
            "Existing \\N in text must be stripped before re-wrapping"
        )

    # ── landscape video cases ─────────────────────────────────────────────────

    def test_long_caption_landscape_fs48(self):
        text = "בדיוק שבת עוד לא יצא אז אני"
        burn = self._burn_lines(text, self.LANDSCAPE_W, self.LANDSCAPE_H, 48)
        prev = _css_approx_lines(text, self.LANDSCAPE_W, self.LANDSCAPE_H, 48,
                                 round(self.CLIENT_H * self.LANDSCAPE_W / self.LANDSCAPE_H))
        assert burn == prev, f"burn={burn} preview={prev}"

    # ── coefficient consistency with source ───────────────────────────────────

    def test_source_uses_050_coefficient(self):
        snippet = _extract_snippet(MODAL_SRC, "build_caption_ass")
        assert "0.50" in snippet, (
            "_rewrap_cap in build_caption_ass must use the 0.50 char-width "
            "coefficient (measured Heebo advance ~0.46) so lines fill the "
            "allowed width without libass re-wrapping"
        )

    def test_generate_ass_uses_055_coefficient(self):
        snippet = _extract_snippet(MODAL_SRC, "generate_ass")
        assert "0.60" in snippet, (
            "generate_ass max_chars must also use 0.60 so initial captions "
            "already have consistent \\N positions before the burn re-wraps"
        )


class TestCaptionStyle:
    """caption_style (hook-parity styling) flows into the Default ASS style:
    text colour → PrimaryColour, outline colour/width → OutlineColour/Outline,
    background box → BorderStyle=4 + BackColour alpha. Defaults must reproduce
    the historical style byte-for-byte."""

    def _build(self, cs=None):
        from tests.backend.conftest import _extract_fn, _build_ns
        ns = _build_ns()
        ns.update({'_fix_rtl_punct': lambda s: s, '_censor_caption_text': lambda s: s,
                   '_rtl_ass_text': lambda s: s, '_RLE': '', '_PDF': ''})
        fn = _extract_fn(MODAL_SRC, 'build_caption_ass', extra_ns=ns)['build_caption_ass']
        return fn(1080, 1920, 'Heebo', 48, 77, 153,
                  [{'start': 0, 'end': 1, 'text': 'שלום'}], {}, caption_style=cs)

    def _style_line(self, ass):
        return next(l for l in ass.splitlines() if l.startswith('Style: Default,'))

    def test_default_style_is_unchanged(self):
        line = self._style_line(self._build(None))
        assert '&H00FFFFFF,&H000000FF,&H00000000,&H80000000,' in line
        assert ',1,2,0,2,' in line   # BorderStyle=1, Outline=2, Shadow=0, Align=2

    def test_empty_dict_same_as_none(self):
        assert self._style_line(self._build({})) == self._style_line(self._build(None))

    def test_font_color_maps_to_primary(self):
        line = self._style_line(self._build({'font_color': '#FF0000'}))
        assert line.split(',')[3] == '&H000000FF'   # red → BGR 0000FF

    def test_outline_color_and_width(self):
        line = self._style_line(self._build({'border_color': '#00FF00', 'border_size': 4}))
        parts = line.split(',')
        assert parts[5] == '&H0000FF00'             # green outline
        assert parts[16] == '4'                     # Outline width

    def test_background_box_uses_borderstyle4_and_alpha(self):
        line = self._style_line(self._build({'bg_color': '#0000FF', 'bg_opacity': 0.6}))
        parts = line.split(',')
        assert parts[15] == '4'                     # BorderStyle=4 (background pad box)
        assert parts[6] == '&H66FF0000'             # alpha (1-0.6)*255=0x66, blue → BGR FF0000

    def test_box_guarantees_padding_with_zero_outline(self):
        line = self._style_line(self._build({'bg_opacity': 0.5, 'border_size': 0}))
        assert int(line.split(',')[16]) >= 1        # pad comes from Outline in BS=4

    def test_no_box_keeps_borderstyle1(self):
        line = self._style_line(self._build({'font_color': '#FFEE00', 'bg_opacity': 0}))
        parts = line.split(',')
        assert parts[15] == '1'
        assert parts[6] == '&H80000000'             # historical BackColour untouched
