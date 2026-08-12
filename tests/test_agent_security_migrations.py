"""Portable migration coverage for persisted agent security state."""

from contextlib import contextmanager

import core.database as database


class _Inspector:
    def __init__(self, columns):
        self._columns = set(columns)

    def get_table_names(self):
        return ["sessions"]

    def get_columns(self, _table):
        return [{"name": name} for name in self._columns]


class _Connection:
    def __init__(self, statements):
        self._statements = statements

    def execute(self, statement):
        self._statements.append(str(statement))


class _Engine:
    def __init__(self):
        self.statements = []

    @contextmanager
    def begin(self):
        yield _Connection(self.statements)


def test_security_mode_migration_uses_sqlalchemy_engine(monkeypatch):
    engine = _Engine()
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "inspect", lambda _engine: _Inspector({"id"}))

    database._migrate_add_security_mode_column()

    assert engine.statements == [
        "ALTER TABLE sessions ADD COLUMN security_mode VARCHAR "
        "NOT NULL DEFAULT 'sandbox'"
    ]


def test_provenance_migration_adds_only_missing_columns(monkeypatch):
    engine = _Engine()
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(
        database,
        "inspect",
        lambda _engine: _Inspector({"id", "agent_external_untrusted_seen"}),
    )

    database._migrate_add_agent_provenance_columns()

    assert engine.statements == [
        "ALTER TABLE sessions ADD COLUMN agent_workspace_untrusted_seen BOOLEAN "
        "NOT NULL DEFAULT FALSE",
        "ALTER TABLE sessions ADD COLUMN agent_odysseus_untrusted_seen BOOLEAN "
        "NOT NULL DEFAULT FALSE",
        "ALTER TABLE sessions ADD COLUMN agent_private_data_seen BOOLEAN "
        "NOT NULL DEFAULT FALSE",
    ]
