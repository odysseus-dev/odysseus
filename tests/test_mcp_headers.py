import json

import pytest
from fastapi import HTTPException

from core.database import EncryptedText, McpServer
from routes.mcp_routes import _parse_mcp_headers


def test_mcp_headers_column_is_encrypted():
    assert isinstance(McpServer.__table__.c.headers.type, EncryptedText)


def test_parse_mcp_headers_accepts_string_values():
    headers = _parse_mcp_headers(json.dumps({
        "Authorization": "Bearer token",
        "X-Tenant": "odysseus",
    }))
    assert headers == {
        "Authorization": "Bearer token",
        "X-Tenant": "odysseus",
    }


@pytest.mark.parametrize("value", [
    "[]",
    '{"Authorization": 123}',
    '{"Authorization": "Bearer token\\r\\nX-Injected: true"}',
])
def test_parse_mcp_headers_rejects_unsafe_values(value):
    with pytest.raises(HTTPException) as exc:
        _parse_mcp_headers(value)
    assert exc.value.status_code == 400


def test_encrypted_text_does_not_store_header_plaintext():
    column_type = EncryptedText()
    plaintext = '{"Authorization":"Bearer test-secret"}'
    ciphertext = column_type.process_bind_param(plaintext, None)

    assert ciphertext.startswith("enc:")
    assert "test-secret" not in ciphertext
    assert column_type.process_result_value(ciphertext, None) == plaintext
