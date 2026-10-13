# Known full-suite failures

`python -m pytest -q` does not come back clean on every machine, and it never
has. Without a list of which failures are expected, a first local run is
uninterpretable: you cannot tell "you broke something" from "you are on a Mac",
so the usual result is either chasing a non-bug or ignoring a real one.

This is that list. It is a record of observation, not a permission slip: a test
here is still a test that does not pass, and the three that remain below are
all still worth someone's time.

Last measured: `lab @ c499c01b` plus the fixes in this change, macOS 15 on
Apple Silicon, Python 3.11, with the **default** `$TMPDIR` — see the socket
entry below for why that qualifier is load-bearing.

```
3 failed, 10658 passed, 6 skipped
```

## Get the prerequisites right first

Most "surprise" failures are a missing dependency rather than anything in this
file. A clean run needs all of:

```bash
python3.11 -m venv venv
./venv/bin/python -m pip install -r requirements.txt
npm ci                              # the browser tests shell out to node
npx playwright install chromium     # ~30 tests drive a real browser
mkdir -p data                       # SQLite lives at ./data/app.db
```

plus `ffmpeg` on `PATH` for the media tests.

If you already have a ChromaDB running, point `CHROMADB_PORT` at a closed port
for the run. The client reaches Chroma over HTTP regardless of the data
directory, so a test run will otherwise attach to whatever store is listening,
including one holding real data.

Miss `npm ci` and roughly 36 browser tests fail on `Cannot find package
'playwright'`. That is not a regression, it is the missing install.

## The three that remain, and the seven that no longer do

### Test bugs: comparing an unresolved path against a resolved one

Three failures compared an unresolved `/tmp` path against a resolved
`/private/tmp` one, and are fixed rather than listed:

- `tests/test_code_nav_tools.py` (two tests) built a fixture under
  `tempfile.mkdtemp(dir="/tmp")` and compared it against the path the code
  reports, which it resolves.
- `tests/test_workspace_confine.py::test_glob_confined_e2e` mixed
  `os.path.realpath(ws)` with an unresolved secret directory, so `relpath`
  produced `../../../../tmp/<absolute path>` and the assertion that the
  absolute path was absent matched it as a substring.

Both now resolve consistently. They are recorded here because the shape recurs:
on macOS, mixing a resolved and an unresolved temp path is a test bug that
looks like a platform failure.

### Test bugs: a temp path too long to bind a socket to

Four more, same family, invisible unless `$TMPDIR` is long enough:

- `tests/test_shell_routes.py::TestHostDockerAccess` (three tests)
- `tests/test_cookbook_docker_access.py::test_container_opt_in_with_unix_socket_is_allowed`

```
OSError: AF_UNIX path too long
```

Each bound an `AF_UNIX` socket at `tmp_path / "docker.sock"`. macOS gives
`sun_path` 104 bytes including the terminator, and pytest's `tmp_path` is
rooted at `$TMPDIR`, which on a stock Mac is a 49-character
`/var/folders/<2>/<30>/T/`. Add `pytest-of-<user>/pytest-<n>/` and the test's
own name and the bind path is 115 bytes before the filename.

This is why the counts above depend on where you run from: under a shortened
`$TMPDIR` the path lands at 103 and the tests pass, and it tips over the moment
pytest's run counter reaches two digits. Linux allows 108 bytes and roots
`$TMPDIR` at `/tmp`, so it never bites there and CI stays green.

They now bind through `tests/helpers/unix_sockets.bound_unix_socket`, which
puts the socket under a short directory. **Measure with the default `$TMPDIR`**
— `env -u TMPDIR` or an explicit `/var/folders/...` — or this whole file
records a run nobody else has.

### Optional dependency: ffmpeg without a WebP encoder

- `tests/test_inspect_media_tool.py::test_inspect_media_exports_final_decodable_frame_at_exact_duration`

```
ffmpeg still extraction failed: Automatic encoder selection failed ...
Error opening output files: Encoder not found
```

The test asks ffmpeg for a `.webp` still and asserts `exit_code == 0`. WebP
encoding is a build option, and Homebrew's ffmpeg does not always carry it. CI
installs a build that does, which is why this is green there.

**Needs a decision**: skip when the encoder is absent, or fall back to PNG. The
current shape asserts success from a codec that is not guaranteed present.

### Environmental: real sockets

- `tests/test_integration_api_call_ssrf.py::test_real_socket_falls_back_from_dead_first_to_live_second`

```
httpcore.ConnectTimeout / httpx.ConnectTimeout
```

Opens real sockets and depends on a connection to a dead address being refused
quickly rather than hanging. Sandboxed and restricted-network machines time out
instead. Genuinely environmental.

### Unexplained: rich-text colour contrast

- `tests/test_document_rich_color_reset_and_contrast.py::test_rich_colors_follow_theme_and_undo_as_one_edit`

A Playwright run times out waiting for `#doc-email-richbody p` to contain a
`span` after a colour is applied.

**This one is not flaky.** Three consecutive runs failed identically, each at
about 31 seconds. It was previously written off as timing noise and that was
wrong. The cause is not established, and until it is, treat it as a possible
real defect in the rich-text colour path rather than a platform artifact.

## Keeping this current

Re-measure on a clean checkout of `lab` with the prerequisites above, and
update the header revision, the counts and any entry that changed. A failure
that appears and is not listed here is a regression until shown otherwise.
