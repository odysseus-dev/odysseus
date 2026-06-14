from types import SimpleNamespace

import pytest


def test_effective_storage_owner_matrix(monkeypatch):
    from src.owner_identity import DEFAULT_LOCAL_OWNER, effective_storage_owner

    monkeypatch.delenv("AUTH_ENABLED", raising=False)
    assert effective_storage_owner(None) is None
    assert effective_storage_owner("") is None
    assert effective_storage_owner("alice") == "alice"
    for sentinel in ("api", "demo", "system", "internal-tool"):
        assert effective_storage_owner(sentinel) is None
        assert effective_storage_owner(f" {sentinel.upper()} ") is None

    monkeypatch.setenv("AUTH_ENABLED", "false")
    assert effective_storage_owner(None) == DEFAULT_LOCAL_OWNER
    assert effective_storage_owner("") == DEFAULT_LOCAL_OWNER
    assert effective_storage_owner("admin") == "admin"
    for sentinel in ("api", "demo", "system", "internal-tool"):
        assert effective_storage_owner(sentinel) is None


def test_storage_owner_for_request_uses_api_token_owner(monkeypatch):
    from src.auth_helpers import storage_owner_for_request

    monkeypatch.delenv("AUTH_ENABLED", raising=False)
    request = SimpleNamespace(
        state=SimpleNamespace(
            current_user="api",
            api_token=True,
            api_token_owner="alice",
        )
    )

    assert storage_owner_for_request(request) == "alice"


@pytest.mark.parametrize("sentinel", ["api", "demo", "system", "internal-tool"])
def test_storage_owner_for_request_rejects_request_sentinel(monkeypatch, sentinel):
    from src.auth_helpers import storage_owner_for_request

    monkeypatch.delenv("AUTH_ENABLED", raising=False)
    request = SimpleNamespace(
        state=SimpleNamespace(
            current_user=sentinel,
            api_token=sentinel == "api",
            api_token_owner=None,
        )
    )

    assert storage_owner_for_request(request) is None


def test_storage_owner_for_request_uses_default_local_when_auth_disabled(monkeypatch):
    from src.auth_helpers import storage_owner_for_request
    from src.owner_identity import DEFAULT_LOCAL_OWNER

    monkeypatch.setenv("AUTH_ENABLED", "false")
    request = SimpleNamespace(state=SimpleNamespace(current_user=None))

    assert storage_owner_for_request(request) == DEFAULT_LOCAL_OWNER
