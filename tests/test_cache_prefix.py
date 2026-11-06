"""Stable ``tools[]`` order so consecutive turns share the provider prefix."""
from src.llm_core import _stable_sort_tools


def test_openai_shaped_tools_sort_by_function_name():
    tools = [
        {"type": "function", "function": {"name": "write_file"}},
        {"type": "function", "function": {"name": "bash"}},
    ]
    names = [item["function"]["name"] for item in _stable_sort_tools(tools)]
    assert names == ["bash", "write_file"]


def test_anthropic_shaped_tools_sort_by_top_level_name():
    tools = [{"name": "Read"}, {"name": "Bash"}, {"name": "Edit"}]
    names = [item["name"] for item in _stable_sort_tools(tools)]
    assert names == ["Bash", "Edit", "Read"]


def test_same_set_in_any_order_produces_the_same_list():
    first = [{"function": {"name": name}} for name in ("b", "a", "c")]
    second = [{"function": {"name": name}} for name in ("c", "b", "a")]
    assert _stable_sort_tools(first) == _stable_sort_tools(second)


def test_missing_names_sort_first_without_crashing():
    tools = [{"function": {"name": "b"}}, {"type": "unknown"}, {"function": {"name": "a"}}]
    result = _stable_sort_tools(tools)
    assert [((item.get("function") or {}).get("name")) for item in result] == [None, "a", "b"]


def test_empty_and_none_pass_through():
    assert _stable_sort_tools([]) == []
    assert _stable_sort_tools(None) is None


def test_input_order_is_not_mutated():
    tools = [{"function": {"name": "b"}}, {"function": {"name": "a"}}]
    _stable_sort_tools(tools)
    assert [item["function"]["name"] for item in tools] == ["b", "a"]
