"""Google Play Billing grant validation and idempotent credit accounting."""
import hashlib

import pytest

from tests.backend.conftest import MODAL_SRC, _build_ns, _extract_fn


_ns = _build_ns()
_billing = _extract_fn(
    MODAL_SRC,
    "_billing_account_id",
    "_purchase_ledger_key",
    "_count_purchased_credits",
    "_apply_voided_play_purchases",
    "_parse_play_purchase",
    extra_ns=_ns,
)
_billing_account_id = _billing["_billing_account_id"]
_purchase_ledger_key = _billing["_purchase_ledger_key"]
_count_purchased_credits = _billing["_count_purchased_credits"]
_apply_voided_play_purchases = _billing["_apply_voided_play_purchases"]
_parse_play_purchase = _billing["_parse_play_purchase"]


class _FakeStore:
    def __init__(self, values):
        self.values = values

    def keys(self):
        return list(self.values)

    def get(self, key, default=None):
        return self.values.get(key, default)

    def __setitem__(self, key, value):
        self.values[key] = value


def _purchase(product="pipeline_credits_20", account="account", state="PURCHASED",
              quantity=1):
    return {
        "purchaseStateContext": {"purchaseState": state},
        "obfuscatedExternalAccountId": account,
        "orderId": "GPA.1234-5678",
        "regionCode": "IL",
        "productLineItem": [{
            "productId": product,
            "productOfferDetails": {"quantity": quantity},
        }],
    }


def test_account_binding_is_stable_opaque_sha256():
    uid = "ab" * 16
    account = _billing_account_id(uid)
    assert account == hashlib.sha256(f"hebpipe-play:{uid}".encode()).hexdigest()
    assert uid not in account
    assert len(account) == 64


def test_raw_purchase_token_is_not_used_as_ledger_key():
    token = "sensitive-play-token"
    key = _purchase_ledger_key(token)
    assert key.startswith("play:")
    assert token not in key
    assert len(key) == 69


def test_counts_only_unique_grants_owned_by_user():
    uid = "u1"
    store = _FakeStore({
        "play:a": {"uid": uid, "state": "granted", "credits": 5},
        "play:b": {"uid": uid, "state": "granted", "credits": 20},
        "play:c": {"uid": "u2", "state": "granted", "credits": 50},
        "play:d": {"uid": uid, "state": "pending", "credits": 999},
        "other": {"uid": uid, "state": "granted", "credits": 999},
    })
    assert _count_purchased_credits(store, uid) == 25


def test_voided_purchase_revokes_matching_grant_without_storing_raw_token():
    token = "voided-sensitive-token"
    key = _purchase_ledger_key(token)
    store = _FakeStore({
        key: {"uid": "u1", "state": "granted", "credits": 20},
    })
    count = _apply_voided_play_purchases(store, [{
        "purchaseToken": token,
        "voidedReason": 1,
        "voidedSource": 0,
    }], now=1234)
    assert count == 1
    assert store.get(key)["state"] == "revoked"
    assert store.get(key)["revoked_ts"] == 1234
    assert _count_purchased_credits(store, "u1") == 0
    assert token not in repr(store.values)


def test_valid_product_purchase_v2_is_normalized():
    result = _parse_play_purchase(
        _purchase(account="acct", quantity=2),
        "pipeline_credits_20",
        "acct",
    )
    assert result == {
        "product_id": "pipeline_credits_20",
        "quantity": 2,
        "order_id": "GPA.1234-5678",
        "region": "IL",
        "test": False,
    }


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (_purchase(account="wrong"), "purchase_account_mismatch"),
        (_purchase(product="another_product"), "purchase_product_mismatch"),
        (_purchase(state="PENDING"), "purchase_pending"),
        (_purchase(state="CANCELLED"), "purchase_not_completed"),
    ],
)
def test_rejects_ungrantable_purchase(payload, expected):
    with pytest.raises(ValueError, match=expected):
        _parse_play_purchase(payload, "pipeline_credits_20", "account")


def test_paid_credits_extend_a_finite_quota():
    helpers = _extract_fn(
        MODAL_SRC, "_quota_state",
        extra_ns={"DEFAULT_VIDEO_LIMIT": 5},
    )
    assert helpers["_quota_state"](
        {"videos_used": 5, "video_limit": 5}, purchased_credits=20
    ) == (False, 5, 25)


def test_paid_credits_do_not_turn_unlimited_into_a_finite_quota():
    helpers = _extract_fn(
        MODAL_SRC, "_quota_state",
        extra_ns={"DEFAULT_VIDEO_LIMIT": 5},
    )
    assert helpers["_quota_state"](
        {"video_limit": None}, purchased_credits=20
    )[2] == -1
