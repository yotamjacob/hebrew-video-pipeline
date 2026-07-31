"""Video quota tests - free-tier limit logic and admin bypass.

Every /process run consumes one credit; these helpers decide who is an admin
and whether a run is allowed. Wrong answers here either leak GPU money
(limit not enforced) or lock paying users out (admin/limit ignored).
"""
from tests.backend.conftest import MODAL_SRC, _extract_fn, _build_ns

_ns = _build_ns()
# Records predating the 3-credit free tier have no explicit video_limit and
# must keep the OLD allowance - lowering the fallback would take credits back
# from existing users. New records are created with DEFAULT_VIDEO_LIMIT (3).
_ns["DEFAULT_VIDEO_LIMIT"] = 3
_ns["LEGACY_VIDEO_LIMIT"] = 5
_quota = _extract_fn(MODAL_SRC, "_quota_state", "_quota_allows", "_count_quota_used", "_usage_since", extra_ns=_ns)
_quota_state      = _quota["_quota_state"]
_quota_allows     = _quota["_quota_allows"]
_count_quota_used = _quota["_count_quota_used"]
_usage_since      = _quota["_usage_since"]


class _FakeStore:
    """Stand-in for a modal.Dict — supports .keys() and .get()."""
    def __init__(self, keys=None, mapping=None):
        self._map = dict(mapping) if mapping else {k: 0 for k in (keys or [])}
    def keys(self):
        return list(self._map.keys())
    def get(self, k, default=None):
        return self._map.get(k, default)


# ── _quota_state ─────────────────────────────────────────────────────────────

def test_defaults_for_legacy_record_without_quota_fields():
    is_admin, used, limit = _quota_state({"uid": "x"})
    assert (is_admin, used, limit) == (False, 0, 5)

def test_reads_stored_usage_and_limit():
    is_admin, used, limit = _quota_state({"videos_used": 3, "video_limit": 10})
    assert (is_admin, used, limit) == (False, 3, 10)

def test_none_limit_means_unlimited():
    _, _, limit = _quota_state({"video_limit": None})
    assert limit == -1

def test_role_admin_in_record():
    is_admin, _, _ = _quota_state({"role": "admin"})
    assert is_admin

def test_admin_via_env_list_case_insensitive():
    is_admin, _, _ = _quota_state({}, admin_users="Alice, BOB", username="bob")
    assert is_admin

def test_not_admin_when_username_absent_from_env_list():
    is_admin, _, _ = _quota_state({}, admin_users="alice,bob", username="carol")
    assert not is_admin

def test_empty_env_list_grants_nothing():
    is_admin, _, _ = _quota_state({}, admin_users="", username="anyone")
    assert not is_admin

def test_missing_record_is_safe():
    is_admin, used, limit = _quota_state(None)
    assert (is_admin, used, limit) == (False, 0, 5)


# ── _quota_allows ────────────────────────────────────────────────────────────

def test_under_limit_allowed():
    assert _quota_allows(False, 4, 5)

def test_at_limit_blocked():
    assert not _quota_allows(False, 5, 5)

def test_over_limit_blocked():
    assert not _quota_allows(False, 9, 5)

def test_admin_always_allowed():
    assert _quota_allows(True, 9999, 5)

def test_negative_limit_is_unlimited():
    assert _quota_allows(False, 9999, -1)

def test_zero_limit_blocks_everything():
    assert not _quota_allows(False, 0, 0)


# ── _count_quota_used (race-proof authoritative count) ───────────────────────

UID  = "ab" * 16
UID2 = "cd" * 16

def test_counts_only_this_users_unique_entries():
    store = _FakeStore([f"{UID}:call1", f"{UID}:call2", f"{UID2}:call3", "unrelated"])
    assert _count_quota_used(store, UID) == 2
    assert _count_quota_used(store, UID2) == 1

def test_empty_store_is_zero():
    assert _count_quota_used(_FakeStore([]), UID) == 0

def test_prefix_is_not_substring_matched():
    # A different uid whose entries merely contain this uid must not count.
    store = _FakeStore([f"x{UID}:call1", f"{UID2}:call2"])
    assert _count_quota_used(store, UID) == 0

def test_concurrent_spawns_each_add_a_distinct_credit():
    # The whole point: N concurrent spawns write N distinct keys, so the count
    # reflects every consumed credit rather than collapsing to one.
    store = _FakeStore([f"{UID}:call{i}" for i in range(7)])
    assert _count_quota_used(store, UID) == 7

def test_store_failure_is_safe():
    class _Boom:
        def keys(self):
            raise RuntimeError("dict unavailable")
    assert _count_quota_used(_Boom(), UID) == 0


# ── _usage_since (daily digest) ──────────────────────────────────────────────

def test_usage_since_counts_recent_only():
    store = _FakeStore(mapping={
        f"{UID}:c1": 1000, f"{UID}:c2": 2000,
        f"{UID2}:c3": 2000, f"{UID2}:c4": 500,   # 500 is stale
    })
    count, users = _usage_since(store, since_ts=1000)
    assert count == 3          # c1, c2, c3 (c4 too old)
    assert users == 2          # UID and UID2

def test_usage_since_empty():
    assert _usage_since(_FakeStore(mapping={}), since_ts=0) == (0, 0)

def test_usage_since_ignores_malformed_keys():
    store = _FakeStore(mapping={"nocolon": 9999, f"{UID}:c1": 9999})
    count, users = _usage_since(store, since_ts=0)
    assert (count, users) == (1, 1)
