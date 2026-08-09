"""GPU-worker encoder selection (NVENC vs libx264, 2026-08-09).

The renders inside process_video pick their video codec via _vcodec_args():
h264_nvenc when the container's ffmpeg+driver expose it (probed once), else
the original libx264 args unchanged. Wrong args here mean either broken
renders (nvenc flags on a CPU box) or silently losing the hardware path.
"""
from tests.backend.conftest import MODAL_SRC, _extract_fn, _build_ns


def _make_vcodec(probe_result):
    ns = _build_ns()
    ns["_nvenc_available"] = lambda: probe_result
    return _extract_fn(MODAL_SRC, "_vcodec_args", extra_ns=ns)["_vcodec_args"]


def test_nvenc_args_when_probe_passes():
    args = _make_vcodec(True)(18)
    assert args[:2] == ["-c:v", "h264_nvenc"]
    # Quality-targeted VBR, not a bitrate cap - the cut-only flow DELIVERS
    # this output directly, so it must be quality-driven like CRF was.
    assert "-cq" in args and "-b:v" in args
    assert args[args.index("-cq") + 1] == "19"
    assert "-tune" in args and "hq" in args


def test_x264_fallback_matches_previous_behavior():
    args = _make_vcodec(False)(18)
    assert args == ["-c:v", "libx264", "-crf", "18", "-preset", "veryfast"]


def test_crf_flows_into_both_paths():
    assert _make_vcodec(False)(20)[3] == "20"
    nv = _make_vcodec(True)(20)
    assert nv[nv.index("-cq") + 1] == "21"


def test_probe_runs_ffmpeg_once_and_caches():
    ns = _build_ns()
    calls = []

    class _R:
        returncode = 1
        stderr = b""

    class _FakeSubprocess:
        PIPE = -1
        DEVNULL = -3

        @staticmethod
        def run(cmd, **kw):
            calls.append(cmd)
            return _R()

    ns["subprocess"] = _FakeSubprocess
    ns["_NVENC_OK"] = None
    fns = _extract_fn(MODAL_SRC, "_nvenc_available", extra_ns=ns)
    avail = fns["_nvenc_available"]
    assert avail() is False
    assert avail() is False
    assert len(calls) == 1                      # cached after the first probe
    assert "h264_nvenc" in calls[0]             # probed the right encoder
