"""Regression: mcp CLI _serialize must not crash when env JSON is not an object.

`env_obj = json.loads(s.env)` can yield a list (e.g. env stored as "[1,2]").
`if redact_env and env_obj:` then called `env_obj.items()` -> AttributeError.
Guard with isinstance(dict).
"""
from types import SimpleNamespace

from tests.helpers.cli_loader import load_script
from tests.helpers.db_stubs import make_core_db_stub


def _srv(env):
    return SimpleNamespace(id="s1", name="n", transport="stdio", command="c", args="[]",
                           env=env, url=None, is_enabled=1, oauth_config=None, created_at=None)


def test_serialize_handles_list_env(monkeypatch):
    make_core_db_stub(monkeypatch, models=["McpServer"])
    cli = load_script("odysseus-mcp")
    out = cli._serialize(_srv("[1, 2]"))  # JSON array, not object
    assert out["id"] == "s1"


def test_serialize_redacts_dict_env(monkeypatch):
    make_core_db_stub(monkeypatch, models=["McpServer"])
    cli = load_script("odysseus-mcp")
    out = cli._serialize(_srv('{"API_KEY": "secret"}'))
    assert out["env"] == {"API_KEY": "***"}
