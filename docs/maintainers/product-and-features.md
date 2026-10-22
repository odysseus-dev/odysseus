# Product and feature proposals

Pewds ([@pewdiepie-archdaemon](https://github.com/pewdiepie-archdaemon)) is the Product Owner for Odysseus and has final authority over product direction. Maintainers own technical feasibility, implementation, and safety.

## Which changes require product approval?

**Product Owner approval before implementation:** substantial new features, changes to product direction, and product changes that are hard to reverse.

**No separate product approval by default:** bug fixes, security fixes, maintenance, tests, CI, documentation, internal refactors, and small, reversible product changes within existing direction.

Maintainers may make those smaller product decisions. Pewds can ask for changes or a rollback; the maintainer owns safe rollback or recovery.

### Urgent security and reliability fixes

Urgent fixes or containment may proceed without prior product approval, even if behavior temporarily changes. Keep the change to what stops the immediate problem, notify Pewds promptly, and record the reason and rollback or recovery plan. Agree on any lasting product-direction change with Pewds afterwards. Normal high-risk review and repository checks still apply.

## Product Owner initiated

```text
Product Owner proposal or implementation
    -> technical feasibility
    -> plan and implementation
    -> risk-based review and validation
```

A Product Owner proposal is already product-approved; it is not automatically technically approved or exempt from high-risk review.

## Community or maintainer initiated

Community proposals start as a Feature Request issue. Check [ROADMAP.md](../../ROADMAP.md) and existing issues first; take early ideas to [Discussions](https://github.com/odysseus-dev/odysseus/discussions/categories/ideas).

```text
Feature proposal
    -> Product Owner accepts / rejects, where approval is required
    -> technical feasibility
    -> plan and implementation
    -> risk-based review and validation
```

Record the decision in an issue, proposal, work item, or PR so future maintainers can discover it. Do not rely on a chat-only decision.

## Decision boundaries

The Product Owner decides **what belongs in Odysseus**. Maintainers decide **how approved work is built safely and maintainably**.

A rejected or out-of-scope feature can be maintained independently in a contributor's fork.

If the Product Owner is unavailable, work requiring prior product approval waits unless authority was explicitly delegated. Smaller decisions and urgent fixes follow the rules above.
