"""
Auto punch-in zoom (Effects tab) — `_zoom_filters` in pipeline_fns.py.

The client computes explicit [start, end] windows (caption_style.zooms) and
the burn renders them verbatim as a split → scale+crop → overlay(enable=…)
chain. These tests pin the filter contract:
  - snap zoom = full-length zoomed copy overlaid only inside the windows
  - strength → factor mapping (mirrored by ZOOM_FACTORS in site/app.js)
  - garbage in the payload degrades to "no zoom", never an exception
Plus source tripwires: the burn wires the chain in (before B-roll and
subtitles), and /preview_frame applies the same zoom to the exact still.
"""
from pathlib import Path

from tests.backend.conftest import MODAL_SRC, _extract_fn

ROOT = Path(__file__).parent.parent.parent
FNS_SRC   = (ROOT / "pipeline_fns.py").read_text()
ASGI_SRC  = (ROOT / "app_modal.py").read_text()

_fns = _extract_fn(MODAL_SRC, "_zoom_filters")
_zoom_filters = _fns["_zoom_filters"]


def _mk(zooms, strength=None):
    style = {"zooms": zooms}
    if strength is not None:
        style["zoom_strength"] = strength
    return style


class TestZoomFilters:
    def test_basic_chain_shape(self):
        fs = _zoom_filters(_mk([[1.0, 3.0]]), 1080, 1920, "0:v", "vzoom")
        assert len(fs) == 3
        assert fs[0] == "[0:v]split[zsa][zsb]"
        # medium default = 1.18 → 1274x2266 (even dims), cropped back to source
        assert "scale=1274:2266" in fs[1] and "crop=1080:1920" in fs[1]
        # face bias: crop center at 30% height (matches CSS transform-origin
        # 50% 30% and the /preview_frame crop)
        assert "(ih-oh)*0.30" in fs[1]
        assert fs[2].startswith("[zsa][zsc]overlay=0:0:enable=")
        assert "between(t,1.000,3.000)" in fs[2]
        assert fs[2].endswith("[vzoom]")

    def test_multiple_windows_one_overlay(self):
        fs = _zoom_filters(_mk([[1, 2], [10, 12], [20.5, 22.1]]), 720, 1280, "vrot", "vz")
        assert len(fs) == 3          # windows share ONE overlay via enable=a+b+c
        assert fs[2].count("between(") == 3
        assert "between(t,10.000,12.000)+between(t,20.500,22.100)" in fs[2]
        assert fs[0].startswith("[vrot]")

    def test_strength_mapping(self):
        # subtle 1.10 / medium 1.18 / strong 1.28 — mirror of app.js ZOOM_FACTORS
        for strength, w in (("subtle", 1188), ("medium", 1274), ("strong", 1382)):
            fs = _zoom_filters(_mk([[0, 1]], strength), 1080, 1920, "a", "b")
            # even-rounded width
            assert f"scale={w}:" in fs[1], (strength, fs[1])

    def test_unknown_strength_falls_back_to_medium(self):
        fs = _zoom_filters(_mk([[0, 1]], "mega"), 1080, 1920, "a", "b")
        assert "scale=1274:" in fs[1]

    def test_empty_and_missing_zooms(self):
        assert _zoom_filters({}, 1080, 1920, "a", "b") == []
        assert _zoom_filters(None, 1080, 1920, "a", "b") == []
        assert _zoom_filters(_mk([]), 1080, 1920, "a", "b") == []

    def test_invalid_spans_dropped_garbage_degrades(self):
        # end <= start and negative starts are dropped
        fs = _zoom_filters(_mk([[5, 5], [3, 1], [-2, 1], [7, 9]]), 1080, 1920, "a", "b")
        assert fs and fs[2].count("between(") == 1
        assert "between(t,7.000,9.000)" in fs[2]
        # entirely non-numeric payload → no zoom, no exception
        assert _zoom_filters(_mk([["x", "y"]]), 1080, 1920, "a", "b") == []
        assert _zoom_filters(_mk("nope"), 1080, 1920, "a", "b") == []

    def test_window_count_capped(self):
        many = [[i * 10.0, i * 10.0 + 2.0] for i in range(200)]
        fs = _zoom_filters(_mk(many), 1080, 1920, "a", "b")
        assert fs[2].count("between(") == 60


class TestBurnWiring:
    """Source tripwires — the chain must stay wired into burn_captions_fn."""

    def test_burn_computes_zoom_filters(self):
        assert "zoom_fs = _zoom_filters(caption_style, width, height" in FNS_SRC

    def test_zoom_forces_complex_path(self):
        # A zoom without B-roll must NOT fall into the simple -vf path.
        assert "if not broll_files and not zoom_fs:" in FNS_SRC

    def test_zoom_applied_before_broll_and_subtitles(self):
        # In the complex path the zoom filters extend `filters` and retarget
        # `prev` BEFORE the b-roll loop (which is before the subtitles append).
        i_zoom  = FNS_SRC.index("filters += zoom_fs")
        i_broll = FNS_SRC.index("for idx, (_, start, end, clip_in_start, clip_in_end) in enumerate(broll_files):")
        i_subs  = FNS_SRC.index('filters.append(f"[{prev}]subtitles=')
        assert i_zoom < i_broll < i_subs

    def test_preview_frame_applies_zoom_before_ass(self):
        route = ASGI_SRC[ASGI_SRC.index('if path in ("/preview_frame", "/preview_frame/")'):]
        route = route[:route.index('if path.startswith("/download/")')]
        assert 'data.get("zoom")' in route
        # clamped to a sane range so a hostile payload can't request a
        # pathological scale
        assert "1.001 < zoom <= 2.0" in route
        # zoom prefix precedes the ass= burn in the vf chain
        assert 'f"{zoom_vf}ass={ap}"' in route
        # same face bias as _zoom_filters
        assert "(ih-oh)*0.30" in route
