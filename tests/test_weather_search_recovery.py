import asyncio
import json

import src.search as search
from src.agent_tools import TOOL_HANDLERS
from src.agent_tools.weather_tools import WeatherTool, weather_location_from_query
from src.agent_tools.web_tools import WebSearchTool
from src.agent_loop import _should_retry_empty_search_in_browser, _weather_tool_relevant
from src.tool_policy import WEB_TOOL_NAMES, ToolPolicy
from src.tool_schemas import FUNCTION_TOOL_SCHEMAS
from src.tool_types import ToolBlock
from src.tool_execution import NO_TOOL_SECURITY_CONTEXT, execute_tool_block
from tests.runtime_evidence_helpers import server_authorized_executor

execute_tool_block = server_authorized_executor(execute_tool_block)
from src.turn_contract import FAMILY_TOOLS


def test_weather_tool_is_registered_and_web_scoped():
    assert "get_weather" in TOOL_HANDLERS
    assert "get_weather" in WEB_TOOL_NAMES
    assert "get_weather" in FAMILY_TOOLS["search_browser"]
    assert any(item["function"]["name"] == "get_weather" for item in FUNCTION_TOOL_SCHEMAS)


def test_weather_tool_returns_timestamped_forecast(monkeypatch):
    from src.agent_tools import weather_tools

    def fake_get(url):
        if "geocoding-api" in url:
            return {"results": [{"name": "Tokyo", "admin1": "Tokyo", "country": "Japan",
                                 "latitude": 35.68, "longitude": 139.69, "timezone": "Asia/Tokyo"}]}
        return {"timezone": "Asia/Tokyo", "current": {"time": "2026-09-23T11:15", "temperature_2m": 22},
                "daily": {"time": ["2026-09-23"], "temperature_2m_max": [25]}}

    monkeypatch.setattr(weather_tools, "_get_json", fake_get)
    result = asyncio.run(WeatherTool().execute('{"location":"Tokyo, Japan"}', {}))
    data = json.loads(result["output"])
    assert result["evidence_status"] == "available"
    assert data["location"] == "Tokyo, Japan"
    assert data["current"]["time"] == "2026-09-23T11:15"
    assert data["source"].startswith("https://api.open-meteo.com/v1/forecast?")


def test_empty_dated_search_retries_without_filter(monkeypatch):
    calls = []

    def fake_search(query, **kwargs):
        calls.append(kwargs["time_filter"])
        if kwargs["time_filter"] is None:
            return "Found a source", [{"title": "Source", "url": "https://example.org/source"}]
        return "No search results found", []

    monkeypatch.setattr(search, "comprehensive_web_search", fake_search)
    result = asyncio.run(WebSearchTool().execute('{"query":"Tokyo weather today","time_filter":"day"}', {}))
    assert calls == ["day", None]
    assert result["evidence_status"] == "available"


def test_empty_news_search_does_not_accept_old_articles(monkeypatch):
    calls = []

    def fake_search(query, **kwargs):
        calls.append(kwargs["time_filter"])
        return "No search results found", []

    monkeypatch.setattr(search, "comprehensive_web_search", fake_search)
    result = asyncio.run(WebSearchTool().execute('{"query":"Tokyo news today","time_filter":"day"}', {}))
    assert calls == ["day"]
    assert result["evidence_status"] == "empty"


def test_weather_search_recovers_from_empty_results(monkeypatch):
    from src.agent_tools import weather_tools

    monkeypatch.setattr(search, "comprehensive_web_search", lambda *args, **kwargs: ("No search results found", []))
    monkeypatch.setattr(weather_tools.WeatherTool, "execute", lambda self, content, ctx: _weather_result(content))

    result = asyncio.run(WebSearchTool().execute(
        '{"query":"Tokyo weather today September 23 2026 current weather","time_filter":"day"}', {}
    ))
    assert result["evidence_status"] == "available"
    assert json.loads(result["output"])["location"] == "Tokyo"


async def _weather_result(content):
    return {"output": json.dumps({"location": json.loads(content)["location"]}),
            "exit_code": 0, "evidence_status": "available"}


def test_weather_query_location_extraction_is_bounded():
    assert weather_location_from_query("Tokyo weather today September 23 2026 current weather") == "Tokyo"
    assert weather_location_from_query("What is the weather in Tokyo today?") == "Tokyo"
    assert weather_location_from_query("weather today") is None


def test_weather_tool_is_only_relevant_for_weather_turns_and_followups():
    assert _weather_tool_relevant([], "Weather Tokyo")
    assert _weather_tool_relevant([], "Use get_weather for Tokyo")
    assert not _weather_tool_relevant([], "Summarize the release notes")
    history = [{"role": "user", "content": "Weather Tokyo"},
               {"role": "assistant", "content": "Current conditions in Tokyo..."}]
    assert _weather_tool_relevant(history, "What about tomorrow?")
    assert _weather_tool_relevant(history, "And tmrw?")
    assert _weather_tool_relevant(history, "Tmr?")
    assert not _weather_tool_relevant(history, "Review my Python code")
    assert not _weather_tool_relevant([], "And tmrw?")


def test_weather_followup_uses_recent_assistant_forecast_context():
    from src.agent_loop import _looks_like_contextual_weather_status_followup

    history = [
        {"role": "user", "content": "What's weather today"},
        {"role": "assistant", "content": "Today in Tokyo: 21 C and light rain."},
        {"role": "user", "content": "And tmrw?"},
    ]
    assert _looks_like_contextual_weather_status_followup(history, "And tmrw?")
    assert _weather_tool_relevant(history, "And tmrw?")
    assert not _weather_tool_relevant(history, "Review my Python code")


def test_web_search_does_not_bypass_disabled_weather_tool(monkeypatch):
    from src.agent_tools import weather_tools

    monkeypatch.setattr(search, "comprehensive_web_search", lambda *args, **kwargs: ("No search results found", []))

    async def unexpected_weather(self, content, ctx):
        raise AssertionError("weather tool was disabled")

    monkeypatch.setattr(weather_tools.WeatherTool, "execute", unexpected_weather)
    result = asyncio.run(WebSearchTool().execute(
        '{"query":"Tokyo weather"}', {"disabled_tools": {"get_weather"}},
    ))
    assert result["evidence_status"] == "empty"


def test_weather_and_browser_fallback_respect_explicit_tool_denial():
    for tool, content in (
        ("get_weather", '{"location":"Tokyo"}'),
        ("private_browser", '{"action":"open","url":"https://www.bing.com"}'),
    ):
        _, result = asyncio.run(execute_tool_block(
            ToolBlock(tool, content), disabled_tools={tool},
            security_context=NO_TOOL_SECURITY_CONTEXT,
        ))
        assert result["exit_code"] == 1
        assert "disabled by user" in result["error"]


def test_empty_search_browser_retry_is_bounded_and_policy_checked():
    empty = {"evidence_status": "empty", "exit_code": 0}
    assert _should_retry_empty_search_in_browser(empty, set(), None, False)
    assert not _should_retry_empty_search_in_browser(empty, set(), None, True)
    assert not _should_retry_empty_search_in_browser(empty, {"private_browser"}, None, False)
    assert not _should_retry_empty_search_in_browser(
        empty, set(), ToolPolicy(disabled_tools=frozenset({"private_browser"})), False,
    )
    assert not _should_retry_empty_search_in_browser(
        {"evidence_status": "available"}, set(), None, False,
    )
