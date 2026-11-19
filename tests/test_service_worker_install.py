"""Run the service-worker install handler tests (Node, no browser)."""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_install_reports_uncached_urls_without_blocking():
    result = subprocess.run(
        ["node", "--test", "tests/serviceWorkerInstall.test.mjs"], cwd=ROOT,
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
