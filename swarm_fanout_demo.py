#!/usr/bin/env python3
"""
swarm_fanout_demo.py — Quick demo of the Tech Duinn Swarm Fan-Out system.

Shows how to use the fan-out orchestrator programmatically.
"""

import json
import sys
from pathlib import Path

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent))

from swarm_fanout import (
    FANOUT_PATTERNS,
    AGENT_TEMPLATES,
    collect_python_files,
    collect_js_files,
    collect_all_source_files,
    SubagentTask,
    SubagentResult,
    SwarmFanoutResult,
)


def demo_list_patterns():
    """Show all available fan-out patterns."""
    print("\n🐝 Available Fan-Out Patterns")
    print("=" * 60)
    for name, info in FANOUT_PATTERNS.items():
        print(f"\n  📋 {name}")
        print(f"     {info['description']}")
        print(f"     Agents: {', '.join(info['agents'])}")
        print(f"     Workers: {info['max_workers']}")


def demo_list_agents():
    """Show all available agent templates."""
    print("\n🤖 Available Agent Templates")
    print("=" * 60)
    for aid, info in AGENT_TEMPLATES.items():
        print(f"\n  🤖 {aid}")
        print(f"     Name: {info['name']}")
        print(f"     Role: {info['role']}")


def demo_file_collection():
    """Show what files would be collected."""
    print("\n📁 File Collection Demo")
    print("=" * 60)

    py_files = collect_python_files(10)
    print(f"\n  Python files (max 10): {len(py_files)}")
    for f in py_files[:5]:
        print(f"    - {f['path']} ({len(f['content'])} chars)")
    if len(py_files) > 5:
        print(f"    ... and {len(py_files) - 5} more")

    js_files = collect_js_files(10)
    print(f"\n  JavaScript files (max 10): {len(js_files)}")
    for f in js_files[:5]:
        print(f"    - {f['path']} ({len(f['content'])} chars)")
    if len(js_files) > 5:
        print(f"    ... and {len(js_files) - 5} more")

    all_files = collect_all_source_files(20)
    print(f"\n  All source files (max 20): {len(all_files)}")


def demo_task_creation():
    """Show how tasks are created for fan-out."""
    print("\n📋 Task Creation Demo")
    print("=" * 60)

    files = collect_python_files(3)
    if not files:
        print("  No files found (run from odysseus directory)")
        return

    agent_id = "code-reviewer"
    template = AGENT_TEMPLATES[agent_id]

    tasks = []
    for i, f in enumerate(files):
        task = SubagentTask(
            task_id=f"demo-{agent_id}-{i:04d}",
            agent_id=agent_id,
            file_path=f["path"],
            file_content=f["content"],
            prompt_template=template["prompt_template"],
        )
        tasks.append(task)

    print(f"\n  Created {len(tasks)} tasks for agent '{agent_id}':")
    for t in tasks:
        print(f"    - {t.task_id}: {t.file_path}")


def demo_dry_run():
    """Show a dry run of the fan-out system."""
    print("\n🔍 Dry Run Demo")
    print("=" * 60)

    pattern_name = "code-review"
    pattern = FANOUT_PATTERNS[pattern_name]

    print(f"\n  Pattern: {pattern_name}")
    print(f"  Description: {pattern['description']}")

    files = pattern["file_collector"](5)  # Limit to 5 for demo
    print(f"\n  Files to process: {len(files)}")

    total_tasks = len(files) * len(pattern["agents"])
    print(f"  Total tasks: {total_tasks}")
    print(f"  Parallel workers: {pattern['max_workers']}")

    print("\n  Task breakdown:")
    for agent_id in pattern["agents"]:
        print(f"    - {agent_id}: {len(files)} files")


def demo_result_structure():
    """Show the structure of fan-out results."""
    print("\n📊 Result Structure Demo")
    print("=" * 60)

    # Simulate a result
    result = SwarmFanoutResult(
        pattern="code-review",
        total_files=10,
        total_findings=5,
        findings_by_severity={"high": 2, "medium": 2, "low": 1},
        findings_by_agent={"code-reviewer": 5},
        elapsed_seconds=45.2,
        results=[
            SubagentResult(
                task_id="demo-001",
                agent_id="code-reviewer",
                file_path="src/auth.py",
                success=True,
                findings=[
                    {"severity": "high", "title": "SQL injection", "file": "src/auth.py"},
                    {"severity": "medium", "title": "Missing input validation", "file": "src/auth.py"},
                ],
                elapsed_seconds=4.5,
            ),
        ],
    )

    print(f"\n  Pattern: {result.pattern}")
    print(f"  Files scanned: {result.total_files}")
    print(f"  Total findings: {result.total_findings}")
    print(f"  Time elapsed: {result.elapsed_seconds}s")

    print("\n  Findings by severity:")
    for sev, count in result.findings_by_severity.items():
        print(f"    - {sev}: {count}")

    print("\n  Sample findings:")
    for r in result.results:
        for f in r.findings:
            print(f"    [{f['severity']}] {f['title']} in {f['file']}")


def main():
    """Run all demos."""
    print("\n" + "=" * 60)
    print("🐝 Tech Duinn Swarm Fan-Out System Demo")
    print("=" * 60)

    demo_list_patterns()
    demo_list_agents()
    demo_file_collection()
    demo_task_creation()
    demo_dry_run()
    demo_result_structure()

    print("\n" + "=" * 60)
    print("✅ Demo complete!")
    print("=" * 60)
    print("\nTo run an actual fan-out scan:")
    print("  python3 swarm_fanout.py --pattern code-review")
    print("  python3 swarm_fanout.py --pattern security-audit --synthesize")
    print("  python3 swarm_fanout.py --pattern full-scan --json")
    print()


if __name__ == "__main__":
    main()
