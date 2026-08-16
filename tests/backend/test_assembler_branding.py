"""Assembler branding (2026-08-16): intro/outro clips with fades and a
draggable watermark. Pure helpers executed from source + contract checks."""

from tests.backend.conftest import MODAL_SRC


def _fn(name):
    i = MODAL_SRC.index(f"def {name}")
    j = MODAL_SRC.index("\ndef ", i)
    ns = {}
    exec(MODAL_SRC[i:j], ns)
    return ns[name]


class TestFadeFilters:
    def test_no_fade_is_empty(self):
        assert _fn("_fade_filters")(10.0, 0, 0) == ("", "")

    def test_in_and_out(self):
        vf, af = _fn("_fade_filters")(10.0, 0.5, 1.0)
        assert vf == ",fade=t=in:st=0:d=0.50,fade=t=out:st=9.00:d=1.00"
        assert af == "afade=t=in:st=0:d=0.50,afade=t=out:st=9.00:d=1.00"

    def test_capped_at_half_the_part(self):
        vf, af = _fn("_fade_filters")(1.0, 3.0, 3.0)
        assert "d=0.50" in vf and "st=0.50:d=0.50" in vf   # in 0-0.5, out 0.5-1.0


class TestWatermarkFilter:
    def test_geometry_and_clamps(self):
        g = _fn("_watermark_filter")({"x": 0.9, "y": 0.9, "w": 0.2, "opacity": 0.5}, 1080)
        assert g.startswith("[1:v]scale=216:-1,format=rgba,colorchannelmixer=aa=0.50[wm];")
        # Clamped inside the frame via overlay's W/H/w/h expressions.
        assert "overlay=x='min(max(0\\,0.9000*W)\\,W-w)':y='min(max(0\\,0.9000*H)\\,H-h)'" in g
        assert g.endswith("[vout]")

    def test_out_of_range_values_are_clamped(self):
        g = _fn("_watermark_filter")({"x": 5, "y": -1, "w": 0.001, "opacity": 9}, 1000)
        assert "scale=30:-1" in g          # w floor 0.03 * 1000
        assert "aa=1.00" in g and "1.0000*W" in g and "0.0000*H" in g

    def test_even_width(self):
        assert "scale=180:-1" in _fn("_watermark_filter")({"w": 0.181}, 1000)   # 181 -> 180

    def test_chains_after_subtitles_label(self):
        g = _fn("_watermark_filter")({}, 720, "[v0]", "[vout]")
        assert "[v0][wm]overlay" in g


class TestBrandingContracts:
    def _render(self):
        i = MODAL_SRC.index("def render_story")
        return MODAL_SRC[i:i + 16000]

    def test_body_parts_fade_only_at_the_edges(self):
        block = self._render()
        assert "fade if i == 0 else 0.0" in block
        assert "fade if i == last else 0.0" in block

    def test_intro_outro_wrap_the_captioned_body_and_are_capped(self):
        block = self._render()
        assert "INTRO_OUTRO_MAX_SECONDS = 20.0" in MODAL_SRC
        assert 'min(_probe_duration(src), INTRO_OUTRO_MAX_SECONDS)' in block
        assert '[p for t, p in wrap if t == "intro"] + [visual]' in block
        assert "shipping body only" in block
        # VO still starts on the body when an intro leads.
        assert "adelay={intro_ms}|{intro_ms}" in block

    def test_watermark_shares_the_caption_encode_and_degrades(self):
        block = self._render()
        assert "_watermark_filter(wm, cw" in block
        assert '"-map", "[vout]", "-map", "0:a"' in block
        assert "watermark unavailable - skipped" in block
        assert "_image_ext(raw)" in block

    def test_route_validates_and_forwards_branding(self):
        i = MODAL_SRC.index('("/assembler/render"')
        block = MODAL_SRC[i:i + 6000]
        assert 'for fld in ("intro_key", "outro_key", "wm_key"):' in block
        assert "_SAFE_KEY_RE.match(v)" in block
        assert 'max(0.0, min(3.0, float(data.get("fade") or 0)))' in block
        assert 'brand_keys["wm_key"] if wm else None, wm)' in block
