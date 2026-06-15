import asyncio
import json
from pathlib import Path

from src.agent_tools import ToolBlock, TOOL_TAGS  # noqa: E402  (import first to avoid circular)
from src.tool_execution import execute_tool_block
from src.tool_index import BUILTIN_TOOL_DESCRIPTIONS
from src.tool_security import is_public_blocked_tool, plan_mode_disabled_tools


def test_plot_chart_returns_interactive_chart_spec():
    spec = {
        "chart_type": "line",
        "title": "Monthly revenue",
        "x": ["Jan", "Feb", "Mar"],
        "series": [
            {"name": "Actual", "y": [10, 14, 18]},
            {"name": "Forecast", "y": [9, 15, 20]},
        ],
        "x_label": "Month",
        "y_label": "USD",
    }
    desc, result = asyncio.run(
        execute_tool_block(
            ToolBlock("plot_chart", json.dumps(spec)),
            session_id="session-1",
            owner="alice",
        )
    )

    assert desc == "plot_chart"
    assert result["exit_code"] == 0
    assert result["results"] == "Prepared interactive line chart: Monthly revenue"
    assert "image_url" not in result
    assert "image_model" not in result
    assert result["chart_spec"] == {
        "version": 1,
        "chart_type": "line",
        "title": "Monthly revenue",
        "x_label": "Month",
        "y_label": "USD",
        "grid": True,
        "legend": True,
        "series": [
            {"name": "Actual", "x": ["Jan", "Feb", "Mar"], "y": [10.0, 14.0, 18.0]},
            {"name": "Forecast", "x": ["Jan", "Feb", "Mar"], "y": [9.0, 15.0, 20.0]},
        ],
    }


def test_plot_chart_normalizes_pie_chart():
    spec = {"chart_type": "pie", "title": "Share", "labels": ["A", "B"], "values": [3, 7]}
    _, result = asyncio.run(execute_tool_block(ToolBlock("plot_chart", json.dumps(spec))))

    assert result["exit_code"] == 0
    assert result["chart_spec"]["labels"] == ["A", "B"]
    assert result["chart_spec"]["values"] == [3.0, 7.0]
    assert "series" not in result["chart_spec"]


def test_plot_chart_normalizes_histogram_chart():
    spec = {"chart_type": "histogram", "values": [1, 2, 2, 4], "bins": 3}
    _, result = asyncio.run(execute_tool_block(ToolBlock("plot_chart", json.dumps(spec))))

    assert result["exit_code"] == 0
    assert result["chart_spec"]["values"] == [1.0, 2.0, 2.0, 4.0]
    assert result["chart_spec"]["bins"] == 3


def test_plot_chart_rejects_non_json_content():
    _, result = asyncio.run(execute_tool_block(ToolBlock("plot_chart", "import os\nos.remove('x')")))

    assert result["exit_code"] == 1
    assert "valid JSON" in result["error"]


def test_plot_chart_rejects_mismatched_series_lengths():
    spec = {"chart_type": "bar", "x": ["A", "B"], "y": [1]}
    _, result = asyncio.run(execute_tool_block(ToolBlock("plot_chart", json.dumps(spec))))

    assert result["exit_code"] == 1
    assert "same length" in result["error"]


def test_plot_chart_serializer_round_trips_structured_args():
    from src.tool_schemas import function_call_to_tool_block

    args = {"chart_type": "scatter", "x": [1, 2], "y": [3, 4], "title": "Points"}
    block = function_call_to_tool_block("plot_chart", json.dumps(args))

    assert block is not None
    assert block.tool_type == "plot_chart"
    assert json.loads(block.content) == args


def test_plot_chart_registered_everywhere():
    assert "plot_chart" in TOOL_TAGS
    assert "plot_chart" in BUILTIN_TOOL_DESCRIPTIONS
    assert is_public_blocked_tool("plot_chart") is False
    assert "plot_chart" in plan_mode_disabled_tools()

    from src.tool_schemas import FUNCTION_TOOL_SCHEMAS

    names = {s["function"]["name"] for s in FUNCTION_TOOL_SCHEMAS}
    assert "plot_chart" in names


def test_plot_chart_has_fenced_tool_prompt_section():
    from src.agent_loop import TOOL_SECTIONS, _assemble_prompt

    assert "plot_chart" in TOOL_SECTIONS
    prompt = _assemble_prompt({"plot_chart"})
    assert "```plot_chart" in prompt
    assert "never Python code" in prompt
    assert "Chart.js" in prompt
    assert "even if no separate skill exists" in prompt
    assert "not from the Skills catalog" in prompt
    assert "matplotlib" not in TOOL_SECTIONS["plot_chart"]


def test_plot_chart_frontend_contract_is_wired():
    repo = Path(__file__).resolve().parent.parent
    renderer = (repo / "static" / "js" / "chatRenderer.js").read_text(encoding="utf-8")
    chat = (repo / "static" / "js" / "chat.js").read_text(encoding="utf-8")
    chart_bubble = (repo / "static" / "js" / "chartBubble.js").read_text(encoding="utf-8")
    agent_loop = (repo / "src" / "agent_loop.py").read_text(encoding="utf-8")

    assert "buildChartBubble" in renderer
    assert "json.chart_spec" in chat
    assert "ev.chart_spec" in renderer
    assert 'tool_output_data["chart_spec"]' in agent_loop
    assert 'tool_event["chart_spec"]' in agent_loop
    assert "_skip_skills_for_plot" in agent_loop
    assert '_relevant_tools.discard("manage_skills")' in agent_loop
    assert "/static/lib/chart.umd.min.js" in chart_bubble
    assert "cdn.jsdelivr.net/npm/chart.js" not in chart_bubble
    assert "toBase64Image('image/png'" in chart_bubble
