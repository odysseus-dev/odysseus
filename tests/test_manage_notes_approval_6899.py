"""Regression tests for approving manage_notes create proposals (#6899)."""

from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import core.database as db
import src.database as compatibility
from src import tool_execution as execution
from src.agent_runtime.authority import ExactOperation, OperationGrant, RequestAuthority
from src.agent_runtime.owned_resources import resolve_owned_operation
from src.agent_runtime.resources import ResourceIdentityError
from src.tool_approvals import ToolApprovalStore
from src.tool_capabilities import ToolRunSecurityContext, capabilities_for_action
from src.tool_types import ToolBlock


@pytest.fixture
def notes_db(monkeypatch: pytest.MonkeyPatch):
    engine = create_engine("sqlite:///:memory:")
    db.Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(db, "SessionLocal", factory)
    monkeypatch.setattr(compatibility, "SessionLocal", factory)
    monkeypatch.setattr(execution, "_owner_is_admin", lambda owner: True)
    with factory() as session:
        session.add(
            db.Session(
                id="s-notes",
                owner="alice",
                name="s-notes",
                endpoint_url="https://model.test",
                model="test",
            )
        )
        session.commit()
    yield factory
    engine.dispose()


@pytest.mark.parametrize(
    "payload",
    [
        {"title": "Docker Compose version", "content": "v5.6.1"},
        {"action": "add", "title": "Docker Compose version", "content": "v5.6.1"},
        {"action": "create", "title": "Docker Compose version", "content": "v5.6.1"},
        {"title": "Groceries", "checklist_items": [{"text": "Milk", "done": False}]},
    ],
)
async def test_approved_manage_notes_create_seals_owned_identity_and_creates_note(
    notes_db, payload: dict
) -> None:
    authority = RequestAuthority(
        "req-notes-create",
        "alice",
        "s-notes",
        "",
        (OperationGrant("manage_notes"),),
    )
    content = json.dumps(payload)

    bound = resolve_owned_operation(
        ExactOperation.normalize("manage_notes", content),
        owner="alice",
        thread_id="s-notes",
    )
    assert bound.resources[0].namespace == "notes"
    assert bound.resources[0].record_id == "*"

    store = ToolApprovalStore()
    pending = store.create(
        owner="alice",
        session_id="s-notes",
        origin_run_id="run-notes",
        tool_name="manage_notes",
        content=content,
        workspace=None,
        request_authority=authority,
        external_untrusted_context_seen=True,
        capabilities=capabilities_for_action("manage_notes", content),
    )
    assert pending.owned_operation is not None

    exact = store.consume(
        pending.approval_id,
        decision="approve",
        owner="alice",
        session_id="s-notes",
    )
    desc, result = await execution.execute_tool_block(
        ToolBlock("manage_notes", content),
        owner="alice",
        session_id="s-notes",
        request_authority=authority,
        exact_approval=exact,
        security_context=ToolRunSecurityContext(external_untrusted_context_seen=True),
    )
    assert desc == "manage_notes"
    assert result.get("exit_code") == 0
    assert not result.get("error")
    assert result.get("note_id")

    with notes_db() as session:
        created = session.get(db.Note, result["note_id"])
        assert created is not None
        assert created.owner == "alice"
        assert created.title == payload["title"]


@pytest.mark.parametrize(
    "content",
    [
        "{}",
        '{"id":"missing-note","title":"Docker Compose version"}',
        '{"action":"update","title":"Nonexistent note","content":"updated"}',
    ],
)
def test_actionless_empty_or_existing_id_note_requests_still_fail_closed(
    notes_db, content: str
) -> None:
    with pytest.raises(ResourceIdentityError):
        resolve_owned_operation(
            ExactOperation.normalize("manage_notes", content),
            owner="alice",
            thread_id="s-notes",
        )
