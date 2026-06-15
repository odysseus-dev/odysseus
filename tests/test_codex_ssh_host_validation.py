"""The Codex cookbook bridge resolves a task's SSH target (remoteHost / sshPort)
from cookbook_state.json and interpolates it into an ``ssh ...`` command string
that runs through a shell. The command body is shlex-quoted, but the host and
port were not validated, so a tampered task entry carrying shell metacharacters
in ``remoteHost`` would be injected into that command.

These pin validation on the host/port before they reach the ssh string, matching
the validators the rest of the cookbook routes already apply.
"""
import pytest
from fastapi import HTTPException

import routes.codex_routes as codex_routes


def test_rejects_remote_host_with_shell_metacharacters():
    task = {"remoteHost": "box; rm -rf ~", "sshPort": ""}
    with pytest.raises(HTTPException) as exc:
        codex_routes._ssh_prefix_for_task(task)
    assert exc.value.status_code == 400


def test_rejects_non_numeric_ssh_port():
    task = {"remoteHost": "box", "sshPort": "22; evil"}
    with pytest.raises(HTTPException) as exc:
        codex_routes._ssh_prefix_for_task(task)
    assert exc.value.status_code == 400


def test_local_task_has_no_host():
    host, port_flag = codex_routes._ssh_prefix_for_task({})
    assert host == ""
    assert port_flag == ""


def test_valid_remote_builds_port_flag():
    host, port_flag = codex_routes._ssh_prefix_for_task(
        {"remoteHost": "user@box", "sshPort": "2222"}
    )
    assert host == "user@box"
    assert port_flag == "-p 2222 "


def test_default_ssh_port_omits_flag():
    host, port_flag = codex_routes._ssh_prefix_for_task(
        {"remoteHost": "box", "sshPort": "22"}
    )
    assert host == "box"
    assert port_flag == ""
