"""Run deterministic Notes contracts in the authoritative pytest CI shards."""
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_notes_domain_application_infrastructure_and_compatibility_contracts():
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
