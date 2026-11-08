"""Regression coverage for authoritative Python CI validation."""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml


_WORKFLOW = (
    Path(__file__).resolve().parent.parent / ".github" / "workflows" / "ci.yml"
)


def _indented_block(text: str, heading: str, indent: int) -> str:
    pattern = re.compile(
        rf"(?ms)^{' ' * indent}{re.escape(heading)}:\n"
        rf"(?P<body>(?:(?:{' ' * (indent + 2)}.*|\s*)\n)*)"
    )
    match = pattern.search(text)
    assert match is not None, f"missing {heading!r} block"
    return match.group(0)


def test_ci_runs_on_integrated_dev_pushes():
    workflow = _WORKFLOW.read_text()
    push = _indented_block(workflow, "push", 2)

    assert re.search(r"(?m)^    branches:\s*\[main,\s*dev\]\s*$", push)
    assert "paths-ignore:" not in push


def test_python_tests_are_authoritative():
    workflow = _WORKFLOW.read_text()
    python_tests = _indented_block(workflow, "python-tests", 2)

    assert "python -m pytest -q" in python_tests
    assert "continue-on-error:" not in python_tests


def test_ci_checks_system_trust_before_project_installation():
    python_tests = _indented_block(_WORKFLOW.read_text(), "python-tests", 2)
    system_check = python_tests.index("python scripts/check_ca_trust.py --system\n")
    system_update = python_tests.index("sudo apt-get install -y --no-install-recommends ffmpeg bubblewrap ca-certificates")
    bootstrap = python_tests.index("uv pip install --system --require-hashes")
    python_check = python_tests.index("python scripts/check_ca_trust.py --python\n")
    application_install = python_tests.index("uv pip install --system -r requirements.txt")

    assert system_update < system_check < bootstrap < python_check < application_install
    assert system_check < python_tests.index("npm ci")
    assert system_check < python_tests.index("npx playwright install")


def _docs_check_script() -> str:
    workflow = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["python-tests"]["steps"]
    return next(step["run"] for step in steps if step.get("id") == "docs-check")


def _git(repo: Path, env: dict, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", "user.name=CI test", "-c", "user.email=ci@example.invalid", *args],
        cwd=repo,
        env=env,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return result.stdout.strip()


def _scratch_repo(tmp_path: Path) -> tuple[Path, dict]:
    home = tmp_path / "home"
    home.mkdir()
    # Inherited GIT_* variables (set when the suite runs from a git hook) could
    # aim these commands at the outer repository; a private HOME keeps the
    # developer's git config, hooks and signing settings out as well.
    env = {name: value for name, value in os.environ.items() if not name.startswith("GIT_")}
    env.update(HOME=str(home), XDG_CONFIG_HOME=str(home), GIT_CONFIG_NOSYSTEM="1")
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(repo)], env=env, check=True, capture_output=True)
    assert Path(_git(repo, env, "rev-parse", "--show-toplevel")).resolve() == repo.resolve()
    return repo, env


def _commit(repo: Path, env: dict, files: dict, message: str) -> str:
    for name, text in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    if files:
        _git(repo, env, "add", "--", *files)
    _git(repo, env, "commit", "-q", "--allow-empty", "-m", message)
    return _git(repo, env, "rev-parse", "HEAD")


def _run_docs_check(repo: Path, env: dict, tmp_path: Path, *, event: str, base: str, head: str) -> str:
    script = _docs_check_script()
    for expression, value in {
        "${{ github.event_name }}": event,
        "${{ github.event.pull_request.base.sha }}": base,
        "${{ github.event.pull_request.head.sha }}": head,
        "${{ github.event.before }}": base,
        "${{ github.sha }}": head,
    }.items():
        script = script.replace(expression, value)
    assert "${{" not in script
    output = tmp_path / "github-output"
    output.write_text("", encoding="utf-8")
    # GitHub runs a run: block without an explicit shell as `bash -e {0}`.
    subprocess.run(
        ["bash", "--noprofile", "--norc", "-e", "-c", script],
        cwd=repo,
        env={**env, "GITHUB_OUTPUT": str(output)},
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    values = [
        line.split("=", 1)[1]
        for line in output.read_text(encoding="utf-8").splitlines()
        if line.startswith("docs_only=")
    ]
    assert len(values) == 1, values
    return values[0]


_NEEDS_SHELL = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("git") is None,
    reason="the docs-only check is a Bash script that runs git",
)


@_NEEDS_SHELL
@pytest.mark.parametrize(
    ("event", "change", "expected"),
    [
        # An empty commit pushed to re-run CI changes no files. Nothing about
        # it is docs-only, so it must not report green without running pytest.
        ("push", {}, "false"),
        ("pull_request", {}, "false"),
        ("push", {"docs/guide.md": "guide\n", "NOTES.md": "notes\n"}, "true"),
        ("pull_request", {"docs/guide.md": "guide\n"}, "true"),
        ("push", {"src/module.py": "value = 1\n"}, "false"),
        ("pull_request", {"docs/guide.md": "guide\n", "src/module.py": "value = 1\n"}, "false"),
    ],
)
def test_docs_check_skips_pytest_only_for_prose_changes(tmp_path, event, change, expected):
    repo, env = _scratch_repo(tmp_path)
    base = _commit(repo, env, {"README.md": "base\n", "src/module.py": "value = 0\n"}, "base")
    head = _commit(repo, env, change, "change")

    assert _run_docs_check(repo, env, tmp_path, event=event, base=base, head=head) == expected


@_NEEDS_SHELL
@pytest.mark.parametrize("unknown_base", ["0" * 40, "1" * 40])
@pytest.mark.parametrize("earlier_commits", [0, 1, 2])
def test_docs_check_runs_pytest_when_the_push_base_is_unknown(tmp_path, unknown_base, earlier_commits):
    # A new branch's first push reports an all-zero `before`, and a force-push
    # can report one that is no longer in the clone. The push may then carry
    # several commits; looking only at its last one would let a docs-only tip
    # hide earlier code changes, so with no diff to the real base pytest runs.
    repo, env = _scratch_repo(tmp_path)
    for index in range(earlier_commits):
        _commit(repo, env, {f"src/module_{index}.py": "value = 0\n"}, f"code {index}")
    head = _commit(repo, env, {"docs/guide.md": "guide\n"}, "docs tip")

    assert _run_docs_check(repo, env, tmp_path, event="push", base=unknown_base, head=head) == "false"
