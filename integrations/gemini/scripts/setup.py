#!/usr/bin/env python3
"""Generate .gemini/settings.json wired to Odysseus MCP servers.

Run from the Odysseus project root:
    python integrations/gemini/scripts/setup.py

The script locates the project venv and mcp_servers/ directory, then writes
(or merges into) .gemini/settings.json so Gemini CLI can reach the four
built-in Odysseus MCP servers on any machine.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent

MCP_SERVERS_DIR = PROJECT_ROOT / "mcp_servers"

# Candidate venv locations, in priority order.
VENV_CANDIDATES = [
    PROJECT_ROOT / "venv",
    PROJECT_ROOT / ".venv",
]

GEMINI_SETTINGS = PROJECT_ROOT / ".gemini" / "settings.json"

SERVERS = {
    "odysseus-memory": "memory_server.py",
    "odysseus-rag": "rag_server.py",
    "odysseus-email": "email_server.py",
    "odysseus-imagegen": "image_gen_server.py",
}


def _find_venv_python() -> Path:
    for venv in VENV_CANDIDATES:
        for rel in ("Scripts/python.exe", "bin/python"):
            candidate = venv / rel
            if candidate.exists():
                return candidate
    # Fall back to the interpreter running this script.
    return Path(sys.executable)


def _build_server_entry(python: Path, script: Path) -> dict:
    return {
        "command": str(python),
        "args": [str(script)],
    }


def main() -> int:
    python = _find_venv_python()
    print(f"Using Python: {python}")

    if not MCP_SERVERS_DIR.exists():
        print(f"ERROR: mcp_servers/ not found at {MCP_SERVERS_DIR}", file=sys.stderr)
        print("Run this script from the Odysseus project root.", file=sys.stderr)
        return 1

    # Load existing settings if present, preserving any user config.
    GEMINI_SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    if GEMINI_SETTINGS.exists():
        try:
            existing = json.loads(GEMINI_SETTINGS.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = {}
    else:
        existing = {}

    mcp_section = existing.setdefault("mcpServers", {})

    added = []
    for name, script_name in SERVERS.items():
        script = MCP_SERVERS_DIR / script_name
        if not script.exists():
            print(f"  [skip] {script_name} not found", file=sys.stderr)
            continue
        mcp_section[name] = _build_server_entry(python, script)
        added.append(name)

    GEMINI_SETTINGS.write_text(
        json.dumps(existing, indent=2) + "\n",
        encoding="utf-8",
    )

    print(f"\nWrote {GEMINI_SETTINGS}")
    for name in added:
        print(f"  + {name}")

    print("\nVerify with: gemini mcp list")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
