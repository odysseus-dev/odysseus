"""Internal secret-store contract tests."""

import json
from unittest.mock import MagicMock, patch

import httpx
import pytest

from src.secrets_store import (
    LocalEncryptedSecretStore,
    OpenBaoSecretStore,
    SecretRef,
    SecretStore,
    SecretStoreConfigurationError,
    SecretStoreUnavailable,
    build_secret_store,
    configure_secret_store,
    get_secret_store,
    load_secret_store_config,
    migrate_secrets,
    resolve_secret_store_config,
    save_secret_store_config,
)


@pytest.fixture(autouse=True)
def isolate_fernet_key(tmp_path, monkeypatch):
    import src.secret_storage as secret_storage

    monkeypatch.setattr(secret_storage, "_KEY_PATH", tmp_path / ".app_key")
    monkeypatch.setattr(secret_storage, "_fernet", None)


def _response(status: int, body: dict | None = None) -> MagicMock:
    response = MagicMock()
    response.status_code = status
    response.json.return_value = body or {}
    return response


def test_local_store_round_trip_is_encrypted_on_disk(tmp_path):
    path = tmp_path / "secrets.json"
    store = LocalEncryptedSecretStore(path)

    store.set("integrations", "mail-token", "super-secret")

    assert store.get("integrations", "mail-token") == "super-secret"
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["integrations"]["mail-token"].startswith("enc:")
    assert "super-secret" not in path.read_text(encoding="utf-8")
    assert isinstance(store, SecretStore)


def test_local_store_missing_and_delete_are_idempotent(tmp_path):
    store = LocalEncryptedSecretStore(tmp_path / "secrets.json")

    assert store.get("mail", "password") is None
    store.delete("mail", "password")
    store.set("mail", "password", "pw")
    store.delete("mail", "password")
    store.delete("mail", "password")

    assert store.get("mail", "password") is None


def test_local_store_rejects_invalid_identifiers(tmp_path):
    store = LocalEncryptedSecretStore(tmp_path / "secrets.json")

    with pytest.raises(ValueError):
        store.set("../escape", "key", "value")
    with pytest.raises(ValueError):
        store.get("safe", "bad/key")


def test_local_store_distinguishes_corruption_from_missing(tmp_path):
    path = tmp_path / "secrets.json"
    path.write_text("{bad json", encoding="utf-8")
    store = LocalEncryptedSecretStore(path)

    with pytest.raises(SecretStoreUnavailable):
        store.get("mail", "password")


def test_openbao_store_uses_kv_v2_and_vault_token_header():
    store = OpenBaoSecretStore(
        url="http://bao.local:8200",
        token="token",
        mount="secret",
        prefix="odysseus/internal",
    )
    read_response = _response(
        200, {"data": {"data": {"value": "secret-value"}}}
    )

    with (
        patch("src.secrets_store.httpx.get", return_value=read_response) as get,
        patch("src.secrets_store.httpx.post", return_value=_response(204)) as post,
        patch("src.secrets_store.httpx.delete", return_value=_response(204)) as delete,
    ):
        assert store.get("mail", "password") == "secret-value"
        store.set("mail", "password", "new-value")
        store.delete("mail", "password")

    get.assert_called_once_with(
        "http://bao.local:8200/v1/secret/data/odysseus/internal/mail/password",
        headers={"X-Vault-Token": "token"},
        timeout=10.0,
    )
    assert post.call_args.kwargs["json"] == {"data": {"value": "new-value"}}
    assert "/data/" in delete.call_args.args[0]


def test_openbao_missing_is_not_an_outage():
    store = OpenBaoSecretStore(url="http://bao", token="token")

    with patch("src.secrets_store.httpx.get", return_value=_response(404)):
        assert store.get("mail", "password") is None

    request = httpx.Request("GET", "http://bao")
    with patch(
        "src.secrets_store.httpx.get",
        side_effect=httpx.ConnectError("refused", request=request),
    ):
        with pytest.raises(SecretStoreUnavailable):
            store.get("mail", "password")


def test_openbao_rejects_invalid_kv_response():
    store = OpenBaoSecretStore(url="http://bao", token="token")

    with patch(
        "src.secrets_store.httpx.get",
        return_value=_response(200, {"data": {"data": {}}}),
    ):
        with pytest.raises(SecretStoreUnavailable):
            store.get("mail", "password")


def test_openbao_bootstraps_from_existing_integration():
    integration = {
        "id": "bao",
        "name": "OpenBao",
        "preset": "openbao",
        "enabled": True,
        "base_url": "http://bao.local:8200",
        "api_key": "token",
    }

    with patch("src.integrations.get_integration", return_value=integration):
        store = OpenBaoSecretStore.from_integration("bao")

    assert isinstance(store, OpenBaoSecretStore)


def test_openbao_bootstrap_rejects_wrong_or_disabled_integration():
    wrong = {
        "id": "other",
        "name": "Other",
        "preset": "gitea",
        "base_url": "http://other",
        "api_key": "token",
    }
    disabled = {
        "id": "bao",
        "name": "OpenBao",
        "preset": "openbao",
        "enabled": False,
        "base_url": "http://bao",
        "api_key": "token",
    }

    with patch("src.integrations.get_integration", return_value=wrong):
        with pytest.raises(SecretStoreConfigurationError):
            OpenBaoSecretStore.from_integration("other")
    with patch("src.integrations.get_integration", return_value=disabled):
        with pytest.raises(SecretStoreConfigurationError):
            OpenBaoSecretStore.from_integration("bao")


def test_factory_defaults_local_and_requires_openbao_integration(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("ODYSSEUS_SECRET_STORE_BACKEND", raising=False)
    store = build_secret_store(local_path=tmp_path / "secrets.json")
    assert isinstance(store, LocalEncryptedSecretStore)

    with pytest.raises(SecretStoreConfigurationError):
        build_secret_store(backend="openbao", integration_id="")


def test_factory_builds_openbao_from_integration():
    integration = {
        "id": "bao",
        "name": "OpenBao",
        "preset": "openbao",
        "enabled": True,
        "base_url": "http://bao.local:8200",
        "api_key": "token",
    }

    with patch("src.integrations.get_integration", return_value=integration):
        store = build_secret_store(
            backend="openbao",
            integration_id="bao",
            mount="kv",
            prefix="apps/odysseus",
        )

    assert isinstance(store, OpenBaoSecretStore)
    assert store._secret_url("mail", "password") == (
        "http://bao.local:8200/v1/kv/data/apps/odysseus/mail/password"
    )


def test_secret_store_config_round_trip_and_environment_override(
    tmp_path, monkeypatch
):
    path = tmp_path / "secret-store.json"
    saved = save_secret_store_config(
        {
            "backend": "openbao",
            "integration_id": "bao-1",
            "mount": "kv",
            "prefix": "apps/odysseus",
        },
        path,
    )

    assert load_secret_store_config(path) == saved
    monkeypatch.setenv("ODYSSEUS_SECRET_STORE_MOUNT", "override")
    effective, overrides = resolve_secret_store_config(path)
    assert effective["mount"] == "override"
    assert overrides == ["ODYSSEUS_SECRET_STORE_MOUNT"]


def test_secret_store_config_rejects_invalid_values(tmp_path):
    path = tmp_path / "secret-store.json"

    with pytest.raises(SecretStoreConfigurationError):
        save_secret_store_config(
            {
                "backend": "openbao",
                "integration_id": "",
                "mount": "secret",
                "prefix": "odysseus/internal",
            },
            path,
        )
    with pytest.raises(ValueError):
        save_secret_store_config(
            {
                "backend": "local",
                "mount": "../bad",
                "prefix": "odysseus/internal",
            },
            path,
        )


def test_openbao_probe_uses_health_endpoint():
    store = OpenBaoSecretStore(url="http://bao.local:8200", token="token")

    with patch(
        "src.secrets_store.httpx.get",
        return_value=_response(200, {"version": "2.3.2"}),
    ) as get:
        result = store.probe()

    assert result == {"version": "2.3.2"}
    assert "/v1/sys/health?" in get.call_args.args[0]
    assert get.call_args.kwargs["headers"] == {"X-Vault-Token": "token"}


def test_process_store_can_be_configured_explicitly(tmp_path):
    store = LocalEncryptedSecretStore(tmp_path / "secrets.json")

    assert configure_secret_store(store) is store
    assert get_secret_store() is store


def test_explicit_migration_only_deletes_after_success(tmp_path):
    source = LocalEncryptedSecretStore(tmp_path / "source.json")
    target = LocalEncryptedSecretStore(tmp_path / "target.json")
    source.set("mail", "password", "pw")

    count = migrate_secrets(
        source,
        target,
        [SecretRef("mail", "password"), SecretRef("mail", "missing")],
        delete_source=True,
    )

    assert count == 1
    assert target.get("mail", "password") == "pw"
    assert source.get("mail", "password") is None


def test_failed_migration_keeps_source_value(tmp_path):
    source = LocalEncryptedSecretStore(tmp_path / "source.json")
    source.set("mail", "password", "pw")
    target = MagicMock(spec=SecretStore)
    target.set.side_effect = SecretStoreUnavailable("target unavailable")

    with pytest.raises(SecretStoreUnavailable):
        migrate_secrets(
            source,
            target,
            [SecretRef("mail", "password")],
            delete_source=True,
        )

    assert source.get("mail", "password") == "pw"
    target.delete.assert_not_called()
