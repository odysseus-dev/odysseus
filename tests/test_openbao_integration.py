"""OpenBao integration preset and request wiring."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src import integrations


def _response(body: dict, status: int = 200) -> MagicMock:
    response = MagicMock()
    response.status_code = status
    response.headers = {"content-type": "application/json"}
    response.json.return_value = body
    response.text = "{}"
    return response


def test_openbao_preset_uses_vault_token_header():
    preset = integrations.INTEGRATION_PRESETS["openbao"]

    assert preset["name"] == "OpenBao"
    assert preset["auth_type"] == "header"
    assert preset["auth_header"] == "X-Vault-Token"


@pytest.mark.asyncio
async def test_openbao_api_call_sends_token_to_vault_api():
    integration = {
        "id": "openbao-test",
        "name": "OpenBao",
        "preset": "openbao",
        "enabled": True,
        "base_url": "http://openbao.local:8200",
        "auth_type": "header",
        "auth_header": "X-Vault-Token",
        "api_key": "test-token",
    }
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.request = AsyncMock(
        return_value=_response(
            {"initialized": True, "sealed": False, "version": "2.3.2"}
        )
    )

    with (
        patch.object(integrations, "_find_integration", return_value=integration),
        patch("src.integrations.httpx.AsyncClient", return_value=client),
    ):
        result = await integrations.execute_api_call(
            "openbao-test", "GET", "/v1/sys/health"
        )

    assert result["exit_code"] == 0
    client.request.assert_awaited_once()
    _, url = client.request.await_args.args[:2]
    assert url == "http://openbao.local:8200/v1/sys/health"
    assert client.request.await_args.kwargs["headers"]["X-Vault-Token"] == "test-token"
