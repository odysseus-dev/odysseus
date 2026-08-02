"""Integrated PDV proof against the real Odysseus ASGI application.

This deliberately runs in a child interpreter.  ``app.py`` resolves its data
root, authentication policy, database engine, and middleware at import time;
an isolated process is the only reliable way to prove that complete production
path without contaminating the developer's live data or pytest's module cache.
No lifespan is entered, so background schedulers and provider probes never run.
"""

import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap


_PROOF_PROGRAM = r"""
import asyncio
import json
import sys
from pathlib import Path

import bcrypt
import httpx

data_dir = Path(sys.argv[1]).resolve()
data_dir.mkdir(parents=True, exist_ok=True)
(data_dir / "auth.json").write_text(json.dumps({
    "users": {
        "owner": {"password_hash": "unused", "is_admin": True},
        "member": {"password_hash": "unused", "is_admin": False},
    }
}), encoding="utf-8")

import app as odysseus_app
from core.database import ApiToken, SessionLocal

tokens = {
    "owner_read": "ody_" + "A" * 43,
    "owner_chat": "ody_" + "B" * 43,
    "member_read": "ody_" + "C" * 43,
}

with SessionLocal() as db:
    for token_id, owner, scopes, raw in (
        ("pdvown01", "owner", "pdv:read", tokens["owner_read"]),
        ("pdvown02", "owner", "chat", tokens["owner_chat"]),
        ("pdvmem01", "member", "pdv:read", tokens["member_read"]),
    ):
        db.add(ApiToken(
            id=token_id,
            owner=owner,
            name=token_id,
            token_hash=bcrypt.hashpw(raw.encode(), bcrypt.gensalt()).decode(),
            token_prefix=raw[:8],
            scopes=scopes,
            is_active=True,
        ))
    db.commit()
odysseus_app.app.state._token_cache_dirty = True

expected_surfaces = {
    "chat": "/api/chat",
    "agents": "/api/assistant/run/{task_id}",
    "research": "/api/research/start",
    "documents": "/api/document",
    "notes": "/api/notes",
    "tasks": "/api/tasks",
    "scheduler": "/api/tasks/{task_id}/run",
    "providers": "/api/model-endpoints",
    "history": "/api/history/{session_id}",
    "email": "/api/email/accounts",
    "calendar": "/api/calendar/events",
}
paths = set(odysseus_app.app.openapi()["paths"])
missing = {name: path for name, path in expected_surfaces.items() if path not in paths}
assert not missing, missing

async def run():
    transport = httpx.ASGITransport(
        app=odysseus_app.app,
        client=("203.0.113.10", 443),
        raise_app_exceptions=True,
    )
    async with httpx.AsyncClient(transport=transport, base_url="http://odysseus.test") as client:
        unauthenticated = await client.get("/api/pdv/source")
        invalid = await client.get(
            "/api/pdv/source", headers={"Authorization": "Bearer ody_invalid-value"}
        )
        wrong_scope = await client.get(
            "/api/pdv/source",
            headers={"Authorization": "Bearer " + tokens["owner_chat"]},
        )
        wrong_owner = await client.get(
            "/api/pdv/source",
            headers={"Authorization": "Bearer " + tokens["member_read"]},
        )
        authorized = await client.get(
            "/api/pdv/source",
            headers={"Authorization": "Bearer " + tokens["owner_read"]},
        )
        await asyncio.sleep(0.05)

    assert unauthenticated.status_code == 401, unauthenticated.text
    assert invalid.status_code == 401, invalid.text
    assert wrong_scope.status_code == 403, wrong_scope.text
    assert wrong_owner.status_code == 403, wrong_owner.text
    assert authorized.status_code == 200, authorized.text
    payload = authorized.json()
    assert payload["canonicalRepository"] == "https://github.com/odysseus-dev/odysseus"
    assert payload["license"] == "AGPL-3.0-or-later"
    assert all(raw not in authorized.text for raw in tokens.values())
    assert str(data_dir) not in authorized.text
    return {
        "routeCount": len(paths),
        "nativeSurfaces": sorted(expected_surfaces),
        "bearerStatuses": {
            "unauthenticated": unauthenticated.status_code,
            "invalid": invalid.status_code,
            "wrongScope": wrong_scope.status_code,
            "nonAdminOwner": wrong_owner.status_code,
            "adminPdvRead": authorized.status_code,
        },
        "sourceCommit": payload["upstreamCommit"],
    }

print(json.dumps(asyncio.run(run()), sort_keys=True))
"""


def test_real_app_bearer_middleware_and_native_surface(tmp_path):
    repository_root = Path(__file__).resolve().parents[1]
    python = repository_root / ".venv" / "Scripts" / "python.exe"
    if not python.is_file():
        python = Path(sys.executable)

    data_dir = tmp_path / "isolated-data"
    key_file = tmp_path / "adapter.key"
    key_file.write_text("a" * 64, encoding="ascii")
    env = os.environ.copy()
    env.update({
        "AUTH_ENABLED": "true",
        "LOCALHOST_BYPASS": "false",
        "APP_BIND": "127.0.0.1",
        "ODYSSEUS_DATA_DIR": str(data_dir),
        "DATABASE_URL": "sqlite:///" + str(data_dir / "app.db"),
        "ODYSSEUS_PDV_ADAPTER_KEY_FILE": str(key_file),
        # The guard remains inert and cannot authorize an outbound provider call.
        "PDV_PROVIDER_GUARD_REQUIRED": "false",
    })

    result = subprocess.run(
        [str(python), "-c", textwrap.dedent(_PROOF_PROGRAM), str(data_dir)],
        cwd=repository_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    assert result.returncode == 0, (
        f"isolated proof failed ({result.returncode})\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    receipt = json.loads(result.stdout.strip().splitlines()[-1])
    assert receipt["routeCount"] >= 400
    assert receipt["bearerStatuses"] == {
        "adminPdvRead": 200,
        "invalid": 401,
        "nonAdminOwner": 403,
        "unauthenticated": 401,
        "wrongScope": 403,
    }
    assert receipt["nativeSurfaces"] == sorted([
        "agents", "calendar", "chat", "documents", "email", "history",
        "notes", "providers", "research", "scheduler", "tasks",
    ])
