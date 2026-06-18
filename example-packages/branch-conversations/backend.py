"""
Branch Conversations — backend.py

Adds branching support to sessions:
  • on_startup() — idempotent migration: adds parent_session_id + fork_message_index
                   columns to the existing sessions table via raw SQLite ALTER TABLE.
  • register_routes(app) — FastAPI routes under /api/branch-conversations/:
      POST /session/{id}/branch        — create child branch (full copy + parent tracking)
      GET  /session/{id}/branch-status — parent info + new-message delta
      GET  /session/{id}/branch-tree   — full ancestor/descendant tree
"""
import logging
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Request

logger = logging.getLogger(__name__)

_SessionLocal = None
_engine = None


# ── DB hook ───────────────────────────────────────────────────────────────────

def register_db(engine, SessionLocal, Base):
    global _SessionLocal, _engine
    _engine = engine
    _SessionLocal = SessionLocal


# ── Startup hook ──────────────────────────────────────────────────────────────

def on_startup():
    """Idempotent migration: add branch columns to sessions if missing."""
    if _engine is None:
        logger.warning("[branch-conversations] on_startup: engine not set, skipping migration")
        return
    try:
        with _engine.connect() as conn:
            rows = conn.execute(
                __import__('sqlalchemy').text("PRAGMA table_info(sessions)")
            ).fetchall()
            existing = {r[1] for r in rows}  # column name is index 1
            if "parent_session_id" not in existing:
                conn.execute(__import__('sqlalchemy').text(
                    "ALTER TABLE sessions ADD COLUMN parent_session_id VARCHAR"
                ))
                logger.info("[branch-conversations] Added column parent_session_id to sessions")
            if "fork_message_index" not in existing:
                conn.execute(__import__('sqlalchemy').text(
                    "ALTER TABLE sessions ADD COLUMN fork_message_index INTEGER"
                ))
                logger.info("[branch-conversations] Added column fork_message_index to sessions")
            conn.commit()
    except Exception as exc:
        logger.error(f"[branch-conversations] Migration failed: {exc}")


# ── Routes hook ───────────────────────────────────────────────────────────────

def register_routes(app):
    router = APIRouter(prefix="/api/branch-conversations", tags=["branch-conversations"])

    def _db():
        if _SessionLocal is None:
            raise HTTPException(503, "DB not initialised")
        db = _SessionLocal()
        try:
            yield db
        finally:
            db.close()

    def _owner(request: Request) -> Optional[str]:
        try:
            from src.auth_helpers import get_current_user
            return get_current_user(request)
        except Exception:
            return None

    def _verify_owner(request: Request, session_id: str):
        try:
            from routes.session_routes import _verify_session_owner
            _verify_session_owner(request, session_id)
        except Exception:
            pass

    # ── POST /session/{id}/branch ─────────────────────────────────────────────

    @router.post("/session/{session_id}/branch")
    async def branch_session(request: Request, session_id: str):
        """Create a child branch from session_id.

        Copies the full conversation and records the parent relationship so the
        branch tree can display lineage and new-message deltas.
        """
        _verify_owner(request, session_id)
        try:
            body = await request.json()
        except Exception:
            body = {}
        name_override = (body.get("name") or "").strip()

        try:
            from core.session_manager import SessionManager
            sm = _get_session_manager()
            if sm is None:
                raise HTTPException(503, "Session manager not available")

            source = sm.sessions.get(session_id)
            if not source:
                try:
                    source = sm.get_session(session_id)
                except Exception:
                    source = None
            if not source:
                raise HTTPException(404, "Session not found")

            from core.models import ChatMessage
            new_id = str(uuid.uuid4())
            branch_name = name_override or f"⎇ {source.name}"
            new_session = sm.create_session(
                session_id=new_id,
                name=branch_name,
                endpoint_url=source.endpoint_url,
                model=source.model,
                rag=False,
                owner=getattr(source, "owner", None),
            )
            parent_msg_count = len(source.history)
            for msg in source.history:
                meta = dict(msg.metadata) if isinstance(msg.metadata, dict) else None
                new_session.add_message(ChatMessage(msg.role, msg.content, meta))

            # Persist branch lineage
            if _SessionLocal is not None:
                db = _SessionLocal()
                try:
                    import sqlalchemy as sa
                    db.execute(
                        sa.text(
                            "UPDATE sessions SET parent_session_id=:pid, fork_message_index=:fi "
                            "WHERE id=:sid"
                        ),
                        {"pid": session_id, "fi": parent_msg_count, "sid": new_id},
                    )
                    db.commit()
                except Exception as e:
                    logger.warning(f"[branch-conversations] Could not persist lineage for {new_id}: {e}")
                    db.rollback()
                finally:
                    db.close()

            try:
                from src.event_bus import fire_event
                fire_event("session_created", getattr(source, "owner", None))
            except Exception:
                pass

            return {
                "status": "ok",
                "id": new_id,
                "name": branch_name,
                "parent_session_id": session_id,
                "fork_message_index": parent_msg_count,
            }
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"[branch-conversations] branch error {session_id}: {e}")
            raise HTTPException(500, str(e))

    # ── GET /session/{id}/branch-status ──────────────────────────────────────

    @router.get("/session/{session_id}/branch-status")
    def get_branch_status(request: Request, session_id: str):
        """Return parent info and how many new messages the parent has since fork."""
        _verify_owner(request, session_id)
        if _SessionLocal is None:
            raise HTTPException(503, "DB not initialised")
        import sqlalchemy as sa
        db = _SessionLocal()
        try:
            child = db.execute(
                sa.text("SELECT parent_session_id, fork_message_index FROM sessions WHERE id=:id"),
                {"id": session_id},
            ).fetchone()
            if not child:
                raise HTTPException(404, "Session not found")

            parent_id = child[0]
            fork_index = child[1]

            if not parent_id:
                return {"has_parent": False}

            parent = db.execute(
                sa.text("SELECT id, name, message_count FROM sessions WHERE id=:id"),
                {"id": parent_id},
            ).fetchone()
            if not parent:
                return {"has_parent": True, "parent_id": parent_id, "parent_exists": False}

            parent_msg_count = parent[2] or 0
            new_since_fork = max(0, parent_msg_count - (fork_index or 0))
            return {
                "has_parent": True,
                "parent_id": parent_id,
                "parent_name": parent[1],
                "parent_exists": True,
                "fork_message_index": fork_index,
                "parent_message_count": parent_msg_count,
                "new_messages_since_fork": new_since_fork,
            }
        finally:
            db.close()

    # ── GET /session/{id}/branch-tree ─────────────────────────────────────────

    @router.get("/session/{session_id}/branch-tree")
    def get_branch_tree(request: Request, session_id: str):
        """Return the full branch tree rooted at the topmost ancestor."""
        _verify_owner(request, session_id)
        owner = _owner(request)
        if _SessionLocal is None:
            raise HTTPException(503, "DB not initialised")
        import sqlalchemy as sa
        db = _SessionLocal()
        try:
            q = "SELECT id, name, message_count, parent_session_id, fork_message_index, created_at, owner FROM sessions WHERE archived=0 OR archived IS NULL"
            params: dict = {}
            if owner:
                q += " AND (owner=:owner OR owner IS NULL)"
                params["owner"] = owner
            rows = db.execute(sa.text(q), params).fetchall()
            by_id = {r[0]: r for r in rows}

            # Walk parent chain to find root
            root_id = session_id
            seen: set = set()
            while True:
                row = by_id.get(root_id)
                if not row:
                    break
                pid = row[3]
                if not pid or pid not in by_id or pid in seen:
                    break
                seen.add(root_id)
                root_id = pid

            def _build(sid: str, depth: int = 0):
                row = by_id.get(sid)
                if not row or depth > 20:
                    return None
                children = sorted(
                    [r for r in rows if r[3] == sid],
                    key=lambda r: r[5] or "",
                )
                child_nodes = [n for n in (_build(c[0], depth + 1) for c in children) if n]
                return {
                    "id": row[0],
                    "name": row[1],
                    "message_count": row[2] or 0,
                    "parent_session_id": row[3],
                    "fork_message_index": row[4],
                    "created_at": row[5].isoformat() if isinstance(row[5], datetime) else (row[5] or None),
                    "children": child_nodes,
                }

            tree = _build(root_id)
            return {"tree": tree, "current_session_id": session_id, "root_id": root_id}
        finally:
            db.close()

    app.include_router(router)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_session_manager():
    try:
        from core.models import get_session_manager
        return get_session_manager()
    except Exception:
        return None
