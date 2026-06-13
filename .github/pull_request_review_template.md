# Pull Request Review Template

Use this shape as a copyable reference for substantive PR reviews; GitHub does
not auto-apply this file to review comments. Omit sections that do not add
useful signal. Lead with confirmed findings; keep speculative notes out of the
public review unless they are framed as a concrete open question.

## Small PR Path

For narrow docs, typo, test-only, or obvious local fixes, a short review is
enough:

```md
LGTM after checking:
- scope:
- validation:
- residual risk:
```

Use the fuller structure below for larger, risky, multi-finding, or
security-sensitive reviews.

## Findings

**<sub><sub>![P2 Badge](https://img.shields.io/badge/P2-yellow?style=flat)</sub></sub> issue (test): Short issue title**

- **Problem:** Concrete broken flow, contract, input, or risk.

- **Impact:** Why this matters to users, CI, maintainers, data, security, or scale.

- **Ask:** Smallest practical correction or decision the author should make.

- **Location:** `path:line`

## Open Questions

- **question (scope, non-blocking): Short author question** Ask the concrete
  intent, scope, or tradeoff question.

## Validation

- Ran:
- Not run:
- Residual risk:

## PR Hygiene

- Target/template/checks:
- Related, duplicate, or superseding context:

## No Findings Variant

```md
## Findings

none confirmed

## Validation

- Ran:
- Not run:
- Residual risk:
```

## Legend

- **Findings:** Verified, author-actionable issues that should be fixed or
  consciously accepted before merge.
- **Priority badges:** The shields.io badges below are optional formatting for
  priority labels. Plain `P0`, `P1`, `P2`, or `P3` text is also acceptable when
  an external image dependency is undesirable or may not render.
  - **P0:** `![P0 Badge](https://img.shields.io/badge/P0-red?style=flat)` -
    release-blocking or actively dangerous.
  - **P1:** `![P1 Badge](https://img.shields.io/badge/P1-orange?style=flat)` -
    serious bug, security risk, data-loss risk, or broken primary flow.
  - **P2:** `![P2 Badge](https://img.shields.io/badge/P2-yellow?style=flat)` -
    meaningful correctness, test, maintainability, or edge-case issue.
  - **P3:** `![P3 Badge](https://img.shields.io/badge/P3-lightgrey?style=flat)` -
    minor polish or low-risk cleanup.
- **Intent labels:**
  - **`issue`:** A confirmed defect, regression, broken contract, or concrete
    risk.
  - **`suggestion`:** A non-blocking improvement that would make the PR clearer,
    safer, or easier to maintain.
  - **`nit`:** A tiny, non-blocking cleanup or style note. Use it only when the
    author can safely ignore it without changing the review outcome.
  - **`question`:** A real author-facing clarification about intent, scope, or
    tradeoffs. Do not use questions to hide an issue that should be stated
    directly.
  - **`LGTM`:** "Looks good to me." Use only when the review found no blocking
    issues, or when any remaining notes are clearly optional.
- **Decorations:** Optional labels in parentheses that clarify the finding type,
  scope, or merge impact.
  - **`security`:** Auth, authorization, ownership, secrets, SSRF, injection,
    unsafe external input, or other trust-boundary concerns.
  - **`test`:** Missing, failing, misleading, brittle, or insufficient tests.
  - **`scope`:** PR scope, feature boundaries, unrelated churn, or work that
    should be split into a separate issue or PR.
  - **`ci`:** CI configuration, workflow failures, flaky checks, or validation
    signal quality.
  - **`api`:** Route, request/response, public function, schema, persistence, or
    integration contract changes.
  - **`docs`:** User-facing docs, contributor docs, examples, or comments that
    need to change with the code.
  - **`non-blocking`:** Useful feedback that should not prevent merge by
    itself.
- **Finding fields:**
  - **Problem:** What is wrong, what contract is ambiguous, or what risk the PR
    introduces.
  - **Impact:** Why the problem matters in practical terms.
  - **Ask:** The smallest concrete fix, test, or decision requested from the PR
    author.
  - **Location:** The most useful repo-relative file and line reference for the
    finding, using `path:line`.
- **Optional sections:**
  - **Open Questions:** Genuine scope or intent questions; omit when there are
    no real questions.
  - **Validation:** What the reviewer ran, what was intentionally not run, and
    what risk remains after review.
  - **PR Hygiene:** Target-branch, template, CI/check, duplicate, related-work,
    or superseding-PR notes.
- **`none confirmed`:** Use only when no review-worthy findings were confirmed;
  still list validation gaps or residual risk when relevant.
