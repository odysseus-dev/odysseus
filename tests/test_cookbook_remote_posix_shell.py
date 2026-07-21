"""Remote Cookbook commands must be forced through `sh -c`.

sshd hands the remote command line to the target user's login shell. A
non-POSIX login shell (fish, csh) rejects `VAR=value` assignments,
`export`, and `if ...; then` — and `set -e` even means something else in
fish — so raw POSIX snippets died before the first real command ran
(system-deps install, binary probes, tmux launch/status on such hosts).
"""
import shlex
import shutil
import subprocess

import pytest

from core.platform_compat import posix_remote_shell_cmd

# One snippet exercising every construct fish cannot parse.
POSIX_SNIPPET = 'PATH="/x:$PATH"; FOO="$(echo hi)"; if [ -n "$FOO" ]; then echo "$FOO"; fi'


def test_wraps_as_single_sh_c_invocation():
    parts = shlex.split(posix_remote_shell_cmd(POSIX_SNIPPET))
    assert parts == ["sh", "-c", POSIX_SNIPPET]


def test_double_layer_survives_local_shell_then_remote_shell():
    # String-composed call sites embed the wrapped command behind one more
    # shlex.quote: the local shell strips that layer, ssh forwards the rest
    # verbatim, and the remote login shell must see `sh -c '<snippet>'`.
    local_cmd = f"ssh host {shlex.quote(posix_remote_shell_cmd(POSIX_SNIPPET))}"
    local_parts = shlex.split(local_cmd)
    assert local_parts[:2] == ["ssh", "host"]
    assert shlex.split(local_parts[2]) == ["sh", "-c", POSIX_SNIPPET]


_HAS_FISH = shutil.which("fish") is not None


@pytest.mark.skipif(not _HAS_FISH, reason="fish not installed")
def test_fish_login_shell_executes_wrapped_snippet():
    # Emulate sshd: it invokes the login shell with the remote command line.
    raw = subprocess.run(
        ["fish", "-c", POSIX_SNIPPET], capture_output=True, text=True, timeout=15
    )
    assert raw.returncode != 0  # the unwrapped form is exactly the reported bug
    wrapped = subprocess.run(
        ["fish", "-c", posix_remote_shell_cmd(POSIX_SNIPPET)],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert wrapped.returncode == 0, wrapped.stderr
    assert wrapped.stdout.strip() == "hi"


def test_remote_tmux_builders_are_wrapped():
    from routes.cookbook_routes import _remote_tmux_command, _remote_tmux_launch_command

    for built in (
        _remote_tmux_command("ls"),
        _remote_tmux_launch_command("sess1", ".sess1_run.sh"),
    ):
        parts = shlex.split(built)
        assert parts[:2] == ["sh", "-c"]
        assert "ODYSSEUS_TMUX" in parts[2]
