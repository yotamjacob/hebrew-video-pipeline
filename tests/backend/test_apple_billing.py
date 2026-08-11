"""Apple StoreKit grant validation, account binding and credit accounting.

The Play equivalents live in test_play_billing.py; the two providers share one
purchase ledger and one credit counter, so a few tests here deliberately mix
grants from both to pin that a user keeps what they paid for on either store.
"""
import base64
import hashlib
import json
import uuid

import pytest

from tests.backend.conftest import MODAL_SRC, _build_ns, _extract_fn


_ns = _build_ns()
# Constants a helper closes over must be injected - only function defs are
# AST-extracted. These must mirror pipeline_core.py.
_ns["PURCHASE_KEY_PREFIXES"] = ("play:", "apple:")
_ns["APPLE_BUNDLE_ID"] = "com.heb.pipeline"
_ns["_APPLE_TXN_RE"] = _ns["re"].compile(r"^[0-9]{1,32}$")

_apple = _extract_fn(
    MODAL_SRC,
    "_apple_account_token",
    "_apple_ledger_key",
    "_decode_jws_payload",
    "_parse_apple_transaction",
    "_revoke_purchase_grant",
    "_count_purchased_credits",
    extra_ns=_ns,
)
_apple_account_token = _apple["_apple_account_token"]
_apple_ledger_key = _apple["_apple_ledger_key"]
_decode_jws_payload = _apple["_decode_jws_payload"]
_parse_apple_transaction = _apple["_parse_apple_transaction"]
_revoke_purchase_grant = _apple["_revoke_purchase_grant"]
_count_purchased_credits = _apple["_count_purchased_credits"]

PRODUCTS = {
    "pipeline_credits_10": 10,
    "pipeline_credits_30": 30,
    "pipeline_credits_100": 100,
}


class _FakeStore:
    def __init__(self, values):
        self.values = values

    def keys(self):
        return list(self.values)

    def get(self, key, default=None):
        return self.values.get(key, default)

    def __setitem__(self, key, value):
        self.values[key] = value


def _txn(product="pipeline_credits_30", account=None, bundle="com.heb.pipeline",
         quantity=1, revoked=None, kind="Consumable", environment="Production"):
    return {
        "bundleId": bundle,
        "productId": product,
        "type": kind,
        "quantity": quantity,
        "transactionId": "2000000012345678",
        "originalTransactionId": "2000000012345600",
        "appAccountToken": account,
        "revocationDate": revoked,
        "storefront": "ISR",
        "environment": environment,
    }


def _jws(payload: dict) -> str:
    """A JWS whose payload segment decodes to `payload` (signature ignored)."""
    def seg(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).decode().rstrip("=")
    return ".".join((seg(b'{"alg":"ES256"}'),
                     seg(json.dumps(payload).encode()),
                     "c2lnbmF0dXJl"))


# ── Account binding ───────────────────────────────────────────────────────────

def test_account_token_is_a_stable_uuid_that_hides_the_uid():
    uid = "ab" * 16
    token = _apple_account_token(uid)
    # StoreKit rejects anything that is not a UUID.
    assert uuid.UUID(token)
    assert token == _apple_account_token(uid)          # deterministic
    assert uid not in token
    assert token != _apple_account_token("cd" * 16)


def test_account_token_is_derived_from_the_apple_namespace_not_the_play_one():
    """A shared derivation would let a Play token be replayed as an Apple one."""
    uid = "ab" * 16
    digest = hashlib.sha256(f"hebpipe-apple:{uid}".encode()).hexdigest()
    assert _apple_account_token(uid).replace("-", "") == digest[:32]
    play = hashlib.sha256(f"hebpipe-play:{uid}".encode()).hexdigest()
    assert _apple_account_token(uid).replace("-", "") != play[:32]


# ── Ledger keys ───────────────────────────────────────────────────────────────

def test_ledger_key_is_namespaced_to_apple():
    assert _apple_ledger_key("2000000012345678") == "apple:2000000012345678"


@pytest.mark.parametrize("bad", [
    "", "abc", "2000000012345678 ", "../play:x", "1" * 33, "-1", "20000000.5",
])
def test_ledger_key_rejects_anything_but_a_decimal_transaction_id(bad):
    """A hostile client must not be able to write outside the apple: namespace."""
    with pytest.raises(ValueError):
        _apple_ledger_key(bad)


# ── JWS decoding ──────────────────────────────────────────────────────────────

def test_decodes_a_payload_with_missing_base64_padding():
    payload = {"productId": "pipeline_credits_10", "quantity": 1}
    assert _decode_jws_payload(_jws(payload)) == payload


@pytest.mark.parametrize("bad", ["", "a.b", "a.b.c.d", "a.!!!.c", None])
def test_malformed_jws_is_rejected(bad):
    with pytest.raises(ValueError):
        _decode_jws_payload(bad)


def test_non_object_payload_is_rejected():
    seg = base64.urlsafe_b64encode(b"[1,2,3]").decode().rstrip("=")
    with pytest.raises(ValueError):
        _decode_jws_payload(f"aGVhZGVy.{seg}.c2ln")


# ── Transaction validation ────────────────────────────────────────────────────

def test_valid_transaction_yields_normalized_grant_data():
    account = _apple_account_token("u1")
    grant = _parse_apple_transaction(_txn(account=account), PRODUCTS, account)
    assert grant == {
        "product_id": "pipeline_credits_30",
        "quantity": 1,
        "transaction_id": "2000000012345678",
        "order_id": "2000000012345600",
        "region": "ISR",
        "test": False,
    }


def test_sandbox_transactions_are_accepted_and_flagged():
    """App Review and TestFlight purchases are always Sandbox ones."""
    account = _apple_account_token("u1")
    grant = _parse_apple_transaction(
        _txn(account=account, environment="Sandbox"), PRODUCTS, account)
    assert grant["test"] is True


def test_transaction_for_another_account_is_refused():
    with pytest.raises(ValueError) as exc:
        _parse_apple_transaction(
            _txn(account=_apple_account_token("attacker")),
            PRODUCTS, _apple_account_token("victim"))
    assert str(exc.value) == "purchase_account_mismatch"


def test_transaction_with_no_account_token_is_refused():
    """An unbound purchase could otherwise be claimed by any signed-in user."""
    with pytest.raises(ValueError) as exc:
        _parse_apple_transaction(_txn(account=None), PRODUCTS,
                                 _apple_account_token("u1"))
    assert str(exc.value) == "purchase_account_mismatch"


def test_account_token_comparison_is_case_insensitive():
    """StoreKit may echo the UUID back uppercased."""
    account = _apple_account_token("u1")
    grant = _parse_apple_transaction(
        _txn(account=account.upper()), PRODUCTS, account)
    assert grant["product_id"] == "pipeline_credits_30"


def test_transaction_from_another_app_is_refused():
    account = _apple_account_token("u1")
    with pytest.raises(ValueError) as exc:
        _parse_apple_transaction(
            _txn(account=account, bundle="com.someone.else"), PRODUCTS, account)
    assert str(exc.value) == "purchase_bundle_mismatch"


def test_unknown_product_is_refused():
    account = _apple_account_token("u1")
    with pytest.raises(ValueError) as exc:
        _parse_apple_transaction(
            _txn(account=account, product="pipeline_credits_999"), PRODUCTS, account)
    assert str(exc.value) == "purchase_product_mismatch"


def test_non_consumable_product_is_refused():
    account = _apple_account_token("u1")
    with pytest.raises(ValueError) as exc:
        _parse_apple_transaction(
            _txn(account=account, kind="Auto-Renewable Subscription"),
            PRODUCTS, account)
    assert str(exc.value) == "purchase_product_mismatch"


def test_refunded_transaction_never_grants():
    """A refund can land before a slow client retry reaches us."""
    account = _apple_account_token("u1")
    with pytest.raises(ValueError) as exc:
        _parse_apple_transaction(
            _txn(account=account, revoked=1754000000000), PRODUCTS, account)
    assert str(exc.value) == "purchase_refunded"


def test_quantity_is_honoured_but_clamped():
    account = _apple_account_token("u1")
    assert _parse_apple_transaction(
        _txn(account=account, quantity=3), PRODUCTS, account)["quantity"] == 3
    assert _parse_apple_transaction(
        _txn(account=account, quantity=10_000), PRODUCTS, account)["quantity"] == 100
    assert _parse_apple_transaction(
        _txn(account=account, quantity=0), PRODUCTS, account)["quantity"] == 1


# ── Revocation + shared credit accounting ─────────────────────────────────────

def test_revoking_a_grant_is_idempotent_and_removes_the_credits():
    key = _apple_ledger_key("2000000012345678")
    store = _FakeStore({key: {"uid": "u1", "state": "granted", "credits": 30}})
    assert _revoke_purchase_grant(store, key, 1234, "refund") is True
    assert store.get(key)["state"] == "revoked"
    assert store.get(key)["revoked_ts"] == 1234
    assert _count_purchased_credits(store, "u1") == 0
    # A duplicate notification must not double-apply or resurrect anything.
    assert _revoke_purchase_grant(store, key, 9999, "refund") is False
    assert store.get(key)["revoked_ts"] == 1234


def test_revoking_an_unknown_key_is_a_no_op():
    store = _FakeStore({})
    assert _revoke_purchase_grant(store, "apple:404", 1234) is False


def test_credits_are_summed_across_both_stores():
    """A user who bought on Android keeps those credits inside the iOS app."""
    store = _FakeStore({
        "play:a": {"uid": "u1", "state": "granted", "credits": 10},
        "apple:2000000012345678": {"uid": "u1", "state": "granted", "credits": 30},
        "apple:2000000099999999": {"uid": "u2", "state": "granted", "credits": 100},
        "apple:2000000088888888": {"uid": "u1", "state": "revoked", "credits": 100},
        "other:x": {"uid": "u1", "state": "granted", "credits": 999},
    })
    assert _count_purchased_credits(store, "u1") == 40
    assert _count_purchased_credits(store, "u2") == 100
