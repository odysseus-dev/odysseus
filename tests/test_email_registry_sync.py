"""PR #3681 — every email-tool registry derives from / covers BUILTIN_EMAIL_TOOLS.

Three review rounds on #3681 each found another hand-maintained copy of the
email tool list that had drifted (NON_ADMIN_BLOCKED_TOOLS, the `disable_tool
email` alias, tool_schemas' private copy). This test pins ALL of them to the
single source of truth, including the email MCP server itself, so adding or
removing an email tool fails loudly everywhere it matters instead of silently
shrinking one surface.
"""
import re
from pathlib import Path

import src.agent_tools  # noqa: F401 — resolve the circular-import cluster first
from src.tool_security import (
    BUILTIN_EMAIL_TOOLS,
    NON_ADMIN_BLOCKED_TOOLS,
    PLAN_MODE_READONLY_TOOLS,
    _PLAN_MODE_KNOWN_MUTATORS,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent

# Read-only email tools — the only ones plan mode may execute.
_READONLY_EMAIL = {"list_email_accounts", "list_emails", "read_email", "search_emails"}


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


def test_function_schemas_cover_email_tools():
    """Every email tool must be advertised to native function-calling models."""
    from src.tool_schemas import FUNCTION_TOOL_SCHEMAS

    names = {(t.get("function") or {}).get("name") for t in FUNCTION_TOOL_SCHEMAS}
    missing = set(BUILTIN_EMAIL_TOOLS) - names
    assert not missing, f"email tools without native schemas: {sorted(missing)}"


def test_prompt_sections_cover_email_tools():
    """Every email tool must be describable to fenced-block models."""
    from src.agent_loop import TOOL_SECTIONS

    missing = set(BUILTIN_EMAIL_TOOLS) - set(TOOL_SECTIONS)
    assert not missing, f"email tools without prompt sections: {sorted(missing)}"


def test_domain_rules_map_covers_email_tools():
    from src.agent_loop import _DOMAIN_TOOL_MAP

    assert BUILTIN_EMAIL_TOOLS <= _DOMAIN_TOOL_MAP["email"]


def test_rag_descriptions_and_keyword_hints_cover_email_tools():
    """Every email tool must be retrievable (embedding index + keyword hints)."""
    from src.tool_index import (
        ASSISTANT_ALWAYS_AVAILABLE,
        BUILTIN_TOOL_DESCRIPTIONS,
    )

    missing = set(BUILTIN_EMAIL_TOOLS) - set(BUILTIN_TOOL_DESCRIPTIONS)
    assert not missing, f"email tools not in the RAG description index: {sorted(missing)}"
    assert BUILTIN_EMAIL_TOOLS <= ASSISTANT_ALWAYS_AVAILABLE


def test_known_tool_names_cover_email_tools():
    from src.tool_policy import _COMMON_TOOL_NAMES

    assert BUILTIN_EMAIL_TOOLS <= _COMMON_TOOL_NAMES


def test_non_admin_blocklist_covers_email_tools():
    assert BUILTIN_EMAIL_TOOLS <= NON_ADMIN_BLOCKED_TOOLS


def test_plan_mode_partitions_email_tools():
    """Plan mode must classify every email tool: the read-only four are
    allowed (both spellings, given the bare/qualified alias gate), everything
    else is in the fail-closed mutator backstop. An unclassified tool would be
    allowed bare and blocked qualified — the round-2 aliasing bug again."""
    readonly = set(BUILTIN_EMAIL_TOOLS) & PLAN_MODE_READONLY_TOOLS
    assert readonly == _READONLY_EMAIL, (
        f"plan-mode read-only email subset drifted: {sorted(readonly)}"
    )
    mutators = set(BUILTIN_EMAIL_TOOLS) - _READONLY_EMAIL
    missing = mutators - _PLAN_MODE_KNOWN_MUTATORS
    assert not missing, f"mutating email tools not in plan-mode backstop: {sorted(missing)}"


def test_plan_mode_classifies_every_email_tool():
    """Every fence-taggable email tool must be EXPLICITLY classified for plan
    mode: read-only (allowlisted) or mutating (in the static denylist via the
    fail-closed backstop). Allowed-by-omission is not a classification — it
    silently flips when schemas/backstop change, and it leaves bare-alias
    safety depending on the MCP read-only inventory being present."""
    from src.tool_security import plan_mode_disabled_tools

    denied = plan_mode_disabled_tools()
    for tool in sorted(BUILTIN_EMAIL_TOOLS):
        if tool in _READONLY_EMAIL:
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


def test_frontend_tool_selector_covers_email_tools():
    """The assistant tool-selector UI must be able to grant/revoke every email
    tool — a name missing here can never be toggled from the UI."""
    source = (_REPO_ROOT / "static" / "js" / "assistant.js").read_text()
    m = re.search(r"'Email':\s*\[([^\]]*)\]", source)
    assert m, "Email group not found in assistant.js TOOL_GROUPS"
    listed = set(re.findall(r"'(\w+)'", m.group(1)))
    assert listed == set(BUILTIN_EMAIL_TOOLS), (
        f"assistant.js Email group != BUILTIN_EMAIL_TOOLS; "
        f"js-only: {sorted(listed - BUILTIN_EMAIL_TOOLS)}, "
        f"missing: {sorted(BUILTIN_EMAIL_TOOLS - listed)}"
    )


def test_default_assistant_seed_covers_email_tools():
    """The default Assistant crew seed grants the full email set."""
    from src.task_scheduler import DEFAULT_ASSISTANT_ENABLED_TOOLS

    assert BUILTIN_EMAIL_TOOLS <= set(DEFAULT_ASSISTANT_ENABLED_TOOLS)


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
