"""Regression: Russian deep-research requests reach trigger_research.

"Проведи исследование на основе предыдущего ответа" matched no domain in
``_classify_agent_request`` (English-only regexes), was treated as low-signal,
and the cold tool index timed out — so the agent only got the always-available
tools plus MCP and answered inline instead of starting a Deep Research job.
"""
import pytest

agent_loop = pytest.importorskip("src.agent_loop")
tool_index = pytest.importorskip("src.tool_index")


def _selected_tools(domains):
    tools = set()
    for domain in domains:
        tools |= agent_loop._DOMAIN_TOOL_MAP.get(domain, set())
    return tools


@pytest.mark.parametrize(
    "prompt",
    [
        "Проведи исследование на основе предыдущего ответа",
        "Проведите глубокое исследование про Олесю Добровольскую",
        "Исследуй тему рэйки и доказательной медицины",
        "изучи, что пишут про тета-хилинг",
        "Хочу исследование по натуропатии",
        "Запусти ресерч по этой теме",
        "do research on quantum dots",
        "research the history of Reiki",
        "deep dive into Usui Reiki lineage",
    ],
)
def test_research_prompts_seed_trigger_research(prompt):
    intent = agent_loop._classify_agent_request([{"role": "user", "content": prompt}], prompt)
    assert intent["low_signal"] is False, intent
    assert "research" in intent["domains"], intent
    assert "trigger_research" in _selected_tools(intent["domains"])


@pytest.mark.parametrize(
    "prompt",
    [
        "Привет, как дела?",
        "Напиши стихотворение про кота",
        "Что показало исследование, которое ты упомянул?",
        "open my research library",
    ],
)
def test_non_research_prompts_do_not_seed_research_domain(prompt):
    intent = agent_loop._classify_agent_request([{"role": "user", "content": prompt}], prompt)
    assert "research" not in intent["domains"], intent


def test_research_domain_has_a_rule_pack():
    rules = agent_loop._domain_rules_for_tools({"trigger_research"})
    assert any("trigger_research" in r and "исследование" in r for r in rules), rules


def test_trigger_research_schema_is_sent_when_selected():
    names = {
        s.get("function", {}).get("name")
        for s in agent_loop.FUNCTION_TOOL_SCHEMAS
        if s.get("function", {}).get("name") in {"trigger_research"}
    }
    assert names == {"trigger_research"}


@pytest.mark.parametrize("query", ["Проведи исследование по рэйки", "исследуй этот вопрос"])
def test_keyword_hints_cover_russian_research(query):
    # Both keyword paths: word-boundary match in get_tools_for_query and the
    # substring fallback used when the tool index is unavailable.
    idx = tool_index.ToolIndex.__new__(tool_index.ToolIndex)
    idx.retrieve = lambda q, k=8: set()
    assert "trigger_research" in idx.get_tools_for_query(query)
    ql = query.lower()
    fallback = set()
    for keywords, tools in tool_index.ToolIndex._KEYWORD_HINTS.items():
        if any(kw in ql for kw in keywords):
            fallback |= tools
    assert "trigger_research" in fallback


def test_index_init_timeout_uses_keyword_fallback():
    # On tool-index init timeout the loop must leave _relevant_tools unset so
    # the keyword fallback runs, not pin it to ALWAYS_AVAILABLE.
    import inspect
    src = inspect.getsource(agent_loop)
    block = src.split("Tool index init exceeded", 1)[1][:400]
    assert "_relevant_tools = None" in block
    assert "_relevant_tools = set(ALWAYS_AVAILABLE)" not in block
