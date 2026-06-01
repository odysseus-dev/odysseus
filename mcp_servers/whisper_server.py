"""
whisper_server.py

MCP server exposing whisper.cpp speech-to-text.
Transcribes audio files using the whisper-cli binary.
"""

import asyncio
import os
import subprocess
import sys
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

server = Server("whisper")

_initialized = False
_whisper_bin = None
_whisper_model = None


def _ensure_init():
    """Find whisper binary and model."""
    global _whisper_bin, _whisper_model, _initialized
    if _initialized:
        return
    _initialized = True

    # Look for whisper-cli in standard locations
    candidates = [
        "/app/data/bin/whisper-cli",
        "/usr/local/bin/whisper-cli",
        os.path.expanduser("~/.local/bin/whisper-cli"),
    ]
    for c in candidates:
        if os.path.isfile(c) and os.access(c, os.X_OK):
            _whisper_bin = c
            break

    # Look for model
    model_candidates = [
        "/app/data/bin/ggml-base.bin",
        "/app/data/ggml-base.bin",
        os.path.expanduser("~/.cache/whisper/ggml-base.bin"),
        "/app/models/ggml-base.bin",
    ]
    # Also check WHISPER_MODEL env var
    env_model = os.environ.get("WHISPER_MODEL", "")
    if env_model and os.path.isfile(env_model):
        _whisper_model = env_model

    if not _whisper_model:
        for m in model_candidates:
            if os.path.isfile(m):
                _whisper_model = m
                break


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="transcribe_audio",
            description="Transcribe audio/speech to text using whisper.cpp. Supports flac, mp3, ogg, wav formats. Returns timestamped transcript.",
            inputSchema={
                "type": "object",
                "properties": {
                    "audio_path": {
                        "type": "string",
                        "description": "Path to audio file on disk",
                    },
                    "language": {
                        "type": "string",
                        "description": "Language code (e.g. 'en', 'es', 'fr'). Auto-detected if omitted.",
                    },
                    "output_format": {
                        "type": "string",
                        "enum": ["text", "srt", "vtt", "json"],
                        "description": "Output format (default: text)",
                    },
                    "translate": {
                        "type": "boolean",
                        "description": "Translate to English instead of transcribe (default: false)",
                    },
                },
                "required": ["audio_path"],
            },
        ),
        Tool(
            name="list_whisper_models",
            description="List available whisper models and the currently active one.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    _ensure_init()

    if name == "transcribe_audio":
        return await _transcribe(arguments)
    elif name == "list_whisper_models":
        return _list_models()
    return [TextContent(type="text", text=f"Unknown tool: {name}")]


async def _transcribe(args: dict) -> list[TextContent]:
    audio_path = args.get("audio_path", "")
    if not audio_path:
        return [TextContent(type="text", text="Error: audio_path is required")]

    if not os.path.isfile(audio_path):
        return [TextContent(type="text", text=f"Error: File not found: {audio_path}")]

    if not _whisper_bin:
        return [TextContent(type="text", text="Error: whisper-cli binary not found. Install whisper.cpp first.")]

    if not _whisper_model:
        return [TextContent(type="text", text="Error: No whisper model found. Download one with: bash whisper.cpp/models/download-ggml-model.sh base")]

    # Build command
    cmd = [
        _whisper_bin,
        "-m", _whisper_model,
        "-f", audio_path,
        "--no-prints",
        "-t", "4",
    ]

    language = args.get("language", "")
    if language:
        cmd.extend(["-l", language])

    if args.get("translate"):
        cmd.append("--translate")

    output_format = args.get("output_format", "text")
    if output_format == "srt":
        cmd.extend(["-osrt"])
    elif output_format == "vtt":
        cmd.extend(["-ovtt"])
    elif output_format == "json":
        cmd.extend(["-oj"])

    try:
        env = {**os.environ, "LD_LIBRARY_PATH": os.path.dirname(_whisper_bin)}
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)

        output = stdout.decode("utf-8", errors="replace").strip()
        errors = stderr.decode("utf-8", errors="replace").strip()

        if proc.returncode != 0:
            return [TextContent(type="text", text=f"Error (exit {proc.returncode}): {errors[:500]}")]

        if not output:
            return [TextContent(type="text", text="(No speech detected)")]

        # Truncate if too long
        if len(output) > 10000:
            output = output[:10000] + f"\n... (truncated, {len(output)} chars total)"

        return [TextContent(type="text", text=output)]

    except asyncio.TimeoutError:
        return [TextContent(type="text", text="Error: Transcription timed out (5 min limit)")]
    except Exception as e:
        return [TextContent(type="text", text=f"Error: {type(e).__name__}: {e}")]


def _list_models() -> list[TextContent]:
    lines = ["Whisper models:\n"]
    if _whisper_model:
        lines.append(f"  Active: {_whisper_model}")
    else:
        lines.append("  Active: (none)")

    # Scan for available models
    search_dirs = [
        "/app/data/bin",
        "/app/data",
        os.path.expanduser("~/.cache/whisper"),
        "/app/models",
    ]
    found = set()
    for d in search_dirs:
        if os.path.isdir(d):
            for f in os.listdir(d):
                if f.startswith("ggml-") and f.endswith(".bin"):
                    full = os.path.join(d, f)
                    if full not in found:
                        found.add(full)
                        size_mb = os.path.getsize(full) / 1024 / 1024
                        lines.append(f"  - {f} ({size_mb:.0f}MB) @ {d}")

    if not found:
        lines.append("  No models found. Download with: bash whisper.cpp/models/download-ggml-model.sh base")

    return [TextContent(type="text", text="\n".join(lines))]


async def run():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(run())
