"""
document_actions.py

Reusable document actions callable from both REST routes and the task scheduler.
"""

import logging
import re
from datetime import datetime

logger = logging.getLogger(__name__)


_JUNK_TITLES = {
    "untitled", "untitled document", "new document", "document",
    "new email", "new mail", "new message", "reply", "fwd", "re:",
    "test", "testing", "asdf", "asd", "foo", "bar", "baz",
    "tmp", "temp", "scratch", "scratchpad", "draft", "delete",
    "remove", "junk", "trash", "xxx", "abc", "qwerty",
}


def _norm_title(t: str) -> str:
    """Normalize a title for grouping: trim, collapse whitespace, lowercase."""
    t = t if isinstance(t, str) else ""
    return re.sub(r"\s+", " ", t.strip()).lower()


def _content_fingerprint(content: str) -> str:
    """A stable fingerprint of document content for duplicate detection.

    Strips bits that differ between otherwise-identical copies — chiefly the
    `upload_id` of a re-imported PDF and the random `id=` of annotations — so
    that N imports of the same file collapse to one fingerprint. Whitespace is
    collapsed and the result lowercased.
    """
    c = content if isinstance(content, str) else ""
    c = re.sub(r'upload_id="[^"]*"', "upload_id", c)          # pdf_source re-imports
    c = re.sub(r"\bid=ann-[A-Za-z0-9_-]+", "id=ann", c)        # annotation ids
    c = re.sub(r"\s+", " ", c).strip().lower()
    return c


def _real_len(content: str) -> int:
    """Length of content with markdown noise stripped — a 'completeness' proxy."""
    content = content if isinstance(content, str) else ""
    stripped = re.sub(r"^#{1,6}\s+", "", content, flags=re.MULTILINE)
    stripped = re.sub(r"[*_`>\-=]+", "", stripped)
    stripped = re.sub(r"\s+", " ", stripped).strip()
    return len(stripped)


async def run_document_tidy(owner: str) -> str:
    """Remove clearly-junk documents and redundant duplicates for an owner.

    Conservative rules (no length-based deletion — short notes are valid):
    - Empty / whitespace-only / placeholder ("# Untitled")
    - Title is a throwaway name (test, asdf, …) or the content itself is one
    - Email reply-chain with no original content
    - Duplicates: docs sharing the same normalized title AND the same content
      fingerprint (ignoring volatile upload/annotation ids). The most complete
      copy (longest real content, then most recent) is kept; the rest deleted.
    """
    from core.database import SessionLocal, Document, Session as DbSession

    db = SessionLocal()
    try:
        if owner:
            # Documents now carry their own owner column (robust to a deleted
            # session). Match on it directly; orphaned legacy rows are swept
            # to the admin at boot so they're attributed too.
            docs = db.query(Document).filter(Document.owner == owner).all()
        else:
            docs = db.query(Document).all()

        deleted_examples = []
        deleted = 0
        kept = 0
        survivors = []  # docs that pass the junk rules, considered for dedup

        for doc in docs:
            content = (doc.current_content or "").strip()
            title = (doc.title or "").strip().lower()

            # Strip markdown noise to get "real" character count
            stripped = re.sub(r"^#{1,6}\s+", "", content, flags=re.MULTILINE)  # headers
            stripped = re.sub(r"[*_`>\-=]+", "", stripped)  # markdown chars
            stripped = re.sub(r"\s+", " ", stripped).strip()
            real_len = len(stripped)

            # Detect emails-saved-as-documents (quote chains with no original content)
            lines = [ln for ln in content.split("\n") if ln.strip()]
            quoted_lines = [ln for ln in lines if ln.lstrip().startswith(">")]
            header_lines = [ln for ln in lines if re.match(r"^On .+ wrote:?\s*$", ln.strip())]
            non_quote_content = "\n".join(
                ln for ln in lines
                if not ln.lstrip().startswith(">")
                and not re.match(r"^On .+ wrote:?\s*$", ln.strip())
            ).strip()
            quote_ratio = len(quoted_lines) / max(len(lines), 1)

            should_delete = False
            reason = ""

            if not content or content in ("", "# Untitled"):
                should_delete = True
                reason = "empty"
            elif title in _JUNK_TITLES:
                # If you named it "test" or "asdf" etc, you don't care about it
                should_delete = True
                reason = f"junk title '{title}'"
            elif stripped.lower() in _JUNK_TITLES:
                should_delete = True
                reason = "throwaway content"
            # No length-based deletion: short notes are legitimate content.
            elif (quoted_lines or header_lines) and len(non_quote_content) < 50 and quote_ratio > 0.4:
                # Email reply chain with no original content
                should_delete = True
                reason = "email quote-chain only"

            if should_delete:
                if len(deleted_examples) < 5:
                    label = (doc.title or "(no title)")[:40]
                    deleted_examples.append(f"{label} ({reason})")
                db.delete(doc)
                deleted += 1
            else:
                survivors.append(doc)

        # --- Duplicate pass: group survivors by (normalized title, content
        # fingerprint) and keep only the most complete copy of each group. ---
        groups: dict = {}
        for doc in survivors:
            key = (_norm_title(doc.title), _content_fingerprint(doc.current_content))
            groups.setdefault(key, []).append(doc)

        for (title_key, _fp), members in groups.items():
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
                deleted_examples.append(f"{label} (+{len(dupes)} duplicate copies)")
            for d in dupes:
                db.delete(d)
                deleted += 1

        if deleted:
            db.commit()

        if deleted == 0:
            # Use sentinel so the scheduler can drop the run row entirely.
            from src.builtin_actions import TaskNoop
            raise TaskNoop(f"scanned {len(docs)} document(s), no junk")
        preview = "; ".join(deleted_examples)
        extra = f" (+{deleted - len(deleted_examples)} more)" if deleted > len(deleted_examples) else ""
        return f"Removed {deleted} of {len(docs)}: {preview}{extra} · {kept} kept"
    finally:
        db.close()
