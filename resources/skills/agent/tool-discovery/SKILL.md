---
name: tool-discovery
description: Discover the smallest capable tool set and confirm argument schemas before acting
version: 1.0.0
category: agent
tags: [tools, discovery, routing, schemas]
status: published
confidence: 1.0
source: builtin
owner: ""
created: "2026-08-30T00:00:00Z"
---

## When to Use

Use when a task requires tools whose names, capabilities, or argument shapes are not already clear. This is especially useful when many tools are available or a previous call failed because the wrong tool or parameters were selected.

## Procedure

1. Translate the request into required capabilities such as reading, searching, editing, executing, browsing, or verifying.
2. Search the tool index for those capabilities and inspect the returned tool descriptions and schemas.
3. Prefer one direct tool over a chain of indirect tools when it can complete the operation and provide evidence.
4. Check required parameters, identifiers, path rules, side effects, and approval requirements before calling the tool.
5. Make a small read-only probe when the environment or target is uncertain.
6. Execute the selected action, inspect the result, and only broaden the tool search if the result shows a concrete capability gap.

## Pitfalls

- Do not guess tool names or argument keys from memory when the index or schema is available.
- Do not load unrelated tool groups into context.
- Do not repeat the same failed call without changing the arguments or strategy.
- Do not use a broad shell or browser workaround when a scoped native tool already owns the operation.

## Verification

- The chosen tool directly matches the required capability.
- Required arguments follow the exposed schema.
- The result contains evidence of the requested effect or a specific error that guides the next step.
