# Independent audit brief for Codex

Perform a separate runtime and correctness review at the final integration HEAD named in `../git-evidence.md`. Inspect the source and full diff against the stated baseline. Treat the implementation team's validation as evidence to verify, not as independent approval. Do not edit files during this audit.

Focus on:

1. Persistence behavior: autosave, manual save, failed save/retry, pending write serialization, closing inactive documents, document/session switching, deletion rollback, dirty-state restoration, and PDF drafts.
2. Async races and cancellation: AI replies and live streams during document switches, pending save teardown, aborted panel loads, stale callbacks, timer cleanup, and observer disposal.
3. Navigation identity and lifecycle: repeated open/close, minimize/restore, session navigation, bottom-dock restoration, focus and Escape layering, keyboard shortcuts, and listener duplication.
4. Browser interactions: rich and source editors, selection overlays, toolbar and tab menus, Markdown images, email composer/send, and PDF operations.
5. Security boundaries and publication consistency: validate the existing security regressions, imported host interfaces, published module graph, and cache registration.
6. Regression quality: determine whether coverage exercises observable behavior and identify affected paths that remain untested.

Use existing tests and browser infrastructure; do not infer real browser validation from mocks. Report findings with reproduction or source/test evidence and classify confirmed defects, residual risks, and untested behaviors separately.
