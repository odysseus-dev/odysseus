# Tech Duinn Swarm Fan-Out Subagent System

A parallel subagent orchestrator that extends the Tech Duinn swarm infrastructure with fan-out patterns for concurrent code analysis.

## Overview

The fan-out system spawns multiple AI subagents in parallel to analyze different files simultaneously. Results are collected, aggregated, and optionally synthesized into a unified report.

## Architecture

```
                    ┌─────────────────┐
                    │  Fan-Out        │
                    │  Orchestrator   │
                    └────────┬────────┘
                             │
        ┌────────────────────┼────────────────────┐
        │                    │                    │
        ▼                    ▼                    ▼
┌───────────────┐    ┌───────────────┐    ┌───────────────┐
│  Subagent 1   │    │  Subagent 2   │    │  Subagent N   │
│  (file_1.py)  │    │  (file_2.py)  │    │  (file_n.py)  │
└───────┬───────┘    └───────┬───────┘    └───────┬───────┘
        │                    │                    │
        └────────────────────┼────────────────────┘
                             │
                             ▼
                    ┌─────────────────┐
                    │   Blackboard    │
                    │   (SQLite DB)   │
                    └────────┬────────┘
                             │
                             ▼
                    ┌─────────────────┐
                    │   Synthesizer   │
                    │   (optional)    │
                    └─────────────────┘
```

## Quick Start

### CLI Usage

```bash
# List available patterns
python3 swarm_fanout.py --list

# List available agents
python3 swarm_fanout.py --list-agents

# Dry run (show what would be processed)
python3 swarm_fanout.py --pattern code-review --dry-run

# Run a fan-out scan
python3 swarm_fanout.py --pattern code-review

# Run with synthesis report
python3 swarm_fanout.py --pattern security-audit --synthesize

# Run with JSON output
python3 swarm_fanout.py --pattern full-scan --json

# Use a specific model
python3 swarm_fanout.py --pattern code-review --model llama3:8b
```

### REST API

```bash
# List patterns
curl http://localhost:8080/api/fanout/patterns

# List agents
curl http://localhost:8080/api/fanout/agents

# Start a fan-out run
curl -X POST http://localhost:8080/api/fanout/run \
  -H "Content-Type: application/json" \
  -d '{"pattern": "code-review", "synthesize": true}'

# Check run status
curl http://localhost:8080/api/fanout/runs/{run_id}

# Get findings with filters
curl http://localhost:8080/api/fanout/runs/{run_id}/findings?severity=high

# Get synthesis report
curl http://localhost:8080/api/fanout/runs/{run_id}/synthesis

# Get system stats
curl http://localhost:8080/api/fanout/stats
```

## Available Patterns

| Pattern | Description | Agents | Workers |
|---------|-------------|--------|---------|
| `code-review` | Parallel code review across all Python files | code-reviewer | 4 |
| `security-audit` | Deep security audit of all source files | security-auditor | 4 |
| `perf-scan` | Performance analysis of Python backend | perf-analyst | 3 |
| `frontend-scan` | Frontend UX/accessibility audit | frontend-auditor | 3 |
| `full-scan` | All agents in parallel | code-reviewer, security-auditor, perf-analyst, frontend-auditor | 6 |
| `backend-deep` | Deep backend analysis | code-reviewer, security-auditor, perf-analyst | 5 |
| `test-coverage` | Identify testing gaps | test-writer | 3 |

## Available Agents

| Agent | Role |
|-------|------|
| `code-reviewer` | Reviews code for bugs, security issues, and bad patterns |
| `security-auditor` | Deep security analysis for vulnerabilities |
| `perf-analyst` | Finds performance bottlenecks and suggests fixes |
| `frontend-auditor` | Reviews JavaScript for UX bugs, accessibility, and performance |
| `doc-checker` | Finds undocumented features and broken docs |
| `test-writer` | Identifies untested code paths and suggests tests |

## Integration with Existing Swarm

The fan-out system integrates with the existing Tech Duinn swarm infrastructure:

- **Shared Blackboard**: Results are posted to the swarm's shared memory
- **Event Bus**: Fan-out runs emit events to the swarm event bus
- **Agent Registry**: Subagents are registered in the swarm agent registry
- **Task Queue**: Fan-out tasks are tracked in the swarm task queue

## Files

- `swarm_fanout.py` — Core fan-out orchestrator
- `routes/fanout_routes.py` — REST API endpoints
- `tests/test_fanout_routes.py` — Tests
- `swarm_fanout_demo.py` — Demo script

## Programmatic Usage

```python
from swarm_fanout import run_fanout, synthesize_findings

# Run a fan-out scan
result = run_fanout("code-review", model="llama3:8b", max_files=10)

# Get results
print(f"Found {result.total_findings} issues in {result.total_files} files")
print(f"By severity: {result.findings_by_severity}")

# Optional: synthesize findings into a report
if result.total_findings > 0:
    report = synthesize_findings(result)
    print(report)
```

## Custom Patterns

Add new patterns by extending `FANOUT_PATTERNS` in `swarm_fanout.py`:

```python
FANOUT_PATTERNS["my-pattern"] = {
    "description": "My custom analysis",
    "agents": ["code-reviewer", "security-auditor"],
    "file_collector": collect_python_files,  # or custom collector
    "max_workers": 4,
}
```

## Custom Agents

Add new agents by extending `AGENT_TEMPLATES` in `swarm_fanout.py`:

```python
AGENT_TEMPLATES["my-agent"] = {
    "name": "My Agent",
    "role": "Does something specific",
    "system": "You are a specialist in...",
    "prompt_template": "Analyze {file_path}:\n{file_content}",
}
```
