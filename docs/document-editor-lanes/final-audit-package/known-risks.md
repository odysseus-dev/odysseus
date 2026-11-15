# Known risks and remaining validation

## Confirmed unresolved defects

- None identified in the completed document-focused automated suites or the final extraction, architecture, and publication checks.

## Resolved regressions

- Source-editor autosave attempted to assign imported timer bindings. Scheduling and cleanup now run through owner functions in `sourceEditor.js`; autosave, close-draft, retry, and pending-save regressions pass.
- Email send workflow referenced missing collaborators. Its in-flight guard could reset after a dispatched request threw, allowing duplicate sends. Required interfaces are now explicit imports and duplicate-send coverage passes.
- Rich-text dropdown repeat activation reused stale activation state. `formatMenus` now issues owner tokens; pointer and keyboard repeat tests pass.
- Security regression tests searched for helpers only in the monolith after those helpers moved. The browser harness now extracts the actual maintained function bodies using Acorn, and its isolated test passes.
- The verifier regression harness fell back to the current integration source when its optional baseline worktree was absent. It now loads the pinned 087e6c88 source from Git, generates fixtures deterministically, and its full 25-case suite passes.

## Residual architectural concerns

- Eight Appendix A items remain in the integration root by design: `init`, `_maybeOpenDocFromHash`, `clearAll`, `getCurrentDocId`, `documentModule`, and three facade publication statements. The independent architecture audit should confirm these remain orchestration and compatibility code rather than accumulating extracted business logic.
- The optional image-selection observer port inventory permits one registration while the final verifier observes zero registrations. `_selResizeObserver` is a separately owned selection lifecycle observer; confirm this distinction remains clear to maintainers.
- Existing host globals are retained only where the application or legacy consumers require compatibility. Independent reviewers should verify those call sites against actual consumers.

## Untested lifecycle interactions

- The manual checklist has not yet been run by a maintainer in a live application session. In particular, exhaustive user-driven PDF editing interleaved with every close/session-switch timing remains untested manually.
- Mobile device behavior and focus restoration are covered by automated regressions but still need the post-audit manual pass.

## Environmental test limitations

- The broad `tests/run_focus.py --fast` run reached 5,472 passed, then stopped because one unrelated image-editor browser test requires Python Playwright, which is not installed in this environment. The isolated setup error was reproduced and reported.
- The release smoke suite was skipped because `APP_PORT` was unset. It requires `scripts/odysseus-smoke` to boot the application.
- The successful 401-test document suite and Node Playwright characterization passed in the available environment.

## Nonblocking follow-up

- Re-run the full repository fast lane and release smoke suite in an environment with Python Playwright and the application launcher available.
- Complete the manual checklist after the two independent audits and any corrections they require.
