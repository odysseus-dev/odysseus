"""Issue #4769 — built-in email tools emitted as bare names must dispatch.

Email tools (list_emails, read_email, …) are implemented as an internal "email"
MCP server, so they execute under the mcp__email__ namespace. But they are also
in TOOL_TAGS, so a local model can name them in two ways:

  - mcp__email__list_emails — native function calls and <invoke>/<tool_code>
    parse paths, which canonicalize via function_call_to_tool_block; and
  - list_emails (bare) — the fenced ```list_emails / [TOOL_CALL] parse paths.

The dispatcher only had a case for the mcp__email__ form, so a bare email tool
fell through to `Unknown tool type: list_emails` and never ran — the exact
failure local Ollama users hit (#4769). execute_tool_block now normalizes the
bare form to mcp__email__ AFTER the security gates (which match the bare names),
so the call dispatches while disable/plan-mode/admin checks still apply.
"""
import asyncio
import sys
from unittest.mock import MagicMock

# The agent-tool stack pulls in heavy DB/auth deps at import; stub them just long
# enough to import, then restore. Mirrors tests/test_unknown_tool_calls.py. We do
# NOT pop src.tool_execution (popping it rebinds the module object and breaks
# attribute monkeypatching).
_ABSENT = object()
_AGENT_MODULES = ["src.agent_tools", "src.tool_parsing", "src.tool_schemas"]
_STUBBED = [
    "sqlalchemy", "sqlalchemy.orm", "sqlalchemy.ext", "sqlalchemy.ext.declarative",
    "sqlalchemy.ext.hybrid", "sqlalchemy.sql", "sqlalchemy.sql.expression",
    "src.database", "core.models", "core.database", "core.auth",
]
_saved_stubs = {name: sys.modules.get(name, _ABSENT) for name in _STUBBED}

for _mod in _AGENT_MODULES:
    sys.modules.pop(_mod, None)
for _mod in _STUBBED:
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

import pytest  # noqa: E402
import src.agent_tools  # noqa: E402,F401
import src.tool_execution as te  # noqa: E402
from src.agent_tools import ToolBlock  # noqa: E402
from src.tool_parsing import parse_tool_blocks  # noqa: E402
from src.tool_schemas import function_call_to_tool_block  # noqa: E402
from src.tool_security import BUILTIN_EMAIL_TOOLS  # noqa: E402

# Drop the stubs we installed so they do not leak into later tests.
for _name, _original in _saved_stubs.items():
    if _original is _ABSENT:
        sys.modules.pop(_name, None)
    else:
        sys.modules[_name] = _original


class _RecordingMCP:
    """Stand-in MCP manager that records the qualified tool name it was asked
    to run and returns a benign success payload."""

    def __init__(self):
        self.calls = []

    async def call_tool(self, tool, args):
        self.calls.append((tool, args))
        return {"output": "ok", "exit_code": 0}


def _run(coro):
    return asyncio.run(coro)


@pytest.mark.parametrize("name", sorted(BUILTIN_EMAIL_TOOLS))
def test_bare_email_tool_dispatches_to_email_mcp(name, monkeypatch):
    """A bare email tool name routes to the email MCP instead of failing with
    'Unknown tool type'."""
    mcp = _RecordingMCP()
    monkeypatch.setattr(te, "get_mcp_manager", lambda: mcp)
    # Treat the caller as authorized so the public/admin gate doesn't pre-empt
    # the dispatch we're exercising.
    monkeypatch.setattr(te, "_owner_is_admin", lambda owner: True)

    desc, result = _run(te.execute_tool_block(ToolBlock(name, "{}"), owner="admin"))

    assert mcp.calls, f"{name} did not reach the email MCP"
    assert mcp.calls[0][0] == f"mcp__email__{name}"
    assert "Unknown tool type" not in (result.get("error") or "")
    assert result.get("exit_code") == 0


def test_bare_email_tool_still_blocked_when_disabled(monkeypatch):
    """Normalization happens AFTER the security gates: a bare email tool the
    user disabled is blocked by its bare name and never reaches the MCP."""
    mcp = _RecordingMCP()
    monkeypatch.setattr(te, "get_mcp_manager", lambda: mcp)
    monkeypatch.setattr(te, "_owner_is_admin", lambda owner: True)

    desc, result = _run(te.execute_tool_block(
        ToolBlock("list_emails", "{}"),
        disabled_tools={"list_emails"},
        owner="admin",
    ))

    assert "BLOCKED" in desc
    assert result["exit_code"] == 1
    assert not mcp.calls, "disabled email tool must not reach the MCP"


def test_native_and_invoke_paths_already_canonical():
    """function_call_to_tool_block (native + <invoke>/<tool_code>) canonicalizes
    bare email names to mcp__email__ — the shared BUILTIN_EMAIL_TOOLS constant."""
    for name in BUILTIN_EMAIL_TOOLS:
        block = function_call_to_tool_block(name, "{}")
        assert block is not None and block.tool_type == f"mcp__email__{name}"


def test_fenced_and_toolcall_paths_emit_email_tool_blocks():
    """The bare-producing parse paths still surface email calls as tool blocks
    (a bare name that the dispatcher then normalizes), rather than dropping or
    mangling them."""
    fenced = parse_tool_blocks("```list_emails\n{}\n```")
    assert [b.tool_type for b in fenced] == ["list_emails"]

    tool_call = parse_tool_blocks('[TOOL_CALL]{tool => "list_emails", args => {}}[/TOOL_CALL]')
    assert [b.tool_type for b in tool_call] == ["list_emails"]

    # <invoke> already canonicalizes.
    invoke = parse_tool_blocks('<invoke name="list_emails"></invoke>')
    assert [b.tool_type for b in invoke] == ["mcp__email__list_emails"]


@pytest.mark.parametrize("name", sorted(BUILTIN_EMAIL_TOOLS))
def test_bare_email_tool_blocked_for_non_admin(name, monkeypatch):
    """A non-admin must not reach an email MCP tool via the bare fenced /
    [TOOL_CALL] form. The dispatcher normalizes bare -> mcp__email__ only AFTER
    the public gate, so the public gate must block the bare name. Without that,
    the six email tools NOT in NON_ADMIN_BLOCKED_TOOLS (delete_email, bulk_email,
    archive_email, download_attachment, mark_email_read, list_email_accounts) —
    previously protected only by the mcp__ prefix rule on their canonical form —
    would escalate, since the email MCP only owner-scopes the mailbox and does not
    re-check admin. Threat model: email is admin-only for non-admins."""
    mcp = _RecordingMCP()
    monkeypatch.setattr(te, "get_mcp_manager", lambda: mcp)
    monkeypatch.setattr(te, "_owner_is_admin", lambda owner: False)

    desc, result = _run(te.execute_tool_block(ToolBlock(name, "{}"), owner="alice"))

    assert "BLOCKED" in desc, f"{name} not blocked for non-admin"
    assert result["exit_code"] == 1
    assert "admin" in (result.get("error") or "").lower()
    assert not mcp.calls, f"{name} reached the email MCP as a non-admin"
