import importlib.machinery
import importlib.util
import sys
import types
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import MagicMock


ROOT = Path(__file__).resolve().parents[1]


def _load_cli(monkeypatch):
    db = types.ModuleType("core.database")
    db.SessionLocal = MagicMock()
    db.ScheduledTask = MagicMock()
    monkeypatch.setitem(sys.modules, "core.database", db)
    path = ROOT / "scripts" / "odysseus-webhook"
    loader = importlib.machinery.SourceFileLoader("odysseus_webhook_cli", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def test_mask_token_handles_short_values(monkeypatch):
    cli = _load_cli(monkeypatch)

    assert cli._mask_token("") == ""
    assert cli._mask_token("short") == "***"
    assert cli._mask_token("abcdef1234567890") == "abcdef…7890"
    assert cli._mask_token("short", reveal=True) == "short"


def test_url_uses_task_webhook_route(monkeypatch):
    cli = _load_cli(monkeypatch)
    task = SimpleNamespace(
        id="task-123",
        name="Daily sync",
        webhook_token="tok_abc",
    )
    emitted = []

    class _Db:
        def get(self, model, ident):
            assert model is cli.ScheduledTask
            assert ident == task.id
            return task

        def close(self):
            pass

    monkeypatch.setattr(cli, "SessionLocal", lambda: _Db())
    monkeypatch.setattr(cli, "emit", lambda payload, args: emitted.append(payload))

    cli.cmd_url(SimpleNamespace(id=task.id, base="https://app.example.com/"))

    assert emitted == [{
        "task_id": task.id,
        "name": task.name,
        "url": "https://app.example.com/api/tasks/task-123/webhook/tok_abc",
        "curl": "curl -X POST https://app.example.com/api/tasks/task-123/webhook/tok_abc",
    }]
