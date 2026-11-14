"""Read-only RFC 2369 / 8058 List-Unsubscribe newsletter scanner (zero outbound requests)."""
from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from email import message_from_bytes
from email.header import decode_header
from pathlib import Path

ROOT = Path(r"C:\Users\Hachi\Projects\odysseus")
sys.path.insert(0, str(ROOT))

import src.secret_storage  # noqa: E402, F401
from routes.email.email_helpers import _imap_connect, _list_email_accounts  # noqa: E402

REPORT_PATH = ROOT / "data" / "newsletter_unsubscribe_candidates.json"


def _decode_hdr(val: str | None) -> str:
    if not val:
        return ""
    out = []
    for chunk, enc in decode_header(val):
        if isinstance(chunk, bytes):
            out.append(chunk.decode(enc or "utf-8", errors="replace"))
        else:
            out.append(chunk)
    return "".join(out).strip()


def _parse_unsubscribe_targets(raw_header: str) -> dict[str, str]:
    targets: dict[str, str] = {}
    for match in re.findall(r"<([^>]+)>", raw_header or ""):
        uri = match.strip()
        if uri.lower().startswith("mailto:"):
            targets["mailto"] = uri
        elif uri.lower().startswith(("https://", "http://")):
            targets["https"] = uri
    return targets


def scan_newsletters(max_headers: int = 300) -> None:
    accounts = _list_email_accounts() or []
    if not accounts:
        print("No email accounts configured in app.db yet (0 rows). Add an account in Integrations first.")
        return

    grouped: dict[str, dict] = defaultdict(
        lambda: {"sender": "", "domain": "", "count": 0, "one_click_post": False, "targets": {}, "sample_subjects": []}
    )

    for acc in accounts:
        acc_id = acc.get("id") or ""
        owner = acc.get("owner") or ""
        imap = _imap_connect(acc_id, owner=owner)
        try:
            imap.select("INBOX", readonly=True)
            typ, data = imap.uid("search", None, "ALL")
            if typ != "OK" or not data or not data[0]:
                continue
            uids = data[0].split()[-max_headers:]
            if not uids:
                continue

            uid_set = b",".join(uids)
            _, fetched = imap.uid(
                "fetch",
                uid_set,
                "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT LIST-UNSUBSCRIBE LIST-UNSUBSCRIBE-POST)])",
            )
            for item in fetched:
                if not isinstance(item, tuple):
                    continue
                msg = message_from_bytes(item[1])
                unsub_raw = _decode_hdr(msg.get("List-Unsubscribe"))
                if not unsub_raw:
                    continue
                targets = _parse_unsubscribe_targets(unsub_raw)
                if not targets:
                    continue

                sender = _decode_hdr(msg.get("From"))
                subject = _decode_hdr(msg.get("Subject"))
                post_hdr = _decode_hdr(msg.get("List-Unsubscribe-Post"))
                domain = sender.split("@")[-1].split(">")[0].strip().lower() if "@" in sender else sender.lower()

                entry = grouped[domain]
                entry["sender"] = sender
                entry["domain"] = domain
                entry["count"] += 1
                entry["one_click_post"] |= ("list-unsubscribe=one-click" in post_hdr.lower())
                entry["targets"].update(targets)
                if subject and len(entry["sample_subjects"]) < 3 and subject not in entry["sample_subjects"]:
                    entry["sample_subjects"].append(subject[:90])
        finally:
            try:
                imap.logout()
            except Exception:
                pass

    ranked = sorted(grouped.values(), key=lambda x: (-x["count"], x["domain"]))
    REPORT_PATH.write_text(json.dumps(ranked, indent=2, ensure_ascii=False), encoding="utf-8")

    if not ranked:
        print("No RFC List-Unsubscribe newsletter headers found.")
        return

    print(f"=== TOP NEWSLETTER SENDERS WITH RFC LIST-UNSUBSCRIBE ({len(ranked)}) ===")
    for idx, item in enumerate(ranked[:20], 1):
        mode = "RFC-8058 One-Click POST" if item["one_click_post"] else ("mailto" if "mailto" in item["targets"] else "https")
        print(f"  {idx:2d}. [{item['count']}x] {item['domain']} ({mode}) | sample: {item['sample_subjects'][0] if item['sample_subjects'] else ''}")
    print(f"\nSaved full read-only report to {REPORT_PATH}")


if __name__ == "__main__":
    scan_newsletters()
