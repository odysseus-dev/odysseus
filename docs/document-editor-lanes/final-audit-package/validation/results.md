# Validation results

Commands below ran in `/home/alteixeira20/Projects/Odysseus/worktrees/document-editor` unless noted. Status uses PASS, FAIL, SKIPPED, BLOCKED, or NOT RUN. An implementation agent running a check is not an independent audit.

## Extraction and architecture

- PASS: `/home/alteixeira20/Projects/Odysseus/audit-artifacts/tools/document-extraction-verifier/bin/verify.mjs --worktree "$PWD" --manifest docs/document-editor-lanes/lane-c-panel-lifecycle/removal.json --json`
  - Baseline: 565 items (436 declarations, 9 top-level statements, 120 `openPanel()` statements).
  - Accounting: 557 moved, 8 composition-root items retained, 0 missing, 0 duplicate, 0 violations.
  - Exports: 46 modules, 0 violations.
  - Import graph: 55 modules, 320 edges, 0 cycles, 0 layer violations.
  - State ownership: 48 files, 0 violations.
  - Ports: 12 commands, 6 hooks, optional observer contract allows 1, actual registered observers 0, 0 violations.
  - Lane C removal: 149/149, 365 authorized rewrites, 2 Class 4 PDF persistence hook substitutions requiring human review, clean simulation. The two `openPanel()` statement wrappers are separate Class 5 rewrites.
- PASS: `node tests/run-all-tests.mjs` in the canonical verifier directory, 25/25 positive and negative controls.
- PASS: `node scripts/check_frontend.mjs`, 98 maintained modules.
- PASS: `python3 scripts/build_frontend.py --check`, 0 changed modules.
- PASS: `git diff --check`.
- PASS: maintained/published parity and module registration checks, no stale published files.

## JavaScript and Python regressions

- PASS: `node --experimental-loader ./tests/helpers/document_email_host_loader.mjs --test tests/document*.test.mjs`, 114 passed, 0 failed, 0 skipped. Node reports its standard experimental-loader warning.
- PASS: `/home/alteixeira20/.venvs/odysseus-pr-6495/bin/python -m pytest -q tests/test_document*.py`, 401 passed, 0 failed, one SQLAlchemy deprecation warning.
- PASS: `/home/alteixeira20/.venvs/odysseus-pr-6495/bin/python -m pytest -q tests/test_codeql_security_js.py::test_pr6503_ledger_email_and_gallery_security_contracts -vv`, 1 passed. This pins security behavior after email helper extraction.
- PASS: Lane A JavaScript behavior tests, 4 passed; API/persistence lifecycle tests, 19 passed (recorded in the Lane A handoff).
- PASS: Lane B JavaScript suites, 7 passed; persistence lifecycle tests, 16 passed; targeted browser/Python regressions, 40 passed; orphaned library open regressions, 6 passed (recorded in the Lane B handoff).
- PASS: previously completed Lane B autosave, close-draft, failed delete rollback, and observer cleanup regressions remain included in the complete document suite.
- BLOCKED: `/home/alteixeira20/.venvs/odysseus-pr-6495/bin/python tests/run_focus.py --fast -- --maxfail=1 -q` reached 5,472 passed, 51 skipped, 5 deselected, 108 warnings, and 187 subtests before stopping on `tests/test_editor_layer_scoped_actions_browser.py::test_remove_background_sends_only_active_layer_and_preserves_others`. Isolated reproduction confirms fixture setup fails because Python Playwright is not installed (`playwright is required for editor browser tests`). This test is outside the Document Editor scope; it is an environment limitation, not a product failure. The run also reports the release smoke suite skipped because `APP_PORT` is unset.

## Browser validation and manual testing

- PASS: browser-backed characterization/lifecycle tests included in the full 401-test document Python suite; the specific browser runner is `tests/test_document_characterization_runner.py`.
- SKIPPED: release smoke suite reports `APP_PORT` unset and requires the repository's `scripts/odysseus-smoke` launcher. No live application instance was used for this run.
- BLOCKED: the Python Playwright browser fixture required by one unrelated fast-lane test is unavailable. Document browser characterization runs via the Node Playwright infrastructure did execute as part of the document-focused suite.
- NOT RUN: maintainer manual QA; use `../qa/manual-checklist.md` after independent audits.

## Test environment notes

The repository virtualenv was required. System `pytest` was unusable because its interpreter/dependency environment did not match the repository. The successful Python suites used `/home/alteixeira20/.venvs/odysseus-pr-6495/bin/python`. No test was marked skipped to hide a failure and no assertion was weakened.
