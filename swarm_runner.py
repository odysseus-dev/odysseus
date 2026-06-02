#!/usr/bin/env python3
"""
swarm_runner.py — Tech Duinn Swarm Agent Runner

Runs local AI agents (via Ollama) against the Odysseus codebase.
Each agent pulls tasks from the Tech Duinn task queue, reviews code,
and logs wins/losses/lessons back to the swarm.

Usage:
    python3 swarm_runner.py              # Run all agents
    python3 swarm_runner.py --agent fixer # Run specific agent
    python3 swarm_runner.py --list        # List available agents
"""

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# ── Config ───────────────────────────────────────────────────────────────
DB_PATH = Path(__file__).parent / "data" / "tech_duinn" / "tech-duinn.db"
OLLAMA_URL = os.getenv("OLLAMA_HOST", "http://localhost:11434")
CODEBASE = Path(__file__).parent


def _extract_json(text: str) -> dict | None:
    """Extract JSON from model response, stripping markdown fences."""
    import re
    # Strip markdown code fences
    text = re.sub(r'```(?:json)?\s*', '', text)
    text = re.sub(r'```\s*', '', text)
    text = text.strip()
    # Find JSON object
    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end])
        except Exception:
            pass
    return None

# Mutable config so main() can override MODEL
_cfg = {"model": os.getenv("SWARM_MODEL", "qwen2.5-coder:3b")}

# ── Agent Definitions ────────────────────────────────────────────────────
AGENTS = {
    "code-reviewer": {
        "name": "Code Reviewer",
        "role": "Reviews code for bugs, security issues, and bad patterns",
        "system": """You are a senior code reviewer. Analyze code for:
- Bugs and logic errors
- Security vulnerabilities (SQL injection, XSS, SSRF, path traversal)
- Race conditions and async issues
- Resource leaks (unclosed connections, missing cleanup)
- Error handling gaps

Output format (JSON):
{"findings": [{"file": "path", "line": N, "severity": "critical|high|medium|low", "title": "short title", "description": "what's wrong", "fix": "how to fix"}]}
If no issues: {"findings": []}""",
        "tasks": ["prompt-injection", "dead-code"],
    },
    "test-writer": {
        "name": "Test Writer",
        "role": "Writes pytest tests for untested code",
        "system": """You are a test engineer. Write pytest tests for the given code.
Focus on:
- Happy path tests
- Edge cases (empty input, None, boundary values)
- Error handling (invalid input, missing files)
- Async function testing with pytest-asyncio

Output format (JSON):
{"tests": [{"file": "test_path.py", "name": "test_function_name", "code": "full pytest code", "covers": "what it tests"}]}
If code is already well-tested: {"tests": [], "note": "explanation"}""",
        "tasks": ["test-coverage"],
    },
    "perf-analyst": {
        "name": "Performance Analyst",
        "role": "Finds performance bottlenecks and suggests fixes",
        "system": """You are a performance engineer. Analyze code for:
- Blocking I/O in async contexts (subprocess.run, requests, open())
- N+1 queries and missing database indexes
- Unnecessary object creation in loops
- Missing caching opportunities
- Large payload handling (missing pagination, streaming)
- Memory leaks and unbounded growth

Output format (JSON):
{"issues": [{"file": "path", "line": N, "type": "blocking_io|n_plus_1|memory|caching|payload", "title": "short title", "impact": "high|medium|low", "fix": "specific code change"}]}
If no issues: {"issues": []}""",
        "tasks": ["perf-static", "perf-async"],
    },
    "frontend-auditor": {
        "name": "Frontend Auditor",
        "role": "Reviews JavaScript for UX bugs, accessibility, and performance",
        "system": """You are a frontend specialist. Review JavaScript/HTML/CSS for:
- Missing event handlers (Escape key, click outside)
- Accessibility issues (missing aria labels, keyboard navigation)
- Memory leaks (event listeners not cleaned up)
- XSS risks (innerHTML with user input)
- Performance (layout thrashing, unnecessary DOM queries)

Output format (JSON):
{"issues": [{"file": "path", "line": N, "category": "a11y|ux|security|performance", "title": "short title", "description": "what's wrong", "fix": "how to fix"}]}
If no issues: {"issues": []}""",
        "tasks": ["modal-positioning", "css-cleanup"],
    },
    "doc-checker": {
        "name": "Documentation Checker",
        "role": "Finds undocumented features and broken docs",
        "system": """You are a documentation auditor. Check for:
- API endpoints without documentation
- Environment variables not listed in .env.example
- Docker services not in README
- Broken links and outdated instructions
- Missing error codes and troubleshooting

Output format (JSON):
{"gaps": [{"type": "missing_doc|outdated|broken_link|missing_env", "file": "path", "description": "what's missing or wrong", "suggestion": "what to add"}]}
If docs are complete: {"gaps": []}""",
        "tasks": ["troubleshooting"],
    },
}


# ── Ollama Client ────────────────────────────────────────────────────────
def ollama_generate(prompt: str, system: str = "", timeout: int = 120) -> str:
    """Call Ollama generate API."""
    import urllib.request
    import urllib.error

    payload = json.dumps({
        "model": _cfg["model"],
        "prompt": prompt,
        "system": system,
        "stream": False,
        "options": {
            "temperature": 0.1,
            "num_predict": 2048,
            "num_ctx": 8192,
        },
    }).encode()
    try:
        req = urllib.request.Request(
            f"{OLLAMA_URL}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())["response"]
    except urllib.error.URLError:
        print(f"  ✗ Cannot connect to Ollama at {OLLAMA_URL}")
        print(f"    Start it with: ollama serve")
        sys.exit(1)
    except Exception as e:
        print(f"  ✗ Ollama error: {e}")
        return ""


# ── Swarm DB Helpers ─────────────────────────────────────────────────────
def get_db():
    """Get a connection to the Tech Duinn swarm DB."""
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    return db


def register_agent(db, agent_id: str, name: str, role: str):
    """Register or update an agent in the swarm."""
    now = time.time()
    db.execute(
        "INSERT OR REPLACE INTO agents (id, name, status, capabilities, last_heartbeat, registered_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (agent_id, name, "active", role, now, now),
    )
    db.commit()


def claim_task(db, agent_id: str, task_id: str) -> bool:
    """Claim a task for an agent."""
    row = db.execute("SELECT status FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if not row or row["status"] not in ("pending", "in_progress"):
        return False
    now = time.time()
    db.execute(
        "UPDATE tasks SET status = 'in_progress', assigned_to = ?, updated_at = ? WHERE id = ?",
        (agent_id, now, task_id),
    )
    db.commit()
    return True


def complete_task(db, task_id: str, result: str):
    """Mark a task as done."""
    now = time.time()
    db.execute(
        "UPDATE tasks SET status = 'done', result = ?, updated_at = ?, completed_at = ? WHERE id = ?",
        (result, now, now, task_id),
    )
    db.commit()


def fail_task(db, task_id: str, reason: str):
    """Mark a task as failed."""
    now = time.time()
    db.execute(
        "UPDATE tasks SET status = 'failed', result = ?, updated_at = ? WHERE id = ?",
        (reason, now, task_id),
    )
    db.commit()


def log_memory(db, namespace: str, key: str, value: dict):
    """Log to shared memory."""
    now = time.time()
    mem_id = f"mem-{int(now * 1000)}-{key}"
    db.execute(
        "INSERT OR REPLACE INTO memory (id, namespace, key, value, tags, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (mem_id, namespace, key, json.dumps(value), namespace, now, now),
    )
    db.commit()


def log_event(db, topic: str, source: str, payload: dict):
    """Publish an event to the swarm bus."""
    now = time.time()
    db.execute(
        "INSERT INTO events (topic, source, payload, timestamp) VALUES (?, ?, ?, ?)",
        (topic, source, json.dumps(payload), now),
    )
    db.commit()


def log_agent_log(db, agent_id: str, task_id: str, level: str, message: str):
    """Write an agent log entry."""
    now = time.time()
    db.execute(
        "INSERT INTO logs (agent_id, level, source, message, metadata, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
        (agent_id, level, task_id, message, json.dumps({"task_id": task_id}), now),
    )
    db.commit()


# ── Code Collection ──────────────────────────────────────────────────────
def collect_python_files(max_files: int = 20) -> list[dict]:
    """Collect Python source files for review."""
    files = []
    for p in sorted(CODEBASE.glob("src/*.py")):
        if p.stat().st_size > 0:
            files.append({"path": str(p.relative_to(CODEBASE)), "content": p.read_text(errors="replace")[:8000]})
    for p in sorted(CODEBASE.glob("routes/*.py")):
        if p.stat().st_size > 0:
            files.append({"path": str(p.relative_to(CODEBASE)), "content": p.read_text(errors="replace")[:8000]})
    return files[:max_files]


def collect_js_files(max_files: int = 15) -> list[dict]:
    """Collect JavaScript source files for review."""
    files = []
    for p in sorted((CODEBASE / "static" / "js").glob("*.js")):
        if p.stat().st_size > 0:
            files.append({"path": str(p.relative_to(CODEBASE)), "content": p.read_text(errors="replace")[:8000]})
    return files[:max_files]


def collect_test_files(max_files: int = 10) -> list[dict]:
    """Collect test files to find coverage gaps."""
    files = []
    for p in sorted(CODEBASE.glob("tests/*.py")):
        if p.stat().st_size > 0 and p.name != "__init__.py":
            files.append({"path": str(p.relative_to(CODEBASE)), "content": p.read_text(errors="replace")[:6000]})
    return files[:max_files]


def collect_route_files(max_files: int = 10) -> list[dict]:
    """Collect route files for API doc checking."""
    files = []
    for p in sorted(CODEBASE.glob("routes/*.py")):
        if p.stat().st_size > 0:
            files.append({"path": str(p.relative_to(CODEBASE)), "content": p.read_text(errors="replace")[:8000]})
    return files[:max_files]


# ── Agent Runners ────────────────────────────────────────────────────────
def run_code_reviewer(db, agent_id: str, task_id: str):
    """Run the code reviewer agent on Python files."""
    files = collect_python_files(15)
    if not files:
        return "No Python files found"

    all_findings = []
    for f in files:
        prompt = f"""Review this file for bugs and security issues:

File: {f['path']}
```python
{f['content']}
```

IMPORTANT: Output ONLY raw JSON. No markdown fences, no explanations, no code blocks."""
        response = ollama_generate(prompt, AGENTS["code-reviewer"]["system"])
        try:
            data = _extract_json(response)
            if data:
                findings = data.get("findings", [])
                for finding in findings:
                    finding["file"] = f["path"]
                    all_findings.append(finding)
        except Exception:
            log_agent_log(db, agent_id, task_id, "warn", f"Failed to parse JSON for {f['path']}")

    result = json.dumps({"total_findings": len(all_findings), "findings": all_findings[:20]})
    log_memory(db, "wins", f"review:{task_id}", {"task": task_id, "findings": len(all_findings)})
    log_event(db, "agent.completed", agent_id, {"task": task_id, "findings": len(all_findings)})
    return result


def run_test_writer(db, agent_id: str, task_id: str):
    """Run the test writer agent on route files."""
    # Get existing tests to avoid duplication
    existing_tests = set()
    for p in (CODEBASE / "tests").glob("test_*.py"):
        existing_tests.add(p.stem.replace("test_", ""))

    # Find untested source files
    route_files = collect_route_files(10)
    src_files = collect_python_files(10)

    untested = []
    for f in route_files + src_files:
        name = Path(f["path"]).stem
        if name not in existing_tests and name != "__init__":
            untested.append(f)

    if not untested:
        return "All files have test coverage"

    all_tests = []
    for f in untested[:5]:  # Limit to 5 files per run
        prompt = f"""Write pytest tests for this file:

File: {f['path']}
```python
{f['content']}
```

Write tests that cover the main functionality. IMPORTANT: Output ONLY raw JSON. No markdown fences, no explanations, no code blocks."""
        response = ollama_generate(prompt, AGENTS["test-writer"]["system"])
        try:
            data = _extract_json(response)
            if data:
                tests = data.get("tests", [])
                for test in tests:
                    test["source"] = f["path"]
                    all_tests.append(test)
        except Exception:
            log_agent_log(db, agent_id, task_id, "warn", f"Failed to parse JSON for {f['path']}")

    # Write test files
    written = 0
    for test in all_tests:
        test_file = CODEBASE / "tests" / test.get("file", "test_generated.py")
        if not test_file.exists():
            test_file.write_text(f'"""Auto-generated tests for {test.get("source", "unknown")}."""\n\n{test.get("code", "")}')
            written += 1

    result = json.dumps({"files_reviewed": len(untested), "tests_written": written, "test_files": [t.get("file") for t in all_tests]})
    log_memory(db, "wins", f"tests:{task_id}", {"task": task_id, "tests_written": written})
    log_event(db, "agent.completed", agent_id, {"task": task_id, "tests_written": written})
    return result


def run_perf_analyst(db, agent_id: str, task_id: str):
    """Run the performance analyst agent."""
    files = collect_python_files(15)
    if not files:
        return "No Python files found"

    all_issues = []
    for f in files:
        prompt = f"""Analyze this file for performance issues:

File: {f['path']}
```python
{f['content']}
```

Focus on blocking I/O in async, N+1 queries, memory issues. IMPORTANT: Output ONLY raw JSON. No markdown fences, no explanations, no code blocks."""
        response = ollama_generate(prompt, AGENTS["perf-analyst"]["system"])
        try:
            data = _extract_json(response)
            if data:
                issues = data.get("issues", [])
                for issue in issues:
                    issue["file"] = f["path"]
                    all_issues.append(issue)
        except Exception:
            log_agent_log(db, agent_id, task_id, "warn", f"Failed to parse JSON for {f['path']}")

    result = json.dumps({"total_issues": len(all_issues), "issues": all_issues[:20]})
    log_memory(db, "wins", f"perf:{task_id}", {"task": task_id, "issues": len(all_issues)})
    log_event(db, "agent.completed", agent_id, {"task": task_id, "issues": len(all_issues)})
    return result


def run_frontend_auditor(db, agent_id: str, task_id: str):
    """Run the frontend auditor agent on JS files."""
    files = collect_js_files(10)
    if not files:
        return "No JavaScript files found"

    all_issues = []
    for f in files:
        prompt = f"""Review this JavaScript file for UX and accessibility issues:

File: {f['path']}
```javascript
{f['content']}
```

Focus on: missing Escape handlers, aria labels, keyboard nav, XSS, memory leaks. IMPORTANT: Output ONLY raw JSON. No markdown fences, no explanations, no code blocks."""
        response = ollama_generate(prompt, AGENTS["frontend-auditor"]["system"])
        try:
            data = _extract_json(response)
            if data:
                issues = data.get("issues", [])
                for issue in issues:
                    issue["file"] = f["path"]
                    all_issues.append(issue)
        except Exception:
            log_agent_log(db, agent_id, task_id, "warn", f"Failed to parse JSON for {f['path']}")

    result = json.dumps({"total_issues": len(all_issues), "issues": all_issues[:20]})
    log_memory(db, "wins", f"frontend:{task_id}", {"task": task_id, "issues": len(all_issues)})
    log_event(db, "agent.completed", agent_id, {"task": task_id, "issues": len(all_issues)})
    return result


def run_doc_checker(db, agent_id: str, task_id: str):
    """Run the documentation checker agent."""
    # Collect docs
    readme = CODEBASE / "README.md"
    env_example = CODEBASE / ".env.example"
    troubleshooting = CODEBASE / "docs" / "TROUBLESHOOTING.md"

    docs = {}
    if readme.exists():
        docs["README.md"] = readme.read_text(errors="replace")[:6000]
    if env_example.exists():
        docs[".env.example"] = env_example.read_text(errors="replace")[:3000]
    if troubleshooting.exists():
        docs["TROUBLESHOOTING.md"] = troubleshooting.read_text(errors="replace")[:6000]

    # Collect route endpoints
    routes = []
    for p in (CODEBASE / "routes").glob("*.py"):
        content = p.read_text(errors="replace")
        # Extract route decorators
        import re
        endpoints = re.findall(r'@\w+\.(?:get|post|put|delete|patch)\s*\(\s*["\']([^"\']+)', content)
        for ep in endpoints:
            routes.append({"file": p.name, "endpoint": ep})

    prompt = f"""Check documentation completeness:

Endpoints found ({len(routes)}):
{json.dumps(routes[:30], indent=2)}

Documentation:
{json.dumps({k: v[:2000] for k, v in docs.items()}, indent=2)}

Find undocumented endpoints, missing env vars, outdated instructions. IMPORTANT: Output ONLY raw JSON. No markdown fences, no explanations, no code blocks."""
    response = ollama_generate(prompt, AGENTS["doc-checker"]["system"])
    try:
        data = _extract_json(response)
        if data:
            gaps = data.get("gaps", [])
            result = json.dumps({"total_gaps": len(gaps), "gaps": gaps[:20]})
            log_memory(db, "wins", f"docs:{task_id}", {"task": task_id, "gaps": len(gaps)})
            log_event(db, "agent.completed", agent_id, {"task": task_id, "gaps": len(gaps)})
            return result
    except Exception:
        pass

    return json.dumps({"total_gaps": 0, "gaps": [], "note": "Could not parse response"})


# ── Agent Runner Map ─────────────────────────────────────────────────────
RUNNERS = {
    "code-reviewer": run_code_reviewer,
    "test-writer": run_test_writer,
    "perf-analyst": run_perf_analyst,
    "frontend-auditor": run_frontend_auditor,
    "doc-checker": run_doc_checker,
}


# ── Main ─────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Tech Duinn Swarm Agent Runner")
    parser.add_argument("--agent", help="Run specific agent (default: all)")
    parser.add_argument("--list", action="store_true", help="List available agents")
    parser.add_argument("--model", default=_cfg["model"], help="Ollama model to use")
    parser.add_argument("--task", help="Run specific task ID")
    args = parser.parse_args()

    if args.list:
        print("\nAvailable Agents:")
        print("-" * 60)
        for aid, info in AGENTS.items():
            print(f"  {aid:20s} {info['role']}")
            print(f"  {'':20s} Tasks: {', '.join(info['tasks'])}")
            print()
        return

    # Override model in config
    _cfg["model"] = args.model

    if not DB_PATH.exists():
        print(f"✗ Swarm DB not found at {DB_PATH}")
        print(f"  Run the swarm setup first.")
        sys.exit(1)

    db = get_db()

    # Check Ollama is running
    try:
        import urllib.request
        with urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=5) as resp:
            available = [m["name"] for m in json.loads(resp.read()).get("models", [])]
            m = _cfg["model"]
            if m not in available and m.replace(":latest", "") not in available:
                print(f"✗ Model {m} not found. Available: {', '.join(available)}")
                print(f"  Pull it with: ollama pull {m}")
                sys.exit(1)
    except Exception as e:
        print(f"✗ Ollama not running at {OLLAMA_URL} ({e})")
        print(f"  Start it with: ollama serve")
        sys.exit(1)

    print(f"\n🐝 Tech Duinn Swarm Runner")
    print(f"   Model: {_cfg['model']}")
    print(f"   DB: {DB_PATH}")
    print(f"   Agents: {args.agent or 'all'}")
    print()

    # Run agents
    agents_to_run = [args.agent] if args.agent else list(AGENTS.keys())

    for agent_id in agents_to_run:
        if agent_id not in AGENTS:
            print(f"✗ Unknown agent: {agent_id}")
            continue

        info = AGENTS[agent_id]
        print(f"🤖 {info['name']} ({agent_id})")
        print(f"   {info['role']}")

        # Register agent
        register_agent(db, agent_id, info["name"], info["role"])

        # Find tasks
        task_ids = [args.task] if args.task else info["tasks"]
        for task_id in task_ids:
            row = db.execute("SELECT id, title, status FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if not row:
                print(f"   ⏭ Task {task_id} not found")
                continue
            if row["status"] == "done":
                print(f"   ⏭ Task {task_id} already done")
                continue

            print(f"   📋 Task: {row['title']} ({task_id})")
            claim_task(db, agent_id, task_id)
            log_agent_log(db, agent_id, task_id, "info", f"Starting task {task_id}")
            log_event(db, "agent.started", agent_id, {"task": task_id})

            start = time.time()
            try:
                runner = RUNNERS[agent_id]
                result = runner(db, agent_id, task_id)
                elapsed = time.time() - start
                complete_task(db, task_id, result)
                log_agent_log(db, agent_id, task_id, "info", f"Completed in {elapsed:.1f}s")
                print(f"   ✓ Done in {elapsed:.1f}s")
            except Exception as e:
                elapsed = time.time() - start
                fail_task(db, task_id, str(e))
                log_agent_log(db, agent_id, task_id, "error", str(e))
                log_memory(db, "losses", f"fail:{task_id}", {"task": task_id, "error": str(e)})
                print(f"   ✗ Failed after {elapsed:.1f}s: {e}")

        print()

    # Print summary
    rows = db.execute("SELECT status, COUNT(*) as cnt FROM tasks GROUP BY status").fetchall()
    print("\n📊 Swarm Status:")
    print("-" * 40)
    for r in rows:
        emoji = {"done": "✓", "in_progress": "⏳", "pending": "○", "failed": "✗"}.get(r["status"], "?")
        print(f"   {emoji} {r['status']:15s} {r['cnt']}")

    # Count wins/losses
    wins = db.execute("SELECT COUNT(*) FROM memory WHERE namespace = 'wins'").fetchone()[0]
    losses = db.execute("SELECT COUNT(*) FROM memory WHERE namespace = 'losses'").fetchone()[0]
    lessons = db.execute("SELECT COUNT(*) FROM memory WHERE namespace = 'lessons'").fetchone()[0]
    print(f"\n   Wins: {wins}  |  Losses: {losses}  |  Lessons: {lessons}")
    print()

    db.close()


if __name__ == "__main__":
    main()
