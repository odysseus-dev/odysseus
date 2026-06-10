"""Coverage that the scheduled_tasks rebuild is transactional (DI-P8-01).

_migrate_add_task_automation_columns rebuilds a legacy scheduled_tasks table
(RENAME -> CREATE -> INSERT -> DROP) to make prompt/schedule/scheduled_time nullable.
Before P9 the four statements ran without an explicit transaction, so a mid-rebuild
failure could leave _old_scheduled_tasks renamed away with no/partial scheduled_tasks.
These tests assert the happy-path atomicity invariants (and, where deterministic, that a
forced failure rolls the rebuild back leaving the original table intact).
"""

import core.db_migrations as migrations
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool


_LEGACY_DDL = """
CREATE TABLE scheduled_tasks (
    id VARCHAR PRIMARY KEY,
    owner VARCHAR,
    name VARCHAR NOT NULL,
    prompt TEXT NOT NULL,
    schedule VARCHAR NOT NULL,
    scheduled_time VARCHAR,
    scheduled_day INTEGER,
    scheduled_date DATETIME,
    next_run DATETIME,
    last_run DATETIME,
    status VARCHAR,
    output_target VARCHAR,
    session_id VARCHAR,
    model VARCHAR,
    endpoint_url VARCHAR,
    run_count INTEGER,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
)
"""


def _legacy_engine():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with eng.begin() as conn:
        conn.execute(text(_LEGACY_DDL))
        conn.execute(text(
            "INSERT INTO scheduled_tasks (id, name, prompt, schedule, created_at, updated_at) "
            "VALUES ('t1', 'legacy task', 'do it', 'daily', '2024-01-01', '2024-01-01')"
        ))
    return eng


def _notnull_map(eng, table="scheduled_tasks"):
    with eng.connect() as conn:
        rows = list(conn.execute(text(f"PRAGMA table_info({table})")))
    return {r[1]: r[3] for r in rows}


def _table_names(eng):
    with eng.connect() as conn:
        rows = list(conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )))
    return {r[0] for r in rows}


def test_rebuild_preserves_row_and_makes_columns_nullable(monkeypatch):
    eng = _legacy_engine()
    # Sanity: legacy table is NOT NULL on prompt/schedule.
    nn = _notnull_map(eng)
    assert nn["prompt"] == 1 and nn["schedule"] == 1

    monkeypatch.setattr(migrations, "engine", eng)
    migrations._migrate_add_task_automation_columns()

    # (a) the original row survives
    with eng.connect() as conn:
        ids = [r[0] for r in conn.execute(text("SELECT id FROM scheduled_tasks"))]
    assert ids == ["t1"]

    # (b) prompt/schedule are now nullable — inserting a row with NULL prompt succeeds
    with eng.begin() as conn:
        conn.execute(text(
            "INSERT INTO scheduled_tasks (id, name, created_at, updated_at) "
            "VALUES ('t2', 'new task', '2024-02-01', '2024-02-01')"
        ))
    with eng.connect() as conn:
        cnt = conn.execute(text("SELECT COUNT(*) FROM scheduled_tasks")).scalar()
    assert cnt == 2

    # (c) no _old_scheduled_tasks table remains
    assert "_old_scheduled_tasks" not in _table_names(eng)
    # automation columns were added
    nn2 = _notnull_map(eng)
    assert "task_type" in nn2 and "trigger_counter" in nn2


def test_rebuild_rolls_back_on_failure(monkeypatch):
    """Force the DROP to fail mid-rebuild; the explicit transaction must roll back the
    whole rebuild so the original scheduled_tasks (with its row) is intact and no
    _old_scheduled_tasks is left behind."""
    eng = _legacy_engine()
    monkeypatch.setattr(migrations, "engine", eng)

    real_execute = type(eng.connect()).execute

    def failing_execute(self, clause, *args, **kwargs):
        sql = str(getattr(clause, "text", clause))
        if "DROP TABLE _old_scheduled_tasks" in sql:
            raise RuntimeError("injected drop failure")
        return real_execute(self, clause, *args, **kwargs)

    monkeypatch.setattr(type(eng.connect()), "execute", failing_execute)

    # The migration swallows its own exceptions (outer try/except logs a warning), so
    # this returns normally — but the rebuild transaction must have rolled back.
    migrations._migrate_add_task_automation_columns()

    monkeypatch.undo()  # restore execute so assertions can query

    names = _table_names(eng)
    # rebuild rolled back: original table intact, no leftover _old table
    assert "scheduled_tasks" in names
    assert "_old_scheduled_tasks" not in names
    with eng.connect() as conn:
        ids = [r[0] for r in conn.execute(text("SELECT id FROM scheduled_tasks"))]
    assert ids == ["t1"]
