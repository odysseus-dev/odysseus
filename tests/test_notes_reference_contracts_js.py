"""Run deterministic Notes contracts in the authoritative pytest CI shards."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _acorn_resolvable():
    # notesCharacterization.test.mjs imports the npm package "acorn".
    probe = subprocess.run(
        ["node", "-e", "require.resolve('acorn')"], cwd=ROOT, capture_output=True,
    )
    return probe.returncode == 0


def test_notes_domain_application_infrastructure_and_compatibility_contracts():
    if not shutil.which("node"):
        pytest.skip("Node is required")
    # CI installs acorn with `npm ci`; never skip there.
    if not os.environ.get("CI") and not _acorn_resolvable():
        pytest.skip("npm package acorn is not installed (run npm ci)")
    files = [
        "notesCharacterization", "notesRecurrence", "notesInfrastructure",
        "notesApplication", "notesReminders", "notesWorkflows", "notesDraftAutosave",
        "selectedNotesContext", "noteTestOracle",
    ]
    result = subprocess.run(
        ["node", "--test", *[f"tests/{name}.test.mjs" for name in files]], cwd=ROOT,
        env=dict(os.environ, ENDPOINT_URL="http://127.0.0.1:1"),
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
