# Release smoke suite

One command that boots this worktree and drives every advertised feature
area once, end to end, against a real instance.

```bash
scripts/odysseus-smoke            # boot, run every area, stop again
scripts/odysseus-smoke --keep-up  # leave the instance running afterwards
scripts/odysseus-smoke --no-boot  # drive whatever is already up here
scripts/odysseus-smoke --areas    # print the coverage table without booting
scripts/odysseus-smoke -- -k notes
```

## Why it exists

The decomposition work had two safety nets and neither covered the
product. The checkpoint benchmark measures the agent runtime. The
computed-style snapshot in `tests/test_css_computed_style_snapshot.py`
pins the rendered CSS. Nothing checked that Notes, Calendar, Documents,
Email, Memory, Cookbook or Settings still worked after a route package
moved or a 17,000-line module was split, and the unit suite does not:
`StressTestor`'s review of #5898 is the worked proof that a
byte-identical file-for-file move can break eleven tests that pass on
the base branch, with CI green throughout.

## Where it lives and why

pytest, not Playwright. Both are in the repo, so this adds no third
harness, and the choice went to pytest because every scenario here is a
request/response round trip rather than a rendering assertion -
rendering is already covered by the computed-style snapshot, and the
28 Playwright specs under `tests/e2e/photo-editor/` are the one area
with browser coverage. A browser would have added flake and start-up
cost for no extra signal.

It owns no instance logic. `scripts/odysseus-dev` already derives ports
per worktree, keeps the data dir and ChromaDB out of `data/`, and waits
on `/api/ready` rather than a TCP accept, so `scripts/odysseus-smoke`
boots through it and only adds the scenarios and the report.

## The contract with the runner

Four environment values, which are what `odysseus dev env` prints plus
the dev admin account:

| Variable | Read through | Used for |
|---|---|---|
| `APP_PORT` | `src.constants.internal_api_base()` | which instance to drive |
| `ODYSSEUS_ADMIN_USER` | - | who to authenticate as |
| `ODYSSEUS_ADMIN_PASSWORD` | - | " |
| `ODYSSEUS_DATA_DIR` | `src.constants.DATA_DIR` | where the email fixture file goes |

Run under a plain `pytest` with none of them set, every scenario skips
with the reason and the full suite stays green. `APP_PORT` pointing at
one of `odysseus dev`'s reserved ports - a normal launch of this
checkout, the machine's own instance - is refused rather than driven,
because the scenarios create and delete real records.

## The deterministic provider

`stub_provider.py` is an OpenAI-compatible server on an ephemeral
loopback port: `GET /v1/models` and `POST /v1/chat/completions`, both
buffered and streamed. No scenario touches a live model endpoint or the
network. It serves two model ids so the Compare area has something to
reveal, and it records every request so a scenario can assert the user's
message actually reached the provider rather than only that some text
came back.

Email uses the repo's own deterministic path rather than a second
mechanism: `routes/email_routes.py` serves a fixture inbox when
`ODYSSEUS_EMAIL_FIXTURE=1` and a fixture file is in the data dir. The
suite writes the file and restores whatever was there; the flag is read
inside the app's process, which is why the runner owns the boot.

## What is covered

One scenario per area, each asserting a user-visible outcome rather than
a status code. `scripts/odysseus-smoke --areas` prints the current list.

| Area | What it asserts |
|---|---|
| Chat | a turn against the stub comes back rendered, on both the buffered and the streamed path, and is in the session history |
| Compare | a blind comparison streams both sides and the vote reveals which model produced which reply |
| Notes | a note is listed, read back, edited, and 404s after delete |
| Calendar | an event appears in the window the UI queries and is gone after delete |
| Tasks | a daily task is accepted with a computed next run, is listed, and pauses |
| Documents (editor) | an edit adds a version, both versions read back, and a restore returns the first |
| Documents (RAG) | an uploaded file is chunked, indexed and listed |
| Email | the fixture inbox lists, opens with its body, and the unread count drops on mark-read |
| Memory | a fact is listed, found by search, and gone after delete |
| Uploads | an attachment reads back byte for byte |
| Cookbook | hardware is detected; a fresh install's recommendation request returns the explicit empty-catalog Rescan guidance; state persists |
| Settings | a preference written on one session is still there after a new login |

## What is not covered, and why

Printed next to the results on every run, so a reader cannot mistake the
table for coverage of everything it does not mention. `DECLARED_GAPS` in
`areas.py` is the list; the short version:

- **Deep Research** and **Web Search** need live egress. A deterministic
  stub for the crawler would be an application change, which this is
  not.
- **Email over IMAP/SMTP** is covered only as far as the fixture path
  goes. There is no local mail server, so real account sync and send are
  untested.
- **Cookbook download and serve** needs tmux, a GPU runtime and a
  multi-gigabyte download.
- **Gallery and the photo editor** already have the repo's only
  Playwright specs.
- **The agent tool loop** is what the checkpoint benchmark measures.
- **MCP servers** are stdio subprocesses outside the app's readiness
  contract.
- **Rendering and layout** are pinned by the computed-style snapshot.

`Documents (RAG)` is the one covered area that can report `SKIP` on a
clean checkout: `requirements.txt` pins `chromadb-client`, the HTTP
client, and the ChromaDB *server* is a separate install. Without one
reachable, the upload route returns a deliberate 503 and the row reads
`SKIP` with that reason. Install `chromadb` in the venv and it goes
green.

## Reading the report

The table has one row per area in `areas.COVERED`, built from what
pytest reported rather than from anything a scenario asserts about
itself. An area whose module never ran shows as `NOT RUN`, so deleting
or renaming a file cannot make a row disappear -
`tests/test_smoke_area_table.py` pins that, and that a module on disk
must be registered.

## What a run leaves behind

Every scenario deletes what it created, with two exceptions on the
scratch instance: the preference key `odysseus_smoke_preference`, which
has no delete route, and the uploaded attachment, which the app's own
upload cleanup owns. Both live in `.odysseus-dev/data/`, never in
`data/`.
