"""Header edits are partial saves, preserving stored effort and unrelated settings."""

import asyncio
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


def test_header_generation_settings_node_suite():
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    repo = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        ["node", "--test", "tests/chatGenerationSettings.test.mjs"],
        cwd=repo, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("change", [
    {"temperature_override": 0.7},
    {"max_tokens_override": 2048},
    {"max_tokens_override": None},
])
def test_generation_settings_endpoint_preserves_omitted_effort(monkeypatch, change):
    from routes.history import history_routes as hr

    session = SimpleNamespace(
        thinking_mode="effort:low", temperature_override=1.2,
        max_tokens_override=4096,
    )
    row = SimpleNamespace()
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = row
    monkeypatch.setattr(hr, "SessionLocal", lambda: db)
    monkeypatch.setattr(hr, "_verify_session_owner", lambda *_args: None)
    manager = MagicMock()
    manager.get_session.return_value = session
    router = hr.setup_history_routes(manager)
    endpoint = next(r.endpoint for r in router.routes
                    if r.path == "/api/session/{session_id}/generation-settings")
    request = SimpleNamespace(json=AsyncMock(return_value=change))

    result = asyncio.run(endpoint(request, "s1"))

    assert result["thinking_mode"] == row.thinking_mode == session.thinking_mode == "effort:low"
    assert result["reasoning_effort"] == "low"
    assert row.temperature_override == change.get("temperature_override", 1.2)
    assert row.max_tokens_override == change.get("max_tokens_override", 4096)
    db.commit.assert_called_once()
