"""Regression test for intentional Wave 3 refusal of unscoped remote scheduled SSH.

Contract:
Raw scheduled remote SSH without an exact external backend resource binding
must fail closed deterministically with:
"Remote scheduled workload requires an exact external backend binding."
"""
import pytest

from src.agent_runtime.authority import OperationGrant, RequestAuthority, bind_request_authority
from src.builtin_actions import _run_subprocess, action_ssh_command


@pytest.mark.asyncio
async def test_scheduled_remote_ssh_refusal_is_deterministic_and_fail_closed():
    """Unscoped remote SSH in a scheduled workload must fail closed."""
    authority = RequestAuthority("sched-1", "alice", "sched-session", "", (OperationGrant("bash"),))
    with bind_request_authority(authority):
        # 1. Direct _run_subprocess with ssh argv
        output, success = await _run_subprocess(["ssh", "user@remote.host", "uptime"])
        assert success is False
        assert output == "Remote scheduled workload requires an exact external backend binding."

        # 2. action_ssh_command targeting remote host
        output, success = await action_ssh_command(
            owner="alice",
            command="uptime",
            host="remote.example.com",
            user="deploy",
        )
        assert success is False
        assert output == "Remote scheduled workload requires an exact external backend binding."


@pytest.mark.asyncio
async def test_scheduled_ssh_refusal_requires_authority_first():
    """Without any active authority, launch is denied before reaching the remote SSH gate."""
    output, success = await _run_subprocess(["ssh", "user@remote.host", "uptime"])
    assert success is False
    assert output == "Scheduled process launch has no server authority."
