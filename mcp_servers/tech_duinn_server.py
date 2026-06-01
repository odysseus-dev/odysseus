"""
tech_duinn_server.py

MCP server bridging Odysseus to the Tech Duinn shared agent workspace.
Provides task queue, agent registry, log store, event bus, and shared memory
backed by SQLite + FTS5.

The actual server implementation lives in the tech-duinn repo. This wrapper
sets up the path and runs it as a built-in MCP server.
"""

import asyncio
import json
import os
import sqlite3
import sys
import time
import uuid
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

server = Server("tech-duinn")

_conn = None
_initialized = False

# Data directory — uses Odysseus's data/tech_duinn/ so it persists across restarts
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "tech_duinn")
DB_PATH = os.path.join(DATA_DIR, "tech-duinn.db")


def _ensure_init():
    """Lazy-init database connection and tables."""
    global _conn, _initialized
    if _initialized:
        return
    _initialized = True
    os.makedirs(DATA_DIR, exist_ok=True)
    _conn = sqlite3.connect(DB_PATH)
    _conn.row_factory = sqlite3.Row
    _conn.execute("PRAGMA journal_mode=WAL")
    _conn.execute("PRAGMA foreign_keys=ON")
    _init_tables(_conn)


def _init_tables(conn):
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


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="manage_tasks",
            description="Tech Duinn swarm task queue: create, assign, complete, list, search distributed work items across agents.",
            inputSchema={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["create", "get", "list", "assign", "update_status", "search"]},
                    "task_id": {"type": "string"},
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "priority": {"type": "integer", "description": "1-10, higher = more urgent"},
                    "status": {"type": "string", "enum": ["pending", "assigned", "in_progress", "completed", "failed", "cancelled"]},
                    "assigned_to": {"type": "string", "description": "Agent ID to assign to"},
                    "created_by": {"type": "string"},
                    "tags": {"type": "string", "description": "Comma-separated tags"},
                    "result": {"type": "string", "description": "Task result/output"},
                    "query": {"type": "string", "description": "Full-text search query"},
                    "limit": {"type": "integer"},
                },
                "required": ["action"],
            },
        ),
        Tool(
            name="manage_swarm_agents",
            description="Tech Duinn agent registry: register agents, send heartbeats, list/query swarm agent status and health.",
            inputSchema={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["register", "heartbeat", "list", "get"]},
                    "agent_id": {"type": "string"},
                    "name": {"type": "string"},
                    "role": {"type": "string", "description": "Agent role (e.g. 'Security Chief', 'Research Scientist')"},
                    "capabilities": {"type": "string", "description": "Comma-separated capabilities"},
                    "status": {"type": "string", "enum": ["online", "busy", "offline", "error"]},
                    "metadata": {"type": "string", "description": "JSON metadata"},
                },
                "required": ["action"],
            },
        ),
        Tool(
            name="manage_swarm_logs",
            description="Tech Duinn log store: write and search centralized logs from all swarm agents.",
            inputSchema={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["write", "search", "tail"]},
                    "message": {"type": "string"},
                    "agent_id": {"type": "string"},
                    "level": {"type": "string", "enum": ["debug", "info", "warn", "error", "fatal"]},
                    "source": {"type": "string", "description": "Log source (e.g. 'agent.loop', 'mcp.server')"},
                    "query": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                "required": ["action"],
            },
        ),
        Tool(
            name="manage_swarm_events",
            description="Tech Duinn event bus: publish and poll inter-agent events for coordination.",
            inputSchema={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["publish", "poll"]},
                    "topic": {"type": "string", "description": "Event topic (e.g. 'task.completed', 'agent.error')"},
                    "payload": {"type": "string", "description": "JSON event payload"},
                    "source": {"type": "string"},
                    "since": {"type": "number", "description": "Unix timestamp to poll from"},
                    "limit": {"type": "integer"},
                },
                "required": ["action"],
            },
        ),
        Tool(
            name="manage_swarm_memory",
            description="Tech Duinn shared memory: persistent key-value store with FTS5 search for cross-agent knowledge sharing.",
            inputSchema={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["set", "get", "list", "search", "delete"]},
                    "key": {"type": "string"},
                    "value": {"type": "string"},
                    "namespace": {"type": "string", "description": "Memory namespace (default: shared)"},
                    "tags": {"type": "string"},
                    "query": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                "required": ["action"],
            },
        ),
    ]


def _t(text: str) -> list[TextContent]:
    return [TextContent(type="text", text=text)]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    _ensure_init()
    action = arguments.get("action", "")

    try:
        if name == "manage_tasks":
            return _handle_tasks(action, arguments)
        elif name == "manage_swarm_agents":
            return _handle_agents(action, arguments)
        elif name == "manage_swarm_logs":
            return _handle_logs(action, arguments)
        elif name == "manage_swarm_events":
            return _handle_events(action, arguments)
        elif name == "manage_swarm_memory":
            return _handle_memory(action, arguments)
        return _t(f"Unknown tool: {name}")
    except Exception as e:
        return _t(f"Error: {type(e).__name__}: {e}")


def _handle_tasks(action: str, args: dict) -> list[TextContent]:
    now = time.time()
    if action == "create":
        tid = uuid.uuid4().hex[:12]
        _conn.execute(
            "INSERT INTO tasks (id,title,description,priority,created_by,tags,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (tid, args.get("title", "Untitled"), args.get("description", ""), args.get("priority", 5), args.get("created_by", "agent"), args.get("tags", ""), now, now),
        )
        _conn.commit()
        return _t(f"Task created: {tid} — {args.get('title', 'Untitled')} (priority {args.get('priority', 5)})")
    elif action == "get":
        r = _conn.execute("SELECT * FROM tasks WHERE id=?", (args.get("task_id", ""),)).fetchone()
        return _t(json.dumps(dict(r), indent=2) if r else "Task not found")
    elif action == "list":
        sql = "SELECT * FROM tasks WHERE 1=1"
        params = []
        if args.get("status"):
            sql += " AND status=?"; params.append(args["status"])
        if args.get("assigned_to"):
            sql += " AND assigned_to=?"; params.append(args["assigned_to"])
        sql += " ORDER BY priority DESC, created_at DESC LIMIT ?"
        params.append(args.get("limit", 50))
        rows = _conn.execute(sql, params).fetchall()
        if not rows:
            return _t("No tasks found.")
        lines = [f"Tasks ({len(rows)}):\n"]
        for r in rows:
            r = dict(r)
            lines.append(f"- `{r['id']}` [{r['status']}] P{r['priority']} — {r['title']}" + (f" → {r['assigned_to']}" if r.get('assigned_to') else ""))
        return _t("\n".join(lines))
    elif action == "assign":
        _conn.execute("UPDATE tasks SET assigned_to=?, status='assigned', updated_at=? WHERE id=? AND status IN ('pending','assigned')",
                      (args.get("assigned_to", ""), now, args.get("task_id", "")))
        _conn.commit()
        return _t(f"Assigned task {args.get('task_id', '')} → {args.get('assigned_to', '')}")
    elif action == "update_status":
        completed_at = now if args.get("status") in ("completed", "failed") else None
        _conn.execute("UPDATE tasks SET status=?, result=?, updated_at=?, completed_at=? WHERE id=?",
                      (args.get("status", ""), args.get("result", ""), now, completed_at, args.get("task_id", "")))
        _conn.commit()
        return _t(f"Task {args.get('task_id', '')} → {args.get('status', '')}")
    elif action == "search":
        rows = _conn.execute(
            "SELECT t.* FROM tasks_fts f JOIN tasks t ON f.rowid = t.rowid WHERE tasks_fts MATCH ? ORDER BY rank LIMIT ?",
            (args.get("query", ""), args.get("limit", 20)),
        ).fetchall()
        if not rows:
            return _t("No matching tasks.")
        lines = [f"Search results ({len(rows)}):\n"]
        for r in rows:
            r = dict(r)
            lines.append(f"- `{r['id']}` [{r['status']}] — {r['title']}")
        return _t("\n".join(lines))
    return _t(f"Unknown task action: {action}")


def _handle_agents(action: str, args: dict) -> list[TextContent]:
    now = time.time()
    if action == "register":
        aid = args.get("agent_id", uuid.uuid4().hex[:8])
        _conn.execute(
            "INSERT OR REPLACE INTO agents (id,name,role,capabilities,status,last_heartbeat,metadata,registered_at) VALUES (?,?,?,?,?,?,?,?)",
            (aid, args.get("name", "unnamed"), args.get("role", ""), args.get("capabilities", ""), "online", now, args.get("metadata", "{}"), now),
        )
        _conn.commit()
        return _t(f"Agent registered: {aid} ({args.get('name', 'unnamed')}) — online")
    elif action == "heartbeat":
        cur = _conn.execute("UPDATE agents SET status=?, last_heartbeat=? WHERE id=?",
                            (args.get("status", "online"), now, args.get("agent_id", "")))
        _conn.commit()
        return _t("Heartbeat recorded" if cur.rowcount > 0 else "Agent not found")
    elif action == "list":
        status = args.get("status", "")
        if status:
            rows = _conn.execute("SELECT * FROM agents WHERE status=? ORDER BY name", (status,)).fetchall()
        else:
            rows = _conn.execute("SELECT * FROM agents ORDER BY name").fetchall()
        if not rows:
            return _t("No agents registered.")
        lines = [f"Swarm agents ({len(rows)}):\n"]
        for r in rows:
            r = dict(r)
            hb = time.strftime("%H:%M:%S", time.localtime(r['last_heartbeat'])) if r.get('last_heartbeat') else "never"
            lines.append(f"- `{r['id']}` {r['name']} [{r['status']}] — {r['role'] or 'no role'} (last seen: {hb})")
        return _t("\n".join(lines))
    elif action == "get":
        r = _conn.execute("SELECT * FROM agents WHERE id=?", (args.get("agent_id", ""),)).fetchone()
        return _t(json.dumps(dict(r), indent=2) if r else "Agent not found")
    return _t(f"Unknown agent action: {action}")


def _handle_logs(action: str, args: dict) -> list[TextContent]:
    now = time.time()
    if action == "write":
        cur = _conn.execute(
            "INSERT INTO logs (agent_id,level,source,message,metadata,timestamp) VALUES (?,?,?,?,?,?)",
            (args.get("agent_id", ""), args.get("level", "info"), args.get("source", ""), args.get("message", ""), "{}", now),
        )
        _conn.commit()
        return _t(f"Log entry #{cur.lastrowid} written")
    elif action in ("search", "tail"):
        query = args.get("query", "")
        if query:
            sql = "SELECT l.* FROM logs_fts f JOIN logs l ON f.rowid = l.id WHERE logs_fts MATCH ?"
            params = [query]
            if args.get("agent_id"):
                sql += " AND l.agent_id=?"; params.append(args["agent_id"])
            if args.get("level"):
                sql += " AND l.level=?"; params.append(args["level"])
            sql += " ORDER BY l.timestamp DESC LIMIT ?"
            params.append(args.get("limit", 50))
        else:
            sql = "SELECT * FROM logs WHERE 1=1"
            params = []
            if args.get("agent_id"):
                sql += " AND agent_id=?"; params.append(args["agent_id"])
            if args.get("level"):
                sql += " AND level=?"; params.append(args["level"])
            sql += " ORDER BY timestamp DESC LIMIT ?"
            params.append(args.get("limit", 50))
        rows = _conn.execute(sql, params).fetchall()
        if not rows:
            return _t("No log entries found.")
        lines = [f"Logs ({len(rows)}):\n"]
        for r in rows:
            r = dict(r)
            ts = time.strftime("%H:%M:%S", time.localtime(r['timestamp']))
            lines.append(f"[{ts}] [{r['level'].upper()}] {r['agent_id'] or '?'}: {r['message'][:120]}")
        return _t("\n".join(lines))
    return _t(f"Unknown log action: {action}")


def _handle_events(action: str, args: dict) -> list[TextContent]:
    now = time.time()
    if action == "publish":
        cur = _conn.execute(
            "INSERT INTO events (topic,source,payload,timestamp) VALUES (?,?,?,?)",
            (args.get("topic", "general"), args.get("source", ""), args.get("payload", "{}"), now),
        )
        _conn.commit()
        return _t(f"Event #{cur.lastrowid} published to '{args.get('topic', 'general')}'")
    elif action == "poll":
        sql = "SELECT * FROM events WHERE timestamp > ?"
        params = [args.get("since", 0)]
        if args.get("topic"):
            sql += " AND topic=?"; params.append(args["topic"])
        sql += " ORDER BY timestamp DESC LIMIT ?"
        params.append(args.get("limit", 50))
        rows = _conn.execute(sql, params).fetchall()
        if not rows:
            return _t("No events found.")
        lines = [f"Events ({len(rows)}):\n"]
        for r in rows:
            r = dict(r)
            ts = time.strftime("%H:%M:%S", time.localtime(r['timestamp']))
            lines.append(f"[{ts}] {r['topic']} from {r['source'] or '?'}: {r['payload'][:100]}")
        return _t("\n".join(lines))
    return _t(f"Unknown event action: {action}")


def _handle_memory(action: str, args: dict) -> list[TextContent]:
    now = time.time()
    if action == "set":
        mid = uuid.uuid4().hex[:12]
        ns = args.get("namespace", "shared")
        key = args.get("key", "")
        _conn.execute(
            "INSERT OR REPLACE INTO memory (id,namespace,key,value,tags,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
            (mid, ns, key, args.get("value", ""), args.get("tags", ""), now, now),
        )
        _conn.commit()
        return _t(f"Memory set: {ns}/{key}")
    elif action == "get":
        r = _conn.execute("SELECT * FROM memory WHERE namespace=? AND key=?",
                          (args.get("namespace", "shared"), args.get("key", ""))).fetchone()
        return _t(json.dumps(dict(r), indent=2) if r else "Not found")
    elif action == "list":
        rows = _conn.execute("SELECT * FROM memory WHERE namespace=? ORDER BY updated_at DESC LIMIT ?",
                             (args.get("namespace", "shared"), args.get("limit", 100))).fetchall()
        if not rows:
            return _t("No memory entries.")
        lines = [f"Memory ({len(rows)}):\n"]
        for r in rows:
            r = dict(r)
            val = r['value'][:80] + "..." if len(r['value']) > 80 else r['value']
            lines.append(f"- `{r['key']}` = {val}")
        return _t("\n".join(lines))
    elif action == "search":
        query = args.get("query", "")
        ns = args.get("namespace", "")
        if ns:
            rows = _conn.execute(
                "SELECT m.* FROM memory_fts f JOIN memory m ON f.rowid = m.rowid WHERE memory_fts MATCH ? AND m.namespace=? ORDER BY rank LIMIT ?",
                (query, ns, args.get("limit", 20)),
            ).fetchall()
        else:
            rows = _conn.execute(
                "SELECT m.* FROM memory_fts f JOIN memory m ON f.rowid = m.rowid WHERE memory_fts MATCH ? ORDER BY rank LIMIT ?",
                (query, args.get("limit", 20)),
            ).fetchall()
        if not rows:
            return _t("No matching memory.")
        lines = [f"Memory search ({len(rows)}):\n"]
        for r in rows:
            r = dict(r)
            lines.append(f"- [{r['namespace']}] `{r['key']}` = {r['value'][:80]}")
        return _t("\n".join(lines))
    elif action == "delete":
        cur = _conn.execute("DELETE FROM memory WHERE namespace=? AND key=?",
                            (args.get("namespace", "shared"), args.get("key", "")))
        _conn.commit()
        return _t("Deleted" if cur.rowcount > 0 else "Not found")
    return _t(f"Unknown memory action: {action}")


async def run():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(run())
