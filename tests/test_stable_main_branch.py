"""After stable update, repo should track origin/main."""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_on_main_branch():
    branch = subprocess.check_output(
        ["git", "-c", f"safe.directory={ROOT}", "branch", "--show-current"],
        text=True,
        cwd=ROOT,
    ).strip()
    assert branch == "main", f"expected main, got {branch!r}"


def test_not_behind_origin_main():
    out = subprocess.check_output(
        ["git", "-c", f"safe.directory={ROOT}", "rev-list", "--count", "HEAD..origin/main"],
        text=True,
        cwd=ROOT,
    ).strip()
    assert out == "0", f"still behind origin/main by {out} commits"
