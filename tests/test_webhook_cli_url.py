import importlib.machinery
import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]


def _load_cli(monkeypatch):
    # Stub core.database so the CLI imports cleanly without a real DB.
    db_mod = types.ModuleType("core.database")
    db_mod.SessionLocal = MagicMock()
    db_mod.ScheduledTask = MagicMock()
    core_pkg = sys.modules.get("core") or types.ModuleType("core")
    monkeypatch.setitem(sys.modules, "core", core_pkg)
    monkeypatch.setitem(sys.modules, "core.database", db_mod)

    path = ROOT / "scripts" / "odysseus-webhook"
    loader = importlib.machinery.SourceFileLoader("odysseus_webhook_cli", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def test_cmd_url_targets_real_task_webhook_route(monkeypatch):
    cli = _load_cli(monkeypatch)

    task = types.SimpleNamespace(id="task-123", name="Nightly", webhook_token="tok-abc")

    fake_db = MagicMock()
    fake_db.get.return_value = task
    monkeypatch.setattr(cli, "SessionLocal", lambda: fake_db)

    captured = {}
    monkeypatch.setattr(cli, "emit", lambda payload, args: captured.update(payload))

    args = types.SimpleNamespace(id="task-123", base="https://app.example.com", pretty=False)
    cli.cmd_url(args)

    # The live inbound route is POST /api/tasks/{task_id}/webhook/{token}
    assert captured["url"] == "https://app.example.com/api/tasks/task-123/webhook/tok-abc"
    assert captured["curl"].endswith("/api/tasks/task-123/webhook/tok-abc")
