import os
import sys
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="POSIX mode bits (0o600) don't exist on Windows; safe_chmod no-ops there.",
)
def test_app_db_created_with_0600(tmp_path):
    """app.db holds secrets — it must not be world-readable.

    Note: under umask 077 a fresh sqlite file is born 0600 and this would pass
    even without the chmod; dev/CI umask is 022, where the chmod is what makes
    it pass. No umask machinery needed — just don't read a green here as proof
    on a 077 box.

    A subprocess (not in-process patching) is used deliberately: the engine
    binds to DATABASE_URL at import time, so a fresh interpreter with its own
    DATABASE_URL is the clean way to exercise init_db() against a real on-disk
    file without rebinding the already-imported engine.
    """
    db_file = tmp_path / "app.db"
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{db_file}"}
    repo_root = Path(__file__).resolve().parents[1]
    # Importing core.database runs init_db() against the temp file-backed DB.
    # cwd=repo_root so `import core` resolves (the `-c` sys.path[0] is the CWD).
    subprocess.run(
        [sys.executable, "-c", "import core.database"],
        env=env,
        cwd=repo_root,
        check=True,
    )
    assert db_file.exists()
    mode = db_file.stat().st_mode & 0o777
    assert mode == 0o600, f"expected 0o600, got 0o{mode:o}"

    # Upgrade path: an already-deployed DB sitting at 0644 must be re-corrected
    # on the next startup. The chmod is unconditional (not gated on create_all
    # having created the file), so this is the common path for existing installs.
    db_file.chmod(0o644)
    subprocess.run(
        [sys.executable, "-c", "import core.database"],
        env=env,
        cwd=repo_root,
        check=True,
    )
    assert db_file.stat().st_mode & 0o777 == 0o600, "existing 0644 DB not re-locked on startup"


def test_normalize_sqlite_url_preserves_sqlite_uri_filename():
    """URI filenames must reach SQLAlchemy unchanged for SQLite to parse."""
    from core.database import _normalize_sqlite_url

    url = "sqlite:///file:/tmp/app.db?mode=rwc&uri=true"
    assert _normalize_sqlite_url(url) == url


def test_sqlite_db_path_handles_driver_and_query_forms():
    """The path fed to chmod must come from SQLAlchemy's parsed URL, not a naive
    replace("sqlite:///"). A driver-qualified URL (sqlite+pysqlite://) or one
    carrying query args (?cache=shared) would otherwise resolve to the wrong
    path and leave the real file world-readable. Pure logic — runs everywhere.
    """
    from sqlalchemy.engine import make_url

    from core.database import _sqlite_db_path

    # Plain forms (relative + absolute) resolve to the file path.
    assert _sqlite_db_path(make_url("sqlite:///data/app.db")) == "data/app.db"
    assert _sqlite_db_path(make_url("sqlite:////abs/app.db")) == "/abs/app.db"
    # A driver qualifier must not defeat detection...
    assert _sqlite_db_path(make_url("sqlite+pysqlite:///data/app.db")) == "data/app.db"
    # ...and query args must be stripped from the path.
    assert _sqlite_db_path(make_url("sqlite:///data/app.db?cache=shared")) == "data/app.db"
    assert _sqlite_db_path(make_url("sqlite+pysqlite:////abs/app.db?mode=ro")) == "/abs/app.db"
    # Nothing to lock for non-file-backed or non-sqlite databases.
    assert _sqlite_db_path(make_url("sqlite:///:memory:")) is None
    assert _sqlite_db_path(make_url("sqlite://")) is None
    assert _sqlite_db_path(make_url("postgresql+psycopg2://u:p@h/db")) is None


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="POSIX mode bits (0o600) don't exist on Windows; safe_chmod no-ops there.",
)
def test_app_db_sidecars_relocked(tmp_path):
    """Stale SQLite sidecars (-wal/-shm) left by an older 0o644 install hold
    copies of DB pages, so startup must re-lock them too — not just app.db.

    The default -journal is transient (SQLite deletes it after the create_all
    commit), so it isn't asserted on here; -wal/-shm persist and are the real
    exposure once WAL has ever been enabled.
    """
    import sqlite3

    db_file = tmp_path / "app.db"
    sqlite3.connect(db_file).close()  # a real, pre-existing DB ...
    db_file.chmod(0o644)
    sidecars = [tmp_path / f"app.db{sfx}" for sfx in ("-wal", "-shm")]
    for s in sidecars:
        s.write_bytes(b"")
        s.chmod(0o644)

    env = {**os.environ, "DATABASE_URL": f"sqlite:///{db_file}"}
    repo_root = Path(__file__).resolve().parents[1]
    subprocess.run(
        [sys.executable, "-c", "import core.database"],
        env=env,
        cwd=repo_root,
        check=True,
    )

    assert db_file.stat().st_mode & 0o777 == 0o600
    for s in sidecars:
        assert s.stat().st_mode & 0o777 == 0o600, f"{s.name} not re-locked on startup"


def test_sqlite_db_path_handles_file_uri_forms(tmp_path):
    """SQLite URI filenames must chmod the real filesystem path, not the
    literal file: URI string. Memory URI databases should still be skipped."""
    from sqlalchemy.engine import make_url

    from core.database import _sqlite_db_path

    db_file = tmp_path / "uri-app.db"

    assert (
        _sqlite_db_path(make_url(f"sqlite+pysqlite:///file:{db_file}?mode=rwc&uri=true"))
        == str(db_file)
    )
    assert (
        _sqlite_db_path(make_url(f"sqlite:///file:{db_file}?cache=shared&uri=true"))
        == str(db_file)
    )

    localhost_db = tmp_path / "localhost-uri.db"
    assert (
        _sqlite_db_path(
            make_url(
                f"sqlite+pysqlite:///file://localhost{localhost_db}"
                "?mode=rwc&uri=true"
            )
        )
        == str(localhost_db)
    )

    non_uri_mode_db = tmp_path / "mode-query-file.db"
    assert (
        _sqlite_db_path(
            make_url(
                f"sqlite+pysqlite:///{non_uri_mode_db}?mode=memory"
            )
        )
        == str(non_uri_mode_db)
    )
    assert (
        _sqlite_db_path(make_url("sqlite+pysqlite:///file::memory:?cache=shared&uri=true"))
        is None
    )
    assert (
        _sqlite_db_path(make_url("sqlite+pysqlite:///file:memdb1?mode=memory&cache=shared&uri=true"))
        is None
    )


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="POSIX mode bits (0o600) don't exist on Windows; safe_chmod no-ops there.",
)
def test_app_db_file_uri_created_with_0600(tmp_path):
    """Import-time DB initialization must lock SQLite file: URI databases too."""
    db_file = tmp_path / "uri-app.db"
    env = {
        **os.environ,
        "DATABASE_URL": f"sqlite+pysqlite:///file:{db_file}?mode=rwc&uri=true",
    }
    repo_root = Path(__file__).resolve().parents[1]

    subprocess.run(
        [sys.executable, "-c", "import core.database"],
        env=env,
        cwd=repo_root,
        check=True,
    )

    assert db_file.exists()
    mode = db_file.stat().st_mode & 0o777
    assert mode == 0o600, f"expected 0o600, got 0o{mode:o}"

@pytest.mark.skipif(
    sys.platform == "win32",
    reason="POSIX mode bits (0o600) don't exist on Windows; safe_chmod no-ops there.",
)
def test_app_db_localhost_file_uri_created_with_0600(tmp_path):
    """A file://localhost URI must chmod the local path SQLite opens."""
    db_file = tmp_path / "localhost-uri.db"
    env = {
        **os.environ,
        "DATABASE_URL": (
            f"sqlite+pysqlite:///file://localhost{db_file}"
            "?mode=rwc&uri=true"
        ),
    }
    repo_root = Path(__file__).resolve().parents[1]

    subprocess.run(
        [sys.executable, "-c", "import core.database"],
        env=env,
        cwd=repo_root,
        check=True,
    )

    assert db_file.exists()
    mode = db_file.stat().st_mode & 0o777
    assert mode == 0o600, f"expected 0o600, got 0o{mode:o}"


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="POSIX mode bits (0o600) don't exist on Windows; safe_chmod no-ops there.",
)
def test_app_db_non_uri_mode_query_created_with_0600(tmp_path):
    """mode=memory without uri=true must not hide a real SQLite file."""
    db_file = tmp_path / "mode-query-file.db"
    env = {
        **os.environ,
        "DATABASE_URL": f"sqlite+pysqlite:///{db_file}?mode=memory",
    }
    repo_root = Path(__file__).resolve().parents[1]

    subprocess.run(
        [sys.executable, "-c", "import core.database"],
        env=env,
        cwd=repo_root,
        check=True,
    )

    assert db_file.exists()
    mode = db_file.stat().st_mode & 0o777
    assert mode == 0o600, f"expected 0o600, got 0o{mode:o}"

@pytest.mark.skipif(
    sys.platform == "win32",
    reason="POSIX mode bits (0o600) don't exist on Windows; safe_chmod no-ops there.",
)
def test_app_db_plain_file_uri_created_with_0600(tmp_path):
    """The documented sqlite:///file: URI form must remain protected."""
    db_file = tmp_path / "plain-uri-app.db"
    env = {
        **os.environ,
        "DATABASE_URL": f"sqlite:///file:{db_file}?mode=rwc&uri=true",
    }
    repo_root = Path(__file__).resolve().parents[1]

    subprocess.run(
        [sys.executable, "-c", "import core.database"],
        env=env,
        cwd=repo_root,
        check=True,
    )

    assert db_file.exists()
    mode = db_file.stat().st_mode & 0o777
    assert mode == 0o600, f"expected 0o600, got 0o{mode:o}"
