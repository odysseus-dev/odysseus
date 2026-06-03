# JUNIPERUS110 - Infinite Mind Context Packs

## Overview

Context Packs are pre-assembled, read-only bundles of operational context extracted from the Infinite Brain. They allow Juniperus to efficiently load relevant files for specific operation types without re-scanning the entire directory tree.

## Available Context Packs

### 1. Operations Console Context

**Purpose:** Operational state and console configuration

**Contains:** Files related to console initialization, state management, and operational readiness.

**Use when:**
- Starting the operator loop
- Checking console health
- Understanding current operational state

**Example files:**
- Console configuration files
- Operational state snapshots
- Mission-control state files

### 2. Infinite Brain Canon Context

**Purpose:** Canonical protocols and runbooks

**Contains:** Protocols, runbooks, standards, and canonical procedures from the Infinite Brain.

**Use when:**
- Implementing governed operations
- Establishing standard procedures
- Ensuring compliance with established protocols

**Example files:**
- Protocol definitions
- Runbook procedures
- Standard operating procedures
- Canonical rules

### 3. Mission Runtime Context

**Purpose:** Active mission state and runtime information

**Contains:** Current mission state, decisions, and runtime artifacts.

**Use when:**
- Executing mission-dependent operations
- Understanding current mission status
- Making mission-aware decisions

**Example files:**
- Mission state files
- Mission decisions
- Runtime mission data
- Mission artifacts

### 4. Operator Loop Context

**Purpose:** Operator loop state and decisions

**Contains:** Operator loop configuration, decisions, and execution context.

**Use when:**
- Understanding current operator loop status
- Checking operator decisions
- Tracing operator loop execution

**Example files:**
- Operator loop configuration
- Operator decisions
- Loop state snapshots
- Decision history

### 5. Model Routing Context

**Purpose:** Model selection and routing policies

**Contains:** Model selection policies, routing configurations, and model availability.

**Use when:**
- Making model selection decisions
- Understanding model availability
- Checking routing policies

**Example files:**
- Model routing policies
- Model availability files
- Selection criteria
- Routing configurations

## Context Pack Structure

Each context pack is a JSON file with this structure:

```json
{
  "packId": "operations-console-context",
  "title": "Operations Console Context",
  "purpose": "Operational state and console configuration",
  "sourceFiles": [
    "path/to/file1.json",
    "path/to/file2.md",
    "path/to/file3.yaml"
  ],
  "summary": "Contains X operational files...",
  "importantAnchors": [],
  "relevantCommands": [],
  "relatedReceipts": [],
  "operationalWarnings": [],
  "suggestedUse": "Load when starting operator loop...",
  "sourceStatus": "complete|insufficient_source",
  "generatedAt": "2026-06-01T00:00:00Z"
}
```

## Usage

### Via UI

1. Navigate to `/gnexus/infinite-mind`
2. See "Context Packs" section
3. Review available packs and their purposes
4. Note files included in each pack

### Via API

List all packs:
```bash
GET /api/gnexus/infinite-mind/context-packs
```

Get specific pack:
```bash
GET /api/gnexus/infinite-mind/context-pack/operations-console-context
```

### Via Python

Assemble context bundle from selected packs:

```python
from src.gnexus_governance.infinite_mind_bridge import get_bridge

bridge = get_bridge()

# Load single pack
pack = bridge.load_context_pack("operations-console-context")

# Assemble bundle from multiple packs
bundle = bridge.assemble_context_bundle(
    pack_ids=["operations-console-context", "mission-runtime-context"],
    search_terms=["mission", "operator"]
)

print(bundle)
```

## Generating Context Packs

Context packs are automatically generated during a rescan via `/api/gnexus/infinite-mind/rescan` or by running the scan script:

```powershell
.\scripts\gnexus\Scan-InfiniteMind.ps1
```

The generation process:

1. **Index** files from Infinite Brain
2. **Classify** each file (protocol, runbook, mission-control, etc.)
3. **Group** files by classification and relevance
4. **Assemble** context packs with related files
5. **Generate summaries** based on source files (no hallucination)
6. **Save** packs and index

## Important Notes

### No Hallucination

Context pack summaries are based **only** on scanned files. If no source files are found for a pack, the `sourceStatus` is marked `insufficient_source`.

### Read-Only

Context packs are read-only. They cannot be modified directly. To update a pack:

1. Delete the pack file
2. Rescan the Infinite Brain
3. Let the system regenerate the pack

### Governance

All context packs respect the governance boundaries:

- **No mutation** of source files
- **No external calls**
- **No secret storage**
- **Read-only access**

## Workflow Example

### Operator Loop Integration

1. **Initialize operator loop**
   - Call `assemble_context_bundle(pack_ids=["operations-console-context"])`
   - Load operational state

2. **Check mission status**
   - Call `assemble_context_bundle(pack_ids=["mission-runtime-context"])`
   - Review mission decisions and runtime state

3. **Make model selection**
   - Call `assemble_context_bundle(pack_ids=["model-routing-context"])`
   - Review model policies and availability

4. **Execute operation**
   - Use loaded context to inform operation execution
   - Operate within governance boundaries

## Troubleshooting

### No packs available
- Run `/api/gnexus/infinite-mind/rescan` to generate packs
- Check that `06_INFINITE_BRAIN` has scanned files

### Pack shows "insufficient_source"
- Some classifications may not have matching files in Infinite Brain
- This is normal and expected
- Add relevant files to Infinite Brain to populate the pack

### Can't load a pack
- Verify pack ID is correct
- Check `/gnexus/infinite-mind` for list of available packs
- Run rescan to regenerate packs

## Next Steps

For writeback and mutation support, see:
- [Writeback Policy](JUNIPERUS_INFINITE_MIND_WRITEBACK_POLICY.md)
- [Bridge Documentation](JUNIPERUS_INFINITE_MIND_BRIDGE.md)
