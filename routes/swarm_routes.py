"""Tech Duinn swarm orchestrator routes — /api/swarm/*.

Exposes the Tech Duinn shared agent workspace (task queue, agent registry,
log store, event bus, shared memory) as REST endpoints backed by SQLite + FTS5.
"""

import json
import logging
import os
import sqlite3
import time
import uuid
from typing import Dict, Any, Optional, List

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# Data directory
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "tech_duinn")
DB_PATH = os.path.join(DATA_DIR, "tech-duinn.db")

_conn: Optional[sqlite3.Connection] = None
_initialized = False


def _get_conn() -> sqlite3.Connection:
    """Lazy-init database connection and tables."""
    global _conn, _initialized
    if _initialized and _conn:
        return _conn
    _initialized = True
    os.makedirs(DATA_DIR, exist_ok=True)
    _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    _conn.row_factory = sqlite3.Row
    _conn.execute("PRAGMA journal_mode=WAL")
    _conn.execute("PRAGMA foreign_keys=ON")
    _init_tables(_conn)
    return _conn


def _init_tables(conn: sqlite3.Connection):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            description TEXT DEFAULT '',
            status TEXT DEFAULT 'pending' CHECK(status IN ('pending','assigned','in_progress','completed','failed','cancelled')),
            priority INTEGER DEFAULT 5 CHECK(priority BETWEEN 1 AND 10),
            assigned_to TEXT,
            created_by TEXT DEFAULT 'system',
            tags TEXT DEFAULT '',
            result TEXT DEFAULT '',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            completed_at REAL
        );
        CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
        CREATE INDEX IF NOT EXISTS idx_tasks_assigned ON tasks(assigned_to);
        CREATE INDEX IF NOT EXISTS idx_tasks_priority ON tasks(priority DESC);

        CREATE VIRTUAL TABLE IF NOT EXISTS tasks_fts USING fts5(
            title, description, tags, result, content='tasks', content_rowid='rowid'
        );
        CREATE TRIGGER IF NOT EXISTS tasks_ai AFTER INSERT ON tasks BEGIN
            INSERT INTO tasks_fts(rowid, title, description, tags, result) VALUES (new.rowid, new.title, new.description, new.tags, new.result);
        END;
        CREATE TRIGGER IF NOT EXISTS tasks_ad AFTER DELETE ON tasks BEGIN
            INSERT INTO tasks_fts(tasks_fts, rowid, title, description, tags, result) VALUES ('delete', old.rowid, old.title, old.description, old.tags, old.result);
        END;
        CREATE TRIGGER IF NOT EXISTS tasks_au AFTER UPDATE ON tasks BEGIN
            INSERT INTO tasks_fts(tasks_fts, rowid, title, description, tags, result) VALUES ('delete', old.rowid, old.title, old.description, old.tags, old.result);
            INSERT INTO tasks_fts(rowid, title, description, tags, result) VALUES (new.rowid, new.title, new.description, new.tags, new.result);
        END;

        CREATE TABLE IF NOT EXISTS agents (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            role TEXT DEFAULT '',
            capabilities TEXT DEFAULT '',
            status TEXT DEFAULT 'offline' CHECK(status IN ('online','busy','offline','error')),
            last_heartbeat REAL,
            metadata TEXT DEFAULT '{}',
            registered_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_agents_status ON agents(status);

        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id TEXT,
            level TEXT DEFAULT 'info' CHECK(level IN ('debug','info','warn','error','fatal')),
            source TEXT DEFAULT '',
            message TEXT NOT NULL,
            metadata TEXT DEFAULT '{}',
            timestamp REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_logs_agent ON logs(agent_id);
        CREATE INDEX IF NOT EXISTS idx_logs_time ON logs(timestamp DESC);

        CREATE VIRTUAL TABLE IF NOT EXISTS logs_fts USING fts5(
            message, source, agent_id, content='logs', content_rowid='id'
        );
        CREATE TRIGGER IF NOT EXISTS logs_ai AFTER INSERT ON logs BEGIN
            INSERT INTO logs_fts(rowid, message, source, agent_id) VALUES (new.id, new.message, new.source, new.agent_id);
        END;

        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            topic TEXT NOT NULL,
            source TEXT DEFAULT '',
            payload TEXT DEFAULT '{}',
            timestamp REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_events_topic ON events(topic);
        CREATE INDEX IF NOT EXISTS idx_events_time ON events(timestamp DESC);

        CREATE TABLE IF NOT EXISTS memory (
            id TEXT PRIMARY KEY,
            namespace TEXT DEFAULT 'shared',
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            tags TEXT DEFAULT '',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_memory_ns ON memory(namespace);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_ns_key ON memory(namespace, key);

        CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
            key, value, tags, content='memory', content_rowid='rowid'
        );
        CREATE TRIGGER IF NOT EXISTS memory_ai AFTER INSERT ON memory BEGIN
            INSERT INTO memory_fts(rowid, key, value, tags) VALUES (new.rowid, new.key, new.value, new.tags);
        END;
        CREATE TRIGGER IF NOT EXISTS memory_ad AFTER DELETE ON memory BEGIN
            INSERT INTO memory_fts(memory_fts, rowid, key, value, tags) VALUES ('delete', old.rowid, old.key, old.value, old.tags);
        END;
        CREATE TRIGGER IF NOT EXISTS memory_au AFTER UPDATE ON memory BEGIN
            INSERT INTO memory_fts(memory_fts, rowid, key, value, tags) VALUES ('delete', old.rowid, old.key, old.value, old.tags);
            INSERT INTO memory_fts(rowid, key, value, tags) VALUES (new.rowid, new.key, new.value, new.tags);
        END;
    """)


# ── Pydantic models ──────────────────────────────────────────────────────

class TaskCreate(BaseModel):
    title: str
    description: str = ""
    priority: int = 5
    created_by: str = "agent"
    tags: str = ""

class TaskUpdate(BaseModel):
    status: Optional[str] = None
    result: Optional[str] = None
    assigned_to: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    priority: Optional[int] = None
    tags: Optional[str] = None

class AgentRegister(BaseModel):
    name: str
    role: str = ""
    capabilities: str = ""
    metadata: str = "{}"

class AgentHeartbeat(BaseModel):
    status: str = "online"

class LogWrite(BaseModel):
    message: str
    agent_id: str = ""
    level: str = "info"
    source: str = ""
    metadata: str = "{}"

class EventPublish(BaseModel):
    topic: str = "general"
    source: str = ""
    payload: str = "{}"

class MemorySet(BaseModel):
    key: str
    value: str
    namespace: str = "shared"
    tags: str = ""


# ── Routes ────────────────────────────────────────────────────────────────

def setup_swarm_routes() -> APIRouter:
    router = APIRouter(tags=["swarm"])

    # ── Tasks ────────────────────────────────────────────────────────────

    @router.get("/api/swarm/tasks")
    async def list_tasks(
        status: Optional[str] = None,
        assigned_to: Optional[str] = None,
        limit: int = Query(50, ge=1, le=500),
    ) -> List[Dict[str, Any]]:
        """List tasks with optional filters."""
        conn = _get_conn()
        sql = "SELECT * FROM tasks WHERE 1=1"
        params = []
        if status:
            sql += " AND status=?"; params.append(status)
        if assigned_to:
            sql += " AND assigned_to=?"; params.append(assigned_to)
        sql += " ORDER BY priority DESC, created_at DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    @router.post("/api/swarm/tasks", status_code=201)
    async def create_task(body: TaskCreate) -> Dict[str, Any]:
        """Create a new task."""
        conn = _get_conn()
        now = time.time()
        tid = uuid.uuid4().hex[:12]
        conn.execute(
            "INSERT INTO tasks (id,title,description,priority,created_by,tags,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (tid, body.title, body.description, body.priority, body.created_by, body.tags, now, now),
        )
        conn.commit()
        return {"id": tid, "title": body.title, "status": "pending", "priority": body.priority}

    @router.get("/api/swarm/tasks/{task_id}")
    async def get_task(task_id: str) -> Dict[str, Any]:
        """Get a single task."""
        conn = _get_conn()
        r = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not r:
            raise HTTPException(404, "Task not found")
        return dict(r)

    @router.patch("/api/swarm/tasks/{task_id}")
    async def update_task(task_id: str, body: TaskUpdate) -> Dict[str, Any]:
        """Update a task (status, assign, etc)."""
        conn = _get_conn()
        now = time.time()
        r = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not r:
            raise HTTPException(404, "Task not found")

        updates = []
        params = []
        if body.status is not None:
            updates.append("status=?"); params.append(body.status)
            if body.status in ("completed", "failed"):
                updates.append("completed_at=?"); params.append(now)
        if body.result is not None:
            updates.append("result=?"); params.append(body.result)
        if body.assigned_to is not None:
            updates.append("assigned_to=?"); params.append(body.assigned_to)
        if body.title is not None:
            updates.append("title=?"); params.append(body.title)
        if body.description is not None:
            updates.append("description=?"); params.append(body.description)
        if body.priority is not None:
            updates.append("priority=?"); params.append(body.priority)
        if body.tags is not None:
            updates.append("tags=?"); params.append(body.tags)

        if not updates:
            return dict(r)

        updates.append("updated_at=?"); params.append(now)
        params.append(task_id)
        conn.execute(f"UPDATE tasks SET {','.join(updates)} WHERE id=?", params)
        conn.commit()
        r = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return dict(r)

    @router.delete("/api/swarm/tasks/{task_id}")
    async def delete_task(task_id: str) -> Dict[str, str]:
        """Delete a task."""
        conn = _get_conn()
        cur = conn.execute("DELETE FROM tasks WHERE id=?", (task_id,))
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(404, "Task not found")
        return {"status": "deleted", "id": task_id}

    @router.get("/api/swarm/tasks/search/{query}")
    async def search_tasks(query: str, limit: int = Query(20, ge=1, le=100)) -> List[Dict[str, Any]]:
        """Full-text search tasks."""
        conn = _get_conn()
        rows = conn.execute(
            "SELECT t.* FROM tasks_fts f JOIN tasks t ON f.rowid = t.rowid WHERE tasks_fts MATCH ? ORDER BY rank LIMIT ?",
            (query, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    # ── Agents ───────────────────────────────────────────────────────────

    @router.get("/api/swarm/agents")
    async def list_agents(status: Optional[str] = None) -> List[Dict[str, Any]]:
        """List registered agents."""
        conn = _get_conn()
        if status:
            rows = conn.execute("SELECT * FROM agents WHERE status=? ORDER BY name", (status,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM agents ORDER BY name").fetchall()
        return [dict(r) for r in rows]

    @router.post("/api/swarm/agents", status_code=201)
    async def register_agent(body: AgentRegister) -> Dict[str, Any]:
        """Register a new agent."""
        conn = _get_conn()
        now = time.time()
        aid = uuid.uuid4().hex[:8]
        conn.execute(
            "INSERT OR REPLACE INTO agents (id,name,role,capabilities,status,last_heartbeat,metadata,registered_at) VALUES (?,?,?,?,?,?,?,?)",
            (aid, body.name, body.role, body.capabilities, "online", now, body.metadata, now),
        )
        conn.commit()
        return {"id": aid, "name": body.name, "status": "online"}

    @router.get("/api/swarm/agents/{agent_id}")
    async def get_agent(agent_id: str) -> Dict[str, Any]:
        """Get agent details."""
        conn = _get_conn()
        r = conn.execute("SELECT * FROM agents WHERE id=?", (agent_id,)).fetchone()
        if not r:
            raise HTTPException(404, "Agent not found")
        return dict(r)

    @router.post("/api/swarm/agents/{agent_id}/heartbeat")
    async def agent_heartbeat(agent_id: str, body: AgentHeartbeat) -> Dict[str, str]:
        """Record agent heartbeat."""
        conn = _get_conn()
        now = time.time()
        cur = conn.execute("UPDATE agents SET status=?, last_heartbeat=? WHERE id=?",
                           (body.status, now, agent_id))
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(404, "Agent not found")
        return {"status": "ok"}

    @router.delete("/api/swarm/agents/{agent_id}")
    async def delete_agent(agent_id: str) -> Dict[str, str]:
        """Remove an agent."""
        conn = _get_conn()
        cur = conn.execute("DELETE FROM agents WHERE id=?", (agent_id,))
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(404, "Agent not found")
        return {"status": "deleted", "id": agent_id}

    # ── Logs ─────────────────────────────────────────────────────────────

    @router.get("/api/swarm/logs")
    async def list_logs(
        agent_id: Optional[str] = None,
        level: Optional[str] = None,
        limit: int = Query(50, ge=1, le=500),
    ) -> List[Dict[str, Any]]:
        """List recent log entries."""
        conn = _get_conn()
        sql = "SELECT * FROM logs WHERE 1=1"
        params = []
        if agent_id:
            sql += " AND agent_id=?"; params.append(agent_id)
        if level:
            sql += " AND level=?"; params.append(level)
        sql += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    @router.post("/api/swarm/logs", status_code=201)
    async def write_log(body: LogWrite) -> Dict[str, Any]:
        """Write a log entry."""
        conn = _get_conn()
        now = time.time()
        cur = conn.execute(
            "INSERT INTO logs (agent_id,level,source,message,metadata,timestamp) VALUES (?,?,?,?,?,?)",
            (body.agent_id, body.level, body.source, body.message, body.metadata, now),
        )
        conn.commit()
        return {"id": cur.lastrowid, "level": body.level}

    @router.get("/api/swarm/logs/search/{query}")
    async def search_logs(query: str, limit: int = Query(50, ge=1, le=200)) -> List[Dict[str, Any]]:
        """Full-text search logs."""
        conn = _get_conn()
        rows = conn.execute(
            "SELECT l.* FROM logs_fts f JOIN logs l ON f.rowid = l.id WHERE logs_fts MATCH ? ORDER BY l.timestamp DESC LIMIT ?",
            (query, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    @router.delete("/api/swarm/logs")
    async def clear_logs(before: Optional[float] = None) -> Dict[str, Any]:
        """Clear logs, optionally before a timestamp."""
        conn = _get_conn()
        if before:
            cur = conn.execute("DELETE FROM logs WHERE timestamp < ?", (before,))
        else:
            cur = conn.execute("DELETE FROM logs")
        conn.commit()
        return {"deleted": cur.rowcount}

    # ── Events ───────────────────────────────────────────────────────────

    @router.get("/api/swarm/events")
    async def list_events(
        topic: Optional[str] = None,
        since: Optional[float] = None,
        limit: int = Query(50, ge=1, le=500),
    ) -> List[Dict[str, Any]]:
        """List events, optionally filtered by topic or time."""
        conn = _get_conn()
        sql = "SELECT * FROM events WHERE 1=1"
        params = []
        if since:
            sql += " AND timestamp > ?"; params.append(since)
        if topic:
            sql += " AND topic=?"; params.append(topic)
        sql += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    @router.post("/api/swarm/events", status_code=201)
    async def publish_event(body: EventPublish) -> Dict[str, Any]:
        """Publish an event to the bus."""
        conn = _get_conn()
        now = time.time()
        cur = conn.execute(
            "INSERT INTO events (topic,source,payload,timestamp) VALUES (?,?,?,?)",
            (body.topic, body.source, body.payload, now),
        )
        conn.commit()
        return {"id": cur.lastrowid, "topic": body.topic}

    @router.delete("/api/swarm/events")
    async def clear_events(before: Optional[float] = None) -> Dict[str, Any]:
        """Clear events, optionally before a timestamp."""
        conn = _get_conn()
        if before:
            cur = conn.execute("DELETE FROM events WHERE timestamp < ?", (before,))
        else:
            cur = conn.execute("DELETE FROM events")
        conn.commit()
        return {"deleted": cur.rowcount}

    # ── Shared Memory ────────────────────────────────────────────────────

    @router.get("/api/swarm/memory")
    async def list_memory(
        namespace: str = "shared",
        limit: int = Query(100, ge=1, le=1000),
    ) -> List[Dict[str, Any]]:
        """List memory entries in a namespace."""
        conn = _get_conn()
        rows = conn.execute(
            "SELECT * FROM memory WHERE namespace=? ORDER BY updated_at DESC LIMIT ?",
            (namespace, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    @router.post("/api/swarm/memory", status_code=201)
    async def set_memory(body: MemorySet) -> Dict[str, str]:
        """Set a memory entry (upsert)."""
        conn = _get_conn()
        now = time.time()
        mid = uuid.uuid4().hex[:12]
        conn.execute(
            "INSERT OR REPLACE INTO memory (id,namespace,key,value,tags,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
            (mid, body.namespace, body.key, body.value, body.tags, now, now),
        )
        conn.commit()
        return {"id": mid, "namespace": body.namespace, "key": body.key}

    @router.get("/api/swarm/memory/{namespace}/{key}")
    async def get_memory(namespace: str, key: str) -> Dict[str, Any]:
        """Get a specific memory entry."""
        conn = _get_conn()
        r = conn.execute("SELECT * FROM memory WHERE namespace=? AND key=?", (namespace, key)).fetchone()
        if not r:
            raise HTTPException(404, "Memory entry not found")
        return dict(r)

    @router.delete("/api/swarm/memory/{namespace}/{key}")
    async def delete_memory(namespace: str, key: str) -> Dict[str, str]:
        """Delete a memory entry."""
        conn = _get_conn()
        cur = conn.execute("DELETE FROM memory WHERE namespace=? AND key=?", (namespace, key))
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(404, "Memory entry not found")
        return {"status": "deleted"}

    @router.get("/api/swarm/memory/search/{query}")
    async def search_memory(
        query: str,
        namespace: Optional[str] = None,
        limit: int = Query(20, ge=1, le=100),
    ) -> List[Dict[str, Any]]:
        """Full-text search memory entries."""
        conn = _get_conn()
        if namespace:
            rows = conn.execute(
                "SELECT m.* FROM memory_fts f JOIN memory m ON f.rowid = m.rowid WHERE memory_fts MATCH ? AND m.namespace=? ORDER BY rank LIMIT ?",
                (query, namespace, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT m.* FROM memory_fts f JOIN memory m ON f.rowid = m.rowid WHERE memory_fts MATCH ? ORDER BY rank LIMIT ?",
                (query, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    # ── Dashboard ────────────────────────────────────────────────────────

    @router.get("/api/swarm/stats")
    async def swarm_stats() -> Dict[str, Any]:
        """Get swarm overview statistics."""
        conn = _get_conn()
        task_counts = {}
        for row in conn.execute("SELECT status, COUNT(*) as cnt FROM tasks GROUP BY status"):
            task_counts[row["status"]] = row["cnt"]

        agent_counts = {}
        for row in conn.execute("SELECT status, COUNT(*) as cnt FROM agents GROUP BY status"):
            agent_counts[row["status"]] = row["cnt"]

        total_logs = conn.execute("SELECT COUNT(*) FROM logs").fetchone()[0]
        total_events = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        total_memory = conn.execute("SELECT COUNT(*) FROM memory").fetchone()[0]

        return {
            "tasks": task_counts,
            "agents": agent_counts,
            "total_logs": total_logs,
            "total_events": total_events,
            "total_memory": total_memory,
        }

    return router
