"""Proof that consolidated frontend modules and entry points work after offline upgrade.

Runs tests/browser/test_frontend_consolidation_offline_upgrade.mjs:
It upgrades a browser profile from the deployed dev base (360f6e9b1)
to candidate, evicts the base cache, shuts down the server,
goes offline, and verifies that updated entry points and new shared modules load together
from CacheStorage without errors.
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tests" / "browser" / "test_frontend_consolidation_offline_upgrade.mjs"
PINNED_BASE_REVISION = "360f6e9b112c5c28e2f1c6197f9a25d070ac1a7e"


def test_base_revision_is_available():
    available = subprocess.run(
        ["git", "cat-file", "-e", f"{PINNED_BASE_REVISION}^{{commit}}"],
        cwd=ROOT,
        capture_output=True,
    )
    assert available.returncode == 0, f"base commit {PINNED_BASE_REVISION} must be reachable"


def test_frontend_consolidation_offline_upgrade():
    env = {**os.environ, "ODYSSEUS_TEST_PYTHON": sys.executable}
    result = subprocess.run(
        ["node", str(SCRIPT)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=180,
        env=env,
    )
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-2000:]
