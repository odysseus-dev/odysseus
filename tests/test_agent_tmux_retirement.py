"""Native Bash does not resurrect tmux; legacy cleanup requires identity."""
import asyncio
from types import SimpleNamespace

import pytest

from src import containment, process_ownership, process_reaper, tool_execution
from src.agent_tools import subprocess_tools
from src.constants import DATA_DIR
from tests.containment_helpers import capture_owned_spawn


async def test_a_chat_session_always_uses_the_owned_runner(monkeypatch, tmp_path):
    captured = capture_owned_spawn(monkeypatch, tmp_path)
    monkeypatch.setattr(tool_execution, "agent_cwd", lambda: str(tmp_path))
    original = subprocess_tools.shutil.which
    monkeypatch.setattr(subprocess_tools.shutil, "which", lambda name: "/fake/tmux" if name == "tmux" else original(name))
    async def forbidden(*args, **kwargs):
        pytest.fail("native Bash resurrected a persistent tmux shell")
    monkeypatch.setattr(subprocess_tools.asyncio, "create_subprocess_shell", forbidden)
    from tests.process_resource_helpers import authorized_handler
    result = await authorized_handler(subprocess_tools.BashTool().execute, tmp_path)("printf ok", {"session_id": "same-chat"})
    assert result["output"] == "ok"
    assert result["teardown"]["dead"] is True
    assert "tmux_session" not in result
    assert captured["kwargs"].get("start_new_session") is True


@pytest.fixture
def legacy(monkeypatch, tmp_path):
    import shlex
    import subprocess
    import shutil
    monkeypatch.setattr(containment, "_store_path", lambda: tmp_path / "grants.json")
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/tmux" if name == "tmux" else None)
    launcher = f"env HOME={shlex.quote(DATA_DIR)} TERM=xterm-256color /bin/bash --noprofile --norc"
    row = f"$8\tody-agent-chat\t1234\t4200\t%9\t4100\t{launcher}\n"
    state = {"rows": row, "calls": [], "released": []}
    def run(argv, **kwargs):
        state["calls"].append(argv)
        if argv[1] == "list-panes":
            return SimpleNamespace(returncode=0, stdout=state["rows"], stderr="")
        if argv[1] == "kill-session":
            state["rows"] = ""
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(process_ownership, "process_table", lambda: {
        4200: process_ownership.ProcessInfo(4200, 4100, "/bin/bash --noprofile --norc"),
        4201: process_ownership.ProcessInfo(4201, 4200, "sleep 60"),
    })
    monkeypatch.setattr(process_ownership, "start_token", lambda pid: f"token:{pid}")
    monkeypatch.setattr(process_ownership, "verify", lambda pid, token: process_ownership.OWNED)
    monkeypatch.setattr(containment, "_pgid_of", lambda pid: pid)
    def release(grant, **kwargs):
        state["released"].append((grant.pid, kwargs))
        return containment.ReleaseOutcome(dead=True, escalated=False)
    monkeypatch.setattr(containment, "release", release)
    return state


def test_legacy_cleanup_checks_home_and_identity_and_kills_children_first(legacy):
    report = process_reaper.reap_legacy_agent_tmux()
    assert report["torn_down"] == 1
    assert [pid for pid, _ in legacy["released"]] == [4201, 4200]
    assert all(options["require_identity"] for _, options in legacy["released"])
    assert legacy["calls"][-2][1:] == ["kill-session", "-t", "$8"]


def test_a_name_prefix_alone_never_authorizes_cleanup(legacy):
    legacy["rows"] = "$8\tody-agent-chat\t1234\t4200\t%9\t4100\t/bin/bash\n"
    report = process_reaper.reap_legacy_agent_tmux()
    assert report["unverifiable"] == 1
    assert legacy["released"] == []
    assert all(call[1] != "kill-session" for call in legacy["calls"])


def test_a_recycled_pane_pid_is_never_signalled(legacy, monkeypatch):
    monkeypatch.setattr(process_ownership, "verify", lambda pid, token: process_ownership.FOREIGN if pid == 4200 else process_ownership.OWNED)
    report = process_reaper.reap_legacy_agent_tmux()
    assert report["unverifiable"] == 1
    assert legacy["released"] == []


def test_a_stale_pane_pid_pointing_at_another_parent_is_not_signalled(legacy, monkeypatch):
    monkeypatch.setattr(process_ownership, "process_table", lambda: {
        4200: process_ownership.ProcessInfo(4200, 9999, "/bin/bash --noprofile --norc"),
    })
    assert process_reaper.reap_legacy_agent_tmux()["unverifiable"] == 1
    assert legacy["released"] == []


def test_a_session_changed_during_discovery_is_never_killed(legacy, monkeypatch):
    original = process_ownership.process_table
    def table():
        legacy["rows"] = legacy["rows"].replace("1234", "5678")
        return original()
    monkeypatch.setattr(process_ownership, "process_table", table)
    report = process_reaper.reap_legacy_agent_tmux()
    assert report["unverifiable"] == 1
    assert legacy["released"] == []


def test_startup_reaper_does_not_kill_a_current_runtime_grant(tmp_path, monkeypatch):
    monkeypatch.setattr(containment, "_store_path", lambda: tmp_path / "grants.json")
    monkeypatch.setattr(containment, "CONTAINMENT_MODE", containment.MODE_REPORT_ONLY)
    grant = containment.acquire(containment.agent_spec(str(tmp_path), {}, 1), owner="active-chat")
    monkeypatch.setattr(containment, "reap_record", lambda record: pytest.fail("startup killed current execution"))
    assert process_reaper.reap_containment_grants()["manager_kept"] == 1
    containment.release(grant)


def test_legacy_cleanup_against_a_private_real_tmux_server(tmp_path, monkeypatch):
    import os
    import shlex
    import shutil
    import subprocess
    real_tmux = shutil.which("tmux")
    if os.name == "nt" or not real_tmux:
        pytest.skip("requires POSIX tmux")
    socket = str(tmp_path / "tmux.sock")
    wrapper = tmp_path / "tmux"
    wrapper.write_text(f"#!/bin/sh\nexec {shlex.quote(real_tmux)} -S {shlex.quote(socket)} \"$@\"\n")
    wrapper.chmod(0o700)
    subprocess.run([real_tmux, "-S", socket, "-f", "/dev/null", "new-session", "-d", "-s", "ody-agent-real",
                    "env", f"HOME={DATA_DIR}", "TERM=xterm-256color", "/bin/bash", "--noprofile", "--norc"], check=True)
    original = shutil.which
    monkeypatch.setattr(shutil, "which", lambda name: str(wrapper) if name == "tmux" else original(name))
    monkeypatch.setattr(containment, "_store_path", lambda: tmp_path / "grants.json")
    try:
        report = process_reaper.reap_legacy_agent_tmux()
        assert report["torn_down"] == 1, report
        assert subprocess.run([real_tmux, "-S", socket, "has-session", "-t", "ody-agent-real"], capture_output=True).returncode != 0
        assert containment.active_grants() == []
    finally:
        subprocess.run([real_tmux, "-S", socket, "kill-server"], capture_output=True)


def test_a_descendant_reissued_after_the_table_read_is_never_released(legacy, monkeypatch):
    reads = {"n": 0}

    def table():
        reads["n"] += 1
        rows = {4200: process_ownership.ProcessInfo(4200, 4100, "/bin/bash --noprofile --norc")}
        if reads["n"] <= 2:
            rows[4201] = process_ownership.ProcessInfo(4201, 4200, "sleep 60")
        else:
            # The child exited and its pid now names an unrelated process.
            rows[4201] = process_ownership.ProcessInfo(4201, 1, "sshd: stranger")
        return rows

    monkeypatch.setattr(process_ownership, "process_table", table)
    process_reaper.reap_legacy_agent_tmux()
    assert 4201 not in [pid for pid, _ in legacy["released"]]


def test_an_unidentifiable_descendant_keeps_the_session_unsignalled(legacy, monkeypatch):
    def start_token(pid):
        if pid == 4201:
            raise process_ownership.InspectionUnavailable("/proc/4201/stat")
        return f"token:{pid}"

    monkeypatch.setattr(process_ownership, "start_token", start_token)
    report = process_reaper.reap_legacy_agent_tmux()
    assert report["unverifiable"] == 1
    assert legacy["released"] == []
    assert all(call[1] != "kill-session" for call in legacy["calls"])
