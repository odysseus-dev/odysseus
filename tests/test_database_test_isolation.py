"""Guard database ownership at the helper and actual pytest lifecycle seams."""

import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest

from tests.helpers.database import isolated_session_database


@pytest.mark.parametrize("fail_inside", [False, True])
def test_session_database_restores_bindings_and_removes_files(tmp_path, fail_inside):
    import core.database as database
    import core.session_manager as manager
    import src.database as compatibility_database
    from core.models import ChatMessage

    modules = (database, compatibility_database, manager)
    names = ("DATABASE_URL", "engine", "SessionLocal", "Base", "Session", "ChatMessage")
    before = [{name: getattr(module, name) for name in names if hasattr(module, name)}
              for module in modules]
    previous_url = os.environ.get("DATABASE_URL")
    saved_manager_class = manager.SessionManager
    saved_db_session = manager.DbSession
    saved_db_message = manager.DbChatMessage
    listener = database.set_sqlite_pragma

    class IntentionalFailure(Exception):
        pass

    try:
        with isolated_session_database(tmp_path) as (sm, db_module):
            owned_path = Path(db_module.engine.url.database)
            assert owned_path.is_file()
            assert owned_path.is_relative_to(tmp_path)
            assert db_module.Session is saved_db_session
            assert db_module.ChatMessage is saved_db_message
            assert sm.__class__ is saved_manager_class
            assert compatibility_database.SessionLocal is manager.SessionLocal
            sm.create_session(session_id="owned", name="t", endpoint_url="x",
                              model="m", rag=False, owner="alice")
            sm.add_message("owned", ChatMessage("user", "keep"))
            sm.add_message("owned", ChatMessage("user", "remove"))
            assert sm.truncate_messages("owned", 1)
            with db_module.SessionLocal() as db:
                assert db.query(saved_db_message).filter_by(session_id="owned").count() == 1
                assert db.query(saved_db_session).filter_by(id="owned").one().message_count == 1
            if fail_inside:
                raise IntentionalFailure
    except IntentionalFailure:
        assert fail_inside

    assert os.environ.get("DATABASE_URL") == previous_url
    for module, bindings in zip(modules, before):
        for name, value in bindings.items():
            assert getattr(module, name) is value
    assert database.set_sqlite_pragma is listener
    assert manager.DbSession is saved_db_session
    assert manager.DbChatMessage is saved_db_message
    assert manager.SessionManager is saved_manager_class
    assert not owned_path.parent.exists()

    with isolated_session_database(tmp_path) as (sm, db_module):
        assert db_module.engine.url.database != str(owned_path)
        with pytest.raises(KeyError, match="Session owned not found"):
            sm.get_session("owned")
        with db_module.SessionLocal() as db:
            assert db.query(saved_db_session).count() == 0


@pytest.mark.parametrize("truncation_first", [True, False])
def test_actual_tests_restore_process_state_and_ignore_inherited_database(tmp_path, truncation_first):
    # An inherited developer URL must never be opened, even during collection.
    inherited_db = tmp_path / "developer.db"
    sentinel = b"a developer database must not be opened or initialized"
    inherited_db.write_bytes(sentinel)
    inherited_url = f"sqlite:///{inherited_db}"
    truncation = "tests/test_truncate_message_count_regression.py"
    owner = "tests/test_manage_tasks_owner_scope.py::test_edit_allowed_for_matching_owner"
    manifest = [truncation, owner] if truncation_first else [owner, truncation]
    script = textwrap.dedent('''
        import os
        import sys
        import pytest

        def snapshot():
            import core
            import src
            import core.database as db
            import core.session_manager as sm
            import src.database as compat
            return (
                os.environ.get("DATABASE_URL"),
                sys.modules["core.database"], core.database,
                sys.modules["core.session_manager"], core.session_manager,
                sys.modules["src.database"], src.database,
                db.DATABASE_URL, db.engine, db.SessionLocal, db.Base,
                db.Session, db.ChatMessage, db.ScheduledTask, db.set_sqlite_pragma,
                compat.DATABASE_URL, compat.engine, compat.SessionLocal,
                compat.Session, compat.ChatMessage,
                sm.SessionLocal, sm.DbSession, sm.DbChatMessage, sm.SessionManager,
            )

        class StateGuard:
            def pytest_sessionstart(self):
                self.initial = snapshot()
                assert self.initial[0] == "sqlite:///:memory:"

            def pytest_collection_finish(self):
                assert snapshot() == self.initial, "collection changed database bindings"

            @pytest.hookimpl(hookwrapper=True, tryfirst=True)
            def pytest_runtest_teardown(self):
                yield
                assert snapshot() == self.initial, "test leaked database or module state"

        inherited_url = os.environ["DATABASE_URL"]
        result = pytest.main(["-q", "-p", "no:cacheprovider", *sys.argv[1:]],
                             plugins=[StateGuard()])
        assert os.environ["DATABASE_URL"] == inherited_url
        raise SystemExit(result)
    ''')
    result = subprocess.run(
        [sys.executable, "-c", script, *manifest],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "DATABASE_URL": inherited_url},
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "3 passed" in result.stdout
    assert inherited_db.read_bytes() == sentinel
    assert sorted(path.name for path in tmp_path.iterdir()) == ["developer.db"]
