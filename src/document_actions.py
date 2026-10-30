"""
document_actions.py

Reusable document actions callable from both REST routes and the task scheduler.
"""

import logging
import re
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def _has_document_content(doc) -> bool:
    """A blank current draft may still have recoverable saved content."""
    def has_content(value):
        return bool(value and value.strip() not in ("", "# Untitled"))

    return has_content(doc.current_content) or any(
        has_content(version.content) for version in doc.versions
    )


def _norm_title(t: str) -> str:
    """Normalize a title for grouping: trim, collapse whitespace, lowercase."""
    t = t if isinstance(t, str) else ""
    return re.sub(r"\s+", " ", t.strip()).lower()


def _content_fingerprint(content: str) -> str:
    """Match exact content; case, whitespace and upload IDs can be meaningful."""
    return content if isinstance(content, str) else ""


def _real_len(content: str) -> int:
    """Length of content with markdown noise stripped — a 'completeness' proxy."""
    content = content if isinstance(content, str) else ""
    stripped = re.sub(r"^#{1,6}\s+", "", content, flags=re.MULTILINE)
    stripped = re.sub(r"[*_`>\-=]+", "", stripped)
    stripped = re.sub(r"\s+", " ", stripped).strip()
    return len(stripped)


async def run_document_tidy(owner: str) -> str:
    """Remove empty drafts with no saved content and archive exact duplicates.

    Titles, short text and quoted email are not evidence that a document is
    disposable. Archived documents and saved version history remain intact.
    """
    from core.database import SessionLocal, Document

    db = SessionLocal()
    try:
        query = db.query(Document).filter(
            Document.is_active == True,  # noqa: E712
            (Document.archived == False) | Document.archived.is_(None),  # noqa: E712
        )
        if owner:
            # Documents now carry their own owner column (robust to a deleted
            # session). Match on it directly; orphaned legacy rows are swept
            # to the admin at boot so they're attributed too.
            query = query.filter(Document.owner == owner)
        docs = query.all()

        deleted_examples = []
        deleted = 0
        archived = 0
        kept = 0
        survivors = []  # docs that pass the junk rules, considered for dedup
        now = datetime.now(timezone.utc)

        for doc in docs:
            created = doc.created_at
            if created and created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)

            # Skip freshly created documents to avoid deleting them while the user is actively editing
            if created and (now - created).total_seconds() < 900:  # 15 minutes
                kept += 1
                continue

            content = (doc.current_content or "").strip()
            is_fresh_empty = (
                not content
                and created is not None
                and (now - created).total_seconds() < 1800
            )
            if is_fresh_empty:
                kept += 1
                continue

            if not _has_document_content(doc):
                if len(deleted_examples) < 5:
                    label = (doc.title or "(no title)")[:40]
                    deleted_examples.append(f"{label} (empty)")
                db.delete(doc)
                deleted += 1
            else:
                survivors.append(doc)

        # Keep duplicate cleanup reversible: each copy may have unique history.
        groups: dict = {}
        for doc in survivors:
            key = (doc.owner, doc.session_id, doc.language,
                   _norm_title(doc.title), _content_fingerprint(doc.current_content))
            groups.setdefault(key, []).append(doc)

        for members in groups.values():
            if len(members) < 2:
                kept += 1
                continue
            # Keep the most complete (longest real content), then most recent.
            def _updated(d):
                return d.updated_at or d.created_at
            # Sort key must be total-order safe: a document with both
            # updated_at and created_at NULL would otherwise make Python
            # compare None against a datetime on a real-length tie, raising
            # TypeError and aborting the whole tidy run. Rank "has a
            # timestamp" before the timestamp itself so a None is never
            # compared against a datetime.
            members.sort(
                key=lambda d: (
                    _real_len(d.current_content),
                    _updated(d) is not None,
                    _updated(d) or datetime.min,
                ),
                reverse=True,
            )
            keeper = members[0]
            kept += 1
            dupes = members[1:]
            if len(deleted_examples) < 5:
                label = (keeper.title or "(no title)")[:40]
                deleted_examples.append(f"{label} ({len(dupes)} duplicate copies archived)")
            for d in dupes:
                d.archived = True
                archived += 1

        if deleted or archived:
            db.commit()

        if deleted == 0 and archived == 0:
            # Use sentinel so the scheduler can drop the run row entirely.
            from src.builtin_actions import TaskNoop
            raise TaskNoop(f"scanned {len(docs)} document(s), no junk")
        preview = "; ".join(deleted_examples)
        return f"Removed {deleted} empty document(s), archived {archived} duplicate(s): {preview} · {kept} kept"
    finally:
        db.close()
