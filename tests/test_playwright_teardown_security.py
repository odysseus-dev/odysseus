"""Regression tests for Playwright global teardown security hardening (CodeQL #1202 & Filesystem Safety).

Validates that:
1. Shell command execution is prevented (execFileSync with shell: false and separate argument arrays).
2. Synthetic command substitutions and shell metacharacters fail safely without invoking a shell.
3. Paths containing spaces and non-standard characters are preserved without word-splitting.
4. Process errors (missing executable, nonzero exit, timeout) fail closed by purging artifacts.
5. Secure default tracing (unconditional CI suppression and local default purging) remains enforced.
6. Opt-in local runs execute the sanitizer and preserve sanitized archives.
7. Filesystem confinement: teardown ignores arbitrary directory environment variables and confines
   cleanup to strictly validated test-owned locations.
"""

import json
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
TEARDOWN_PATH = REPO_ROOT / "tests" / "e2e" / "teardown.js"


def _run_teardown(env: dict = None, options: dict = None) -> subprocess.CompletedProcess:
    """Run teardown.js in an isolated Node process with custom environment and options."""
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    opts_json = json.dumps(options or {})
    script = (
        "const teardown = require('./tests/e2e/teardown.js');\n"
        f"const opts = {opts_json};\n"
        "teardown(opts).then(() => process.exit(0)).catch((err) => {\n"
        "  console.error(err);\n"
        "  process.exit(1);\n"
        "});\n"
    )
    return subprocess.run(
        ["node", "-e", script],
        cwd=REPO_ROOT,
        env=full_env,
        capture_output=True,
        text=True,
    )


def test_teardown_unconditional_ci_suppression(tmp_path):
    """In CI environments, all sensitive trace artifacts are purged unconditionally."""
    results_dir = tmp_path / "test-results"
    results_dir.mkdir()
    (results_dir / "trace.zip").write_bytes(b"PK0304 sensitive trace archive")
    (results_dir / "session.har").write_text('{"log": {"entries": []}}')
    (results_dir / "failure.png").write_bytes(b"\x89PNG sensitive screenshot")
    (results_dir / "temp.tmp").write_text("in-progress trace data")

    res = _run_teardown(
        env={
            "CI": "true",
            "GITHUB_ACTIONS": "true",
            "PLAYWRIGHT_ENABLE_TRACE": "1",  # Even with opt-in, CI must purge unconditionally
        },
        options={"resultsDir": str(results_dir)},
    )
    assert res.returncode == 0
    # All sensitive artifacts must be purged
    assert not (results_dir / "trace.zip").exists()
    assert not (results_dir / "session.har").exists()
    assert not (results_dir / "failure.png").exists()
    assert not (results_dir / "temp.tmp").exists()


def test_teardown_local_default_purges_without_opt_in(tmp_path):
    """Local runs without explicit PLAYWRIGHT_ENABLE_TRACE=1 purge artifacts."""
    results_dir = tmp_path / "test-results"
    results_dir.mkdir()
    (results_dir / "trace.zip").write_bytes(b"PK0304 trace")
    (results_dir / "session.har").write_text('{"log": {}}')
    (results_dir / "failure.png").write_bytes(b"\x89PNG")
    (results_dir / "temp.tmp").write_text("temp")

    res = _run_teardown(
        env={
            "CI": "",
            "GITHUB_ACTIONS": "",
            "PLAYWRIGHT_ENABLE_TRACE": "0",  # No opt-in
        },
        options={"resultsDir": str(results_dir)},
    )
    assert res.returncode == 0
    assert not (results_dir / "trace.zip").exists()
    assert not (results_dir / "session.har").exists()
    assert not (results_dir / "failure.png").exists()
    assert not (results_dir / "temp.tmp").exists()


def test_teardown_sanitizer_success_with_opt_in(tmp_path):
    """Opt-in local runs execute the Python sanitizer and retain sanitized archives."""
    results_dir = tmp_path / "test-results"
    results_dir.mkdir()
    trace_file = results_dir / "trace.zip"
    with zipfile.ZipFile(trace_file, "w") as z:
        action = {"type": "action", "name": "fill", "params": {"value": "secret_password"}}
        z.writestr("0-trace.trace", json.dumps(action).encode() + b"\n")

    res = _run_teardown(
        env={
            "CI": "",
            "GITHUB_ACTIONS": "",
            "PLAYWRIGHT_ENABLE_TRACE": "1",
            "ODYSSEUS_TEST_PYTHON": sys.executable,
        },
        options={"resultsDir": str(results_dir)},
    )
    assert res.returncode == 0
    # Archive is retained because sanitizer succeeded
    assert trace_file.exists()
    with zipfile.ZipFile(trace_file, "r") as z:
        content = json.loads(z.read("0-trace.trace").decode())
        assert content["params"]["value"] == "[REDACTED]"


def test_teardown_missing_python_executable_fails_closed(tmp_path):
    """If the python executable is missing, teardown fails closed and purges archives."""
    results_dir = tmp_path / "test-results"
    results_dir.mkdir()
    trace_file = results_dir / "trace.zip"
    trace_file.write_bytes(b"PK0304 trace")
    tmp_file = results_dir / "trace.tmp"
    tmp_file.write_text("trace tmp")

    res = _run_teardown(
        env={
            "CI": "",
            "GITHUB_ACTIONS": "",
            "PLAYWRIGHT_ENABLE_TRACE": "1",
            "ODYSSEUS_TEST_PYTHON": "/usr/bin/nonexistent_python_binary_xyz_404",
        },
        options={"resultsDir": str(results_dir)},
    )
    assert res.returncode == 0
    # Fail closed: archives must be purged
    assert not trace_file.exists()
    assert not tmp_file.exists()


def test_teardown_nonzero_sanitizer_exit_fails_closed(tmp_path):
    """If the sanitizer exits nonzero, teardown fails closed and purges archives."""
    failing_script = tmp_path / "fail_sanitizer.py"
    failing_script.write_text("import sys; sys.exit(2)\n")

    results_dir = tmp_path / "test-results"
    results_dir.mkdir()
    trace_file = results_dir / "trace.zip"
    trace_file.write_bytes(b"PK0304 trace")
    tmp_file = results_dir / "trace.tmp"
    tmp_file.write_text("trace tmp")

    res = _run_teardown(
        env={
            "CI": "",
            "GITHUB_ACTIONS": "",
            "PLAYWRIGHT_ENABLE_TRACE": "1",
            "ODYSSEUS_TEST_PYTHON": sys.executable,
        },
        options={
            "resultsDir": str(results_dir),
            "sanitizerScript": str(failing_script),
        },
    )
    assert res.returncode == 0
    assert not trace_file.exists()
    assert not tmp_file.exists()


def test_teardown_sanitizer_timeout_fails_closed(tmp_path):
    """If the sanitizer hangs or exceeds timeout, teardown fails closed and purges archives."""
    hanging_script = tmp_path / "hang_sanitizer.py"
    hanging_script.write_text("import time; time.sleep(5)\n")

    results_dir = tmp_path / "test-results"
    results_dir.mkdir()
    trace_file = results_dir / "trace.zip"
    trace_file.write_bytes(b"PK0304 trace")
    tmp_file = results_dir / "trace.tmp"
    tmp_file.write_text("trace tmp")

    res = _run_teardown(
        env={
            "CI": "",
            "GITHUB_ACTIONS": "",
            "PLAYWRIGHT_ENABLE_TRACE": "1",
            "ODYSSEUS_TEST_PYTHON": sys.executable,
        },
        options={
            "resultsDir": str(results_dir),
            "sanitizerScript": str(hanging_script),
            "timeout": 300,  # 300ms timeout
        },
    )
    assert res.returncode == 0
    # Fail closed: timeout killed the process and purged archives
    assert not trace_file.exists()
    assert not tmp_file.exists()


def test_teardown_handles_paths_containing_spaces(tmp_path):
    """Paths with spaces in results directory and sanitizer script execute cleanly."""
    spaced_root = tmp_path / "space dir test" / "sub dir results"
    spaced_root.mkdir(parents=True)
    trace_file = spaced_root / "trace.zip"
    with zipfile.ZipFile(trace_file, "w") as z:
        action = {"type": "action", "params": {"value": "secret123"}}
        z.writestr("0.trace", json.dumps(action).encode() + b"\n")

    spaced_script = tmp_path / "sanitizer tools" / "run sanitizer.py"
    spaced_script.parent.mkdir(parents=True)
    spaced_script.write_text(
        "import sys, json, zipfile, os\n"
        "target = sys.argv[1]\n"
        "assert os.path.exists(target)\n"
        "marker = os.path.join(target, 'executed.marker')\n"
        "with open(marker, 'w') as f: f.write('ok')\n"
    )

    res = _run_teardown(
        env={
            "CI": "",
            "GITHUB_ACTIONS": "",
            "PLAYWRIGHT_ENABLE_TRACE": "1",
            "ODYSSEUS_TEST_PYTHON": sys.executable,
        },
        options={
            "resultsDir": str(spaced_root),
            "sanitizerScript": str(spaced_script),
        },
    )
    assert res.returncode == 0
    assert (spaced_root / "executed.marker").exists()


@pytest.mark.parametrize("payload", [
    '$(touch "{marker}")',
    '`touch "{marker}"`',
    '; touch "{marker}"',
    '&& touch "{marker}"',
    '|| touch "{marker}"',
    '| touch "{marker}"',
    '& touch "{marker}"',
    '> "{marker}"',
])
def test_teardown_synthetic_command_substitution_and_metacharacters(tmp_path, payload):
    """Untrusted environment strings in ODYSSEUS_TEST_PYTHON cannot execute shell commands.

    With execFileSync and shell: false, any shell metacharacters and command substitutions
    are treated strictly as literal filename characters for execve, resulting in ENOENT
    without invoking any subshell or creating the marker file.
    """
    marker = tmp_path / "shell_pwned_marker.txt"
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    trace_file = results_dir / "trace.zip"
    trace_file.write_bytes(b"PK0304 trace")

    formatted_payload = f"python {payload.format(marker=str(marker))}"

    res = _run_teardown(
        env={
            "CI": "",
            "GITHUB_ACTIONS": "",
            "PLAYWRIGHT_ENABLE_TRACE": "1",
            "ODYSSEUS_TEST_PYTHON": formatted_payload,
        },
        options={"resultsDir": str(results_dir)},
    )
    assert res.returncode == 0
    # The shell marker MUST NOT have been created!
    assert not marker.exists(), f"Security failure: shell command executed for payload {payload}!"
    # Fail closed: trace must be purged because python execution failed
    assert not trace_file.exists()


def test_teardown_rejects_unconfined_data_directory_environment_variable(tmp_path):
    """Untrusted PHOTO_EDITOR_E2E_DATA_DIR pointing outside OS temp or lacking expected prefix is ignored."""
    protected_file = tmp_path / "important_data.txt"
    protected_file.write_text("do not delete")

    # Target outside tmp or missing odysseus-photo-editor-e2e- prefix
    res = _run_teardown(env={
        "PHOTO_EDITOR_E2E_DATA_DIR": str(tmp_path),
    })
    assert res.returncode == 0
    # Must NOT have been deleted
    assert protected_file.exists(), "Security failure: unconfined data directory was deleted!"


def test_teardown_rejects_unconfined_programmatic_paths():
    """Programmatic options pointing outside allowed test roots are rejected fail-closed."""
    # Attempting to target /etc or home root
    res = _run_teardown(options={"resultsDir": "/etc"})
    assert res.returncode == 1
    assert "Security rejection" in res.stderr
