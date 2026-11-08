# Practical PR review playbook

Use this as a **repeatable review method**, not as an extra organizational approval ladder. The binding minimums remain in [changes and review](changes-and-review.md): ordinary maintainer changes may be self-merged, external PRs require maintainer review, and high-risk work requires at least one independent qualified maintainer review.

No single checklist replaces engineering judgment. Increase depth with risk.

## 1. Understand the PR and establish its review gates

- Establish the problem, what the PR fundamentally proposes, and why the approach makes sense.
- Check product direction, architecture fit, and scope before detailed review.
- Write down the questions the review must answer, the evidence needed, and what would block further review or approval.
- Confirm repository, source fork, target branch (`dev`), exact **HEAD SHA**, and current base.
- Review the full effective diff, commit history, issue, prior feedback, and related upstream changes.
- Confirm the branch is mergeable against the **current** `dev`, not an older local snapshot.
- Identify the user-facing contract, affected data and users, security boundaries, and rollback plan.
- Separate pre-existing upstream problems from regressions introduced or made reachable by the PR.

Example read-only commands:

```bash
REPO="odysseus-dev/odysseus"
PR=1234
gh pr view "$PR" --repo "$REPO" --json title,state,isDraft,headRefOid,baseRefName,mergeable
gh pr diff "$PR" --repo "$REPO"
gh pr checks "$PR" --repo "$REPO"
```

A green check on an old commit does not verify the revised branch.

## 2. Review the failure and threat model

For the relevant change, ask what happens with malformed input, missing auth state, wrong owner, concurrent requests, stale caches, partial failure, retries, cancelled jobs, unavailable dependencies, and restart/recovery.

Prioritize boundaries that can lose data or authority: identity, delegated API tokens, filesystem confinement, email deletion, persistence versioning, network/SSRF destinations, secrets, and agent tool execution.

For security claims, demonstrate the source-to-sink path and the *actual* guard. Tests must represent production middleware or transport where a unit mock could conceal a bypass.

## 3. Correct without obscuring contributor work

For small, unambiguous issues, maintainers may add focused corrections directly to a contributor PR under [the contribution review policy](changes-and-review.md#external-contributions-and-small-corrections). Preserve provenance, explain the change, prefer normal commits and fast-forward pushes, and avoid unrelated rewrites.

For substantive or ambiguous work, ask the contributor or prepare a clearly separated patch. Any code change means validating **the new SHA**, not approving based solely on the previous audit.

## 4. Build an evidence record

Run contributor code under the [runtime isolation rules](testing-and-releases.md#runtime-isolation).

Useful evidence includes:

- Pre-fix reproduction, expected outcome, and actual outcome.
- Focused regression tests for the fix and broad suites relevant to cross-cutting effects.
- Actual runtime checks (browser/UI, backend, Docker, supported OS) where necessary.
- Exact executed commands, result counts, skipped cases, and environmental limitations.
- GitHub required checks and conflict-free state on the exact final SHA.
- Relevant operational consequences (data migration, rollback, compatibility).

Keep audits reproducible. A screenshot alone is not a functional test, and a passing mocked test is not proof that a production integration path is safe.

## 5. Independent review for high-risk changes

A separate **qualified maintainer** must inspect high-risk changes; the implementing author cannot be the sole reviewer. Independent coding agents may be useful for adversarial tests and cross-checks, but **an AI agent is not itself a GitHub approver** and does not replace human accountability.

To avoid confirmation bias, give the independent reviewer the current SHA, changed files, acceptance criteria, known reproduction, and explicit permission to challenge the proposed solution. Preserve unresolved findings and require a new audit when meaningful fixes change the diff.

## CodeQL and security scanning

A successful CodeQL workflow means analysis ran, not that the security dashboard is empty. Likewise, resolving a PR discussion does not necessarily resolve its code-scanning alert.

For each relevant alert:

1. Record the alert number, rule, severity, file/line, state, and source-to-sink trace if applicable.
2. Compare with the current `dev` baseline; decide whether the PR introduced, exposed, or did not affect it.
3. Reproduce or reason through the actual production behavior, including validation and authorization.
4. **Fix** credible vulnerabilities, preferably with a regression test. Do not hide them by broad suppressions.
5. **Recommend dismissal** only for a demonstrable false positive or a stale/invalid finding, with a precise technical explanation; only authorized maintainers should dismiss alerts.
6. Document genuine but intentionally accepted risk separately. A true positive is not a false positive merely because it is deliberate.
7. Rerun or inspect updated analysis on the final commit and confirm no blocking issues remain.

Never publish exploit payloads, secrets, or credentials in public review comments. See [SECURITY.md](../../SECURITY.md).

## 6. Decide and integrate

Before approval or self-merge, verify:

- Current branch and reviewed SHA are still the ones to be merged.
- All applicable required checks are complete and successful.
- High-risk independent review is present, where required.
- No unresolved, credible P0/P1/P2 security or data-integrity defects remain in the change. Resolve or explicitly document P3 observations with a tracked owner and priority.
- Review discussions, compatibility risks, and operational handoffs have been addressed.
- The correct target is `dev`; `main` is curated for releases.

This P0-P3 classification is [triage guidance](triage.md#practical-priority-language), not a replacement for branch protection or an excuse to self-approve high-risk work. Maintainers may apply a stricter bar where warranted.

For a final manual merge, GitHub CLI supports an exact-head guard:

```bash
gh pr merge 1234 --repo odysseus-dev/odysseus --squash \
  --match-head-commit <reviewed-full-head-sha>
```

Use `--delete-branch` only when removing the source branch is appropriate and does not disrupt another workstream. Do not force a merge past review or security requirements.

## 7. Post-merge follow-up

Verify the PR actually merged and reference its integration commit. Record remaining operational work, such as schema migrations, backup/restore verification, service restarts, or compatibility documentation. A code change to a container mount does **not** by itself migrate existing runtime data.

Do not silently mark a deferred data migration complete; track its owner, evidence, rollback, and execution separately.

## Reusable reviewer handoff

```text
PR / target:
Reviewed source HEAD SHA:
Current target branch SHA:
Scope and changed files:
Impact and risk classification:
Security / correctness invariants challenged:
Pre-fix reproducer and post-fix evidence:
Tests run, skipped, and environment:
CodeQL / scanning alert dispositions:
Required GitHub checks (exact SHA):
Mergeability and remaining conflicts:
Unresolved P0 / P1 / P2 / P3 findings:
Rollback / operational follow-ups:
Independent qualified review required? Completed?
Verdict: APPROVED / CHANGES REQUIRED / BLOCKED
```

State what was actually verified. Do not write "all checks green" if only a subset of workflows was inspected.
