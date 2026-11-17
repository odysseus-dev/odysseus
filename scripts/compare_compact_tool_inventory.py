"""Read-only schema ablation on the served model; generated calls are never executed.

This isolates inventory size, not full harness performance or blind accuracy.
"""
import os
import concurrent.futures
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx
from src.agent_loop import _compact_openai_tool_schema
from src.tool_schemas import FUNCTION_TOOL_SCHEMAS
from src.turn_contract import FAMILY_TOOLS

CASES = [
    ("notes", "Show my noes", "manage_notes"),
    ("calendar", "What is on my caledar?", "manage_calendar"),
    ("tasks", "List my scheduled tasks", "manage_tasks"),
    ("skills", "Show my skills", "manage_skills"),
    ("memory", "Remember that I prefer short answers", "manage_memory"),
    ("documents", "List my documents", "manage_documents"),
    ("email", "Show my connected email accounts", "list_email_accounts"),
    ("search_browser", "Search the web for PostgreSQL transaction isolation documentation", "web_search"),
    ("shell_files", "Use bash to run pwd", "bash"),
    ("cookbook_admin", "List configured Cookbook servers", "list_cookbook_servers"),
]
FAMILIES = {row[0] for row in CASES}


def run(job):
    profile, (family, prompt, expected) = job
    names = set().union(*(FAMILY_TOOLS[f] for f in (FAMILIES if profile == "all" else {family})))
    schemas = [_compact_openai_tool_schema(s) for s in FUNCTION_TOOL_SCHEMAS
               if s["function"]["name"] in names]
    start = time.monotonic()
    try:
        response = httpx.post(
            os.environ["ENDPOINT_URL"],
            json={"model": "odysseus-qwen3.5-tools-pre-heretic", "temperature": 0,
                  "max_tokens": 256, "chat_template_kwargs": {"enable_thinking": False},
                  "messages": [{"role": "system", "content": "You are Odysseus. Use the available tools to fulfill the request. Answer normally when no tool is needed."},
                               {"role": "user", "content": prompt}], "tools": schemas},
            timeout=90,
        )
        response.raise_for_status()
        data = response.json()
        message = data["choices"][0]["message"]
        called = [c["function"]["name"] for c in message.get("tool_calls") or []]
        return {"profile": profile, "family": family, "prompt": prompt,
                "schemas": len(schemas), "called": called, "expected": expected,
                "routing_pass": expected in called, "message": message,
                "usage": data.get("usage"), "seconds": round(time.monotonic()-start, 2)}
    except Exception as exc:
        return {"profile": profile, "family": family, "error": str(exc)}


if __name__ == "__main__":
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        rows = list(pool.map(run, [(profile, case) for profile in ("family", "all") for case in CASES]))
    print(json.dumps(rows, ensure_ascii=False, indent=2))
