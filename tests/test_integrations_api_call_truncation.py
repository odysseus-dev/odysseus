"""Tests for api_call truncation in execute_api_call.

Covers:
  (a) Large JSON list response -> sentinel appended, valid JSON returned
  (b) Small response -> returned unchanged, no truncation
"""
import json
import sys
import os
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Minimal stubs so src.integrations can be imported without heavy deps
# ---------------------------------------------------------------------------

for mod_name in ("core", "core.atomic_io", "core.platform_compat"):
    if mod_name not in sys.modules:
        sys.modules[mod_name] = types.ModuleType(mod_name)

core_atomic = sys.modules["core.atomic_io"]
if not hasattr(core_atomic, "atomic_write_json"):
    core_atomic.atomic_write_json = lambda *a, **kw: None  # type: ignore

core_compat = sys.modules["core.platform_compat"]
if not hasattr(core_compat, "safe_chmod"):
    core_compat.safe_chmod = lambda *a, **kw: None  # type: ignore

if "src.secret_storage" not in sys.modules:
    stub = types.ModuleType("src.secret_storage")
    stub.encrypt = lambda s: s  # type: ignore
    stub.decrypt = lambda s: s  # type: ignore
    stub.is_encrypted = lambda s: False  # type: ignore
    sys.modules["src.secret_storage"] = stub

if "src.constants" not in sys.modules:
    stub_c = types.ModuleType("src.constants")
    stub_c.DATA_DIR = "/tmp"  # type: ignore
    stub_c.INTEGRATIONS_FILE = "/tmp/integrations_test.json"  # type: ignore
    stub_c.SETTINGS_FILE = "/tmp/settings_test.json"  # type: ignore
    sys.modules["src.constants"] = stub_c

from src import integrations  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

DUMMY_INTEGRATION = {
    "id": "test_integ",
    "name": "TestInteg",
    "enabled": True,
    "base_url": "http://api.example.com",
    "auth_type": "none",
    "api_key": "",
    "auth_header": "",
    "auth_param": "",
    "description": "",
    "preset": "",
}


def _make_response(json_data, status=200):
    resp = MagicMock()
    resp.status_code = status
    resp.headers = {"content-type": "application/json; charset=utf-8"}
    resp.json.return_value = json_data
    resp.text = json.dumps(json_data)
    return resp


async def _call(json_data, status=200):
    mock_resp = _make_response(json_data, status)

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.request = AsyncMock(return_value=mock_resp)

    with (
        patch.object(integrations, "_find_integration", return_value=DUMMY_INTEGRATION),
        patch("httpx.AsyncClient", return_value=mock_client),
    ):
        return await integrations.execute_api_call("test_integ", "GET", "/items")


async def _call_with_integration(integration, path="/items"):
    mock_resp = _make_response({"ok": True})

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.request = AsyncMock(return_value=mock_resp)

    with (
        patch.object(integrations, "_find_integration", return_value=integration),
        patch("httpx.AsyncClient", return_value=mock_client),
    ):
        result = await integrations.execute_api_call("test_integ", "GET", path)
    return result, mock_client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_api_call_rejects_stored_base_url_with_query_without_requesting():
    integration = {**DUMMY_INTEGRATION, "base_url": "http://api.example.com/api?token=abc"}
    result, mock_client = await _call_with_integration(integration)

    assert result == {
        "error": "Integration base URL must not include query or fragment",
        "exit_code": 1,
    }
    mock_client.request.assert_not_called()


@pytest.mark.asyncio
async def test_api_call_joins_path_under_configured_base_path():
    integration = {**DUMMY_INTEGRATION, "base_url": "http://api.example.com/root"}
    result, mock_client = await _call_with_integration(integration, "/v1/items?limit=1")

    assert result.get("exit_code") == 0
    mock_client.request.assert_called_once()
    assert mock_client.request.call_args.args[:2] == (
        "GET",
        "http://api.example.com/root/v1/items?limit=1",
    )


@pytest.mark.asyncio
async def test_api_call_rejects_path_fragment_without_requesting():
    result, mock_client = await _call_with_integration(DUMMY_INTEGRATION, "/items#fragment")

    assert result == {"error": "Path must not contain a fragment", "exit_code": 1}
    mock_client.request.assert_not_called()


@pytest.mark.asyncio
async def test_large_json_list_returns_valid_json_with_sentinel():
    """A JSON list whose serialized form exceeds 12000 chars must be truncated
    to a valid JSON array ending with a sentinel object, not mid-string cut."""
    # Each item is ~120 chars; 120 items => ~14 400 chars serialized
    big_list = [{"id": i, "name": f"item_{i}", "data": "x" * 80} for i in range(120)]

    result = await _call(big_list)

    assert result.get("exit_code") == 0
    # Parse the JSON portion (after "HTTP 200\n")
    body = result["output"].split(chr(10), 1)[1]
    parsed = json.loads(body)  # must not raise -- proves valid JSON

    assert isinstance(parsed, list)
    sentinel = parsed[-1]
    assert sentinel.get("_truncated") is True
    assert sentinel["total_items"] == 120
    assert sentinel["shown_items"] < 120
    # The shown prefix must match the original items in order
    assert parsed[:-1] == big_list[: sentinel["shown_items"]]


@pytest.mark.asyncio
async def test_small_json_list_not_truncated():
    """A JSON list whose serialized form is under 12000 chars is returned as-is."""
    small_list = [{"id": i} for i in range(5)]

    result = await _call(small_list)

    assert result.get("exit_code") == 0
    body = result["output"].split(chr(10), 1)[1]
    parsed = json.loads(body)
    assert parsed == small_list
    # No sentinel in a short response
    assert not any(
        isinstance(item, dict) and item.get("_truncated") for item in parsed
    )


@pytest.mark.asyncio
async def test_large_json_dict_actually_truncated():
    """A JSON dict response that exceeds 12000 chars must be truncated to fit,
    with _truncated: true marking presence — not just marked without removal."""
    # Build a dict with enough entries to exceed 12000 chars when serialized.
    # Each value is ~200 chars; 100 entries ~ 22000 chars.
    big_dict = {f"key_{i}": "v" * 200 for i in range(100)}

    result = await _call(big_dict)

    assert result.get("exit_code") == 0
    body = result["output"].split(chr(10), 1)[1]
    parsed = json.loads(body)  # must be valid JSON

    assert isinstance(parsed, dict)
    assert parsed.get("_truncated") is True
    # The body must be within the 12000-char limit
    assert len(body) <= 12000
    # Some entries must have been dropped (not all 100 keys present)
    original_keys = set(big_dict.keys())
    kept_keys = set(parsed.keys()) - {"_truncated"}
    assert len(kept_keys) < len(original_keys), (
        "Dict truncation should have removed entries to fit within the limit"
    )
    # Keys that were kept must match the original values
    for k in kept_keys:
        assert parsed[k] == big_dict[k]


@pytest.mark.asyncio
async def test_small_json_dict_not_truncated():
    """A JSON dict whose serialized form is under 12000 chars is returned as-is."""
    small_dict = {"key_a": "value_a", "key_b": 42, "key_c": [1, 2, 3]}

    result = await _call(small_dict)

    assert result.get("exit_code") == 0
    body = result["output"].split(chr(10), 1)[1]
    parsed = json.loads(body)
    assert parsed == small_dict
    assert "_truncated" not in parsed


@pytest.mark.asyncio
async def test_list_truncation_respects_limit_including_sentinel():
    """After list truncation the total serialized body must not exceed 12000 chars,
    including the appended sentinel object."""
    # Items sized so the prefix alone would be just under the limit but
    # adding a sentinel would push it over without the overhead fix.
    big_list = [{"id": i, "name": f"item_{i}", "data": "x" * 80} for i in range(120)]

    result = await _call(big_list)

    assert result.get("exit_code") == 0
    body = result["output"].split(chr(10), 1)[1]
    assert len(body) <= 12000, (
        f"Truncated list body is {len(body)} chars, must be <= 12000"
    )
    parsed = json.loads(body)
    assert isinstance(parsed, list)
    sentinel = parsed[-1]
    assert sentinel.get("_truncated") is True
