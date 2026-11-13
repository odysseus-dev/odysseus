"""Pre-extraction behavioral characterization runner for document editor.

Exercises storage key compatibility (M4), email workflow and guards (M5),
selection highlight re-anchoring and chat moves (M6), import-time side
effects and multi-instance evaluation (M7).
"""
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("scenario", [
    "import-time-effects",
    "multi-instance-check",
    "storage-keys",
    "email-double-send",
    "email-stale-ai-reply",
    "email-attachments",
    "email-local-draft",
    "selection-highlights",
    "chat-moves",
])
def test_document_characterization_scenarios(scenario):
    result = subprocess.run(
        ["node", "tests/documentCharacterization.mjs", scenario],
        cwd=ROOT,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
