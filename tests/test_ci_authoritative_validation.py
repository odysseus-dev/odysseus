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
    system_update = python_tests.index('-o Dir::Cache::archives="$debs" $APT_PACKAGES\n')
    bootstrap = python_tests.index("uv pip install --system --require-hashes")
    python_check = python_tests.index("python scripts/check_ca_trust.py --python\n")
    application_install = python_tests.index("uv pip install --system -r requirements.txt")

    assert system_update < system_check < bootstrap < python_check < application_install
    assert system_check < python_tests.index("npm ci")
    assert system_check < python_tests.index("npx playwright install")


def test_ci_apt_packages_cover_ffmpeg_bubblewrap_and_trust_store():
    python_tests = _indented_block(_WORKFLOW.read_text(), "python-tests", 2)

    assert re.search(
        r"(?m)^      APT_PACKAGES: ffmpeg bubblewrap ca-certificates$", python_tests
    )


def _job_blocks(text: str) -> dict[str, str]:
    jobs = text[text.index("\njobs:\n"):]
    parts = re.split(r"(?m)^  ([\w-]+):\n", jobs)
    return dict(zip(parts[1::2], parts[2::2]))


def test_ci_every_job_has_a_timeout():
    jobs = _job_blocks(_WORKFLOW.read_text())

    assert "python-tests" in jobs
    for name, block in jobs.items():
        match = re.search(r"(?m)^    timeout-minutes: (\d+)$", block)
        assert match is not None, f"{name} has no job timeout"
        assert int(match.group(1)) <= 30, name


def test_ci_apt_step_is_cached_and_bounded():
    python_tests = _indented_block(_WORKFLOW.read_text(), "python-tests", 2)
    restore = python_tests.index("uses: actions/cache/restore@")
    install = python_tests.index("id: apt-install")
    save = python_tests.index("uses: actions/cache/save@")
    install_step = python_tests[install:save]

    assert restore < install < save
    assert "ImageVersion" in python_tests[:restore]
    assert "timeout-minutes: 8" in install_step
    # A cache hit installs from the restored set without touching a mirror.
    hit = install_step.index('if [ "$CACHE_HIT" = "true" ]')
    miss = install_step.index('if [ "$installed" != "true" ]')
    assert "--no-download" in install_step[hit:miss]
    assert "apt-get update" not in install_step[hit:miss]
    # A miss bounds wall time, since apt's idle timeouts miss a slow trickle.
    assert "timeout 60 sudo apt-get update" in install_step
    assert "timeout 150 sudo apt-get install -y --download-only" in install_step
    assert "/etc/apt/apt-mirrors.txt" in install_step
    assert "steps.apt-install.outputs.save == 'true'" in python_tests
