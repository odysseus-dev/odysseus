"""
memory_server.py

MCP server exposing memory management (list, add, edit, delete, search).
Imports MemoryManager and MemoryVectorStore from the Odysseus codebase.
"""

import asyncio
import sys
import time
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

server = Server("memory")

# Late-initialized managers (set during first tool call)
_memory_manager = None
_memory_vector = None
_initialized = False


def _ensure_init():
    """Lazy-init memory managers on first use."""
    global _memory_manager, _memory_vector, _initialized
    if _initialized:
        return
    _initialized = True

    from src.constants import DATA_DIR
    from src.memory import MemoryManager
    _memory_manager = MemoryManager(DATA_DIR)

    try:
        from src.memory_vector import MemoryVectorStore
        _memory_vector = MemoryVectorStore(DATA_DIR)
        if not _memory_vector.healthy:
            _memory_vector = None
    except Exception:
        _memory_vector = None


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="manage_memory",
            description="Manage the user's memory system: list, add, edit, delete, or search memories.",
            inputSchema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["list", "add", "edit", "delete", "search"],
                        "description": "The action to perform",
                    },
                    "text": {"type": "string", "description": "Memory text (add/edit) or search query (search)"},
                    "memory_id": {"type": "string", "description": "Memory ID (edit/delete)"},
                    "category": {
                        "type": "string",
                        "enum": ["fact", "event", "contact", "preference"],
                        "description": "Memory category (add/list filter)",
                    },
                },
                "required": ["action"],
            },
        )
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name != "manage_memory":
        return [TextContent(type="text", text=f"Unknown tool: {name}")]

    _ensure_init()
    if not _memory_manager:
        return [TextContent(type="text", text="Error: Memory manager not available")]

    action = arguments.get("action", "")

    if action == "list":
        category_filter = arguments.get("category", "")
        memories = _memory_manager.load()
        if category_filter:
            memories = [m for m in memories if m.get("category", "").lower() == category_filter.lower()]
        if not memories:
            msg = "No memories found"
            if category_filter:
                msg += f" in category '{category_filter}'"
            return [TextContent(type="text", text=msg + ".")]

        lines = [f"Found {len(memories)} memory entries:\n"]
        for m in memories:
            cat = m.get("category", "fact")
            mid = m.get("id", "?")[:8]
            text = m.get("text", "")
            if len(text) > 150:
                text = text[:150] + "..."
            lines.append(f"- [{cat}] `{mid}` — {text}")
        return [TextContent(type="text", text="\n".join(lines))]

    elif action == "add":
        text = arguments.get("text", "")
        category = arguments.get("category", "fact")
        if not text:
            return [TextContent(type="text", text="Error: Memory text cannot be empty")]
        entry = _memory_manager.add_entry(text, source="ai_agent", category=category)
        memories = _memory_manager.load_all()
        memories.append(entry)
        _memory_manager.save(memories)
        if _memory_vector and _memory_vector.healthy:
            try:
                _memory_vector.add(entry["id"], text)
            except Exception:
                pass
        return [TextContent(type="text", text=f"Memory added: [{category}] {text} (id: {entry['id'][:8]})")]

    elif action == "edit":
        memory_id = arguments.get("memory_id", "")
        new_text = arguments.get("text", "")
        if not memory_id or not new_text:
            return [TextContent(type="text", text="Error: edit needs memory_id and text")]
        memories = _memory_manager.load_all()
        found = False
        full_id = None
        for m in memories:
            if m.get("id", "").startswith(memory_id):
                m["text"] = new_text
                m["timestamp"] = int(time.time())
                found = True
                full_id = m["id"]
                break
        if not found:
            return [TextContent(type="text", text=f"Error: Memory '{memory_id}' not found")]
        _memory_manager.save(memories)
        if _memory_vector and _memory_vector.healthy and full_id:
            try:
                _memory_vector.remove(full_id)
                _memory_vector.add(full_id, new_text)
            except Exception:
                pass
        return [TextContent(type="text", text=f"Memory updated: {new_text}")]

    elif action == "delete":
        memory_id = arguments.get("memory_id", "")
        if not memory_id:
            return [TextContent(type="text", text="Error: delete needs memory_id")]
        memories = _memory_manager.load_all()
        full_id = None
        deleted_text = ""
        deleted_category = ""
        for m in memories:
            if m.get("id", "").startswith(memory_id):
                full_id = m["id"]
                deleted_text = m.get("text", "")
                deleted_category = m.get("category", "")
                break
        if not full_id:
            return [TextContent(type="text", text=f"Error: Memory '{memory_id}' not found")]
        memories = [m for m in memories if m.get("id") != full_id]
        _memory_manager.save(memories)
        if _memory_vector and _memory_vector.healthy and full_id:
            try:
                _memory_vector.remove(full_id)
            except Exception:
                pass
        cat = f"[{deleted_category}] " if deleted_category else ""
        snippet = deleted_text if len(deleted_text) <= 120 else deleted_text[:117] + "..."
        return [TextContent(type="text", text=f"Memory deleted: {cat}{snippet} (id: {memory_id})")]

    elif action == "search":
        query = arguments.get("text", "")
        if not query:
            return [TextContent(type="text", text="Error: search needs text (query)")]
        memories = _memory_manager.load()
        if hasattr(_memory_manager, 'get_relevant_memories'):
            results = _memory_manager.get_relevant_memories(query, memories, threshold=0.05, max_items=20)
        else:
            query_lower = query.lower()
            results = [m for m in memories if query_lower in m.get("text", "").lower()][:20]
        if not results:
            return [TextContent(type="text", text=f"No memories found matching '{query}'.")]
        lines = [f"Found {len(results)} matching memories:\n"]
        for m in results:
            cat = m.get("category", "fact")
            mid = m.get("id", "?")[:8]
            text = m.get("text", "")
            lines.append(f"- [{cat}] `{mid}` — {text}")
        return [TextContent(type="text", text="\n".join(lines))]

    else:
        return [TextContent(type="text", text=f"Error: Unknown action '{action}'. Use: list, add, edit, delete, search")]


async def run():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(run())
