"""What one credit buys, and how credits are charged and refunded.

A credit is sold per video, but a long source and the 4K upscale each cost
real extra compute, so both add a credit. Getting this wrong either
overcharges a paying user or hands out compute for free, and the multi-key
charge/refund pair is what keeps a failed job from silently keeping the
user's money.
"""
import pytest

from tests.backend.conftest import MODAL_SRC, _extract_fn, _build_ns

_ns = _build_ns()
_ns.update({
    "CREDIT_SECONDS": 600,
    "UPSCALE_CREDIT_COST": 1,
    "UPSCALE_MAX_SECONDS": 180,
    "CREDIT_DURATION_TOLERANCE": 5.0,
})
_fns = _extract_fn(MODAL_SRC, "_credit_cost", "_upscale_allowed",
                   "_charge_credits", "_refund_credits", extra_ns=_ns)
_credit_cost     = _fns["_credit_cost"]
_upscale_allowed = _fns["_upscale_allowed"]
_charge_credits  = _fns["_charge_credits"]
_refund_credits  = _fns["_refund_credits"]

_count = _extract_fn(MODAL_SRC, "_count_quota_used")["_count_quota_used"]


class _FakeStore(dict):
    """Stand-in for a modal.Dict - dict already has the needed surface."""
    def keys(self):
        return list(super().keys())


# ── _credit_cost ─────────────────────────────────────────────────────────────

def test_a_short_plain_video_costs_one_credit():
    assert _credit_cost(120.0, "none") == 1


def test_a_video_over_ten_minutes_costs_two():
    assert _credit_cost(11 * 60, "none") == 2


def test_exactly_ten_minutes_still_costs_one():
    assert _credit_cost(600.0, "none") == 1


def test_a_hair_over_ten_minutes_is_absorbed_by_the_tolerance():
    # Clients report a float duration; 600.4s must not double the price of a
    # video the user believes is exactly ten minutes.
    assert _credit_cost(600.4, "none") == 1
    assert _credit_cost(606.0, "none") == 2


def test_the_upscale_adds_a_credit():
    assert _credit_cost(120.0, "esrgan") == 2


def test_a_long_upscaled_video_costs_all_three():
    assert _credit_cost(11 * 60, "esrgan") == 3


def test_the_light_touch_filter_is_free():
    assert _credit_cost(120.0, "filters") == 1


@pytest.mark.parametrize("bad", [None, "", "abc", float("nan")])
def test_an_unusable_duration_never_raises_and_never_overcharges(bad):
    # A missing duration must not price the run at more than the base credit.
    assert _credit_cost(bad, "none") == 1


def test_an_unknown_duration_defaults_to_one_credit():
    assert _credit_cost(0.0, "none") == 1


# ── _upscale_allowed ─────────────────────────────────────────────────────────

def test_a_short_source_may_be_upscaled():
    assert _upscale_allowed(120.0) is True


def test_a_source_past_the_cap_may_not():
    # Past this the upscale outruns process_video's timeout, and a timeout
    # refunds the credit AFTER the GPU time was spent.
    assert _upscale_allowed(600.0) is False


def test_an_unknown_duration_defers_to_the_workers_probe():
    assert _upscale_allowed(None) is True
    assert _upscale_allowed("nonsense") is True


# ── charge / refund ──────────────────────────────────────────────────────────

def test_one_credit_keeps_the_historical_key_shape():
    store = _FakeStore()
    assert _charge_credits(store, "u1", "call1", 1) == 1
    assert list(store.keys()) == ["u1:call1"]


def test_extra_credits_are_distinct_keys_so_the_count_is_right():
    store = _FakeStore()
    _charge_credits(store, "u1", "call1", 3)
    assert len(store) == 3
    # The authoritative counter must see all three.
    assert _count(store, "u1") == 3


def test_two_calls_never_collide():
    store = _FakeStore()
    _charge_credits(store, "u1", "call1", 2)
    _charge_credits(store, "u1", "call2", 2)
    assert _count(store, "u1") == 4


def test_a_zero_or_negative_charge_still_costs_one():
    store = _FakeStore()
    _charge_credits(store, "u1", "call1", 0)
    assert _count(store, "u1") == 1


def test_refund_returns_every_credit_the_run_charged():
    store = _FakeStore()
    _charge_credits(store, "u1", "call1", 3)
    assert _refund_credits(store, "u1", "call1") == 3
    assert _count(store, "u1") == 0


def test_refund_is_idempotent_so_concurrent_polls_cannot_double_refund():
    store = _FakeStore()
    _charge_credits(store, "u1", "call1", 2)
    assert _refund_credits(store, "u1", "call1") == 2
    assert _refund_credits(store, "u1", "call1") == 0


def test_refund_leaves_other_calls_alone():
    store = _FakeStore()
    _charge_credits(store, "u1", "call1", 2)
    _charge_credits(store, "u1", "call2", 1)
    _refund_credits(store, "u1", "call1")
    assert _count(store, "u1") == 1
    assert "u1:call2" in store


def test_charging_survives_a_store_that_rejects_writes():
    class _Broken(_FakeStore):
        def __setitem__(self, k, v):
            raise RuntimeError("dict unavailable")
    # Best-effort: a dead store must not take down the spawn.
    assert _charge_credits(_Broken(), "u1", "call1", 2) == 0
