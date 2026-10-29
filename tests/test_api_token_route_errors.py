"""Authenticated handler failures must surface as errors, not false 401s."""

import os
from pathlib import Path
import subprocess
import sys
import textwrap


ROOT = Path(__file__).resolve().parents[1]


def test_real_bearer_middleware_does_not_catch_downstream_failures(tmp_path):
    env = os.environ.copy()
    env.update({
        "AUTH_ENABLED": "true", "LOCALHOST_BYPASS": "false",
        "ODYSSEUS_DATA_DIR": str(tmp_path), "ODYSSEUS_DISABLE_MCP": "1",
        "CHROMADB_HOST": "127.0.0.1", "CHROMADB_PORT": "9",
        "CHROMADB_CONNECT_TIMEOUT": "0.01", "PYTHON_DOTENV_DISABLED": "1",
        "DATABASE_URL": f"sqlite:///{tmp_path / 'app.db'}", "OPENAI_API_KEY": "",
    })
    probe = textwrap.dedent('''
        import bcrypt
        from types import SimpleNamespace
        from fastapi.testclient import TestClient
        import app as module

        module.auth_manager = SimpleNamespace(is_configured=True)
        module.app.state._token_cache_dirty = False
        raw = "ody_abcdefghijklmnopqrstuv_fake_test_token"
        hashed = bcrypt.hashpw(raw.encode(), bcrypt.gensalt(rounds=4)).decode()
        module._token_cache = {raw[:8]: [("test-id", hashed, "alice", ["email:read"])]}

        @module.app.get("/api/token-test-ok")
        async def ok(request: module.Request):
            return {"owner": request.state.api_token_owner}

        @module.app.get("/api/token-test-error")
        async def fail():
            raise TypeError("handler failed after authentication")

        client = TestClient(module.app, raise_server_exceptions=False)
        headers = {"Authorization": "Bearer " + raw}
        assert client.get("/api/token-test-ok", headers=headers).json() == {"owner": "alice"}
        response = client.get("/api/token-test-error", headers=headers)
        assert response.status_code == 500, (response.status_code, response.text)
        response = client.get("/api/token-test-ok", headers={"Authorization": "Bearer ody_invalid_token"})
        assert response.status_code == 401
        assert response.json() == {"error": "Invalid API token"}
        print("verified bearer middleware response statuses")
    ''')
    result = subprocess.run([sys.executable, "-c", probe], cwd=ROOT, env=env,
                            capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "verified bearer middleware response statuses" in result.stdout
