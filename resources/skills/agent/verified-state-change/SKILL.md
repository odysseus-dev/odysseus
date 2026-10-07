---
name: verified-state-change
description: Make scoped state changes with target confirmation, minimal mutation, and read-back verification
version: 1.0.0
category: agent
tags: [state, mutation, verification, safety]
status: published
confidence: 1.0
source: builtin
owner: ""
created: "2026-08-30T00:00:00Z"
---

## When to Use

Use when creating, editing, deleting, moving, sending, scheduling, or otherwise changing persistent state through an application, API, filesystem, or service.

## Procedure

1. Read the current state and identify the target using stable identifiers plus enough content to disambiguate it.
2. Preserve fields the user did not ask to change and choose the narrowest supported mutation.
3. For destructive or externally visible actions, confirm that the user's instruction authorizes the exact target and effect.
4. Perform the mutation once and capture the returned identifier, status, or revision.
5. Read the target again through an independent list, fetch, status, or content operation.
6. Compare the observed state with the requested outcome and repair only the specific mismatch.

## Pitfalls

- Do not infer the target from a stale active item when a stable identifier can be fetched.
- Do not report success from an accepted request alone; asynchronous or partial operations may not have completed.
- Do not replace an entire object when a field-level update is supported and safer.
- Do not silently broaden a mutation to adjacent files, records, accounts, or services.

## Verification

- The target identity was confirmed before mutation.
- A read-back shows the intended values and preserves unrelated state.
- Any external effect has a concrete status, identifier, or observable result.
