# PR 910 Modal Position Test Matrix

Manual verification for persisted modal positions, using the Tasks modal as the representative dockable dynamic modal.

Tested locally on June 2, 2026 with:

- `AUTH_ENABLED=false LOCALHOST_BYPASS=true .venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 7010`
- Chromium against `http://127.0.0.1:7010/`
- Viewports: desktop `1365x900`, resized desktop `800x700`, mobile `390x844`

| Case | Steps | Result | Evidence |
| --- | --- | --- | --- |
| Regular modal reopen | Open Tasks, drag the title bar to `left=722.5 top=381.75`, close, reopen. | Pass. Reopened modal restored to the saved position. | [regular-modal-reopen.png](assets/pr-910/regular-modal-reopen.png) |
| Docked right modal | Save a normal windowed position, reopen Tasks, apply right dock state. | Pass. Docked layout cleared inline `left/top` position styles and used the dock layout. | [right-docked-modal.png](assets/pr-910/right-docked-modal.png) |
| Docked left modal | From the same saved position, switch Tasks into left dock state. | Pass. Left dock also cleared inline `left/top` position styles and used the dock layout. | [left-docked-modal.png](assets/pr-910/left-docked-modal.png) |
| Mobile bottom sheet | Save a desktop position, switch to `390x844`, open Tasks. | Pass. Mobile ignored the saved desktop coordinates and rendered as a full-width bottom sheet. | [mobile-bottom-sheet.png](assets/pr-910/mobile-bottom-sheet.png) |
| Window resize after saved position | Save a desktop position, close Tasks, resize viewport to `800x700`, reopen. | Pass. The restored position was clamped to the minimum-visible policy so the window remained recoverable. | [resized-window-clamp.png](assets/pr-910/resized-window-clamp.png) |
| Off-screen saved position recovery | Write `left=5000 top=4000` to saved modal storage, then open Tasks at `900x700`. | Pass. The modal was clamped to the minimum-visible policy instead of being lost off-screen. | [offscreen-position-recovery.png](assets/pr-910/offscreen-position-recovery.png) |

Notes:

- Persisted coordinates are applied only for normal desktop windowed modals.
- Docked, tiled, fullscreen, and mobile states reset inline drag coordinates so saved positions do not override those layouts.
- Invalid saved coordinates are removed; valid but off-screen coordinates are clamped when restored.
