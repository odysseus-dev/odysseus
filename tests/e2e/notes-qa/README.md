# Notes regression specs

Two Playwright specs for the note window. Every test asserts the expected
behaviour, so it is red while its bug is there. They are not skipped or marked
as expected failures: each fix turns its test green.

- `notes-qa-regressions.spec.mjs`: one test per confirmed bug in the note
  window (a note that fails to render or to build, a fence in a quote, a
  checklist started from the quick bar, a one-line code block on a card, phone
  layout and keyboard, links, swipe to close).
- `notes-edge-cases.spec.mjs`: edge cases (Backspace after a code block, paste
  cost, Checklist on a selection, phone landscape).

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
