"""Regression tests for #7079 sensitive-path write and bash mutation guard."""
from __future__ import annotations

import json
import os
import pytest

from src.agent_runtime.path_policy import (
    find_sensitive_shell_mutation,
    load_sensitive_mutation_config,
    match_protected_mutation_path,
)
from src.agent_tools.filesystem_tools import ApplyPatchTool, EditFileTool, WriteFileTool
from src.agent_tools.subprocess_tools import BashTool
from src.tool_execution import _active_workspace


@pytest.fixture(autouse=True)
def isolated_sensitive_config(tmp_path, monkeypatch):
    """Ensure tests run without picking up an operator config unless explicitly created."""
    cfg_path = tmp_path / "nonexistent_sensitive_paths.json"
    monkeypatch.setenv("ODYSSEUS_SENSITIVE_PATHS_CONFIG", str(cfg_path))
    return cfg_path


# ── Path boundary & config behavior ────────────────────────────────────────

def test_default_config_enabled_without_file():
    enabled, paths = load_sensitive_mutation_config()
    assert enabled is True
    assert paths == ("/etc", "~/.ssh", "~/.gnupg")


def test_boundary_matching_not_substring(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("USERPROFILE", str(fake_home))

    # Protected targets
    assert match_protected_mutation_path("/etc") is not None
    assert match_protected_mutation_path("/etc/passwd") is not None
    assert match_protected_mutation_path("/etc/nginx/nginx.conf") is not None
    assert match_protected_mutation_path("~/.ssh") is not None
    assert match_protected_mutation_path("~/.ssh/authorized_keys") is not None
    assert match_protected_mutation_path("~/.gnupg") is not None
    assert match_protected_mutation_path("~/.gnupg/pubring.kbx") is not None

    # Sibling names sharing prefixes must NOT match (boundary check, not substring)
    assert match_protected_mutation_path("/etcetera/config") is None
    assert match_protected_mutation_path("/tmp/etc/hosts") is None
    assert match_protected_mutation_path("~/.ssh_backup/id_rsa") is None
    assert match_protected_mutation_path("~/.gnupg2/trustdb.gpg") is None


def test_config_extra_paths_and_disable(isolated_sensitive_config, tmp_path):
    secret_dir = tmp_path / "corp_secrets"
    secret_dir.mkdir()
    target = secret_dir / "token.txt"

    # Before config file: custom path is not protected
    assert match_protected_mutation_path(str(target)) is None

    # Add extra_paths via config file
    isolated_sensitive_config.write_text(
        json.dumps({"extra_paths": [str(secret_dir)]}),
        encoding="utf-8",
    )
    assert match_protected_mutation_path(str(target)) is not None
    assert match_protected_mutation_path("/etc/hosts") is not None

    # Set enabled: false via config file
    isolated_sensitive_config.write_text(
        json.dumps({"enabled": False, "extra_paths": [str(secret_dir)]}),
        encoding="utf-8",
    )
    assert match_protected_mutation_path(str(target)) is None
    assert match_protected_mutation_path("/etc/hosts") is None
    assert find_sensitive_shell_mutation("rm -rf /etc/hosts") is None


# ── File tools: write_file, edit_file, apply_patch ─────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["/etc/hosts", "~/.ssh/authorized_keys", "~/.gnupg/gpg.conf"])
async def test_write_file_blocks_protected_paths(target):
    tool = WriteFileTool()
    res = await tool.execute(json.dumps({"path": target, "content": "malicious"}), {})
    assert res["exit_code"] == 1
    assert "error" in res


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["/etc/hosts", "~/.ssh/config", "~/.gnupg/gpg-agent.conf"])
async def test_edit_file_blocks_protected_paths(target):
    tool = EditFileTool()
    res = await tool.execute(
        json.dumps({"path": target, "old_string": "a", "new_string": "b"}),
        {},
    )
    assert res["exit_code"] == 1
    assert "error" in res


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["/etc/hosts", "~/.ssh/known_hosts", "~/.gnupg/pubring.kbx"])
async def test_apply_patch_blocks_protected_paths(target):
    patch_text = "\n".join([
        "*** Begin Patch",
        f"*** Add File: {target}",
        "+injected line",
        "*** End Patch",
    ])
    tool = ApplyPatchTool()
    res = await tool.execute(patch_text, {})
    assert res["exit_code"] == 1
    assert "error" in res


@pytest.mark.asyncio
async def test_workspace_symlink_to_protected_path_blocked(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    fake_home = tmp_path / "home"
    ssh_dir = fake_home / ".ssh"
    ssh_dir.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("USERPROFILE", str(fake_home))

    link = ws / "ssh_escape"
    try:
        os.symlink(str(ssh_dir), str(link), target_is_directory=True)
    except OSError:
        pytest.skip("Symlink creation requires elevated privileges on this Windows host")

    token = _active_workspace.set(str(ws))
    try:
        res = await WriteFileTool().execute(
            json.dumps({"path": "ssh_escape/new_key", "content": "ssh-ed25519 AAAA"}),
            {},
        )
        assert res["exit_code"] == 1
        assert not (ssh_dir / "new_key").exists()
    finally:
        _active_workspace.reset(token)


# ── Shell (bash) mutation detection & wrapper unwrapping ───────────────────

@pytest.mark.parametrize(
    "cmd",
    [
        "rm -rf /etc/nginx/sites-enabled/default",
        "mv /tmp/evil ~/.ssh/authorized_keys",
        "cp /tmp/evil ~/.gnupg/gpg.conf",
        "echo 'ssh-ed25519 AAAA' >> ~/.ssh/authorized_keys",
        "cat /tmp/hosts > /etc/hosts",
        "sudo rm -f /etc/hosts",
        "sudo -u root mv /tmp/x /etc/crontab",
        "env FOO=bar rm -rf ~/.ssh/id_rsa",
        "env -i PATH=/usr/bin touch ~/.gnupg/lock",
        "nice -n 10 rm /etc/motd",
        "printf '/etc/passwd\n' | xargs rm -f",
        "xargs -0 -n 1 rm ~/.ssh/config",
        "timeout 5 tee /etc/profile",
        "nohup chmod 777 ~/.ssh/id_rsa",
        "sed -i 's/foo/bar/' /etc/hosts",
        "dd if=/dev/zero of=/etc/shadow bs=1 count=1",
        "sh -c 'rm -f ~/.gnupg/trustdb.gpg'",
        "echo ok && mv /tmp/a /etc/hostname",
    ],
)
def test_shell_mutations_blocked(cmd, tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    fake_home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("USERPROFILE", str(fake_home))

    reason = find_sensitive_shell_mutation(cmd, cwd=str(tmp_path))
    assert reason is not None, f"Expected mutation refusal for: {cmd}"


@pytest.mark.parametrize(
    "cmd",
    [
        "cat /etc/os-release",
        "ls -la ~/.ssh",
        "grep -i localhost /etc/hosts",
        "rm -rf /tmp/scratch_file",
        "mv ./foo.txt /etcetera/foo.txt",
        "echo hello > /tmp/output.txt",
        "sed 's/foo/bar/' /etc/hosts",
    ],
)
def test_shell_readonly_and_non_sensitive_mutations_allowed(cmd, tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    fake_home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("USERPROFILE", str(fake_home))

    assert find_sensitive_shell_mutation(cmd, cwd=str(tmp_path)) is None


@pytest.mark.asyncio
async def test_bash_tool_blocks_before_process_launch(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("USERPROFILE", str(fake_home))

    res = await BashTool().execute("env FOO=1 nice -n 5 rm -f ~/.ssh/id_rsa", {})
    assert res["exit_code"] == 1
    assert res.get("blocked") is True
    assert "refused mutating sensitive path" in res["error"]
