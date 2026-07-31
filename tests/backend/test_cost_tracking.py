"""Per-job compute cost tracking - the evidence behind credit pricing.

A credit is sold per VIDEO while the compute behind one varies by ~40x with
length and by an order of magnitude with the 4K upscale. _cost_summary turns
the recorded job entries into the numbers that decide whether a credit is
priced right, so wrong aggregation here means pricing decisions made on bad
data (and the upscale's cost staying invisible inside an average).
"""
from tests.backend.conftest import MODAL_SRC, _extract_fn, _build_ns

_ns = _build_ns()
_ns["GPU_USD_PER_SEC"] = 0.000222 + 0.00000222 * 4
_ns["CPU_USD_PER_SEC"] = 0.0000131 * 2 + 0.00000222 * 4
_cost_summary = _extract_fn(MODAL_SRC, "_cost_summary", extra_ns=_ns)["_cost_summary"]

GPU_RATE = _ns["GPU_USD_PER_SEC"]
CPU_RATE = _ns["CPU_USD_PER_SEC"]


class _FakeStore:
    """Stand-in for a modal.Dict - supports .keys() and .get()."""
    def __init__(self, mapping=None):
        self._map = dict(mapping or {})

    def keys(self):
        return list(self._map.keys())

    def get(self, k, default=None):
        return self._map.get(k, default)


def _proc(ts, dur=60.0, enhance="none", gpu=120.0):
    return {"ts": ts, "uid": "u1", "dur": dur, "enhance": enhance, "gpu_secs": gpu}


def _burn(ts, secs=30.0, broll=0):
    return {"ts": ts, "uid": "u1", "burn_secs": secs, "broll": broll}


# ── windowing ────────────────────────────────────────────────────────────────

def test_entries_before_the_window_are_ignored():
    store = _FakeStore({"a": _proc(1_000), "b": _proc(100)})
    out = _cost_summary(store, 500)
    assert out["videos"] == 1


def test_empty_store_reports_zero_without_dividing_by_zero():
    out = _cost_summary(_FakeStore(), 0)
    assert out["videos"] == 0
    assert out["usd"] == 0.0
    assert out["usd_per_video"] == 0.0


def test_malformed_entries_are_skipped():
    store = _FakeStore({"a": None, "b": "nonsense", "c": {}, "d": _proc(1_000)})
    out = _cost_summary(store, 0)
    assert out["videos"] == 1


def test_an_unreadable_store_returns_the_empty_shape():
    class _Broken:
        def keys(self):
            raise RuntimeError("dict unavailable")
    out = _cost_summary(_Broken(), 0)
    assert out["videos"] == 0 and out["usd"] == 0.0


# ── dollars ──────────────────────────────────────────────────────────────────

def test_gpu_and_cpu_seconds_are_priced_at_their_own_rates():
    store = _FakeStore({"p": _proc(1_000, gpu=100.0), "b": _burn(1_000, secs=50.0)})
    out = _cost_summary(store, 0)
    assert out["usd"] == round(100.0 * GPU_RATE + 50.0 * CPU_RATE, 4)


def test_cost_per_video_divides_by_videos_not_by_total_entries():
    # The burn belongs to the SAME video as the process run - counting it as a
    # second unit would halve the reported cost of delivering one video.
    store = _FakeStore({"p": _proc(1_000, gpu=100.0), "b": _burn(1_000, secs=50.0)})
    out = _cost_summary(store, 0)
    assert out["videos"] == 1 and out["burns"] == 1
    assert out["usd_per_video"] == out["usd"]


def test_burn_entries_never_count_as_videos():
    store = _FakeStore({"b1": _burn(1_000), "b2": _burn(1_001)})
    out = _cost_summary(store, 0)
    assert out["videos"] == 0 and out["burns"] == 2
    assert out["usd_per_video"] == 0.0     # no video ran, so no per-video figure


def test_broll_jobs_are_counted_only_when_clips_were_used():
    store = _FakeStore({"b1": _burn(1_000, broll=3), "b2": _burn(1_001, broll=0)})
    assert _cost_summary(store, 0)["broll_jobs"] == 1


# ── the upscale, which is the whole point ────────────────────────────────────

def test_upscale_cost_is_broken_out_instead_of_hidden_in_the_average():
    store = _FakeStore({
        "a": _proc(1_000, dur=60.0, enhance="none",   gpu=60.0),
        "b": _proc(1_001, dur=60.0, enhance="esrgan", gpu=1_200.0),
    })
    out = _cost_summary(store, 0)
    assert out["by_mode"]["none"]["n"] == 1
    assert out["by_mode"]["esrgan"]["n"] == 1
    # 20x the GPU for the same source length is exactly the signal a blended
    # average would bury.
    assert out["by_mode"]["none"]["gpu_per_src"] == 1.0
    assert out["by_mode"]["esrgan"]["gpu_per_src"] == 20.0


def test_a_missing_enhance_mode_lands_in_none():
    store = _FakeStore({"a": {"ts": 1_000, "dur": 10.0, "gpu_secs": 10.0}})
    assert _cost_summary(store, 0)["by_mode"]["none"]["n"] == 1


def test_zero_duration_source_does_not_divide_by_zero():
    store = _FakeStore({"a": _proc(1_000, dur=0.0, gpu=30.0)})
    out = _cost_summary(store, 0)
    assert out["by_mode"]["none"]["gpu_per_src"] == 0.0
