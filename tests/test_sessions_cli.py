import sys
from types import ModuleType
from types import SimpleNamespace

from tests.helpers.cli_loader import load_script


def _load_sessions_cli(monkeypatch):
    core_mod = ModuleType("core")
    database_mod = ModuleType("core.database")
    database_mod.SessionLocal = object
    database_mod.Session = object
    monkeypatch.setitem(sys.modules, "core", core_mod)
    monkeypatch.setitem(sys.modules, "core.database", database_mod)
    return load_script("odysseus-sessions")


def test_serialize_normalizes_numeric_counters(monkeypatch):
    cli = _load_sessions_cli(monkeypatch)
    session = SimpleNamespace(
        id="s1",
        name="chat",
        model="m",
        endpoint_url="",
        owner=None,
        folder=None,
        archived=False,
        rag=False,
        is_important=False,
        message_count="12",
        total_input_tokens="bad",
        total_output_tokens=None,
        last_accessed=None,
        created_at=None,
    )

    out = cli._serialize(session)

    assert out["message_count"] == 12
    assert out["total_input_tokens"] == 0
    assert out["total_output_tokens"] == 0
