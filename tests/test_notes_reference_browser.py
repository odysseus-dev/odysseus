"""Execute the real Notes ESM graph and DOM with deterministic API responses."""
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_notes_reference_desktop_mobile_and_public_identity(tmp_path):
    env = dict(os.environ, NOTES_EVIDENCE_DIR=os.environ.get("NOTES_EVIDENCE_DIR", str(tmp_path)))
    result = subprocess.run(
        ["node", "tests/browser/notesReference.mjs"], cwd=ROOT, env=env,
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_notes_complete_graph_imports_first_time_offline():
    result = subprocess.run(
        ["node", "tests/browser/notesOffline.mjs"], cwd=ROOT,
        capture_output=True, text=True, timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
