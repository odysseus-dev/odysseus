"""Tests for JSON-in-text tool call parsing (Pattern 6) in tool_parsing.py.

Circular-import note: tool_parsing imports ToolBlock/TOOL_TAGS from agent_tools,
and agent_tools re-exports from tool_parsing. We break the cycle by pre-stubbing
agent_tools with just the names tool_parsing needs.
"""

import sys
import os
import types
from unittest.mock import MagicMock
from collections import namedtuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── Mandatory stubs before any src imports ──────────────────────────────────

# Stub optional packages
for mod in [
    'sqlalchemy', 'sqlalchemy.orm', 'sqlalchemy.ext', 'sqlalchemy.ext.declarative',
    'sqlalchemy.ext.hybrid', 'sqlalchemy.sql', 'sqlalchemy.sql.expression',
    'sqlalchemy.sql.sqltypes', 'sqlalchemy.types',
    'bcrypt', 'pyotp',
    'httpx', 'fastapi', 'fastapi.responses', 'fastapi.routing',
    'starlette', 'starlette.responses', 'starlette.middleware',
    'starlette.middleware.base',
    'pydantic',
]:
    if mod not in sys.modules:
        sys.modules[mod] = MagicMock()

# Stub core modules
for mod in ['core.database', 'core.models', 'core.auth', 'core.session_manager',
            'core.constants', 'core.exceptions', 'core.middleware']:
    if mod not in sys.modules:
        sys.modules[mod] = MagicMock()

# Stub src modules that are transitively imported
for mod in ['src.settings', 'src.prompt_security', 'src.tool_security',
            'src.context_compactor', 'src.model_context',
            'src.tool_index', 'src.integrations', 'src.llm_core',
            'src.memory', 'src.rag_manager', 'src.rag_singleton', 'src.rag_vector',
            'src.deep_research', 'src.research_handler', 'src.teacher_escalation',
            'src.event_bus', 'src.task_scheduler', 'src.webhook_manager',
            'src.mcp_manager', 'src.builtin_mcp']:
    if mod not in sys.modules:
        sys.modules[mod] = MagicMock()

# Break circular import: pre-create agent_tools with the names tool_parsing needs
if 'src.agent_tools' not in sys.modules:
    _agent_tools_stub = types.ModuleType('src.agent_tools')
    _agent_tools_stub.ToolBlock = namedtuple("ToolBlock", ["tool_type", "content"])
    _agent_tools_stub.TOOL_TAGS = {
        "bash", "python", "web_search", "web_fetch", "read_file", "write_file",
        "create_document", "update_document", "edit_document", "suggest_document",
        "search_chats", "chat_with_model", "create_session", "list_sessions",
        "send_to_session", "pipeline", "manage_session", "manage_memory",
        "list_models", "ui_control", "generate_image", "manage_tasks", "api_call",
        "ask_teacher", "manage_skills", "manage_endpoints", "manage_mcp",
        "manage_webhooks", "manage_tokens", "manage_documents", "manage_settings",
        "manage_notes", "manage_calendar", "resolve_contact", "manage_contact",
        "list_email_accounts", "send_email", "list_emails", "read_email",
        "reply_to_email", "bulk_email", "archive_email", "delete_email",
        "mark_email_read", "download_model", "serve_model", "list_served_models",
        "stop_served_model", "list_downloads", "cancel_download",
        "search_hf_models", "list_cached_models", "list_serve_presets",
        "serve_preset", "adopt_served_model", "list_cookbook_servers",
        "edit_image", "trigger_research", "manage_research", "app_api",
        "vault_search", "vault_get", "vault_unlock",
    }
    sys.modules['src.agent_tools'] = _agent_tools_stub

# Now safe to import tool_parsing
from src.tool_parsing import parse_tool_blocks, strip_tool_blocks


class TestJsonToolParsing:
    """Test Pattern 6: JSON-in-text tool call detection and parsing."""

    def test_app_api_endpoints(self):
        text = '{"name":"app_api","arguments":{"action":"endpoints"}}'
        blocks = parse_tool_blocks(text)
        assert len(blocks) == 1
        assert blocks[0].tool_type == "app_api"

    def test_app_api_endpoints_with_surrounding_text(self):
        text = 'Let me check: {"name":"app_api","arguments":{"action":"endpoints"}} done.'
        blocks = parse_tool_blocks(text)
        assert len(blocks) == 1
        assert blocks[0].tool_type == "app_api"

    def test_app_api_call_with_nested_args(self):
        text = '{"name":"app_api","arguments":{"action":"call","method":"GET","path":"/api/gallery/list"}}'
        blocks = parse_tool_blocks(text)
        assert len(blocks) == 1
        assert blocks[0].tool_type == "app_api"

    def test_manage_memory_list(self):
        text = '{"name":"manage_memory","arguments":{"action":"list"}}'
        blocks = parse_tool_blocks(text)
        assert len(blocks) == 1
        assert blocks[0].tool_type == "manage_memory"

    def test_manage_memory_search(self):
        text = '{"name":"manage_memory","arguments":{"action":"search","text":"prefs"}}'
        blocks = parse_tool_blocks(text)
        assert len(blocks) == 1
        assert blocks[0].tool_type == "manage_memory"

    def test_list_sessions(self):
        text = '{"name":"list_sessions","arguments":{}}'
        blocks = parse_tool_blocks(text)
        assert len(blocks) == 1
        assert blocks[0].tool_type == "list_sessions"

    def test_list_models(self):
        text = '{"name":"list_models","arguments":{}}'
        blocks = parse_tool_blocks(text)
        assert len(blocks) == 1
        assert blocks[0].tool_type == "list_models"

    def test_search_chats(self):
        text = '{"name":"search_chats","arguments":{"query":"calendar integration"}}'
        blocks = parse_tool_blocks(text)
        assert len(blocks) == 1
        assert blocks[0].tool_type == "search_chats"

    def test_web_search(self):
        text = '{"name":"web_search","arguments":{"query":"latest AI news"}}'
        blocks = parse_tool_blocks(text)
        assert len(blocks) == 1
        assert blocks[0].tool_type == "web_search"

    # --- Blocklisted tools ---

    def test_bash_blocked(self):
        text = '{"name":"bash","arguments":{"command":"rm -rf /"}}'
        blocks = parse_tool_blocks(text)
        assert len(blocks) == 0

    def test_python_blocked(self):
        text = '{"name":"python","arguments":{"code":"import os"}}'
        blocks = parse_tool_blocks(text)
        assert len(blocks) == 0

    def test_write_file_blocked(self):
        text = '{"name":"write_file","arguments":{"path":"/etc/passwd","content":"x"}}'
        blocks = parse_tool_blocks(text)
        assert len(blocks) == 0

    def test_shell_alias_blocked(self):
        text = '{"name":"shell","arguments":{"command":"cat /etc/passwd"}}'
        blocks = parse_tool_blocks(text)
        assert len(blocks) == 0

    def test_terminal_alias_blocked(self):
        text = '{"name":"terminal","arguments":{"command":"ls"}}'
        blocks = parse_tool_blocks(text)
        assert len(blocks) == 0

    # --- Unknown tools ---

    def test_unknown_tool_ignored(self):
        text = '{"name":"nonexistent_tool","arguments":{"foo":"bar"}}'
        blocks = parse_tool_blocks(text)
        assert len(blocks) == 0

    # --- Plain text, no JSON ---

    def test_plain_text_no_match(self):
        text = "I don't have that information."
        blocks = parse_tool_blocks(text)
        assert len(blocks) == 0

    def test_json_without_name_key(self):
        text = '{"action":"endpoints"}'
        blocks = parse_tool_blocks(text)
        assert len(blocks) == 0

    # --- Multiple tool calls ---

    def test_multiple_json_tool_calls(self):
        text = (
            "Results:\n"
            '{"name":"app_api","arguments":{"action":"endpoints"}}\n'
            '{"name":"manage_memory","arguments":{"action":"list"}}\n'
        )
        blocks = parse_tool_blocks(text)
        assert len(blocks) == 2
        assert blocks[0].tool_type == "app_api"
        assert blocks[1].tool_type == "manage_memory"

    # --- Precedence ---

    def test_fenced_block_takes_precedence(self):
        text = '```app_api\n{"action":"endpoints"}\n```\n{"name":"manage_memory","arguments":{"action":"list"}}'
        blocks = parse_tool_blocks(text)
        assert len(blocks) == 1
        assert blocks[0].tool_type == "app_api"

    # --- Nested JSON ---

    def test_nested_arguments(self):
        text = '{"name":"app_api","arguments":{"action":"call","body":{"key":"value"}}}'
        blocks = parse_tool_blocks(text)
        assert len(blocks) == 1
        assert blocks[0].tool_type == "app_api"

    # --- Whitespace ---

    def test_whitespace_variations(self):
        text = '{ "name" : "app_api" , "arguments" : { "action" : "endpoints" } }'
        blocks = parse_tool_blocks(text)
        assert len(blocks) == 1
        assert blocks[0].tool_type == "app_api"

    # --- XML takes precedence ---

    def test_xml_takes_precedence_over_json(self):
        text = '<invoke name="app_api"><parameter name="action">endpoints</parameter></invoke>\n{"name":"manage_memory","arguments":{"action":"list"}}'
        blocks = parse_tool_blocks(text)
        assert len(blocks) == 1
        assert blocks[0].tool_type == "app_api"


class TestStripJsonToolCalls:
    """Test that strip_tool_blocks removes JSON-in-text tool calls."""

    def test_strip_app_api(self):
        text = 'Result: {"name":"app_api","arguments":{"action":"endpoints"}}'
        cleaned = strip_tool_blocks(text)
        assert '{"name":"app_api"' not in cleaned
        assert "Result:" in cleaned

    def test_strip_multiple(self):
        text = (
            "Results:\n"
            '{"name":"app_api","arguments":{"action":"endpoints"}}\n'
            '{"name":"manage_memory","arguments":{"action":"list"}}\n'
        )
        cleaned = strip_tool_blocks(text)
        assert '{"name":"app_api"' not in cleaned
        assert '{"name":"manage_memory"' not in cleaned
        assert "Results:" in cleaned

    def test_strip_preserves_context(self):
        text = 'Before {"name":"list_sessions","arguments":{}} After'
        cleaned = strip_tool_blocks(text)
        assert "Before" in cleaned
        assert "After" in cleaned
        assert '{"name":"list_sessions"' not in cleaned

    def test_strip_nested(self):
        text = 'R: {"name":"app_api","arguments":{"action":"call","body":{"k":"v"}}} done'
        cleaned = strip_tool_blocks(text)
        assert '{"name":"app_api"' not in cleaned
        assert "R:" in cleaned
        assert "done" in cleaned

    def test_strip_full_removal(self):
        text = '{"name":"app_api","arguments":{"action":"endpoints"}}'
        cleaned = strip_tool_blocks(text)
        assert cleaned == ""

    def test_no_false_positive_plain_json(self):
        """JSON without name+arguments shape should stay."""
        text = 'Response: {"status":"ok","count":42}'
        cleaned = strip_tool_blocks(text)
        assert '{"status":"ok"' in cleaned
