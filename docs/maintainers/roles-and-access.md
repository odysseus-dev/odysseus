# Roles and access

Roles clarify who is accountable. They do not create a management chain or automatically grant privileges.

## Responsibility and typical access

| Responsibility | Typical GitHub access | Purpose |
|---|---|---|
| Product Owner | No automatic repository access implied | Product direction |
| Organization Owner | Organization Owner | Organization administration and continuity |
| Repository Admin | Admin | Repository settings, permissions, and protection rules |
| Maintainer | Write by default | Implement, review, merge, and maintain |
| Triage | Triage | Organize intake, de-duplicate, and prepare work |
| Contributor / Platform Tester | Public / Read as appropriate | Contribute code or provide validation |

Maintain access may be granted when repository-management duties require it.

## Maintainer focus

- **Core Maintainer:** project-wide judgment, difficult technical decisions, governance support.
- **Technical Maintainer:** implementation, architecture, validation, technical health.
- **Project Maintainer:** review, contributor coordination, documentation, releases, organization.

These are responsibility labels. No label automatically changes GitHub permissions or overrides the [review rules](changes-and-review.md).

## Functional responsibilities

Maintainers may coordinate Release, Security, Infrastructure and CI, Community, or Test and Validation. These are duties, not ranks. For each release, designate one Release Maintainer and record the handoff if ownership changes.

## Least privilege and continuity

Grant access because it is necessary for current work, not because of a title. GitHub, Matrix, Plane, infrastructure, SSH, deployment systems, secrets, and external services are separate access surfaces.

For elevated or sensitive access, document a responsible owner and recovery route in an appropriate **private** administrative system. Do not publish tokens, SSH details, personal addresses, or credential locations in this handbook. Remove access promptly when it is no longer needed.

## Inactivity

Tell an admin when you expect to be unavailable; a communicated absence is fine.

If there is no meaningful maintainer or triage activity for **two months**, no prior notice, and no response to reasonable follow-up, elevated access may be reviewed and removed. This is access hygiene, not punishment. Returning members may regain access appropriate to their active responsibilities.
