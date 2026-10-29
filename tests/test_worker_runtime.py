"""The default namespace must protect callers and simultaneous pytest runs."""

import os
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from tests.helpers.worker_runtime import isolated_runtime


@pytest.mark.parametrize("fail", [False, True])
def test_runtime_restores_environment_and_removes_files(monkeypatch, tmp_path, fail):
    caller = tmp_path / "caller"
    caller.mkdir()
    sentinel = caller / "sentinel"
    sentinel.write_text("keep")
    for name in ("ODYSSEUS_DATA_DIR", "ODYSSEUS_MAIL_ATTACHMENTS_DIR",
                 "FASTEMBED_CACHE_PATH", "XDG_RUNTIME_DIR", "AGENT_BROWSER_SOCKET_DIR",
                 "TMPDIR", "TMP", "TEMP"):
        monkeypatch.setenv(name, str(caller))
    before = dict(os.environ)
    previous_tmp = tempfile.tempdir
    root = None
    try:
        with isolated_runtime("gw0") as root:
            assert root != caller
            assert "AGENT_BROWSER_SOCKET_DIR" not in os.environ
            for name in ("ODYSSEUS_DATA_DIR", "ODYSSEUS_MAIL_ATTACHMENTS_DIR",
                         "FASTEMBED_CACHE_PATH", "XDG_RUNTIME_DIR", "TMPDIR", "TMP", "TEMP"):
                path = Path(os.environ[name])
                assert path.is_dir() and path.is_relative_to(root)
                (path / "owned").write_text("test")
            assert os.environ["DATABASE_URL"] == f"sqlite:///{root / 'collection.db'}"
            assert Path(tempfile.gettempdir()).is_relative_to(root)
            if fail:
                raise RuntimeError("test failure")
    except RuntimeError:
        assert fail
    assert dict(os.environ) == before
    assert tempfile.tempdir == previous_tmp
    assert root is not None and not root.exists()
    assert sentinel.read_text() == "keep"
    assert list(caller.iterdir()) == [sentinel]


def test_same_worker_name_gets_distinct_namespaces():
    data_variable = "ODYSSEUS_DATA_DIR"
    with isolated_runtime("gw0") as first:
        (first / "data" / "state").write_text("first")
        with isolated_runtime("gw0") as second:
            assert first != second
            assert not (second / "data" / "state").exists()
            assert Path(os.environ[data_variable]) == second / "data"
        assert Path(os.environ[data_variable]) == first / "data"
        assert (first / "data" / "state").read_text() == "first"
    assert not first.exists() and not second.exists()


def test_subprocess_inherits_private_temp_and_data_directories():
    with isolated_runtime("gw1") as root:
        result = subprocess.run(
            [sys.executable, "-c", "import os,tempfile; from pathlib import Path; "
             "name = 'ODYSSEUS_DATA_DIR'; Path(os.environ[name], 'child').write_text('data'); "
             "Path(tempfile.gettempdir(), 'child').write_text('temp')"],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0, result.stderr
        assert (root / "data" / "child").read_text() == "data"
        assert (root / "tmp" / "child").read_text() == "temp"
    assert not root.exists()


@pytest.mark.skipif(not hasattr(socket, "AF_UNIX"), reason="requires AF_UNIX")
def test_tmp_path_under_private_runtime_fits_unix_socket(tmp_path):
    # pytest truncates this name to 30 characters, as for the real-tmux
    # witness. A nested pytest-of-<user> basetemp made it 110 bytes under xdist.
    with socket.socket(socket.AF_UNIX) as sock:
        sock.bind(str(tmp_path / "tmux.sock"))


def test_collection_database_survives_thread_connection_churn():
    """TestClient threads must not lose the collection database or its schema.

    SingletonThreadPool's default five-connection limit used to close the main
    thread's in-memory database after repeated route clients. Each new thread
    also saw an empty database. Exercise more threads than that limit and then
    verify both compatibility factories still see all registered tables.
    """
    from concurrent.futures import ThreadPoolExecutor
    from sqlalchemy import inspect
    import core.database as database
    import src.database as compatibility

    expected = set(database.Base.metadata.tables)

    def table_names(factory):
        with factory() as session:
            return set(inspect(session.connection()).get_table_names())

    assert {"documents", "model_endpoints", "mcp_servers", "calendars"} <= expected
    assert expected <= table_names(database.SessionLocal)
    for _ in range(8):
        # A fresh executor creates a fresh thread rather than reusing one.
        with ThreadPoolExecutor(max_workers=1) as executor:
            assert expected <= executor.submit(table_names, database.SessionLocal).result()
    assert expected <= table_names(database.SessionLocal)
    assert expected <= table_names(compatibility.SessionLocal)
