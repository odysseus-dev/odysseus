#!/usr/bin/env python3
"""
mcp_bridge.py — Shared MCP bridge for Claude Code, OpenCode, and OpenClaw.

Exposes the Tech Duinn MCP server over HTTP (SSE transport) so multiple
AI coding assistants can share the same task queue, agent registry, logs,
events, and memory.

Usage:
    python3 mcp_bridge.py                    # Start on port 8378
    python3 mcp_bridge.py --port 9000        # Custom port
    python3 mcp_bridge.py --token mysecret   # Require auth token

Endpoints:
    GET  /sse                 — MCP SSE transport (for MCP clients)
    POST /messages            — MCP message endpoint (for MCP clients)
    GET  /api/health          — Health check
    GET  /api/tasks           — List tasks (REST fallback)
    POST /api/tasks           — Create task (REST fallback)
    GET  /api/agents          — List agents (REST fallback)
    GET  /api/memory          — List memory (REST fallback)
    GET  /api/events          — List events (REST fallback)
    GET  /api/logs            — List logs (REST fallback)

Config for Claude Code (.claude/settings.json):
    "mcpServers": {
      "tech-duinn": {
        "url": "http://localhost:8378/sse",
        "headers": {"Authorization": "Bearer YOUR_TOKEN"}
      }
    }

Config for OpenCode (opencode.json):
    "mcp": {
      "tech-duinn": {
        "type": "sse",
        "url": "http://localhost:8378/sse",
        "headers": {"Authorization": "Bearer YOUR_TOKEN"}
      }
    }

Config for OpenClaw (.openclaw/config.json):
    "mcpServers": {
      "tech-duinn": {
        "transport": "sse",
        "url": "http://localhost:8378/sse"
      }
    }
"""

import argparse
import asyncio
import json
import logging
import os
import sqlite3
import sys
import time
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
logger = logging.getLogger("mcp-bridge")

# ── Database ─────────────────────────────────────────────────────────────

DB_PATH = PROJECT_ROOT / "data" / "tech_duinn" / "tech-duinn.db"


def get_db():
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    return db


# ── Auth ─────────────────────────────────────────────────────────────────

AUTH_TOKEN = os.getenv("MCP_BRIDGE_TOKEN", "")


def check_auth(headers: dict) -> bool:
    """Check authorization. Returns True if valid or no token is set."""
    if not AUTH_TOKEN:
        return True
    auth = headers.get("authorization", "")
    return auth == f"Bearer {AUTH_TOKEN}" or auth == AUTH_TOKEN


# ── REST API (fallback for non-MCP clients) ──────────────────────────────

async def handle_rest_request(method: str, path: str, body: dict = None) -> dict:
    """Handle REST API requests."""
    db = get_db()
    try:
        if path == "/api/health":
            return {"status": "ok", "db": str(DB_PATH), "timestamp": time.time()}

        elif path == "/api/tasks" and method == "GET":
            rows = db.execute("SELECT * FROM tasks ORDER BY priority DESC, created_at DESC LIMIT 100").fetchall()
            return {"tasks": [dict(r) for r in rows]}

        elif path == "/api/tasks" and method == "POST":
            data = body or {}
            task_id = data.get("id", f"task-{int(time.time()*1000)}")
            now = time.time()
            db.execute(
                "INSERT OR REPLACE INTO tasks (id, title, description, status, priority, assigned_to, created_by, tags, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (task_id, data.get("title", ""), data.get("description", ""), data.get("status", "pending"),
                 data.get("priority", 5), data.get("assigned_to"), data.get("created_by", "api"),
                 data.get("tags", ""), now, now)
            )
            db.commit()
            return {"id": task_id, "status": "created"}

        elif path == "/api/tasks/search" and method == "GET":
            q = (body or {}).get("q", "")
            rows = db.execute("SELECT * FROM tasks_fts WHERE tasks_fts MATCH ? ORDER BY rank LIMIT 50", (q,)).fetchall()
            return {"tasks": [dict(r) for r in rows]}

        elif path == "/api/agents" and method == "GET":
            rows = db.execute("SELECT * FROM agents ORDER BY last_heartbeat DESC").fetchall()
            return {"agents": [dict(r) for r in rows]}

        elif path == "/api/agents" and method == "POST":
            data = body or {}
            now = time.time()
            db.execute(
                "INSERT OR REPLACE INTO agents (id, name, role, capabilities, status, last_heartbeat, metadata, registered_at) VALUES (?,?,?,?,?,?,?,?)",
                (data["id"], data.get("name", data["id"]), data.get("role", ""),
                 data.get("capabilities", ""), data.get("status", "online"), now,
                 json.dumps(data.get("metadata", {})), now)
            )
            db.commit()
            return {"id": data["id"], "status": "registered"}

        elif path == "/api/memory" and method == "GET":
            rows = db.execute("SELECT * FROM memory ORDER BY updated_at DESC LIMIT 100").fetchall()
            return {"memory": [dict(r) for r in rows]}

        elif path == "/api/memory" and method == "POST":
            data = body or {}
            mem_id = data.get("id", f"mem-{int(time.time()*1000)}")
            now = time.time()
            db.execute(
                "INSERT OR REPLACE INTO memory (id, namespace, key, value, tags, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                (mem_id, data.get("namespace", "shared"), data["key"],
                 json.dumps(data["value"]) if not isinstance(data["value"], str) else data["value"],
                 data.get("tags", ""), now, now)
            )
            db.commit()
            return {"id": mem_id, "status": "stored"}

        elif path == "/api/events" and method == "GET":
            rows = db.execute("SELECT * FROM events ORDER BY timestamp DESC LIMIT 100").fetchall()
            return {"events": [dict(r) for r in rows]}

        elif path == "/api/events" and method == "POST":
            data = body or {}
            now = time.time()
            db.execute(
                "INSERT INTO events (topic, source, payload, timestamp) VALUES (?,?,?,?)",
                (data.get("topic", "general"), data.get("source", "api"),
                 json.dumps(data.get("payload", {})), now)
            )
            db.commit()
            return {"status": "published"}

        elif path == "/api/logs" and method == "GET":
            rows = db.execute("SELECT * FROM logs ORDER BY timestamp DESC LIMIT 100").fetchall()
            return {"logs": [dict(r) for r in rows]}

        elif path == "/api/logs" and method == "POST":
            data = body or {}
            now = time.time()
            db.execute(
                "INSERT INTO logs (agent_id, level, source, message, metadata, timestamp) VALUES (?,?,?,?,?,?)",
                (data.get("agent_id", "api"), data.get("level", "info"),
                 data.get("source", ""), data.get("message", ""),
                 json.dumps(data.get("metadata", {})), now)
            )
            db.commit()
            return {"status": "logged"}

        elif path == "/api/stats":
            tasks = db.execute("SELECT status, COUNT(*) as cnt FROM tasks GROUP BY status").fetchall()
            agents = db.execute("SELECT status, COUNT(*) as cnt FROM agents GROUP BY status").fetchall()
            memory = db.execute("SELECT COUNT(*) as cnt FROM memory").fetchone()
            events = db.execute("SELECT COUNT(*) as cnt FROM events").fetchone()
            logs = db.execute("SELECT COUNT(*) as cnt FROM logs").fetchone()
            return {
                "tasks": {r["status"]: r["cnt"] for r in tasks},
                "agents": {r["status"]: r["cnt"] for r in agents},
                "memory_count": memory["cnt"],
                "event_count": events["cnt"],
                "log_count": logs["cnt"],
            }

        else:
            return {"error": "not found", "path": path}

    finally:
        db.close()


# ── MCP over SSE ─────────────────────────────────────────────────────────

async def handle_mcp_sse(reader, writer):
    """Handle MCP SSE transport connection."""
    logger.info("MCP SSE client connected")
    # This is handled by the Starlette SSE endpoint below
    pass


# ── HTTP Server (Starlette) ─────────────────────────────────────────────

def create_app():
    """Create the Starlette ASGI app."""
    try:
        from starlette.applications import Starlette
        from starlette.routing import Route, Mount
        from starlette.requests import Request
        from starlette.responses import JSONResponse, StreamingResponse
        from starlette.middleware import Middleware
        from starlette.middleware.cors import CORSMiddleware
    except ImportError:
        logger.error("starlette not installed: pip install starlette sse-starlette")
        sys.exit(1)

    async def health(request: Request):
        return JSONResponse({"status": "ok", "service": "tech-duinn-mcp-bridge"})

    async def rest_api(request: Request):
        """Generic REST API handler."""
        path = request.url.path
        # Public endpoints (no auth required)
        if path in ("/api/health", "/api/stats"):
            pass
        elif not check_auth(dict(request.headers)):
            return JSONResponse({"error": "unauthorized"}, status_code=401)

        method = request.method
        path = request.url.path

        if method == "GET":
            # Parse query params as body for GET requests
            body = dict(request.query_params)
        else:
            try:
                body = await request.json()
            except Exception:
                body = {}

        result = await handle_rest_request(method, path, body)
        status = 200 if "error" not in result else 404
        return JSONResponse(result, status_code=status)

    async def mcp_sse_endpoint(request: Request):
        """SSE endpoint for MCP clients."""
        if not check_auth(dict(request.headers)):
            return JSONResponse({"error": "unauthorized"}, status_code=401)

        from sse_starlette.sse import EventSourceResponse
        import mcp.types as types

        async def event_generator():
            """Generate SSE events for MCP."""
            # Send endpoint info
            yield {
                "event": "endpoint",
                "data": "/messages"
            }
            # Keep alive
            while True:
                yield {"event": "ping", "data": str(time.time())}
                await asyncio.sleep(30)

        return EventSourceResponse(event_generator())

    async def mcp_messages_endpoint(request: Request):
        """Handle MCP JSON-RPC messages."""
        if not check_auth(dict(request.headers)):
            return JSONResponse({"error": "unauthorized"}, status_code=401)

        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid json"}, status_code=400)

        # Handle MCP JSON-RPC
        method = body.get("method", "")
        params = body.get("params", {})
        msg_id = body.get("id")

        db = get_db()
        try:
            if method == "initialize":
                return JSONResponse({
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"tools": {"listChanged": False}},
                        "serverInfo": {"name": "tech-duinn-bridge", "version": "1.0.0"}
                    }
                })

            elif method == "tools/list":
                tools = [
                    {
                        "name": "manage_tasks",
                        "description": "Create, update, search, and manage tasks in the Tech Duinn swarm",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "action": {"type": "string", "enum": ["create", "list", "get", "update", "search", "delete"]},
                                "id": {"type": "string"},
                                "title": {"type": "string"},
                                "description": {"type": "string"},
                                "status": {"type": "string"},
                                "priority": {"type": "integer"},
                                "assigned_to": {"type": "string"},
                                "tags": {"type": "string"},
                                "query": {"type": "string"}
                            },
                            "required": ["action"]
                        }
                    },
                    {
                        "name": "manage_swarm_agents",
                        "description": "Register, list, and manage agents in the Tech Duinn swarm",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "action": {"type": "string", "enum": ["register", "list", "heartbeat", "delete"]},
                                "id": {"type": "string"},
                                "name": {"type": "string"},
                                "role": {"type": "string"},
                                "capabilities": {"type": "string"}
                            },
                            "required": ["action"]
                        }
                    },
                    {
                        "name": "manage_swarm_memory",
                        "description": "Store, retrieve, and search shared memory across agents",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "action": {"type": "string", "enum": ["set", "get", "list", "search", "delete"]},
                                "key": {"type": "string"},
                                "value": {"type": "string"},
                                "namespace": {"type": "string"},
                                "query": {"type": "string"}
                            },
                            "required": ["action"]
                        }
                    },
                    {
                        "name": "manage_swarm_events",
                        "description": "Publish and list events on the Tech Duinn event bus",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "action": {"type": "string", "enum": ["publish", "list", "clear"]},
                                "topic": {"type": "string"},
                                "source": {"type": "string"},
                                "payload": {"type": "object"}
                            },
                            "required": ["action"]
                        }
                    },
                    {
                        "name": "manage_swarm_logs",
                        "description": "Write and search agent logs",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "action": {"type": "string", "enum": ["write", "list", "search", "clear"]},
                                "agent_id": {"type": "string"},
                                "level": {"type": "string"},
                                "message": {"type": "string"},
                                "query": {"type": "string"}
                            },
                            "required": ["action"]
                        }
                    }
                ]
                return JSONResponse({"jsonrpc": "2.0", "id": msg_id, "result": {"tools": tools}})

            elif method == "tools/call":
                tool_name = params.get("name", "")
                tool_args = params.get("arguments", {})
                result = await _handle_tool_call(db, tool_name, tool_args)
                return JSONResponse({
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]}
                })

            else:
                return JSONResponse({
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {"code": -32601, "message": f"Method not found: {method}"}
                })

        finally:
            db.close()

    async def _handle_tool_call(db, tool_name: str, args: dict) -> dict:
        """Handle MCP tool calls."""
        now = time.time()

        if tool_name == "manage_tasks":
            action = args.get("action", "list")
            if action == "list":
                rows = db.execute("SELECT * FROM tasks ORDER BY priority DESC, created_at DESC LIMIT 100").fetchall()
                return {"tasks": [dict(r) for r in rows]}
            elif action == "create":
                task_id = args.get("id", f"task-{int(now*1000)}")
                db.execute(
                    "INSERT OR REPLACE INTO tasks (id,title,description,status,priority,assigned_to,created_by,tags,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (task_id, args.get("title",""), args.get("description",""), args.get("status","pending"),
                     args.get("priority",5), args.get("assigned_to"), args.get("created_by","mcp"),
                     args.get("tags",""), now, now))
                db.commit()
                return {"id": task_id, "status": "created"}
            elif action == "get":
                row = db.execute("SELECT * FROM tasks WHERE id=?", (args["id"],)).fetchone()
                return dict(row) if row else {"error": "not found"}
            elif action == "update":
                sets, vals = [], []
                for k in ("title","description","status","priority","assigned_to","tags","result"):
                    if k in args:
                        sets.append(f"{k}=?")
                        vals.append(args[k])
                sets.append("updated_at=?")
                vals.append(now)
                vals.append(args["id"])
                db.execute(f"UPDATE tasks SET {','.join(sets)} WHERE id=?", vals)
                db.commit()
                return {"id": args["id"], "status": "updated"}
            elif action == "search":
                rows = db.execute("SELECT * FROM tasks_fts WHERE tasks_fts MATCH ? ORDER BY rank LIMIT 50", (args.get("query",""),)).fetchall()
                return {"tasks": [dict(r) for r in rows]}
            elif action == "delete":
                db.execute("DELETE FROM tasks WHERE id=?", (args["id"],))
                db.commit()
                return {"id": args["id"], "status": "deleted"}

        elif tool_name == "manage_swarm_agents":
            action = args.get("action", "list")
            if action == "list":
                rows = db.execute("SELECT * FROM agents ORDER BY last_heartbeat DESC").fetchall()
                return {"agents": [dict(r) for r in rows]}
            elif action == "register":
                db.execute(
                    "INSERT OR REPLACE INTO agents (id,name,role,capabilities,status,last_heartbeat,metadata,registered_at) VALUES (?,?,?,?,?,?,?,?)",
                    (args["id"], args.get("name",args["id"]), args.get("role",""),
                     args.get("capabilities",""), "online", now, "{}", now))
                db.commit()
                return {"id": args["id"], "status": "registered"}
            elif action == "heartbeat":
                db.execute("UPDATE agents SET last_heartbeat=?, status='online' WHERE id=?", (now, args["id"]))
                db.commit()
                return {"id": args["id"], "status": "alive"}
            elif action == "delete":
                db.execute("DELETE FROM agents WHERE id=?", (args["id"],))
                db.commit()
                return {"id": args["id"], "status": "deleted"}

        elif tool_name == "manage_swarm_memory":
            action = args.get("action", "list")
            if action == "list":
                ns = args.get("namespace", "shared")
                rows = db.execute("SELECT * FROM memory WHERE namespace=? ORDER BY updated_at DESC LIMIT 100", (ns,)).fetchall()
                return {"memory": [dict(r) for r in rows]}
            elif action == "set":
                mem_id = f"mem-{int(now*1000)}"
                db.execute(
                    "INSERT OR REPLACE INTO memory (id,namespace,key,value,tags,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
                    (mem_id, args.get("namespace","shared"), args["key"],
                     json.dumps(args["value"]) if not isinstance(args["value"],str) else args["value"],
                     args.get("tags",""), now, now))
                db.commit()
                return {"id": mem_id, "status": "stored"}
            elif action == "get":
                row = db.execute("SELECT * FROM memory WHERE key=? ORDER BY updated_at DESC LIMIT 1", (args["key"],)).fetchone()
                return dict(row) if row else {"error": "not found"}
            elif action == "search":
                rows = db.execute("SELECT * FROM memory WHERE key LIKE ? OR value LIKE ? ORDER BY updated_at DESC LIMIT 50",
                    (f"%{args['query']}%", f"%{args['query']}%")).fetchall()
                return {"memory": [dict(r) for r in rows]}
            elif action == "delete":
                db.execute("DELETE FROM memory WHERE key=?", (args["key"],))
                db.commit()
                return {"key": args["key"], "status": "deleted"}

        elif tool_name == "manage_swarm_events":
            action = args.get("action", "list")
            if action == "list":
                rows = db.execute("SELECT * FROM events ORDER BY timestamp DESC LIMIT 100").fetchall()
                return {"events": [dict(r) for r in rows]}
            elif action == "publish":
                db.execute(
                    "INSERT INTO events (topic,source,payload,timestamp) VALUES (?,?,?,?)",
                    (args.get("topic","general"), args.get("source","mcp"),
                     json.dumps(args.get("payload",{})), now))
                db.commit()
                return {"status": "published"}
            elif action == "clear":
                db.execute("DELETE FROM events")
                db.commit()
                return {"status": "cleared"}

        elif tool_name == "manage_swarm_logs":
            action = args.get("action", "list")
            if action == "list":
                rows = db.execute("SELECT * FROM logs ORDER BY timestamp DESC LIMIT 100").fetchall()
                return {"logs": [dict(r) for r in rows]}
            elif action == "write":
                db.execute(
                    "INSERT INTO logs (agent_id,level,source,message,metadata,timestamp) VALUES (?,?,?,?,?,?)",
                    (args.get("agent_id","mcp"), args.get("level","info"),
                     args.get("source",""), args.get("message",""),
                     json.dumps(args.get("metadata",{})), now))
                db.commit()
                return {"status": "logged"}
            elif action == "search":
                rows = db.execute("SELECT * FROM logs WHERE message LIKE ? ORDER BY timestamp DESC LIMIT 50",
                    (f"%{args['query']}%",)).fetchall()
                return {"logs": [dict(r) for r in rows]}
            elif action == "clear":
                db.execute("DELETE FROM logs")
                db.commit()
                return {"status": "cleared"}

        return {"error": f"Unknown tool: {tool_name}"}

    # Routes
    routes = [
        Route("/health", health, methods=["GET"]),
        Route("/sse", mcp_sse_endpoint, methods=["GET"]),
        Route("/messages", mcp_messages_endpoint, methods=["POST"]),
        # REST fallback
        Route("/api/health", rest_api, methods=["GET"]),
        Route("/api/tasks", rest_api, methods=["GET", "POST"]),
        Route("/api/tasks/search", rest_api, methods=["GET"]),
        Route("/api/agents", rest_api, methods=["GET", "POST"]),
        Route("/api/memory", rest_api, methods=["GET", "POST"]),
        Route("/api/events", rest_api, methods=["GET", "POST"]),
        Route("/api/logs", rest_api, methods=["GET", "POST"]),
        Route("/api/stats", rest_api, methods=["GET"]),
    ]

    middleware = [
        Middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
    ]

    return Starlette(routes=routes, middleware=middleware)


# ── Main ─────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Tech Duinn MCP Bridge")
    parser.add_argument("--port", type=int, default=int(os.getenv("MCP_BRIDGE_PORT", "8378")))
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--token", default=os.getenv("MCP_BRIDGE_TOKEN", ""))
    args = parser.parse_args()

    global AUTH_TOKEN
    if args.token:
        AUTH_TOKEN = args.token

    logger.info(f"Starting Tech Duinn MCP Bridge on {args.host}:{args.port}")
    logger.info(f"Database: {DB_PATH}")
    logger.info(f"Auth: {'enabled' if AUTH_TOKEN else 'disabled (open)'}")
    logger.info("")
    logger.info("MCP endpoints:")
    logger.info(f"  SSE:       http://{args.host}:{args.port}/sse")
    logger.info(f"  Messages:  http://{args.host}:{args.port}/messages")
    logger.info("")
    logger.info("REST endpoints:")
    logger.info(f"  Health:    http://{args.host}:{args.port}/api/health")
    logger.info(f"  Tasks:     http://{args.host}:{args.port}/api/tasks")
    logger.info(f"  Agents:    http://{args.host}:{args.port}/api/agents")
    logger.info(f"  Memory:    http://{args.host}:{args.port}/api/memory")
    logger.info(f"  Events:    http://{args.host}:{args.port}/api/events")
    logger.info(f"  Logs:      http://{args.host}:{args.port}/api/logs")
    logger.info(f"  Stats:     http://{args.host}:{args.port}/api/stats")

    import uvicorn
    app = create_app()
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
