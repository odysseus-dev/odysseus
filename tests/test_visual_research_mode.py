from pathlib import Path

import pytest

from src.deep_research import CATEGORY_PROMPTS, _infer_research_category
from src.tool_schemas import FUNCTION_TOOL_SCHEMAS
from src.visual_report import _standard_visual_variant
from tests.helpers.stylesheets import app_css


ROOT = Path(__file__).resolve().parents[1]


def _tool_schema(name: str) -> dict:
    return next(
        schema["function"]
        for schema in FUNCTION_TOOL_SCHEMAS
        if schema.get("function", {}).get("name") == name
    )


def test_visual_research_mode_is_removed_from_ui_prompt_and_tool_schema():
    panel_source = (ROOT / "static/js/research/panel.js").read_text(encoding="utf-8")
    style_source = app_css()
    routes_source = (ROOT / "routes/research/research_routes.py").read_text(encoding="utf-8")
    category_schema = _tool_schema("trigger_research")["parameters"]["properties"]["category"]
    rounds_schema = _tool_schema("trigger_research")["parameters"]["properties"]["max_rounds"]

    assert '<option value="visual">' not in panel_source
    assert "visual: 'Visual explanation'" not in panel_source
    assert 'data-category="visual"' not in style_source
    assert "visual" not in category_schema["enum"]
    assert rounds_schema["minimum"] == 0
    assert "visual" not in CATEGORY_PROMPTS
    assert 'Literal["product", "comparison", "howto", "factcheck"]' in routes_source
    assert not (ROOT / "resources/skills/communication/visual-explainer/SKILL.md").exists()
    assert not (ROOT / "data/skills/communication/visual-explainer/SKILL.md").exists()


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("Best home cinema speakers in 2026", "product"),
        ("Where can I buy an Apple M3 Mini in Japan?", "product"),
        ("Compare Scala with Kotlin and Clojure", "comparison"),
        ("How to configure SearXNG step by step", "howto"),
        ("Fact-check the claim that coffee dehydrates you", "factcheck"),
        ("Does coffee really dehydrate you?", "factcheck"),
        ("Advantages and disadvantages of PostgreSQL and MySQL", "comparison"),
        ("Walk me through setting up SearXNG on Ubuntu", "howto"),
        ("What should I buy for a compact home cinema?", "product"),
        ("Explain how a transformer neural network works", None),
        ("Show the carbon cycle as a diagram", None),
        ("Current news in Sweden this week", None),
        ("The history of SearXNG", None),
        ("Python best practices for error handling", None),
    ],
)
def test_auto_format_uses_strong_intent_before_llm(question, expected):
    assert _infer_research_category(question) == expected


def test_standard_reports_use_one_format_palette():
    assert _standard_visual_variant("first report", "rp-one") == 0
    assert _standard_visual_variant("completely different report", "rp-two") == 0

    panel_source = (ROOT / "static/js/research/panel.js").read_text(encoding="utf-8")
    variant_function = panel_source.split("function _researchVisualVariant", 1)[1].split("\n}", 1)[0]
    assert "return 0;" in variant_function
    assert "charCodeAt" not in variant_function
