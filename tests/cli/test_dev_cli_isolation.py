"""The isolation contract of `odysseus dev`.

The launcher exists so that two checkouts on one machine cannot share
runtime state by accident. Every test here pins one of the guarantees
that makes that true: derived ports never land on a port the project
already means something by, a ChromaDB we did not start is refused
rather than adopted, a checkout wired into a service manager is not
bootable, and nothing is signalled on the strength of a pid alone.
"""
import argparse
import os
import socket

import pytest

from tests.helpers.cli_loader import load_script


@pytest.fixture
def cli():
    return load_script("odysseus-dev")


@pytest.fixture
def worktree(tmp_path):
    """A directory shaped enough like a checkout for the launcher to accept it."""
    for marker in ("app.py", "setup.py", "requirements.txt"):
        (tmp_path / marker).write_text("")
    (tmp_path / "venv" / "bin").mkdir(parents=True)
    (tmp_path / "venv" / "bin" / "python").write_text("")
    return tmp_path


def up_args(**overrides):
    defaults = dict(
        port=None, chroma_port=None, no_chroma=False, venv=None, from_pr=None,
        remote="origin", foreground=False, timeout=5, pretty=False,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def test_derived_ports_are_stable_distinct_and_never_reserved(cli):
    first = cli.derive_ports("/checkouts/alpha")
    assert first == cli.derive_ports("/checkouts/alpha")
    assert first != cli.derive_ports("/checkouts/beta")
    assert first["chroma"] == first["app"] + 1
    assert first["test_static"] == first["app"] + 2

    # No path can derive onto a port the project already owns — 7860 is a
    # normal start-macos.sh launch, 8100 somebody else's vector store.
    for index in range(500):
        for port in cli.derive_ports(f"/checkouts/w{index}").values():
            assert cli.reserved_reason(port) is None, port


def test_reserved_ports_are_refused_even_when_asked_for(cli, worktree, monkeypatch):
    monkeypatch.chdir(worktree)
    with pytest.raises(SystemExit):
        cli.resolve_ports(worktree, up_args(port=7860))
    with pytest.raises(SystemExit):
        cli.resolve_ports(worktree, up_args(chroma_port=8100))


def test_root_is_resolved_from_the_working_directory(cli, worktree):
    nested = worktree / "static" / "js"
    nested.mkdir(parents=True)
    assert cli.find_repo_root(nested) == worktree.resolve()
    assert cli.find_repo_root(worktree.parent) is None


def test_a_checkout_run_by_a_service_manager_is_not_bootable(cli, worktree, monkeypatch):
    units = worktree.parent / "units"
    units.mkdir()
    (units / "com.odysseus.server.plist").write_text(
        f"<plist><string>{worktree.resolve()}/start-macos.sh</string></plist>"
    )
    assert cli.managed_by_service(worktree, unit_dirs=[units]).endswith(".plist")
    unrelated = worktree.parent / "somewhere-else"
    unrelated.mkdir()
    assert cli.managed_by_service(unrelated, unit_dirs=[units]) is None

    monkeypatch.chdir(worktree)
    monkeypatch.setattr(cli, "service_unit_dirs", lambda: [units])
    with pytest.raises(SystemExit):
        cli.cmd_up(up_args())


def test_a_chromadb_we_did_not_start_is_refused_not_adopted(cli, worktree, monkeypatch):
    monkeypatch.chdir(worktree)
    monkeypatch.setattr(cli, "service_unit_dirs", list)
    ports = cli.derive_ports(worktree)

    foreign = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    foreign.bind(("127.0.0.1", 0))
    ports["chroma"] = foreign.getsockname()[1]
    # Port derivation is covered above. This refusal test owns a held socket
    # rather than depending on a derived port being free on the host.
    monkeypatch.setattr(cli, "derive_ports", lambda _root: ports)
    foreign.listen(1)
    try:
        with pytest.raises(SystemExit):
            cli.cmd_up(up_args())
    finally:
        foreign.close()

    # Refused means refused: nothing was started and no state was recorded.
    assert not cli.state_path(worktree).exists()


def test_no_chroma_points_the_app_at_a_port_nothing_answers(cli):
    port = cli.unused_port()
    assert not cli.port_bound(port)
    assert cli.reserved_reason(port) is None


def test_every_chroma_outcome_leaves_the_app_off_a_port_we_do_not_own(cli, worktree):
    """The decision has three endings and none of them is "use theirs"."""
    ports = cli.derive_ports(worktree)
    data, logs = worktree / "data", worktree / "logs"
    data.mkdir()
    logs.mkdir()
    venv_python = worktree / "venv" / "bin" / "python"

    # 1. Asked to go without: a port nothing answers on, not the derived
    # one, which is where a foreign server may appear later.
    entry, port, note = cli.resolve_chroma(
        up_args(no_chroma=True), {}, ports, venv_python, data, logs
    )
    assert (entry, port != ports["chroma"], cli.port_bound(port)) == (None, True, False)
    assert "no-chroma" in note

    # 2. No server to start (this venv has no `chroma` binary, which is
    # the stock requirements.txt): keyword mode, and again not the
    # derived port.
    entry, port, note = cli.resolve_chroma(
        up_args(), {}, ports, venv_python, data, logs
    )
    assert entry is None and port != ports["chroma"]
    assert "keyword-only" in note


def test_a_pid_is_never_trusted_without_its_command_line(cli, worktree, monkeypatch):
    own_pid = os.getpid()
    assert cli.pid_is_ours(own_pid, ["definitely-not-in-this-command-line"]) is False
    assert cli.pid_is_ours(None, []) is False
    assert cli.pid_is_ours(own_pid, [cli.pid_command(own_pid).split()[0]]) is True

    # `down` must not signal a live process whose fingerprints disagree —
    # here, this very test run — and must keep the record so the pid can
    # be investigated rather than lost.
    cli.write_state(worktree, {"app": {"pid": own_pid, "fingerprints": ["uvicorn --port 1"]}})
    monkeypatch.chdir(worktree)
    monkeypatch.setattr(os, "kill", _forbidden_kill)
    cli.cmd_down(up_args())
    assert cli.state_path(worktree).exists()


def test_down_forgets_an_instance_that_is_gone(cli, worktree, monkeypatch):
    cli.write_state(worktree, {"app": {"pid": 2 ** 31 - 1, "fingerprints": ["uvicorn"]}})
    monkeypatch.chdir(worktree)
    cli.cmd_down(up_args())
    assert not cli.state_path(worktree).exists()


def _forbidden_kill(pid, sig):
    """Liveness probes (signal 0) are fine; anything that would actually
    reach the process is the failure this test is about."""
    if sig == 0:
        return None
    raise AssertionError(f"cmd_down sent signal {sig} to a process it does not own")
