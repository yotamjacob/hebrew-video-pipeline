"""Enhance-video filter chain: applied only when requested, in every render path."""
import re

from tests.backend.conftest import MODAL_SRC, _extract_fn, _build_ns

_ns = _build_ns()

# render() calls run(cmd) — stub it to capture the command
_captured = []
_ns["run"] = lambda cmd: _captured.append(cmd)
_ns["_rotation_vf"] = _extract_fn(MODAL_SRC, "_rotation_vf")["_rotation_vf"]

_render = _extract_fn(MODAL_SRC, "render", extra_ns=_ns)["render"]

ENHANCE_VF = re.search(r'ENHANCE_VIDEO_VF = "([^"]+)"', MODAL_SRC).group(1)


class _Seg:
    def __init__(self, start, end):
        self.start, self.end = start, end


def _fc(cmd):
    return cmd[cmd.index("-filter_complex") + 1]


def test_render_without_enhance_has_no_filter_chain():
    _captured.clear()
    _render("in.mp4", "a.wav", [_Seg(0, 2), _Seg(3, 5)], None, "out.mp4")
    assert ENHANCE_VF not in _fc(_captured[0])


def test_render_with_enhance_applies_chain_before_output():
    _captured.clear()
    _render("in.mp4", "a.wav", [_Seg(0, 2)], None, "out.mp4", extra_vf=ENHANCE_VF)
    fc = _fc(_captured[0])
    assert ENHANCE_VF in fc
    # applied on the concatenated stream, before the [vout] label
    assert fc.index("concat=") < fc.index(ENHANCE_VF) < fc.index("[vout]")


def test_render_enhance_composes_with_rotation():
    _captured.clear()
    _render("in.mp4", "a.wav", [_Seg(0, 2)], None, "out.mp4", rotation=90, extra_vf=ENHANCE_VF)
    fc = _fc(_captured[0])
    assert "transpose=cclock" in fc and ENHANCE_VF in fc
    assert fc.index("transpose=cclock") < fc.index(ENHANCE_VF)


def test_enhance_chain_is_valid_filtergraph_syntax():
    # every element must be name=args, commas separating filters
    for f in ENHANCE_VF.split(","):
        assert re.match(r"^[a-z0-9_]+=[^,]+$", f), f
