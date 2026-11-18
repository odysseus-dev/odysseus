"""Settings must open offline after a real service-worker upgrade.

Runs tests/browser/test_settings_offline_upgrade.mjs: it upgrades a browser
profile from the previous application version (PREVIOUS_REF, default
origin/dev) to this working tree, goes offline, and opens Settings. Static URL
coverage alone lives in test_service_worker_static_cache.py.
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PREVIOUS_REF = os.environ.get("PREVIOUS_REF", "origin/dev")


def _ref_resolves() -> bool:
    return subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"{PREVIOUS_REF}^{{commit}}"],
        cwd=ROOT, capture_output=True,
    ).returncode == 0


@pytest.mark.skipif(shutil.which("node") is None, reason="node is required")
def test_settings_open_offline_after_a_service_worker_upgrade(tmp_path):
    if not _ref_resolves():
        pytest.skip(f"{PREVIOUS_REF} is not available; set PREVIOUS_REF to an earlier commit")
    result = subprocess.run(
        ["node", "tests/browser/test_settings_offline_upgrade.mjs"], cwd=ROOT,
        env=dict(os.environ, PREVIOUS_REF=PREVIOUS_REF),
        capture_output=True, text=True, timeout=420,
    )
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-2000:]
