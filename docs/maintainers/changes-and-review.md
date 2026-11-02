# Changes and review

Review is **risk-based**. Trusted maintainers should be able to ship ordinary work without creating a queue of ceremonial approvals.

## Required review

| Change | Minimum review rule |
|---|---|
| Ordinary, reversible maintainer change | Independent approval optional; self-merge permitted after appropriate validation |
| External contribution | Maintainer review required |
| High-risk change | At least one independent, qualified maintainer review required |

Repository branch protection and required checks also apply. Never treat this document as permission to bypass a live protection rule.

## Ordinary maintainer changes

1. Open a PR against `dev`, scoped to one coherent change.
2. Explain intent, expected behavior, validation, and rollback if relevant.
3. Run suitable tests and inspect the actual result.
4. Ensure applicable required checks pass, address feedback, and merge when ready.

Self-merge is allowed for ordinary reversible work. The author remains responsible for follow-up, correction, and rollback.

## External contributions and small corrections

External contributions receive maintainer review.

A maintainer may directly fix a **small, obvious** problem in a contributor PR when the intended behavior is clear and the repair is faster than a separate round trip. Document what changed, preserve contributor attribution and history, avoid force-pushing, and re-run affected checks.

Substantive or ambiguous corrections require discussion with the contributor or a separately scoped correction. A maintainer must not silently turn a contributor's PR into unrelated work. Any corrective commit changes the SHA being reviewed; update validation and audit evidence accordingly.

## High-risk changes

High-risk work requires **at least one independent qualified review**. The author cannot be the only approver. Examples:

- Authentication, authorization, tenancy, sandboxing, agent tool permissions, or secrets.
- Outbound network and SSRF defenses, sensitive integrations, and deployment access.
- Destructive operations, deletion, filesystem ownership, and data migrations.
- Persistence and concurrency invariants with possible data loss.
- Major architectural contracts, release infrastructure, or changes hard to roll back.

A high-risk review should verify the threat or failure model, exact final commit, reproducer or regression evidence, failure and rollback behavior, and the relevant CI/security findings. Use the [review playbook](review-playbook.md).

## Cross-area changes and CODEOWNERS

Touching multiple areas does **not** automatically require multiple approvals. Consult domain expertise when needed. `.github/CODEOWNERS` currently contains no ownership rules; the per-area map is under discussion in [issue #593](https://github.com/odysseus-dev/odysseus/issues/593). Once populated, it helps identify reviewers and accountable contacts; it is not a blanket approval requirement by itself.

## Validation and UI evidence

Automated tests count as runtime evidence when they truly exercise the changed behavior. Use manual validation when coverage is insufficient.

For any change to visible UI behavior, follow the current [contributor UI checklist](../../CONTRIBUTING.md#style-and-visual-changes) and the repository PR template. Screenshots or clips are expected when they materially demonstrate the rendered result; screenshots alone do not replace behavioral tests.

## Disagreement and escalation

The maintainer doing the work owns ordinary implementation decisions within approved product direction and existing technical contracts. For substantive disagreements:

1. Involve a qualified maintainer with relevant expertise.
2. Escalate unresolved cross-cutting technical issues to trusted project-wide maintainers.
3. Refer product-direction disagreements to the Product Owner.

Security findings should be handled via [SECURITY.md](../../SECURITY.md), without disclosing exploit details in public issues. Repository protection rules should support these risk-based policies and should not accidentally force independent approval for every ordinary maintainer PR.
