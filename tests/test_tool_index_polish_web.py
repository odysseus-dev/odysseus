"""Regression for issue #4055 — Polish web-search queries must force-include web tools.

ToolIndex._WEB_RE extends the existing web-intent regex (alongside English URL/
search phrases) so `get_tools_for_query` surfaces web_search/web_fetch when RAG
embeddings miss non-English phrasing.
"""

from src.tool_index import ToolIndex


def _index_without_embeddings():
    ti = ToolIndex.__new__(ToolIndex)
    ti.retrieve = lambda query, k=8: []
    return ti


def test_polish_weather_search_force_includes_web_tools():
    ti = _index_without_embeddings()
    q = "Wyszukaj w internecie i podaj temperaturę w Lubartowie dzisiaj"
    tools = ti.get_tools_for_query(q)
    assert "web_search" in tools
    assert "web_fetch" in tools


def test_polish_generic_search_force_includes_web_tools():
    ti = _index_without_embeddings()
    q = "Sprawdź w internecie aktualne informacje o stawkach VAT"
    tools = ti.get_tools_for_query(q)
    assert "web_search" in tools
    assert "web_fetch" in tools


def test_polish_price_search_force_includes_web_tools():
    ti = _index_without_embeddings()
    q = "Poszukaj najnowsza cena i kurs euro"
    tools = ti.get_tools_for_query(q)
    assert "web_search" in tools
    assert "web_fetch" in tools


def test_english_weather_search_still_force_includes_web_tools():
    ti = _index_without_embeddings()
    q = "Search the web for the weather forecast in London today"
    tools = ti.get_tools_for_query(q)
    assert "web_search" in tools
    assert "web_fetch" in tools
