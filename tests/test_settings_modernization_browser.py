"""Run the Settings browser suite against isolated Odysseus instances.

tests/browser/test_settings_modernization.mjs starts real servers on ephemeral
ports with temporary data directories and drives them with Playwright. It
covers the Settings finder and navigation, per-account versus server-wide
scope, authentication-status failure and recovery, the Integrations list
refresh, member/administrator isolation, the personal model-default label and
the integration forms. Node browser tests are not discovered by pytest on
their own, so this wrapper is what CI runs.
"""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_settings_browser_suite_passes():
    result = subprocess.run(
        ["node", "tests/browser/test_settings_modernization.mjs"], cwd=ROOT,
        capture_output=True, text=True, timeout=900,
    )
    failed = [line for line in result.stdout.splitlines() if line.startswith("FAIL ")]
    assert result.returncode == 0, "\n".join(failed) + "\n" + result.stdout[-3000:] + result.stderr[-1500:]
