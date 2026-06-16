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
