# Notes regression specs

Two Playwright specs.
Every test asserts the expected behaviour, so it is red while its bug is
there. They are not skipped or marked as expected failures: each fix turns
its test green.

- `notes-qa-regressions.spec.mjs`: one test per confirmed bug from review
  pass.
- `notes-edge-cases.spec.mjs`: exploratory edge cases (Backspace after a code
  block, stale ticks, paste cost, offline retry, phone landscape, ...).
- `bad_model.py`: an OpenAI-compatible endpoint that fails (`/fail/v1` 500,
  `/slow/v1` hangs, `/garbage/v1` non-JSON), for the failing-model test.

## Run

```bash
npm ci
npx playwright install chromium            # add webkit for NOTES_QA_BROWSERS=webkit
venv/bin/python tests/e2e/notes-qa/run_notes_qa.py
```

`run_notes_qa.py` boots a disposable instance on `127.0.0.1:7861` (its own
data dir and SQLite file, `AUTH_ENABLED=true`, ChromaDB on a dead port), creates
an admin `alice` and a non-admin `bob`, gives alice the repo's stub model
(`tests/browser/openai_stub.py`), fills in both specs' environment variables
and runs `npx playwright test`. It stops the instance at the end; `--keep`
leaves it up and prints the PIDs. Port 7860 is refused. Set
`NOTES_QA_PASSWORD` to give both accounts a password you can log in with
afterwards; otherwise they are random and never written anywhere.

Extra arguments go to Playwright:

```bash
venv/bin/python tests/e2e/notes-qa/run_notes_qa.py -g BLOCKER
venv/bin/python tests/e2e/notes-qa/run_notes_qa.py notes-edge-cases -g "Backspace"
NOTES_QA_BROWSERS=chromium,webkit venv/bin/python tests/e2e/notes-qa/run_notes_qa.py
```

Against an instance that is already up, set the variables yourself:

```bash
NOTES_QA_ORIGIN=http://127.0.0.1:7861 NOTES_QA_OWNER=alice NOTES_QA_OWNER_PW=... \
NOTES_QA_COLLAB=bob NOTES_QA_COLLAB_PW=... \
NOTES_ORIGIN=http://127.0.0.1:7861 NOTES_USER=alice NOTES_PASS=... NOTES_USER2=bob NOTES_PASS2=... \
npx playwright test -c tests/e2e/notes-qa/playwright.config.js
```

`NOTES_QA_*` feeds the QA spec, `NOTES_*` the edge-case spec. The owner must
be an admin; `NOTES_USER` must also have a working model endpoint.

Results and traces go to `$TMPDIR/odysseus-notes-qa-results` (override with
`NOTES_QA_OUTPUT_DIR`), not into the tree.

## Tests that need more setup and skip otherwise

- `P1 a note run whose model fails says so`: `NOTES_FAILING_MODEL=1`, and the
  admin's default endpoint pointed at `bad_model.py`
  (`python tests/e2e/notes-qa/bad_model.py 7873`, then
  `http://127.0.0.1:7873/fail/v1` as the endpoint).
- `P2 an iPhone HEIC photo is visible in Chromium`: `NOTES_HEIC=<path to a real .heic>`.
- `P2 a note with one 300 KB line opens in WebKit in under 3 s`: WebKit only.

`P2 (intermittent) rapid taps ...` and `P2 Ctrl+Z after Checklist ...` can
pass on a given run: the first is intermittent, the second was seen in
exploration but not reproduced by the spec.

Timing tests (`pasting 100 plain-text lines`, `one 300 KB line`) depend on
the machine. A loaded host can turn them red on its own.
