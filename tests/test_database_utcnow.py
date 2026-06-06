import types

import pytest

sqlalchemy = pytest.importorskip("sqlalchemy")
if not isinstance(sqlalchemy, types.ModuleType):
    pytest.skip("sqlalchemy is stubbed in this environment", allow_module_level=True)

from core.database import ChatMessage, DocumentVersion, Session, TaskRun, UserToolData, utcnow_naive


def test_utcnow_naive_returns_naive_utc_datetime():
    now = utcnow_naive()

    assert now.tzinfo is None
    assert abs((now - utcnow_naive()).total_seconds()) < 2


def test_database_timestamp_defaults_use_utcnow_naive():
    defaults = (
        Session.created_at.default.arg,
        Session.updated_at.default.arg,
        Session.updated_at.onupdate.arg,
        ChatMessage.timestamp.default.arg,
        DocumentVersion.created_at.default.arg,
        UserToolData.created_at.default.arg,
        UserToolData.updated_at.default.arg,
        UserToolData.updated_at.onupdate.arg,
        TaskRun.started_at.default.arg,
    )

    for fn in defaults:
        assert fn.__name__ == "utcnow_naive"
