"""Regression: agent_input_token_budget must be settable from chat (not flagged secret)."""
import asyncio
import json

import src.settings as settings_mod
from src.tool_implementations import do_manage_settings


def test_set_token_budget_is_not_refused_as_secret(monkeypatch):
    store = {}
    monkeypatch.setattr(settings_mod, "load_settings", lambda: dict(store))
    monkeypatch.setattr(settings_mod, "save_settings", lambda s: store.update(s))

    result = asyncio.run(do_manage_settings(json.dumps({
        "action": "set", "key": "agent_input_token_budget", "value": 8000,
    })))

    # The "token" substring used to flag this int setting as a credential and
    # refuse to set it (even though there's a deliberate "token budget" alias).
    assert "credential" not in result.get("response", "").lower(), result
    assert result.get("exit_code") == 0, result
    assert store.get("agent_input_token_budget") == 8000
