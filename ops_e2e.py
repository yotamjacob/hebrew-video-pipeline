"""Ops-only helper - NOT part of the deployed app (its own tiny Modal app).

Mints a short-lived session for the standing e2e test account so an automated
check can drive the live app (upload -> process -> burn) without anyone's
mailbox. Callable only by someone holding the Modal workspace token, who could
read AUTH_SECRET anyway, so it adds no new access.

The session token is never printed: it is RSA-OAEP encrypted to the caller's
public key, so only the holder of the matching private key can read it out of
the (public) CI log.

Run: modal run ops_e2e.py::mint --pubkey-b64 <base64 of a PEM public key>
"""
import modal

app = modal.App("hebpipe-ops-e2e")
image = modal.Image.debian_slim(python_version="3.11").pip_install("cryptography")

users_store = modal.Dict.from_name("hebpipe-users")
quota_store = modal.Dict.from_name("hebpipe-quota")

E2E_EMAIL = "e2e-bot@hebrew-pipeline.app"
TOKEN_TTL = 2 * 3600          # 2 hours - enough for a check, useless afterwards


@app.function(image=image, secrets=[modal.Secret.from_name("hebpipe-auth")])
def mint(pubkey_b64: str, credits: int = 3) -> str:
    import base64, hashlib, hmac, os, secrets, time
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    now = time.time()
    rec = users_store.get(E2E_EMAIL)
    if not isinstance(rec, dict):
        # Same record shape /auth/verify-code writes for a new account.
        uid = secrets.token_hex(16)
        rec = {"uid": uid, "created": now, "email": E2E_EMAIL, "email_verified": True,
               "auth": "code", "terms_accepted_ts": now, "signup_src": "e2e",
               "video_limit": 0, "videos_used": 0, "e2e_account": True}
        users_store[f"uid:{uid}"] = E2E_EMAIL
        users_store[f"email:{E2E_EMAIL}"] = E2E_EMAIL
    uid = rec["uid"]
    # Keep `credits` AVAILABLE (same approach as the store-review account): the
    # authoritative consumed count is the unique quota-key scan.
    used = sum(1 for k in quota_store.keys() if isinstance(k, str) and k.startswith(f"{uid}:"))
    rec["video_limit"] = used + max(0, min(int(credits), 5))
    users_store[E2E_EMAIL] = rec

    # Mirrors pipeline_core._sign_token (uid.exp.hmac_sha256).
    msg = f"{uid}.{int(now + TOKEN_TTL)}"
    sig = hmac.new(os.environ["AUTH_SECRET"].encode(), msg.encode(), hashlib.sha256).hexdigest()
    token = f"{msg}.{sig}"

    pub = serialization.load_pem_public_key(base64.b64decode(pubkey_b64))
    ct = pub.encrypt(token.encode(), padding.OAEP(
        mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
    out = base64.b64encode(ct).decode()
    print(f"E2E_TOKEN_ENC={out}")
    print(f"[e2e] account ready: used={used} limit={rec['video_limit']} ttl={TOKEN_TTL}s")
    return out
