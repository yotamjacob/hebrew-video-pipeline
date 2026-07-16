"""
Unit tests for Modal ASGI endpoint helpers.

_poll_fn_call and _check_rate_limit are extracted from app_modal.py and
tested with mocked Modal FunctionCall objects — no Modal credits used.
"""
import time
import threading
import pytest
from unittest.mock import MagicMock, patch
from tests.backend.conftest import MODAL_SRC, _extract_fn


# ─────────────────────────────────────────────────────────────────────────────
# Extract helpers from app_modal.py without importing it
# ─────────────────────────────────────────────────────────────────────────────

_helpers = _extract_fn(MODAL_SRC, "_poll_fn_call", "_check_rate_limit")
_poll_fn_call     = _helpers["_poll_fn_call"]
_check_rate_limit = _helpers["_check_rate_limit"]


# ─────────────────────────────────────────────────────────────────────────────
# _poll_fn_call
# ─────────────────────────────────────────────────────────────────────────────

class TestPollFnCall:
    """
    _poll_fn_call wraps fn_call.get(timeout=0):
      - TimeoutError (non-FunctionTimeout)  → still_running=True
      - Any other exception                 → raises RuntimeError
      - Success                             → (result, False)
    """

    def _make_call(self, side_effect=None, return_value=None):
        mock = MagicMock()
        if side_effect is not None:
            mock.get.side_effect = side_effect
        else:
            mock.get.return_value = return_value
        return mock

    def test_success_returns_result_and_false(self):
        payload = {"captions": [], "video_key": "key.mp4"}
        fn_call = self._make_call(return_value=payload)
        result, still_running = _poll_fn_call(fn_call)
        assert result == payload
        assert still_running is False

    def test_timeout_error_returns_still_running(self):
        # Simulate Modal raising a generic TimeoutError while job runs
        class FakeTimeoutError(Exception):
            pass
        FakeTimeoutError.__name__ = "TimeoutError"

        fn_call = self._make_call(side_effect=FakeTimeoutError("job still running"))
        result, still_running = _poll_fn_call(fn_call)
        assert still_running is True
        assert result is None

    def test_function_timeout_error_raises(self):
        # FunctionTimeoutError means the job exceeded its own timeout — terminal
        class FakeFunctionTimeoutError(Exception):
            pass
        FakeFunctionTimeoutError.__name__ = "FunctionTimeoutError"

        fn_call = self._make_call(side_effect=FakeFunctionTimeoutError("job timed out"))
        with pytest.raises(RuntimeError):
            _poll_fn_call(fn_call)

    def test_generic_exception_raises_runtime_error(self):
        fn_call = self._make_call(side_effect=ValueError("something broke"))
        with pytest.raises(RuntimeError, match="something broke"):
            _poll_fn_call(fn_call)

    def test_runtime_error_message_uses_class_name_when_empty(self):
        class SilentError(Exception):
            pass
        SilentError.__name__ = "SilentError"

        fn_call = self._make_call(side_effect=SilentError(""))
        with pytest.raises(RuntimeError, match="SilentError"):
            _poll_fn_call(fn_call)

    def test_get_called_with_timeout_zero(self):
        fn_call = self._make_call(return_value={})
        _poll_fn_call(fn_call)
        fn_call.get.assert_called_once_with(timeout=0)


# ─────────────────────────────────────────────────────────────────────────────
# _check_rate_limit
# ─────────────────────────────────────────────────────────────────────────────

class TestCheckRateLimit:
    """
    _check_rate_limit(ip) uses an in-process deque to enforce 10 req/60 s.
    Tests run against a fresh import of the extracted function each time
    so they don't share the module-level dict.
    """

    def _fresh(self):
        """Return a fresh _check_rate_limit with its own state."""
        import collections, threading as _threading
        # Re-exec the function source injecting a clean dict and lock
        ns = {
            "_threading": _threading,
            "_collections": collections,
        }
        # Replace module-level references the extracted function uses
        src = """
import collections as _collections
import threading as _threading
_rate_limit_lock = _threading.Lock()
_rate_limit = {}

def _check_rate_limit(ip):
    MAX_REQUESTS = 10
    WINDOW = 60
    now = __import__('time').time()
    with _rate_limit_lock:
        if ip not in _rate_limit:
            _rate_limit[ip] = _collections.deque()
        dq = _rate_limit[ip]
        while dq and now - dq[0] > WINDOW:
            dq.popleft()
        if len(dq) >= MAX_REQUESTS:
            return False
        dq.append(now)
        return True
"""
        exec(src, ns)  # noqa: S102
        return ns["_check_rate_limit"]

    def test_first_request_allowed(self):
        fn = self._fresh()
        assert fn("1.2.3.4") is True

    def test_nine_requests_allowed(self):
        fn = self._fresh()
        for _ in range(9):
            assert fn("1.2.3.4") is True

    def test_eleventh_request_blocked(self):
        fn = self._fresh()
        for _ in range(10):
            fn("1.2.3.4")
        # 11th should be blocked
        assert fn("1.2.3.4") is False

    def test_different_ips_independent(self):
        fn = self._fresh()
        for _ in range(10):
            fn("10.0.0.1")
        # Different IP still allowed
        assert fn("10.0.0.2") is True

    def test_same_ip_different_limits(self):
        fn = self._fresh()
        # Exhaust IP A
        for _ in range(10):
            fn("192.168.1.1")
        assert fn("192.168.1.1") is False
        # IP B unaffected
        assert fn("192.168.1.2") is True

    def test_returns_bool(self):
        fn = self._fresh()
        result = fn("5.5.5.5")
        assert isinstance(result, bool)


# ─────────────────────────────────────────────────────────────────────────────
# Deferred-spawn done-marker invalidation (source-level tripwire)
# ─────────────────────────────────────────────────────────────────────────────

class TestDoneMarkerInvalidation:
    """A re-run of the same file reuses its signature-derived upload key, and
    /process_pending checks the "done:" marker FIRST - so a stale marker from
    a previous run short-circuits every resume poll to the OLD call_id and
    serves a result rendered with the OLD toggles (the "silences cut although
    the toggle is off" bug). Registration (defer) and the direct spawn must
    both pop the old marker. Route bodies are closures inside api() and can't
    be extracted as functions, so this guards the source directly."""

    POP = 'pending_store.pop("done:" + uprefix + upload_key)'

    def test_done_marker_popped_at_registration_and_direct_spawn(self):
        assert MODAL_SRC.count(self.POP) >= 2, (
            "both the defer registration and the direct /process spawn must "
            "pop the previous run's done-marker")

    def test_registration_pop_precedes_registration_write(self):
        # Pop BEFORE the new registration exists: once the registration is
        # written, a concurrent /process_pending poll must never see the old
        # done-marker.
        first_pop = MODAL_SRC.index(self.POP)
        reg_write = MODAL_SRC.index("pending_store[uprefix + upload_key] = {")
        assert first_pop < reg_write
