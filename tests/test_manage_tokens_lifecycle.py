"""Regression coverage for API tokens created through the agent tool."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_agent_created_token_is_auth_compatible_and_invalidates_cache(monkeypatch):
    from core import database
    from src import api_token_cache, security_audit
    from src.agent_tools.admin_tools import do_manage_tokens

    session = MagicMock()
    monkeypatch.setattr(database, "SessionLocal", lambda: session)
    invalidator = MagicMock()
    audit = AsyncMock()
    monkeypatch.setattr(api_token_cache, "invalidate_token_cache", invalidator)
    monkeypatch.setattr(security_audit, "log_security_event_async", audit)

    result = await do_manage_tokens(
        json.dumps({"action": "create", "name": "Codex", "scopes": ["todos:write"]}),
        owner="Alice",
    )

    assert result["exit_code"] == 0
    assert result["token"].startswith("ody_")
    assert result["owner"] == "alice"
    assert result["scopes"] == ["todos:read", "todos:write"]
    created = session.add.call_args.args[0]
    assert created.owner == "alice"
    assert created.token_prefix == result["token"][:8]
    assert created.scopes == "todos:read,todos:write"
    assert created.token_hash != result["token"]
    session.commit.assert_called_once()
    invalidator.assert_called_once()
    audit.assert_awaited_once()


@pytest.mark.asyncio
async def test_agent_token_delete_enforces_owner_and_invalidates(monkeypatch):
    from core import database
    from src import api_token_cache, security_audit
    from src.agent_tools.admin_tools import do_manage_tokens

    token = SimpleNamespace(id="abc12345", owner="alice", name="Codex")
    session = MagicMock()
    session.query.return_value.filter.return_value.first.return_value = token
    monkeypatch.setattr(database, "SessionLocal", lambda: session)
    invalidator = MagicMock()
    audit = AsyncMock()
    monkeypatch.setattr(api_token_cache, "invalidate_token_cache", invalidator)
    monkeypatch.setattr(security_audit, "log_security_event_async", audit)

    denied = await do_manage_tokens(
        json.dumps({"action": "delete", "token_id": token.id}),
        owner="bob",
    )
    assert denied == {"error": "Not your token", "exit_code": 1}
    session.delete.assert_not_called()
    invalidator.assert_not_called()

    allowed = await do_manage_tokens(
        json.dumps({"action": "delete", "token_id": token.id}),
        owner="alice",
    )
    assert allowed["exit_code"] == 0
    session.delete.assert_called_once_with(token)
    invalidator.assert_called_once()
    audit.assert_awaited_once()
