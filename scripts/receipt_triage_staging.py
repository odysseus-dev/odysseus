"""Proposal-gated email & local drop-folder receipt triage with persistent vendor_rules learning and split-bill ledger."""
from __future__ import annotations

import argparse
import csv
import email
import hashlib
import json
import re
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from email.header import decode_header
from pathlib import Path

ROOT = Path(r"C:\Users\Hachi\Projects\odysseus")
sys.path.insert(0, str(ROOT))

from src.email_attachment_text import attachment_text  # noqa: E402

ARCHIVE_ROOT = Path(r"G:\Ody_AI Organized")
DROP_QUEUE_ROOT = ARCHIVE_ROOT / "99 - Review Queue"
STAGING_ROOT = DROP_QUEUE_ROOT / "_receipt_staging"
TRIAGE_DB = ROOT / "data" / "receipt_triage.db"
LEDGER_CSV = ROOT / "data" / "receipt_ledger_erpnext_ready.csv"

SUPPORTED_DROP_EXTS = {".pdf", ".txt", ".csv", ".eml", ".png", ".jpg", ".jpeg"}

DEST_MAP = {
    "business_receipt": ARCHIVE_ROOT / "02 - Financial & Banking" / "business",
    "business_tax": ARCHIVE_ROOT / "03 - Tax & Accounting" / "business",
    "personal_receipt": ARCHIVE_ROOT / "10 - Receipts, Manuals & Warranties" / "personal",
    "personal_bill": ARCHIVE_ROOT / "02 - Financial & Banking" / "personal",
    "mixed_split": ARCHIVE_ROOT / "03 - Tax & Accounting" / "business",
}

BUSINESS_HINTS = re.compile(
    r"\b(we all shine|6tech|nein9|llc|ein|commercial|wholesale|supplies|janitorial|equipment|stripe|square|quickbooks|hosting|domain|github|openai|aws)\b",
    re.I,
)
MIXED_HINTS = re.compile(
    r"\b(verizon|t-mobile|at&t|comcast|spectrum|internet|wireless|phone|electric|gas|water|utility|adobe|microsoft 365|google workspace|apple\.com|amazon)\b",
    re.I,
)
AMOUNT_RE = re.compile(
    r"(?:total|amount paid|amount due|grand total|charged|balance due)[^\d$]{0,20}\$?\s*([0-9]{1,3}(?:,[0-9]{3})*\.[0-9]{2})",
    re.I,
)
FALLBACK_AMOUNT_RE = re.compile(r"\$\s*([0-9]{1,3}(?:,[0-9]{3})*\.[0-9]{2})")
DATE_RE = re.compile(r"\b(20[2-3][0-9]-[01][0-9]-[0-3][0-9]|[01]?[0-9]/[0-3]?[0-9]/20[2-3][0-9])\b")


def init_db() -> sqlite3.Connection:
    TRIAGE_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(TRIAGE_DB))
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS receipt_proposals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_key TEXT UNIQUE NOT NULL,
            account_id TEXT,
            message_id TEXT,
            sender TEXT,
            subject TEXT,
            email_date TEXT,
            tags_json TEXT,
            staged_files_json TEXT,
            vendor TEXT,
            vendor_key TEXT,
            invoice_date TEXT,
            total_amount REAL,
            proposed_bucket TEXT,
            business_pct REAL,
            personal_pct REAL,
            proposed_dest_dir TEXT,
            rule_source TEXT DEFAULT 'heuristic',
            status TEXT DEFAULT 'pending_approval',
            approved_at TEXT,
            final_files_json TEXT,
            notes TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS vendor_rules (
            vendor_key TEXT PRIMARY KEY,
            display_vendor TEXT NOT NULL,
            bucket TEXT NOT NULL,
            business_pct REAL NOT NULL,
            personal_pct REAL NOT NULL,
            dest_dir TEXT NOT NULL,
            approval_count INTEGER DEFAULT 1,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    return conn


def _decode_hdr(val: str | None) -> str:
    if not val:
        return ""
    parts = []
    for chunk, enc in decode_header(val):
        if isinstance(chunk, bytes):
            parts.append(chunk.decode(enc or "utf-8", errors="replace"))
        else:
            parts.append(chunk)
    return "".join(parts).strip()


def _safe_filename(name: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip().strip(".")
    return cleaned[:120] or "attachment.bin"


def _vendor_identity(sender: str, fallback_text: str = "") -> tuple[str, str]:
    display_raw = "" if sender.strip().lower() == "local_drop" else sender.split("<")[0].strip().strip('"')
    domain = sender.split("@")[-1].split(">")[0].strip().lower() if "@" in sender else ""
    if not display_raw and not domain and fallback_text:
        first_line = next((ln.strip() for ln in fallback_text.splitlines() if len(ln.strip()) >= 3), "")
        cleaned = re.sub(r"^\d{4}[-_]\d{2}[-_]\d{2}[-_\s]*", "", Path(first_line).stem if "." in first_line and len(first_line.split()) == 1 else first_line)
        cleaned = re.sub(r"[-_\s]+(statement|receipt|invoice|bill|order|confirmation)$", "", cleaned, flags=re.IGNORECASE)
        display_raw = cleaned[:40]
    display = re.sub(r"[^A-Za-z0-9 _&-]", "", display_raw or domain.split(".")[0]).strip()[:40] or "Unknown_Vendor"
    key_base = domain if domain and domain not in {"gmail.com", "yahoo.com", "outlook.com", "icloud.com", "local_drop"} else display.lower()
    vendor_key = re.sub(r"[^a-z0-9._-]+", "_", key_base).strip("_") or "unknown_vendor"
    return display, vendor_key


def _infer_proposal(triage_conn: sqlite3.Connection, sender: str, subject: str, body_text: str, pdf_text: str, tags: list[str]):
    blob = f"{sender}\n{subject}\n{body_text[:4000]}\n{pdf_text[:6000]}"
    m_amt = AMOUNT_RE.search(blob) or FALLBACK_AMOUNT_RE.search(blob)
    total = float(m_amt.group(1).replace(",", "")) if m_amt else 0.0

    m_dt = DATE_RE.search(blob)
    inv_date = m_dt.group(1) if m_dt else datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if "/" in inv_date:
        mm, dd, yyyy = inv_date.split("/")
        inv_date = f"{yyyy}-{int(mm):02d}-{int(dd):02d}"

    vendor, vendor_key = _vendor_identity(sender, fallback_text=pdf_text or subject)
    learned = triage_conn.execute("SELECT * FROM vendor_rules WHERE vendor_key=?", (vendor_key,)).fetchone()
    if learned:
        lr = dict(learned)
        return (
            lr["display_vendor"] or vendor,
            vendor_key,
            inv_date,
            total,
            lr["bucket"],
            float(lr["business_pct"]),
            float(lr["personal_pct"]),
            lr["dest_dir"],
            f"learned_rule(n={lr['approval_count']})",
        )

    if BUSINESS_HINTS.search(blob):
        bucket, biz_pct, pers_pct, dest = "business", 100.0, 0.0, DEST_MAP["business_receipt"]
    elif MIXED_HINTS.search(blob):
        bucket, biz_pct, pers_pct, dest = "mixed", 50.0, 50.0, DEST_MAP["mixed_split"]
    else:
        bucket, biz_pct, pers_pct = "personal", 0.0, 100.0
        dest = DEST_MAP["personal_bill"] if "bills" in tags else DEST_MAP["personal_receipt"]

    return vendor, vendor_key, inv_date, total, bucket, biz_pct, pers_pct, str(dest), "heuristic"


def cmd_ingest_folder(drop_dir: Path) -> None:
    if not drop_dir.exists():
        print(f"Drop folder does not exist: {drop_dir}")
        return

    triage_conn = init_db()
    STAGING_ROOT.mkdir(parents=True, exist_ok=True)
    staged_count = 0

    for item in sorted(drop_dir.iterdir()):
        if not item.is_file():
            continue
        if item.suffix.lower() not in SUPPORTED_DROP_EXTS:
            continue

        file_hash = hashlib.sha256(item.read_bytes()).hexdigest()[:24]
        msg_key = f"drop_{file_hash}"
        if triage_conn.execute("SELECT 1 FROM receipt_proposals WHERE message_key=?", (msg_key,)).fetchone():
            print(f"  Skipping already-staged file: {item.name}")
            continue

        stage_dir = (STAGING_ROOT / msg_key).resolve()
        if stage_dir.parent != STAGING_ROOT.resolve():
            raise ValueError("Staging directory escape blocked")
        stage_dir.mkdir(parents=True, exist_ok=True)

        staged_path = stage_dir / _safe_filename(item.name)
        shutil.move(str(item), str(staged_path))

        extracted = ""
        if staged_path.suffix.lower() in {".pdf", ".txt", ".csv"}:
            res = attachment_text(staged_path, max_chars=8000)
            extracted = res.get("content") or ""

        tags = ["local_drop", "receipt"]
        if re.search(r"\b(bill|statement|utility|amount due)\b", f"{staged_path.name}\n{extracted[:1500]}", re.I):
            tags.append("bills")

        vendor, vendor_key, inv_date, total, bucket, biz_pct, pers_pct, dest_dir, rule_src = _infer_proposal(
            triage_conn,
            sender="local_drop",
            subject=staged_path.stem.replace("_", " "),
            body_text="",
            pdf_text=extracted,
            tags=tags,
        )

        triage_conn.execute(
            """
            INSERT INTO receipt_proposals (
                message_key, account_id, message_id, sender, subject, email_date,
                tags_json, staged_files_json, vendor, vendor_key, invoice_date, total_amount,
                proposed_bucket, business_pct, personal_pct, proposed_dest_dir, rule_source, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                msg_key,
                "local_drop",
                msg_key,
                "local_drop",
                staged_path.name,
                datetime.now(timezone.utc).isoformat(),
                json.dumps(tags),
                json.dumps([str(staged_path)]),
                vendor,
                vendor_key,
                inv_date,
                total,
                bucket,
                biz_pct,
                pers_pct,
                dest_dir,
                rule_src,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        triage_conn.commit()
        staged_count += 1

    triage_conn.close()
    print(f"Folder ingest complete. Staged {staged_count} file(s) from {drop_dir} into {STAGING_ROOT}")


def cmd_scan(max_recent: int = 100) -> None:
    import src.secret_storage  # noqa: F401
    from routes.email.email_helpers import _imap_connect, _list_email_accounts

    accounts = _list_email_accounts() or []
    if not accounts:
        print("No email accounts configured in app.db yet (0 rows). Use --mode ingest-folder for local PDFs.")
        return

    triage_conn = init_db()
    STAGING_ROOT.mkdir(parents=True, exist_ok=True)
    new_count = 0

    for acc in accounts:
        acc_id = acc.get("id") or ""
        owner = acc.get("owner") or ""
        imap = _imap_connect(acc_id, owner=owner)
        try:
            imap.select("INBOX", readonly=True)
            typ, data = imap.uid("search", None, "ALL")
            if typ != "OK" or not data or not data[0]:
                continue
            for uid_b in data[0].split()[-max_recent:]:
                uid_str = uid_b.decode("utf-8", errors="replace")
                msg_key = hashlib.sha256(f"{acc_id}:uid:{uid_str}".encode("utf-8")).hexdigest()[:24]
                if triage_conn.execute("SELECT 1 FROM receipt_proposals WHERE message_key=?", (msg_key,)).fetchone():
                    continue

                st, hdr_data = imap.uid("fetch", uid_b, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE MESSAGE-ID)])")
                if st != "OK" or not hdr_data or not isinstance(hdr_data[0], tuple):
                    continue
                hdr_msg = email.message_from_bytes(hdr_data[0][1])
                sender = _decode_hdr(hdr_msg.get("From"))
                subject = _decode_hdr(hdr_msg.get("Subject"))
                email_date = _decode_hdr(hdr_msg.get("Date"))
                msg_id = _decode_hdr(hdr_msg.get("Message-ID"))

                low = f"{sender}\n{subject}".lower()
                tags = []
                if re.search(r"\b(receipt|order|payment confirmation|invoice|statement|charged|purchase)\b", low):
                    tags.append("receipt")
                if re.search(r"\b(bill|billing|amount due|payment due|autopay|utility)\b", low):
                    tags.append("bills")
                if not tags:
                    continue

                st_full, full_data = imap.uid("fetch", uid_b, "(BODY.PEEK[])")
                if st_full != "OK" or not full_data or not isinstance(full_data[0], tuple):
                    continue
                raw_bytes = full_data[0][1]
                msg = email.message_from_bytes(raw_bytes)

                stage_dir = (STAGING_ROOT / msg_key).resolve()
                if stage_dir.parent != STAGING_ROOT.resolve():
                    raise ValueError("Staging directory escape blocked")
                stage_dir.mkdir(parents=True, exist_ok=True)

                staged_files: list[str] = []
                body_parts: list[str] = []
                pdf_texts: list[str] = []

                for part in msg.walk():
                    ctype = part.get_content_type()
                    disp = str(part.get("Content-Disposition") or "")
                    fname = _decode_hdr(part.get_filename())
                    payload = part.get_payload(decode=True)
                    if not payload:
                        continue
                    if fname and (fname.lower().endswith(".pdf") or "attachment" in disp.lower()):
                        out_path = stage_dir / _safe_filename(fname)
                        out_path.write_bytes(payload)
                        staged_files.append(str(out_path))
                        if out_path.suffix.lower() == ".pdf":
                            res = attachment_text(out_path, max_chars=8000)
                            if res.get("content"):
                                pdf_texts.append(res["content"])
                    elif ctype == "text/plain" and "attachment" not in disp.lower():
                        body_parts.append(payload.decode(part.get_content_charset() or "utf-8", errors="replace"))

                if not any(f.lower().endswith(".pdf") for f in staged_files):
                    eml_path = stage_dir / "receipt_email.eml"
                    eml_path.write_bytes(raw_bytes)
                    staged_files.append(str(eml_path))

                vendor, vendor_key, inv_date, total, bucket, biz_pct, pers_pct, dest_dir, rule_src = _infer_proposal(
                    triage_conn, sender, subject, "\n".join(body_parts), "\n".join(pdf_texts), tags
                )

                triage_conn.execute(
                    """
                    INSERT INTO receipt_proposals (
                        message_key, account_id, message_id, sender, subject, email_date,
                        tags_json, staged_files_json, vendor, vendor_key, invoice_date, total_amount,
                        proposed_bucket, business_pct, personal_pct, proposed_dest_dir, rule_source, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        msg_key, acc_id, msg_id, sender, subject, email_date,
                        json.dumps(tags), json.dumps(staged_files), vendor, vendor_key, inv_date, total,
                        bucket, biz_pct, pers_pct, dest_dir, rule_src, datetime.now(timezone.utc).isoformat(),
                    ),
                )
                triage_conn.commit()
                new_count += 1
        finally:
            try:
                imap.logout()
            except Exception:
                pass

    triage_conn.close()
    print(f"Scan complete. Staged {new_count} new receipt/bill proposal(s) in {STAGING_ROOT}")


def cmd_list() -> None:
    conn = init_db()
    rows = conn.execute("SELECT * FROM receipt_proposals WHERE status='pending_approval' ORDER BY id").fetchall()
    conn.close()
    if not rows:
        print("No pending receipt proposals awaiting approval.")
        return
    print(f"=== PENDING RECEIPT PROPOSALS ({len(rows)}) ===")
    for r in rows:
        d = dict(r)
        print(
            f"  #{d['id']} | {d['invoice_date']} | {d['vendor']} | ${d['total_amount']:.2f} "
            f"| bucket={d['proposed_bucket']} (biz={d['business_pct']:.0f}% / pers={d['personal_pct']:.0f}%) "
            f"| source={d.get('rule_source', 'heuristic')} | dest={d['proposed_dest_dir']}"
        )


def cmd_rules() -> None:
    conn = init_db()
    rows = conn.execute("SELECT * FROM vendor_rules ORDER BY approval_count DESC, display_vendor").fetchall()
    conn.close()
    if not rows:
        print("No learned vendor split rules yet. Approving any receipt automatically trains its vendor rule.")
        return
    print(f"=== LEARNED VENDOR SPLIT RULES ({len(rows)}) ===")
    for r in rows:
        d = dict(r)
        print(
            f"  - {d['display_vendor']} [{d['vendor_key']}] -> bucket={d['bucket']} "
            f"(biz={d['business_pct']:.0f}% / pers={d['personal_pct']:.0f}%) | approved={d['approval_count']}x | dest={d['dest_dir']}"
        )


def cmd_approve(proposal_id: int, bucket: str | None, business_pct: float | None, dest_override: str | None) -> None:
    conn = init_db()
    row = conn.execute("SELECT * FROM receipt_proposals WHERE id=?", (proposal_id,)).fetchone()
    if not row:
        print(f"Proposal #{proposal_id} not found.")
        conn.close()
        return

    d = dict(row)
    if d["status"] != "pending_approval":
        print(f"Proposal #{proposal_id} is already '{d['status']}'.")
        conn.close()
        return

    final_bucket = bucket or d["proposed_bucket"]
    if business_pct is not None:
        biz_pct = max(0.0, min(100.0, float(business_pct)))
    elif bucket == "business":
        biz_pct = 100.0
    elif bucket == "personal":
        biz_pct = 0.0
    else:
        biz_pct = float(d["business_pct"])
    pers_pct = round(100.0 - biz_pct, 2)

    if dest_override:
        dest_dir = Path(dest_override)
    elif final_bucket == "business":
        dest_dir = DEST_MAP["business_receipt"]
    elif final_bucket == "mixed":
        dest_dir = DEST_MAP["mixed_split"]
    else:
        dest_dir = DEST_MAP["personal_receipt"]

    dest_dir.mkdir(parents=True, exist_ok=True)
    staged_files = [Path(p) for p in json.loads(d["staged_files_json"] or "[]")]
    final_files: list[str] = []

    safe_vendor = re.sub(r"[^A-Za-z0-9_-]+", "_", d["vendor"]).strip("_") or "Receipt"
    amt_tag = f"{d['total_amount']:.2f}"

    for idx, src in enumerate(staged_files, 1):
        if not src.exists():
            continue
        suffix = f"_{idx}" if len(staged_files) > 1 else ""
        target_name = f"{d['invoice_date']}_{safe_vendor}_${amt_tag}{suffix}{src.suffix.lower()}"
        target_path = dest_dir / target_name
        shutil.move(str(src), str(target_path))
        final_files.append(str(target_path))
        if src.parent.exists() and not any(src.parent.iterdir()):
            src.parent.rmdir()

    total = float(d["total_amount"] or 0.0)
    biz_amt = round(total * (biz_pct / 100.0), 2)
    pers_amt = round(total - biz_amt, 2)
    approved_at = datetime.now(timezone.utc).isoformat()

    write_header = not LEDGER_CSV.exists()
    with LEDGER_CSV.open("a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if write_header:
            writer.writerow([
                "proposal_id", "approved_at", "invoice_date", "vendor", "bucket",
                "total_amount", "business_pct", "business_amount", "personal_pct",
                "personal_amount", "archived_files", "sender", "subject",
            ])
        writer.writerow([
            proposal_id, approved_at, d["invoice_date"], d["vendor"], final_bucket,
            f"{total:.2f}", f"{biz_pct:.2f}", f"{biz_amt:.2f}", f"{pers_pct:.2f}",
            f"{pers_amt:.2f}", ";".join(final_files), d["sender"], d["subject"],
        ])

    v_key = d.get("vendor_key") or _vendor_identity(d["sender"] or d["vendor"])[1]
    conn.execute(
        """
        INSERT INTO vendor_rules (vendor_key, display_vendor, bucket, business_pct, personal_pct, dest_dir, approval_count, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, 1, ?)
        ON CONFLICT(vendor_key) DO UPDATE SET
            display_vendor=excluded.display_vendor,
            bucket=excluded.bucket,
            business_pct=excluded.business_pct,
            personal_pct=excluded.personal_pct,
            dest_dir=excluded.dest_dir,
            approval_count=vendor_rules.approval_count + 1,
            updated_at=excluded.updated_at
        """,
        (v_key, d["vendor"], final_bucket, biz_pct, pers_pct, str(dest_dir), approved_at),
    )

    conn.execute(
        """
        UPDATE receipt_proposals
        SET status='approved', proposed_bucket=?, business_pct=?, personal_pct=?,
            proposed_dest_dir=?, approved_at=?, final_files_json=?
        WHERE id=?
        """,
        (final_bucket, biz_pct, pers_pct, str(dest_dir), approved_at, json.dumps(final_files), proposal_id),
    )
    conn.commit()
    conn.close()

    print(
        f"Approved #{proposal_id}: {d['vendor']} (${total:.2f}) -> bucket={final_bucket} "
        f"[Business: ${biz_amt:.2f} ({biz_pct:.0f}%) | Personal: ${pers_amt:.2f} ({pers_pct:.0f}%)]"
    )
    print(f"Learned rule saved for '{v_key}' -> {final_bucket} ({biz_pct:.0f}% biz / {pers_pct:.0f}% pers)")
    for fp in final_files:
        print(f"  Filed: {fp}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Proposal-gated email & drop-folder receipt router with vendor learning")
    parser.add_argument("--mode", choices=["scan", "ingest-folder", "list", "rules", "approve"], required=True)
    parser.add_argument("--dir", type=str, default=str(DROP_QUEUE_ROOT), help="Drop directory for --mode ingest-folder")
    parser.add_argument("--id", type=int, help="Proposal ID for --mode approve")
    parser.add_argument("--bucket", choices=["business", "personal", "mixed"], help="Override entity bucket")
    parser.add_argument("--business-pct", type=float, help="Override business percentage (0-100)")
    parser.add_argument("--dest", type=str, help="Override destination directory path")
    args = parser.parse_args()

    if args.mode == "scan":
        cmd_scan()
    elif args.mode == "ingest-folder":
        cmd_ingest_folder(Path(args.dir))
    elif args.mode == "list":
        cmd_list()
    elif args.mode == "rules":
        cmd_rules()
    elif args.mode == "approve":
        if not args.id:
            parser.error("--id is required with --mode approve")
        cmd_approve(args.id, args.bucket, args.business_pct, args.dest)


if __name__ == "__main__":
    main()
