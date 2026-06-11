"""The generated Ollama runner must print the install hint, not execute it.

The runner script emitted by /api/model/serve contained:

    echo "ERROR: Ollama not found ... or `curl -fsSL .../install.sh | sh`."

Backticks inside double quotes are bash command substitution, so on any host
without ollama the script downloaded and ran the system-wide installer
(including remote SSH serve targets) instead of printing the hint. The hint
now lives in OLLAMA_MISSING_HINT, contains no substitution tokens, and is
emitted single-quoted.
"""
import os
import shutil
import subprocess

import pytest

from routes.cookbook_helpers import OLLAMA_MISSING_HINT, _bash_squote

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_hint_has_no_shell_expansion_tokens():
    assert "`" not in OLLAMA_MISSING_HINT
    assert "$(" not in OLLAMA_MISSING_HINT


def test_hint_still_tells_the_user_how_to_install():
    assert "https://ollama.com/download" in OLLAMA_MISSING_HINT
    assert "install.sh" in OLLAMA_MISSING_HINT


def test_no_runner_echo_line_uses_backticks_in_double_quotes():
    # Source-level guard: generated-script echo lines must never carry
    # backticks inside a double-quoted bash string again.
    src = open(os.path.join(ROOT, "routes", "cookbook_routes.py"), encoding="utf-8").read()
    offenders = [
        line.strip()
        for line in src.splitlines()
        if "append(" in line and 'echo "' in line and "`" in line.split('echo "', 1)[1]
    ]
    assert offenders == []


def test_single_quoted_echo_prints_hint_literally():
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("bash not available")
    out = subprocess.run(
        [bash, "-c", f"echo '{_bash_squote(OLLAMA_MISSING_HINT)}'"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert out.returncode == 0
    assert out.stdout.strip() == OLLAMA_MISSING_HINT
