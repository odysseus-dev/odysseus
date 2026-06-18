"""
Paperclip — backend.py

AI agent team management: companies, agents, tasks.
CEO agents get full company context + management tools injected into their sessions.
Agents can have skills, MCPs, and persistent memory.
"""
import json
import logging
import uuid
from typing import Optional, List

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
import sqlalchemy as sa

logger = logging.getLogger(__name__)

_SessionLocal = None
_engine = None


# ── DB hook ────────────────────────────────────────────────────────────────────

def register_db(engine, SessionLocal, Base):
    global _SessionLocal, _engine
    _engine = engine
    _SessionLocal = SessionLocal
    _run_migrations(engine)


def _add_col(conn, table, col, col_def):
    rows = conn.execute(sa.text(f"PRAGMA table_info({table})")).fetchall()
    if col not in [r[1] for r in rows]:
        conn.execute(sa.text(f"ALTER TABLE {table} ADD COLUMN {col} {col_def}"))


def _run_migrations(engine):
    if engine is None:
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
        with engine.connect() as conn:
            for stmt in ddl:
                conn.execute(sa.text(stmt))
            _add_col(conn, 'pc_agents', 'skills',            "TEXT DEFAULT '[]'")
            _add_col(conn, 'pc_agents', 'mcps',              "TEXT DEFAULT '[]'")
            _add_col(conn, 'pc_agents', 'memory',            "TEXT DEFAULT ''")
            _add_col(conn, 'pc_agents', 'is_ceo',            "INTEGER DEFAULT 0")
            _add_col(conn, 'pc_agents', 'active_session_id', "TEXT DEFAULT NULL")
            conn.commit()
        logger.info("[paperclip] tables ready")
    except Exception as exc:
        logger.error(f"[paperclip] migration failed: {exc}")


def on_startup():
    _run_migrations(_engine)


# ── Routes hook ────────────────────────────────────────────────────────────────

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

    # ── Available resources (models, MCPs, skills) ─────────────────────────────

    @router.get("/available-models")
    def get_available_models(request: Request):
        """Fetch available models from configured Odysseus endpoints."""
        db = _SessionLocal()
        try:
            rows = db.execute(sa.text(
                "SELECT id, name, cached_models, pinned_models FROM model_endpoints "
                "WHERE is_enabled=1 ORDER BY name"
            )).fetchall()
            models = []
            seen = set()
            for ep_id, ep_name, cached_json, pinned_json in rows:
                for src in [cached_json, pinned_json]:
                    try:
                        for m in json.loads(src or '[]'):
                            mid = m.get('id', str(m)) if isinstance(m, dict) else str(m)
                            if mid and mid not in seen:
                                seen.add(mid)
                                models.append({"id": mid, "endpoint": ep_name or ep_id})
                    except Exception:
                        pass
            return {"models": models}
        except Exception as e:
            logger.warning(f"[paperclip] available-models failed: {e}")
            return {"models": []}
        finally:
            db.close()

    @router.get("/available-mcps")
    def get_available_mcps(request: Request):
        """Fetch enabled MCP servers from Odysseus."""
        db = _SessionLocal()
        try:
            rows = db.execute(sa.text(
                "SELECT id, name, transport FROM mcp_servers WHERE is_enabled=1 ORDER BY name"
            )).fetchall()
            return {"mcps": [{"id": r[0], "name": r[1], "transport": r[2]} for r in rows]}
        except Exception as e:
            logger.warning(f"[paperclip] available-mcps failed: {e}")
            return {"mcps": []}
        finally:
            db.close()

    @router.get("/available-skills")
    def get_available_skills(request: Request):
        """Fetch skills from Odysseus skills system."""
        try:
            from services.memory.skills import SkillsManager
            mgr = SkillsManager()
            raw = mgr.list_skills() or []
            return {"skills": [
                {
                    "id":          s.get("id") or s.get("name", ""),
                    "name":        s.get("name", ""),
                    "description": s.get("description", ""),
                    "category":    s.get("category", ""),
                }
                for s in raw
            ]}
        except Exception as e:
            logger.warning(f"[paperclip] skills service unavailable: {e}")
            return {"skills": []}

    # ── Bootstrap ──────────────────────────────────────────────────────────────

    @router.post("/companies/{cid}/bootstrap")
    async def bootstrap_company(cid: str, request: Request):
        """Auto-create a CEO and CTO for the company."""
        owner = _owner(request)
        db = _SessionLocal()
        try:
            row = db.execute(sa.text(
                "SELECT name, goal FROM pc_companies WHERE id=:id"
            ), {"id": cid}).fetchone()
            if not row:
                raise HTTPException(404, "Company not found")
            company_name, company_goal = row

            existing_count = db.execute(sa.text(
                "SELECT COUNT(*) FROM pc_agents WHERE company_id=:cid"
            ), {"cid": cid}).fetchone()[0]
            if existing_count > 0:
                raise HTTPException(400, "Company already has agents — clear them first")

            ceo_id = str(uuid.uuid4())
            cto_id = str(uuid.uuid4())
            goal_text = f" Company goal: {company_goal}." if company_goal else ""

            action_instructions = (
                "\n\n## Management Actions\n"
                "When you want to take a concrete action (hire, assign a task, set a model, update memory), "
                "output it as a JSON code block tagged `action`. The Paperclip UI will execute it automatically.\n\n"
                "**Hire an agent:**\n"
                "```action\n"
                '{"action":"hire","name":"Alice","role":"Backend Engineer","model":"","system_prompt":"You are a backend engineer..."}\n'
                "```\n\n"
                "**Assign a task:**\n"
                "```action\n"
                '{"action":"assign_task","title":"Build REST API","description":"...","assignee":"Alice"}\n'
                "```\n\n"
                "**Set an agent's model:**\n"
                "```action\n"
                '{"action":"set_model","agent":"Alice","model":"claude-opus-4-8"}\n'
                "```\n\n"
                "**Update an agent's memory:**\n"
                "```action\n"
                '{"action":"update_memory","agent":"Alice","memory":"Alice specializes in Python/FastAPI."}\n'
                "```\n\n"
                "You can include one or more action blocks in a single response alongside your normal explanation."
            )

            db.execute(sa.text(
                "INSERT INTO pc_agents (id,company_id,name,role,model,system_prompt,is_ceo,skills,mcps,memory,owner) "
                "VALUES (:id,:cid,:name,:role,:model,:sp,1,'[]','[]','',:owner)"
            ), {
                "id":    ceo_id, "cid": cid,
                "name":  "CEO",
                "role":  "Chief Executive Officer",
                "model": "",
                "sp": (
                    f"You are the CEO of {company_name}.{goal_text}\n\n"
                    "You lead a team of specialized AI agents. You set strategy, delegate work, and ensure company goals are achieved.\n\n"
                    "## Your Responsibilities\n"
                    "- Define and communicate company strategy\n"
                    "- Hire agents with the right skills for each role\n"
                    "- Assign tasks and review progress\n"
                    "- Make final decisions on technical and product direction"
                    + action_instructions
                ),
                "owner": owner,
            })

            db.execute(sa.text(
                "INSERT INTO pc_agents (id,company_id,name,role,model,system_prompt,skills,mcps,memory,owner) "
                "VALUES (:id,:cid,:name,:role,:model,:sp,'[]','[]','',:owner)"
            ), {
                "id":    cto_id, "cid": cid,
                "name":  "CTO",
                "role":  "Chief Technology Officer",
                "model": "",
                "sp": (
                    f"You are the CTO of {company_name}.{goal_text}\n\n"
                    "You lead technical strategy, architecture decisions, and engineering execution. "
                    "You work closely with the CEO to turn company goals into concrete technical plans. "
                    "You evaluate tooling, set engineering standards, and unblock the technical team."
                    + action_instructions
                ),
                "owner": owner,
            })

            db.commit()
            return {"ceo_id": ceo_id, "cto_id": cto_id, "ok": True}
        finally:
            db.close()

    # ── Companies ──────────────────────────────────────────────────────────────

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
            row = db.execute(sa.text(
                "SELECT id,name,goal,owner,created_at FROM pc_companies WHERE id=:id"
            ), {"id": cid}).fetchone()
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
            db.execute(sa.text("DELETE FROM pc_tasks WHERE company_id=:id"),   {"id": cid})
            db.execute(sa.text("DELETE FROM pc_agents WHERE company_id=:id"),  {"id": cid})
            db.execute(sa.text("DELETE FROM pc_companies WHERE id=:id"),       {"id": cid})
            db.commit()
            return {"ok": True}
        finally:
            db.close()

    # ── Agents ─────────────────────────────────────────────────────────────────

    class AgentCreate(BaseModel):
        name: str
        role: str = ""
        model: str = ""
        system_prompt: str = ""
        token_budget: int = 0
        skills: List[str] = []
        mcps: List[str] = []
        memory: str = ""
        is_ceo: bool = False

    class AgentUpdate(BaseModel):
        name: Optional[str] = None
        role: Optional[str] = None
        model: Optional[str] = None
        system_prompt: Optional[str] = None
        token_budget: Optional[int] = None
        skills: Optional[List[str]] = None
        mcps: Optional[List[str]] = None
        memory: Optional[str] = None
        is_ceo: Optional[bool] = None

    _AGENT_COLS = (
        "id,company_id,name,role,model,system_prompt,token_budget,owner,created_at,"
        "skills,mcps,memory,is_ceo,active_session_id"
    )

    @router.get("/companies/{cid}/agents")
    def list_agents(cid: str, request: Request):
        db = _SessionLocal()
        try:
            rows = db.execute(sa.text(
                f"SELECT {_AGENT_COLS} FROM pc_agents "
                "WHERE company_id=:cid ORDER BY is_ceo DESC, created_at"
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
                "INSERT INTO pc_agents "
                "(id,company_id,name,role,model,system_prompt,token_budget,owner,skills,mcps,memory,is_ceo) "
                "VALUES (:id,:cid,:name,:role,:model,:sp,:budget,:owner,:skills,:mcps,:memory,:is_ceo)"
            ), {
                "id": aid, "cid": cid,
                "name": body.name.strip(), "role": body.role,
                "model": body.model, "sp": body.system_prompt,
                "budget": body.token_budget, "owner": owner,
                "skills": json.dumps(body.skills),
                "mcps":   json.dumps(body.mcps),
                "memory": body.memory,
                "is_ceo": 1 if body.is_ceo else 0,
            })
            db.commit()
            row = db.execute(sa.text(
                f"SELECT {_AGENT_COLS} FROM pc_agents WHERE id=:id"
            ), {"id": aid}).fetchone()
            return _agent(row)
        finally:
            db.close()

    @router.put("/agents/{aid}")
    def update_agent(aid: str, body: AgentUpdate, request: Request):
        db = _SessionLocal()
        try:
            if not db.execute(sa.text("SELECT id FROM pc_agents WHERE id=:id"), {"id": aid}).fetchone():
                raise HTTPException(404, "Agent not found")
            updates = {}
            for k, v in body.model_dump().items():
                if v is None:
                    continue
                if k in ('skills', 'mcps'):
                    updates[k] = json.dumps(v)
                elif k == 'is_ceo':
                    updates[k] = 1 if v else 0
                else:
                    updates[k] = v
            if updates:
                sets = ", ".join(f"{k}=:{k}" for k in updates)
                updates["aid"] = aid
                db.execute(sa.text(f"UPDATE pc_agents SET {sets} WHERE id=:aid"), updates)
                db.commit()
            row = db.execute(sa.text(
                f"SELECT {_AGENT_COLS} FROM pc_agents WHERE id=:id"
            ), {"id": aid}).fetchone()
            return _agent(row)
        finally:
            db.close()

    @router.delete("/agents/{aid}")
    def delete_agent(aid: str, request: Request):
        db = _SessionLocal()
        try:
            if not db.execute(sa.text("SELECT id FROM pc_agents WHERE id=:id"), {"id": aid}).fetchone():
                raise HTTPException(404, "Agent not found")
            db.execute(sa.text("UPDATE pc_tasks SET agent_id=NULL WHERE agent_id=:id"), {"id": aid})
            db.execute(sa.text("DELETE FROM pc_agents WHERE id=:id"), {"id": aid})
            db.commit()
            return {"ok": True}
        finally:
            db.close()

    @router.post("/agents/{aid}/run")
    async def run_agent(aid: str, request: Request):
        """
        Create (or resume) a chat session for this agent.
        Returns {session_id, name, existing: bool}.
        """
        owner = _owner(request)
        db = _SessionLocal()
        try:
            row = db.execute(sa.text(
                f"SELECT {_AGENT_COLS} FROM pc_agents WHERE id=:id"
            ), {"id": aid}).fetchone()
            if not row:
                raise HTTPException(404, "Agent not found")
            (agent_id, company_id, agent_name, agent_role, agent_model,
             system_prompt, _budget, _owner_col, _created,
             skills_json, mcps_json, memory, is_ceo, active_sid) = row
        finally:
            db.close()

        # ── Reuse existing session if still valid ──────────────────────────────
        if active_sid:
            try:
                from core.models import get_session_manager
                sm = get_session_manager()
                existing = sm.get_session(active_sid) if sm else None
                if existing and (not owner or existing.owner == owner):
                    return {"session_id": active_sid, "name": existing.name, "existing": True}
            except Exception:
                pass

        try:
            from core.models import get_session_manager, ChatMessage
        except ImportError:
            raise HTTPException(503, "Session manager unavailable")

        sm = get_session_manager()
        if sm is None:
            raise HTTPException(503, "Session manager not ready")

        # ── Resolve endpoint ───────────────────────────────────────────────────
        endpoint_url, resolved_model = None, agent_model
        try:
            from src.endpoint_resolver import resolve_endpoint
            endpoint_url, default_model, _ = resolve_endpoint("default", owner=owner)
            if not agent_model:
                resolved_model = default_model
        except Exception:
            pass

        # ── Build enriched system prompt ───────────────────────────────────────
        parts = []
        if system_prompt:
            parts.append(system_prompt)

        if memory:
            parts.append(f"## Your Memory\n{memory}")

        skills = json.loads(skills_json or '[]')
        if skills:
            parts.append("## Your Skills\n" + "\n".join(f"- {s}" for s in skills))

        mcps = json.loads(mcps_json or '[]')
        if mcps:
            parts.append("## Connected Tools (MCP)\n" + "\n".join(f"- {m}" for m in mcps))

        if is_ceo:
            db2 = _SessionLocal()
            try:
                team_rows = db2.execute(sa.text(
                    "SELECT name, role, model FROM pc_agents "
                    "WHERE company_id=:cid AND id!=:aid ORDER BY name"
                ), {"cid": company_id, "aid": aid}).fetchall()
                task_rows = db2.execute(sa.text(
                    "SELECT title, status, agent_id FROM pc_tasks "
                    "WHERE company_id=:cid ORDER BY created_at DESC LIMIT 30"
                ), {"cid": company_id}).fetchall()
                agent_map = {
                    r[0]: r[1]
                    for r in db2.execute(sa.text(
                        "SELECT id, name FROM pc_agents WHERE company_id=:cid"
                    ), {"cid": company_id}).fetchall()
                }
            finally:
                db2.close()

            if team_rows:
                team_list = "\n".join(
                    f"- **{t[0]}** ({t[1]})" + (f"  [model: {t[2]}]" if t[2] else "")
                    for t in team_rows
                )
                parts.append(f"## Your Team\n{team_list}")

            if task_rows:
                task_list = "\n".join(
                    f"- [{t[1].upper()}] {t[0]}"
                    + (f" → {agent_map.get(t[2], '')}" if t[2] else "")
                    for t in task_rows
                )
                parts.append(f"## Task Board\n{task_list}")

            parts.append(
                "## Management Actions\n"
                "To take a concrete action, output a JSON code block tagged `action`. "
                "The Paperclip UI parses and executes it automatically.\n\n"
                "Hire: ```action\n{\"action\":\"hire\",\"name\":\"Alice\",\"role\":\"Backend Engineer\",\"model\":\"\",\"system_prompt\":\"...\"}\n```\n\n"
                "Assign task: ```action\n{\"action\":\"assign_task\",\"title\":\"Build API\",\"description\":\"...\",\"assignee\":\"Alice\"}\n```\n\n"
                "Set model: ```action\n{\"action\":\"set_model\",\"agent\":\"Alice\",\"model\":\"claude-opus-4-8\"}\n```\n\n"
                "Update memory: ```action\n{\"action\":\"update_memory\",\"agent\":\"Alice\",\"memory\":\"...\"}\n```"
            )

        enhanced_prompt = "\n\n---\n\n".join(parts)

        # ── Create session ─────────────────────────────────────────────────────
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

        if enhanced_prompt:
            new_session.add_message(ChatMessage(
                role="system",
                content=enhanced_prompt,
                metadata={"hidden": True, "paperclip_agent": aid},
            ))

        # Store active session ID
        with _engine.connect() as conn:
            conn.execute(sa.text(
                "UPDATE pc_agents SET active_session_id=:sid WHERE id=:id"
            ), {"sid": session_id, "id": aid})
            conn.commit()

        try:
            from src.event_bus import fire_event
            fire_event("session_created", owner)
        except Exception:
            pass

        return {"session_id": session_id, "name": new_session.name, "existing": False}

    # ── Tasks ──────────────────────────────────────────────────────────────────

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
            ), {
                "id": tid, "cid": cid, "aid": body.agent_id,
                "title": body.title.strip(), "desc": body.description,
                "pri": body.priority, "owner": owner,
            })
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
            if not db.execute(sa.text("SELECT id FROM pc_tasks WHERE id=:id"), {"id": tid}).fetchone():
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


# ── Serializers ────────────────────────────────────────────────────────────────

def _company(r):
    return {
        "id": r[0], "name": r[1], "goal": r[2],
        "owner": r[3], "created_at": str(r[4]) if r[4] else None,
    }

def _agent(r):
    return {
        "id": r[0], "company_id": r[1], "name": r[2], "role": r[3],
        "model": r[4], "system_prompt": r[5], "token_budget": r[6],
        "owner": r[7], "created_at": str(r[8]) if r[8] else None,
        "skills":    _jlist(r[9]  if len(r) > 9  else None),
        "mcps":      _jlist(r[10] if len(r) > 10 else None),
        "memory":    r[11]        if len(r) > 11 else "",
        "is_ceo":    bool(r[12])  if len(r) > 12 else False,
        "active_session_id": r[13] if len(r) > 13 else None,
    }

def _task(r):
    return {
        "id": r[0], "company_id": r[1], "agent_id": r[2], "title": r[3],
        "description": r[4], "status": r[5], "priority": r[6],
        "owner": r[7], "created_at": str(r[8]) if r[8] else None,
    }

def _jlist(v):
    try:
        return json.loads(v or '[]')
    except Exception:
        return []
