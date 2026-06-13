import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from routes.openclaw_inbox_routes import setup_openclaw_inbox_routes


def _request(scopes=None, owner="alice"):
    return SimpleNamespace(state=SimpleNamespace(
        api_token=True,
        api_token_scopes=scopes or [],
        api_token_owner=owner,
    ))


def _endpoint(router, path: str, method: str):
    for route in router.routes:
        if getattr(route, "path", None) == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError(f"route not found: {method} {path}")


@pytest.fixture()
def inbox_state(tmp_path, monkeypatch):
    monkeypatch.setattr("routes.openclaw_inbox_routes.DATA_DIR", str(tmp_path))
    state = {
        "total_unread": 3,
        "total_urgent": 2,
        "max_score": 3,
        "per_uid": {
            "acct-1:101": {
                "score": 3,
                "subject": "Production incident",
                "from": "Ops Team",
                "reason": "container down",
                "tags": ["work"],
            },
            "acct-1:102": {
                "score": 2,
                "subject": "Needs reply",
                "from": "Customer",
                "reason": "waiting on answer",
                "tags": ["work"],
            },
            "acct-1:103": {
                "score": 1,
                "subject": "FYI",
                "from": "Newsletter",
                "reason": "info",
                "tags": ["newsletter"],
            },
        },
    }
    (tmp_path / "email_urgency_state_alice.json").write_text(json.dumps(state), encoding="utf-8")
    return tmp_path


@pytest.mark.asyncio
async def test_triage_lists_urgent_items(inbox_state):
    triage = _endpoint(setup_openclaw_inbox_routes(), "/api/openclaw/inbox/triage", "GET")
    data = await triage(_request(["email:read"]))
    assert data["status"] == "ok"
    assert data["total_unread"] == 3
    assert len(data["items"]) == 2
    assert data["items"][0]["tier"] == "urgent"
    assert data["items"][0]["actions"][:2] == ["ack", "mute_sender_2h"]


@pytest.mark.asyncio
async def test_triage_requires_email_read_scope(inbox_state):
    triage = _endpoint(setup_openclaw_inbox_routes(), "/api/openclaw/inbox/triage", "GET")
    with pytest.raises(HTTPException) as exc:
        await triage(_request(["chat"]))
    assert exc.value.status_code == 403
    assert "email:read" in exc.value.detail


@pytest.mark.asyncio
async def test_ack_hides_item_by_default(inbox_state):
    router = setup_openclaw_inbox_routes()
    triage = _endpoint(router, "/api/openclaw/inbox/triage", "GET")
    ack = _endpoint(router, "/api/openclaw/inbox/triage/{item_id}/ack", "POST")
    before = await triage(_request(["email:read"]))
    item_id = before["items"][0]["id"]
    result = await ack(_request(["email:read"]), item_id)
    assert result["status"] == "ok"
    after = await triage(_request(["email:read"]))
    assert item_id not in [item["id"] for item in after["items"]]
    with_ack = await triage(_request(["email:read"]), include_acknowledged=True)
    assert item_id in [item["id"] for item in with_ack["items"]]


@pytest.mark.asyncio
async def test_mute_sender_hides_matching_sender(inbox_state):
    router = setup_openclaw_inbox_routes()
    triage = _endpoint(router, "/api/openclaw/inbox/triage", "GET")
    mute = _endpoint(router, "/api/openclaw/inbox/triage/{item_id}/mute-sender", "POST")
    before = await triage(_request(["email:read"]))
    item_id = next(item["id"] for item in before["items"] if item["from"] == "Customer")
    result = await mute(_request(["email:read"]), item_id, None)
    assert result["status"] == "ok"
    after = await triage(_request(["email:read"]))
    assert "Customer" not in [item["from"] for item in after["items"]]
    with_muted = await triage(_request(["email:read"]), include_muted=True)
    assert "Customer" in [item["from"] for item in with_muted["items"]]
