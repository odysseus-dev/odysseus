"""`odysseus-research list --status complete` was returning nothing.

The CLI's `--status` argparse choice is "complete" — that is the user-facing
label — but the writer in `services/research/research_handler.py` stores
`status="done"` for a finished run (and the older `src/research_handler.py`
copy does the same). The list filter was a literal string compare, so
`--status complete` matched zero records on any real on-disk corpus.

These tests pin the alias so the friendlier CLI word keeps matching the
stored value. The other choices (`running`, `cancelled`, `error`) are
stored verbatim, so they must NOT be rewritten by the alias map.

Part of #2122 (odysseus-* CLI list/search bugs).
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]


def _load_cli():
    path = ROOT / "scripts" / "odysseus-research"
    loader = importlib.machinery.SourceFileLoader("odysseus_research_cli", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def _run_list(cli, tmp_path, monkeypatch, status, records):
    cli._DATA_DIR = tmp_path
    for name, blob in records.items():
        (tmp_path / f"{name}.json").write_text(json.dumps(blob))
    emitted = []
    monkeypatch.setattr(cli, "emit", lambda value, args: emitted.append(value))
    cli.cmd_list(SimpleNamespace(status=status, limit=50))
    assert emitted, "cmd_list emitted nothing"
    return [r["id"] for r in emitted[0]]


def test_status_complete_matches_writer_done_records(tmp_path, monkeypatch):
    """`--status complete` must return the records the writer marked `done`.
    Without the alias this filter is silently empty on any real corpus."""
    cli = _load_cli()
    ids = _run_list(cli, tmp_path, monkeypatch, status="complete", records={
        "rp-done":      {"query": "finished one", "status": "done",      "started_at": "2026-01-02"},
        "rp-running":   {"query": "still running", "status": "running",  "started_at": "2026-01-01"},
        "rp-cancelled": {"query": "user stopped",  "status": "cancelled","started_at": "2025-12-31"},
    })
    assert ids == ["rp-done"], (
        "--status complete should alias to the writer's stored 'done' value; "
        f"got {ids}. The alias map in `_STATUS_CLI_TO_STORED` was bypassed."
    )


def test_status_running_still_matches_verbatim(tmp_path, monkeypatch):
    """`running` is stored verbatim, so the alias must NOT rewrite it.
    A blanket map that turned every CLI choice into a stored variant would
    re-introduce the empty-result bug on the running/cancelled/error paths."""
    cli = _load_cli()
    ids = _run_list(cli, tmp_path, monkeypatch, status="running", records={
        "rp-done":    {"query": "finished",     "status": "done"},
        "rp-running": {"query": "still running", "status": "running"},
    })
    assert ids == ["rp-running"], f"--status running must match verbatim; got {ids}"


def test_status_cancelled_still_matches_verbatim(tmp_path, monkeypatch):
    cli = _load_cli()
    ids = _run_list(cli, tmp_path, monkeypatch, status="cancelled", records={
        "rp-done":      {"query": "finished",  "status": "done"},
        "rp-cancelled": {"query": "user stop", "status": "cancelled"},
    })
    assert ids == ["rp-cancelled"]


def test_status_error_still_matches_verbatim(tmp_path, monkeypatch):
    cli = _load_cli()
    ids = _run_list(cli, tmp_path, monkeypatch, status="error", records={
        "rp-done":  {"query": "finished", "status": "done"},
        "rp-error": {"query": "crashed",  "status": "error"},
    })
    assert ids == ["rp-error"]


def test_status_filter_tolerates_missing_or_non_string_status(tmp_path, monkeypatch):
    """A corrupt record with no `status` (or a non-string status) must not
    crash the filter and must not falsely match `--status complete`. The
    existing `_load_path` already drops non-dict blobs; this guards the
    next layer."""
    cli = _load_cli()
    ids = _run_list(cli, tmp_path, monkeypatch, status="complete", records={
        "rp-good":  {"query": "ok",  "status": "done"},
        "rp-blank": {"query": "no status field"},
        "rp-typed": {"query": "non-string", "status": 42},
    })
    assert ids == ["rp-good"], (
        "--status complete should only match the writer's 'done' string; "
        f"got {ids}."
    )
