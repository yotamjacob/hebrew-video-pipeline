"""Send a built Pipeline email through the app's Resend sender (ephemeral Modal run).

Uses the production `hebpipe-auth` secret exactly like pipeline_core._send_email,
so mail comes from the app's verified EMAIL_FROM. Replies go to hebrewpipeline@gmail.com.

    # one address (test)
    modal run tools/mail/send.py --spec tools/mail/specs/hp100.json --to you@example.com

    # every registered account: first count, then send
    modal run tools/mail/send.py --spec tools/mail/specs/hp100.json --all-users --dry-run
    modal run tools/mail/send.py --spec tools/mail/specs/hp100.json --all-users

    # a list file (one address per line)
    modal run tools/mail/send.py --spec tools/mail/specs/hp100.json --list recipients.txt

Every send is appended to tools/mail/out/<name>/sent.jsonl and skipped on the
next run, so a stopped campaign resumes where it left off. Resend allows ~2
requests/second; sends are paced at 0.6 s and retried on 429.
Addresses in --exclude (comma separated) are always skipped; the test account
yotam.jacob@hellostencil.com is excluded by default.
"""
import json
import time
from pathlib import Path

import modal

app = modal.App("hp-mail-send")
HERE = Path(__file__).resolve().parent
REPLY_TO = "hebrewpipeline@gmail.com"
DEFAULT_EXCLUDE = {"yotam.jacob@hellostencil.com"}


@app.function(timeout=300)
def list_users() -> dict:
    """Addresses of every live account (deleted accounts are removed from the store)."""
    users = modal.Dict.from_name("hebpipe-users")
    seen, out, no_email = set(), [], 0
    for key, rec in users.items():
        k = str(key)
        if k.startswith("uid:") or k.startswith("email:"):
            continue
        addr = ""
        if isinstance(rec, dict):
            addr = (rec.get("email") or "").strip()
        if not addr and "@" in k:
            addr = k
        if not addr or "@" not in addr:
            no_email += 1
            continue
        low = addr.lower()
        if low in seen:
            continue
        seen.add(low)
        out.append(addr)
    return {"addresses": sorted(out), "without_email": no_email}


@app.function(secrets=[modal.Secret.from_name("hebpipe-auth")], timeout=1800)
def send_batch(recipients: list, subject: str, html: str, text: str) -> list:
    import json as _json
    import os
    import urllib.error
    import urllib.request

    sender = os.environ.get("EMAIL_FROM", "Pipeline <onboarding@resend.dev>")
    results = []
    for to in recipients:
        payload = {"from": sender, "to": [to], "reply_to": REPLY_TO,
                   "subject": subject, "html": html, "text": text}
        req = urllib.request.Request(
            "https://api.resend.com/emails", data=_json.dumps(payload).encode(),
            headers={"Authorization": "Bearer " + os.environ["RESEND_API_KEY"],
                     "Content-Type": "application/json",
                     "User-Agent": "hebrew-video-pipeline/1.0", "Accept": "application/json"})
        for attempt in range(4):
            try:
                body = urllib.request.urlopen(req, timeout=20).read().decode()
                results.append({"to": to, "id": _json.loads(body).get("id"), "ts": time.time()})
                break
            except urllib.error.HTTPError as e:
                detail = ""
                try:
                    detail = e.read().decode()[:200]
                except Exception:  # noqa: BLE001
                    pass
                if e.code == 429 and attempt < 3:
                    time.sleep(2 * (attempt + 1))
                    continue
                results.append({"to": to, "error": f"HTTP {e.code} {detail}", "ts": time.time()})
                break
            except Exception as e:  # noqa: BLE001
                results.append({"to": to, "error": str(e)[:200], "ts": time.time()})
                break
        time.sleep(0.6)
    return results


@app.local_entrypoint()
def main(spec: str, to: str = "", list: str = "", all_users: bool = False,  # noqa: A002
         dry_run: bool = False, limit: int = 0, exclude: str = ""):
    spec_d = json.loads(Path(spec).read_text(encoding="utf-8"))
    out = HERE / "out" / spec_d["name"]
    html = (out / "email.html").read_text(encoding="utf-8")
    text = (out / "email.txt").read_text(encoding="utf-8")
    subject = spec_d["subject"]
    log = out / "sent.jsonl"

    if to:
        recipients = [a.strip() for a in to.split(",") if a.strip()]
    elif list:
        recipients = [l.strip() for l in Path(list).read_text().splitlines() if l.strip() and "@" in l]
    elif all_users:
        info = list_users.remote()
        recipients = info["addresses"]
        print(f"registered accounts with an email: {len(recipients)} (+{info['without_email']} legacy accounts without one)")
    else:
        raise SystemExit("pass --to, --list or --all-users")

    excluded = DEFAULT_EXCLUDE | {a.strip().lower() for a in exclude.split(",") if a.strip()}
    already = set()
    if log.exists() and not to:
        for line in log.read_text().splitlines():
            try:
                rec = json.loads(line)
                if rec.get("id"):
                    already.add(rec["to"].lower())
            except ValueError:
                pass
    todo = [r for r in recipients if r.lower() not in excluded and r.lower() not in already]
    if limit:
        todo = todo[:limit]
    print(f"to send: {len(todo)}  (excluded {len(recipients) - len(todo) - 0 if not already else len(recipients) - len(todo)}: "
          f"{len(already)} already sent, {sum(1 for r in recipients if r.lower() in excluded)} excluded)")
    print(f"subject: {subject}")
    if dry_run:
        print("dry run, nothing sent")
        return

    sent = failed = 0
    with log.open("a", encoding="utf-8") as f:
        for i in range(0, len(todo), 40):
            chunk = todo[i:i + 40]
            for rec in send_batch.remote(chunk, subject, html, text):
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                if rec.get("id"):
                    sent += 1
                else:
                    failed += 1
                    print(f"  failed {rec['to']}: {rec.get('error')}")
            f.flush()
            print(f"  {min(i + 40, len(todo))}/{len(todo)} done")
    print(f"sent {sent}, failed {failed}; log: {log.relative_to(HERE.parent.parent)}")
