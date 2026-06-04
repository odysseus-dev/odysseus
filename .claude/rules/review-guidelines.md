# Review Guidelines

How to *conduct* a review — workflow, threads, summaries, and author
interaction. For *what to check*, see the domain reviewers in
[`reviewers/`](reviewers/) and the cross-cutting
[`review-checks-common.md`](review-checks-common.md).

## Workflow

1. Read the PR intent: `gh pr view <N> --json title,body`. Note any
   stated test steps and any "POC / WIP / experimental" framing.
2. Read the diff: `gh pr diff <N> --stat`, then `gh pr diff <N>`.
3. Check existing review comments first
   (`mcp__github__get_pull_request_review_comments`) so you never repeat
   feedback from a previous round.
4. Post inline comments only for NEW issues in changed lines.
5. Post exactly ONE summary comment per review cycle (see below).

## Scope & noise

- Review the lines this PR changes. Issues in **unchanged** code are
  "pre-existing, out of scope" — mention at most informationally, never
  block on them.
- Group related issues; keep the summary brief.
- Every inline comment references a file path and line.
- Never re-raise feedback the author has already addressed or consciously
  declined in an earlier round.

## Context-aware depth

| Context | Focus | Avoid |
|---------|-------|-------|
| Normal change | All standards below | Bikeshedding |
| POC / experimental / WIP | Does it work? Security. Broken logic. | YAGNI, edge cases, style |
| Refactor (no behavior change) | Behavior preservation | New-feature requests, scope creep |
| Infra / config | Behavioral changes, correctness | Questioning stated design intent |

Detect POC from the PR title/body ("POC", "demo", "prototype",
"experimental", "WIP"). If POC, open the summary with "Reviewing as
POC with relaxed standards" and review only bugs, security, and broken
logic.

## Summary comment

- ONE summary per cycle, **COMMENT status** — never `APPROVE` or
  `REQUEST_CHANGES` (a human owns the merge decision).
- High-level observations and cross-cutting notes only. Do NOT repeat
  inline-comment content.
- If the PR is clean, post a short positive summary and stop. An empty
  nitpick wall is noise.
- Do NOT comment on PR title/body/commit-message *formatting* — that is
  out of scope for code review.
- After the author pushes fixes: acknowledge what was addressed in one
  brief note; only flag genuinely new or still-unfixed issues.

## Code suggestions

Use GitHub ```suggestion blocks for straightforward, committable fixes
(single-line: comment on the line; multi-line: set start_line/line). Add
a one-line explanation before the block. Use prose for complex changes.
Never use a suggestion block for non-code changes (renames, file moves).

## Draft handoff

If the review posted any inline comments, convert the PR back to draft
(`gh pr ready --undo <N>`) so the author can fix everything in one pass
without triggering a re-review on each push. The author marks "Ready for
review" again when done. Do NOT convert a clean PR to draft.

> Verify the real inline-comment count from the API before deciding —
> `gh pr view <N> --json reviewComments --jq '.reviewComments | length'`
> — do not rely on recollection of whether you posted comments.
