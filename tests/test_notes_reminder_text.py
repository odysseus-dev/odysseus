"""Reminder text is plain: what email, ntfy and a notification can show.

The browser's local notification uses the same rules
(tests/notesReminderText.test.mjs reads the same vectors).
"""
import asyncio
import json
from pathlib import Path

import pytest

import routes.note_routes as nr
from core.notes_markdown import reminder_body

VECTORS = json.loads((Path(__file__).parent / "fixtures" / "notes_reminder_vectors.json").read_text(encoding="utf-8"))["reminder"]


@pytest.mark.parametrize("case", VECTORS, ids=lambda c: c["name"])
def test_reminder_vectors(case):
    assert reminder_body(case["content"]) == case["body"]


def test_reminder_body_never_carries_image_markdown():
    body = reminder_body('Pick up\n![](/api/upload/0123456789abcdef0123456789abcdef.png "w=50")\n- [ ] ![](/api/upload/a.png)\n- [ ] frame')
    assert "![" not in body and "/api/upload/" not in body
    assert body == "Pick up\n\nPending (1):\n- frame"


@pytest.mark.parametrize("raw, expected", [
    ("You have two things waiting.\nPending (2):", "You have two things waiting."),
    ("You have two things waiting.\nPending:", "You have two things waiting."),
    ("You have two things waiting.\n2 pending", "You have two things waiting."),
])
def test_synthesis_drops_an_echoed_pending_heading(monkeypatch, raw, expected):
    import src.endpoint_resolver as resolver
    import src.llm_core as llm_core

    async def fake_llm(**kwargs):
        return raw

    monkeypatch.setattr(resolver, "resolve_endpoint", lambda *a, **kw: ("http://llm", "m", {}))
    monkeypatch.setattr(llm_core, "llm_call_async", fake_llm)
    result = asyncio.run(nr.dispatch_reminder(
        title="Errands", note_body="Pending (2):\n- post office\n- bank", note_id="",
        settings_override={"reminder_channel": "browser", "reminder_llm_synthesis": True},
    ))
    assert result["synthesis"] == expected
