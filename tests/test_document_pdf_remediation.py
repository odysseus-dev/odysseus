"""Browser regressions for PDF pane persistence authority and lifecycle."""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_document_pdf_remediation_browser():
    result = subprocess.run(
        ["node", str(ROOT / "tests/documentPdfRemediation.mjs")],
        cwd=ROOT, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS PDF" in result.stdout
