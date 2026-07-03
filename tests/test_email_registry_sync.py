"""PR #3681 — the surfaces this PR derives from BUILTIN_EMAIL_TOOLS stay in sync.

The review rounds on #3681 each found a hand-maintained copy of the email tool
list that had drifted. This PR's scope pins the SECURITY-RELEVANT surfaces to
the single source of truth (the email MCP server itself, the fence tags, the
non-admin blocklist, the bare<->qualified alias rule, and the plan-mode
read-only fix the alias gate requires). The wider advertising/registry
consolidation (schemas, prompt sections, RAG index, UI selector, assistant
seed) lives in a follow-up PR with its own sync tests.
"""
import re
from pathlib import Path

import src.agent_tools  # noqa: F401 — resolve the circular-import cluster first
from src.tool_security import (
    BUILTIN_EMAIL_TOOLS,
    NON_ADMIN_BLOCKED_TOOLS,
    PLAN_MODE_READONLY_TOOLS,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent


def test_email_server_tools_match_builtin_set():
    """BUILTIN_EMAIL_TOOLS must equal exactly what the email server exposes."""
    source = (_REPO_ROOT / "mcp_servers" / "email_server.py").read_text()
    served = set(re.findall(r'Tool\(\s*name="(\w+)"', source))
    assert served == set(BUILTIN_EMAIL_TOOLS), (
        f"email_server tools != BUILTIN_EMAIL_TOOLS; "
        f"server-only: {sorted(served - BUILTIN_EMAIL_TOOLS)}, "
        f"set-only: {sorted(BUILTIN_EMAIL_TOOLS - served)}"
    )


def test_fence_tags_cover_email_tools():
    from src.agent_tools import TOOL_TAGS

    assert BUILTIN_EMAIL_TOOLS <= set(TOOL_TAGS)


def test_non_admin_blocklist_covers_email_tools():
    assert BUILTIN_EMAIL_TOOLS <= NON_ADMIN_BLOCKED_TOOLS


def test_plan_mode_classifies_every_email_tool():
    """Every fence-taggable email tool must be EXPLICITLY classified for plan
    mode: read-only (allowlisted) or mutating (in the static denylist via the
    fail-closed backstop). Allowed-by-omission is not a classification — it
    silently flips when schemas/backstop change, and it leaves bare-alias
    safety depending on the MCP read-only inventory being present."""
    from src.tool_security import plan_mode_disabled_tools

    denied = plan_mode_disabled_tools()
    readonly = {"list_email_accounts", "list_emails", "read_email", "search_emails"}
    for tool in sorted(BUILTIN_EMAIL_TOOLS):
        if tool in readonly:
            assert tool in PLAN_MODE_READONLY_TOOLS, f"{tool} must be explicit read-only"
            assert tool not in denied, f"read-only {tool} must not be denied in plan mode"
        else:
            assert tool in denied, f"mutating {tool} missing from the plan-mode denylist"


def test_plan_mode_allows_qualified_readonly_email_discovery():
    """list_email_accounts has a native schema, so plan mode's schema-derived
    bare denylist contains it; with the bidirectional alias gate, the bare
    entry would also block the qualified mcp__email__ call that the MCP
    read-only filter deliberately allows — unless it's in the read-only
    allowlist (which subtracts it from the denylist)."""
    assert "list_email_accounts" in PLAN_MODE_READONLY_TOOLS


def test_email_policy_name_aliases():
    """The alias rule every execution gate relies on."""
    from src.tool_security import email_tool_policy_names

    assert email_tool_policy_names("list_emails") == {
        "list_emails", "mcp__email__list_emails",
    }
    assert email_tool_policy_names("mcp__email__delete_email") == {
        "delete_email", "mcp__email__delete_email",
    }
    # Non-email names alias only to themselves — including mcp__email__
    # spellings of tools the email server doesn't expose.
    assert email_tool_policy_names("bash") == {"bash"}
    assert email_tool_policy_names("mcp__email__not_a_tool") == {"mcp__email__not_a_tool"}
    assert email_tool_policy_names("mcp__other__list_emails") == {"mcp__other__list_emails"}
