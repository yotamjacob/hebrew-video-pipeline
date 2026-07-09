"""One-off admin password reset for a single account.

Runs entirely on the Modal client (local entrypoint) against the live
`hebpipe-users` Dict — no deploy, no container. Re-hashes with the same
PBKDF2 the app uses and writes back ONLY `salt` + `pw`.

    # 1. Find the account (dry run — never writes):
    modal run scripts/reset_password.py --query alina

    # 2. Reset (only applies when exactly one account matches):
    modal run scripts/reset_password.py --query alina --new-password 'bahuoss33' --apply
"""
from pipeline_core import app, users_store, _hash_password


def _find(query: str):
    q = query.strip().casefold()
    matches = []
    for k in users_store.keys():
        # Skip index entries — `uid:<uid>` / `email:<addr>` map to strings.
        if k.startswith("uid:") or k.startswith("email:"):
            continue
        rec = users_store.get(k)
        if not isinstance(rec, dict):
            continue
        email = str(rec.get("email") or "")
        if q in k.casefold() or q in email.casefold():
            matches.append((k, rec))
    return matches


@app.local_entrypoint()
def main(query: str, new_password: str = "", apply: bool = False):
    matches = _find(query)
    print(f"[reset] {len(matches)} match(es) for {query!r}:")
    for k, rec in matches:
        print(f"  key={k!r}  uid={rec.get('uid')}  email={rec.get('email')}  "
              f"role={rec.get('role')}  created={rec.get('created')}")

    if not apply:
        print("[reset] dry-run — re-run with --new-password '...' --apply to reset.")
        return

    if not new_password or len(new_password) < 8:
        print("[reset] ABORT: --new-password missing or under 8 characters.")
        return
    if len(matches) != 1:
        print(f"[reset] ABORT: need exactly 1 match to apply, got {len(matches)}. "
              f"Narrow --query.")
        return

    k, rec = matches[0]
    salt, ph = _hash_password(new_password)
    rec["salt"] = salt
    rec["pw"] = ph
    users_store[k] = rec           # write back the whole record (Dict has no partial update)
    print(f"[reset] DONE — password updated for key={k!r} (uid={rec.get('uid')}).")
