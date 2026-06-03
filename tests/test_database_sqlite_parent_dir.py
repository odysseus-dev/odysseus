import os
import subprocess
import sys
import textwrap


def test_database_import_creates_missing_sqlite_parent_dir(tmp_path):
    db_path = tmp_path / "missing" / "nested" / "app.db"
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{db_path}"
    env["PYTHONPATH"] = repo_root + os.pathsep + env.get("PYTHONPATH", "")

    script = textwrap.dedent(
        f"""
        from pathlib import Path
        import core.database

        db_path = Path({str(db_path)!r})
        assert db_path.parent.is_dir()
        assert db_path.exists()
        """
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=repo_root,
        env=env,
        text=True,
        capture_output=True,
        timeout=15,
    )

    assert result.returncode == 0, result.stderr
