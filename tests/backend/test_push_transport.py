"""Push tokens are transport-specific and must never cross over.

Android registers an FCM token; @capacitor/push-notifications on iOS hands back
a raw APNs device token (no Firebase in the iOS app). They share one
`hebpipe-fcm` Dict, so the split is what keeps either platform's notifications
from being black-holed.
"""
import pytest

from tests.backend.conftest import MODAL_SRC, _build_ns, _extract_fn


_ns = _build_ns()
_ns["APNS_TOKEN_PREFIX"] = "apns:"
_push = _extract_fn(MODAL_SRC, "_split_push_tokens", "_apns_payload", extra_ns=_ns)
_split_push_tokens = _push["_split_push_tokens"]
_apns_payload = _push["_apns_payload"]


def test_bare_tokens_are_fcm_and_prefixed_ones_are_apns():
    fcm, apns = _split_push_tokens(["android-token", "apns:AABBCC", "another-fcm"])
    assert fcm == ["android-token", "another-fcm"]
    assert apns == ["AABBCC"]      # the prefix is stripped for the APNs URL


def test_legacy_bare_tokens_stay_fcm():
    """Every token written before iOS existed is unprefixed and is Android."""
    fcm, apns = _split_push_tokens(["t1", "t2"])
    assert fcm == ["t1", "t2"]
    assert apns == []


def test_empty_and_malformed_entries_are_dropped():
    fcm, apns = _split_push_tokens(["", None, 42, "apns:", "ok"])
    assert fcm == ["ok"]
    assert apns == []              # "apns:" with nothing after it is not a token


def test_no_tokens_at_all():
    assert _split_push_tokens([]) == ([], [])
    assert _split_push_tokens(None) == ([], [])


def test_apns_payload_is_a_visible_alert_carrying_the_routing_kind():
    payload = _apns_payload("כותרת", "גוף ההודעה", kind="video_ready")
    assert payload["aps"]["alert"] == {"title": "כותרת", "body": "גוף ההודעה"}
    assert payload["kind"] == "video_ready"
    # A silent push would need a background mode the app does not declare.
    assert "content-available" not in payload["aps"]


def test_apns_payload_defaults_to_video_ready():
    assert _apns_payload("t", "b")["kind"] == "video_ready"


class TestPushRegistrationRoute:
    """/push/register namespaces by platform. Route bodies are closures."""

    def _block(self):
        i = MODAL_SRC.index('("/push/register"')
        return MODAL_SRC[i:i + 1800]

    def test_ios_tokens_are_namespaced_server_side(self):
        block = self._block()
        assert 'data.get("platform") or "").lower() == "ios"' in block
        assert "APNS_TOKEN_PREFIX + token" in block

    def test_client_cannot_forge_the_other_transports_namespace(self):
        """An Android client sending an apns: token must not land in the iOS
        bucket, and vice versa - the prefix is stripped before re-adding."""
        block = self._block()
        assert block.count("removeprefix(APNS_TOKEN_PREFIX)") == 2

    def test_unknown_platform_defaults_to_fcm(self):
        block = self._block()
        assert "else:" in block


class TestSenderIsolation:
    """Each sender takes only its own tokens, and pruning must not eat the
    other platform's."""

    def _fcm(self):
        i = MODAL_SRC.index("def _send_fcm(")
        return MODAL_SRC[i:i + 3000]

    def _apns(self):
        i = MODAL_SRC.index("def _send_apns(")
        return MODAL_SRC[i:i + 4200]

    def test_fcm_skips_ios_tokens(self):
        assert "tokens, _ = _split_push_tokens(all_tokens)" in self._fcm()

    def test_fcm_pruning_preserves_ios_tokens(self):
        """Rewriting from the filtered list would silently unregister iOS."""
        assert "fcm_store[uid] = [t for t in all_tokens if t not in dead]" in self._fcm()

    def test_apns_skips_android_tokens(self):
        assert "_, tokens = _split_push_tokens(all_tokens)" in self._apns()

    def test_apns_pruning_preserves_android_tokens(self):
        block = self._apns()
        assert "dead.append(APNS_TOKEN_PREFIX + token)" in block
        assert "fcm_store[uid] = [t for t in all_tokens if t not in dead]" in block

    def test_apns_is_a_no_op_without_credentials(self):
        """A deploy before the APNs key exists must stay healthy."""
        block = self._apns()
        assert "if not (key_id and team_id and private_key.strip() and uid):" in block
        assert "return" in block

    def test_apns_retries_sandbox_only_on_a_bad_device_token(self):
        """TestFlight/App Store tokens are production, a dev build's are
        sandbox, and nothing here can tell them apart up front."""
        block = self._apns()
        assert "api.push.apple.com" in block
        assert "api.sandbox.push.apple.com" in block
        assert 'reason != "BadDeviceToken"' in block

    def test_apns_uses_http2(self):
        """APNs refuses HTTP/1.1 outright, so requests cannot be used."""
        assert "_httpx.Client(http2=True" in self._apns()

    def test_collapse_id_carries_the_one_notification_story(self):
        assert "apns-collapse-id" in self._apns()

    def test_every_pipeline_push_fans_out_to_both_platforms(self):
        i = MODAL_SRC.index("def _send_push(")
        block = MODAL_SRC[i:i + 400]
        assert "_send_fcm(" in block and "_send_apns(" in block
        # And nothing still calls the FCM sender directly for a user-facing push.
        assert MODAL_SRC.count("_send_fcm(uid, title, body, kind=kind, tag=tag)") == 1
