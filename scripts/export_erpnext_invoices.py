"""Export approved business & mixed-split ledger rows into ERPNext Purchase Invoice CSV format."""
from __future__ import annotations

import argparse
import csv
from pathlib import Path

ROOT = Path(r"C:\Users\Hachi\Projects\odysseus")
LEDGER_CSV = ROOT / "data" / "receipt_ledger_erpnext_ready.csv"
OUT_CSV = ROOT / "data" / "erpnext_purchase_invoices_import.csv"

ERPNEXT_HEADERS = [
    "Series",
    "Supplier",
    "Posting Date",
    "Company",
    "Currency",
    "Credit To",
    "Item Name (Items)",
    "Accepted Qty (Items)",
    "Rate (Items)",
    "Amount (Items)",
    "Expense Head (Items)",
    "Cost Center (Items)",
    "Remarks",
]


def export_erpnext(company: str, expense_account: str, credit_to: str, cost_center: str) -> int:
    if not LEDGER_CSV.exists():
        print(f"No approved ledger found at {LEDGER_CSV} yet. Approve a receipt first via receipt_triage_staging.py.")
        return 0

    exported = 0
    with LEDGER_CSV.open("r", encoding="utf-8", newline="") as fin, OUT_CSV.open("w", encoding="utf-8", newline="") as fout:
        reader = csv.DictReader(fin)
        writer = csv.writer(fout)
        writer.writerow(ERPNEXT_HEADERS)

        for row in reader:
            biz_amt = float(row.get("business_amount") or 0.0)
            if biz_amt <= 0.0:
                continue
            bucket = row.get("bucket") or "business"
            pct = float(row.get("business_pct") or 100.0)
            total = float(row.get("total_amount") or biz_amt)
            remarks = (
                f"OdyAI Proposal #{row.get('proposal_id')} | bucket={bucket} "
                f"({pct:.0f}% business of ${total:.2f}) | {row.get('subject', '')}"
            ).strip()

            writer.writerow([
                "ACC-PINV-.YYYY.-",
                row.get("vendor") or "Unknown Supplier",
                row.get("invoice_date") or "",
                company,
                "USD",
                credit_to,
                f"{row.get('vendor', 'Expense')} ({bucket})",
                "1",
                f"{biz_amt:.2f}",
                f"{biz_amt:.2f}",
                expense_account,
                cost_center,
                remarks,
            ])
            exported += 1

    print(f"Exported {exported} ERPNext Purchase Invoice row(s) -> {OUT_CSV}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert approved OdyAI split ledger rows to ERPNext Purchase Invoice CSV.")
    parser.add_argument("--company", default="We All Shine LLC")
    parser.add_argument("--expense-account", default="5110 - Cost of Goods Sold / Operating Expenses - WAS")
    parser.add_argument("--credit-to", default="2110 - Creditors - WAS")
    parser.add_argument("--cost-center", default="Main - WAS")
    args = parser.parse_args()
    return export_erpnext(args.company, args.expense_account, args.credit_to, args.cost_center)


if __name__ == "__main__":
    raise SystemExit(main())
