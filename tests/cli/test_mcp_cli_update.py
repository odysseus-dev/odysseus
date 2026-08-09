from types import SimpleNamespace

import pytest

from tests.helpers.cli_loader import load_script
from tests.helpers.db_stubs import make_core_db_stub


class _Server:
    def __init__(self, server_id, *, args='["old"]', env='{"OLD":"value"}', is_enabled=False):
        self.id = server_id
        self.name = "LSP Code Intelligence (Pinned)"
        self.transport = "stdio"
        self.command = "node"
        self.args = args
        self.env = env
        self.url = None
        self.is_enabled = is_enabled
        self.oauth_config = None
        self.created_at = None


class _Db:
    def __init__(self, rows):
        self.rows = rows
        self.commits = 0
        self.closed = False

    def get(self, _model, server_id):
        return self.rows.get(server_id)

    def commit(self):
        self.commits += 1

    def close(self):
        self.closed = True


def _load(monkeypatch, rows):
    make_core_db_stub(monkeypatch, models=["McpServer"])
    cli = load_script("odysseus-mcp")
    db = _Db(rows)
    monkeypatch.setattr(cli, "SessionLocal", lambda: db)
    captured = {}
    monkeypatch.setattr(cli, "emit", lambda payload, _args: captured.update(payload))
    monkeypatch.setattr(cli, "fail", lambda message: (_ for _ in ()).throw(ValueError(message)))
    return cli, db, captured


def test_update_rejects_invalid_json_shapes(monkeypatch):
    cli, _db, _captured = _load(monkeypatch, {})

    with pytest.raises(ValueError, match="invalid args"):
        cli._parse_update_json("{bad", "args", list)
    with pytest.raises(ValueError, match="expected JSON object"):
        cli._parse_update_json("[]", "env", dict)


def test_update_changes_only_explicit_fields_and_redacts_output(monkeypatch):
    target = _Server("target")
    other = _Server("other", args='["untouched"]', env='{"KEEP":"safe"}', is_enabled=True)
    cli, db, captured = _load(monkeypatch, {"target": target, "other": other})

    cli.cmd_update(
        SimpleNamespace(
            id="target",
            args='["/opt/odysseus-lsp/node_modules/@treedy/lsp-mcp/dist/index.js"]',
            env='{"PATH":"/opt/odysseus-lsp/bin:/usr/bin"}',
            is_enabled="true",
            pretty=False,
        )
    )

    assert target.args == '["/opt/odysseus-lsp/node_modules/@treedy/lsp-mcp/dist/index.js"]'
    assert target.env == '{"PATH": "/opt/odysseus-lsp/bin:/usr/bin"}'
    assert target.is_enabled is True
    assert other.args == '["untouched"]'
    assert other.env == '{"KEEP":"safe"}'
    assert other.is_enabled is True
    assert db.commits == 1
    assert db.closed is True
    assert captured["env"] == {"PATH": "***"}


def test_update_unknown_id_fails_closed_without_commit(monkeypatch):
    cli, db, _captured = _load(monkeypatch, {})

    with pytest.raises(ValueError, match="no MCP server"):
        cli.cmd_update(
            SimpleNamespace(id="missing", args='[]', env=None, is_enabled=None, pretty=False)
        )

    assert db.commits == 0
    assert db.closed is True
