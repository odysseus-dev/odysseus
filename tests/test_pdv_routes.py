"""Public HTTP contract tests for the PDV adapter boundary."""

import hashlib
import json
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient


class _AuthManager:
    is_configured = True

    @staticmethod
    def is_admin(username):
        return username == "owner"


def _client(repository_root: Path, runtime_state_provider=None, integration_probe=None) -> TestClient:
    from routes.pdv_routes import setup_pdv_routes

    app = FastAPI()
    app.state.auth_manager = _AuthManager()

    @app.middleware("http")
    async def stamp_test_user(request: Request, call_next):
        request.state.current_user = request.headers.get("X-Test-User")
        request.state.api_token = request.headers.get("X-Test-Api-Token") == "true"
        request.state.api_token_owner = request.headers.get("X-Test-Api-Owner")
        request.state.api_token_scopes = (request.headers.get("X-Test-Api-Scopes") or "").split(",")
        return await call_next(request)

    app.include_router(
        setup_pdv_routes(
            repository_root=repository_root,
            readiness_checker=lambda: {
                "ready": True,
                "version": "1.0.2",
                "checks": {"database": {"ok": True}, "data_dir": {"ok": True}},
            },
            runtime_state_provider=runtime_state_provider or (lambda: {
                "provider": None,
                "model": None,
                "currentRunStatus": "UNKNOWN",
                "taskCorrelationId": None,
                "failureMessage": None,
            }),
            integration_probe=integration_probe or (lambda _url, _key: _ready_probe()),
        )
    )
    return TestClient(app)


async def _ready_probe():
    return {
        "executionOsReachable": True,
        "pdvControlMcpConnected": True,
        "pdvControlBridgeVerified": True,
    }


def _snapshot(repository_root: Path):
    (repository_root / "PDV_UPSTREAM_SNAPSHOT.json").write_text(
        json.dumps(
            {
                "canonicalRepository": "https://github.com/odysseus-dev/odysseus",
                "upstreamCommit": "25c9e735ef5ce605f47f8f666ac6689056d2c10c",
                "upstreamBranch": "dev",
                "integrationBranch": "codex/pdv-integration-v1",
                "license": "AGPL-3.0-or-later",
            }
        ),
        encoding="utf-8",
    )
    _archive(repository_root)


def _archive(repository_root: Path, content: bytes = b"source archive"):
    archive = repository_root / "data" / "pdv-integration-v1" / "source" / "odysseus-corresponding-source.zip"
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.write_bytes(content)
    archive.with_suffix(".zip.json").write_text(
        json.dumps({"archiveSha256": hashlib.sha256(content).hexdigest()}), encoding="utf-8"
    )
    return archive


def _integrated_boundary(monkeypatch, key_file: Path, bind: str = "127.0.0.1"):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("APP_BIND", bind)
    monkeypatch.setenv("ODYSSEUS_PDV_ADAPTER_KEY_FILE", str(key_file))
    monkeypatch.setenv("PDV_EXECUTION_OS_URL", "http://127.0.0.1:4310")
    monkeypatch.setenv("PDV_PROVIDER_GUARD_REQUIRED", "true")
    monkeypatch.setenv("PDV_ADAPTER_KEY_ACL_VERIFIED", "true")


def test_pdv_routes_require_existing_admin_auth(tmp_path, monkeypatch):
    _snapshot(tmp_path)
    key_file = tmp_path / "adapter.key"
    key_file.write_text("a" * 64, encoding="utf-8")
    _integrated_boundary(monkeypatch, key_file)
    client = _client(tmp_path)

    assert client.get("/api/pdv/health").status_code == 403
    assert client.get("/api/pdv/source", headers={"X-Test-User": "member"}).status_code == 403


def test_pdv_health_reports_readiness_without_disclosing_secret(tmp_path, monkeypatch):
    _snapshot(tmp_path)
    key_file = tmp_path / "adapter.key"
    secret = "a" * 64
    key_file.write_text(secret, encoding="utf-8")
    _integrated_boundary(monkeypatch, key_file)

    response = _client(tmp_path).get("/api/pdv/health", headers={"X-Test-User": "owner"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["ready"] is True
    assert payload["boundary"] == {
        "loopback": True,
        "adapterKeyReferenceConfigured": True,
        "adapterKeyReadable": True,
        "adapterKeyAclRestricted": True,
        "executionOsConfigured": True,
        "executionOsReachable": True,
        "pdvControlMcpConnected": True,
        "pdvControlBridgeVerified": True,
        "providerGuardRequired": True,
    }
    assert payload["sourceArchiveAvailable"] is True
    assert len(payload["sourceArchiveSha256"]) == 64
    assert payload["capabilities"]["chat"] == "AVAILABLE"
    assert payload["capabilities"]["calendar"] == "AVAILABLE"
    assert payload["capabilities"]["email"] == "AUTH_REQUIRED"
    assert secret not in response.text
    assert str(key_file) not in response.text


def test_pdv_health_fails_closed_when_bind_is_not_loopback(tmp_path, monkeypatch):
    _snapshot(tmp_path)
    key_file = tmp_path / "adapter.key"
    key_file.write_text("a" * 64, encoding="utf-8")
    _integrated_boundary(monkeypatch, key_file, bind="0.0.0.0")

    response = _client(tmp_path).get("/api/pdv/health", headers={"X-Test-User": "owner"})

    assert response.status_code == 503
    assert response.json()["ready"] is False


def test_pdv_health_fails_closed_when_execution_os_guard_is_disconnected(tmp_path, monkeypatch):
    _snapshot(tmp_path)
    key_file = tmp_path / "adapter.key"
    key_file.write_text("a" * 64, encoding="utf-8")
    _integrated_boundary(monkeypatch, key_file)
    monkeypatch.delenv("PDV_EXECUTION_OS_URL")

    response = _client(tmp_path).get("/api/pdv/health", headers={"X-Test-User": "owner"})

    assert response.status_code == 503
    payload = response.json()
    assert payload["ready"] is False
    assert payload["boundary"]["executionOsConfigured"] is False
    assert payload["capabilities"]["mcp"] == "DEGRADED"


def test_pdv_health_fails_closed_when_live_bridge_or_mcp_child_is_missing(tmp_path, monkeypatch):
    _snapshot(tmp_path)
    key_file = tmp_path / "adapter.key"
    key_file.write_text("a" * 64, encoding="utf-8")
    _integrated_boundary(monkeypatch, key_file)

    async def disconnected(_url, _key):
        return {
            "executionOsReachable": True,
            "pdvControlMcpConnected": False,
            "pdvControlBridgeVerified": True,
        }

    response = _client(tmp_path, integration_probe=disconnected).get(
        "/api/pdv/health", headers={"X-Test-User": "owner"}
    )
    assert response.status_code == 503
    assert response.json()["ready"] is False
    assert response.json()["boundary"]["pdvControlMcpConnected"] is False


def test_default_integration_probe_requires_connected_child_and_governed_provenance(monkeypatch):
    import asyncio
    import src.tool_utils as tool_utils
    from routes import pdv_routes

    class Manager:
        def get_server_status(self, server_id):
            assert server_id == "pdv_control"
            return {"status": "connected", "tool_count": 7}

    class Response:
        status_code = 200
        def json(self):
            return {"allowed": True, "provenance": {"source_system": "pdv-execution-os", "tool_name": "health.status"}}

    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *_args): return None
        async def post(self, url, headers, json):
            assert url == "http://127.0.0.1:4310/v1/integrations/odysseus/mcp/invoke"
            assert headers == {"X-PDV-Odysseus-Key": "a" * 64}
            assert json["tool_name"] == "health.status"
            return Response()

    monkeypatch.setattr(tool_utils, "get_mcp_manager", lambda: Manager())
    monkeypatch.setattr(pdv_routes.httpx, "AsyncClient", lambda **_kwargs: Client())
    observed = asyncio.run(pdv_routes._default_integration_probe("http://127.0.0.1:4310", "a" * 64))
    assert observed == {
        "executionOsReachable": True,
        "pdvControlMcpConnected": True,
        "pdvControlBridgeVerified": True,
    }


def test_pdv_source_returns_attribution_without_local_paths(tmp_path, monkeypatch):
    _snapshot(tmp_path)
    monkeypatch.setenv("AUTH_ENABLED", "true")
    response = _client(tmp_path).get("/api/pdv/source", headers={"X-Test-User": "owner"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["license"] == "AGPL-3.0-or-later"
    assert payload["upstreamCommit"] == "25c9e735ef5ce605f47f8f666ac6689056d2c10c"
    assert payload["correspondingSourceRequired"] is True
    assert str(tmp_path) not in response.text


def test_pdv_health_reports_truthful_optional_runtime_state(tmp_path, monkeypatch):
    _snapshot(tmp_path)
    key_file = tmp_path / "adapter.key"
    key_file.write_text("a" * 64, encoding="utf-8")
    _integrated_boundary(monkeypatch, key_file)
    runtime = {
        "provider": "ollama",
        "model": "qwen3:8b",
        "currentRunStatus": "UNKNOWN",
        "taskCorrelationId": None,
        "failureMessage": None,
        "apiKey": "must-not-leak",
    }

    response = _client(tmp_path, runtime_state_provider=lambda: runtime).get(
        "/api/pdv/health", headers={"X-Test-User": "owner"}
    )

    assert response.status_code == 200
    assert response.json()["runtime"] == {
        "provider": "ollama",
        "model": "qwen3:8b",
        "currentRunStatus": "UNKNOWN",
        "taskCorrelationId": None,
        "failureMessage": None,
    }
    assert "must-not-leak" not in response.text


def test_pdv_routes_allow_only_scoped_admin_owned_api_tokens(tmp_path, monkeypatch):
    _snapshot(tmp_path)
    monkeypatch.setenv("AUTH_ENABLED", "true")
    client = _client(tmp_path)
    base = {"X-Test-Api-Token": "true", "X-Test-Api-Owner": "owner"}

    assert client.get("/api/pdv/source", headers=base).status_code == 403
    assert client.get(
        "/api/pdv/source", headers={**base, "X-Test-Api-Scopes": "pdv:read"}
    ).status_code == 200
    assert client.get(
        "/api/pdv/source",
        headers={**base, "X-Test-Api-Owner": "member", "X-Test-Api-Scopes": "pdv:read"},
    ).status_code == 403


def test_pdv_source_archive_is_hash_verified_and_never_discloses_path(tmp_path, monkeypatch):
    _snapshot(tmp_path)
    expected = b"verified archive bytes"
    archive = _archive(tmp_path, expected)
    monkeypatch.setenv("AUTH_ENABLED", "true")
    client = _client(tmp_path)

    response = client.get("/api/pdv/source/archive", headers={"X-Test-User": "owner"})
    assert response.status_code == 200
    assert response.content == expected
    assert response.headers["content-type"] == "application/zip"
    assert response.headers["digest"] == f"sha-256={hashlib.sha256(expected).hexdigest()}"
    assert str(tmp_path) not in response.text

    archive.write_bytes(b"tampered")
    assert client.get(
        "/api/pdv/source/archive", headers={"X-Test-User": "owner"}
    ).status_code == 503
