"""Tests for the report-only changed-test guidance helper."""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = ROOT / ".github" / "scripts" / "focused_test_guidance.py"


def _load_helper():
    spec = importlib.util.spec_from_file_location("focused_test_guidance", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


guidance = _load_helper()


def test_parse_paths_supports_nul_delimited_git_output():
    raw_paths = b"tests/test_alpha.py\0tests/path with spaces/test_beta.py\0"

    assert guidance.parse_paths(raw_paths) == [
        "tests/test_alpha.py",
        "tests/path with spaces/test_beta.py",
    ]


def test_select_test_paths_ignores_paths_outside_tests():
    paths = [
        "src/test_alpha.py",
        "tests/test_beta.py",
        "./tests/unit/example_test.py",
        "tests/../src/test_gamma.py",
    ]

    assert guidance.select_test_paths(paths) == [
        "tests/test_beta.py",
        "tests/unit/example_test.py",
    ]


def test_select_test_paths_deduplicates_paths():
    paths = ["tests/test_alpha.py", "./tests/test_alpha.py"]

    assert guidance.select_test_paths(paths) == ["tests/test_alpha.py"]


def test_format_report_builds_command_for_changed_python_test():
    report = guidance.format_report(["tests/test_beta.py"])

    assert "- `tests/test_beta.py`" in report
    assert "python3 -m pytest -q tests/test_beta.py" in report
    assert "does not infer tests from source changes" in report
    assert "Existing blocking CI remains the source of truth" in report


def test_format_report_lists_changed_non_python_test_path_without_command():
    report = guidance.format_report(["tests/README.md"])

    assert "- `tests/README.md`" in report
    assert "No directly runnable pytest files changed." in report
    assert "python3 -m pytest" not in report


def test_format_report_ignores_src_path():
    report = guidance.format_report(["src/test_ignored.py"])

    assert "src/test_ignored.py" not in report
    assert "No changed paths under `tests/`." in report


def test_format_report_shell_quotes_path_with_spaces():
    report = guidance.format_report(["tests/path with spaces/test_beta.py"])

    assert "- `tests/path with spaces/test_beta.py`" in report
    assert (
        "python3 -m pytest -q 'tests/path with spaces/test_beta.py'"
    ) in report


def test_format_report_handles_no_changed_test_paths():
    report = guidance.format_report([])

    assert "No changed paths under `tests/`." in report
    assert "No directly runnable pytest files changed." in report

def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_changed_paths_from_merge_base_excludes_base_only_test_changes(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()

    _git(repo, "init")
    _git(repo, "config", "user.email", "ci@example.test")
    _git(repo, "config", "user.name", "CI Test")

    _write(repo / "tests/test_shared.py", "def test_shared():\n    assert True\n")
    _git(repo, "add", "tests/test_shared.py")
    _git(repo, "commit", "-m", "base")
    ancestor = _git(repo, "rev-parse", "HEAD")

    _git(repo, "checkout", "-b", "feature")
    _write(repo / "tests/test_pr_delta.py", "def test_pr_delta():\n    assert True\n")
    _git(repo, "add", "tests/test_pr_delta.py")
    _git(repo, "commit", "-m", "add pr test")
    head_sha = _git(repo, "rev-parse", "HEAD")

    _git(repo, "checkout", "-b", "dev", ancestor)
    _write(repo / "tests/test_shared.py", "def test_shared():\n    assert 1 == 1\n")
    _git(repo, "add", "tests/test_shared.py")
    _git(repo, "commit", "-m", "base-only test change")
    base_sha = _git(repo, "rev-parse", "HEAD")

    endpoint_paths = guidance.parse_paths(
        subprocess.check_output(
            [
                "git",
                "diff",
                "--name-only",
                "--diff-filter=ACMRT",
                "-z",
                base_sha,
                head_sha,
                "--",
                "tests/",
            ],
            cwd=repo,
        )
    )
    assert "tests/test_shared.py" in endpoint_paths

    monkeypatch.chdir(repo)
    assert guidance.changed_paths_from_merge_base(base_sha, head_sha) == [
        "tests/test_pr_delta.py"
    ]
