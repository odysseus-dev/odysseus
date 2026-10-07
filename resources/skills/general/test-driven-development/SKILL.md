---
name: test-driven-development
description: Build or fix software with a focused red-green-refactor loop
version: 1.0.0
category: general
tags: [tdd, testing, debugging, red-green-refactor]
status: published
confidence: 1.0
source: builtin
owner: ""
created: "2026-08-18T00:00:00Z"
---

## When to Use

Use when implementing a feature, fixing a bug, or changing behavior where a regression test can define the expected result. Prefer this workflow for parser, routing, agent-loop, and UI behavior changes.

## Procedure

1. Inspect the relevant code, existing tests, and local conventions before editing.
2. Write the smallest regression test that demonstrates the requested behavior or reproduces the bug.
3. Run that test and confirm it fails for the expected reason, not because the test setup is broken.
4. Make the smallest production change that makes the test pass.
5. Run the focused test again, then run the surrounding module suite.
6. Review the diff for unrelated changes, brittle assertions, hidden state, and missing error paths.
7. Report the tests run and any remaining coverage or environment limits.

## Pitfalls

- Do not write a test that only mirrors the implementation; assert the user-visible contract.
- Do not weaken an assertion just to make a failing test pass.
- Do not skip the focused failing-test step when the behavior is observable in a local test.
- Keep network, filesystem, and model calls deterministic with fakes or fixtures unless the integration itself is under test.

## Verification

- The new regression test fails before the fix and passes after it.
- The relevant focused suite passes.
- The broader suite passes or its failure is explained with evidence.
- The final diff contains the test and the production change needed for the same behavior.
