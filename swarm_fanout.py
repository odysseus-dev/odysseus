#!/usr/bin/env python3
"""
swarm_fanout.py — Tech Duinn Swarm Fan-Out Subagent Orchestrator

Extends the swarm_runner.py with parallel subagent fan-out patterns.
Multiple agents run concurrently against different files/tasks, results
are collected into the shared blackboard, and a synthesizer agent merges
findings into a unified report.

Usage:
    python3 swarm_fanout.py --pattern code-review    # Fan-out code review
    python3 swarm_fanout.py --pattern security-audit  # Fan-out security audit
    python3 swarm_fanout.py --pattern full-scan       # All agents in parallel
    python3 swarm_fanout.py --list                    # List available patterns
"""

import argparse
import asyncio
import json
import os
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

# ── Config ───────────────────────────────────────────────────────────────
DB_PATH = Path(__file__).parent / "data" / "tech_duinn" / "tech-duinn.db"
OLLAMA_URL = os.getenv("OLLAMA_HOST", "http://localhost:11434")
CODEBASE = Path(__file__).parent

# Mutable config
_cfg = {"model": os.getenv("SWARM_MODEL", "qwen2.5-coder:3b")}


# ── Data Classes ─────────────────────────────────────────────────────────

@dataclass
class SubagentTask:
    """A single unit of work for a subagent."""
    task_id: str
    agent_id: str
    file_path: str
    file_content: str
    prompt_template: str
    priority: int = 5
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SubagentResult:
    """Result from a single subagent execution."""
    task_id: str
    agent_id: str
    file_path: str
    success: bool
    findings: List[Dict[str, Any]]
    elapsed_seconds: float
    error: Optional[str] = None
    raw_response: str = ""


@dataclass
class SwarmFanoutResult:
    """Aggregated result from a fan-out swarm run."""
    pattern: str
    total_files: int
    total_findings: int
    findings_by_severity: Dict[str, int]
    findings_by_agent: Dict[str, int]
    elapsed_seconds: float
    results: List[SubagentResult]
    synthesis: str = ""


# ── JSON Extraction ──────────────────────────────────────────────────────

def _extract_json(text: str) -> dict | None:
    """Extract JSON from model response, stripping markdown fences."""
    import re
    text = re.sub(r'```(?:json)?\s*', '', text)
    text = re.sub(r'```\s*', '', text)
    text = text.strip()
    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end])
        except Exception:
            pass
    return None


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

def get_db() -> sqlite3.Connection:
    """Get a connection to the Tech Duinn swarm DB."""
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    return db


def register_agent(db: sqlite3.Connection, agent_id: str, name: str, role: str):
    """Register or update an agent in the swarm."""
    now = time.time()
    db.execute(
        "INSERT OR REPLACE INTO agents (id, name, status, capabilities, last_heartbeat, registered_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (agent_id, name, "active", role, now, now),
    )
    db.commit()


def claim_task(db: sqlite3.Connection, agent_id: str, task_id: str) -> bool:
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


def complete_task(db: sqlite3.Connection, task_id: str, result: str):
    """Mark a task as done."""
    now = time.time()
    db.execute(
        "UPDATE tasks SET status = 'done', result = ?, updated_at = ?, completed_at = ? WHERE id = ?",
        (result, now, now, task_id),
    )
    db.commit()


def fail_task(db: sqlite3.Connection, task_id: str, reason: str):
    """Mark a task as failed."""
    now = time.time()
    db.execute(
        "UPDATE tasks SET status = 'failed', result = ?, updated_at = ? WHERE id = ?",
        (reason, now, task_id),
    )
    db.commit()


def log_memory(db: sqlite3.Connection, namespace: str, key: str, value: dict):
    """Log to shared memory."""
    now = time.time()
    mem_id = f"mem-{int(now * 1000)}-{key}"
    db.execute(
        "INSERT OR REPLACE INTO memory (id, namespace, key, value, tags, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (mem_id, namespace, key, json.dumps(value), namespace, now, now),
    )
    db.commit()


def log_event(db: sqlite3.Connection, topic: str, source: str, payload: dict):
    """Publish an event to the swarm bus."""
    now = time.time()
    db.execute(
        "INSERT INTO events (topic, source, payload, timestamp) VALUES (?, ?, ?, ?)",
        (topic, source, json.dumps(payload), now),
    )
    db.commit()


def log_agent_log(db: sqlite3.Connection, agent_id: str, task_id: str, level: str, message: str):
    """Write an agent log entry."""
    now = time.time()
    db.execute(
        "INSERT INTO logs (agent_id, level, source, message, metadata, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
        (agent_id, level, task_id, message, json.dumps({"task_id": task_id}), now),
    )
    db.commit()


def post_blackboard(db: sqlite3.Connection, root_id: str, author: str, key: str, value: Any):
    """Post an update to the shared blackboard (memory namespace)."""
    log_memory(db, f"blackboard:{root_id}", key, {
        "author": author,
        "value": value,
        "timestamp": time.time(),
    })


# ── File Collection ──────────────────────────────────────────────────────

def collect_python_files(max_files: int = 30) -> List[Dict[str, str]]:
    """Collect Python source files for review."""
    files = []
    for p in sorted(CODEBASE.glob("src/*.py")):
        if p.stat().st_size > 0:
            files.append({"path": str(p.relative_to(CODEBASE)), "content": p.read_text(errors="replace")[:8000]})
    for p in sorted(CODEBASE.glob("routes/*.py")):
        if p.stat().st_size > 0:
            files.append({"path": str(p.relative_to(CODEBASE)), "content": p.read_text(errors="replace")[:8000]})
    for p in sorted(CODEBASE.glob("core/*.py")):
        if p.stat().st_size > 0:
            files.append({"path": str(p.relative_to(CODEBASE)), "content": p.read_text(errors="replace")[:8000]})
    return files[:max_files]


def collect_js_files(max_files: int = 20) -> List[Dict[str, str]]:
    """Collect JavaScript source files for review."""
    files = []
    for p in sorted((CODEBASE / "static" / "js").glob("*.js")):
        if p.stat().st_size > 0:
            files.append({"path": str(p.relative_to(CODEBASE)), "content": p.read_text(errors="replace")[:8000]})
    return files[:max_files]


def collect_all_source_files(max_files: int = 40) -> List[Dict[str, str]]:
    """Collect all source files (Python + JS) for comprehensive review."""
    py = collect_python_files(max_files // 2)
    js = collect_js_files(max_files // 2)
    return py + js


# ── Agent Prompt Templates ───────────────────────────────────────────────

AGENT_TEMPLATES = {
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
{"findings": [{"severity": "critical|high|medium|low", "title": "short title", "description": "what's wrong", "fix": "how to fix"}]}
If no issues: {"findings": []}""",
        "prompt_template": "Review this file for bugs and security issues:\n\nFile: {file_path}\n```python\n{file_content}\n```\n\nIMPORTANT: Output ONLY raw JSON. No markdown fences, no explanations.",
    },
    "security-auditor": {
        "name": "Security Auditor",
        "role": "Deep security analysis for vulnerabilities",
        "system": """You are a security specialist. Perform deep analysis for:
- SQL injection (string formatting in queries, missing parameterization)
- XSS (innerHTML, unsanitized user input in HTML)
- SSRF (user-controlled URLs, missing validation)
- Path traversal (user input in file paths, missing sanitization)
- Authentication bypass (missing auth checks, weak token validation)
- Secret exposure (hardcoded keys, logging sensitive data)
- CSRF (missing tokens on state-changing endpoints)
- Command injection (subprocess with shell=True, os.system)

Output format (JSON):
{"findings": [{"severity": "critical|high|medium|low", "category": "injection|xss|ssrf|traversal|auth|secret|csrf|cmd_injection", "title": "short title", "description": "detailed description", "line_hint": "approximate line or function", "fix": "specific code change"}]}
If no issues: {"findings": []}""",
        "prompt_template": "Perform deep security audit on this file:\n\nFile: {file_path}\n```python\n{file_content}\n```\n\nIMPORTANT: Output ONLY raw JSON. No markdown fences, no explanations.",
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
{"findings": [{"severity": "high|medium|low", "type": "blocking_io|n_plus_1|memory|caching|payload", "title": "short title", "impact": "description of impact", "fix": "specific code change"}]}
If no issues: {"findings": []}""",
        "prompt_template": "Analyze this file for performance issues:\n\nFile: {file_path}\n```python\n{file_content}\n```\n\nFocus on blocking I/O in async, N+1 queries, memory issues. IMPORTANT: Output ONLY raw JSON.",
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
{"findings": [{"severity": "high|medium|low", "category": "a11y|ux|security|performance", "title": "short title", "description": "what's wrong", "fix": "how to fix"}]}
If no issues: {"findings": []}""",
        "prompt_template": "Review this JavaScript file for UX and accessibility issues:\n\nFile: {file_path}\n```javascript\n{file_content}\n```\n\nIMPORTANT: Output ONLY raw JSON. No markdown fences, no explanations.",
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
{"findings": [{"severity": "medium|low", "type": "missing_doc|outdated|broken_link|missing_env", "description": "what's missing or wrong", "suggestion": "what to add"}]}
If docs are complete: {"findings": []}""",
        "prompt_template": "Check this file for documentation gaps:\n\nFile: {file_path}\n```\n{file_content}\n```\n\nIMPORTANT: Output ONLY raw JSON. No markdown fences, no explanations.",
    },
    "test-writer": {
        "name": "Test Writer",
        "role": "Identifies untested code paths and suggests tests",
        "system": """You are a test engineer. Identify untested code paths and suggest pytest tests.
Focus on:
- Happy path tests
- Edge cases (empty input, None, boundary values)
- Error handling (invalid input, missing files)
- Async function testing with pytest-asyncio

Output format (JSON):
{"findings": [{"severity": "medium|low", "type": "missing_test|edge_case|error_path", "function": "function_name", "description": "what needs testing", "test_code": "suggested pytest code"}]}
If code is well-tested: {"findings": []}""",
        "prompt_template": "Identify untested code paths in this file:\n\nFile: {file_path}\n```python\n{file_content}\n```\n\nIMPORTANT: Output ONLY raw JSON. No markdown fences, no explanations.",
    },
}


# ── Fan-Out Patterns ─────────────────────────────────────────────────────

FANOUT_PATTERNS = {
    "code-review": {
        "description": "Parallel code review across all Python files",
        "agents": ["code-reviewer"],
        "file_collector": collect_python_files,
        "max_workers": 4,
    },
    "security-audit": {
        "description": "Deep security audit of all source files",
        "agents": ["security-auditor"],
        "file_collector": collect_all_source_files,
        "max_workers": 4,
    },
    "perf-scan": {
        "description": "Performance analysis of Python backend",
        "agents": ["perf-analyst"],
        "file_collector": collect_python_files,
        "max_workers": 3,
    },
    "frontend-scan": {
        "description": "Frontend UX/accessibility audit",
        "agents": ["frontend-auditor"],
        "file_collector": collect_js_files,
        "max_workers": 3,
    },
    "full-scan": {
        "description": "All agents in parallel — comprehensive analysis",
        "agents": ["code-reviewer", "security-auditor", "perf-analyst", "frontend-auditor"],
        "file_collector": collect_all_source_files,
        "max_workers": 6,
    },
    "backend-deep": {
        "description": "Deep backend analysis (code + security + perf)",
        "agents": ["code-reviewer", "security-auditor", "perf-analyst"],
        "file_collector": collect_python_files,
        "max_workers": 5,
    },
    "test-coverage": {
        "description": "Identify testing gaps across codebase",
        "agents": ["test-writer"],
        "file_collector": collect_python_files,
        "max_workers": 3,
    },
}


# ── Subagent Executor ────────────────────────────────────────────────────

def execute_subagent(task: SubagentTask) -> SubagentResult:
    """Execute a single subagent task (runs in thread pool)."""
    start = time.time()
    template = AGENT_TEMPLATES[task.agent_id]

    prompt = task.prompt_template.format(
        file_path=task.file_path,
        file_content=task.file_content,
    )

    try:
        response = ollama_generate(prompt, template["system"], timeout=90)
        data = _extract_json(response)

        findings = []
        if data:
            # Normalize findings from different agent types
            raw_findings = (
                data.get("findings", []) or
                data.get("issues", []) or
                data.get("gaps", [])
            )
            for f in raw_findings:
                f["file"] = task.file_path
                f["agent"] = task.agent_id
                findings.append(f)

        elapsed = time.time() - start
        return SubagentResult(
            task_id=task.task_id,
            agent_id=task.agent_id,
            file_path=task.file_path,
            success=True,
            findings=findings,
            elapsed_seconds=elapsed,
            raw_response=response[:500],
        )
    except Exception as e:
        elapsed = time.time() - start
        return SubagentResult(
            task_id=task.task_id,
            agent_id=task.agent_id,
            file_path=task.file_path,
            success=False,
            findings=[],
            elapsed_seconds=elapsed,
            error=str(e),
        )


# ── Fan-Out Orchestrator ─────────────────────────────────────────────────

def run_fanout(
    pattern_name: str,
    model: Optional[str] = None,
    max_files: Optional[int] = None,
    dry_run: bool = False,
) -> SwarmFanoutResult:
    """Run a fan-out pattern: spawn subagents in parallel, collect results."""

    if pattern_name not in FANOUT_PATTERNS:
        raise ValueError(f"Unknown pattern: {pattern_name}. Available: {list(FANOUT_PATTERNS.keys())}")

    pattern = FANOUT_PATTERNS[pattern_name]
    if model:
        _cfg["model"] = model

    overall_start = time.time()

    print(f"\n🐝 Tech Duinn Swarm Fan-Out")
    print(f"   Pattern: {pattern_name} — {pattern['description']}")
    print(f"   Model: {_cfg['model']}")
    print(f"   Agents: {', '.join(pattern['agents'])}")
    print(f"   Max workers: {pattern['max_workers']}")
    print()

    # Collect files
    files = pattern["file_collector"](max_files or 40)
    print(f"   📁 Files collected: {len(files)}")

    if dry_run:
        print("\n   [DRY RUN] Would process:")
        for f in files:
            print(f"     - {f['path']}")
        return SwarmFanoutResult(
            pattern=pattern_name,
            total_files=len(files),
            total_findings=0,
            findings_by_severity={},
            findings_by_agent={},
            elapsed_seconds=0,
            results=[],
        )

    # Build task list
    tasks: List[SubagentTask] = []
    for agent_id in pattern["agents"]:
        template = AGENT_TEMPLATES[agent_id]
        for i, f in enumerate(files):
            task_id = f"fanout-{pattern_name}-{agent_id}-{i:04d}"
            tasks.append(SubagentTask(
                task_id=task_id,
                agent_id=agent_id,
                file_path=f["path"],
                file_content=f["content"],
                prompt_template=template["prompt_template"],
            ))

    print(f"   📋 Total tasks: {len(tasks)}")
    print(f"\n   ⚡ Fan-out starting ({pattern['max_workers']} parallel workers)...")
    print()

    # Execute in parallel
    results: List[SubagentResult] = []
    completed = 0
    failed = 0

    with ThreadPoolExecutor(max_workers=pattern["max_workers"]) as executor:
        future_to_task = {executor.submit(execute_subagent, t): t for t in tasks}

        for future in as_completed(future_to_task):
            result = future.result()
            results.append(result)
            completed += 1

            if result.success:
                finding_count = len(result.findings)
                status = f"✓ {finding_count} findings" if finding_count else "✓ clean"
                print(f"   [{completed}/{len(tasks)}] {result.agent_id}: {result.file_path} — {status} ({result.elapsed_seconds:.1f}s)")
            else:
                failed += 1
                print(f"   [{completed}/{len(tasks)}] {result.agent_id}: {result.file_path} — ✗ {result.error}")

    # Aggregate results
    all_findings = []
    for r in results:
        all_findings.extend(r.findings)

    severity_counts = {}
    agent_counts = {}
    for f in all_findings:
        sev = f.get("severity", "unknown")
        severity_counts[sev] = severity_counts.get(sev, 0) + 1
        agent = f.get("agent", "unknown")
        agent_counts[agent] = agent_counts.get(agent, 0) + 1

    elapsed = time.time() - overall_start

    # Print summary
    print(f"\n{'='*60}")
    print(f"📊 Fan-Out Results: {pattern_name}")
    print(f"{'='*60}")
    print(f"   Files scanned:    {len(files)}")
    print(f"   Tasks completed:  {completed} ({failed} failed)")
    print(f"   Total findings:   {len(all_findings)}")
    print(f"   Time elapsed:     {elapsed:.1f}s")
    print()

    if severity_counts:
        print("   By severity:")
        for sev in ["critical", "high", "medium", "low", "unknown"]:
            if sev in severity_counts:
                emoji = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢"}.get(sev, "⚪")
                print(f"     {emoji} {sev:12s} {severity_counts[sev]}")

    if agent_counts:
        print("\n   By agent:")
        for agent, count in sorted(agent_counts.items()):
            print(f"     🤖 {agent:20s} {count}")

    # Log to swarm DB
    if DB_PATH.exists():
        db = get_db()
        try:
            log_event(db, "fanout.completed", "swarm-fanout", {
                "pattern": pattern_name,
                "files": len(files),
                "findings": len(all_findings),
                "elapsed": elapsed,
            })
            log_memory(db, "wins", f"fanout:{pattern_name}:{int(time.time())}", {
                "pattern": pattern_name,
                "findings": len(all_findings),
                "severity": severity_counts,
            })
        finally:
            db.close()

    return SwarmFanoutResult(
        pattern=pattern_name,
        total_files=len(files),
        total_findings=len(all_findings),
        findings_by_severity=severity_counts,
        findings_by_agent=agent_counts,
        elapsed_seconds=elapsed,
        results=results,
    )


# ── Synthesis Agent ──────────────────────────────────────────────────────

def synthesize_findings(result: SwarmFanoutResult) -> str:
    """Run a synthesis agent to merge all findings into a unified report."""

    # Collect all findings
    all_findings = []
    for r in result.results:
        all_findings.extend(r.findings)

    if not all_findings:
        return "No findings to synthesize."

    # Group by file
    by_file: Dict[str, List[Dict]] = {}
    for f in all_findings:
        fp = f.get("file", "unknown")
        by_file.setdefault(fp, []).append(f)

    # Build synthesis prompt
    findings_json = json.dumps(all_findings[:50], indent=2)  # Limit to fit context

    prompt = f"""You are a senior technical lead synthesizing code review findings.

Here are {len(all_findings)} findings from {len(result.results)} subagent reviews:

{findings_json}

Create a prioritized action plan:
1. Group related findings
2. Identify the top 5 most critical issues
3. Suggest a fix order (what to fix first)
4. Note any systemic patterns (e.g., "many files lack input validation")

Output a concise markdown report with sections: Critical Issues, High Priority, Medium Priority, Systemic Patterns, Recommended Fix Order."""

    print("\n   🧠 Running synthesis agent...")
    response = ollama_generate(prompt, "You are a technical lead creating actionable reports from code review findings.", timeout=120)

    return response


# ── CLI ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Tech Duinn Swarm Fan-Out Orchestrator")
    parser.add_argument("--pattern", help="Fan-out pattern to run")
    parser.add_argument("--list", action="store_true", help="List available patterns")
    parser.add_argument("--list-agents", action="store_true", help="List available agents")
    parser.add_argument("--model", default=_cfg["model"], help="Ollama model to use")
    parser.add_argument("--max-files", type=int, help="Max files per agent")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be processed")
    parser.add_argument("--synthesize", action="store_true", help="Run synthesis agent after scan")
    parser.add_argument("--json", action="store_true", help="Output results as JSON")
    args = parser.parse_args()

    if args.list:
        print("\n🐝 Available Fan-Out Patterns:")
        print("-" * 60)
        for name, info in FANOUT_PATTERNS.items():
            print(f"  {name:20s} {info['description']}")
            print(f"  {'':20s} Agents: {', '.join(info['agents'])}")
            print(f"  {'':20s} Workers: {info['max_workers']}")
            print()
        return

    if args.list_agents:
        print("\n🤖 Available Agent Templates:")
        print("-" * 60)
        for aid, info in AGENT_TEMPLATES.items():
            print(f"  {aid:20s} {info['role']}")
        print()
        return

    if not args.pattern:
        parser.print_help()
        print("\nUse --list to see available patterns.")
        return

    # Check Ollama
    try:
        import urllib.request
        with urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=5) as resp:
            available = [m["name"] for m in json.loads(resp.read()).get("models", [])]
            m = args.model or _cfg["model"]
            if m not in available and m.replace(":latest", "") not in available:
                print(f"✗ Model {m} not found. Available: {', '.join(available)}")
                sys.exit(1)
    except Exception as e:
        print(f"✗ Ollama not running at {OLLAMA_URL} ({e})")
        sys.exit(1)

    # Run fan-out
    result = run_fanout(
        args.pattern,
        model=args.model,
        max_files=args.max_files,
        dry_run=args.dry_run,
    )

    # Optional synthesis
    if args.synthesize and not args.dry_run and result.total_findings > 0:
        synthesis = synthesize_findings(result)
        result.synthesis = synthesis
        print(f"\n{'='*60}")
        print("📝 Synthesis Report")
        print(f"{'='*60}")
        print(synthesis)

    # JSON output
    if args.json:
        output = {
            "pattern": result.pattern,
            "total_files": result.total_files,
            "total_findings": result.total_findings,
            "findings_by_severity": result.findings_by_severity,
            "findings_by_agent": result.findings_by_agent,
            "elapsed_seconds": result.elapsed_seconds,
            "findings": [],
        }
        for r in result.results:
            output["findings"].extend(r.findings)
        if result.synthesis:
            output["synthesis"] = result.synthesis
        print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
