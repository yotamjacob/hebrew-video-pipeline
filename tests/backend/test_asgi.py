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

_helpers = _extract_fn(
    MODAL_SRC, "_poll_fn_call", "_check_rate_limit", "_receive_stream_upload")
_poll_fn_call     = _helpers["_poll_fn_call"]
_check_rate_limit = _helpers["_check_rate_limit"]
_receive_stream_upload = _helpers["_receive_stream_upload"]


class TestReceiveStreamUpload:
    """A transport disconnect must never look like a complete video upload."""

    @staticmethod
    def _run(messages, flush_bytes=4):
        import asyncio
        queued = list(messages)
        written = []

        async def receive():
            return queued.pop(0)

        async def write_chunk(data):
            written.append(data)

        result = asyncio.run(_receive_stream_upload(
            receive, write_chunk, flush_bytes=flush_bytes))
        return result, b"".join(written)

    def test_clean_asgi_eof_completes_and_writes_every_byte(self):
        result, written = self._run([
            {"type": "http.request", "body": b"abc", "more_body": True},
            {"type": "http.request", "body": b"def", "more_body": False},
        ])
        assert result == (True, 6)
        assert written == b"abcdef"

    def test_disconnect_is_incomplete_and_never_flushes_partial_tail(self):
        result, written = self._run([
            {"type": "http.request", "body": b"abc", "more_body": True},
            {"type": "http.disconnect"},
        ])
        assert result == (False, 3)
        assert written == b""

    def test_stream_route_publishes_staging_file_only_after_validation(self):
        route = MODAL_SRC[MODAL_SRC.index(
            'if path in ("/upload_stream", "/upload_stream/")'):]
        publish = route.index("_os.replace, staging_path, chunk_path")
        validate = route.index("received_bytes != expected_bytes")
        spawn = route.index("_spawn_pending_job")
        assert validate < publish < spawn


class TestCancelStreamUpload:
    """Start Over must discard every server-side trace of a native upload."""

    def test_cancel_route_cleans_registration_progress_and_partial_files(self):
        route = MODAL_SRC[MODAL_SRC.index(
            'if path in ("/cancel_upload", "/cancel_upload/")'):]
        route = route[:route.index(
            'if path in ("/upload_check", "/upload_check/")')]
        assert "pending_store.pop(full_key)" in route
        assert 'pending_store.pop("done:" + full_key)' in route
        assert "progress_store.pop(full_key)" in route
        assert 'f"{full_key}_chunk_0000"' in route
        assert 'glob(f".{full_key}.*.uploading")' in route


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


# ─────────────────────────────────────────────────────────────────────────────
# _alert_admins — admin selection for error-alert pushes
# ─────────────────────────────────────────────────────────────────────────────

class TestAlertAdmins:
    """Error alerts push to ALL admins (role=='admin' or ADMIN_USERS env),
    skip index entries and regular users, and never raise."""

    def _run(self, users, admin_env=None, monkeypatch=None):
        import os
        sent = []
        ns = {"users_store": users,
              "_send_fcm": lambda uid, title, body, kind=None, tag=None: sent.append((uid, title, kind))}
        fn = _extract_fn(MODAL_SRC, "_alert_admins", extra_ns=ns)["_alert_admins"]
        if admin_env is not None:
            monkeypatch.setenv("ADMIN_USERS", admin_env)
        else:
            monkeypatch.delenv("ADMIN_USERS", raising=False)
        fn("t", "b")
        return sent

    def test_pushes_to_role_admin_only(self, monkeypatch):
        users = {"boss@x.com": {"uid": "a" * 32, "role": "admin"},
                 "user@x.com": {"uid": "b" * 32},
                 "uid:" + "a" * 32: "boss@x.com",
                 "email:user@x.com": "user@x.com"}
        sent = self._run(users, None, monkeypatch)
        assert [s[0] for s in sent] == ["a" * 32]
        assert sent[0][2] == "admin_alert"

    def test_admin_users_env_counts(self, monkeypatch):
        users = {"legacy_admin": {"uid": "c" * 32}, "user@x.com": {"uid": "d" * 32}}
        sent = self._run(users, "Legacy_Admin", monkeypatch)
        assert [s[0] for s in sent] == ["c" * 32]

    def test_never_raises_on_broken_store(self, monkeypatch):
        class Broken:
            def keys(self): raise RuntimeError("boom")
        ns = {"users_store": Broken(), "_send_fcm": lambda *a, **k: None}
        fn = _extract_fn(MODAL_SRC, "_alert_admins", extra_ns=ns)["_alert_admins"]
        monkeypatch.delenv("ADMIN_USERS", raising=False)
        fn("t", "b")   # must not raise


# ─────────────────────────────────────────────────────────────────────────────
# Credit pricing wiring in the router
# ─────────────────────────────────────────────────────────────────────────────

class TestCreditCharging:
    """A run can cost more than one credit (source over 10 min, 4K upscale).
    Route bodies are closures inside api() and can't be extracted, so this
    guards the source. The failure modes are expensive in both directions: a
    charge site left at 1 gives away compute, and a refund that returns only
    the first key keeps a failed job's money."""

    def test_process_prices_the_run_before_checking_quota(self):
        assert "credit_cost = _credit_cost(src_duration, enhance_video)" in MODAL_SRC
        # The whole cost must be available up front - charging 2 to a user
        # holding 1 would leave a negative balance.
        assert "_quota_allows(is_admin, used + credit_cost - 1, limit)" in MODAL_SRC

    def test_both_spawn_paths_charge_the_computed_cost(self):
        # Direct spawn (upload-key and legacy-body variants) plus the deferred
        # spawn from the upload endpoint.
        assert MODAL_SRC.count("_charge_credits(quota_store, uid, call.object_id, credit_cost)") == 1
        assert '_charge_credits(quota_store, rec["uid"], call.object_id, _cost)' in MODAL_SRC

    def test_deferred_registration_carries_the_price(self):
        # The upload endpoint has no query string to re-read, so the cost
        # computed at registration has to travel with the params.
        assert '"credit_cost": credit_cost,' in MODAL_SRC

    def test_every_spawn_tells_the_worker_what_was_charged(self):
        # process_video re-prices from its own probe, so understating the
        # duration in the request buys nothing.
        assert MODAL_SRC.count("credits_charged=(0 if is_admin else credit_cost)") == 2
        assert "credits_charged=(0 if rec.get(\"is_admin\")" in MODAL_SRC

    def test_the_worker_verifies_the_price_against_its_own_probe(self):
        assert "if credits_charged and _credit_cost(duration, enhance_video) > credits_charged:" in MODAL_SRC
        assert 'raise RuntimeError(\n                f"credit_mismatch:' in MODAL_SRC

    def test_the_upscale_cap_is_enforced_for_everyone(self):
        # Not a billing rule: past the cap the job would outrun its timeout, so
        # admins (who are never charged) are gated too.
        assert 'if enhance_video == "esrgan" and not _upscale_allowed(duration):' in MODAL_SRC
        assert 'if enhance_video == "esrgan" and not _upscale_allowed(src_duration):' in MODAL_SRC

    def test_failure_refunds_every_credit_not_just_the_first(self):
        assert "_back = _refund_credits(quota_store, uid, call_id)" in MODAL_SRC
        assert 'del quota_store[f"{uid}:{call_id}"]' not in MODAL_SRC


# ─────────────────────────────────────────────────────────────────────────────
# Open signup — no invite gate, and every new account gets exactly 3 credits
# ─────────────────────────────────────────────────────────────────────────────

class TestOpenSignup:
    """The invite-code gate was removed 2026-08-01 so anyone (including a Play
    reviewer) can create an account. With it gone, the free-tier grant is the
    only thing standing between an open signup and free compute, so both the
    absence of the gate and the size of that grant are pinned here. Route bodies
    are closures inside api(), so this guards the source directly."""

    def test_no_invite_gate_anywhere(self):
        # Not just the env read - any comparison against an invite field would
        # re-close the door on a reviewer who has no code.
        assert 'os.environ.get("INVITE_CODE"' not in MODAL_SRC
        assert 'data.get("invite")' not in MODAL_SRC
        assert 'code="invalid_invite"' not in MODAL_SRC

    def test_terms_acceptance_is_still_required_for_new_accounts(self):
        # Legal gate, not an access gate - it must survive the invite removal.
        assert 'if is_new and not data.get("terms_accepted"):' in MODAL_SRC
        assert MODAL_SRC.count('code="terms_required"') >= 1
        assert '"code": "terms_required"' in MODAL_SRC

    def test_google_signup_asks_for_terms_with_a_flag_old_shells_understand(self):
        # An installed shell only knows `need_invite`; a current one prefers
        # `need_terms`. Sending both keeps every build able to reveal the box.
        assert '"need_terms": True, "need_invite": True,' in MODAL_SRC

    def test_free_tier_is_three_credits(self):
        # Read from source, not by import: CI has no modal installed.
        assert "\nDEFAULT_VIDEO_LIMIT = 3\n" in MODAL_SRC
        # The legacy fallback grandfathers pre-2026-07-31 records and must not
        # be lowered to match - that would take credits from existing users.
        assert "\nLEGACY_VIDEO_LIMIT = 5\n" in MODAL_SRC

    def test_every_account_creation_site_pins_the_free_tier(self):
        # Two creation sites remain (the email-code flow and Google); each must
        # write an EXPLICIT limit, or _quota_state falls back to the legacy 5.
        grant = '"video_limit": DEFAULT_VIDEO_LIMIT, "videos_used": 0}'
        assert MODAL_SRC.count(grant) == 2
        # Every place a brand-new record is written is one of those two.
        assert MODAL_SRC.count('users_store[f"uid:{new_uid}"] = ident') == 2


# ─────────────────────────────────────────────────────────────────────────────
# R2 direct upload — /upload_r2/init|complete|abort|probe (route bodies are
# closures inside api(), so these guard the source directly)
# ─────────────────────────────────────────────────────────────────────────────

class TestR2Upload:
    """The fast web-upload path: presigned multipart PUTs straight to R2, then
    /complete assembles the object and copies it onto the volume as chunk 0000
    (the /upload_stream shape, so processing/pending-spawn/retention are
    untouched)."""

    def test_init_validates_key_and_size(self):
        # Both init and complete must gate on the same key regex as every
        # other upload route - the object key embeds it.
        r2_block = MODAL_SRC[MODAL_SRC.index("/upload_r2/init"):
                             MODAL_SRC.index("/upload_r2/probe")]
        assert r2_block.count("_SAFE_KEY_RE.match(key)") >= 3, (
            "init, complete and abort must all validate the upload key")
        assert "R2_MAX_SIZE" in r2_block

    def test_object_key_is_uid_namespaced(self):
        # Per-user isolation: the R2 object key must carry the uid prefix so
        # one user's init can never address another's upload.
        assert MODAL_SRC.count('f"uploads/{uprefix}{key}.src"') >= 3

    def test_complete_lands_chunk_0000_via_staging(self):
        # The volume publish must be staging-file + atomic replace (same as
        # /upload_stream) - never a partial write at the final path.
        block = MODAL_SRC[MODAL_SRC.index("/upload_r2/complete"):
                          MODAL_SRC.index("/upload_r2/abort")]
        assert '_chunk_0000' in block
        assert "_os.replace(staging, chunk_path)" in block

    def test_complete_is_idempotent(self):
        # A client retry after a network blip must not re-download or fail:
        # an existing chunk_0000 short-circuits to the spawn check.
        block = MODAL_SRC[MODAL_SRC.index("/upload_r2/complete"):
                          MODAL_SRC.index("/upload_r2/abort")]
        assert "chunk_path.exists" in block

    def test_complete_spawns_pending_job(self):
        # Deferred spawn parity with /upload_stream: the whole file landed, so
        # a registered job starts NOW even if the app is closed.
        block = MODAL_SRC[MODAL_SRC.index("/upload_r2/complete"):
                          MODAL_SRC.index("/upload_r2/abort")]
        assert "_spawn_pending_job" in block

    def test_missing_creds_answer_503_r2_unavailable(self):
        # Absent secret keys must degrade to the chunked path (frontend falls
        # back on 503), never crash the route.
        assert MODAL_SRC.count('code="r2_unavailable"') >= 3

    def test_api_mounts_backup_secret(self):
        # The routes read S3_* env vars - api() must mount hebpipe-backup.
        assert 'modal.Secret.from_name("hebpipe-backup")' in MODAL_SRC
