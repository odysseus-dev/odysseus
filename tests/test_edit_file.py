"""edit_file: filesystem-write permission policy + behavior."""
import json
import os
import tempfile

import pytest
from tests.runtime_evidence_helpers import server_authorized_executor


@pytest.fixture(autouse=True)
def standalone_dispatch_authority(monkeypatch):
    from src import tool_execution
    monkeypatch.setattr(tool_execution, "execute_tool_block",
                        server_authorized_executor(tool_execution.execute_tool_block))

from src import tool_security
from src.tool_security import (
    NON_ADMIN_BLOCKED_TOOLS,
    is_public_blocked_tool,
    blocked_tools_for_owner,
)
from src.agent_tools.filesystem_tools import EditFileTool
from src.agent_tools import ToolBlock


# ── Permission policy ─────────────────────────────────────────────────────
def test_edit_file_is_sensitive_write_tool():
    # Must be blocked for non-admins exactly like write_file.
    assert "edit_file" in NON_ADMIN_BLOCKED_TOOLS
    assert is_public_blocked_tool("edit_file") is True


def test_blocked_tools_for_owner_includes_edit_file_for_non_admin(monkeypatch):
    monkeypatch.setattr(tool_security, "owner_is_admin_or_single_user", lambda owner: False)
    blocked = blocked_tools_for_owner("bob")
    assert "edit_file" in blocked and "write_file" in blocked
    # Admin / single-user gets nothing blocked.
    monkeypatch.setattr(tool_security, "owner_is_admin_or_single_user", lambda owner: True)
    assert blocked_tools_for_owner("admin") == set()


@pytest.mark.asyncio
async def test_edit_file_blocked_at_execution_for_non_admin(monkeypatch):
    # Execution-level gate: a non-admin owner must be refused even if the tool
    # reaches execute_tool_block. edit_file stays admin-gated by tool_security
    # after #2684 (ALWAYS_AVAILABLE only changed advertisement, not execution).
    #
    # Resolve execute_tool_block from the live module object (te) rather than a
    # top-level import: other test modules pop src.tool_execution from
    # sys.modules and re-import it, so a stale top-level reference would call a
    # different module's function than the one monkeypatch targets — silently
    # bypassing the admin gate.
    import src.tool_execution as te
    from src.agent_runtime.authority import create_request_authority
    monkeypatch.setattr(te, "_owner_is_admin", lambda owner: False)
    ws = tempfile.mkdtemp()
    p = os.path.join(ws, "ef_block.txt")
    open(p, "w").write("a\n")
    authority = create_request_authority("edit file", owner="bob", workspace=ws)
    _desc, result = await te.execute_tool_block(
        ToolBlock("edit_file", json.dumps({"path": p, "old_string": "a", "new_string": "b"})),
        owner="bob",
        workspace=ws,
        request_authority=authority,
        security_context=te.NO_TOOL_SECURITY_CONTEXT,
    )
    assert result.get("exit_code") == 1 and "admin" in result.get("error", "").lower()
    os.unlink(p)


# ── Behavior ──────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_edit_file_success():
    p = os.path.join("/tmp", "ef_ok.py")
    open(p, "w").write("def f():\n    return 1\n")
    res = await EditFileTool().execute(json.dumps({"path": p, "old_string": "return 1", "new_string": "return 2"}), {})
    assert res["exit_code"] == 0
    assert open(p).read() == "def f():\n    return 2\n"
    assert res["diff"]["added"] == 1 and res["diff"]["removed"] == 1 and res["diff"]["file"] == "ef_ok.py"
    os.unlink(p)


@pytest.mark.asyncio
async def test_edit_file_not_found():
    p = os.path.join("/tmp", "ef_nf.txt")
    open(p, "w").write("hello\n")
    res = await EditFileTool().execute(json.dumps({"path": p, "old_string": "nope", "new_string": "x"}), {})
    assert res["exit_code"] == 1 and "not found" in res["error"]
    os.unlink(p)


@pytest.mark.asyncio
async def test_edit_file_non_unique():
    p = os.path.join("/tmp", "ef_dup.txt")
    open(p, "w").write("x\nx\n")
    res = await EditFileTool().execute(json.dumps({"path": p, "old_string": "x", "new_string": "y"}), {})
    assert res["exit_code"] == 1 and "not unique" in res["error"]
    # replace_all resolves it
    res = await EditFileTool().execute(json.dumps({"path": p, "old_string": "x", "new_string": "y", "replace_all": True}), {})
    assert res["exit_code"] == 0 and open(p).read() == "y\ny\n"
    os.unlink(p)


@pytest.mark.asyncio
async def test_edit_file_outside_allowed_roots():
    res = await EditFileTool().execute(json.dumps({"path": "/etc/hosts", "old_string": "x", "new_string": "y"}), {})
    assert res["exit_code"] == 1 and ("outside the allowed roots" in res["error"] or "sensitive" in res["error"])
