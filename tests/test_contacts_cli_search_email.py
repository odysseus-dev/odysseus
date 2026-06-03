"""Regression: `odysseus-contacts search` must match by email.

`_fetch_contacts` returns contacts normalized by `_normalize_contact`, whose
shape is `{"uid", "name", "emails": [...], "phones": [...]}` — there is no
singular `email` key. `cmd_search` filtered on `c.get("email")`, which is
always None, so the email branch was dead: searching by an email address (or
domain) returned nothing even when a matching contact existed, despite the
help text advertising "filter by name/email substring". Match against the
`emails` list instead.
"""
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


def test_search_matches_by_email(monkeypatch):
    cli = _load_cli(monkeypatch)

    contact = {"uid": "1", "name": "Bob Roberts",
               "emails": ["alice@example.com"], "phones": []}
    cli._get_carddav_config = lambda: {"url": "https://dav.example"}
    cli._fetch_contacts = lambda *a, **k: [contact]

    captured = {}
    cli.emit = lambda payload, args: captured.setdefault("matches", payload)

    args = types.SimpleNamespace(query="alice@example", json=False, refresh=False)
    cli.cmd_search(args)

    # The query matches the contact's email, not its name. On the old code
    # (c.get("email")) this returned [] because there is no singular email key.
    assert captured["matches"] == [contact]
