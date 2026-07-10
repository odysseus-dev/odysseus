---
name: deploy-checklist
description: Gate deployment on source, config, health, and rollback evidence.
category: deployment
tags: [misumi, lelouch, deployment]
status: published
confidence: 1.0
source: first-party
---
## When to Use
Use for a service install, update, or cutover.
## Procedure
1. Confirm clean reviewed source and external state directory.
2. Validate on a non-production port.
3. Check auth, readiness, logs, and rollback.
4. Cut over only after all checks pass.
## Pitfalls
- Do not deploy over a dirty checkout.
## Verification
- Old service remains recoverable until post-cutover validation passes.
