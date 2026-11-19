"""Settings must open offline after a real service-worker upgrade.

Runs tests/browser/test_settings_offline_upgrade.mjs: it upgrades a browser
profile from a pinned earlier application revision to this working tree, goes
offline, and opens Settings. Static URL coverage alone lives in
test_service_worker_static_cache.py.

The previous revision is a fixed commit, so this test does not change meaning
as dev moves. It must run: a checkout without that commit (a shallow clone)
fails with an instruction instead of skipping, so the regression cannot vanish
from CI unnoticed.
"""
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tests" / "browser" / "test_settings_offline_upgrade.mjs"


def _pinned_revision() -> str:
    match = re.search(r"PINNED_PREVIOUS_REVISION = '([0-9a-f]{40})'", SCRIPT.read_text(encoding="utf-8"))
    assert match, "the browser test must pin its previous revision to a full commit SHA"
    return match.group(1)


def test_the_previous_revision_is_available_and_predates_the_settings_modules():
    revision = _pinned_revision()
    available = subprocess.run(
        ["git", "cat-file", "-e", f"{revision}^{{commit}}"], cwd=ROOT, capture_output=True,
    )
    assert available.returncode == 0, (
        f"commit {revision} is missing; fetch full history (git fetch --unshallow) "
        "so the offline upgrade test can archive the previous application"
    )
    for path, expect_present in (("static/sw.js", True), ("static/js/settings/access.js", False)):
        present = subprocess.run(
            ["git", "cat-file", "-e", f"{revision}:{path}"], cwd=ROOT, capture_output=True,
        ).returncode == 0
        assert present is expect_present, f"{path} at {revision}"


def test_settings_open_offline_after_a_service_worker_upgrade():
    result = subprocess.run(
        ["node", "tests/browser/test_settings_offline_upgrade.mjs"], cwd=ROOT,
        capture_output=True, text=True, timeout=420,
    )
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-2000:]
