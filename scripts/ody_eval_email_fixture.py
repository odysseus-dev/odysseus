"""Shared fixture email wiring for local Odysseus self-evals."""

from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
from typing import Any, Iterator

from src.constants import DATA_DIR
from src.fixture_email import execute_fixture_email
from src.tool_utils import get_mcp_manager, set_mcp_manager


class FixtureEmailMcpManager:
    """Minimal MCP manager that serves only deterministic fixture email tools."""

    async def call_tool(self, tool: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        if not tool.startswith("mcp__email__"):
            return {"error": f"MCP server for {tool} not connected", "exit_code": 1}
        args = dict(args or {})
        owner = str(args.pop("_odysseus_owner", "") or "").strip() or None
        return execute_fixture_email(tool, args, owner=owner)


@contextlib.contextmanager
def email_fixture(enabled: bool, *, owner: str = "pewds") -> Iterator[None]:
    """Temporarily install fixture email data and an MCP manager for evals."""
    if not enabled:
        yield
        return

    fixture_path = Path(DATA_DIR) / "fixture_email_messages.json"
    backup = fixture_path.read_bytes() if fixture_path.exists() else None
    old_mcp_manager = get_mcp_manager()
    old_fixture_env = os.environ.get("ODYSSEUS_EMAIL_FIXTURE")
    fixture = {
        "messages": [
            {
                "owner": owner,
                "from": "Booking.com <email.campaign@sg.booking.com>",
                "subject": "Save up to 20% off car rentals 🚗",
                "date": "Fri, 21 Aug 2026 06:43:57 +0200",
                "summary": "Car rental promotion fixture for latest-email evals.",
                "body": "Save up to 20% off selected car rentals.",
            },
            {
                "owner": owner,
                "from": "Older Fixture <older.fixture@example.invalid>",
                "subject": "Older inbox message",
                "date": "Thu, 20 Aug 2026 12:00:00 +0000",
                "summary": "Older fixture email.",
                "body": "Older fixture email so latest ordering is deterministic.",
            },
        ]
    }
    fixture_path.parent.mkdir(parents=True, exist_ok=True)
    fixture_path.write_text(json.dumps(fixture, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.environ["ODYSSEUS_EMAIL_FIXTURE"] = "1"
    set_mcp_manager(FixtureEmailMcpManager())
    try:
        yield
    finally:
        set_mcp_manager(old_mcp_manager)
        if old_fixture_env is None:
            os.environ.pop("ODYSSEUS_EMAIL_FIXTURE", None)
        else:
            os.environ["ODYSSEUS_EMAIL_FIXTURE"] = old_fixture_env
        if backup is not None:
            fixture_path.write_bytes(backup)
        else:
            with contextlib.suppress(FileNotFoundError):
                fixture_path.unlink()
