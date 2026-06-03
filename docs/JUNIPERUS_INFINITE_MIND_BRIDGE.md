# JUNIPERUS110 - Infinite Mind Bridge

## Overview

The **Infinite Mind Bridge** is a governed, read-only access system that allows Juniperus to index, search, and retrieve operational context from the local `06_INFINITE_BRAIN` workspace without mutating it.

## Core Principle

- **Juniperus may READ the Infinite Mind by default.**
- **Juniperus may NOT WRITE to the Infinite Mind** unless an explicit human-approved writeback gate is added later (JUNIPERUS120).
- **No mutations** to `06_INFINITE_BRAIN` in this pass.
- **No external API calls.**
- **No cloud models.**
- **No secret storage.**

## Architecture

The bridge is implemented across 10 layers:

### LAYER 1: Source Binding
- Detects whether the Infinite Brain root exists
- Classifies state: `missing`, `exists_empty`, `exists_unscanned`, `indexed`, `indexed_with_warnings`, `error`
- Persists state to: `data/gnexus/infinite-mind/source-binding.json`

### LAYER 2: Safe Scanner
- Indexes `06_INFINITE_BRAIN` safely
- Ignores dangerous patterns: `.git`, `venv`, `node_modules`, `__pycache__`, `dist`, `build`, `.cache`, large `logs`
- Only indexes safe file types: `.md`, `.txt`, `.json`, `.yaml`, `.ps1`, `.py`, `.html`, `.csv`, `.log`
- Generates:
  - `file-index.json` - Complete file index
  - `candidate-records.json` - Indexed file records
  - `source-map.json` - ID to path mapping
  - `scan-report.json` - Scan summary

### LAYER 3: Infinite Mind Context Packs
- Pre-assembled context bundles for common operations
- Automatically generated from scanned files
- Available packs:
  - **Operations Console Context** - Operational state and console config
  - **Infinite Brain Canon Context** - Canonical protocols and runbooks
  - **Mission Runtime Context** - Active mission state
  - **Operator Loop Context** - Operator loop state and decisions
  - **Model Routing Context** - Model selection and routing policies

### LAYER 4: Search API
- `GET /api/gnexus/infinite-mind/state` - Current bridge state
- `GET /api/gnexus/infinite-mind/search?q=...` - Search indexed files
- `GET /api/gnexus/infinite-mind/context-packs` - List available context packs
- `GET /api/gnexus/infinite-mind/context-pack/{pack_id}` - Get specific pack
- `POST /api/gnexus/infinite-mind/rescan` - Trigger rescan (local-only, read-only)

### LAYER 5: Frontstage UI
- Accessible at `/gnexus/infinite-mind`
- Shows:
  - Bridge status and binding information
  - Indexed file count and context packs
  - Governance boundaries (writeback locked, mutation locked)
  - Search box for indexed files
  - Context pack cards
  - Rescan and export actions
  - Recent scan receipts

### LAYER 6: Operator Loop Integration
- Context request queue: `context-request-queue.json`
- Provides `assemble_context_bundle()` function to operator loop
- Bundles selected context packs for operation execution
- Uses local Ollama/model routing (if smoke test is needed)

### LAYER 7: Writeback Gate Stub
- Writeback is locked by default
- Policy: `writeback-policy.json` - States `writebackAllowed: false`
- Queue: `writeback-queue.json` - Remains empty
- Next stage: `JUNIPERUS120_INFINITE_MIND_WRITEBACK_GATE`

### LAYER 8: Verification
Three PowerShell scripts ensure integrity:

```powershell
# Run scanner
.\scripts\gnexus\Scan-InfiniteMind.ps1

# Test context bundle assembly
.\scripts\gnexus\Test-InfiniteMindContextBundle.ps1

# Full verification
.\scripts\gnexus\Verify-JuniperusInfiniteMindBridge.ps1
```

### LAYER 9: Documentation
Complete documentation of the bridge, context packs, and writeback policy.

### LAYER 10: Closeout Receipt
Final verification receipt: `data/gnexus/receipts/JUNIPERUS110-closeout.json`
- Status: `JUNIPERUS_INFINITE_MIND_BRIDGE_READY_LOCAL_CLOSEOUT`
- Indexed file count
- Context pack count
- Governance boundaries confirmed
- All verifications passed

## Usage

### Scanning

To index the Infinite Brain:

```powershell
.\scripts\gnexus\Scan-InfiniteMind.ps1 -MaxSizeMB 10
```

This will:
1. Detect the `06_INFINITE_BRAIN` root
2. Walk directory tree, ignoring dangerous patterns
3. Index safe file types
4. Extract classification, tags, and snippets
5. Generate file index, candidate records, and context packs
6. Update source binding state

### Searching

Search indexed files via the UI at `/gnexus/infinite-mind` or via API:

```bash
GET /api/gnexus/infinite-mind/search?q=mission
```

Results include file path, classification, tags, snippet, and search score.

### Using Context Packs

Load a specific context pack in the UI or via API:

```bash
GET /api/gnexus/infinite-mind/context-pack/operations-console-context
```

Use in operator loop:

```python
from src.gnexus_governance.infinite_mind_bridge import get_bridge

bridge = get_bridge()
bundle = bridge.assemble_context_bundle(
    pack_ids=["operations-console-context"],
    search_terms=["mission"]
)
```

### Rescan

Trigger a fresh scan via the UI or API:

```bash
POST /api/gnexus/infinite-mind/rescan
```

Rescan is:
- Local-only (no external calls)
- Read-only (does not mutate source)
- Repeatable (can be run multiple times)

## Governance Boundaries

| Boundary | Status | Reason |
|----------|--------|--------|
| Writeback | Locked | Read-only bridge first |
| Mutation | Locked | Source is immutable |
| External Calls | Disabled | Local-only |
| Secrets Storage | Disabled | No secrets stored |
| Approval Required | Yes | Default for mutations |

## File Classifications

Files are automatically classified:

- **finalizer** - Finalizer procedures
- **receipt** - Operation receipts
- **verifier** - Verification logic
- **mission-control** - Mission state
- **memory** - Memory files
- **skill** - Skill definitions
- **canon** - Canonical standards
- **protocol** - Procedures/protocols
- **runbook** - Runbook procedures
- **dashboard** - Dashboard configs
- **bridge** - Bridge-related files
- **repair** - Repair procedures
- **replay** - Replay data
- **ledger** - Ledger/audit log
- **unknown** - Unclassified

## Redaction

Sensitive data is redacted from search results and context packs:

- API keys
- Tokens
- Passwords
- Authorization headers
- AWS credentials

Pattern matching redacts values while preserving file structure for readability.

## Verification

Run the verification script to ensure proper implementation:

```powershell
.\scripts\gnexus\Verify-JuniperusInfiniteMindBridge.ps1
```

Checks:
- All required files exist
- JSON is valid
- Python syntax is valid
- Governance boundaries are locked
- UI pages are not in endless loading state
- Branding is correct

## Troubleshooting

### "Source root not found"
- Ensure `C:\Users\iamcy\CymaticsDev\06_INFINITE_BRAIN` exists
- Run scan to detect and classify state

### "No results found"
- Check that the bridge has been scanned
- Run `/api/gnexus/infinite-mind/rescan` to index files
- Verify that search query matches file names or content

### "Context packs empty"
- Run rescan to generate context packs from indexed files
- Ensure scanned files are classified correctly

## Next Steps

### JUNIPERUS120 - Writeback Gate
Future implementation will add:
- Approval-gated writeback capability
- Whitelist of allowed write types
- Audit trail for write operations
- Integration with approval desk

## Related Documentation

- [Context Packs](JUNIPERUS_INFINITE_MIND_CONTEXT_PACKS.md)
- [Writeback Policy](JUNIPERUS_INFINITE_MIND_WRITEBACK_POLICY.md)
- [Gnexus Operations Console](START_HERE_GNEXUS_OPERATIONS_CONSOLE.md)
