"""Notification history uses the real auth middleware and session ownership."""

import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("auth_enabled", [False, True], ids=["disabled", "enabled"])
def test_notification_log_authorization(tmp_path, auth_enabled):
    # Auth middleware and the database are configured at app import time.
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent("""
            from fastapi.testclient import TestClient

            from app import AUTH_ENABLED, app, auth_manager
            from core.database import NotificationLog, SessionLocal

            path = "/api/tasks/notification-logs"
            payload = {"title": "Editor", "body": "Draft retained", "status": "error", "owner": "other"}
            password = "test-only-notification-password"
            alice = {"username": "alice", "password": password}
            other = {"username": "other", "password": password}

            with TestClient(app) as client:
                if AUTH_ENABLED:
                    setup = client.post("/api/auth/setup", json=other)
                    assert setup.status_code == 200, setup.text
                    assert auth_manager.create_user("alice", password, is_admin=False)

                    # Configured server with no cookie. Neither read nor write is public.
                    denied_get = client.get(path)
                    denied_post = client.post(path, json=payload)
                    assert denied_get.status_code == denied_post.status_code == 401
                    with SessionLocal() as db:
                        assert db.query(NotificationLog).count() == 0

                    assert client.post("/api/auth/login", json=other).json()["ok"] is True
                    assert client.post(path, json={"body": "Other user's private notification"}).json() == {"success": True}
                    assert client.post("/api/auth/logout").json()["ok"] is True
                    assert client.post("/api/auth/login", json=alice).json()["ok"] is True
                    assert client.get(path).json() == {"notifications": []}
                    posted = client.post(path, json=payload)
                    assert posted.status_code == 200, posted.text
                    assert posted.json() == {"success": True}
                    rows = client.get(path).json()["notifications"]
                    assert len(rows) == 1, rows
                    assert (rows[0]["task_name"], rows[0]["body"], rows[0]["status"]) == (
                        "Editor", "Draft retained", "error",
                    )
                    with SessionLocal() as db:
                        assert db.query(NotificationLog).filter_by(body="Draft retained").one().owner == "alice"
                else:
                    posted = client.post(path, json=payload)
                    assert posted.status_code == 200, posted.text
                    assert posted.json() == {"success": False, "skipped": True}
                    response = client.get(path)
                    assert response.status_code == 200, response.text
                    assert response.json() == {"notifications": []}
                    with SessionLocal() as db:
                        assert db.query(NotificationLog).count() == 0
        """)],
        cwd=ROOT,
        env={
            **os.environ,
            "AUTH_ENABLED": str(auth_enabled).lower(),
            "LOCALHOST_BYPASS": "false",
            "SECURE_COOKIES": "false",
            "DATABASE_URL": f"sqlite:///{tmp_path / 'app.db'}",
            "ODYSSEUS_DATA_DIR": str(tmp_path),
            "CHROMADB_HOST": "127.0.0.1",
            "CHROMADB_PORT": "9",
            "CHROMADB_CONNECT_TIMEOUT": "0.01",
            "ODYSSEUS_DISABLE_MCP": "1",
            "ODYSSEUS_STARTUP_WARMUPS": "0",
            "ODYSSEUS_TOOL_INDEX_PREWARM": "0",
            "PYTHON_DOTENV_DISABLED": "1",
        },
        capture_output=True,
        text=True,
        timeout=45,
    )
    assert result.returncode == 0, result.stdout + result.stderr
