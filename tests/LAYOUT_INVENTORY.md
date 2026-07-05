# Test Layout Inventory

## Purpose

Inventory for the first low-risk split of the flat `tests/` directory
(issue #3712, parent #2523). This document only records *what* should move
first and *why*; it moves nothing. The actual move is a separate, mechanical
PR that relocates the listed files verbatim and changes no test content.

The target layout and category definitions come from
[`TESTING_STANDARD.md`](./TESTING_STANDARD.md); the collection-time markers
come from [`_taxonomy.py`](./_taxonomy.py), which classifies by **filename
tokens only** (paths are ignored, except the `tests/helpers/` rule). A file
keeps its `area_*`/`sub_*` markers when moved into a subdirectory, and
`conftest.py` discovers marker names recursively (`rglob`), so a move does not
disturb marker registration or focused selection.

## Current low-risk candidate groups

Groups whose tests need no route/app setup and no real DB/session setup:

1. **CLI / script tests** (`area_cli`, 28 files) - load `scripts/` entry
   points via `tests.helpers.cli_loader.load_script`; DB access is stubbed
   with `tests.helpers.db_stubs` (`SessionLocal` is a plain stub attribute).
   No `TestClient`, no FastAPI app import, no SQLite files.
2. **Helper self-tests** (`area_helpers`) - e.g. `test_helpers_import_state.py`,
   `test_db_stubs_helper.py`. Safe but tiny (two files), and they test the
   shared helpers from the #3685 audit (merged) that the rest of the suite
   depends on; little payoff as a first slice.
3. **Pure unit / parsing tests** (`area_unit`) - `*_nonstring.py`,
   `*_nondict.py`, parsing tests. Large and heterogeneous; some touch
   provider/session modules, so the boundary is less crisp.
4. **Static checks** - e.g. `test_readme_ascii_fenced.py`,
   `test_docs_no_orphan_images.py`. Safe but tiny and `uncategorized` in the
   taxonomy, so a move buys little and matches no existing marker.

Not candidates for the first move (per #3712 guidance): security/owner-scope
tests, route/API tests, DB/session-heavy tests, auth/session concurrency
tests, and the taxonomy/runner infrastructure tests that changed recently
(#3491, #3556, #3659, #3711).

## Recommended first move

**CLI / script tests → `tests/cli/`**

Why this group over the alternatives:

- Lowest coupling: every file imports only the script under test (via
  `cli_loader`) plus `tests.helpers` stubs - no app, no routes, no real DB.
- Crisp, machine-checkable boundary: the set is exactly the files classified
  `area_cli` by `_taxonomy.py`, so before/after selection counts can be
  compared mechanically.
- Already the planned target dir for this category in `TESTING_STANDARD.md`
  (`tests/cli/`).
- Absolute imports (`from tests.helpers...`) and unique basenames mean no
  import-order or module-name collisions after the move.
- Lower risk than helper self-tests (tiny group, little payoff), unit tests
  (fuzzy boundary), or anything security/route/session-shaped.

## Files included in the first move

The 28 files classified `area_cli` (verified against `_taxonomy.py`):

Note: this inventory was refreshed against current `dev` after `tests/test_research_cli_status.py` was added to the `area_cli` set.

- `tests/test_calendar_cli_name.py`
- `tests/test_contacts_cli_rows.py`
- `tests/test_cookbook_cli_state.py`
- `tests/test_docs_cli_content_length.py`
- `tests/test_gallery_cli_album_count.py`
- `tests/test_gallery_cli_preview.py`
- `tests/test_logs_cli_resolve_nonstring.py`
- `tests/test_mail_cli_read_empty_fetch.py`
- `tests/test_mail_cli_recipients.py`
- `tests/test_mcp_cli_env_serialize.py`
- `tests/test_mcp_cli_json.py`
- `tests/test_memory_cli_rows.py`
- `tests/test_notes_cli_items.py`
- `tests/test_personal_cli_rows.py`
- `tests/test_preset_cli_invalid_entries.py`
- `tests/test_preset_cli_set_corrupt_entry.py`
- `tests/test_preset_cli_store.py`
- `tests/test_research_cli_preview.py`
- `tests/test_research_cli_status_filter.py`
- `tests/test_research_cli_status.py`
- `tests/test_research_cli_store.py`
- `tests/test_sessions_cli.py`
- `tests/test_signature_cli_export.py`
- `tests/test_skills_cli_preview.py`
- `tests/test_skills_cli_rows.py`
- `tests/test_tasks_cli_preview.py`
- `tests/test_theme_cli_store.py`
- `tests/test_webhook_cli_mask.py`

## Files intentionally excluded

- `tests/test_backup_cli_security.py` - classifies as `area_security`
  (security outranks cli in the taxonomy); moving it into `tests/cli/` would
  make the directory disagree with its marker. It belongs with the security
  group in a later phase.
- `tests/test_run_focus.py`, `tests/test_taxonomy.py` - taxonomy/runner
  infrastructure tests, recently changed (#3556, #3659); they also pin
  flat-layout paths (e.g. `tests/test_auth_config_lock_concurrency.py` in
  `test_run_focus.py`), so they stay put.
- Script-like but `uncategorized` files - `test_pr_blocker_audit.py`,
  `test_update_database_script.py`, `test_windows_update_script.py`,
  `test_setup_admin_user.py`, `test_amd_gpu_check_args.py`, `test_hwfit_*.py`.
  They exercise `scripts/` too, but moving them would make `tests/cli/`
  diverge from the `area_cli` marker set. Reclassify or move them in a later,
  separate slice.
- Everything else (security, routes, services, unit, js, helpers) - out of
  scope for the first move by design.

## How this was verified

Read-only checks, run from the repo root on this branch. Note the real API is
`classify_test_path` (there is no `classify_test_file`).

```bash
# Compute the area_cli set and confirm test_backup_cli_security.py is
# area_security. Expected: 28 files, then "security".
./venv/bin/python - <<'PY'
from pathlib import Path
from tests._taxonomy import classify_test_path

cli = [p for p in sorted(Path("tests").glob("test_*.py"))
       if classify_test_path(p).area == "cli"]
print(len(cli))
for p in cli:
    print(p)
print(classify_test_path("tests/test_backup_cli_security.py").area)
PY

# Coupling check across the CLI files. Expected: the only hits are
# "SessionLocal" as stub attribute names passed to tests.helpers.db_stubs;
# no TestClient, FastAPI, create_app, sqlite, or dependency_overrides.
rg -n "TestClient|FastAPI|create_app|SessionLocal|sqlite|dependency_overrides" \
  tests/test_*cli*.py tests/test_sessions_cli.py

# Hard-coded flat paths to the exact CLI files outside tests/. Expected: no matches.
./venv/bin/python - <<'PY2' > /tmp/area_cli_paths.txt
from pathlib import Path
from tests._taxonomy import classify_test_path

for path in sorted(Path("tests").glob("test_*.py")):
    if classify_test_path(path).area == "cli":
        print(path)
PY2

rg -n -F -f /tmp/area_cli_paths.txt .github scripts docs \
  tests/README.md tests/TESTING_STANDARD.md pyproject.toml 2>/dev/null || true
```

Also checked by reading the code: `tests/conftest.py` registers sub-markers
from a recursive `rglob` scan, and `tests/_taxonomy.py` classifies by filename
tokens only (plus the `tests/helpers/` directory rule), so the markers of the
28 files do not change when they move into `tests/cli/`.

## Validation for the future move PR

Run with the project venv (`./venv/bin/python`); system `python3` may miss
pinned deps. Before the move, record the baseline; after, compare:

```bash
# Selection must match the 28 files before and after the move.
./venv/bin/python tests/run_focus.py --dry-run --area cli
./venv/bin/python -m pytest -m area_cli -q

# Moved files pass when targeted directly.
./venv/bin/python -m pytest tests/cli/ -q

# Whole-suite collection still succeeds (catches import/path breakage).
./venv/bin/python -m pytest --collect-only -q

# Taxonomy/runner infrastructure is unaffected.
./venv/bin/python -m pytest tests/test_taxonomy.py tests/test_run_focus.py -q

# No stale flat-path references to the moved files. Expected: no matches
# outside tests/cli/ itself.
./venv/bin/python - <<'PY2' > /tmp/area_cli_paths.txt
from pathlib import Path
from tests._taxonomy import classify_test_path

for path in sorted(Path("tests").glob("test_*.py")):
    if classify_test_path(path).area == "cli":
        print(path)
PY2

rg -n -F -f /tmp/area_cli_paths.txt .github scripts docs \
  tests/README.md tests/TESTING_STANDARD.md pyproject.toml 2>/dev/null || true
```

Pass criteria: identical test counts for `-m area_cli` before/after, zero
collection errors, and no changes outside the moved files.

## Non-goals

- No file moves, renames, or deletions in this PR.
- No changes to `conftest.py`, `_taxonomy.py`, `run_focus.py`, helpers,
  markers, CI workflows, or production code.
- No recommendation to split the whole suite at once; later groups get their
  own inventory-then-move slices.
