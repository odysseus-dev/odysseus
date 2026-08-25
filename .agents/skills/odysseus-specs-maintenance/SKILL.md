---
name: odysseus-specs-maintenance
description: Reconcile Odysseus implementation-truth specs with the latest origin/dev when explicitly asked to check, refresh, or update specs. Do not use for ordinary code changes or PR reviews that only read specs as context.
---

# Odysseus Specs Maintenance

Keep `specs/` aligned with the final implementation on the latest upstream `dev` without turning plans, commit summaries, or assumptions into implementation truth.

## Mutation Boundary

- Edit specs only when the user explicitly requests spec maintenance or the current task intentionally includes spec changes.
- Treat specs as read-only during ordinary implementation work and code review. Report drift in the task's normal output instead of changing specs.
- Do not require a pull request author to update specs unless the user explicitly asks for that spec work.
- Do not modify product code as part of a specs-only reconciliation. Report a discovered implementation bug separately unless the user expands the task.
- Do not commit, push, open a pull request, or change remote state unless the user explicitly requests that action.

## Establish the Basis

1. Read `specs/_readme.md` completely, including its quality contract, subsystem map, and cross-cutting update triggers.
2. Read `specs/last_specs_check.txt` and identify the most recent completed `dev` checkpoint.
3. Fetch `origin/dev` and record its exact full SHA before inspection. Treat that fetched tree as the implementation source of truth, not the current feature or review branch.
4. Work in a clean focused branch or worktree based on that `origin/dev` SHA. Preserve unrelated user changes; if the current worktree is dirty or serves another task, use a separate worktree.
5. Verify whether the checkpoint is an ancestor of the fetched SHA. If history was rewritten, do not report a misleading linear commit count; compare the final source trees, record the discontinuity, and inspect the affected implementation directly.

## Reconcile

1. Compare the last completed checkpoint with the fetched `origin/dev` tree. Use changed paths and commit history only to route inspection; commit titles, pull request descriptions, and existing specs are not proof of runtime behavior.
2. Map changed implementation paths through the subsystem map and update triggers in `specs/_readme.md`. Read each affected spec plus adjacent cross-cutting specs whose ownership, security, persistence, context, runtime, frontend, integration, or testing contracts may have changed.
3. Inspect the final source, call sites, tests, configuration, migrations, and runtime wiring needed to verify each claim. Distinguish current behavior from superseded intermediate commits.
4. Update only claims that are stale. Preserve accurate content and the established domain-specific structure; do not rewrite unaffected specs for style.
5. Set each changed spec's `Last updated` header to the fetched `dev` short SHA and reconciliation date. Update `specs/_readme.md` only when its own control text, subsystem map, or update triggers changed.
6. Keep planning, research, branch notes, audit logs, and speculative proposals out of `specs/`.

## Validate

1. Re-read the resulting diff against the final `origin/dev` source and confirm that every changed factual claim is source-grounded.
2. Run focused existing tests or checks when they materially validate the documented behavior. Record commands, outcomes, and any residual validation gap; do not claim that passing tests prove untested behavior.
3. Verify Markdown structure and links, confirm every non-index top-level `specs/*.md` file appears exactly once in the subsystem map, confirm provider specs remain routed through `specs/model-providers/_readme.md`, and run `git diff --check`.
4. Confirm the task diff contains no unintended product-code changes and no private paths, credentials, raw logs, or environment-specific workspace details.
5. Append the completed check to `specs/last_specs_check.txt` only after reconciliation and validation finish. Include the date, full checked `dev` SHA, whether specs changed, relevant validation, and the local spec-update branch or commit when available.

## Closeout

Report the checked `dev` SHA, inspected range or history discontinuity, specs changed or confirmed current, validation performed, and residual uncertainty. A no-change reconciliation is a valid result and should still advance the checkpoint after the comparison is complete.
