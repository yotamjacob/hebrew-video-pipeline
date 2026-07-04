"""Auth helper tests — password hashing, token signing, and key ownership.

These are the primitives the whole user-isolation model rests on, so they are
tested exhaustively: forged/expired/malformed tokens must never yield a uid,
and ownership must be an exact prefix match.
"""
import re

from tests.backend.conftest import MODAL_SRC, _extract_fn, _build_ns

_ns = _build_ns()
_ns["TOKEN_TTL_SECONDS"] = 30 * 24 * 3600   # module constant referenced by _sign_token's default
_ns["MEDIA_TOKEN_TTL_SECONDS"] = 3600       # referenced by _sign_media_token's default
_auth = _extract_fn(MODAL_SRC, "_hash_password", "_verify_password",
                    "_sign_token", "_verify_token",
                    "_sign_media_token", "_verify_media_token",
                    "_user_prefix", "_owned_key",
                    extra_ns=_ns)
_hash_password    = _auth["_hash_password"]
_verify_password  = _auth["_verify_password"]
_sign_token       = _auth["_sign_token"]
_verify_token     = _auth["_verify_token"]
_sign_media_token = _auth["_sign_media_token"]
_verify_media_token = _auth["_verify_media_token"]
_user_prefix      = _auth["_user_prefix"]
_owned_key        = _auth["_owned_key"]

SECRET = "test-secret"
UID    = "ab" * 16   # 32 hex chars


# ── Passwords ────────────────────────────────────────────────────────────────

def test_password_roundtrip():
    salt, h = _hash_password("s3cret-password")
    assert _verify_password("s3cret-password", salt, h)

def test_password_wrong_rejected():
    salt, h = _hash_password("s3cret-password")
    assert not _verify_password("s3cret-passworD", salt, h)
    assert not _verify_password("", salt, h)

def test_password_salted():
    s1, h1 = _hash_password("same-password")
    s2, h2 = _hash_password("same-password")
    assert s1 != s2 and h1 != h2   # unique salt per user


# ── Tokens ───────────────────────────────────────────────────────────────────

def test_token_roundtrip():
    tok = _sign_token(UID, SECRET)
    assert _verify_token(tok, SECRET) == UID

def test_token_expired_rejected():
    tok = _sign_token(UID, SECRET, ttl=10, now=1000.0)
    assert _verify_token(tok, SECRET, now=1005.0) == UID
    assert _verify_token(tok, SECRET, now=1011.0) is None

def test_token_forged_signature_rejected():
    tok = _sign_token(UID, SECRET)
    uid, exp, sig = tok.split(".")
    assert _verify_token(f"{uid}.{exp}.{'0' * len(sig)}", SECRET) is None

def test_token_wrong_secret_rejected():
    tok = _sign_token(UID, SECRET)
    assert _verify_token(tok, "other-secret") is None

def test_token_tampered_uid_rejected():
    tok = _sign_token(UID, SECRET)
    other = "cd" * 16
    _, exp, sig = tok.split(".")
    assert _verify_token(f"{other}.{exp}.{sig}", SECRET) is None

def test_token_tampered_expiry_rejected():
    tok = _sign_token(UID, SECRET, ttl=10, now=1000.0)
    uid, exp, sig = tok.split(".")
    assert _verify_token(f"{uid}.{int(exp) + 999999}.{sig}", SECRET, now=1011.0) is None

def test_token_garbage_rejected():
    for bad in ("", "x", "a.b", "a.b.c.d", None if False else "..", "🎬.123.sig"):
        assert _verify_token(bad, SECRET) is None


# ── Key ownership ────────────────────────────────────────────────────────────

def test_owned_key_exact_prefix():
    key = _user_prefix(UID) + "abc123_cut.mp4"
    assert _owned_key(key, UID)

def test_owned_key_other_user_rejected():
    other = "cd" * 16
    key = _user_prefix(other) + "abc123_cut.mp4"
    assert not _owned_key(key, UID)

def test_owned_key_unprefixed_rejected():
    assert not _owned_key("abc123_cut.mp4", UID)
    assert not _owned_key("", UID)
    assert not _owned_key(None, UID)

def test_owned_key_prefix_spoof_rejected():
    # A key that merely *contains* the prefix mid-string is not owned
    key = "x" + _user_prefix(UID) + "abc_cut.mp4"
    assert not _owned_key(key, UID)

def test_prefix_format():
    assert re.match(r"^u[0-9a-f]{32}__$", _user_prefix(UID))


# ── Media tokens (short-lived, GET-media-scoped) ─────────────────────────────

def test_media_token_roundtrip():
    tok = _sign_media_token(UID, SECRET, now=1000)
    assert _verify_media_token(tok, SECRET, now=1000) == UID

def test_media_token_expired_rejected():
    tok = _sign_media_token(UID, SECRET, ttl=3600, now=1000)
    assert _verify_media_token(tok, SECRET, now=1000 + 3601) is None

def test_media_token_forged_sig_rejected():
    tok = _sign_media_token(UID, SECRET, now=1000)
    forged = tok[:-1] + ("0" if tok[-1] != "0" else "1")
    assert _verify_media_token(forged, SECRET, now=1000) is None

def test_media_token_wrong_secret_rejected():
    tok = _sign_media_token(UID, SECRET, now=1000)
    assert _verify_media_token(tok, "other-secret", now=1000) is None

def test_media_and_session_tokens_not_interchangeable():
    # A session token must not verify as a media token, and vice-versa —
    # the format is disjoint (3 parts vs 4, and the "m." prefix).
    sess  = _sign_token(UID, SECRET, now=1000)
    media = _sign_media_token(UID, SECRET, now=1000)
    assert _verify_media_token(sess, SECRET, now=1000) is None
    assert _verify_token(media, SECRET, now=1000) is None

def test_media_token_bad_uid_rejected():
    # A tampered uid segment that isn't 32 hex chars is rejected even if the
    # rest parses — mirrors _verify_token's uid guard.
    assert _verify_media_token("m.notauid.9999999999.deadbeef", SECRET, now=1000) is None
    assert _verify_media_token("garbage", SECRET, now=1000) is None
