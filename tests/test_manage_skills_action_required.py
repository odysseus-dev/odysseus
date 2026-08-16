import json

import pytest

from src.tools.system import do_manage_skills


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"action": ""},
        {"action": "   "},
        {"name": "demo", "description": "x", "procedure": ["step"]},
    ],
)
async def test_manage_skills_requires_action(payload):
    result = await do_manage_skills(json.dumps(payload), owner="test")

    assert result == {
        "error": "action is required (list|view|view_ref|add|edit|patch|publish|delete|search)",
        "exit_code": 1,
    }
