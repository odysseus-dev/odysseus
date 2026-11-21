import uuid

from core.database import ChatMessage, Project, Session, SessionLocal, utcnow_naive
from src.project_context import build_project_context_messages
from src.session_search import search_session_messages


def _id(prefix):
    return f"{prefix}-{uuid.uuid4()}"


def _insert_project_fixture(owner="alice"):
    project_id = _id("project")
    other_project_id = _id("other-project")
    current_session_id = _id("session")
    sibling_session_id = _id("sibling")
    other_session_id = _id("other-session")
    db = SessionLocal()
    try:
        project = Project(
            id=project_id,
            owner=owner,
            name="Odysseus Projects",
            description="Coordinate related Odysseus work.",
            instructions="Prefer project decisions over generic defaults.",
            brief="The project feature groups related chats and keeps a brief.",
            archived=False,
            is_pinned=False,
            created_at=utcnow_naive(),
            updated_at=utcnow_naive(),
        )
        other_project = Project(
            id=other_project_id,
            owner=owner,
            name="Other",
            description="",
            instructions="",
            brief="",
            archived=False,
            is_pinned=False,
            created_at=utcnow_naive(),
            updated_at=utcnow_naive(),
        )
        db.add_all([project, other_project])
        db.add_all([
            Session(
                id=current_session_id,
                name="Current",
                endpoint_url="http://example.test/v1/chat/completions",
                model="test-model",
                owner=owner,
                project_id=project_id,
                headers={},
                created_at=utcnow_naive(),
                updated_at=utcnow_naive(),
            ),
            Session(
                id=sibling_session_id,
                name="Sibling",
                endpoint_url="http://example.test/v1/chat/completions",
                model="test-model",
                owner=owner,
                project_id=project_id,
                headers={},
                created_at=utcnow_naive(),
                updated_at=utcnow_naive(),
            ),
            Session(
                id=other_session_id,
                name="Other Session",
                endpoint_url="http://example.test/v1/chat/completions",
                model="test-model",
                owner=owner,
                project_id=other_project_id,
                headers={},
                created_at=utcnow_naive(),
                updated_at=utcnow_naive(),
            ),
        ])
        db.add_all([
            ChatMessage(
                id=_id("msg"),
                session_id=current_session_id,
                role="user",
                content="How should alpha be handled here?",
                timestamp=utcnow_naive(),
            ),
            ChatMessage(
                id=_id("msg"),
                session_id=sibling_session_id,
                role="assistant",
                content="Alpha belongs in the related project chat only.",
                timestamp=utcnow_naive(),
            ),
            ChatMessage(
                id=_id("msg"),
                session_id=other_session_id,
                role="assistant",
                content="Alpha from another project must stay out.",
                timestamp=utcnow_naive(),
            ),
        ])
        db.commit()
    finally:
        db.close()
    return project_id, other_project_id, current_session_id


def test_project_context_injects_configured_project_and_related_snippets():
    project_id, _, current_session_id = _insert_project_fixture()

    messages = build_project_context_messages(
        current_session_id,
        "alpha",
        owner="alice",
    )

    assert messages[0]["role"] == "system"
    assert "Project: Odysseus Projects" in messages[0]["content"]
    assert "Prefer project decisions" in messages[0]["content"]
    merged = "\n".join(m["content"] for m in messages)
    assert "project feature groups related chats" in merged
    assert "Alpha belongs in the related project chat only" in merged
    assert "Alpha from another project must stay out" not in merged


def test_session_search_can_be_scoped_to_project():
    project_id, other_project_id, _ = _insert_project_fixture(owner="bob")

    first = search_session_messages(
        "Alpha",
        owner="bob",
        restrict_owner=True,
        include_legacy_owner=False,
        project_id=project_id,
    )
    second = search_session_messages(
        "Alpha",
        owner="bob",
        restrict_owner=True,
        include_legacy_owner=False,
        project_id=other_project_id,
    )

    assert {r.session_name for r in first} == {"Current", "Sibling"}
    assert {r.session_name for r in second} == {"Other Session"}
