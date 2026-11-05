"""Cookbook tmux launches give their own pane long scrollback without
changing the user's global tmux ``history-limit`` (#5300)."""
import os
import shlex
import shutil
import subprocess

import pytest

from routes.cookbook_routes import (
    _TMUX_HISTORY_LIMIT,
    _remote_tmux_launch_command,
    _tmux_new_session_command,
)

REAL_TMUX = shutil.which("tmux")
pytestmark = pytest.mark.skipif(os.name == "nt" or not REAL_TMUX, reason="requires POSIX tmux")


@pytest.fixture
def private_tmux(tmp_path):
    """A ``tmux`` wrapper bound to a private socket and config, plus a query helper."""
    socket = str(tmp_path / "tmux.sock")
    conf = tmp_path / "tmux.conf"
    conf.write_text("")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    wrapper = bindir / "tmux"
    wrapper.write_text(
        f"#!/bin/sh\nexec {shlex.quote(REAL_TMUX)} -S {shlex.quote(socket)} "
        f"-f {shlex.quote(str(conf))} \"$@\"\n"
    )
    wrapper.chmod(0o700)

    def query(*args):
        return subprocess.run([str(wrapper), *args], capture_output=True, text=True).stdout.strip()

    yield wrapper, conf, query
    subprocess.run([str(wrapper), "kill-server"], capture_output=True)


def _run(command, **kwargs):
    return subprocess.run(command, shell=True, capture_output=True, text=True, **kwargs)


def test_fresh_server_pane_gets_long_scrollback_and_config_value_survives(private_tmux):
    wrapper, conf, query = private_tmux
    conf.write_text("set -g history-limit 7777\n")
    result = _run(_tmux_new_session_command(shlex.quote(str(wrapper)), "serve-a", "sleep 60"))
    assert result.returncode == 0, result.stderr
    assert query("display-message", "-p", "-t", "serve-a", "#{history_limit}") == str(_TMUX_HISTORY_LIMIT)
    assert query("show-options", "-gv", "history-limit") == "7777"
    assert query("show-options", "-gv", "@odysseus_prev_history_limit") == ""


def test_running_server_keeps_the_users_global_history_limit(private_tmux):
    wrapper, _conf, query = private_tmux
    subprocess.run([str(wrapper), "new-session", "-d", "-s", "mine", "sleep 60", ";",
                    "set-option", "-g", "history-limit", "50000"], check=True)
    result = _run(_tmux_new_session_command(shlex.quote(str(wrapper)), "cookbook-b", "sleep 60"))
    assert result.returncode == 0, result.stderr
    assert query("display-message", "-p", "-t", "cookbook-b", "#{history_limit}") == str(_TMUX_HISTORY_LIMIT)
    assert query("show-options", "-gv", "history-limit") == "50000"


def test_tmux_without_set_option_format_still_starts_the_session(private_tmux, tmp_path):
    wrapper, _conf, query = private_tmux
    old_tmux = tmp_path / "old-tmux"
    old_tmux.write_text(
        "#!/bin/sh\n"
        "for a in \"$@\"; do [ \"$a\" = -gF ] && { echo 'unknown flag -F' >&2; exit 1; }; done\n"
        f"exec {shlex.quote(str(wrapper))} \"$@\"\n"
    )
    old_tmux.chmod(0o700)
    result = _run(_tmux_new_session_command(shlex.quote(str(old_tmux)), "serve-c", "sleep 60"))
    assert result.returncode == 0, result.stderr
    assert subprocess.run([str(wrapper), "has-session", "-t", "serve-c"]).returncode == 0


def test_remote_launch_command_uses_the_scoped_launch(private_tmux, tmp_path):
    wrapper, _conf, query = private_tmux
    home = tmp_path / "home"
    home.mkdir()
    runner = home / "runner.sh"
    runner.write_text("#!/bin/sh\nsleep 60\n")
    env = {"HOME": str(home), "PATH": f"{wrapper.parent}:/usr/bin:/bin"}
    result = _run(_remote_tmux_launch_command("serve-d", "runner.sh"), cwd=home, env=env)
    assert result.returncode == 0, result.stderr
    assert query("display-message", "-p", "-t", "serve-d", "#{history_limit}") == str(_TMUX_HISTORY_LIMIT)
    assert query("show-options", "-gv", "history-limit") == "2000"


def test_remote_launch_does_not_start_tmux_when_chmod_fails(private_tmux, tmp_path):
    wrapper, _conf, _query = private_tmux
    env = {"HOME": str(tmp_path), "PATH": f"{wrapper.parent}:/usr/bin:/bin"}
    result = _run(_remote_tmux_launch_command("serve-e", "missing.sh"), cwd=tmp_path, env=env)
    assert result.returncode != 0
    assert subprocess.run([str(wrapper), "has-session", "-t", "serve-e"], capture_output=True).returncode != 0
