# Triage

Triage turns reports into evidence-backed work that a maintainer can pick up without guessing.

## Intake flow

```text
Report
  -> reproduce / clarify
  -> identify duplicates and affected versions
  -> classify impact, urgency, and risk
  -> prepare actionable work item
  -> maintainer takes ownership
  -> PR, verification, and follow-up
```

## A useful work item

Record what is known and distinguish it from what remains unverified:

- Original report and reproducible steps; installation method, OS, version/commit, and relevant environment.
- Expected versus actual behavior, including user-visible impact.
- Evidence: sanitized logs, screenshots, failing tests, or controlled reproduction.
- Related issues, PRs, duplicates, or a known prior regression.
- Scope, acceptance criteria, and non-goals.
- Priority, ownership, and relevant domain expertise.
- Remaining unknowns, risks, and safe recovery or workaround when available.

Small and obvious fixes may go straight to a focused PR, consistent with [CONTRIBUTING.md](../../CONTRIBUTING.md) and [changes and review](changes-and-review.md).

## Practical priority language

Use P0-P3 as **triage shorthand**, not as a substitute for verified exploitability or the project's actual GitHub labels:

| Priority | Typical impact | Expected response |
|---|---|---|
| P0 | Active exploitation, broad irreversible loss, catastrophic outage | Immediate containment and escalation |
| P1 | Credible severe security or data-integrity vulnerability | Urgent owner assignment and validated fix |
| P2 | Significant correctness, security, or operational regression | Prioritize and resolve before approving affected high-risk changes |
| P3 | Limited impact, maintainability, or low-risk polish | Fix or explicitly track with rationale |

Do not call a concern harmless merely because an automated check is green. Escalate privately when reproduction would disclose a vulnerability.

## Where decisions live

Matrix is for coordination; durable decisions belong in GitHub, Plane, a specification, or the relevant PR. Keep confidential security and access details in appropriate private channels. Do not leave important project decisions only in chat history.

See the [review playbook](review-playbook.md) for converting triage into review evidence and handling contested or uncertain findings.
