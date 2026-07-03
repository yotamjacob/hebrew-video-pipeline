"""Video quota tests - free-tier limit logic and admin bypass.

Every /process run consumes one credit; these helpers decide who is an admin
and whether a run is allowed. Wrong answers here either leak GPU money
(limit not enforced) or lock paying users out (admin/limit ignored).
"""
from tests.backend.conftest import MODAL_SRC, _extract_fn, _build_ns

_ns = _build_ns()
_ns["DEFAULT_VIDEO_LIMIT"] = 5
_quota = _extract_fn(MODAL_SRC, "_quota_state", "_quota_allows", extra_ns=_ns)
_quota_state  = _quota["_quota_state"]
_quota_allows = _quota["_quota_allows"]


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
