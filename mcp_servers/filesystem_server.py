"""
filesystem_server.py

MCP server exposing filesystem operations (list, read, write, delete, move,
copy, mkdir, search). Sandboxed to a configurable root directory.
"""

import asyncio
import os
import shutil
import sys
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

server = Server("filesystem")

_initialized = False
_root_dir = None


def _ensure_init():
    """Lazy-init: determine sandbox root directory."""
    global _root_dir, _initialized
    if _initialized:
        return
    _initialized = True
    # Default sandbox: /app/data/files inside Docker, ~/odysseus-data/files on host
    _root_dir = os.environ.get("FILESYSTEM_ROOT", "")
    if not _root_dir:
        data_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data", "files",
        )
        _root_dir = data_dir
    os.makedirs(_root_dir, exist_ok=True)


def _safe_path(requested: str) -> str:
    """Resolve and validate that the path stays within the sandbox root."""
    resolved = os.path.realpath(os.path.join(_root_dir, requested))
    if not resolved.startswith(os.path.realpath(_root_dir)):
        raise ValueError(f"Path escapes sandbox root: {requested}")
    return resolved


def _truncate(text: str, limit: int = 8000) -> str:
    if len(text) > limit:
        return text[:limit] + f"\n... (truncated, {len(text)} chars total)"
    return text


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="manage_files",
            description=(
                "Manage files in the Odysseus data directory. "
                "Operations: list, read, write, delete, move, copy, mkdir, search, stat. "
                "All paths are relative to the sandbox root (data/files/)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["list", "read", "write", "delete", "move", "copy", "mkdir", "search", "stat"],
                        "description": "The action to perform",
                    },
                    "path": {
                        "type": "string",
                        "description": "File/directory path relative to sandbox root (e.g. 'notes/todo.md')",
                    },
                    "content": {
                        "type": "string",
                        "description": "File content (for write action)",
                    },
                    "destination": {
                        "type": "string",
                        "description": "Destination path (for move/copy actions)",
                    },
                    "pattern": {
                        "type": "string",
                        "description": "Search pattern (for search action, matches filenames)",
                    },
                    "recursive": {
                        "type": "boolean",
                        "description": "List directories recursively (for list action, default false)",
                    },
                },
                "required": ["action"],
            },
        )
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name != "manage_files":
        return [TextContent(type="text", text=f"Unknown tool: {name}")]

    _ensure_init()
    action = arguments.get("action", "")
    rel_path = arguments.get("path", "")

    try:
        if action == "list":
            target = _safe_path(rel_path) if rel_path else _root_dir
            if not os.path.isdir(target):
                return [TextContent(type="text", text=f"Error: '{rel_path}' is not a directory")]
            recursive = arguments.get("recursive", False)
            lines = [f"Contents of {rel_path or '/'}:\n"]
            if recursive:
                for root, dirs, files in os.walk(target):
                    rel = os.path.relpath(root, _root_dir)
                    depth = rel.count(os.sep)
                    for d in sorted(dirs):
                        lines.append(f"{'  ' * depth}📁 {d}/")
                    for f in sorted(files):
                        size = os.path.getsize(os.path.join(root, f))
                        lines.append(f"{'  ' * depth}📄 {f} ({_human_size(size)})")
            else:
                entries = sorted(os.listdir(target))
                for entry in entries:
                    full = os.path.join(target, entry)
                    if os.path.isdir(full):
                        lines.append(f"📁 {entry}/")
                    else:
                        size = os.path.getsize(full)
                        lines.append(f"📄 {entry} ({_human_size(size)})")
            if len(lines) == 1:
                return [TextContent(type="text", text=f"Directory '{rel_path}' is empty.")]
            return [TextContent(type="text", text="\n".join(lines))]

        elif action == "read":
            if not rel_path:
                return [TextContent(type="text", text="Error: path is required")]
            target = _safe_path(rel_path)
            if not os.path.isfile(target):
                return [TextContent(type="text", text=f"Error: File not found: {rel_path}")]
            size = os.path.getsize(target)
            if size > 500_000:  # 500KB limit
                return [TextContent(type="text", text=f"Error: File too large ({_human_size(size)}). Max 500KB.")]
            with open(target, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            return [TextContent(type="text", text=_truncate(content))]

        elif action == "write":
            if not rel_path:
                return [TextContent(type="text", text="Error: path is required")]
            content = arguments.get("content", "")
            if content is None:
                return [TextContent(type="text", text="Error: content is required for write")]
            target = _safe_path(rel_path)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "w", encoding="utf-8") as f:
                f.write(content)
            return [TextContent(type="text", text=f"Wrote {len(content)} bytes to {rel_path}")]

        elif action == "delete":
            if not rel_path:
                return [TextContent(type="text", text="Error: path is required")]
            target = _safe_path(rel_path)
            if not os.path.exists(target):
                return [TextContent(type="text", text=f"Error: Path not found: {rel_path}")]
            if os.path.isdir(target):
                shutil.rmtree(target)
                return [TextContent(type="text", text=f"Deleted directory: {rel_path}")]
            else:
                os.remove(target)
                return [TextContent(type="text", text=f"Deleted file: {rel_path}")]

        elif action == "move":
            if not rel_path or not arguments.get("destination"):
                return [TextContent(type="text", text="Error: path and destination are required")]
            src = _safe_path(rel_path)
            dst = _safe_path(arguments["destination"])
            if not os.path.exists(src):
                return [TextContent(type="text", text=f"Error: Source not found: {rel_path}")]
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.move(src, dst)
            return [TextContent(type="text", text=f"Moved {rel_path} → {arguments['destination']}")]

        elif action == "copy":
            if not rel_path or not arguments.get("destination"):
                return [TextContent(type="text", text="Error: path and destination are required")]
            src = _safe_path(rel_path)
            dst = _safe_path(arguments["destination"])
            if not os.path.exists(src):
                return [TextContent(type="text", text=f"Error: Source not found: {rel_path}")]
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            if os.path.isdir(src):
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)
            return [TextContent(type="text", text=f"Copied {rel_path} → {arguments['destination']}")]

        elif action == "mkdir":
            if not rel_path:
                return [TextContent(type="text", text="Error: path is required")]
            target = _safe_path(rel_path)
            os.makedirs(target, exist_ok=True)
            return [TextContent(type="text", text=f"Created directory: {rel_path}")]

        elif action == "search":
            pattern = arguments.get("pattern", "")
            if not pattern:
                return [TextContent(type="text", text="Error: pattern is required")]
            matches = []
            pattern_lower = pattern.lower()
            for root, dirs, files in os.walk(_root_dir):
                for f in files:
                    if pattern_lower in f.lower():
                        full = os.path.join(root, f)
                        rel = os.path.relpath(full, _root_dir)
                        size = os.path.getsize(full)
                        matches.append(f"- {rel} ({_human_size(size)})")
                        if len(matches) >= 50:
                            break
                if len(matches) >= 50:
                    break
            if not matches:
                return [TextContent(type="text", text=f"No files matching '{pattern}' found.")]
            return [TextContent(type="text", text=f"Search results for '{pattern}' ({len(matches)}):\n" + "\n".join(matches))]

        elif action == "stat":
            if not rel_path:
                return [TextContent(type="text", text="Error: path is required")]
            target = _safe_path(rel_path)
            if not os.path.exists(target):
                return [TextContent(type="text", text=f"Error: Path not found: {rel_path}")]
            st = os.stat(target)
            import time
            info = (
                f"Path: {rel_path}\n"
                f"Type: {'directory' if os.path.isdir(target) else 'file'}\n"
                f"Size: {_human_size(st.st_size)}\n"
                f"Modified: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(st.st_mtime))}\n"
                f"Created: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(st.st_ctime))}\n"
                f"Permissions: {oct(st.st_mode)[-3:]}"
            )
            return [TextContent(type="text", text=info)]

        else:
            return [TextContent(type="text", text=f"Error: Unknown action '{action}'. Use: list, read, write, delete, move, copy, mkdir, search, stat")]

    except ValueError as e:
        return [TextContent(type="text", text=f"Security error: {e}")]
    except Exception as e:
        return [TextContent(type="text", text=f"Error: {type(e).__name__}: {e}")]


def _human_size(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.0f}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}TB"


async def run():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(run())
