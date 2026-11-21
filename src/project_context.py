from __future__ import annotations

from typing import Any

from core.database import Project, Session as DbSession, SessionLocal
from src.auth_helpers import owner_filter
from src.prompt_security import untrusted_context_message
from src.session_search import search_session_messages


PROJECT_CONTEXT_SNIPPET_LIMIT = 3


def _plain_text(value: Any, limit: int = 4000) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n[truncated]"


def build_project_context_messages(
    session_id: str,
    query: str,
    *,
    owner: str | None = None,
    include_related_snippets: bool = True,
) -> list[dict[str, Any]]:
    """Build project-level context for a chat turn.

    Project metadata is attached only when the current session belongs to a
    visible, non-archived project in the same owner scope. The user's current
    message remains untouched; related project material is added separately as
    untrusted evidence.
    """
    db = SessionLocal()
    try:
        session_q = db.query(DbSession).filter(DbSession.id == session_id)
        session_q = owner_filter(session_q, DbSession, owner)
        session = session_q.first()
        project_id = getattr(session, "project_id", None) if session is not None else None
        if not project_id:
            return []

        project_q = db.query(Project).filter(
            Project.id == project_id,
            Project.archived == False,  # noqa: E712
        )
        project_q = owner_filter(project_q, Project, owner)
        project = project_q.first()
        if project is None:
            return []

        messages: list[dict[str, Any]] = []
        configured_parts = [f"Project: {project.name}"]
        description = _plain_text(project.description, 2000)
        instructions = _plain_text(project.instructions, 3000)
        if description:
            configured_parts.append(f"Description: {description}")
        if instructions:
            configured_parts.append(f"Instructions: {instructions}")
        messages.append({
            "role": "system",
            "content": "\n\n".join(configured_parts),
            "metadata": {
                "source": "project",
                "project_id": project.id,
                "project_name": project.name,
            },
        })

        brief = _plain_text(project.brief, 4000)
        if brief:
            messages.append(untrusted_context_message(
                "project brief",
                brief,
                provenance_origin=f"project:{project.id}",
                arm_tool_gate=False,
            ))

        if include_related_snippets and query and query.strip():
            snippets = []
            for result in search_session_messages(
                query,
                limit=PROJECT_CONTEXT_SNIPPET_LIMIT + 2,
                owner=owner,
                include_archived=False,
                context_messages=0,
                restrict_owner=owner is not None,
                include_legacy_owner=False,
                project_id=project.id,
                db=db,
            ):
                if result.session_id == session_id:
                    continue
                snippet = _plain_text(result.content_snippet or result.content, 900)
                if snippet:
                    snippets.append(f"[{result.session_name}] {result.role}: {snippet}")
                if len(snippets) >= PROJECT_CONTEXT_SNIPPET_LIMIT:
                    break
            if snippets:
                messages.append(untrusted_context_message(
                    "related project chat excerpts",
                    "\n\n".join(snippets),
                    provenance_origin=f"project:{project.id}:chat_search",
                ))
        return messages
    finally:
        db.close()
