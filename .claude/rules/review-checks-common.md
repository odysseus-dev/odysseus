# Cross-Cutting Review Checks

Universal checks applied to **every** changed file, regardless of domain.
For workflow rules see [`review-guidelines.md`](review-guidelines.md); for
domain specifics see [`reviewers/`](reviewers/).

## Severity levels

- **REQUIRED** — a real bug, security hole, or correctness/logic error.
  Should be addressed (or justified) before merge.
- **RECOMMENDED** — an improvement: clarity, naming, structure,
  performance that is not a bug. Advisory; never blocks.

Do not label something REQUIRED unless it is genuinely a defect. When in
doubt, RECOMMENDED.

## False-positive gate (before EVERY inline comment)

Do not post unless ALL of these hold:

0. **In scope** — the file is part of this PR's diff
   (`gh pr diff --name-only`). If not, it is pre-existing — skip.
1. **Real issue** — a bug / security / architecture / performance /
   logic / naming problem, not a style preference a linter owns.
2. **Verified** — you read the actual file at HEAD and the issue still
   applies at the latest commit.
3. **Adds value** — the fix is materially better, not a restatement of
   the original.

If any fails, do NOT post. When in doubt, skip it.

## Code verification protocol

1. Read the actual file — never raise an issue from a diff snippet alone.
2. Confirm exact line numbers and values.
3. Check whether a later commit in the PR already fixes it.
4. Quote the exact code when making a claim.

## Cross-cutting checks

- **Secrets** — no API keys, passwords, or tokens committed to code.
  Odysseus is privacy-first: flag anything that logs, stores, or
  transmits user data, credentials, or PII unexpectedly.
- **Dead code** — for a new function/class/constant, grep for references
  outside its definition. No references → flag as possibly unused
  (exclude tests, exports, and framework entrypoints).
- **Parameter changes** — if a signature changed, grep ALL call sites
  (not just the diff) before claiming a break or suggesting a new
  required argument.
- **Input validation** — validate user/request input early; fail loud
  with context. Flag silent exception swallowing (`except: pass`,
  discarded errors that drive later branching).
- **Naming** — only flag genuinely misleading names. An established
  pattern (5+ existing uses) is a convention, not a smell.

## Known false-positive traps

Verify the real code before raising any of these:

1. Formatting that is already correct (read current code, not diff
   context).
2. Code that no longer exists after a force-push (verify the line is
   still there).
3. A return type / annotation that is already present (read the actual
   signature).
4. YAGNI — accept the author's "defer" judgment.
5. `except E: side_effect(); raise` is NOT swallowing — the caller still
   receives the error.
6. Shell scripts may target different shells — check the shebang before
   flagging syntax.
7. Intentional behavior removal that the PR title/body explains — not a
   bug.
8. f-strings without placeholders (`f"static"`) are ruff F541 — flag the
   missing `{}`, not the string.
