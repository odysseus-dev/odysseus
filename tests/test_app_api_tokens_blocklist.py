"""Regression: app_api must block the API-token management endpoints.

The blocklist prefix was `/api/tokens/` (trailing slash), which only matched
`DELETE /api/tokens/{id}`. The collection endpoints are registered slash-less
(`GET /api/tokens` lists every token with owner + prefix, `POST /api/tokens`
mints a new long-lived credential), so they sailed through the gate and were
reachable via app_api with the internal admin header. The prefix is now
slash-less so both the collection and the per-id paths are blocked.
"""
import asyncio
import json

from src.tool_implementations import do_app_api


def _blocked(path, method="GET"):
    result = asyncio.run(do_app_api(json.dumps({"path": path, "method": method})))
    return "blocked" in (result.get("error") or "").lower() and result.get("exit_code") == 1


def test_list_tokens_is_blocked():
    assert _blocked("/api/tokens", "GET")


def test_mint_token_is_blocked():
    assert _blocked("/api/tokens", "POST")


def test_delete_token_by_id_still_blocked():
    assert _blocked("/api/tokens/abc123", "DELETE")
