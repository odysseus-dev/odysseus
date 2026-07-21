"""Application route-registration regressions."""

import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def test_companion_routes_are_not_registered(tmp_path):
    """The shipped ASGI app must not expose the removed companion bridge."""
    env = os.environ.copy()
    env.update({
        "AUTH_ENABLED": "false",
        "CHROMADB_CONNECT_TIMEOUT": "0.01",
        "CHROMADB_HOST": "127.0.0.1",
        "CHROMADB_PORT": "9",
        "DATABASE_URL": f"sqlite:///{tmp_path / 'app.db'}",
        "ODYSSEUS_DATA_DIR": str(tmp_path),
        "ODYSSEUS_DISABLE_MCP": "1",
        "OPENAI_API_KEY": "",
        "PYTHONPATH": str(ROOT),
        "PYTHON_DOTENV_DISABLED": "1",
    })
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import json; from app import app; "
                "print('ROUTES=' + json.dumps(sorted({"
                "getattr(route, 'path', '') for route in app.routes "
                "if getattr(route, 'path', '').startswith('/api/companion')"
                "})))"
            ),
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert probe.returncode == 0, probe.stderr
    route_line = next(
        (line for line in probe.stdout.splitlines() if line.startswith("ROUTES=")),
        None,
    )
    assert route_line is not None, probe.stdout
    assert json.loads(route_line.removeprefix("ROUTES=")) == []
