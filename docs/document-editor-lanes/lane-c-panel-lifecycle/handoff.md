# Lane C panel lifecycle handoff

## Scope and implementation

Lane C owns 149 baseline items: 26 declarations, 3 top-level statements, and
120 `openPanel()` statements. The integration extracts them into:

- `ui/panel/paneTemplate.js`
- `ui/panel/paneLayout.js`
- `ui/panel/documentPresenter.js`
- `ui/panel/panelLifecycle.js`
- `ui/header/headerActions.js`
- `ui/header/languagePicker.js`
- `ui/email/composerWiring.js`
- `ui/editor/sourceEditorBinding.js`
- `ui/moduleWiring.js`

Source editor debounce scheduling and selection observer cleanup remain owned
by their respective source editor and selection modules. `panelLifecycle` now
orchestrates pane creation, component wiring, repeated open/close behavior,
minimize and restore, Escape layering, and teardown.

## Integration evidence

- Extraction verifier: 149/149 Lane C items, clean removal simulation.
- Full extraction ledger: 565/565 accounted for, zero missing or duplicate
  items.
- Import graph: 55 modules, zero cycles.
- State ownership: 48 files checked, zero violations.
- Frontend architecture checker: passed.
- Focused browser lifecycle and rich editor regressions are recorded in the
  final integration audit package.

The current integration checkpoint is recorded in
[`../integration-ledger.md`](../integration-ledger.md). The full validation
results and exact commands are in the final audit package.

## Development worktree note

The Lane C development worktree contains the shared pre-integration extraction
overlay used to build the authoritative integration. The integration branch is
the source of truth for the final combined result; do not cherry-pick the
development overlay wholesale over Lanes A or B.
