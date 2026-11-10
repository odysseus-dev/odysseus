# Browser End-to-End Tests

## Photo Editor Release Gate

The photo editor release gate launches an isolated Odysseus server on port
`7013` with a temporary SQLite database and disposable authenticated account.

```bash
npm install
npm run test:photo-editor:install
npm run test:photo-editor
```

Set `PHOTO_EDITOR_E2E_PORT` to use another port. Failure screenshots and traces
are written to `test-results/photo-editor/`. The server uses the repository
`.venv` automatically; set `ODYSSEUS_TEST_PYTHON` to another Python executable
when using a different environment layout.

The disposable account is created in the temporary data directory and exercises
owner-scoped draft and notification routes without using an existing account.
`PHOTO_EDITOR_E2E_AUTH=0` selects the legacy unauthenticated configuration for
investigating it; offline-upgrade coverage requires the authenticated mode.

## Mobile and iOS Layout Suite

The dedicated mobile and iOS layout suite exercises viewport behavior,
accessibility zoom, scrolling, mobile safe-area geometry (Notes and Documents
panes), and floating window restoration against an isolated local Odysseus server
running on port `17739`.

```bash
npm install
npm run test:ios-layout:chromium
```

### Profiles and Browser Prerequisites

- **Chromium profiles** (`desktop-chromium`, `phone-chromium-portrait`, `phone-chromium-landscape`, `tablet-chromium`):
  Provide automated baseline coverage in CI and local development without requiring
  WebKit dependencies.
- **WebKit profiles** (`phone-webkit-portrait`, `phone-webkit-landscape`, `tablet-webkit`):
  Used for platform-oriented WebKit rendering validation. WebKit requires browser binaries:

```bash
npm run test:ios-layout:install
# or: npx playwright install webkit
npm run test:ios-layout:webkit
```

To run all installed profiles:

```bash
npm run test:ios-layout
```

### Evidence and Cleanup

- **Evidence artifacts**: Screenshots are written to `test-results/ios-layout/` by
  default and traces are written to `test-results/ios-layout-traces/`. Both paths
  are git-ignored. Set `ODYSSEUS_LAYOUT_EVIDENCE_DIR` to override the screenshot
  output path.
- **Teardown**: Disposable application state and database files created in the
  OS temporary directory are automatically cleaned up on both normal and failure
  teardown, preserving diagnostic screenshots and failure traces.

### Platform Considerations

Emulated Chromium and WebKit viewports validate deterministic CSS cascade,
computed layout geometry, viewport meta tags, and JavaScript restoration. However,
browser emulation is not a physical device substitute: physical iOS Safari and
standalone installed PWAs should still be verified separately for native gesture
pinch-zoom, software keyboard dismissal transitions, and dynamic hardware notch/island insets.
