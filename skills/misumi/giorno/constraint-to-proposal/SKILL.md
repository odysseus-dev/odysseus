---
name: constraint-to-proposal
description: Turn a concrete constraint into a small reversible proposal.
category: improvement
tags: [misumi, giorno, proposal]
status: published
confidence: 1.0
source: first-party
---
## When to Use
Use when a useful idea is blocked by cost, time, space, or maintenance.
## Procedure
1. State the binding constraint.
2. Reduce scope until the constraint is respected.
3. Add evidence and rollback criteria.
## Pitfalls
- Do not hide the constraint with optimistic assumptions.
## Verification
- The proposal explicitly satisfies the stated constraint.
