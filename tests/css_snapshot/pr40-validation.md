# PR #40 final harness validation

Starting branch: `review/pr40-final-lab-validation`.
Starting HEAD: `6f14439e4179b8ad674660e59ca0bea96f1a6ead` (clean).

This cleanup changes snapshot tooling, test harnesses, baselines and documentation.
It changes no application production implementation.

## Reproduction and root cause

Both the computed-style baseline failure and the obsolete markdown VM loader
failure reproduced on the reconciled branch and preserved lab source at
`fff55a786c5c40daebf64d1a12b576d6c889b1a3`. The snapshot mismatch also reproduced
on the original recording revision, `73b1726d`. No canonical lab checkout was used.

Chromium: `151.0.7922.34`; Node: `22.23.1`; Python: `3.12.3`.

All 42 pre-existing Linux/macOS element-hash mismatches were explained by exact
recovery of the committed hashes from raw captures:

| Elements | Proven difference |
|---|---|
| Two document roots | Default font-family `Times` on macOS versus `"Times New Roman"` on Linux |
| 39 elements | `BlinkMacSystemFont` serializes as `"system-ui"` on macOS |
| `custom-system-prompt` | The same alias difference, plus authored `30lh` resolving to `480px` on macOS and `450px` on Linux |

Independent repeated unmodified captures also changed the message textarea outline
offset from `0px` to `2px`: asynchronous autofocus races measurement. Animations
also depend on elapsed time; an idle cascade inventory needs a defined sample time.

## Canonical snapshot contract

- Pin the UA standard font preference through CDP; author declarations still win.
- Normalize only the proven unquoted BlinkMacSystemFont family token; preserve all other families and order.
- Remove autofocus before parsing; focus states were already outside this inventory.
- Pause CSS animations at time zero and finish transitions; keep their computed declarations.
- Opt only `custom-system-prompt/max-height` into line-relative measurement; the `30lh` line count remains significant.
- Preserve all 122 properties, 676 elements and 24 variants.

Two full controlled captures, separated by a 150ms measurement delay on every
page/variant, were byte-identical. Their canonical digest is
`9868d50a542b7ad1`. There were no missing inventory entries. The controlled
lab/merged comparison still differs on exactly five intended PR #40 elements:

| Element | Intended reconciled change |
|---|---|
| app-shell/reasoning-effort-btn | Moved control to the Chat Context home; visibility changes |
| bench/.reasoning-effort-prefix | Removed old narrow composer hiding rule |
| bench/.doc-suggestion-card | Responsive overflow, display and minimum sizing |
| bench/.ge-layers-header | Wrapping layer controls |
| bench/.ge-layers-list | Minimum height and bottom padding |

Only 11 element hashes change from the old committed baseline: these five, the
two pinned root fonts, the two autofocus controls (message and login username),
welcome-screen at the defined animation start, and the line-relative textarea cap.
The other 665 element hashes are unchanged. The new baseline was written from the
proven identical full captures, not from an unexplained failing local capture.

The new browser self-test perturbs timing and font metrics, checks autofocus
suppression, retains animation/transition metadata, distinguishes 30lh from 31lh,
and detects changed animation keyframes. The existing cascade-order test still
detects reordering conflicting declarations.

## Markdown and environment reference

The standalone codefence script previously stripped imports/exports with regexes
and evaluated the result as a classic VM script. Its ui.js pattern missed the
versioned ES-module import. It now uses the existing streaming markdown ESM loader
and keeps its original regression assertions. A Python wrapper gates it in normal pytest.
No production markdown.js change was needed.

The env-reference suite passed before edits (13 tests). Adding capture timing
support moved the snapshot tooling environment read from line 249 to 254. The
generator was rerun only after the resulting reference mismatch was proven; its
diff changes exactly that one location.

The current fetched-page contract was verified before edits: the provider-facing
schema omits top-level anyOf for provider compatibility; compact preview validation
requires url or urls. `test_fetch_requires_a_page_in_full_and_compact_contracts` and
its whole targeted module passed (10 tests).

## Validation

Tests use a fresh isolated virtual environment installed from `requirements.txt`:
`/tmp/pr40-final-validation-venv`. Dependency consistency: all 107 packages compatible.
Optional PyMuPDF and openpyxl remain absent. Their attachment skips are explicit
importorskip contracts; PyMuPDF and spreadsheet extraction extras are optional in
requirements-optional.txt. Live endpoint tests require explicit opt-in fixtures.

- Required snapshot/CSS/markdown/env/schema/attachment gates: **138 passed, 3 intentional skips**.
- Additional CSS and streaming Python gates: **11 passed**.
- Reconstructed runtime suite: **3543 passed, 32 intentional skips, 2 expected xfails**, 173 files.
- Runtime selection: changed reconciliation test modules, tests matching changed production stems, turn contract/tool/browser/runtime/markdown/CSS suites and model-tool-mode, preview recovery, form roundtrip and env-reference gates.
- Expected xfails: two pre-existing negative-web-wording cases in test_runtime_behavior_regressions.py; their markers document partially detected negative instructions on lab.
- Python compileall: 1680 tracked Python files passed.
- Node syntax checks: 279 tracked .js files and 82 tracked .mjs files passed.
- git diff --check passed; conflict-marker scan found no tracked files with conflict markers.

## Every executable tests .mjs gate

Invoked with `node --experimental-vm-modules`; browser fixtures route locally or
use isolated DOMs. The live email UI fixture was not supplied.

| File | Result |
|---|---|
| `tests/backgroundToolJobs.test.mjs` | PASS |
| `tests/chatEditorProgress.test.mjs` | PASS |
| `tests/chatImageDeletion.test.mjs` | PASS |
| `tests/chatProcessingHandoff.test.mjs` | PASS |
| `tests/documentSelectionCaret.mjs` | PASS |
| `tests/editor-ai-cancel.mjs` | PASS |
| `tests/editor-layer-styles.mjs` | PASS |
| `tests/editorRichUpdate.mjs` | PASS |
| `tests/editorSuggestionApply.mjs` | PASS |
| `tests/editorSuggestionButtons.mjs` | PASS |
| `tests/emailReplyBrowser.test.mjs` | PASS; skipped 1 (opt-in UI fixture absent) |
| `tests/emailReplyStream.test.mjs` | PASS |
| `tests/generatedImageResult.test.mjs` | PASS |
| `tests/historyResumeRendering.test.mjs` | PASS |
| `tests/live_thinking_scheduler.test.mjs` | PASS |
| `tests/markdown_codefence_placeholder_regression.mjs` | PASS |
| `tests/noteTestOracle.test.mjs` | PASS |
| `tests/notesDraftAutosave.test.mjs` | PASS |
| `tests/researchMobileButtons.test.mjs` | PASS |
| `tests/schemaThinkingProbe.test.mjs` | PASS |
| `tests/sidebarNewChat.test.mjs` | PASS |
| `tests/skillsApproval.test.mjs` | PASS |
| `tests/toolFollowupOracle.test.mjs` | PASS |
| `tests/tool_followup_oracle.test.mjs` | PASS |
| `tests/turnRendering.test.mjs` | PASS |
| `tests/streaming/invariant.test.mjs` | PASS |
| `tests/streaming/segmenter.test.mjs` | PASS |
| `tests/helpers/test_settings_shell_coordinator.mjs` | PASS |

`tests/css_snapshot/capture.mjs` ran repeatedly with valid capture jobs through
the snapshot gate, including all 72 page/variant combinations. Other helpers
(document_source.mjs, stylesheets.mjs, streaming/corpus.mjs and markdownHarness.mjs)
are imported support modules, not standalone gates.

## Remaining limits

A new macOS capture was not available. Cross-platform normalization is backed by
exact old macOS hash recovery and controlled Linux captures, with no broad property
exclusions. Browser upgrades may introduce new serialization differences requiring
fresh investigation. Optional/live fixtures were intentionally skipped. Unused
`.session-run-state` CSS remains follow-up debt; it was not removed.

Full canonical pytest was run after every code/test edit in the fresh requirements environment.
Command: `DATABASE_URL=sqlite:///:memory: /tmp/pr40-final-validation-venv/bin/python -m pytest -q -p no:cacheprovider -rsx`.

Result: **11396 passed, 53 skipped, 2 xfailed, 185 warnings, 6 subtests passed in 451.08s (0:07:31)**.

Declared skip/xfail contracts:

```text
SKIPPED [1] tests/smoke/test_calendar_smoke.py:16: APP_PORT is not set, so there is no instance to drive. Run the suite with `scripts/odysseus-smoke`, which boots this worktree and exports it.
SKIPPED [1] tests/smoke/test_chat_smoke.py:20: APP_PORT is not set, so there is no instance to drive. Run the suite with `scripts/odysseus-smoke`, which boots this worktree and exports it.
SKIPPED [1] tests/smoke/test_chat_smoke.py:35: APP_PORT is not set, so there is no instance to drive. Run the suite with `scripts/odysseus-smoke`, which boots this worktree and exports it.
SKIPPED [1] tests/smoke/test_chat_smoke.py:59: APP_PORT is not set, so there is no instance to drive. Run the suite with `scripts/odysseus-smoke`, which boots this worktree and exports it.
SKIPPED [1] tests/smoke/test_compare_smoke.py:39: APP_PORT is not set, so there is no instance to drive. Run the suite with `scripts/odysseus-smoke`, which boots this worktree and exports it.
SKIPPED [1] tests/smoke/test_cookbook_smoke.py:18: APP_PORT is not set, so there is no instance to drive. Run the suite with `scripts/odysseus-smoke`, which boots this worktree and exports it.
SKIPPED [1] tests/smoke/test_cookbook_smoke.py:31: APP_PORT is not set, so there is no instance to drive. Run the suite with `scripts/odysseus-smoke`, which boots this worktree and exports it.
SKIPPED [1] tests/smoke/test_cookbook_smoke.py:41: APP_PORT is not set, so there is no instance to drive. Run the suite with `scripts/odysseus-smoke`, which boots this worktree and exports it.
SKIPPED [1] tests/smoke/test_documents_rag_smoke.py:31: APP_PORT is not set, so there is no instance to drive. Run the suite with `scripts/odysseus-smoke`, which boots this worktree and exports it.
SKIPPED [1] tests/smoke/test_documents_smoke.py:12: APP_PORT is not set, so there is no instance to drive. Run the suite with `scripts/odysseus-smoke`, which boots this worktree and exports it.
SKIPPED [1] tests/smoke/test_email_smoke.py:75: APP_PORT is not set, so there is no instance to drive. Run the suite with `scripts/odysseus-smoke`, which boots this worktree and exports it.
SKIPPED [1] tests/smoke/test_memory_smoke.py:19: APP_PORT is not set, so there is no instance to drive. Run the suite with `scripts/odysseus-smoke`, which boots this worktree and exports it.
SKIPPED [1] tests/smoke/test_notes_smoke.py:11: APP_PORT is not set, so there is no instance to drive. Run the suite with `scripts/odysseus-smoke`, which boots this worktree and exports it.
SKIPPED [1] tests/smoke/test_settings_smoke.py:16: APP_PORT is not set, so there is no instance to drive. Run the suite with `scripts/odysseus-smoke`, which boots this worktree and exports it.
SKIPPED [1] tests/smoke/test_tasks_smoke.py:16: APP_PORT is not set, so there is no instance to drive. Run the suite with `scripts/odysseus-smoke`, which boots this worktree and exports it.
SKIPPED [1] tests/smoke/test_uploads_smoke.py:11: APP_PORT is not set, so there is no instance to drive. Run the suite with `scripts/odysseus-smoke`, which boots this worktree and exports it.
SKIPPED [1] tests/test_ajax_email_live.py:21: opt-in live Ajax endpoint
SKIPPED [5] tests/test_ajax_email_live.py:55: opt-in live Ajax endpoint
SKIPPED [12] tests/test_ajax_email_live.py:74: opt-in live Ajax endpoint
SKIPPED [2] tests/test_ajax_email_live.py:141: opt-in live Ajax endpoint
SKIPPED [8] tests/test_ajax_email_live.py:175: opt-in live Ajax endpoint
SKIPPED [1] tests/test_cookbook_helpers.py:875: Windows Ollama CLI startup guard
SKIPPED [1] tests/test_email_attachment_text.py:19: could not import 'fitz': No module named 'fitz'
SKIPPED [1] tests/test_email_attachment_text.py:41: could not import 'openpyxl': No module named 'openpyxl'
SKIPPED [1] tests/test_email_attachment_text.py:110: Opt-in Ajax fixture test
SKIPPED [1] tests/test_inspect_media_tool.py:574: needs an ffmpeg built without webp
SKIPPED [1] tests/test_inspect_media_tool.py:934: rsvg-convert required
SKIPPED [1] tests/test_markitdown_runtime.py:64: could not import 'markitdown': No module named 'markitdown'
SKIPPED [1] tests/test_result_reference_followup.py:96: Opt-in live Ajax replay
SKIPPED [1] tests/test_upload_content_detection_magic.py:41: libmagic/python-magic not installed in this environment
XFAIL tests/test_runtime_behavior_regressions.py::test_negative_web_wording_withholds_the_web_tools_unhandled[Summarise what you already know. Do not search the web.] - negative web wording is only partially detected on lab@c499c01b; these phrasings still get the web tools offered
XFAIL tests/test_runtime_behavior_regressions.py::test_negative_web_wording_withholds_the_web_tools_unhandled[No web search please, just tell me what you know about Python decorators.] - negative web wording is only partially detected on lab@c499c01b; these phrasings still get the web tools offered
```

Final commit SHA and clean status are reported in the session completion message.
