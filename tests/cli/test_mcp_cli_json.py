from tests.helpers.cli_loader import load_script
from tests.helpers.db_stubs import make_core_db_stub


def test_mcp_json_helpers_reject_wrong_shapes(monkeypatch):
    make_core_db_stub(monkeypatch, models=["McpServer"])
    cli = load_script("odysseus-mcp")

    assert cli._json_list('["a"]') == ["a"]
    assert cli._json_list('{"not":"list"}') == []
    assert cli._json_list("{bad") == []
    assert cli._json_dict('{"A":"B"}') == {"A": "B"}
    assert cli._json_dict('["bad"]') == {}
    assert cli._json_dict("{bad") == {}
