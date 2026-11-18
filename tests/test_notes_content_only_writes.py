"""A write that sends only a note's text keeps its task lines.

Agents, the Codex bridge and API clients read a note's prose and its tasks
apart (`view` prints them separately, `items` is its own field) and send the
edited prose back on its own. That must not delete the checklist. Clearing
it takes an explicit empty list.
"""
import asyncio
import json

import pytest

import core.database as cdb
from core.database import Note
from src.tools import notes as notes_tool
from tests.helpers.database import disposable_database
from tests.helpers.note_routes_client import notes_routes_harness

_ID = "trip0001-note"


@pytest.fixture
def rest(monkeypatch, tmp_path):
    with notes_routes_harness(monkeypatch, tmp_path) as harness:
        yield harness


@pytest.fixture
def notes(monkeypatch, tmp_path):
    with disposable_database(tmp_path) as factory:
        monkeypatch.setattr(cdb, "SessionLocal", factory)

        def seed(content):
            with factory() as db:
                db.add(Note(id=_ID, owner="alice", title="Trip", content=content, note_type="note"))
                db.commit()

        def content():
            with factory() as db:
                return db.get(Note, _ID).content

        yield seed, content, None


def _run(args, owner="alice"):
    return asyncio.run(notes_tool.do_manage_notes(json.dumps(args), owner=owner))


async def test_content_only_put_keeps_the_task_lines(rest):
    rest.seed("n1", "Pack light\n\n- [ ] passport\n- [x] charger")
    r = await rest.call("PUT", "/api/notes/n1", json={"content": "Pack light, bring snacks"})
    assert r.status_code == 200
    assert rest.content("n1") == "Pack light, bring snacks\n\n- [ ] passport\n- [x] charger"
    assert [i["text"] for i in r.json()["items"]] == ["passport", "charger"]


async def test_content_put_with_tasks_or_items_is_taken_as_written(rest):
    rest.seed("n2", "Intro\n- [ ] a")
    await rest.call("PUT", "/api/notes/n2", json={"content": "New\n- [ ] b"})
    assert rest.content("n2") == "New\n- [ ] b"
    rest.seed("n3", "Intro\n- [ ] a")
    await rest.call("PUT", "/api/notes/n3", json={"content": "Cleared", "items": []})
    assert rest.content("n3") == "Cleared"



def test_update_with_only_content_keeps_the_tasks(notes):
    seed, content, _ = notes
    seed("Pack light\n\n- [ ] passport\n- [x] charger")
    result = _run({"action": "update", "id": _ID, "content": "Pack light, bring snacks"})
    assert result["exit_code"] == 0
    assert content() == "Pack light, bring snacks\n\n- [ ] passport\n- [x] charger"


def test_update_with_an_empty_checklist_clears_the_tasks(notes):
    seed, content, _ = notes
    seed("Pack light\n\n- [ ] passport")
    result = _run({"action": "update", "id": _ID, "content": "Pack light", "checklist_items": []})
    assert result["exit_code"] == 0
    assert content() == "Pack light"


async def test_codex_bridge_update_with_only_content_keeps_the_tasks(notes):
    import httpx
    from fastapi import FastAPI
    import routes.codex_routes as cr

    seed, content, _ = notes
    seed("Groceries\n\n- [ ] milk")

    class _Token:
        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            if scope["type"] == "http":
                scope.setdefault("state", {}).update(
                    current_user="api", api_token=True, api_token_scopes=["todos:write"], api_token_owner="alice")
            await self.app(scope, receive, send)

    app = FastAPI()
    app.include_router(cr.setup_codex_routes())
    transport = httpx.ASGITransport(app=_Token(app), client=("203.0.113.7", 54321))
    async with httpx.AsyncClient(transport=transport, base_url="http://codex.test") as c:
        r = await c.post("/api/codex/todos", json={"action": "update", "id": _ID, "content": "Groceries for Sunday"})
    assert r.status_code == 200
    assert content() == "Groceries for Sunday\n\n- [ ] milk"
