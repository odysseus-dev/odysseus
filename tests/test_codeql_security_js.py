"""Real Chromium regressions for the corrected CodeQL data-flow contracts."""
import json
import subprocess
from pathlib import Path


def test_codeql_security_browser_contracts():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        ["node", "tests/codeql_security_browser.cjs"], cwd=root,
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == {
        "chat": True, "markdown": True, "svg": True,
        "menus": True, "admin": True, "bootstrap": True,
    }


def test_pr6503_ledger_email_and_gallery_security_contracts():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        ["node", "tests/pr6503_security_browser.cjs"], cwd=root,
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == {
        "ledger": True, "email": True, "gallery": True, "links": True, "skills": True,
        "sanitizer": True, "print": True,
    }
