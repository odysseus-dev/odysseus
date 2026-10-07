# Browser End-to-End Tests

The photo editor release gate launches an isolated, authentication-disabled
Odysseus server on port `7013` with a temporary SQLite database.

```bash
npm install
npm run test:photo-editor:install
npm run test:photo-editor
```

Set `PHOTO_EDITOR_E2E_PORT` to use another port. Failure screenshots and traces
are written to `test-results/photo-editor/`. The server uses the repository
`.venv` automatically; set `ODYSSEUS_TEST_PYTHON` to another Python executable
when using a different environment layout.
