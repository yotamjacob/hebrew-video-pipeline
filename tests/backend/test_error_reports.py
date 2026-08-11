"""Admin error reports: context sanitising, repeat counting, alert copy.

The admin push is the only thing that reaches a phone, so its body has to be
worth reading; the rest lands in the admin-only Errors panel. None of this is
ever visible to a non-admin - /admin/errors is 403 for them.
"""
import json

import pytest

from tests.backend.conftest import MODAL_SRC, _build_ns, _extract_fn


_ns = _build_ns()
_ns["ERROR_CONTEXT_MAX_KEYS"] = 24
_ns["ERROR_CONTEXT_MAX_CHARS"] = 2000
_ns["ERROR_SIMILAR_WINDOW_S"] = 3600
_fns = _extract_fn(
    MODAL_SRC,
    "_sanitize_error_context", "_error_signature", "_count_similar_errors",
    "_error_alert_title", "_error_alert_body",
    extra_ns=_ns,
)
_sanitize_error_context = _fns["_sanitize_error_context"]
_error_signature = _fns["_error_signature"]
_count_similar_errors = _fns["_count_similar_errors"]
_error_alert_title = _fns["_error_alert_title"]
_error_alert_body = _fns["_error_alert_body"]


class _FakeStore:
    def __init__(self, values):
        self.values = values

    def keys(self):
        return list(self.values)

    def get(self, key, default=None):
        return self.values.get(key, default)


# ── Context sanitising ────────────────────────────────────────────────────────

def test_keeps_the_shapes_the_client_actually_sends():
    ctx = _sanitize_error_context({
        "platform": "android", "native": True, "duration": 154, "online": False,
        "options": {"cut": True, "enhance_video": "esrgan"},
        "trail": [{"t": 1.2, "ev": "stage", "d": "upload"}],
    })
    assert ctx["platform"] == "android"
    assert ctx["native"] is True          # bools must not become "True"
    assert ctx["duration"] == 154         # numbers must not become "154"
    assert ctx["online"] is False
    assert ctx["options"] == {"cut": True, "enhance_video": "esrgan"}
    assert ctx["trail"] == [{"t": 1.2, "ev": "stage", "d": "upload"}]


def test_non_dict_context_is_dropped():
    for bad in (None, "nope", 42, ["a"]):
        assert _sanitize_error_context(bad) == {}


def test_key_count_is_capped():
    ctx = _sanitize_error_context({f"k{i}": i for i in range(200)})
    assert len(ctx) <= 24


def test_long_values_are_truncated_not_rejected():
    ctx = _sanitize_error_context({"message": "x" * 5000})
    assert len(ctx["message"]) <= 160


def test_a_pathological_blob_is_trimmed_rather_than_stored():
    """One report must never be able to fill the errors Dict."""
    ctx = _sanitize_error_context({f"k{i}": "y" * 150 for i in range(20)})
    assert ctx.get("_truncated") is True
    assert len(json.dumps(ctx)) < 2000


def test_nested_lists_of_dicts_are_bounded():
    ctx = _sanitize_error_context({"trail": [{"ev": "x", "d": "y"}] * 500})
    assert len(ctx["trail"]) <= 20


# ── Repeat counting ───────────────────────────────────────────────────────────

def test_signature_ignores_numbers_so_ids_do_not_split_a_cluster():
    a = _error_signature("burn", "job 12345 failed after 30s")
    b = _error_signature("burn", "job 98765 failed after 12s")
    assert a == b


def test_signature_separates_different_stages():
    assert _error_signature("burn", "boom") != _error_signature("upload", "boom")


def test_counts_only_recent_look_alikes():
    now = 1_000_000.0
    store = _FakeStore({
        "e:1:aa": {"ts": now - 10, "stage": "burn", "message": "ffmpeg died at 3s"},
        "e:2:bb": {"ts": now - 20, "stage": "burn", "message": "ffmpeg died at 91s"},
        "e:3:cc": {"ts": now - 99999, "stage": "burn", "message": "ffmpeg died at 4s"},
        "e:4:dd": {"ts": now - 5, "stage": "upload", "message": "ffmpeg died at 3s"},
        "other": {"ts": now, "stage": "burn", "message": "ffmpeg died at 3s"},
    })
    # The two recent burn failures match; the hour-old one and the upload do not.
    assert _count_similar_errors(store, "burn", "ffmpeg died at 7s", now) == 2


def test_repeat_count_is_never_zero():
    """The report being counted is itself an occurrence."""
    assert _count_similar_errors(_FakeStore({}), "burn", "boom", 1.0) == 1


def test_a_broken_store_does_not_break_the_alert():
    class _Boom:
        def keys(self):
            raise RuntimeError("dict unavailable")
    assert _count_similar_errors(_Boom(), "burn", "boom", 1.0) == 1


# ── Alert copy ────────────────────────────────────────────────────────────────

def test_title_names_the_stage():
    assert "burn" in _error_alert_title("burn")


def test_a_repeat_gets_a_louder_title():
    once = _error_alert_title("burn", 1)
    many = _error_alert_title("burn", 5)
    assert once != many
    assert "5" in many


def test_body_leads_with_who_and_on_what():
    body = _error_alert_body("alina@example.com", "burn", "v1.47.4",
                             "ffmpeg exited 1", {"platform": "android"}, 1)
    first = body.splitlines()[0]
    assert "alina@example.com" in first
    assert "android" in first and "v1.47.4" in first


def test_body_carries_the_job_shape_that_explains_most_failures():
    body = _error_alert_body("u@x.com", "burn", "v1", "boom", {
        "platform": "ios", "duration": 154,
        "options": {"cut": True, "captions": True, "broll": False,
                    "enhance_video": "esrgan"},
    }, 1)
    assert "2:34" in body                 # duration, human readable
    assert "cut+captions+esrgan" in body  # only what was actually enabled
    assert "broll" not in body


def test_body_flags_a_user_who_was_offline():
    body = _error_alert_body("u@x.com", "upload", "v1", "network lost",
                             {"online": False, "net": "3g"}, 1)
    assert "offline" in body
    assert "3g" in body


def test_body_says_when_it_is_spreading():
    body = _error_alert_body("u@x.com", "burn", "v1", "boom", {}, 7)
    assert "7" in body


def test_body_survives_a_missing_context():
    body = _error_alert_body("u@x.com", "burn", "v1", "boom")
    assert "u@x.com" in body and "boom" in body


def test_body_stays_notification_sized():
    body = _error_alert_body("u@x.com", "burn", "v1", "x" * 2000, {
        "platform": "android", "duration": 600,
        "options": {"cut": True, "captions": True, "hook": True},
    }, 12)
    assert len(body) < 400


class TestErrorReportRoute:
    """Route bodies are closures inside api() - guard the source."""

    def _block(self):
        i = MODAL_SRC.index('("/error-report"')
        return MODAL_SRC[i:i + 2600]

    def test_context_is_sanitised_before_it_becomes_durable_state(self):
        assert "_sanitize_error_context(data.get(\"context\"))" in self._block()

    def test_alert_uses_the_enriched_copy(self):
        block = self._block()
        assert "_error_alert_title(" in block and "_error_alert_body(" in block

    def test_the_store_stays_bounded(self):
        assert "_keys[:-300]" in self._block()

    def test_terminal_job_failures_record_the_server_side_traceback(self):
        i = MODAL_SRC.index("a terminal JOB failure")
        block = MODAL_SRC[i:i + 1800]
        assert '"exception": type(e).__name__' in block
        assert "_tb.format_exception" in block
        # Stored in the same admin-only feed, never returned to the caller.
        assert "errors_store[" in block

    def test_detail_is_admin_only(self):
        """The panel's data source must stay gated."""
        i = MODAL_SRC.index('("/admin/errors"')
        block = MODAL_SRC[i:i + 700]
        assert "if not caller_admin:" in block
        assert 'await send_error("Forbidden", 403)' in block
