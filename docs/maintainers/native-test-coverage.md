# Native test merge gate

`Native test suites` runs on pull requests, `dev`/`main` pushes, and manual workflow dispatch.
It establishes authoritative JavaScript and browser validation without imposing unnecessary merge latency on normal pull requests.

## Selective Architecture

The workflow implements a three-level execution architecture:

1. **Level A: Coverage Verification and Selection**
   Runs on every pull request in approximately 10 to 15 seconds without launching Chromium or heavy application dependencies. It validates the complete 84-file test inventory against `tests/native-ci-manifest.json`, checks that declared pytest wrappers exist and invoke their assigned tests, and computes required test execution from the PR diff against merge base (`git merge-base`).

2. **Level B: Selective PR Validation**
   Runs only the suites required by the PR diff:
   - Documentation-only changes (`docs/`, `website/`, `*.md`) skip both Node and Chromium jobs, taking negligible critical-path CI time.
   - Independent backend Python changes validated by pytest shards skip Node and Chromium jobs.
   - Photo Editor changes (`static/js/editor/`, `tests/e2e/photo-editor/`) trigger the Chromium browser suite and native Node tests.
   - Shared frontend infrastructure changes (`static/app.js`, `static/index.html`, `static/css/`, `static/js/shared/`, `package.json`) trigger both suites.
   - Authentication, session, and core API changes (`core/auth.py`, `core/session_manager.py`, `routes/auth_routes.py`, `app.py`) trigger both suites.
   - Test infrastructure, runner, workflow, or manifest changes trigger both suites.
   - Unknown or unclassified paths fall back conservatively to full execution fail-closed.

3. **Level C: Full Integration Validation**
   Runs all 26 native Node suites (157 test cases) and all 36 Chromium suites (137 test cases) on:
   - Every push to `dev`.
   - Every push to `main`.
   - Explicit manual workflow dispatches (`workflow_dispatch`).

## Aggregate Status Check and Enforcement

`Authoritative native suites` is the single required status check. Because the workflow uses no workflow-level `paths:` filter, this job is always created on every PR and push.

The aggregate gate evaluates:
1. `coverage-decision` must have succeeded without error.
2. If `run_node` is required (`true`), `native-node` must have succeeded. If skipped or failed, the gate fails.
3. If `run_browser` is required (`true`), `browser` must have succeeded. If skipped or failed, the gate fails.
4. If a suite was permitted to skip by policy (`false`), its job status must be `skipped`.
5. Any unexpected failure, cancellation, or unpermitted skip fails the gate immediately.

Repository administrators must configure branch protection or rulesets to require `Authoritative native suites`. Note that the repository ruleset `Protect dev` is currently disabled; maintainers must enable enforcement for this gate to block merges.

## Test Inventory and Execution Ownership

All 84 discovered test files importing `node:test` are partitioned into explicit, disjoint categories in `tests/native-ci-manifest.json`:
- **26 files in `native-ci`:** Directly executed by `node .github/scripts/run_native_tests.mjs` (157 test cases total, including `notesColors.test.mjs`, `notesReminderText.test.mjs`, `notesBrowserRunner.test.mjs`, `notesDraftRecovery.test.mjs`, `notesExports.test.mjs`, `notesMarkdownSanitize.test.mjs`, `notesMarkdownSerializer.test.mjs`, `notesQueries.test.mjs`, `notesWindowSync.test.mjs`, and `markdownPreviewParity.test.mjs`).
- **1 file in `browser-ci`:** `tests/emailReplyBrowser.test.mjs`, executed against the disposable authenticated application via `tests/e2e/photo-editor/email-reply-native.spec.js`.
- **1 file in `architecture-ci`:** `tests/frontendArchitecture.test.mjs`, executed during frontend architecture gates.
- **15 files in `ci-syntax`:** Document behavior and reference suites executed in `ci.yml` `node-syntax`.
- **11 files in `behavior-delegates`:** Behavioral modules invoked via `runBehaviorSuite` by their corresponding runner tests.
- **30 files in `python-ci`:** Validated via established pytest wrappers in `python-tests` shards.

The manifest validator (`.github/scripts/coverage_decision.py --validate-manifest`) verifies that every assigned file exists, that wrappers execute their declared tests, and that no unassigned or duplicated tests exist.

## Browser Testing and Performance Safeguards

The Chromium browser job runs 137 test cases (136 Photo Editor scenarios and 1 native email reply contract driver) against a fresh authenticated loopback app.
- Server startup overhead is bounded by disabling background MCP server connections (`ODYSSEUS_DISABLE_MCP=1`), model warmups (`ODYSSEUS_STARTUP_WARMUPS=0`), and tool index prewarming (`ODYSSEUS_TOOL_INDEX_PREWARM=0`).
- Tracing is disabled in CI by default (`trace: 'off'`) to prevent screencast and DOM capture overhead across all scenarios. Tracing requires explicit opt-in (`PLAYWRIGHT_ENABLE_TRACE=1`).
- Failure screenshots (`screenshot: 'only-on-failure'`) remain active for diagnostics.

## Artifact Security and Confinement

- **Fail-closed verification gate (`.github/scripts/verify_sanitized_artifacts.py`):** Pre-upload verification step returns a nonzero exit code on any inspection or deletion failure.
- **Conditional artifact upload:** Artifact upload steps execute only when independent verification succeeds (`if: always() && steps.<id>.outcome == 'success'`).
- **Restrictive published-JUnit representation:** Publishable XML files are restricted strictly to recognized JUnit report filenames and allowed structural elements. Free-form elements (`<properties>`, `<property>`, `<system-out>`, `<system-err>`) are pruned.
- **Skipped-element sanitization:** Clears free-form text inside `<skipped>` tags and normalizes status attributes strictly to safe tokens.
- **Credential scrubbing:** Tokens, Bearer/Basic headers, URL credentials, and passwords in XML attributes or failure text are scrubbed and replaced with `[REDACTED]`. Unrecognized XML files are purged.
- **Fixture ownership contract:** Teardown requires disposable fixture proofs (`.disposable-ci-fixture.json`). Never upload the database or browser storage state.
