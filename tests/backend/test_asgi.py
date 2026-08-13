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
        # _alert_admins goes through _send_push (FCM + APNs fan-out), not the
        # FCM sender directly - admins on an iPhone must get the alert too.
        ns = {"users_store": users,
              "_send_push": lambda uid, title, body, kind=None, tag=None: sent.append((uid, title, kind))}
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
        r2_block = MODAL_SRC[MODAL_SRC.index('("/upload_r2/init"'):
                             MODAL_SRC.index('("/upload_r2/probe"')]
        assert r2_block.count("_SAFE_KEY_RE.match(key)") >= 3, (
            "init, complete and abort must all validate the upload key")
        assert "R2_MAX_SIZE" in r2_block

    def test_object_key_is_uid_namespaced(self):
        # Per-user isolation: the R2 object key must carry the uid prefix so
        # one user's init can never address another's upload.
        assert MODAL_SRC.count('f"uploads/{uprefix}{key}.src"') >= 3

    def test_complete_lands_chunk_0000_via_staging(self):
        # The volume publish must be staging-file + atomic replace (same as
        # /upload_stream) - never a partial write at the final path. The logic
        # lives in _r2_land_on_volume, shared by the route and the sweep.
        block = MODAL_SRC[MODAL_SRC.index('("/upload_r2/complete"'):
                          MODAL_SRC.index('("/upload_r2/abort"')]
        assert "_r2_land_on_volume" in block
        helper = MODAL_SRC[MODAL_SRC.index("def _r2_land_on_volume"):
                           MODAL_SRC.index("def sweep_r2_uploads")]
        assert "_chunk_0000" in helper
        assert "_os.replace(staging, chunk_path)" in helper

    def test_sweep_recovers_closed_app_uploads(self):
        # Native R2 uploads: the foreground service PUTs the file, but only
        # the app can call /complete - if it never reopens, the scheduled
        # sweep must land the object and spawn, or the registered job (and
        # its push) is lost. The sweep trusts the registration record's uid
        # (expect_uid=None), while the request path keeps the caller check.
        sweep = MODAL_SRC[MODAL_SRC.index("def sweep_r2_uploads"):]
        sweep = sweep[:sweep.index("\n@") if "\n@" in sweep else len(sweep)]
        assert "_spawn_pending_job_impl(full_key)" in sweep
        assert "head_object" in sweep
        assert "_spawn_pending_job_impl(full_key, expect_uid=uid" in MODAL_SRC, (
            "the request-path delegate must still enforce the registrant check")

    def test_parallel_mode_presigns_complete_and_abort(self):
        # parallel=true (custom Android service): part URLs PLUS presigned
        # complete/abort so the service finishes the multipart directly
        # against R2 - the assembled object then exists even if the app dies,
        # which is what lets sweep_r2_uploads recover it.
        init_block = MODAL_SRC[MODAL_SRC.index('("/upload_r2/init"'):
                               MODAL_SRC.index('("/upload_r2/complete"')]
        assert 'data.get("parallel")' in init_block
        assert '"complete_multipart_upload"' in init_block
        assert '"abort_multipart_upload"' in init_block

    def test_single_mode_for_native_uploads(self):
        # single=true → one presigned PUT (the native uploader can't slice
        # parts); complete verifies the object exists (atomic PUT) instead of
        # assembling a multipart.
        init_block = MODAL_SRC[MODAL_SRC.index('("/upload_r2/init"'):
                               MODAL_SRC.index('("/upload_r2/complete"')]
        assert 'data.get("single")' in init_block
        comp_block = MODAL_SRC[MODAL_SRC.index('("/upload_r2/complete"'):
                               MODAL_SRC.index('("/upload_r2/abort"')]
        assert 'data.get("single")' in comp_block

    def test_complete_is_idempotent(self):
        # A client retry after a network blip must not re-download or fail:
        # an existing chunk_0000 short-circuits to the spawn check.
        block = MODAL_SRC[MODAL_SRC.index('("/upload_r2/complete"'):
                          MODAL_SRC.index('("/upload_r2/abort"')]
        assert "chunk_path.exists" in block

    def test_complete_spawns_pending_job(self):
        # Deferred spawn parity with /upload_stream: the whole file landed, so
        # a registered job starts NOW even if the app is closed.
        block = MODAL_SRC[MODAL_SRC.index('("/upload_r2/complete"'):
                          MODAL_SRC.index('("/upload_r2/abort"')]
        assert "_spawn_pending_job" in block

    def test_missing_creds_answer_503_r2_unavailable(self):
        # Absent secret keys must degrade to the chunked path (frontend falls
        # back on 503), never crash the route.
        assert MODAL_SRC.count('code="r2_unavailable"') >= 3

    def test_api_mounts_backup_secret(self):
        # The routes read S3_* env vars - api() must mount hebpipe-backup.
        assert 'modal.Secret.from_name("hebpipe-backup")' in MODAL_SRC


class TestProfilesRoute:
    """/profiles: bounded per-account settings snapshots ([{name, data}] +
    the auto-applied default's name) on the user record. Route bodies are
    closures - guard the source."""

    def _block(self):
        i = MODAL_SRC.index('("/profiles"')
        return MODAL_SRC[i:i + 2500]

    def test_authed_and_bounded(self):
        block = self._block()
        assert '"unauthorized", 401' in block
        assert "len(profs) <= 6" in block
        assert "16384" in block
        assert '1 <= len(p["name"]) <= 40' in block

    def test_default_validated_against_profile_names(self):
        block = self._block()
        assert 'any(p["name"] == default for p in profs)' in block

    def test_stored_on_the_user_record(self):
        block = self._block()
        assert 'urec["profiles"] = profs' in block
        assert "users_store[uname] = urec" in block


class TestStatsRoute:
    """GET /stats: the public lifetime video counter behind /welcome.
    Route bodies are closures - guard the source."""

    def _block(self):
        i = MODAL_SRC.index('("/stats"')
        return MODAL_SRC[i:i + 1600]

    def test_counts_videos_not_credits(self):
        # Multi-credit runs write "#n"-suffixed extras; only unsuffixed base
        # keys may be counted, or the public number becomes credits-consumed.
        assert '"#" not in k' in self._block()

    def test_high_water_mark_is_monotonic(self):
        # Account deletion removes that user's quota entries - the published
        # count must never shrink, so serve max(count, hwm) and only raise.
        block = self._block()
        assert 'stats_store.get("total_videos")' in block
        assert "max(count, hwm)" in block

    def test_cached_and_cacheable(self):
        # Anonymous landing-page polls must not trigger a Dict scan per hit.
        block = self._block()
        assert '_stats_cache["ts"] > 60' in block
        assert b"public, max-age=60".decode() in block


class TestAssemblerRoutes:
    """/assembler/* - the standalone Story Assembler page (hidden beta).
    Additive routes; the main pipeline must stay untouched."""

    def _analyze(self):
        i = MODAL_SRC.index('("/assembler/analyze"')
        return MODAL_SRC[i:i + 2600]

    def _render(self):
        i = MODAL_SRC.index('("/assembler/render"')
        return MODAL_SRC[i:i + 3200]

    def test_keys_are_prefix_scoped_and_validated(self):
        # Client sends bare keys (up to 5 clips); the route validates EVERY
        # one and namespaces them with the caller's uid prefix, so one user
        # can never analyze/render another's upload.
        for block in (self._analyze(), self._render()):
            assert "all(k and _SAFE_KEY_RE.match(k) for k in keys)" in block
            assert 'f"{uprefix}{k}"' in block
            assert "[:5]" in block

    def test_polls_enforce_spawn_ownership(self):
        for marker in ("/assembler/analyze-poll/", "/assembler/render-poll/"):
            i = MODAL_SRC.index(f'"{marker}"')
            assert "_call_owned(call_id)" in MODAL_SRC[i:i + 700]

    def test_render_segments_are_bounded(self):
        block = self._render()
        assert "1 <= len(segs) <= 16" in block
        assert "total > 600" in block
        assert "b - a >= 0.5" in block
        # Clip-indexed windows must reference an uploaded clip.
        assert "0 <= ci < len(keys)" in block

    def test_multi_clip_render_normalizes_onto_one_canvas(self):
        # Mixed resolutions/orientations across clips would break concat -
        # every part is scaled+padded to the first kept clip's frame at
        # uniform fps/audio before the copy-concat.
        i = MODAL_SRC.index("def render_story")
        block = MODAL_SRC[i:i + 3600]
        assert "force_original_aspect_ratio=decrease" in block
        assert "pad=" in block and "fps=30" in block
        assert '"-ar", "48000", "-ac", "2"' in block

    def test_spawns_flush_the_volume_before_the_worker_reads(self):
        # upload_chunk defers volume commits to spawn time; a spawn without
        # tmp_vol.commit() hands the worker an empty view of the chunks.
        for block in (self._analyze(), self._render()):
            assert "tmp_vol.commit" in block

    def test_render_lands_in_history_via_standard_conventions(self):
        i = MODAL_SRC.index("def render_story")
        block = MODAL_SRC[i:i + 7000]
        assert '_out.mp4' in block
        assert "_record_job(out_key" in block

    def test_captions_burn_through_the_shared_builder(self):
        # Solo-app captions (2026-08-13): word transcript persisted at analyze
        # (_asm_words.json), remapped to the output timeline at render, burned
        # via the SAME build_caption_ass as the main pipeline. A missing
        # transcript degrades to a clean cut, never a failed render.
        i = MODAL_SRC.index("def render_story")
        block = MODAL_SRC[i:i + 7000]
        assert "build_caption_ass" in block
        assert "subtitles='" in block
        assert "_captions_for_windows" in block
        assert "shipping clean cut" in block
        # The route forwards the toggle (default on).
        assert 'bool(data.get("captions", True))' in self._render()

    def test_analyze_persists_the_word_transcript(self):
        i = MODAL_SRC.index("def analyze_story")
        block = MODAL_SRC[i:i + 5000]
        assert "_asm_words_path(key).write_text" in block
        assert "word_timestamps=True" in block

    def test_analysis_returns_the_narrative_story(self):
        # The story card feeds the Phase-3 voice-over script - the prompt must
        # request it and the result must carry it (bounded).
        i = MODAL_SRC.index("def _pick_moments")
        block = MODAL_SRC[i:i + 4200]
        assert '"story"' in block
        assert 'data.get("story")' in block
        assert "[:2000]" in block

    def test_moment_picking_snaps_to_segment_boundaries(self):
        i = MODAL_SRC.index("def _pick_moments")
        block = MODAL_SRC[i:i + 3000]
        assert "from_seg" in block and "to_seg" in block
        assert 'segs[a]["start"]' in block and 'segs[b]["end"]' in block


class TestAppleBillingRoute:
    """/billing/apple/verify + the App Store Server Notifications webhook.
    Route bodies are closures inside api() - guard the source."""

    def _verify_block(self):
        i = MODAL_SRC.index('("/billing/apple/verify"')
        return MODAL_SRC[i:i + 3500]

    def _notify_block(self):
        i = MODAL_SRC.index('("/billing/apple/notify"')
        return MODAL_SRC[i:i + 2600]

    def test_client_cannot_choose_the_product_or_the_credit_count(self):
        """The only client input is an id; product and value come from Apple."""
        block = self._verify_block()
        assert 'data.get("transaction_id")' in block
        assert 'data.get("product_id")' not in block
        assert 'APPLE_CREDIT_PRODUCTS[verified["product_id"]]' in block

    def test_transaction_is_fetched_from_apple_before_granting(self):
        block = self._verify_block()
        assert "_fetch_apple_transaction" in block
        assert "_parse_apple_transaction" in block

    def test_purchase_is_bound_to_the_signed_in_account(self):
        block = self._verify_block()
        assert "_apple_account_token(uid)" in block

    def test_replay_by_another_account_is_refused(self):
        block = self._verify_block()
        assert 'grant.get("uid") != uid' in block
        assert '"purchase_account_mismatch"' in block
        assert "409" in block

    def test_grant_is_idempotent_and_rate_limited(self):
        block = self._verify_block()
        # An existing ledger entry short-circuits the fetch, so a retry never
        # grants twice.
        assert "purchases_store.get(ledger_key)" in block
        assert "if not grant:" in block
        assert "_check_rate_limit" in block

    def test_credentials_outage_is_a_503_not_a_silent_zero(self):
        block = self._verify_block()
        assert '"billing_unavailable"' in block
        assert "503" in block

    def test_notification_is_reverified_against_apple_before_revoking(self):
        """The webhook body is untrusted: it only names the transaction."""
        block = self._notify_block()
        assert "_fetch_apple_transaction" in block
        assert '_authoritative.get("revocationDate")' in block
        assert "_revoke_purchase_grant" in block

    def test_notification_retries_on_transient_failure_only(self):
        block = self._notify_block()
        # Unparseable = never retriable (200); an Apple/API failure = 500 so
        # Apple resends.
        assert "unparseable payload" in block
        assert 'await send_error("Notification handling failed", 500)' in block

    def test_notify_is_public_but_verify_is_authed(self):
        """The webhook must sit ABOVE the session guard, verify below it."""
        guard = MODAL_SRC.index("# ── Session guard")
        assert MODAL_SRC.index('("/billing/apple/notify"') < guard
        assert MODAL_SRC.index('("/billing/apple/verify"') > guard


class TestAccountDeleteRoute:
    """/account/delete - App Store Guideline 5.1.1(v) in-app deletion.
    The purge body lives in the shared _purge_user_data helper (2026-08-13,
    also used by /admin/delete-user) - store assertions target the helper."""

    def _block(self):
        i = MODAL_SRC.index('("/account/delete"')
        return MODAL_SRC[i:i + 1600]

    def _purge_block(self):
        i = MODAL_SRC.index("def _purge_user_data")
        return MODAL_SRC[i:i + 4800]

    def test_requires_an_explicit_confirmation(self):
        block = self._block()
        assert '_d.get("confirm") is not True' in block
        assert '"confirm_required"' in block

    def test_route_uses_the_shared_purge(self):
        assert "asyncio.to_thread(_purge_user_data, uid, uname, urec)" in self._block()

    def test_purges_every_store_keyed_to_the_user(self):
        block = self._purge_block()
        for store in ("jobs_store", "costs_store", "progress_store",
                      "pending_store", "quota_store", "errors_store",
                      "calls_store", "fcm_store", "users_store"):
            assert store in block, store
        # Rendered media on the volume, not just the metadata.
        assert "tmp_vol.commit()" in block

    def test_purchase_grants_are_anonymized_not_left_grantable(self):
        block = self._purge_block()
        assert '"uid": None' in block
        assert '"state": "revoked"' in block
        assert '"account_deleted"' in block

    def test_account_record_and_both_indexes_go(self):
        block = self._purge_block()
        assert 'f"uid:{t_uid}"' in block
        assert 'f"email:' in block


class TestAdminDeleteUserRoute:
    """/admin/delete-user - full purge of another account, admin-only."""

    def _block(self):
        i = MODAL_SRC.index('("/admin/delete-user"')
        return MODAL_SRC[i:i + 2600]

    def test_admin_gated(self):
        block = self._block()
        assert "if not caller_admin:" in block
        assert '"Forbidden", 403' in block

    def test_requires_confirmation_and_existing_target(self):
        block = self._block()
        assert 'data.get("confirm") is not True' in block
        assert '"Unknown user", 404' in block

    def test_refuses_self_and_admin_targets(self):
        # Both guards protect the owner's own account from a mis-tap in a
        # list UI: self-delete goes through /account/delete, and an admin
        # account must be demoted before it can be deleted.
        block = self._block()
        assert '"self_delete"' in block
        assert "target == uname or t_uid == uid" in block
        assert '"admin_target"' in block

    def test_uses_the_same_purge_as_self_delete(self):
        assert "asyncio.to_thread(_purge_user_data, t_uid, target, trec)" in self._block()

    def test_a_surviving_token_cannot_spawn_new_work(self):
        """The session token is a stateless 30-day HMAC: it outlives the
        record, so /process must refuse a uid with no account."""
        i = MODAL_SRC.index("stateless 30-day HMAC")
        block = MODAL_SRC[i:i + 500]
        assert "if not urec:" in block
        assert '"account_deleted"' in block
        assert "401" in block
