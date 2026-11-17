# Notes implementation validation and checkpoint receipts

Implementation baseline: `1cc56cb9d78f3e2399d1d61155b71d771a17b85b`, updated via
non-destructive fast-forward to latest official `upstream/dev` at
`86b886c611338282af6d4a717246ea4e256f58df` (incorporating PRs #6704 and #6693).
Independent AGY audit returned CONDITIONAL PASS. Pre-publication corrective
pass verified issue template, PR template, visual evidence paths, and em-dash removal.
The official integration branch is `dev`. Tracking issue #6712; PR #6713.

## Final executed evidence

| Check | Result |
| --- | --- |
| Notes Python tests, discovered by filename, plus F1 publication/graph/offline/loader tests | 172 passed, one release-smoke skip without `APP_PORT` |
| Node architecture, Notes oracle, domain, infrastructure, application, reminders, drafts, selection and workflow tests | 46 passed |
| Native Python lifespan/static distribution probe | Passed for every mapped output, with source/Node absent |
| Existing Docker image distribution probe | Passed with networking disabled and candidate files mounted read-only |
| Real authenticated native app, desktop and touch-mobile | Passed CRUD persistence, owner identity, actual Chat selected-context indicator/reset and the original release smoke scenario |
| Real Chromium first evaluation of Notes offline | Passed; every maintained output cached, facade/canonical API identity shared |
| Source/output parity and F1 boundaries | Passed, 43 maintained modules and nine composition-only legacy exceptions |
| Foundation-based full pytest execution | 13 failed, 13,909 passed, 60 skipped, six subtests passed, 192 warnings, 517.04 seconds |
| Targeted unchanged F1 baseline reproduction | Same 13 failures, 77 passed, six warnings |
| Targeted untouched latest `dev` reproduction | Three inherited failures, 87 passed, six warnings |
| Final indexed latest `dev` plus Notes overlay, full pytest | Three same-environment baseline failures, 13,923 passed, 60 skipped, six subtests passed, 192 warnings, 512.09 seconds |
| Untouched latest `dev` readiness checks, without the prewarm override | Five passed |

The foundation-based full repository result is not green. Its failures reproduce against
the unchanged foundation in a disposable archive, with the same interpreter and
closed Chroma port. None was disabled, relabeled or fixed in this Notes change.
The focused Notes suite and required source/deployment gates are green. The final
code/test overlay is fingerprinted in [implementation.sha256](evidence/notes/implementation.sha256).
The only post-full-suite test-source edit removes a terminal
blank line from the browser wrapper; its content was compared ignoring terminal
whitespace and all three focused contract/browser checks passed again. Raw command
outputs are kept out of the repository; the counts above are the record.

The first full run reported 19 failures. Six were introduced validation issues:
the SW cache name did not follow the existing numeric convention; a temporary
probe environment input was not registered by the configuration-reference checker;
and a Notes source assertion still expected the original default object spelling.
The cache name now follows the convention, the probe passes its output list as a
command argument, and the assertion verifies the new public binding. All 58 tests
in those affected files passed after correction. The foundation-based full run has only the
13 reproduced baseline failures below.

### Reproduced baseline failures

- `tests/test_aux_llm_owner_scope.py::test_auto_compaction_utility_endpoint_keeps_chat_owner`
- `tests/test_chatgpt_reasoning_and_grouping.py::test_history_and_chat_routes_support_reasoning_effort`
- `tests/test_containment_enforcement.py::test_namespace_handshake_closes_model_stdin`
- `tests/test_containment_enforcement.py::test_execution_environment_cannot_replace_probed_bwrap[False]`
- `tests/test_context_compactor.py::TestMaybeCompactFourthMessage::test_does_not_crash_on_none_content_turn`
- `tests/test_context_compactor.py::TestMaybeCompactFourthMessage::test_handles_multimodal_list_content`
- `tests/test_css_computed_style_snapshot.py::test_computed_styles_match_the_committed_baseline`
- `tests/test_history_compact_tool_calls.py::test_manual_compact_tolerates_chatmessage_with_none_content`
- `tests/test_history_compact_tool_calls.py::test_manual_compact_tolerates_dict_message_with_none_content`
- `tests/test_history_compact_tool_calls.py::test_registered_manual_compact_route_tolerates_none_content`
- `tests/test_history_compact_tool_calls.py::test_registered_manual_compact_route_uses_session_owner`
- `tests/test_model_helper_owner_scope.py::test_history_compact_resolves_with_owner_scope`
- `tests/test_readiness.py::test_tool_index_is_reported_but_not_critical_by_default`

These cover existing compaction fixtures/source assertions, namespace containment,
settings-row CSS baseline drift and readiness expectations. The baseline reproduction used
`git archive` into `/tmp/odysseus-notes-baseline-OitxsU`, not another branch/worktree.

## Reproducible commands

### Latest integration target

Final upstream inspection found `86b886c611338282af6d4a717246ea4e256f58df`.
PR #6704 repairs ten of the foundation's reproduced failures; PR #6693 adds
remote-only skill gating. Neither changes a file owned by the Notes diff.
The branch remains on the requested foundation, with no merge or rebase.
An archive of that latest target plus the exact final Notes code/test overlay is
validated in `/tmp/odysseus-notes-latest-VmZrgC` using the same full-suite command.
The fixture has a local Git index, initialized from the latest target and updated
with the review files, but no commit. This enables the tracked-publication checks.
The first archive-only attempt had one Git-metadata failure and seven checkout-only
skips. Both affected files subsequently passed all 19 tests with metadata present;
the complete suite was then rerun in that indexed fixture.
An untouched archive at `/tmp/odysseus-notes-latest-baseline-mfE4ss` reproduced
the remaining two containment failures and the readiness failure: three failed,
87 passed.

These are reproduced local-environment failures, not a claim that official CI is
currently failing. The readiness assertion expects prewarming enabled, whereas
the full-run safety command explicitly sets `ODYSSEUS_TOOL_INDEX_PREWARM=0`.
With that override absent, all five readiness tests pass on untouched latest `dev`.
The two namespace tests receive extra `Failed to create stream fd` stderr in this
host's contained process output. That same output occurs without the Notes overlay;
no backend containment code was changed or a failing assertion weakened.
Reproduce those two checks on the supported CI runner before claiming a green
full-suite certification.

In the final indexed full-suite run the only failures are `test_namespace_handshake_closes_model_stdin`,
`test_execution_environment_cannot_replace_probed_bwrap[False]`, and
`test_tool_index_is_reported_but_not_critical_by_default`. The missing-Git-metadata
publication failure and the seven checkout-only skips are absent from this run.
No Notes regression was reported. Feature code matched N6 at publication. Later
commits on the PR changed `ui/reminderPicker.js` (date input value assigned through
the DOM property) and, after review, `index.js`, `ui/layout.js` and
`application/state.js`; `implementation.sha256` covers the current files.

To reproduce the integration rehearsal, extract the latest target with `git archive`
into a fresh fixture and copy only files in the changed-file inventory on top.
Do not overlay foundation files that are unchanged in this PR. Install the locked
development dependencies in the fixture, then run the commands below there.

While this review diff is uncommitted, this prepares an equivalent Linux fixture
without changing either task worktree:

```sh
notes_fixture=$(mktemp -d /tmp/ody-notes-integration-XXXXXX)
git archive 86b886c611338282af6d4a717246ea4e256f58df | tar -x -C "$notes_fixture"
{ git diff --name-only -z; git ls-files --others --exclude-standard -z; } |
  tar --null -T - -cf - | tar -xf - -C "$notes_fixture"
git -C "$notes_fixture" init -q
git -C "$notes_fixture" fetch --no-write-fetch-head "$PWD" 86b886c611338282af6d4a717246ea4e256f58df
git -C "$notes_fixture" read-tree 86b886c611338282af6d4a717246ea4e256f58df
(cd "$notes_fixture" && npm ci --ignore-scripts --no-audit --no-fund && git add --all)
# Run the validation commands below from notes_fixture, using an isolated Python environment.
```

Use a real dependency directory for this recipe; a symlink named `node_modules`
can bypass the repository's directory-only ignore pattern. No checkpoint or
fixture commit is needed. After integration, validate the actual integrated checkout
directly, rather than relying on the uncommitted-diff recipe.

### Commands

Use the actual tested interpreter, or install the repository requirements in your
own isolated virtualenv. Node development dependencies were installed with the
existing lockfile and `npm ci --ignore-scripts --no-audit --no-fund`.

```sh
python3 scripts/build_frontend.py
python3 scripts/build_frontend.py --check
node scripts/check_frontend.mjs
node scripts/check_frontend.mjs --inventory

ENDPOINT_URL=http://127.0.0.1:1 node --test \
  tests/frontendArchitecture.test.mjs tests/noteTestOracle.test.mjs \
  tests/notesDraftAutosave.test.mjs tests/selectedNotesContext.test.mjs \
  tests/notesCharacterization.test.mjs tests/notesRecurrence.test.mjs \
  tests/notesInfrastructure.test.mjs tests/notesApplication.test.mjs \
  tests/notesReminders.test.mjs tests/notesWorkflows.test.mjs

CHROMADB_HOST=127.0.0.1 CHROMADB_PORT=1 \
  python -m pytest -q -rs \
  $(rg --files tests -g 'test_*note*.py') \
  tests/test_frontend_build.py tests/test_frontend_foundation_offline_js.py \
  tests/test_frontend_module_graph.py tests/test_frontend_module_version_parity.py \
  tests/test_panel_loader_js.py

python scripts/verify_frontend_runtime.py
python scripts/verify_notes_browser_runtime.py

docker run --rm --network none \
  --mount type=bind,src="$PWD",dst=/candidate,readonly \
  --workdir /candidate --entrypoint python \
  <existing-odysseus-image> scripts/verify_frontend_runtime.py

CHROMADB_HOST=127.0.0.1 CHROMADB_PORT=1 \
  ODYSSEUS_STARTUP_WARMUPS=0 ODYSSEUS_TOOL_INDEX_PREWARM=0 \
  python -m pytest -q -rfs

git diff --check
```

`ENDPOINT_URL` satisfies the existing oracle's import configuration; its endpoint-
driving main does not run in these unit tests. Port 1 is the closed loopback target
used for pytest; the two runtime scripts reserve their own non-listening Chroma port.
The native browser script creates a private distribution/database and a temporary
authenticated user, then terminates the server and removes that fixture. It uses
the real application and Notes API rather than mocked browser routes.

The deterministic browser harness uses stub API responses and the real Notes ESM,
DOM and stylesheet graph. It verifies namespace/default methods and arities,
aliases, remount/search reset, rejected/accepted attachments, draft persistence,
drawing/undo and listener/portal cleanup. It also checks larger-touch reorder after
re-render, active-drag close cleanup and cancellation of a mobile selection hold.
It includes the mobile viewport meta tag,
tap-to-read/edit behavior and the existing selection jiggle. The original monolith
can be replayed with `NOTES_BASELINE=1`; it is read from the frozen F1 revision.

## Browser and packaging evidence

Actual app screenshots: [desktop](evidence/notes/native-desktop.png) and
[mobile](evidence/notes/native-mobile.png). These are Chromium screenshots of the
disposable authenticated app at 1440 x 1000 and 390 x 844, not rendered mockups.
The deterministic before/after fixture had an identical mobile PNG and only 14
changed desktop pixels in the header region; no layout/style change was observed.
CSS, app bootstrap and HTML have no implementation diff.

The native and Docker distribution probe serves all manifest outputs without
`frontend/`, `node_modules/` or Node on the server path. It checks response bytes,
JS MIME, revalidation, CSP, shell startup, static confinement and source-tree 404.
Docker used existing image
`sha256:cc2d47e2327d573af01c6b027f23d2ab0f2ee9b85d658e9eb8065bd02b9c3515`.
This is not a fresh Dockerfile build or deployment.

Tested locally: Linux, Python 3.14.4, Node 22.23.1 and Playwright/Chromium 1.62.1.
CI's exact Python 3.11/Node 20 pair, Firefox/WebKit, macOS/Windows executable builds,
live model/email notification delivery and full root offline reload were not run.
The Docker runtime uses its own existing dependency versions. Shared active-window
gesture interleavings are not exhaustively covered. No broader platform certification
is claimed.

The full-run safety overrides differ from official CI's plain pytest command.
For the readiness default-contract check, use
`env -u ODYSSEUS_TOOL_INDEX_PREWARM python -m pytest -q tests/test_readiness.py`.
Keep explicit warmup/storage isolation on the disposable live-app probes.

## Internal checkpoints

Checkpoint snapshots are kept with the maintainer handoff, not in the repository.
The hashes below identify them. They contain scoped source/output/facade/manifest/test
overlays, not secrets or user data. Each starts from the same frozen baseline; they
are not separate PRs or release candidates.

| Stage | Boundary and executed exit evidence | Snapshot SHA-256 |
| --- | --- | --- |
| N0 | Public/consumer/state/lifecycle map; 95 baseline Python and 11 Node passes; four new characterization tests; desktop/touch harness; 33 F1 publication/graph checks | `db0a2e6e68fec3442ce350ffb0acf47bcdf2a5266f7c64c2bf910a1c41c4fb62` |
| N1 | Pure recurrence/date rules; eight Node and nine focused Python passes; differential oracle in four time zones; parity/boundaries | `fd0638470744706fbbd5787cb47bd2078a93c9b854139f89f8fdc169c1f353c6` |
| N2 | HTTP/storage adapters; fourteen Node and seven focused Python passes; exact requests, keys and fallback contracts | `30826fb7972d1b9e3a790f97ee81f66dcb3dc58eb7d23e24d4f46e6d841b7c73` |
| N3 | Single state owner and CRUD/undo boundary; ten Node and seven focused Python passes; read-only state and archive rollback | `4bc5293519b8438d0126a61887bea64195dcdb7d59bcfaaba8ed4ae8c38be439` |
| N4 | Page-owned reminder workflow; four reminder contract tests and seven focused Python passes; singleton interval, catch-up, markers and fallback | `febb835c4958c8758e4b7c23ce89f3ec8d103c2cc28f89817d258b3791a9fcdf` |
| N5 | Complete UI/application integration; 44 Node passes, targeted source guards and actual browser checks; drawing binding correction, read-only state and disposal | `e147f645bdb2161a4b1dc6cf10c36ba6d0c214d8e12fccb478835043eaaf3e94` |
| N6 | Final facade, identity, exact offline graph, native/Docker checks, final editor-deletion/reorder ownership review, touch cleanup regression checks and maintainer review package | `c450e91f9fbfe5f0cd805e596b92a76383f22a74e9515ca3e3f6154aebf5f61f` |

The N6 overlay contained the final code, tests, manifest and attributes at
publication, not the evidence directory itself. From the repository root, run
`sha256sum -c docs/frontend-architecture/evidence/notes/implementation.sha256`
to verify the current implementation files.

No checkpoint commits were made. To reproduce an overlay, create a fresh temporary
directory, extract `git archive 1cc56cb9...` there, then extract the desired
snapshot from the handoff on top. Install the locked test dependencies
in that fixture and run its relevant commands. Begin from the baseline rather than
overlaying snapshots onto a later dirty tree. For live rollback, release a complete
matching artifact through the authorized release workflow.

Temporary mechanical extraction scripts are not part of the supported deliverable.
They were archived locally, then removed. The first generated patch accidentally targeted
the separate foundation worktree; only those edits were reversed, absolute paths
were adopted, and that worktree's clean state was verified afterward.

## Open PR overlaps and handoff limits

The following remained open at the read-only GitHub refresh:

- [#6585](https://github.com/odysseus-dev/odysseus/pull/6585): optional Notes field removal in the backend. Existing null-field behavior is retained here.
- [#6647](https://github.com/odysseus-dev/odysseus/pull/6647): read-only Notes intent routing.
- [#6652](https://github.com/odysseus-dev/odysseus/pull/6652): `manage_notes` registry dispatch.
- [#6470](https://github.com/odysseus-dev/odysseus/pull/6470): shared theme, typography, sidebar and modal changes.
- [#6695](https://github.com/odysseus-dev/odysseus/pull/6695): extension system/platform integration.

Their changes were not merged or reimplemented. Shared UI exceptions, root-SW scope,
the original last-completion fetch race, temporary-note correlation and canvas-draft
limits are documented in the case study. Backend owner checks and PR #6495 contracts
remain authoritative.
