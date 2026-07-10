---
name: rollback-path
description: Specify how to reverse a proposed change before trying it.
category: rollback
tags: [misumi, giorno, rollback]
status: published
confidence: 1.0
source: first-party
---
## When to Use
Use before any experiment, configuration change, or new automation.
## Procedure
1. Record the current state.
2. Name the exact reversal action and retained artifact.
3. Define the rollback trigger.
## Pitfalls
- Do not call recreation from memory a rollback.
## Verification
- Reversal can be performed without the failed system.
