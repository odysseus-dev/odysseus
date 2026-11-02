# Odysseus maintainer handbook

Odysseus maintainers are trusted to act, not to wait for permission. The default is individual responsibility; additional review is required when the **risk** warrants it.

This handbook records the project's maintainer governance and gives reviewers a reusable process. It complements [CONTRIBUTING.md](../../CONTRIBUTING.md) and [SECURITY.md](../../SECURITY.md); it does not replace the PR template, repository protections, or product decisions.

## At a glance

| Area | Working rule |
|---|---|
| Product direction | Pewds is the Product Owner |
| Ordinary, reversible maintainer PR | Self-merge allowed after appropriate validation and applicable repository checks |
| External contribution | Maintainer review required |
| High-risk change | At least one **independent, qualified** maintainer review |
| Access | Need-to-have, least privilege, separately granted per system |
| Release | A designated Release Maintainer owns preparation and publish |
| Inactivity | Access review after 2 months without activity, notice, or response |
| Documentation | Repository Markdown is canonical |

These are governance rules, not guarantees that a GitHub permission or branch-protection setting permits a particular action. Respect the stronger applicable automated gate.

## Start here

- [Roles and access](roles-and-access.md): responsibility, permissions, and continuity.
- [Product and feature proposals](product-and-features.md): product approval versus technical ownership.
- [Changes and review](changes-and-review.md): review thresholds, self-merge, contributor corrections.
- [Practical review playbook](review-playbook.md): risk, provenance, CI, CodeQL, independent audit, and handoff evidence.
- [Triage](triage.md): turning incoming reports into actionable work.
- [Testing and releases](testing-and-releases.md): evidence, platform validation, promotion to main.
- [Documentation](documentation.md): canonical sources, updates, and traceability.

## Accountability

Maintainers own the changes they take on, including validation, regression prevention, follow-up, and rollback when needed. Roles describe responsibilities, not a hierarchy of technical approval.

Important product-direction decisions go through the Product Owner. Maintainers retain responsibility for feasibility, implementation, maintainability, and security.

AI coding or review agents can help collect evidence, test hypotheses, and prepare changes, but they are not the qualified human reviewer required for high-risk changes. A maintainer must inspect and accept the evidence.
