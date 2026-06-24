"""Admin routes for internal secret-store configuration."""

from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

import routes.secret_store_routes as routes
from src.secrets_store import LocalEncryptedSecretStore, SecretStoreUnavailable


def _client(monkeypatch) -> TestClient:
    monkeypatch.setattr(routes, "require_admin", lambda request: None)
    app = FastAPI()
    app.include_router(routes.setup_secret_store_routes())
    return TestClient(app)


def test_get_config_lists_openbao_integrations_without_token(monkeypatch):
    monkeypatch.setattr(
        routes,
        "load_secret_store_config",
        lambda: {
            "backend": "local",
            "integration_id": "",
            "mount": "secret",
            "prefix": "odysseus/internal",
        },
    )
    monkeypatch.setattr(
        routes,
        "resolve_secret_store_config",
        lambda: (
            {
                "backend": "local",
                "integration_id": "",
                "mount": "secret",
                "prefix": "odysseus/internal",
            },
            [],
        ),
    )
    monkeypatch.setattr(
        routes,
        "load_integrations",
        lambda: [
            {
                "id": "bao",
                "name": "OpenBao",
                "preset": "openbao",
                "base_url": "http://bao",
                "api_key": "must-not-leak",
                "enabled": True,
            },
            {"id": "other", "name": "Gitea", "preset": "gitea"},
        ],
    )

    data = _client(monkeypatch).get("/api/admin/secret-store").json()

    assert data["integrations"] == [
        {
            "id": "bao",
            "name": "OpenBao",
            "base_url": "http://bao",
            "enabled": True,
            "token_set": True,
        }
    ]
    assert "must-not-leak" not in str(data)


def test_save_local_config_applies_immediately(monkeypatch):
    store = MagicMock(spec=LocalEncryptedSecretStore)
    monkeypatch.setattr(
        routes, "resolve_secret_store_config", lambda: ({"backend": "local"}, [])
    )
    monkeypatch.setattr(routes, "build_secret_store", lambda **kwargs: store)
    monkeypatch.setattr(
        routes,
        "save_secret_store_config",
        lambda config: {
            "backend": "local",
            "integration_id": "",
            "mount": "secret",
            "prefix": "odysseus/internal",
        },
    )
    configured = []
    monkeypatch.setattr(routes, "configure_secret_store", configured.append)

    response = _client(monkeypatch).post(
        "/api/admin/secret-store",
        json={
            "enabled": False,
            "integration_id": "",
            "mount": "secret",
            "prefix": "odysseus/internal",
        },
    )

    assert response.status_code == 200
    assert configured == [store]


def test_save_rejects_environment_control(monkeypatch):
    monkeypatch.setattr(
        routes,
        "resolve_secret_store_config",
        lambda: (
            {"backend": "openbao"},
            ["ODYSSEUS_SECRET_STORE_BACKEND"],
        ),
    )

    response = _client(monkeypatch).post(
        "/api/admin/secret-store",
        json={"enabled": False},
    )

    assert response.status_code == 409
    assert "ODYSSEUS_SECRET_STORE_BACKEND" in response.json()["detail"]


def test_test_endpoint_reports_backend_failure(monkeypatch):
    store = MagicMock()
    store.probe.side_effect = SecretStoreUnavailable("vault unavailable")
    monkeypatch.setattr(routes, "build_secret_store", lambda **kwargs: store)
    monkeypatch.setattr(routes, "OpenBaoSecretStore", type(store))

    response = _client(monkeypatch).post(
        "/api/admin/secret-store/test",
        json={
            "enabled": True,
            "integration_id": "bao",
            "mount": "secret",
            "prefix": "odysseus/internal",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"ok": False, "error": "vault unavailable"}
