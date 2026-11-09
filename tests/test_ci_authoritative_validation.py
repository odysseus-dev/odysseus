"""Regression coverage for authoritative Python CI validation."""

import re
from pathlib import Path


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
