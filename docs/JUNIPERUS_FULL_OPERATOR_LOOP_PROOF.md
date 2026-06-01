# JUNIPERUS FULL OPERATOR LOOP PROOF

_One safe, end-to-end governed operation proof for Juniperus / Gnexus Operations Console._

---

## What the Proof Does

Runs a complete lifecycle inside `data/gnexus/operator-loop/sandbox/`:

1. Creates/locates a harmless test file (`governed-proof-target.txt`).
2. Proposes a small edit.
3. Generates a unified diff (`governed-proof.diff`).
4. Queues an approval object (`approval-object.json`).
5. **If** `approved=true` AND `confirm=true`:
   - Creates a rollback snapshot (`*.snapshot`).
   - Applies the patch (writes proposed content).
   - Verifies changed content matches the proposal.
   - Rolls back from snapshot.
   - Verifies rollback restored original content.
6. Writes a receipt (`proof-receipt.json`) + surfaced copy in receipts folder.

No shell execution. No cloud calls. All operations confined to the sandbox.

---

## Surface Conditions

| Condition | Verification |
|---|---|
| Diff gate works | Unified diff generated with valid format. |
| Approval object exists | JSON written to sandbox folder. |
| Patch apply works | Content applied only when approved; blocked otherwise. |
| Rollback snapshot works | Snapshot file written; restore verified. |
| Verifier loop works | Content hash/compare after each step. |
| Receipt exists | Written to `data/gnexus/operator-loop/sandbox/proof-receipt.json`. |
| UI shows outcome | `/gnexus/operator-loop` loads state; `/api/gnexus/operator-loop/state` returns JSON. |

---

## Running the Proof

```powershell
# Check status only (gated: does not apply)
powershell -ExecutionPolicy Bypass -File .\scripts\gnexus\Run-FullGovernedOperatorSmoke.ps1 -NoApprove

# Full apply + verify + rollback (requires explicit approval)
powershell -ExecutionPolicy Bypass -File .\scripts\gnexus\Run-FullGovernedOperatorSmoke.ps1
```

Output includes:

- Status: `GOVERNED_PROOF_GATED_AWAITING_APPROVAL` (when `-NoApprove`)
- or: `GOVERNED_PROOF_COMPLETE_APPLY_VERIFY_ROLLBACK` (when approved)
- Receipt: `data/gnexus/operator-loop/sandbox/proof-receipt.json`

---

## Proof Receipt Fields

| Field | Meaning |
|---|---|
| `diffGateProven` | Boolean: unified diff was generated. |
| `approvalObjectExists` | Boolean: approval JSON exists in sandbox. |
| `rollbackSnapshotProven` | Boolean: snapshot was written before apply. |
| `applyVerified` | Boolean: content after apply matched proposal. |
| `rollbackVerified` | Boolean: rollback restored original. |
| `verifierLoopProven` | Boolean: verify + rollback checks passed. |
| `governedOperationProof` | Top-level true when all above are true. |
| `status` | The closeout status string. |

---

## Integration with Rooms

| Room | Role in Proof |
|---|---|
| Diff Gate | Generates diffs and queues patches. |
| Approval Desk | Receives approval objects. |
| Patch Apply | Applies only after approval + snapshot. |
| Verifier Loop | Post-apply verification and rollback requests. |
| Operator Loop | End-to-end orchestration view. |

---

## Security

- Sandbox path: `data/gnexus/operator-loop/sandbox/` — never escapes workspace root.
- No destructive operations run against real code outside the sandbox.
- The proof script is ASCII-safe and Windows PowerShell 5.1 compatible.

---

*Workspace root: `C:\Users\iamcy\CymaticsDev`*  
*Human approval is the authority layer. No endless loading states.*
