#!/usr/bin/env python3
"""
CLI wrapper for the Raphael skill + memory administrator.
Usage: python3 raphael.py <JSON-args>

Skill actions:
  python3 raphael.py '{"action":"audit"}'
  python3 raphael.py '{"action":"evolve_all"}'
  python3 raphael.py '{"action":"evolve","target":"open-windows-app"}'
  python3 raphael.py '{"action":"merge","skills":["a","b"],"target":"c","category":"cat"}'
  python3 raphael.py '{"action":"delete","target":"slug"}'
  python3 raphael.py '{"action":"absorb","content":"...","category":"cat"}'

Memory actions:
  python3 raphael.py '{"action":"memory_audit"}'
  python3 raphael.py '{"action":"memory_list"}'
  python3 raphael.py '{"action":"memory_pin","id":"abc12345"}'
  python3 raphael.py '{"action":"memory_delete","id":"abc12345"}'
  python3 raphael.py '{"action":"memory_add","text":"User prefers X","category":"preference"}'
  python3 raphael.py '{"action":"memory_evolve","id":"abc12345"}'
  python3 raphael.py '{"action":"memory_evolve_all"}'

Owner: pass the data owner via the ODYSSEUS_OWNER environment variable. When
unset, raphael operates across all owners (single-user installs can leave it
unset).
"""
import asyncio
import json
import sys
import os

# Run from the app root so `src` / `services` imports resolve regardless of cwd.
APP_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP_ROOT)
os.chdir(APP_ROOT)


async def main():
    raw = " ".join(sys.argv[1:]).strip() if len(sys.argv) > 1 else '{"action":"audit"}'
    if not raw.startswith("{"):
        raw = json.dumps({"action": raw})

    from src.ai_interaction import do_raphael
    owner = os.environ.get("ODYSSEUS_OWNER") or None
    result = await do_raphael(raw, owner=owner)

    output = result.get("output") or result.get("error") or json.dumps(result)
    print(output)
    sys.exit(result.get("exit_code", 0))


asyncio.run(main())
