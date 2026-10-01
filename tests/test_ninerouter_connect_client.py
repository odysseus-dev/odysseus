"""Connect-client allowlist and redaction; no live 9router."""

from pathlib import Path

import pytest

from services.ninerouter.connect import (
    NineRouterConnectClient,
    NineRouterConnectError,
    derive_cli_token,
    normalize_connect_path,
)
from services.ninerouter.metadata import NineRouterMetadataError


def test_connect_allowlist_rejects_inference_and_dashboard():
    with pytest.raises(NineRouterMetadataError, match="allowlist"):
        normalize_connect_path("POST", "/v1/chat/completions")
    with pytest.raises(NineRouterMetadataError, match="allowlist"):
        normalize_connect_path("GET", "/v1/models")
    with pytest.raises(NineRouterMetadataError, match="allowlist"):
        normalize_connect_path("GET", "/dashboard/providers")


def test_connect_allowlist_rejects_dotdot_traversal():
    with pytest.raises(NineRouterMetadataError, match="allowlist"):
        normalize_connect_path("GET", "/api/oauth/../v1/models")
    with pytest.raises(NineRouterMetadataError, match="allowlist"):
        normalize_connect_path("GET", "/api/oauth/../dashboard/providers")


def test_connect_allowlist_accepts_probed_provider_paths():
    assert normalize_connect_path("GET", "/api/providers") == "/api/providers"
    assert normalize_connect_path("POST", "/api/providers") == "/api/providers"
    assert normalize_connect_path("GET", "/api/oauth/codex/authorize") == "/api/oauth/codex/authorize"
    assert normalize_connect_path("POST", "/api/oauth/codex/exchange") == "/api/oauth/codex/exchange"
    assert normalize_connect_path("POST", "/api/oauth/codex/import-token") == "/api/oauth/codex/import-token"
    assert normalize_connect_path("DELETE", "/api/providers/c156fa13") == "/api/providers/c156fa13"


def test_derive_cli_token_matches_9router_hash(tmp_path: Path):
    (tmp_path / "auth").mkdir()
    (tmp_path / "machine-id").write_text("machine-aaa\n", encoding="utf-8")
    (tmp_path / "auth" / "cli-secret").write_text("secret-bbb\n", encoding="utf-8")
    token = derive_cli_token(str(tmp_path))
    assert len(token) == 16
    assert token == derive_cli_token(str(tmp_path))


def test_create_api_key_posts_then_drops_secret_from_result():
    seen = []

    def fetch(method, path, headers, json_body):
        seen.append((method, path, json_body, headers.get("x-9r-cli-token")))
        return {
            "connection": {
                "id": "conn-1",
                "apiKey": "sk-live-should-strip",
                "status": "usable",
                "name": "OpenAI",
            }
        }

    client = NineRouterConnectClient(fetch=fetch, token="cli-tok")
    row = client.create_api_key("openai", "sk-live-should-strip")
    assert seen[0][0] == "POST"
    assert seen[0][1] == "/api/providers"
    assert seen[0][2] == {"provider": "openai", "apiKey": "sk-live-should-strip"}
    assert seen[0][3] == "cli-tok"
    assert row["id"] == "conn-1"
    assert "apiKey" not in row
    assert "api_key" not in row


def test_start_oauth_rejects_dashboard_authorization_url():
    def fetch(method, path, headers, json_body):
        return {"authUrl": "http://9router:20128/dashboard/providers"}

    client = NineRouterConnectClient(fetch=fetch, token="x")
    with pytest.raises(NineRouterConnectError, match="dashboard"):
        client.start_oauth("codex", "http://odysseus/api/ninerouter/connections/oauth/callback")


def test_start_oauth_returns_idp_url():
    def fetch(method, path, headers, json_body):
        assert "authorize" in path
        assert "redirect_uri=" in path
        return {"authUrl": "https://auth.openai.com/authorize?client_id=x", "state": "st1"}

    client = NineRouterConnectClient(fetch=fetch, token="x")
    out = client.start_oauth("codex", "http://odysseus/callback")
    assert out["authorization_url"].startswith("https://auth.openai.com/")
    assert out["state"] == "st1"


def test_import_codex_token_posts_access_token_and_redacts():
    """Device-flow ChatGPT deposits the OpenAI access token in overlay 9router only."""
    seen = []

    def fetch(method, path, headers, json_body):
        seen.append((method, path, json_body))
        return {
            "success": True,
            "connection": {
                "id": "conn-codex",
                "accessToken": "should-strip",
                "provider": "codex",
                "name": "ChatGPT",
            },
        }

    client = NineRouterConnectClient(fetch=fetch, token="cli-tok")
    row = client.import_codex_token("sk-live-access")
    assert seen[0][0] == "POST"
    assert seen[0][1] == "/api/oauth/codex/import-token"
    assert seen[0][2] == {"accessToken": "sk-live-access"}
    assert row["id"] == "conn-codex"
    assert "accessToken" not in row
    assert "access_token" not in row


def test_complete_oauth_forwards_code_not_tokens_and_redacts():
    seen = []

    def fetch(method, path, headers, json_body):
        seen.append((method, path, json_body))
        return {"id": "conn-2", "access_token": "tok", "refresh_token": "rt", "status": "usable"}

    client = NineRouterConnectClient(fetch=fetch, token="x")
    row = client.complete_oauth("codex", "auth-code-1", state="s")
    assert seen[0][0] == "POST"
    assert seen[0][1] == "/api/oauth/codex/exchange"
    assert seen[0][2] == {"code": "auth-code-1", "state": "s"}
    assert "access_token" not in row
    assert row["id"] == "conn-2"


def test_connect_client_cannot_fetch_completions():
    client = NineRouterConnectClient(
        fetch=lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not fetch")),
        token="x",
    )
    with pytest.raises(NineRouterMetadataError, match="allowlist"):
        client._request("POST", "/v1/chat/completions", json_body={"model": "x"})
