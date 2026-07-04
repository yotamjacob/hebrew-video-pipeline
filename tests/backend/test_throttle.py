"""Brute-force throttle tests — the persistent, cross-container lockout that
protects password and invite-code guessing.

Wrong answers here either let an attacker guess indefinitely (lockout never
trips) or lock out legitimate users forever (window never resets).
"""
from tests.backend.conftest import MODAL_SRC, _extract_fn, _build_ns

_ns = _build_ns()
_ns["THROTTLE_MAX_FAILS"] = 8
_ns["THROTTLE_WINDOW_SECONDS"] = 15 * 60
_t = _extract_fn(MODAL_SRC, "_throttle_allowed", "_throttle_record_fail", "_throttle_clear", extra_ns=_ns)
_throttle_allowed     = _t["_throttle_allowed"]
_throttle_record_fail = _t["_throttle_record_fail"]
_throttle_clear       = _t["_throttle_clear"]

WINDOW = 15 * 60
MAX    = 8


class _Dict:
    """In-memory stand-in for a modal.Dict (get/pop/setitem)."""
    def __init__(self):
        self.d = {}
    def get(self, k, default=None):
        return self.d.get(k, default)
    def pop(self, k):
        return self.d.pop(k)   # KeyError if missing, like modal.Dict
    def __setitem__(self, k, v):
        self.d[k] = v


def test_allowed_when_no_history():
    ok, retry = _throttle_allowed(_Dict(), "login:alice", now=1000)
    assert ok and retry == 0

def test_locks_out_after_max_fails():
    s = _Dict()
    for _ in range(MAX):
        _throttle_record_fail(s, "login:alice", now=1000)
    ok, retry = _throttle_allowed(s, "login:alice", now=1000)
    assert not ok
    assert 0 < retry <= WINDOW + 1

def test_one_under_max_still_allowed():
    s = _Dict()
    for _ in range(MAX - 1):
        _throttle_record_fail(s, "login:alice", now=1000)
    ok, _ = _throttle_allowed(s, "login:alice", now=1000)
    assert ok

def test_window_expiry_resets():
    s = _Dict()
    for _ in range(MAX):
        _throttle_record_fail(s, "login:alice", now=1000)
    # Same window → still locked
    assert not _throttle_allowed(s, "login:alice", now=1000 + WINDOW - 1)[0]
    # After the window elapses → clean slate
    assert _throttle_allowed(s, "login:alice", now=1000 + WINDOW + 1)[0]

def test_clear_unlocks_immediately():
    s = _Dict()
    for _ in range(MAX):
        _throttle_record_fail(s, "login:alice", now=1000)
    assert not _throttle_allowed(s, "login:alice", now=1000)[0]
    _throttle_clear(s, "login:alice")
    assert _throttle_allowed(s, "login:alice", now=1000)[0]

def test_keys_are_independent():
    s = _Dict()
    for _ in range(MAX):
        _throttle_record_fail(s, "login:alice", now=1000)
    # Bob and a different IP are unaffected by Alice's failures.
    assert _throttle_allowed(s, "login:bob", now=1000)[0]
    assert _throttle_allowed(s, "loginip:1.2.3.4", now=1000)[0]

def test_recording_fail_starts_fresh_window_after_expiry():
    s = _Dict()
    _throttle_record_fail(s, "invite:1.2.3.4", now=1000)
    # A fail long after the first restarts the count from 1, not from stale state.
    _throttle_record_fail(s, "invite:1.2.3.4", now=1000 + WINDOW + 100)
    rec = s.get("t:invite:1.2.3.4")
    assert rec["fails"] == 1

def test_clear_missing_key_is_safe():
    _throttle_clear(_Dict(), "login:nobody")   # must not raise

def test_store_failure_fails_open_on_check():
    class _Boom:
        def get(self, *a, **k):
            raise RuntimeError("down")
    ok, retry = _throttle_allowed(_Boom(), "login:alice", now=1000)
    assert ok and retry == 0   # availability over lockout when the store is broken
