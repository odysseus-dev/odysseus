"""
Paperclip — backend.py

AI agent team management: companies, agents, tasks.
Agents can be "run" — which creates a live Odysseus chat session
pre-configured with the agent's model and system prompt.
"""
import logging
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
import sqlalchemy as sa

logger = logging.getLogger(__name__)

_SessionLocal = None
_engine = None


# ── DB hook ───────────────────────────────────────────────────────────────────

def register_db(engine, SessionLocal, Base):
    global _SessionLocal, _engine
    _engine = engine
    _SessionLocal = SessionLocal


def on_startup():
    """Idempotent table creation via raw SQL (avoids ORM Base conflicts)."""
    if _engine is None:
        return
    ddl = [
        """CREATE TABLE IF NOT EXISTS pc_companies (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            goal TEXT NOT NULL DEFAULT '',
            owner TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS pc_agents (
            id TEXT PRIMARY KEY,
            company_id TEXT NOT NULL,
            name TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT '',
            model TEXT NOT NULL DEFAULT '',
            system_prompt TEXT NOT NULL DEFAULT '',
            token_budget INTEGER DEFAULT 0,
            owner TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS pc_tasks (
            id TEXT PRIMARY KEY,
            company_id TEXT NOT NULL,
            agent_id TEXT,
            title TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'todo',
            priority INTEGER DEFAULT 0,
            owner TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""",
    ]
    try:
        with _engine.connect() as conn:
            for stmt in ddl:
                conn.execute(sa.text(stmt))
            conn.commit()
        logger.info("[paperclip] tables ready")
    except Exception as exc:
        logger.error(f"[paperclip] on_startup migration failed: {exc}")


# ── Routes hook ───────────────────────────────────────────────────────────────

def register_routes(app):
    router = APIRouter(prefix="/api/paperclip", tags=["paperclip"])

    def _db():
        if _SessionLocal is None:
            raise HTTPException(503, "DB not ready")
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

    # ── Companies ─────────────────────────────────────────────────────────────

    class CompanyCreate(BaseModel):
        name: str
        goal: str = ""

    @router.get("/companies")
    def list_companies(request: Request):
        owner = _owner(request)
        db = _SessionLocal()
        try:
            q = "SELECT id,name,goal,owner,created_at FROM pc_companies"
            params: dict = {}
            if owner:
                q += " WHERE owner=:o OR owner IS NULL"
                params["o"] = owner
            q += " ORDER BY created_at DESC"
            rows = db.execute(sa.text(q), params).fetchall()
            return {"companies": [_company(r) for r in rows]}
        finally:
            db.close()

    @router.post("/companies")
    def create_company(body: CompanyCreate, request: Request):
        owner = _owner(request)
        db = _SessionLocal()
        try:
            cid = str(uuid.uuid4())
            db.execute(sa.text(
                "INSERT INTO pc_companies (id,name,goal,owner) VALUES (:id,:name,:goal,:owner)"
            ), {"id": cid, "name": body.name.strip(), "goal": body.goal, "owner": owner})
            db.commit()
            row = db.execute(sa.text("SELECT id,name,goal,owner,created_at FROM pc_companies WHERE id=:id"), {"id": cid}).fetchone()
            return _company(row)
        finally:
            db.close()

    @router.delete("/companies/{cid}")
    def delete_company(cid: str, request: Request):
        owner = _owner(request)
        db = _SessionLocal()
        try:
            row = db.execute(sa.text("SELECT owner FROM pc_companies WHERE id=:id"), {"id": cid}).fetchone()
            if not row:
                raise HTTPException(404, "Company not found")
            if owner and row[0] and row[0] != owner:
                raise HTTPException(403, "Not your company")
            db.execute(sa.text("DELETE FROM pc_tasks WHERE company_id=:id"), {"id": cid})
            db.execute(sa.text("DELETE FROM pc_agents WHERE company_id=:id"), {"id": cid})
            db.execute(sa.text("DELETE FROM pc_companies WHERE id=:id"), {"id": cid})
            db.commit()
            return {"ok": True}
        finally:
            db.close()

    # ── Agents ────────────────────────────────────────────────────────────────

    class AgentCreate(BaseModel):
        name: str
        role: str = ""
        model: str = ""
        system_prompt: str = ""
        token_budget: int = 0

    class AgentUpdate(BaseModel):
        name: Optional[str] = None
        role: Optional[str] = None
        model: Optional[str] = None
        system_prompt: Optional[str] = None
        token_budget: Optional[int] = None

    @router.get("/companies/{cid}/agents")
    def list_agents(cid: str, request: Request):
        db = _SessionLocal()
        try:
            rows = db.execute(sa.text(
                "SELECT id,company_id,name,role,model,system_prompt,token_budget,owner,created_at "
                "FROM pc_agents WHERE company_id=:cid ORDER BY created_at"
            ), {"cid": cid}).fetchall()
            return {"agents": [_agent(r) for r in rows]}
        finally:
            db.close()

    @router.post("/companies/{cid}/agents")
    def create_agent(cid: str, body: AgentCreate, request: Request):
        owner = _owner(request)
        db = _SessionLocal()
        try:
            aid = str(uuid.uuid4())
            db.execute(sa.text(
                "INSERT INTO pc_agents (id,company_id,name,role,model,system_prompt,token_budget,owner) "
                "VALUES (:id,:cid,:name,:role,:model,:sp,:budget,:owner)"
            ), {"id": aid, "cid": cid, "name": body.name.strip(), "role": body.role,
                "model": body.model, "sp": body.system_prompt, "budget": body.token_budget, "owner": owner})
            db.commit()
            row = db.execute(sa.text(
                "SELECT id,company_id,name,role,model,system_prompt,token_budget,owner,created_at "
                "FROM pc_agents WHERE id=:id"
            ), {"id": aid}).fetchone()
            return _agent(row)
        finally:
            db.close()

    @router.put("/agents/{aid}")
    def update_agent(aid: str, body: AgentUpdate, request: Request):
        db = _SessionLocal()
        try:
            row = db.execute(sa.text("SELECT id FROM pc_agents WHERE id=:id"), {"id": aid}).fetchone()
            if not row:
                raise HTTPException(404, "Agent not found")
            updates = {k: v for k, v in body.model_dump().items() if v is not None}
            if updates:
                sets = ", ".join(f"{k}=:{k}" for k in updates)
                updates["aid"] = aid
                db.execute(sa.text(f"UPDATE pc_agents SET {sets} WHERE id=:aid"), updates)
                db.commit()
            row = db.execute(sa.text(
                "SELECT id,company_id,name,role,model,system_prompt,token_budget,owner,created_at "
                "FROM pc_agents WHERE id=:id"
            ), {"id": aid}).fetchone()
            return _agent(row)
        finally:
            db.close()

    @router.delete("/agents/{aid}")
    def delete_agent(aid: str, request: Request):
        db = _SessionLocal()
        try:
            row = db.execute(sa.text("SELECT id FROM pc_agents WHERE id=:id"), {"id": aid}).fetchone()
            if not row:
                raise HTTPException(404, "Agent not found")
            db.execute(sa.text("UPDATE pc_tasks SET agent_id=NULL WHERE agent_id=:id"), {"id": aid})
            db.execute(sa.text("DELETE FROM pc_agents WHERE id=:id"), {"id": aid})
            db.commit()
            return {"ok": True}
        finally:
            db.close()

    @router.post("/agents/{aid}/run")
    async def run_agent(aid: str, request: Request):
        """Create a new Odysseus session pre-loaded with this agent's config."""
        owner = _owner(request)
        db = _SessionLocal()
        try:
            row = db.execute(sa.text(
                "SELECT id,name,role,model,system_prompt FROM pc_agents WHERE id=:id"
            ), {"id": aid}).fetchone()
            if not row:
                raise HTTPException(404, "Agent not found")
            agent_id, agent_name, agent_role, agent_model, system_prompt = row
        finally:
            db.close()

        try:
            from core.models import get_session_manager, ChatMessage
        except ImportError:
            raise HTTPException(503, "Session manager unavailable")

        sm = get_session_manager()
        if sm is None:
            raise HTTPException(503, "Session manager not ready")

        # Resolve default endpoint for the user
        endpoint_url, resolved_model, _headers = None, agent_model, {}
        try:
            from src.endpoint_resolver import resolve_endpoint
            endpoint_url, default_model, _headers = resolve_endpoint("default", owner=owner)
            if not agent_model:
                resolved_model = default_model
        except Exception:
            pass

        session_id = str(uuid.uuid4())
        label = f"[{agent_role}] {agent_name}" if agent_role else agent_name
        new_session = sm.create_session(
            session_id=session_id,
            name=f"⬡ {label}",
            endpoint_url=endpoint_url or "",
            model=resolved_model or "",
            rag=False,
            owner=owner,
        )

        if system_prompt:
            new_session.add_message(ChatMessage(
                role="system",
                content=system_prompt,
                metadata={"hidden": True, "paperclip_agent": aid},
            ))

        try:
            from src.event_bus import fire_event
            fire_event("session_created", owner)
        except Exception:
            pass

        return {"session_id": session_id, "name": new_session.name}

    # ── Tasks ─────────────────────────────────────────────────────────────────

    class TaskCreate(BaseModel):
        title: str
        description: str = ""
        agent_id: Optional[str] = None
        priority: int = 0

    class TaskUpdate(BaseModel):
        title: Optional[str] = None
        description: Optional[str] = None
        status: Optional[str] = None
        agent_id: Optional[str] = None
        priority: Optional[int] = None

    @router.get("/companies/{cid}/tasks")
    def list_tasks(cid: str, request: Request):
        db = _SessionLocal()
        try:
            rows = db.execute(sa.text(
                "SELECT id,company_id,agent_id,title,description,status,priority,owner,created_at "
                "FROM pc_tasks WHERE company_id=:cid ORDER BY priority DESC, created_at"
            ), {"cid": cid}).fetchall()
            return {"tasks": [_task(r) for r in rows]}
        finally:
            db.close()

    @router.post("/companies/{cid}/tasks")
    def create_task(cid: str, body: TaskCreate, request: Request):
        owner = _owner(request)
        db = _SessionLocal()
        try:
            tid = str(uuid.uuid4())
            db.execute(sa.text(
                "INSERT INTO pc_tasks (id,company_id,agent_id,title,description,priority,owner) "
                "VALUES (:id,:cid,:aid,:title,:desc,:pri,:owner)"
            ), {"id": tid, "cid": cid, "aid": body.agent_id, "title": body.title.strip(),
                "desc": body.description, "pri": body.priority, "owner": owner})
            db.commit()
            row = db.execute(sa.text(
                "SELECT id,company_id,agent_id,title,description,status,priority,owner,created_at "
                "FROM pc_tasks WHERE id=:id"
            ), {"id": tid}).fetchone()
            return _task(row)
        finally:
            db.close()

    @router.put("/tasks/{tid}")
    def update_task(tid: str, body: TaskUpdate, request: Request):
        db = _SessionLocal()
        try:
            row = db.execute(sa.text("SELECT id FROM pc_tasks WHERE id=:id"), {"id": tid}).fetchone()
            if not row:
                raise HTTPException(404, "Task not found")
            updates = {k: v for k, v in body.model_dump().items() if v is not None}
            if updates:
                sets = ", ".join(f"{k}=:{k}" for k in updates)
                updates["tid"] = tid
                db.execute(sa.text(f"UPDATE pc_tasks SET {sets} WHERE id=:tid"), updates)
                db.commit()
            row = db.execute(sa.text(
                "SELECT id,company_id,agent_id,title,description,status,priority,owner,created_at "
                "FROM pc_tasks WHERE id=:id"
            ), {"id": tid}).fetchone()
            return _task(row)
        finally:
            db.close()

    @router.delete("/tasks/{tid}")
    def delete_task(tid: str, request: Request):
        db = _SessionLocal()
        try:
            db.execute(sa.text("DELETE FROM pc_tasks WHERE id=:id"), {"id": tid})
            db.commit()
            return {"ok": True}
        finally:
            db.close()

    app.include_router(router)


# ── Serializers ───────────────────────────────────────────────────────────────

def _company(r):
    return {"id": r[0], "name": r[1], "goal": r[2], "owner": r[3],
            "created_at": str(r[4]) if r[4] else None}

def _agent(r):
    return {"id": r[0], "company_id": r[1], "name": r[2], "role": r[3],
            "model": r[4], "system_prompt": r[5], "token_budget": r[6],
            "owner": r[7], "created_at": str(r[8]) if r[8] else None}

def _task(r):
    return {"id": r[0], "company_id": r[1], "agent_id": r[2], "title": r[3],
            "description": r[4], "status": r[5], "priority": r[6],
            "owner": r[7], "created_at": str(r[8]) if r[8] else None}
