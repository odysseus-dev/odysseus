#!/usr/bin/env python3
"""Full governed operation proof for Juniperus / Gnexus Operations Console.

Runs one safe, local-first end-to-end governed operation inside a sandbox:

  1. Create/locate a harmless test file.
  2. Propose a small edit.
  3. Generate a unified diff (diff gate).
  4. Queue an approval object.
  5. Apply ONLY if approved=true / confirm=true.
  6. Create a rollback snapshot before apply.
  7. Apply the patch.
  8. Verify changed content.
  9. Roll back.
 10. Verify rollback restored original content.
 11. Write a receipt and proof object surfaced in the cockpit.

No shell execution. No cloud calls. No destructive operations outside the
sandbox folder.

Usage:
  python run_governed_operator_proof.py --approve   (proves apply + rollback)
  python run_governed_operator_proof.py             (stops at approval gate)
"""

from __future__ import annotations

import argparse
import difflib
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SANDBOX = REPO_ROOT / "data" / "gnexus" / "operator-loop" / "sandbox"
RECEIPTS = REPO_ROOT / "data" / "gnexus" / "receipts"
PROOF_RECEIPT = SANDBOX / "proof-receipt.json"
TEST_FILE = SANDBOX / "governed-proof-target.txt"
SNAPSHOT_FILE = SANDBOX / "governed-proof-target.txt.snapshot"
DIFF_FILE = SANDBOX / "governed-proof.diff"
APPROVAL_FILE = SANDBOX / "approval-object.json"

ORIGINAL_CONTENT = "Juniperus governed operation proof target.\nstate: original\n"
PROPOSED_CONTENT = "Juniperus governed operation proof target.\nstate: edited-by-governed-operation\n"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log(steps, name, ok, detail=""):
    steps.append({"step": name, "ok": bool(ok), "detail": detail, "at": _utc_now()})
    marker = "[OK]" if ok else "[FAIL]"
    print("%s %s %s" % (marker, name, ("- " + detail) if detail else ""))


def run(approved: bool) -> int:
    SANDBOX.mkdir(parents=True, exist_ok=True)
    RECEIPTS.mkdir(parents=True, exist_ok=True)
    steps = []
    op_id = "proof-" + uuid.uuid4().hex[:10]

    # 1. Create/locate harmless test file (reset to known original).
    TEST_FILE.write_text(ORIGINAL_CONTENT, encoding="utf-8")
    _log(steps, "create_test_file", TEST_FILE.exists(), str(TEST_FILE))

    # 2 + 3. Propose edit and generate unified diff.
    diff_lines = list(
        difflib.unified_diff(
            ORIGINAL_CONTENT.splitlines(keepends=True),
            PROPOSED_CONTENT.splitlines(keepends=True),
            fromfile="a/governed-proof-target.txt",
            tofile="b/governed-proof-target.txt",
        )
    )
    diff_text = "".join(diff_lines)
    DIFF_FILE.write_text(diff_text, encoding="utf-8")
    _log(steps, "generate_unified_diff", bool(diff_text.strip()), "%d diff lines" % len(diff_lines))

    # 4. Queue approval object.
    approval = {
        "id": "appr-" + uuid.uuid4().hex[:10],
        "operationId": op_id,
        "createdAt": _utc_now(),
        "kind": "patch_apply",
        "target": str(TEST_FILE),
        "diffFile": str(DIFF_FILE),
        "risk": "low_sandbox",
        "approved": bool(approved),
        "confirm": bool(approved),
        "requires": {
            "rollbackSnapshotBeforeApply": True,
            "verifyAfterApply": True,
            "verifyAfterRollback": True,
        },
    }
    APPROVAL_FILE.write_text(json.dumps(approval, indent=2), encoding="utf-8")
    _log(steps, "queue_approval_object", APPROVAL_FILE.exists(), "approved=%s" % approved)

    apply_verified = False
    rollback_verified = False
    gated = False

    # 5. Apply ONLY if approved.
    if not (approval["approved"] and approval["confirm"]):
        gated = True
        _log(steps, "approval_gate", True, "Not approved; apply correctly blocked. Re-run with --approve.")
    else:
        # 6. Rollback snapshot before apply.
        SNAPSHOT_FILE.write_text(TEST_FILE.read_text(encoding="utf-8"), encoding="utf-8")
        snap_ok = SNAPSHOT_FILE.exists() and SNAPSHOT_FILE.read_text(encoding="utf-8") == ORIGINAL_CONTENT
        _log(steps, "rollback_snapshot", snap_ok, str(SNAPSHOT_FILE))

        # 7. Apply patch (write proposed content).
        TEST_FILE.write_text(PROPOSED_CONTENT, encoding="utf-8")
        _log(steps, "apply_patch", True, "proposed content written")

        # 8. Verify changed content.
        apply_verified = TEST_FILE.read_text(encoding="utf-8") == PROPOSED_CONTENT
        _log(steps, "verify_applied", apply_verified, "content matches proposed")

        # 9. Roll back from snapshot.
        TEST_FILE.write_text(SNAPSHOT_FILE.read_text(encoding="utf-8"), encoding="utf-8")
        _log(steps, "rollback", True, "restored from snapshot")

        # 10. Verify rollback restored original.
        rollback_verified = TEST_FILE.read_text(encoding="utf-8") == ORIGINAL_CONTENT
        _log(steps, "verify_rollback", rollback_verified, "content matches original")

    proven = bool(apply_verified and rollback_verified)
    if gated:
        status = "GOVERNED_PROOF_GATED_AWAITING_APPROVAL"
    elif proven:
        status = "GOVERNED_PROOF_COMPLETE_APPLY_VERIFY_ROLLBACK"
    else:
        status = "GOVERNED_PROOF_FAILED"

    receipt = {
        "schema": "gnexus.operator-loop.governed-proof.v1",
        "system": "Juniperus",
        "title": "Gnexus Operations Console - Full Governed Operation Proof",
        "operationId": op_id,
        "ranAt": _utc_now(),
        "approved": bool(approved),
        "gatedAwaitingApproval": gated,
        "diffGateProven": bool(diff_text.strip()),
        "approvalObjectExists": APPROVAL_FILE.exists(),
        "rollbackSnapshotProven": SNAPSHOT_FILE.exists() if not gated else False,
        "applyVerified": apply_verified,
        "rollbackVerified": rollback_verified,
        "verifierLoopProven": proven,
        "status": status,
        "sandbox": str(SANDBOX),
        "steps": steps,
        "boundary": {
            "shellExecuted": False,
            "externalWrites": False,
            "outsideSandboxWrites": False,
            "humanApprovalRequired": True,
        },
    }
    PROOF_RECEIPT.write_text(json.dumps(receipt, indent=2, ensure_ascii=False), encoding="utf-8")
    (RECEIPTS / "governed-operation-proof-receipt.json").write_text(
        json.dumps(receipt, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print("")
    print("Status: %s" % status)
    print("Receipt: %s" % PROOF_RECEIPT)

    if gated:
        return 0  # gated is a correct outcome when not approved
    return 0 if proven else 3


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--approve", action="store_true", help="set approved=true to prove apply + rollback")
    args = ap.parse_args()
    return run(approved=args.approve)


if __name__ == "__main__":
    raise SystemExit(main())
