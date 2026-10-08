"""
session_actions.py

Reusable session actions that can be called from both REST routes
and the task scheduler / builtin actions system.
"""

import json
import logging
import re
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

_FRESH_EMPTY_SESSION_GRACE = timedelta(minutes=10)
_FRESH_SESSION_GRACE = _FRESH_EMPTY_SESSION_GRACE


def _utcnow_naive() -> datetime:
    """Return naive UTC for existing session DateTime columns."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _as_naive_utc(value):
    if value is None:
        return None
    if getattr(value, "tzinfo", None) is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def is_session_recently_active(row, now=None, grace=_FRESH_SESSION_GRACE) -> bool:
    """Return True while a new or active session is too fresh to auto-delete."""
    now = _as_naive_utc(now) or _utcnow_naive()
    for attr in ("last_message_at", "last_accessed", "updated_at", "created_at"):
        value = _as_naive_utc(getattr(row, attr, None))
        if not value:
            continue
        if value >= now:
            return True
        if now - value <= grace:
            return True
    return False


async def run_auto_sort(owner: str, skip_llm: bool = False, delete_throwaway: bool = True) -> str:
    """Remove empty/Incognito sessions and optionally organize saved chats.

    Args:
        owner: user whose sessions to process
        skip_llm: when True, do only Phase 1 (delete empty/Incognito sessions);
            skip Phase 2 (AI folder assignment). Used by the built-in daily
            background sweep so it never burns LLM tokens.
        delete_throwaway: retained for caller compatibility. Ordinary sessions
            with saved messages are always preserved, regardless of this flag.

    Returns a human-readable summary of what was done.
    """
    from core.database import SessionLocal, Session as DbSession, ChatMessage as DbMsg
    from src.llm_core import llm_call_async
    from src.task_endpoint import resolve_task_endpoint

    db = SessionLocal()
    try:
        # ── Phase 1: Delete only empty and explicitly Incognito sessions ──
        deleted_empty = 0
        deleted_throwaway = 0

        rows = db.query(DbSession).filter(
            DbSession.archived == False,
            *([DbSession.owner == owner] if owner else []),
        ).all()

        cleanup_now = _utcnow_naive()
        for row in rows:
            if getattr(row, 'is_important', False):
                continue
            created_at = _as_naive_utc(row.created_at or row.updated_at) or _utcnow_naive()
            is_fresh = (_utcnow_naive() - created_at) < _FRESH_EMPTY_SESSION_GRACE
            if (row.name or "").strip() == "Incognito":
                deleted_throwaway += 1
                db.delete(row)
                continue
            if is_session_recently_active(row, now=cleanup_now):
                continue

            has_messages = db.query(DbMsg.id).filter(
                DbMsg.session_id == row.id
            ).first() is not None
            if not has_messages:
                if is_fresh:
                    continue
                deleted_empty += 1
                db.delete(row)

        if deleted_empty or deleted_throwaway:
            db.commit()
            logger.info(f"Auto-sort: deleted {deleted_empty} empty + {deleted_throwaway} throwaway sessions")

        # ── Phase 2: AI folder assignment ──
        remaining = db.query(DbSession).filter(
            DbSession.archived == False,
            *([DbSession.owner == owner] if owner else []),
        ).all()

        session_list = []
        for row in remaining:
            if row.name == "Incognito":
                continue
            session_list.append({
                "id": row.id,
                "name": row.name or "(unnamed)",
                "current_folder": row.folder,
            })

        if len(session_list) < 2:
            return f"Cleaned {deleted_empty + deleted_throwaway} sessions. Too few remaining to sort."

        # Background built-in sweep skips folder-sort to stay pure infra.
        if skip_llm:
            return f"Cleaned {deleted_empty + deleted_throwaway} sessions (folder sort skipped)."

        url, model, headers = resolve_task_endpoint(owner=owner or None)
        if not url:
            return f"Cleaned {deleted_empty + deleted_throwaway} sessions. No model endpoint available for sorting."

        names_text = "\n".join(f'  "{s["id"][:8]}": "{s["name"]}"' for s in session_list)
        prompt = (
            "You are a session organizer. Group these chat sessions into folders by topic.\n\n"
            "Rules:\n"
            "- Be aggressive about grouping — put EVERY session in a folder\n"
            "- Use short folder names (2-4 words max)\n"
            "- Use the 8-char ID prefixes exactly as given\n"
            "- Output ONLY raw JSON, no markdown fences, no explanation\n\n"
            "Required JSON format:\n"
            '{"folders": {"Folder Name": ["id_prefix1", "id_prefix2"], "Other Folder": ["id_prefix3"]}}\n\n'
            f"Sessions (id_prefix: name):\n{{\n{names_text}\n}}"
        )

        try:
            # 16384 (was 4096): large folder JSON + reasoning-model thinking
            # overflowed 4096 and truncated the JSON, so it never parsed.
            raw = await llm_call_async(url, model, [{"role": "user", "content": prompt}],
                                       temperature=0.3, max_tokens=16384, headers=headers, timeout=120)
        except Exception as e:
            logger.warning(f"Auto-sort LLM call failed: {e}")
            return f"Cleaned {deleted_empty + deleted_throwaway} sessions. Folder sort skipped (model unreachable)."

        # Parse JSON from response
        text = raw.strip()
        result = None
        try:
            result = json.loads(text)
        except json.JSONDecodeError:
            pass
        if result is None:
            fence_match = re.search(r'```(?:json)?\s*\n?([\s\S]*?)```', text)
            if fence_match:
                try:
                    result = json.loads(fence_match.group(1).strip())
                except json.JSONDecodeError:
                    pass
        if result is None:
            brace_start = text.find('{')
            brace_end = text.rfind('}')
            if brace_start >= 0 and brace_end > brace_start:
                try:
                    result = json.loads(text[brace_start:brace_end + 1])
                except json.JSONDecodeError:
                    pass
        if result is None:
            return f"Cleaned {deleted_empty + deleted_throwaway} sessions. AI returned unparseable response."

        folders = result.get("folders", {})
        if not folders:
            return f"Cleaned {deleted_empty + deleted_throwaway} sessions. No folder groupings found."

        # Apply assignments
        id_prefix_map = {s["id"][:8]: s["id"] for s in session_list}
        updated = 0
        for folder_name, ids in folders.items():
            for sid_or_prefix in ids:
                full_id = None
                if sid_or_prefix in id_prefix_map.values():
                    full_id = sid_or_prefix
                else:
                    prefix = sid_or_prefix.rstrip(".").rstrip(" ")
                    if prefix in id_prefix_map:
                        full_id = id_prefix_map[prefix]
                    else:
                        for p, fid in id_prefix_map.items():
                            if fid.startswith(prefix) or prefix.startswith(p):
                                full_id = fid
                                break
                if full_id:
                    db_sess = db.query(DbSession).filter(DbSession.id == full_id).first()
                    if db_sess:
                        db_sess.folder = folder_name
                        db_sess.updated_at = _utcnow_naive()
                        updated += 1
        db.commit()

        folder_summary = ", ".join(f"{k} ({len(v)})" for k, v in folders.items())
        return f"Deleted {deleted_empty} empty + {deleted_throwaway} throwaway. Sorted {updated} sessions into: {folder_summary}"

    finally:
        db.close()
