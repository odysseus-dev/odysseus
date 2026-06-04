from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_api_call_is_hidden_when_no_integrations_are_configured():
    source = (ROOT / "src" / "agent_loop.py").read_text(encoding="utf-8")

    assert "def _has_enabled_api_integrations" in source
    assert 'disabled_tools.add("api_call")' in source


def test_api_call_schema_says_external_integrations_only():
    source = (ROOT / "src" / "tool_schemas.py").read_text(encoding="utf-8")

    assert "external registered API integration only" in source
    assert "Do not use this for Odysseus' own /api/* routes" in source
    assert "Not 'Memory' or another internal Odysseus surface" in source
