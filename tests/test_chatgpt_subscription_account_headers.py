import base64
import json

from src import chatgpt_subscription as mod


def _jwt(payload: dict) -> str:
    def enc(value: dict) -> str:
        raw = json.dumps(value, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    return f"{enc({'alg': 'none', 'typ': 'JWT'})}.{enc(payload)}.sig"


def test_chatgpt_headers_include_account_routing_and_codex_metadata():
    token = _jwt(
        {
            "exp": 4102444800,
            "https://api.openai.com/auth": {
                "chatgpt_account_id": "account-123",
                "chatgpt_account_is_fedramp": True,
            },
        }
    )
    headers = mod.chatgpt_headers(token)
    assert headers["Authorization"] == f"Bearer {token}"
    assert headers["ChatGPT-Account-ID"] == "account-123"
    assert headers["X-OpenAI-Fedramp"] == "true"
    assert headers["originator"] == "codex_cli_rs"
    assert headers["version"] == mod.OPENAI_CODEX_CLIENT_VERSION
    assert headers["User-Agent"].startswith("codex_cli_rs/")


def test_chatgpt_headers_do_not_invent_account_id():
    token = _jwt({"exp": 4102444800})
    headers = mod.chatgpt_headers(token)
    assert "ChatGPT-Account-ID" not in headers
    assert "X-OpenAI-Fedramp" not in headers


def test_model_discovery_uses_codex_metadata(monkeypatch):
    token = _jwt(
        {
            "exp": 4102444800,
            "https://api.openai.com/auth": {"chatgpt_account_id": "account-xyz"},
        }
    )
    captured = {}

    class Response:
        status_code = 200

        def json(self):
            return {
                "models": [
                    {"slug": "gpt-visible", "visibility": "list", "priority": 2},
                    {"slug": "gpt-hidden", "visibility": "hide", "priority": 1},
                ]
            }

    def fake_get(url, *, params=None, headers=None, timeout=None):
        captured.update(url=url, params=params, headers=headers, timeout=timeout)
        return Response()

    monkeypatch.setattr(mod.httpx, "get", fake_get)
    assert mod.fetch_available_models(token) == ["gpt-visible"]
    assert captured["url"].endswith("/backend-api/codex/models")
    assert captured["params"] == {"client_version": mod.OPENAI_CODEX_CLIENT_VERSION}
    assert captured["headers"]["ChatGPT-Account-ID"] == "account-xyz"
    assert captured["headers"]["originator"] == "codex_cli_rs"


def test_device_code_request_sends_codex_originator(monkeypatch):
    captured = {}

    class Response:
        status_code = 200

        def json(self):
            return {"device_auth_id": "device", "user_code": "CODE"}

    def fake_post(url, *, json=None, headers=None, timeout=None, data=None):
        captured.update(url=url, json=json, headers=headers, timeout=timeout, data=data)
        return Response()

    monkeypatch.setattr(mod.httpx, "post", fake_post)
    result = mod.request_device_code()
    assert result["user_code"] == "CODE"
    assert captured["headers"]["originator"] == "codex_cli_rs"
