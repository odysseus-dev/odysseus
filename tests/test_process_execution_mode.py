"""Process authority, capability, and explicit Full Access regressions."""

import asyncio
import importlib

import pytest


def _process_execution():
    return importlib.import_module("src.process_execution")


def _subprocess_tools():
    return importlib.import_module("src.agent_tools.subprocess_tools")


def _capability(pe, *, sandbox=True, sandbox_broker=True, full=True, full_broker=True):
    return pe.ProcessCapability(
        pe.ProfileCapability(
            sandbox,
            "" if sandbox else "sandbox unavailable",
            sandbox_broker,
            "" if sandbox_broker else "sandbox broker unavailable",
        ),
        pe.ProfileCapability(
            full,
            "" if full else "full access unavailable",
            full_broker,
            "" if full_broker else "full access broker unavailable",
        ),
        1.0,
    )


def test_invalid_mode_value_fails_safe_to_sandbox():
    pe = _process_execution()
    assert (
        pe.process_execution_mode_from_value("unexpected")
        is pe.ProcessExecutionMode.SANDBOX
    )


def test_full_access_is_temporary_and_retains_network_policy():
    pe = _process_execution()
    warning = pe.FULL_ACCESS_WARNING
    assert "mounted volumes" in warning
    assert "networkless by default" in warning
    assert "HTTP(S) broker" in warning
    assert "reset to Sandbox" in warning
    assert "already-running Full Access process retains" in warning


def test_full_access_requires_exact_confirmation_and_resets():
    pe = _process_execution()
    pe.reset_process_execution_mode()

    with pytest.raises(ValueError, match="confirmation"):
        pe.set_process_execution_mode(
            pe.ProcessExecutionMode.FULL_ACCESS,
            confirmation="yes",
        )

    pe.set_process_execution_mode(
        pe.ProcessExecutionMode.FULL_ACCESS,
        confirmation=pe.FULL_ACCESS_CONFIRMATION,
    )
    assert pe.configured_process_execution_mode() is pe.ProcessExecutionMode.FULL_ACCESS

    pe.reset_process_execution_mode()
    assert pe.configured_process_execution_mode() is pe.ProcessExecutionMode.SANDBOX


def test_process_capability_is_cached(monkeypatch):
    pe = _process_execution()
    calls = []
    status = _capability(pe)
    monkeypatch.setattr(
        pe,
        "_probe_process_capability",
        lambda: calls.append(True) or status,
    )
    pe.clear_process_capability_cache()

    assert pe.process_capability() is status
    assert pe.process_capability() is status
    assert calls == [True]


def test_capability_distinguishes_modes_and_brokered_profile(monkeypatch, tmp_path):
    pe = _process_execution()
    calls = []
    monkeypatch.setattr(
        "src.constants.AGENT_WORKSPACE_DIR",
        str(tmp_path / "agent-workspace"),
    )

    def fake_probe_one_mode(_workspace, *, full_access):
        calls.append(full_access)
        if full_access:
            return pe.ProfileCapability(True, "", False, "full broker unavailable")
        return pe.ProfileCapability(True, "", True, "")

    monkeypatch.setattr(pe, "_probe_one_mode", fake_probe_one_mode)
    status = pe._probe_process_capability()

    assert status.sandbox.networkless is True
    assert status.sandbox.brokered is True
    assert status.full_access.networkless is True
    assert status.full_access.brokered is False
    assert status.full_access.brokered_reason == "full broker unavailable"
    assert calls == [False, True]


class _FakeProcess:
    returncode = 0
    stdout = None
    stderr = None

    async def wait(self):
        return 0

    def kill(self):
        return None


@pytest.mark.asyncio
async def test_bash_blocks_when_sandbox_probe_fails(monkeypatch, tmp_path):
    pe = _process_execution()
    st = _subprocess_tools()
    monkeypatch.setattr(
        st,
        "configured_process_execution_mode",
        lambda: pe.ProcessExecutionMode.SANDBOX,
    )
    monkeypatch.setattr(
        st,
        "process_capability",
        lambda: _capability(pe, sandbox=False, sandbox_broker=False),
    )
    monkeypatch.setattr("src.tool_execution.agent_cwd", lambda: str(tmp_path))

    async def unexpected_spawn(*_args, **_kwargs):
        raise AssertionError("blocked Sandbox mode must not spawn a process")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", unexpected_spawn)

    result = await st.BashTool().execute("echo blocked", {})

    assert result["blocked"] is True
    assert result["execution_mode"] == "sandbox"
    assert "sandbox unavailable" in result["error"]


@pytest.mark.asyncio
async def test_brokered_process_blocks_when_only_networkless_probe_passes(
    monkeypatch,
    tmp_path,
):
    pe = _process_execution()
    st = _subprocess_tools()
    from src.execution_sandbox import SandboxNetworkProfile

    monkeypatch.setattr(
        st,
        "configured_process_execution_mode",
        lambda: pe.ProcessExecutionMode.SANDBOX,
    )
    monkeypatch.setattr(
        st,
        "process_capability",
        lambda: _capability(pe, sandbox=True, sandbox_broker=False),
    )
    monkeypatch.setattr("src.tool_execution.agent_cwd", lambda: str(tmp_path))

    result = await st.BashTool().execute(
        "echo blocked",
        {"network_profile": SandboxNetworkProfile.BROKERED_ONLY},
    )

    assert result["blocked"] is True
    assert "sandbox broker unavailable" in result["error"]


@pytest.mark.asyncio
async def test_full_access_bash_is_one_shot_but_retains_brokered_network(
    monkeypatch,
    tmp_path,
):
    pe = _process_execution()
    st = _subprocess_tools()
    from src.execution_sandbox import SandboxNetworkProfile

    spawned = {}
    monkeypatch.setattr(
        st,
        "configured_process_execution_mode",
        lambda: pe.ProcessExecutionMode.FULL_ACCESS,
    )
    monkeypatch.setattr(st, "process_capability", lambda: _capability(pe))
    monkeypatch.setattr(
        st,
        "full_access_command",
        lambda argv, **_kwargs: ["/trusted/bwrap-full", *argv],
    )
    monkeypatch.setattr("src.tool_execution.agent_cwd", lambda: str(tmp_path))
    monkeypatch.setattr(st.shutil, "which", lambda _name: "/usr/bin/tmux")

    async def fake_spawn(*argv, **kwargs):
        spawned["argv"] = argv
        spawned["kwargs"] = kwargs
        return _FakeProcess()

    async def fake_stream(*_args, **_kwargs):
        return "ok", "", 0, False

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_spawn)
    monkeypatch.setattr(st, "_run_subprocess_streaming", fake_stream)
    monkeypatch.setattr(
        st,
        "_run_tmux_bash",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Full Access must not create a persistent tmux shell")
        ),
    )

    result = await st.BashTool().execute(
        "echo ok",
        {
            "session_id": "chat-1",
            "network_profile": SandboxNetworkProfile.BROKERED_ONLY,
        },
    )

    assert spawned["argv"][:2] == ("/trusted/bwrap-full", "/bin/bash")
    assert spawned["kwargs"]["env"] == {}
    assert result["execution_mode"] == "full_access"
    assert result["network_enforcement"] == "brokered_http_https"
    assert result["warning"] == pe.FULL_ACCESS_WARNING


@pytest.mark.asyncio
async def test_unverifiable_preexisting_tmux_session_is_recreated(monkeypatch, tmp_path):
    st = _subprocess_tools()
    name = "ody-agent-sbx-v2-chat-workspace-network-policy"
    killed = []
    created = []
    has_session_results = iter([True, True])
    st._TMUX_OWNED_SESSIONS.clear()

    async def fake_has_session(_name):
        return next(has_session_results)

    async def fake_kill(session_name):
        killed.append(session_name)
        st._TMUX_OWNED_SESSIONS.discard(session_name)

    async def fake_run(*args, **_kwargs):
        if args[:2] == ("tmux", "new-session"):
            created.append(args)
        return "", "", 0

    monkeypatch.setattr(st, "_tmux_has_session", fake_has_session)
    monkeypatch.setattr(st, "_tmux_kill_session", fake_kill)
    monkeypatch.setattr(st, "_run_exec", fake_run)
    monkeypatch.setattr(st.os.path, "isfile", lambda _path: True)

    await st._ensure_tmux_session(
        name,
        str(tmp_path),
        ["/trusted/sandbox-shell"],
    )

    assert killed == [name]
    assert created
    assert name in st._TMUX_OWNED_SESSIONS
