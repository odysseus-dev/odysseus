"""Internal secret storage contract and backend implementations.

The default backend stores encrypted values in ``data/secrets.json`` using the
existing application Fernet key. OpenBao is opt-in and bootstraps from an
existing OpenBao API Integration, whose token intentionally remains in the
local encrypted integration store.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Protocol, runtime_checkable
from urllib.parse import quote, urlparse

import httpx

from core.atomic_io import atomic_write_json
from core.platform_compat import safe_chmod
from src.constants import SECRETS_FILE
from src.secret_storage import decrypt, encrypt

logger = logging.getLogger(__name__)

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_BACKEND_ENV = "ODYSSEUS_SECRET_STORE_BACKEND"
_INTEGRATION_ENV = "ODYSSEUS_SECRET_STORE_INTEGRATION_ID"
_MOUNT_ENV = "ODYSSEUS_SECRET_STORE_MOUNT"
_PREFIX_ENV = "ODYSSEUS_SECRET_STORE_PREFIX"
_local_store_lock = threading.RLock()


class SecretStoreError(RuntimeError):
    """Base class for secret-store failures."""


class SecretStoreConfigurationError(SecretStoreError):
    """The selected backend is missing or has invalid configuration."""


class SecretStoreUnavailable(SecretStoreError):
    """The backend could not complete an operation."""


@runtime_checkable
class SecretStore(Protocol):
    """Minimal internal secret-storage contract."""

    def get(self, namespace: str, key: str) -> str | None: ...

    def set(self, namespace: str, key: str, value: str) -> None: ...

    def delete(self, namespace: str, key: str) -> None: ...


@dataclass(frozen=True)
class SecretRef:
    namespace: str
    key: str


def _validate_identifier(value: str, label: str) -> str:
    cleaned = str(value or "").strip()
    if not _IDENTIFIER.fullmatch(cleaned):
        raise ValueError(
            f"{label} must start with a letter or digit and contain only "
            "letters, digits, dots, underscores, or hyphens"
        )
    return cleaned


class LocalEncryptedSecretStore:
    """Fernet-encrypted JSON secret store used by default."""

    def __init__(self, path: Path | str = SECRETS_FILE) -> None:
        self._path = Path(path)

    def _load(self) -> dict[str, dict[str, str]]:
        if not self._path.exists():
            return {}
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SecretStoreUnavailable("Could not read local secret store") from exc
        if not isinstance(data, dict):
            raise SecretStoreUnavailable("Local secret store must contain an object")
        result: dict[str, dict[str, str]] = {}
        for namespace, values in data.items():
            if not isinstance(namespace, str) or not isinstance(values, dict):
                raise SecretStoreUnavailable("Local secret store has an invalid shape")
            if not all(isinstance(k, str) and isinstance(v, str) for k, v in values.items()):
                raise SecretStoreUnavailable("Local secret store contains invalid values")
            result[namespace] = dict(values)
        return result

    def _save(self, data: dict[str, dict[str, str]]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        try:
            atomic_write_json(str(self._path), data, indent=2)
            safe_chmod(self._path, 0o600)
        except OSError as exc:
            raise SecretStoreUnavailable("Could not write local secret store") from exc

    def get(self, namespace: str, key: str) -> str | None:
        namespace = _validate_identifier(namespace, "namespace")
        key = _validate_identifier(key, "key")
        with _local_store_lock:
            stored = self._load().get(namespace, {}).get(key)
        if stored is None:
            return None
        value = decrypt(stored)
        if stored and not value:
            raise SecretStoreUnavailable("Could not decrypt local secret")
        return value

    def set(self, namespace: str, key: str, value: str) -> None:
        namespace = _validate_identifier(namespace, "namespace")
        key = _validate_identifier(key, "key")
        if not isinstance(value, str):
            raise TypeError("secret value must be a string")
        with _local_store_lock:
            data = self._load()
            data.setdefault(namespace, {})[key] = encrypt(value)
            self._save(data)

    def delete(self, namespace: str, key: str) -> None:
        namespace = _validate_identifier(namespace, "namespace")
        key = _validate_identifier(key, "key")
        with _local_store_lock:
            data = self._load()
            values = data.get(namespace)
            if not values or key not in values:
                return
            del values[key]
            if not values:
                data.pop(namespace, None)
            self._save(data)


class OpenBaoSecretStore:
    """OpenBao / Vault KV v2 implementation of :class:`SecretStore`."""

    def __init__(
        self,
        *,
        url: str,
        token: str,
        mount: str = "secret",
        prefix: str = "odysseus/internal",
        timeout: float = 10.0,
    ) -> None:
        self._url = str(url or "").strip().rstrip("/")
        self._token = str(token or "").strip()
        self._mount = _validate_identifier(mount, "mount")
        prefix_parts = [part for part in str(prefix or "").strip("/").split("/") if part]
        parsed_url = urlparse(self._url)
        if parsed_url.scheme not in ("http", "https") or not parsed_url.hostname:
            raise SecretStoreConfigurationError("OpenBao URL must be HTTP(S)")
        if parsed_url.query or parsed_url.fragment:
            raise SecretStoreConfigurationError(
                "OpenBao URL must not include query or fragment"
            )
        if not self._token:
            raise SecretStoreConfigurationError("OpenBao token is required")
        if not prefix_parts:
            raise SecretStoreConfigurationError("OpenBao prefix is required")
        self._prefix = "/".join(
            _validate_identifier(part, "prefix segment") for part in prefix_parts
        )
        self._timeout = timeout

    @classmethod
    def from_integration(
        cls,
        integration_id: str,
        *,
        mount: str = "secret",
        prefix: str = "odysseus/internal",
        timeout: float = 10.0,
    ) -> "OpenBaoSecretStore":
        from src.integrations import get_integration

        integration = get_integration(integration_id)
        if not integration:
            raise SecretStoreConfigurationError(
                f"OpenBao integration not found: {integration_id}"
            )
        preset = str(integration.get("preset") or "").lower()
        name = str(integration.get("name") or "").lower()
        if preset != "openbao" and name != "openbao":
            raise SecretStoreConfigurationError(
                f"Integration '{integration_id}' is not an OpenBao integration"
            )
        if integration.get("enabled", True) is False:
            raise SecretStoreConfigurationError("OpenBao integration is disabled")
        return cls(
            url=integration.get("base_url", ""),
            token=integration.get("api_key", ""),
            mount=mount,
            prefix=prefix,
            timeout=timeout,
        )

    def _secret_url(self, namespace: str, key: str) -> str:
        namespace = _validate_identifier(namespace, "namespace")
        key = _validate_identifier(key, "key")
        path = "/".join(
            quote(part, safe="")
            for part in (*self._prefix.split("/"), namespace, key)
        )
        return f"{self._url}/v1/{quote(self._mount, safe='')}/data/{path}"

    @property
    def _headers(self) -> dict[str, str]:
        return {"X-Vault-Token": self._token}

    def get(self, namespace: str, key: str) -> str | None:
        try:
            response = httpx.get(
                self._secret_url(namespace, key),
                headers=self._headers,
                timeout=self._timeout,
            )
        except httpx.RequestError as exc:
            raise SecretStoreUnavailable("OpenBao read failed") from exc
        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            raise SecretStoreUnavailable(
                f"OpenBao read failed with HTTP {response.status_code}"
            )
        try:
            value = response.json()["data"]["data"]["value"]
        except (KeyError, TypeError, ValueError) as exc:
            raise SecretStoreUnavailable("OpenBao returned an invalid KV v2 response") from exc
        if not isinstance(value, str):
            raise SecretStoreUnavailable("OpenBao secret value is not a string")
        return value

    def set(self, namespace: str, key: str, value: str) -> None:
        if not isinstance(value, str):
            raise TypeError("secret value must be a string")
        try:
            response = httpx.post(
                self._secret_url(namespace, key),
                headers=self._headers,
                json={"data": {"value": value}},
                timeout=self._timeout,
            )
        except httpx.RequestError as exc:
            raise SecretStoreUnavailable("OpenBao write failed") from exc
        if response.status_code >= 400:
            raise SecretStoreUnavailable(
                f"OpenBao write failed with HTTP {response.status_code}"
            )

    def delete(self, namespace: str, key: str) -> None:
        try:
            response = httpx.delete(
                self._secret_url(namespace, key),
                headers=self._headers,
                timeout=self._timeout,
            )
        except httpx.RequestError as exc:
            raise SecretStoreUnavailable("OpenBao delete failed") from exc
        if response.status_code not in (200, 204, 404):
            raise SecretStoreUnavailable(
                f"OpenBao delete failed with HTTP {response.status_code}"
            )


def build_secret_store(
    *,
    backend: str | None = None,
    integration_id: str | None = None,
    mount: str | None = None,
    prefix: str | None = None,
    local_path: Path | str = SECRETS_FILE,
) -> SecretStore:
    """Build the configured store without changing the default local behavior."""

    selected = str(backend or os.getenv(_BACKEND_ENV, "local")).strip().lower()
    if selected in ("", "local"):
        return LocalEncryptedSecretStore(local_path)
    if selected != "openbao":
        raise SecretStoreConfigurationError(
            f"Unsupported secret-store backend: {selected}"
        )
    resolved_integration = str(
        integration_id or os.getenv(_INTEGRATION_ENV, "")
    ).strip()
    if not resolved_integration:
        raise SecretStoreConfigurationError(
            f"{_INTEGRATION_ENV} is required for the OpenBao backend"
        )
    return OpenBaoSecretStore.from_integration(
        resolved_integration,
        mount=mount or os.getenv(_MOUNT_ENV, "secret"),
        prefix=prefix or os.getenv(_PREFIX_ENV, "odysseus/internal"),
    )


_secret_store: SecretStore | None = None


def get_secret_store() -> SecretStore:
    """Return the process-wide configured store, constructing it lazily."""

    global _secret_store
    if _secret_store is None:
        _secret_store = build_secret_store()
    return _secret_store


def configure_secret_store(store: SecretStore | None = None) -> SecretStore:
    """Replace or rebuild the process-wide store.

    Passing ``None`` rebuilds from environment configuration. This explicit
    operation supports tests and future admin-controlled backend switching
    without silently changing stores during a request.
    """

    global _secret_store
    _secret_store = store if store is not None else build_secret_store()
    return _secret_store


def migrate_secrets(
    source: SecretStore,
    target: SecretStore,
    refs: Iterable[SecretRef],
    *,
    delete_source: bool = False,
) -> int:
    """Explicitly copy selected secrets between stores.

    Migration never enumerates or silently switches backends. Source values are
    deleted only after the corresponding target write succeeds.
    """

    migrated = 0
    for ref in refs:
        value = source.get(ref.namespace, ref.key)
        if value is None:
            continue
        target.set(ref.namespace, ref.key, value)
        if delete_source:
            source.delete(ref.namespace, ref.key)
        migrated += 1
    return migrated
