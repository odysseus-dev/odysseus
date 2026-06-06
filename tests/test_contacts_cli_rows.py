import importlib.machinery
import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock


ROOT = Path(__file__).resolve().parents[1]


def _load_cli(monkeypatch):
    routes = types.ModuleType("routes.contacts_routes")
    routes._get_carddav_config = MagicMock()
    routes._fetch_contacts = MagicMock()
    routes._create_contact = MagicMock()
    monkeypatch.setitem(sys.modules, "routes.contacts_routes", routes)
    path = ROOT / "scripts" / "odysseus-contacts"
    loader = importlib.machinery.SourceFileLoader("odysseus_contacts_cli", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def test_contact_rows_skips_invalid_rows(monkeypatch):
    cli = _load_cli(monkeypatch)

    assert cli._contact_rows([
        {"name": "Ada", "email": "ada@example.test"},
        "bad-row",
        None,
    ]) == [{"name": "Ada", "email": "ada@example.test"}]
