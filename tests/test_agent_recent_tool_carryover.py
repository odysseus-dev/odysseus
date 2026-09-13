"""Tests for the recent-tool carry-forward — keeping tools used (and skills
viewed) in recent turns available in per-turn tool selection so consecutive
requests keep their toolset."""

import sys
from unittest.mock import MagicMock

_MOCKED_IMPORTS = [
    'sqlalchemy', 'sqlalchemy.orm', 'sqlalchemy.ext', 'sqlalchemy.ext.declarative',
    'sqlalchemy.ext.hybrid', 'sqlalchemy.sql', 'sqlalchemy.sql.expression',
    'src.database',
    'src.agent_tools',
    'core.models', 'core.database',
]
_INJECTED_IMPORT_STUBS = {}
_PREEXISTING_AGENT_LOOP = sys.modules.get("src.agent_loop")


def _drop_module_if_same(name, expected):
    if sys.modules.get(name) is expected:
        sys.modules.pop(name, None)
    parent_name, _, attr = name.rpartition(".")
    parent = sys.modules.get(parent_name)
    if parent is not None and getattr(parent, "__dict__", {}).get(attr) is expected:
        delattr(parent, attr)


for mod in _MOCKED_IMPORTS:
    if mod not in sys.modules:
        stub = MagicMock()
        sys.modules[mod] = stub
        _INJECTED_IMPORT_STUBS[mod] = stub

_IMPORTED_AGENT_LOOP = None
try:
    from src.agent_loop import _recently_used_tools, _recently_viewed_skill_names
    _IMPORTED_AGENT_LOOP = sys.modules.get("src.agent_loop")
finally:
    if _PREEXISTING_AGENT_LOOP is None and _IMPORTED_AGENT_LOOP is not None:
        _drop_module_if_same("src.agent_loop", _IMPORTED_AGENT_LOOP)
    for _mod, _stub in _INJECTED_IMPORT_STUBS.items():
        _drop_module_if_same(_mod, _stub)


def _assistant_with_tools(*tools):
    events = [{"tool": t, "command": "x", "output": "y"} for t in tools]
    return {"role": "assistant", "content": "done", "metadata": {"tool_events": events}}


def test_previous_turn_tools_are_carried_forward():
    messages = [
        {"role": "user", "content": "run the test script"},
        _assistant_with_tools("bash", "read_file"),
        {"role": "user", "content": "ok, now show the diff"},
    ]
    assert _recently_used_tools(messages) == {"bash", "read_file"}


def test_no_tool_history_returns_empty():
    messages = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "Hi!"},
        {"role": "user", "content": "what can you do?"},
    ]
    assert _recently_used_tools(messages) == set()


def test_tools_older_than_window_are_dropped():
    messages = [
        {"role": "user", "content": "older task"},
        _assistant_with_tools("generate_image"),
        {"role": "user", "content": "middle task"},
        _assistant_with_tools("read_file"),
        {"role": "user", "content": "newer task"},
        _assistant_with_tools("bash"),
        {"role": "user", "content": "follow-up"},
    ]
    assert _recently_used_tools(messages) == {"bash", "read_file"}


def test_unresolved_mcp_events_are_skipped():
    messages = [
        {"role": "user", "content": "do it"},
        {
            "role": "assistant",
            "content": "done",
            "metadata": {"tool_events": [{"tool": "mcp", "command": "run", "output": "no name"}]},
        },
        {"role": "user", "content": "again"},
    ]
    assert _recently_used_tools(messages) == set()


def test_malformed_messages_do_not_raise():
    assert _recently_used_tools([None, {}, {"role": "assistant"}, "junk"]) == set()


def test_cap_limits_collected_tools():
    messages = [
        {"role": "user", "content": "go"},
        _assistant_with_tools(*[f"tool_{i}" for i in range(30)]),
        {"role": "user", "content": "continue"},
    ]
    result = _recently_used_tools(messages)
    assert len(result) <= 16


def _untrusted_context_row():
    # Shape produced by untrusted_context_message (src/prompt_security.py):
    # a user-role row carrying metadata.trusted=False. These are injected
    # context (uploads, research, integrations) — not real turns.
    return {"role": "user", "content": "…", "metadata": {"trusted": False}}


def test_injected_context_rows_do_not_consume_the_turn_window():
    messages = [
        {"role": "user", "content": "older task"},
        _assistant_with_tools("generate_image"),
        {"role": "user", "content": "newer task"},
        _assistant_with_tools("bash"),
        _untrusted_context_row(),
        _untrusted_context_row(),
        {"role": "user", "content": "follow-up"},
    ]
    # Without the trusted=False skip, the two context rows would burn the
    # whole 2-turn window before any assistant row is reached (empty result).
    assert _recently_used_tools(messages) == {"bash", "generate_image"}


def _skills_event(command):
    return {
        "role": "assistant",
        "content": "fetched skill",
        "metadata": {"tool_events": [{"tool": "manage_skills", "command": command}]},
    }


def test_recently_viewed_skills_parsed_from_json_view_event():
    messages = [
        {"role": "user", "content": "deploy the app"},
        _skills_event('{"action": "view", "name": "deploy-app"}'),
        {"role": "user", "content": "ok do step 3 now"},
    ]
    assert _recently_viewed_skill_names(messages) == {"deploy-app"}


def test_recently_viewed_skills_parsed_from_key_value_view_event():
    messages = [
        {"role": "user", "content": "run the procedure"},
        _skills_event("view name=deploy-app"),
        {"role": "user", "content": "continue"},
    ]
    assert _recently_viewed_skill_names(messages) == {"deploy-app"}


def test_view_ref_action_and_non_view_actions():
    messages = [
        {"role": "user", "content": "go"},
        _skills_event('{"action": "view_ref", "name": "ref-skill"}'),
        _skills_event('{"action": "list"}'),
        _skills_event('{"action": "add", "name": "new-skill"}'),
        {"role": "user", "content": "follow-up"},
    ]
    assert _recently_viewed_skill_names(messages) == {"ref-skill"}


def test_non_manage_skills_events_are_ignored():
    messages = [
        {"role": "user", "content": "go"},
        _assistant_with_tools("bash"),
        {"role": "user", "content": "follow-up"},
    ]
    assert _recently_viewed_skill_names(messages) == set()


def test_recently_viewed_skills_respect_turn_window():
    messages = [
        {"role": "user", "content": "first task"},
        _skills_event('{"action": "view", "name": "old-skill"}'),
        {"role": "user", "content": "second task"},
        _skills_event('{"action": "view", "name": "middle-skill"}'),
        {"role": "user", "content": "third task"},
        _skills_event('{"action": "view", "name": "recent-skill"}'),
        {"role": "user", "content": "follow-up"},
    ]
    assert _recently_viewed_skill_names(messages) == {"recent-skill", "middle-skill"}


def test_search_query_containing_view_word_is_not_a_skill_view():
    # "view" inside a search query must not read as action=view — otherwise
    # the search's own `name`-like key would leak as a viewed skill.
    messages = [
        {"role": "user", "content": "find procedures"},
        _skills_event('{"action": "search", "query": "how to view logs", "name": "log-skill"}'),
        {"role": "user", "content": "follow-up"},
    ]
    assert _recently_viewed_skill_names(messages) == set()


def test_skill_names_strip_control_characters():
    # A raw newline inside a (malformed-JSON) name value must not survive
    # extraction — control characters are removed entirely so a forged
    # name can't splice fake lines into server logs.
    messages = [
        {"role": "user", "content": "go"},
        _skills_event('{"action": "view", "name": "evil\nfake log line"}'),
        {"role": "user", "content": "follow-up"},
    ]
    names = _recently_viewed_skill_names(messages)
    assert names == {"evilfake log line"}
    for name in names:
        assert "\n" not in name and "\r" not in name
