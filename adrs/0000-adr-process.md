# ADR-0000: Architecture Decision Record Process

- Status: proposed
- Deciders: Felix, X (i propose 5) active maintainers needed to add here.
- Date: 2026-06-16

## Summary

## Context

Odysseus has no formal process for recording architecture decisions. Decisions are scattered across PR comments, issue threads, and undocumented conventions.

The following two principles should motivate all ADRs:

> **All architecture decisions are a trade-off.**

There are no right or wrong answers — only trade-offs. Recording the trade-off reasoning is the point. If there are no trade-offs, then it's not an ADR

> **Why is more important than how.**

Architecture Decision Records are more useful than diagrams. A diagram shows what was built. An ADR records *why* it was built that way, what alternatives were considered, and what trade-offs were accepted. This is the information that disappears fastest and matters most when revisiting a decision.

**When to write an ADR:** If a decision would take a long time to reverse, it makes sense to take the time to document why and how that decision was the correct one in that context. Not every decision needs an ADR — only those with significant, hard-to-reverse consequences.

## Decision

### Format

ADRs live in `adrs/NNNN-kebab-title.md`, zero-padded sequential numbering. Each ADR names a single decision. Numbers are stable references for discussion — they do not imply priority or ordering.

Required sections:

```
# ADR-NNNN: Title (a single decision, not a topic)

- Status: proposed | accepted | rejected | superseded by NNNN | deferred
- Deciders: [names]
- Date: YYYY-MM-DD

## Summary

## Context
Why this decision is needed. What forces are at play.

## Decision
What was decided and why. 
Source-grounded evidence — not opinion.
Proposed implementation plan.
Open questions.

## Consequences
This is the most important part of an ADR.
```

A framework ADR (like this one) may set principles, with follow-up ADRs for each concrete change. One decision per ADR — do not bundle unrelated choices.

### Lifecycle

```
proposed → accepted     (maintainer consensus)
proposed → rejected     (decision: do not do this)
proposed → deferred     (decision is sound but not yet actionable)
accepted → superseded   (replaced by a newer ADR)
```

### Acceptance criteria

An ADR is accepted when:

1. **Maintainer agreement:** At least 5 approving reviews from active maintainers.
2. **Source-grounded evidence:** Claims are verified against code, specs, or data — not opinion or convention.
3. **No unresolved blocking objections:** Every blocking objection has been addressed or explicitly recorded as a known trade-off.
4. **Clear implementation path:** The ADR describes how to get from current state to decided state, even if implementation is deferred.

Acceptance records intent, not authorization to implement. Each code change still lands as its own small, reviewable PR referencing the ADR. An accepted ADR means "we agree this is the right direction" — not "merge everything now."

### Amendments

Changing an accepted ADR means writing a new ADR that supersedes it. You do not rewrite history — only update the status field of the old ADR to `superseded by NNNN`. This keeps the reasoning chain intact: future contributors can trace why a decision changed by reading both ADRs.

## Consequences

- Decisions that would take a long time to reverse get documented before implementation, not after.
- The *why* behind a decision survives contributor turnover. New contributors can read the ADR instead of re-discovering trade-offs through trial and error.
- PR review can reference ADR numbers (`per ADR-0001`) instead of re-arguing the reasoning in every review thread.
- The bar is intentionally high (5 maintainer approvals, source-grounded evidence, no unresolved objections) because ADRs record hard-to-reverse decisions. Easy-to-reverse decisions do not need ADRs.
- The append-only amendment rule means the project accumulates a decision log, not a living document that hides its own history.
