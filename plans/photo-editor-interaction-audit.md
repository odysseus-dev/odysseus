# Editor interaction audit

Date: 2026-09-16
Scope: make existing editing operations predictable and familiar. No additional tools.
Evidence: code inspection plus a focused browser regression for rasterization.
This is not a claim that every workflow has been manually verified.

## Implementation progress

The full audit remains open. Changes made on 2026-09-16:

- Removed destructive single-letter lasso shortcuts and made command dispatch
  return after handling undo, duplicate, save, transform and related actions.
- Native fields and contenteditable targets now own keyboard editing. Keyboard
  and paste bindings are replaced on editor rebuild rather than accumulating.
- M selects Marquee, S selects Clone, Ctrl/Cmd+D deselects, Ctrl/Cmd+A selects
  all, and Ctrl/Cmd+J copies the selection when one exists. Legacy deselect and
  select-all chords remain aliases. Tool keys now have a shared map.
- Shift+Alt chooses intersection consistently for marquee, lasso and wand.
- Cut no longer creates an extra visible layer. Lasso copy retains selection
  and returns immediately rather than also copying the whole layer.
- Pixel fill, selection erase, destructive blur and edge processing now await
  the rasterization confirmation. Edge cancellation no longer reports success.
- Quick Mask painting bypasses the parent-layer rasterization prompt.

Verified so far: 16 focused Python/JS tests passed; browser checks have verified
field focus, selection copy, shortcut mappings, intersection and editor reopening.
The browser suite stubs the unrelated notification-log endpoint because that
endpoint returns 401 without an account and triggers page navigation on the
isolated test server. Editor operations use the real application.

Still required: full dialog/shortcut ownership, active mask consistency across
fill/erase/filter, target visibility/lock feedback, gesture transitions, stable
controls, broader cross-browser/mobile tests, and the 4K/20-edit recovery gate.

Second implementation pass:

- Added a shared pixel-target resolver for selection erase, fill and destructive
  blur: selected layer/group masks are edited directly, including local offsets.
  Parent pixel/transparency locks no longer incorrectly block mask operations;
  owner/group locks still apply.
- Restored the existing Fill command in the Image menu; it had a handler but
  no menu entry. With no selection it fills the selected surface.
- Legacy lasso erase now uses the same document-space selection-delete path.
- Tool switching ends an active brush stroke before changing its tool identity.
  Desktop reselect keeps controls open; the mobile sheet toggle is preserved.
- Chromium verified offset-mask fill/delete preserve parent pixels. Firefox
  verified rasterize/cancel/undo, focus ownership, selection-copy pixels, cut,
  intersection, reopening and mask editing. The focused Python/JS suite now
  passes 20 tests. Firefox also passed the held-brush tool-switch test: one
  history entry, no lingering stroke, undo restores pixels, controls stay open.

Still open: copy/clipboard and edge-filter mask targeting, visibility feedback,
full dialog precedence, layer-switch/focus-loss gesture lifecycle, mobile panel
stability, and the 4K/20-edit recovery gate. These are not covered by the focused
passing tests above.

## 1. Command and keyboard ownership (highest priority)

Third implementation pass:

- Copy/cut and duplicate-selection share selected-surface extraction. Selected
  masks copy their own pixels, not their parent's image. Internal paste retains
  the source document offset and selects Move through the normal toolbar path.
- Canvas window handlers are replaced on editor rebuild. Focus loss releases
  drawing/pan gestures and temporary Space-pan state, preventing a returning
  pointer from extending a stale stroke.
- Verified seven interaction workflows in Chromium and eight in Firefox
  (including rasterize confirmation), plus 20 focused Python/JS tests. The
  offset-mask case verifies white mask pixels, the preserved paste offset and
  undo. The focus-loss case verifies one undo entry and no continued painting.
- Still open: full dialog precedence, layer-switch gesture lifecycle, edge-filter
  mask targeting, visibility feedback, stale asynchronous previews, mobile panel
  stability, and the 4K/20-edit persistence and export verification.

The findings below describe the initial audit; progress above records resolved
parts without removing the remaining acceptance criteria.

Fourth implementation pass:

- Filter dialogs own keyboard input ahead of the editor and surrounding app.
  Escape cancels, Enter applies (or activates focused Cancel), and Tab stays in
  the dialog. Destructive blur cancellation no longer pops unrelated history
  or clears redo: the history snapshot is taken only on acceptance.
- Filter prompts reject a changed document/target and cancel on editor close
  or reopen. Preview rollback on close is synchronous. Broader asynchronous
  preview/persistence interaction still requires verification.
- Layer thumbnails refresh after settled composites without rebuilding the
  panel. Changed layer rows briefly flash using the theme highlight; unchanged
  rows do not. Preview signatures reset between editor documents.
- Chromium: nine interaction workflows passed, including pixel-verified
  thumbnail refresh, the edited-row flash, and filter Escape/redo preservation.
- Clarified toolbar feedback: the top bar must stay on one row. Removed the
  forced second row; narrow windows scroll horizontally. Dropdown popovers
  escape that scroll clip without moving their DOM/event ownership. Chromium
  verifies one-row alignment and menu actions at 1280, 900, 600 and 390px.

`static/js/editor/keyboard-shortcuts.js` handles Space, arrow keys, transforms,
undo and clipboard before its general typing-target guard. Several commands can
therefore reach editor state while a field or text editor owns focus. Lasso
shortcuts run after tool switching: C can select Crop and copy a selection;
D can select Burn and delete selected pixels. These need one dispatch decision.
`galleryEditor.js` additionally handles Escape at window capture, document
capture and through a gallery callback. The rasterize browser test exposed
Escape escaping the new confirmation and discarding the editor state.

Work: define precedence as dialog, text/field editing, active gesture, canvas
command, surrounding application. Consume each command once. Keep native text
undo/cut/copy while typing. Centralize command labels and shortcut hints.

Shortcut mismatches in `editor/build/toolbar.js`: M selects Inpaint, R selects
Marquee, S selects AI Sharpen, K selects Clone, and D selects Burn. The existing
Deselect chord is Ctrl/Cmd+Shift+D. Adobe documents M for Marquee, S for Clone
and Ctrl/Cmd+D for Deselect. Browser-reserved chords such as Ctrl+T require an
explicit browser-compatible alternative, with matching UI hints.

Reference: https://helpx.adobe.com/photoshop/web/get-set-up/preferences-and-settings/keyboard-shortcuts.html

Acceptance: keyboard-only text editing, dialog cancellation, selection editing
and tool changes never invoke two commands or change an unrelated layer.

## 2. Layer target and rasterization

Before this patch, `_beginDraw` and paint handlers displayed rasterize toasts;
the actual conversion controls lived elsewhere. Text, shape and placed layers
had different paths. The new confirmation supports selecting/reselecting a
pixel tool or trying it on canvas, Enter, Cancel, and undo. Mask targets bypass
conversion. Do not replay a pointer stroke after a modal closes.

Remaining work: use the same permission/target decision for fill, selection
erase and destructive filters (`_canMutateLayerPixels` still only toasts).
Distinguish locked pixels, locked transparency, hidden layers and adjustment
layers with a concrete reason and relevant action. Make the active pixel/mask/
group target unmistakable in the layer panel and controls.

Acceptance: brush, erase, fill and filters agree on the active target; cancellation
changes nothing; undo restores retained text/shape/placed content.

## 3. Selection behavior

Selection state still crosses `wandMask`, lasso points and selection-space
conversion. Marquee already supports add/subtract and moving a boundary, so
preserve that implementation and reconcile other entry points with it.

Work: one consistent replace/add/subtract/intersect contract, clear distinction
between moving a boundary and moving selected pixels, consistent copy/cut/fill/
delete on offset layers and masks. Remove legacy single-letter destructive
lasso commands that collide with tools. Audit Ctrl/Cmd+J with an active selection:
the current dispatch always calls duplicateActiveLayer before selection handling.

Acceptance: the same selected region produces the same edited pixels across
marquee, lasso and wand, including zoomed and offset layers; undo restores both.

## 4. Gesture completion and tool switching

`onSelectTool` cancels crop, marquee and gradient work but commits transform
and text work. Reselecting a tool toggles its controls sheet. These policies are
distributed rather than expressed as one transition contract.

Work: specify commit/cancel for each pending operation, Enter/Escape, switching
tools, switching layers, losing focus and pointer cancellation. Keep temporary
pan distinct from changing tools. Preserve the existing direct-manipulation
and transform geometry modules; consolidate their lifecycle callers.

Acceptance: one drag produces one undo step; Escape restores the pre-drag
result; a released pointer outside the canvas cannot leave an operation active.

## 5. Contextual controls and visual feedback

`onSelectTool` individually shows/hides many control sections. Layer-type
controls, effects popups and mobile sheets need a consistent target and focus
contract. Keep the canvas position stable when these surfaces open.

Work: align control placement, selected states, disabled reasons, cursor/brush
preview and focus restoration. Preserve settings for each existing tool where
appropriate. Review repeated-tool clicks on desktop versus mobile, where they
currently also dismiss the controls sheet.

Acceptance: selecting a tool exposes its relevant controls without moving the
artwork; opening and dismissing a popup returns to the same target and viewport.

## 6. Responsiveness, undo and recovery

There are already worker rendering, history budget, persistence and cancellation
modules. Assess their observable behavior before proposing a replacement.

Work: measure stroke latency, preview latency and history cost on a 4K document
with multiple layers. Exercise 20 mixed operations, repeated undo/redo, save,
reopen and export. Check stale asynchronous previews after switching layers or
closing the document. Saved status must correspond to completed persistence.

Acceptance: no lost edits, stale previews or export/reopen differences in the
tested workflow. Record timings and browser/device rather than an arbitrary
percentage of Photoshop parity.

## Delivery order

1. Rasterization confirmation and focused regression (this change).
2. Command ownership and conflicting shortcuts.
3. Selection and active-target consistency.
4. Gesture commit/cancel and history consistency.
5. Controls, cursor feedback and stable panels.
6. Cross-browser desktop/mobile workflow and performance verification.

Existing browser tests under `tests/e2e/photo-editor/` cover useful building
blocks. Extend them with real sequences across tools; avoid testing each tool
only in isolation. Full Photoshop parity, new filters and new file formats are
outside this audit's scope.
