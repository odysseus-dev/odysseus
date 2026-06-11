from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_api_call_implementation_checks_integrations():
    source = (ROOT / "src" / "tool_implementations.py").read_text(encoding="utf-8")

    assert 'load_integrations' in source
    assert "none configured" in source


def test_api_call_in_tool_index():
    source = (ROOT / "src" / "tool_index.py").read_text(encoding="utf-8")

    assert '"api_call"' in source or "'api_call'" in source


def test_api_call_schema_describes_api_call_tool():
    source = (ROOT / "src" / "tool_schemas.py").read_text(encoding="utf-8")

    assert "api_call" in source
    assert "Generic loopback" in source or "registered API integration" in source
