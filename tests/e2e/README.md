# Browser End-to-End Tests

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
