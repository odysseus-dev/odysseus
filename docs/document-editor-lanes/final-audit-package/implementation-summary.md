# Implementation summary

## Objective and final shape

The original Document Editor monolith at `static/js/document.js` was decomposed into explicit application, domain, and UI modules while preserving its public facade and browser behavior. Lane A, Lane B, Lane C, and the integration-owned composition root together account for all 565 baseline items. Seven modules from earlier work remain recognized as pre-existing modules by Appendix A.

The root imports feature modules, binds their command interfaces, preserves initialization ordering, and publishes `documentModule`. Business logic for status, AI streams, history, panel lifecycle, headers, composer wiring, source editor binding, and other assigned functions lives in the maintained modules under `frontend/src/features/documents/`. Published copies live under `static/js/document/` and are registered in `frontend/modules.json` and the service worker.

The 565-item allocation is: 289 items integrated before these lane checkpoints, 43 Lane A items, 76 Lane B items, 149 Lane C items, and 8 composition-root items retained by the integrator.

## Module responsibilities

- Application modules own document state, persistence, session transitions, and commands.
- Domain modules own language/document kinds, email message parsing, rich-text rules, and other pure transformations.
- Lane A owns document status rendering, AI update application, live stream lifecycle, and version history.
- Lane B owns UI operations assigned by Appendix A, including rich editor, selection, toolbar, tabs, and lifecycle operations recorded in its handoff.
- Lane C owns pane template/layout/presentation/lifecycle, header actions and language picker, email composer wiring, source-editor binding, and feature module wiring.
- The composition root owns initialization, cross-module command wiring, the `documentModule` facade, and the eight integration-assigned baseline items.

Exact module paths and declaration assignments are in `accounting/appendix-A.json` and the lane manifests.

## State ownership and lifecycle

Mutable document/session state is owned by `application/documentState`. Persistence and command interfaces own serialized saves and navigation. Editor timers are scheduled and cancelled by `sourceEditor`; selection observers are created and disposed by `selectionHighlights`; PDF disposal is owned by the module-wiring boundary. The root no longer assigns imported source-editor timer bindings. Lane C panel lifecycle calls explicit wiring and disposal functions, retaining idempotent UI wiring guards and cleanup order.

AI stream application preserves active-document checks and cancellation behavior. Version restoration flows through the existing document commands and persistence path. Lane C's two `openPanel()` statement extractions are recorded as named wrappers in its removal manifest and independently covered by lifecycle regression tests.

## Compatibility strategy

The default `documentModule` continues to publish 39 properties. The facade retains 30 named exports. Existing host callbacks required by consumers remain compatibility boundaries; obsolete recursive `generateEmailReply` and global overflow callback bridges were removed in favor of direct module-owned interfaces. The root keeps window publication of the documented facade.

## Important decisions

- Timer mutations moved to source-editor-owned scheduling/disposal functions instead of mutating imported bindings.
- Selection observer cleanup is exposed by the module that owns the observer.
- The AI reply entry point is exported from `aiReply` and directly forwarded by the facade.
- Rich-text dropdown activation tokens are issued by `formatMenus`, the owner of activation serial state.
- The test-only Node email host loader maps browser host imports to call-time test doubles without changing production host behavior.
- Frontend maintained and published module trees are kept in parity and service-worker cache metadata is updated.
