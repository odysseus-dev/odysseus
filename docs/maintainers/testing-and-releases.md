# Testing and releases

Validation must match the actual change, and evidence must state what was **run**, **observed**, and **not tested**.

`dev` is the integration and stabilization branch. `main` is the curated release destination. Changes land in `dev`; a release promotes a verified `dev` revision to `main`.

## Runtime isolation

Run contributor-controlled tests, builds, installs, and repros in an isolated, secretless environment. Use disposable containers or microVMs where appropriate, with no credentials, sensitive host files, production data, or host/container-management sockets exposed. If safe isolation is unavailable, record the check as not run.

Agents may use a skill for the maintainer's preferred environment and handling; the same isolation requirements apply.

## Change validation

- Run focused tests that cover the modified behavior and failure cases.
- Run broader regressions where risk crosses module boundaries.
- For authentication, data loss, or similar high-risk work, include adversarial and rollback tests.
- Reproduce the issue on the pre-fix version when safe and practical, then prove the correction.
- For visual changes, run the application and attach the screenshots or clips required by [CONTRIBUTING.md](../../CONTRIBUTING.md).
- Record exact commands, environment, result, skipped tests, and blockers. Never claim execution from a command you did not run.

GitHub Actions success means a workflow finished successfully; it does **not** mean CodeQL found no alerts. Use [review playbook](review-playbook.md#codeql-and-security-scanning) to assess findings separately. A review must tie required CI and the final diff to the **same commit SHA**.

## Supported-environment evidence

Platform Testers may validate without maintainer permissions. Useful coverage includes:

- Windows native and WSL.
- macOS Apple Silicon.
- macOS Intel while supported.
- Linux native on the reference distribution.
- Docker on supported hosts.

Record the environment and configuration for each result. Do not imply that a platform was tested when it was not.

## Release ownership

A designated **Release Maintainer** owns preparation and the publish decision once the release criteria are met. Other maintainers and testers supply the evidence needed for the change set. If the Release Maintainer becomes unavailable, another maintainer explicitly takes over.

## Release checklist

Before promoting `dev` to `main`:

1. Identify and freeze the release candidate SHA; check the actual content that will ship.
2. Verify required checks and relevant supported-environment validation.
3. Confirm high-risk and data-migration findings are resolved or addressed through an explicit approved plan.
4. Document skipped coverage, known issues, compatibility and upgrade implications.
5. Prepare the version, tag, artifacts, and release notes.
6. Confirm rollback/recovery, including any manual persistence or data migration steps.
7. Publish and record what was promoted, the verification evidence, and operational follow-ups.

Publishing does not require ceremonial Core or domain sign-off when the criteria are met. Exceptional or disputed releases follow the normal high-risk escalation path.
