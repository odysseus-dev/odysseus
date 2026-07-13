import pytest
from fastapi import HTTPException

from routes import mcp_routes


@pytest.mark.parametrize(
    "transport",
    ["", "bogus", "websocket", "streamable_http", "stdio "],
)
def test_validate_transport_rejects_unsupported_values(transport):
    with pytest.raises(HTTPException) as exc:
        mcp_routes._validate_mcp_transport(transport)

    assert exc.value.status_code == 400
    assert "transport" in str(exc.value.detail).lower()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("stdio", "stdio"),
        ("STDIO", "stdio"),
        ("sse", "sse"),
        ("http", "http"),
    ],
)
def test_validate_transport_accepts_supported_values(raw, expected):
    assert mcp_routes._validate_mcp_transport(raw) == expected


@pytest.mark.parametrize("raw", ["{", "{}", '"value"', "1", "null"])
def test_parse_args_rejects_invalid_or_wrong_type(raw):
    with pytest.raises(HTTPException) as exc:
        mcp_routes._parse_mcp_args(raw)

    assert exc.value.status_code == 400
    assert "args" in str(exc.value.detail).lower()


@pytest.mark.parametrize(
    "raw",
    ['["ok", 3]', '["ok", null]', '["ok", true]'],
)
def test_parse_args_requires_string_items(raw):
    with pytest.raises(HTTPException):
        mcp_routes._parse_mcp_args(raw)


def test_parse_args_accepts_string_list():
    assert mcp_routes._parse_mcp_args(
        '["--flag", "value"]'
    ) == ["--flag", "value"]


@pytest.mark.parametrize("raw", ["{", "[]", '"value"', "1", "null"])
def test_parse_env_rejects_invalid_or_wrong_type(raw):
    with pytest.raises(HTTPException) as exc:
        mcp_routes._parse_mcp_env(raw)

    assert exc.value.status_code == 400
    assert "env" in str(exc.value.detail).lower()


@pytest.mark.parametrize(
    "raw",
    ['{"TOKEN": 3}', '{"TOKEN": null}', '{"TOKEN": true}'],
)
def test_parse_env_requires_string_values(raw):
    with pytest.raises(HTTPException):
        mcp_routes._parse_mcp_env(raw)


def test_parse_env_accepts_string_mapping():
    assert mcp_routes._parse_mcp_env(
        '{"TOKEN": "value", "EMPTY": ""}'
    ) == {
        "TOKEN": "value",
        "EMPTY": "",
    }
