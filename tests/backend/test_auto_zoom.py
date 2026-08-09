"""
Auto punch-in zoom (Effects tab) — `_zoom_filters` in pipeline_fns.py.

The client computes explicit [start, end] windows (caption_style.zooms) and
the burn renders them verbatim. Since 2026-08-09 each window is a SMOOTH
camera ramp: its own trim → zoompan(smoothstep ease, face-point pan) →
overlay(enable=…) chain, re-offset with setpts so the base track and audio
keep their original timestamps. These tests pin the filter contract:
  - per-window chain shape (split fan-out, trim boundaries, setpts offset)
  - strength → factor mapping (mirrored by ZOOM_FACTORS in site/app.js)
  - ramp ease + face-point expressions (constants mirrored by app.js)
  - garbage in the payload degrades to "no zoom", never an exception
Plus source tripwires: the burn wires the chain in (before B-roll and
subtitles) with per-window face detection, and /preview_frame applies the
same face-framed zoom to the exact still.
"""
from pathlib import Path

from tests.backend.conftest import MODAL_SRC, _extract_fn

ROOT = Path(__file__).parent.parent.parent
FNS_SRC   = (ROOT / "pipeline_fns.py").read_text()
ASGI_SRC  = (ROOT / "app_modal.py").read_text()

_fns = _extract_fn(MODAL_SRC, "_zoom_spans", "_zoom_filters")
_zoom_filters = _fns["_zoom_filters"]
_zoom_spans   = _fns["_zoom_spans"]


def _mk(zooms, strength=None):
    style = {"zooms": zooms}
    if strength is not None:
        style["zoom_strength"] = strength
    return style


class TestZoomFilters:
    def test_single_window_chain_shape(self):
        fs = _zoom_filters(_mk([[1.0, 3.0]]), 1080, 1920, "0:v", "vzoom")
        assert len(fs) == 3
        assert fs[0] == "[0:v]split=2[zbase][zin0]"
        # the zoomed copy: trimmed to its window, zoompan'd, re-offset to S
        assert fs[1].startswith("[zin0]trim=start=1.000:end=3.000,setpts=PTS-STARTPTS,zoompan=")
        assert fs[1].endswith("setpts=PTS+1.000/TB[zc0]")
        assert "s=1080x1920" in fs[1]
        # shown only inside its window, base passes through afterwards
        assert fs[2] == ("[zbase][zc0]overlay=0:0:eof_action=pass:"
                         "enable='between(t,1.000,3.000)'[vzoom]")

    def test_smoothstep_ramp_and_face_pan_expressions(self):
        fs = _zoom_filters(_mk([[1.0, 3.0]]), 1080, 1920, "0:v", "vz",
                           faces=[(0.62, 0.25)])
        zp = fs[1]
        # ease: u clipped 0..1 from ramp-in 0.45s / ramp-out 0.35s (app.js
        # ZOOM_RAMP_* mirror), smoothstepped into the factor
        assert "clip(min(it/0.450,(2.000-it)/0.350),0,1)" in zp
        assert "(3-2*clip(" in zp
        # detected face point stays fixed while zooming
        assert "x='0.6200*(iw-iw/zoom)'" in zp
        assert "y='0.2500*(ih-ih/zoom)'" in zp

    def test_face_default_is_upper_third(self):
        fs = _zoom_filters(_mk([[0.0, 2.0]]), 1080, 1920, "a", "b")
        assert "x='0.5000*(iw-iw/zoom)'" in fs[1]
        assert "y='0.3000*(ih-ih/zoom)'" in fs[1]

    def test_short_window_clamps_ramps_to_half(self):
        fs = _zoom_filters(_mk([[0.0, 0.5]]), 1080, 1920, "a", "b")
        # 0.5s window → both ramps clamp to 0.25s
        assert "it/0.250" in fs[1] and "/0.250)" in fs[1]

    def test_multiple_windows_chain_and_fanout(self):
        fs = _zoom_filters(_mk([[1, 2], [10, 12], [20.5, 22.1]]), 720, 1280, "vrot", "vz")
        assert len(fs) == 1 + 2 * 3                    # split + (zoompan+overlay) each
        assert fs[0] == "[vrot]split=4[zbase][zin0][zin1][zin2]"
        assert "enable='between(t,10.000,12.000)'[zo1]" in fs[3 + 1]
        # the last overlay lands on the requested out label
        assert fs[-1].endswith("[vz]")

    def test_strength_mapping(self):
        # subtle 1.10 / medium 1.18 / strong 1.28 — mirror of app.js ZOOM_FACTORS
        for strength, delta in (("subtle", "0.1000"), ("medium", "0.1800"), ("strong", "0.2800")):
            fs = _zoom_filters(_mk([[0, 1]], strength), 1080, 1920, "a", "b")
            assert f"1+{delta}*" in fs[1], (strength, fs[1])

    def test_unknown_strength_falls_back_to_medium(self):
        fs = _zoom_filters(_mk([[0, 1]], "mega"), 1080, 1920, "a", "b")
        assert "1+0.1800*" in fs[1]

    def test_fps_plumbed_and_bounded(self):
        fs = _zoom_filters(_mk([[0, 1]]), 1080, 1920, "a", "b", fps=59.94)
        assert "fps=59.94" in fs[1]
        for bad in (0, -5, 10_000, "nope", None):
            fs = _zoom_filters(_mk([[0, 1]]), 1080, 1920, "a", "b", fps=bad)
            assert "fps=30" in fs[1]

    def test_empty_and_missing_zooms(self):
        assert _zoom_filters({}, 1080, 1920, "a", "b") == []
        assert _zoom_filters(None, 1080, 1920, "a", "b") == []
        assert _zoom_filters(_mk([]), 1080, 1920, "a", "b") == []

    def test_invalid_spans_dropped_garbage_degrades(self):
        # end <= start and negative starts are dropped
        fs = _zoom_filters(_mk([[5, 5], [3, 1], [-2, 1], [7, 9]]), 1080, 1920, "a", "b")
        assert len(fs) == 3
        assert "between(t,7.000,9.000)" in fs[2]
        # entirely non-numeric payload → no zoom, no exception
        assert _zoom_filters(_mk([["x", "y"]]), 1080, 1920, "a", "b") == []
        assert _zoom_filters(_mk("nope"), 1080, 1920, "a", "b") == []

    def test_window_count_capped(self):
        many = [[i * 10.0, i * 10.0 + 2.0] for i in range(200)]
        fs = _zoom_filters(_mk(many), 1080, 1920, "a", "b")
        assert fs[0].startswith("[a]split=41")         # 40-window cap + base

    def test_zoom_spans_shared_validator(self):
        assert _zoom_spans(_mk([[1, 2], [9, 8]])) == [(1.0, 2.0)]
        assert _zoom_spans({}) == []
        assert _zoom_spans(None) == []


class TestBurnWiring:
    """Source tripwires — the chain must stay wired into burn_captions_fn."""

    def test_burn_computes_zoom_filters_with_faces_and_fps(self):
        assert "zoom_fs = _zoom_filters(caption_style, width, height" in FNS_SRC
        # one face detection per validated window, indices aligned via the
        # shared _zoom_spans validator
        assert "_zspans = _zoom_spans(caption_style)" in FNS_SRC
        assert "_zfaces = [_face_center(video_in" in FNS_SRC
        assert "fps=_probe_fps(video_in), faces=_zfaces" in FNS_SRC

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

    def test_face_detector_is_best_effort(self):
        # Both detectors swallow failure into None/default - a missing cv2 or
        # an undetectable frame must never fail a burn or a preview.
        core = (ROOT / "pipeline_core.py").read_text()
        assert "def _face_center_from_image" in core
        assert "except Exception:\n        return None" in core
        assert "def _face_center(" in FNS_SRC

    def test_preview_frame_applies_face_framed_zoom_before_ass(self):
        route = ASGI_SRC[ASGI_SRC.index('if path in ("/preview_frame", "/preview_frame/")'):]
        route = route[:route.index('if path.startswith("/download/")')]
        assert 'data.get("zoom")' in route
        # clamped to a sane range so a hostile payload can't request a
        # pathological scale
        assert "1.001 < zoom <= 2.0" in route
        # zoom prefix precedes the ass= burn in the vf chain
        assert 'f"{zoom_vf}ass={ap}"' in route
        # face-framed crop with the upper-third default
        assert "_face_center_from_image(fr) or (0.5, 0.30)" in route
        assert "(iw-ow)*{fx:.4f}:(ih-oh)*{fy:.4f}" in route
