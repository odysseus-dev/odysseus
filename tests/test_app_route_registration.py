"""Application route-registration regressions."""

import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def test_companion_routes_are_not_registered(tmp_path):
    """Remove the app-specific bridge without dropping generic surfaces."""
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
                "print('ROUTES=' + json.dumps({"
                "path: sorted(methods) "
                "for path, methods in app.openapi()['paths'].items()"
                "}, sort_keys=True))"
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
    route_entries = json.loads(route_line.removeprefix("ROUTES="))
    route_methods = {
        (path, method)
        for path, methods in route_entries.items()
        for method in methods
    }

    assert not any(path.startswith("/api/companion") for path, _ in route_methods)

    preserved = {
        ("/", "get"),
        ("/api/auth/2fa/status", "get"),
        ("/api/claude/plugin.zip", "get"),
        ("/api/codex/capabilities", "get"),
        ("/api/models", "get"),
        ("/api/v1/chat", "post"),
        ("/api/webhooks", "get"),
    }
    assert preserved <= route_methods, preserved - route_methods
