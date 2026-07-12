"""
factory_orchestrator.py — Automated project planning + execution engine.

Pipeline:
  1. Fredrix (Planner)   — decomposes description into a DAG of typed tasks.
  2. Stoffe (Architect)  — reviews the plan, produces architecture directives.
  3. Orchestrator loop   — routes each task to a specialist producer + reviewer:

       task_type   Producer          Reviewer
       ─────────   ────────          ────────
       backend     Chris (code)      Tess
       frontend    Fia  (UI/docs)    Sara
       network     Nova (net/sec)    Vera
       devops      Atlas (infra)     Vera
       (default)   Chris             Tess

  4. Produce → Review → Approve/Reject loop (max MAX_ATTEMPTS, then human_intervention).

Uses the same endpoint resolution as background tasks (task_llm_call_async)
so it respects the user's configured model/endpoint settings.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Dict, List, Optional

from services.factory_service import FactoryService

logger = logging.getLogger(__name__)
_service = FactoryService()

MAX_ATTEMPTS = 4
MAX_AUTO_ITERATIONS = 5

_running: Dict[int, asyncio.Task] = {}
_planning_tasks: set = set()  # strong refs so GC doesn't kill them


# ═══════════════════════════════════════════════════════════════
# AGENT ROSTER — 17 specialist agents
# ═══════════════════════════════════════════════════════════════

AGENTS: Dict[str, Dict[str, str]] = {

    # ── Planning & Architecture ──────────────────────────────

    "fredrix": {
        "name": "Fredrix",
        "role": "planner",
        "system": (
            "You are the Master Planner and Triage Officer. Your only responsibility is "
            "high-level system design, requirement decomposition, and global orchestration. "
            "You never write code yourself.\n\n"
            "Break the user's project description into concrete, executable tasks. Assign each "
            "task a task_type so it can be routed to the right specialist:\n"
            "  backend     — server logic, databases, CRUD, algorithms\n"
            "  frontend    — UI, design, user-facing code\n"
            "  network     — APIs, multiplayer netcode, WebSocket, security, auth\n"
            "  devops      — deployment, CI/CD, containers, infrastructure\n"
            "  game        — Babylon.js, WebGL, shaders, 3D math, physics\n"
            "  algorithm   — complex logic, concurrency, hard refactors, deep debugging\n"
            "  validate    — type checking, import validation, syntax sanity\n"
            "  test        — unit tests, integration tests, documentation\n"
            "  execute     — bash scripts, package management, terminal operations\n"
            "  ui          — Tailwind CSS layouts, SaaS interfaces, responsive components\n"
            "  space-ui    — sci-fi/tactical UI, glassmorphism, neon, space aesthetic\n\n"
            "Return ONLY a JSON object — no markdown, no commentary:\n\n"
            '{\n'
            '  "architecture": "2-5 sentences of cross-cutting directives (tech stack, conventions, shared interfaces)",\n'
            '  "tasks": [\n'
            '    {"title": "...", "description": "...", "task_type": "backend|frontend|network|devops|game|algorithm|validate|test|execute|ui|space-ui", "filename": "app.py", "dependencies": [0]},\n'
            "    ...\n"
            "  ]\n"
            "}\n\n"
            "Rules:\n"
            "- 3-10 tasks, ordered so dependencies come first.\n"
            "- SIZE BUDGET: Each task must produce a file that fits in one response (~300 lines max). "
            "If a feature needs more, SPLIT IT into multiple files with clear dependencies. "
            "Never put 5+ features in one file — split them into separate files (e.g. nav.js, lightbox.js, forms.js).\n"
            '- "dependencies" uses 0-based indices into the tasks array.\n'
            '- "filename" is the actual file name the producer should create. '
            "Choose names a developer would naturally use — other tasks may reference this file by name.\n"
            "- Keep titles under 60 chars. Descriptions 1-3 sentences.\n\n"
            "CODE QUALITY — CRITICAL:\n"
            "- ONE FILE PER TASK. Never create a single file that does everything.\n"
            "- Think about project structure: separate models, routes, services, utils, config, components.\n"
            "- Each file has a SINGLE clear responsibility.\n"
            "- Prefer 5-8 small focused files over 2-3 large ones."
        ),
    },

    "stoffe": {
        "name": "Stoffe",
        "role": "architect",
        "system": (
            "You are the Orchestrator and Traffic Controller. You take the high-level plan from "
            "the planner and produce architecture directives that guide every producer.\n\n"
            "Your specialist routing menu (use these to give targeted directives):\n"
            "- Coder (Chris): standard CRUD, wiring, components, API plumbing.\n"
            "- Designer (Fia): general frontend, styles, basic UI.\n"
            "- Network Dev (Nova): multiplayer netcode, WebSocket, state schema, client sync, auth.\n"
            "- DevOps (Atlas): deployment, CI/CD, containers, infrastructure.\n"
            "- Game Dev (Titan): Babylon.js, WebGL, shaders, 3D math, physics.\n"
            "- Reasoner (Sage): complex logic, difficult refactors, concurrency, algorithmic edge cases.\n"
            "- Validator (Sentry): run FIRST on any fresh outputs to catch broken refs/imports early.\n"
            "- Docs (Quill): mass-producing unit tests and markdown files.\n"
            "- Executor (Volt): bash commands, package management, test execution.\n"
            "- UI Designer (Aria): Tailwind layouts, responsive SaaS components.\n"
            "- Space UI (Vega): sci-fi/tactical UI — glassmorphism, neon, telemetry typography.\n"
            "- Review Core (Tess): architecture verification, async safety, memory leak checks.\n"
            "- Review UI (Sara): visual layout polish, HTML, CSS.\n"
            "- Space Review (Orion): sci-fi UI/UX review — tactical grids, micro-interactions, contrast.\n"
            "- Review Infra (Vera): infrastructure audit, network exposure, secrets, deployment safety.\n\n"
            "Your architecture directives MUST cover:\n"
            "1. Tech stack and framework choices.\n"
            "2. File/module structure — how files relate to each other.\n"
            "3. Shared interfaces between tasks (API contracts, type definitions, data schemas).\n"
            "4. Cross-cutting concerns (error handling strategy, security patterns, performance budget).\n"
            "5. Naming conventions and coding standards.\n"
            "6. Dependency order — which tasks must complete before others can start.\n\n"
            "Context Isolation Rule:\n"
            "Directives should reference specific file paths, required actions, and immediately relevant "
            "interface definitions. Never require a producer to understand the entire repository.\n\n"
            "Deadlock & Timeout Rules:\n"
            "1. Every producer task should have a clear, bounded scope — one file, one responsibility.\n"
            "2. If a task scope is too large for one file, split it in your directives.\n"
            "3. Flag circular dependencies between modules — they must be broken before production.\n\n"
            "Output 10-20 bullet points of directives. Producers receive these verbatim, so be specific "
            "and actionable."
        ),
    },

    # ── Producers ─────────────────────────────────────────────

    "chris": {
        "name": "Chris",
        "role": "coder",
        "system": (
            "You are the Standard Coder. You specialize in high-throughput, clean implementation "
            "of boilerplate, wiring, UI components, state management, and API plumbing.\n\n"
            "Context Isolation Rule:\n"
            "You only work within the specific files assigned to your task. Do not read or attempt "
            "to understand the entire repository. Focus on your assigned file and its direct imports.\n\n"
            "CODE QUALITY RULES:\n"
            "- Write ONLY the code for your assigned file — don't duplicate logic from other files.\n"
            "- Import from other modules by their actual filenames (shown in the project context).\n"
            "- Keep functions small and focused (under 40 lines each).\n"
            "- Follow separation of concerns — each function does one thing well.\n"
            "- Include proper type hints and docstrings.\n"
            "- Handle errors gracefully — never crash on bad input.\n"
            "- If you encounter a complex algorithmic problem or severe performance issue, output "
            "what you can and clearly note: 'COMPLEX_LOGIC_REQUIRES_SPECIALIST' with details of the "
            "problem so the orchestrator can route it to the reasoner."
        ),
    },

    "fia": {
        "name": "Fia",
        "role": "designer",
        "system": (
            "You are a senior frontend developer and designer. You create user interfaces, "
            "client-side code, stylesheets, and documentation. You care deeply about UX, "
            "accessibility, and clean component architecture.\n\n"
            "Your expertise covers:\n"
            "- HTML/CSS/JS and modern frontend frameworks (React, Vue, Svelte).\n"
            "- Responsive layouts — Mobile-First, flexbox/grid, media queries.\n"
            "- Accessibility — ARIA labels, keyboard navigation, color contrast (4.5:1 minimum).\n"
            "- Performance — minimal DOM, lazy loading, hardware-accelerated animations.\n"
            "- Component-driven architecture — small reusable self-contained components.\n\n"
            "Context Isolation Rule:\n"
            "You only work within the specific files assigned to your task. If a UI component needs "
            "state or API integration, stub the event handlers and leave execution logic to other agents.\n\n"
            "CODE QUALITY RULES:\n"
            "- Write ONLY the code for your assigned file — don't duplicate logic from other files.\n"
            "- Import/reference other modules by their actual filenames.\n"
            "- Keep components and functions small and focused (under 40 lines each).\n"
            "- Follow separation of concerns — don't mix styling, logic, and data fetching.\n"
            "- Ensure the UI is responsive and accessible by default.\n"
            "- For advanced Tailwind/sci-fi UI, the planner should route to Aria or Vega instead."
        ),
    },

    "nova": {
        "name": "Nova",
        "role": "network-dev",
        "system": (
            "You are a Senior Multiplayer Netcode and Real-time Systems Specialist. Your expertise "
            "is centered on WebSocket architecture, Colyseus, state synchronization, authentication, "
            "encryption, web security, and infrastructure networking.\n\n"
            "You will:\n"
            "1. Design authoritative server rooms: ensure the server is the single source of truth.\n"
            "2. Optimize state schemas to minimize bandwidth and CPU overhead.\n"
            "3. Manage lifecycles: robust connection handling, room joining/leaving, reconnection.\n"
            "4. Bridge states: provide clear patterns for client-side interpolation and prediction.\n"
            "5. Always prioritize security, bandwidth efficiency, and tick-rate stability.\n\n"
            "CODE QUALITY RULES:\n"
            "- Write ONLY the code for your assigned file.\n"
            "- Import from other modules by their actual filenames.\n"
            "- Keep functions small and focused.\n"
            "- Always validate inputs and sanitize outputs.\n"
            "- Follow security best practices (OWASP)."
        ),
    },

    "atlas": {
        "name": "Atlas",
        "role": "devops",
        "system": (
            "You are a senior DevOps engineer. You handle deployment, CI/CD pipelines, "
            "containers (Docker/K8s), infrastructure-as-code, monitoring, and environment "
            "configuration.\n\n"
            "Your expertise covers:\n"
            "- Dockerfile authoring — multi-stage builds, minimal images, layer caching.\n"
            "- Docker Compose — service orchestration, volumes, networks, healthchecks.\n"
            "- CI/CD pipelines — GitHub Actions, GitLab CI, build/test/deploy stages.\n"
            "- Kubernetes — deployments, services, ingress, configmaps, secrets.\n"
            "- Infrastructure-as-code — Terraform, Ansible, CloudFormation.\n"
            "- Monitoring — logging, metrics, alerting, health endpoints.\n"
            "- Security — least-privilege containers, scanned images, secret management.\n\n"
            "CODE QUALITY RULES:\n"
            "- Write ONLY the config for your assigned file.\n"
            "- Reference other services/configs by their actual filenames.\n"
            "- Keep configs DRY — use environment variables and shared config patterns.\n"
            "- Include health checks, proper logging, and resource limits.\n"
            "- Never hardcode secrets — use environment variables or mounted secrets.\n"
            "- Optimize for reproducible builds (pin versions, cache layers)."
        ),
    },

    "titan": {
        "name": "Titan",
        "role": "gamedev",
        "system": (
            "You are the 3D Game Development Specialist. You live and breathe Babylon.js, "
            "WebGL lifecycle management, matrices, vectors, particle systems, and shader pipelines.\n\n"
            "Your expertise covers:\n"
            "- Babylon.js scene management, cameras, lights, and materials.\n"
            "- WebGL shader programming (vertex + fragment).\n"
            "- 3D mathematics: transformations, quaternions, collision detection.\n"
            "- Physics engine integration.\n"
            "- Performance optimization for real-time rendering.\n\n"
            "CODE QUALITY RULES:\n"
            "- Write ONLY the code for your assigned file.\n"
            "- Import from other modules by their actual filenames.\n"
            "- Keep render loops efficient — avoid per-frame allocations.\n"
            "- Comment complex math transformations clearly."
        ),
    },

    "sage": {
        "name": "Sage",
        "role": "reasoner",
        "system": (
            "You are the Deep Reasoner, called upon only for deep, systemic technical challenges.\n\n"
            "Your scope is strictly limited to:\n"
            "- Complex debugging sessions (race conditions, memory leaks).\n"
            "- Difficult algorithmic optimization.\n"
            "- Compiler, parser, or highly abstract architecture logic.\n"
            "- Concurrency and parallelism design.\n\n"
            "Focus all your computing power on the specific code snippet or isolated file "
            "containing the problem. Do not waste tokens writing standard boilerplate — solve "
            "the logical core.\n\n"
            "CODE QUALITY RULES:\n"
            "- Write ONLY the code for your assigned file.\n"
            "- Include detailed comments explaining the algorithmic reasoning.\n"
            "- Consider edge cases, boundary conditions, and worst-case complexity.\n"
            "- Profile-aware: note O(n) complexity where relevant."
        ),
    },

    "sentry": {
        "name": "Sentry",
        "role": "validator",
        "system": (
            "You are the Cheap Validator. Your job is to act as a lightweight static analyzer "
            "and sanity gatekeeper.\n\n"
            "When given a piece of code or a file, you must instantly verify:\n"
            "1. Are all import statements valid and pointing to existing files/packages?\n"
            "2. Do the TypeScript/interfaces match between usage and declaration?\n"
            "3. Are there any obvious syntax errors or broken references?\n"
            "4. Are there missing error handlers or unhandled edge cases?\n\n"
            "Do not propose architectural rewrites. Output the corrected/validated code."
        ),
    },

    "quill": {
        "name": "Quill",
        "role": "docs",
        "system": (
            "You are the Documentation and Testing Assistant. You take functional, finalized "
            "files and write standard unit tests, integration tests, or markdown documentation.\n\n"
            "Test patterns you produce (infer the framework from the project, default to Jest/Vitest):\n"
            "1. Unit tests — cover pure functions, utility helpers, and data transformations.\n"
            "2. Component tests — render with minimal props, assert on output.\n"
            "3. Integration tests — wire 2-3 modules together and verify data flow.\n"
            "4. Edge cases — always include: empty input, null/undefined, boundary values, error paths.\n\n"
            "Documentation patterns:\n"
            "- README sections: Overview, Installation, Usage, API reference, Examples.\n"
            "- Inline JSDoc/TSDoc for exported functions and types.\n\n"
            "Hard rules:\n"
            "- Never modify source code. You only write test files and markdown files.\n"
            "- Keep test files next to the source they test."
        ),
    },

    "volt": {
        "name": "Volt",
        "role": "executor",
        "system": (
            "You are the Terminal Execution Engine, specialized in high-speed, direct interaction "
            "with the development environment. Your primary objective is to execute commands, "
            "manage dependencies, and perform automated maintenance tasks.\n\n"
            "You have full access to bash and the filesystem. When tasked with a job, immediately "
            "determine the most efficient command sequence to achieve the goal.\n\n"
            "When a command fails, analyze ONLY the immediate error output. Apply a single "
            "corrective action, retry, and report results with absolute brevity.\n\n"
            "If you modify static configuration files and the system hangs, automatically run "
            "a cache-clear or clean build command before verifying the task."
        ),
    },

    "aria": {
        "name": "Aria",
        "role": "ui-designer",
        "system": (
            "You are the Lead UI/UX Engineer and Tailwind Specialist. Your goal is to write "
            "production-ready, beautiful, and accessible web interfaces using Tailwind CSS. "
            "You combine the logic of a senior developer with the aesthetic precision of a "
            "world-class designer.\n\n"
            "Implementation rules (in priority order):\n"
            "1. Spacing & Breathability — generous padding (p-6, p-8), space-y-6, gap-6.\n"
            "2. Color Palette — rich grays/slates (zinc-950, slate-950), soft borders, subtle backgrounds.\n"
            "3. Typography — tracking-tight for headings, tracking-wide uppercase for labels.\n"
            "4. Micro-interactions — every button/link has transition-all duration-200 ease-in-out.\n"
            "5. Shadows — soft and layered (shadow-sm, shadow-xl shadow-black/5).\n"
            "6. Responsive — Mobile-First, grid-cols-1 md:grid-cols-2 lg:grid-cols-3.\n\n"
            "Output clean, modular code with orderly class lists (Layout -> Spacing -> Typography -> Visuals -> Interactivity).\n"
            "Never touch backend routing, API systems, or data persistence models."
        ),
    },

    "vega": {
        "name": "Vega",
        "role": "space-ui",
        "system": (
            "You are the Lead Sci-Fi UI/UX Visual Designer. Your mission is to write and implement "
            "gorgeous, production-ready frontend components with a premium tactical/space aesthetic "
            "using utility-first CSS (Tailwind).\n\n"
            "Design tokens:\n"
            "1. Surfaces — deep premium off-blacks (bg-zinc-950, bg-slate-950). Never flat #000000.\n"
            "2. Containers — glassmorphism (bg-zinc-900/50 backdrop-blur-md) with razor-thin borders (border-zinc-800/50).\n"
            "3. Accents — tactical neon (text-cyan-400, bg-emerald-500/10, border-indigo-500/30) with strict moderation.\n"
            "4. Spacing — whitespace is critical for complex data. Default to p-6, p-8, gap-6.\n"
            "5. Typography — tracking-widest uppercase font-mono for technical readouts, high-contrast data labels.\n"
            "6. Micro-interactions — every element feels alive (transition-all duration-200 ease-in-out, hover:scale-[1.01]).\n"
            "7. Responsive — fluid mobile-first (grid grid-cols-1 md:grid-cols-3 xl:grid-cols-4).\n\n"
            "Never touch backend state engines, API orchestrators, or game loop systems."
        ),
    },

    # ── Reviewers ─────────────────────────────────────────────

    "tess": {
        "name": "Tess",
        "role": "review-core",
        "system": (
            "You are the Core Code Reviewer. Your job is to audit code with extreme precision.\n\n"
            "You look strictly for:\n"
            "1. Memory leaks — uncleaned listeners, dangling timers, unresolved subscriptions, unclosed handles.\n"
            "2. Async race conditions — unhandled rejections, missing await, fire-and-forget without error handling.\n"
            "3. Security vulnerabilities — unsanitized input, missing auth, exposed secrets.\n"
            "4. Brittle coupling — circular dependencies, hidden side effects, implicit init order assumptions.\n\n"
            "Reject only for concrete problems — vague concerns are not grounds for rejection.\n\n"
            'Return ONLY JSON: {"approved": true/false, "feedback": "..."}'
        ),
    },

    "sara": {
        "name": "Sara",
        "role": "review-ui",
        "system": (
            "You are the UI Reviewer. You ensure frontend components are pixel-perfect, CSS layout "
            "rules are clean, responsive designs are correct, and UI styling matches the expected "
            "aesthetic.\n\n"
            "Your review checklist (in priority order):\n"
            "1. Responsiveness — does the layout adapt across mobile, tablet, desktop?\n"
            "2. Layout Flow — are flexbox/grid rules correct? Check overflow, collapsing margins.\n"
            "3. CSS Specificity — flag overly specific selectors, !important abuse.\n"
            "4. Accessibility — verify color contrast (4.5:1), focus outlines, semantic headings.\n"
            "5. Performance — flag layout thrashing, expensive paint operations.\n"
            "6. Component Aesthetics — check visual consistency (spacing, typography, color palette).\n\n"
            "Reject only for concrete problems.\n\n"
            'Return ONLY JSON: {"approved": true/false, "feedback": "..."}'
        ),
    },

    "vera": {
        "name": "Vera",
        "role": "review-infra",
        "system": (
            "You are the Infrastructure and DevOps Reviewer. You audit configs, deployment scripts, "
            "and infrastructure definitions for reliability, security, and best practices.\n\n"
            "You look strictly for:\n"
            "1. Security — hardcoded secrets or credentials, exposed ports, missing TLS, "
            "overly permissive CORS, containers running as root.\n"
            "2. Reliability — missing health checks, no restart policies, missing resource limits, "
            "no graceful shutdown handling.\n"
            "3. Scalability — single points of failure, no horizontal scaling support, "
            "missing caching layers.\n"
            "4. Observability — missing logging, no metrics endpoints, no alerting configuration.\n"
            "5. Configuration drift — hardcoded values that should be environment variables, "
            "inconsistent naming between services.\n"
            "6. Network exposure — services reachable that shouldn't be, missing network isolation, "
            "incorrect port mappings.\n\n"
            "Reject only for concrete problems — vague concerns are not grounds for rejection.\n\n"
            'Return ONLY JSON: {"approved": true/false, "feedback": "..."}'
        ),
    },

    "orion": {
        "name": "Orion",
        "role": "space-review",
        "system": (
            "You are the Tactical UI/UX Reviewer, specialized in premium, data-dense, and highly "
            "performant sci-fi interfaces (Stellar/SaaS aesthetic).\n\n"
            "Your review checklist:\n"
            "1. Space Aesthetic — verify deep tones (zinc-950, slate-950), glassmorphism cards, no flat black.\n"
            "2. Micro-interactions — every interactive element has transition-all duration-200 ease-in-out.\n"
            "3. Typography — headers use tracking-tight or tracking-widest uppercase font-mono.\n"
            "4. Responsive grid — adapts across mobile/tablet/widescreen. Flag hardcoded widths.\n"
            "5. Accessibility — contrast ratios against dark backdrops (4.5:1), focus outlines intact.\n"
            "6. DOM Performance — animations hardware-accelerated (transform, opacity). Flag heavy filters.\n\n"
            "Reject only for concrete problems.\n\n"
            'Return ONLY JSON: {"approved": true/false, "feedback": "..."}'
        ),
    },
}

# ── Task routing: task_type → (producer_key, reviewer_key) ────

TASK_ROUTING: Dict[str, tuple] = {
    "backend":     ("chris",  "tess"),
    "code":        ("chris",  "tess"),
    "crud":        ("chris",  "tess"),
    "frontend":    ("fia",    "sara"),
    "design":      ("fia",    "sara"),
    "network":     ("nova",   "vera"),
    "security":    ("nova",   "vera"),
    "api":         ("nova",   "vera"),
    "multiplayer": ("nova",   "vera"),
    "websocket":   ("nova",   "vera"),
    "devops":      ("atlas",  "vera"),
    "infra":       ("atlas",  "vera"),
    "docker":      ("atlas",  "vera"),
    "game":        ("titan",  "tess"),
    "3d":          ("titan",  "tess"),
    "shader":      ("titan",  "tess"),
    "babylon":     ("titan",  "tess"),
    "physics":     ("titan",  "tess"),
    "algorithm":   ("sage",   "tess"),
    "debug":       ("sage",   "tess"),
    "complex":     ("sage",   "tess"),
    "concurrency": ("sage",   "tess"),
    "validate":    ("sentry", "tess"),
    "check":       ("sentry", "tess"),
    "test":        ("quill",  "sara"),
    "documentation":("quill", "sara"),
    "execute":     ("volt",   "vera"),
    "bash":        ("volt",   "vera"),
    "script":      ("volt",   "vera"),
    "tailwind":    ("aria",   "sara"),
    "component":   ("aria",   "sara"),
    "ui":          ("aria",   "sara"),
    "space-ui":    ("vega",   "orion"),
    "sci-fi":      ("vega",   "orion"),
    "tactical":    ("vega",   "orion"),
    "space-review":("vega",   "orion"),
}
DEFAULT_ROUTE: tuple = ("chris", "tess")


def _route(task_type: str) -> tuple:
    """Return (producer_key, reviewer_key) for a task_type."""
    return TASK_ROUTING.get((task_type or "").lower().strip(), DEFAULT_ROUTE)


# ═══════════════════════════════════════════════════════════════
# Utilities
# ═══════════════════════════════════════════════════════════════

def _extract_json(text: str) -> Optional[Any]:
    """Extract a JSON object from an LLM response that may have markdown fences."""
    text = text.strip()
    if text.startswith("{") or text.startswith("["):
        try:
            return json.loads(text)
        except Exception:
            pass
    m = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except Exception:
            pass
    first, last = text.find("{"), text.rfind("}")
    if first != -1 and last > first:
        try:
            return json.loads(text[first:last + 1])
        except Exception:
            pass
    return None


def _estimate_task_tokens(task: Dict) -> int:
    """Estimate how many output tokens a task will require.

    Port of the frontend _estimateTokens() — uses task-type profiles,
    description feature count, and filename to produce a rough but
    directionally-correct estimate. Used by the auto-split check.
    """
    desc = ((task.get("description") or "") + " " + (task.get("title") or "")).strip()
    if not desc:
        return 500

    tt = (task.get("task_type") or "").lower().strip()
    fname = (task.get("filename") or "").lower().strip()

    # Feature count: description clauses separated by commas, "and",
    # numbered items, semicolons, newlines.
    parts = re.split(r'[,;]|\band\b|\balso\b|\n|\d+[.)]', desc)
    features = [p.strip() for p in parts if len(p.strip()) > 8]
    feature_count = max(1, len(features))

    words = len(desc.split())

    profiles = {
        "frontend":   {"base": 600, "per_feat": 550},
        "design":     {"base": 600, "per_feat": 550},
        "ui":         {"base": 500, "per_feat": 450},
        "space-ui":   {"base": 500, "per_feat": 500},
        "backend":    {"base": 400, "per_feat": 350},
        "code":       {"base": 400, "per_feat": 350},
        "api":        {"base": 400, "per_feat": 350},
        "network":    {"base": 400, "per_feat": 350},
        "devops":     {"base": 200, "per_feat": 180},
        "infra":      {"base": 200, "per_feat": 180},
        "test":       {"base": 300, "per_feat": 250},
        "docs":       {"base": 200, "per_feat": 180},
        "execute":    {"base": 100, "per_feat": 80},
    }
    p = profiles.get(tt, {"base": 400, "per_feat": 350})
    est = p["base"] + (feature_count * p["per_feat"])

    if fname.endswith((".html", ".htm")):
        est = round(est * 1.3)
    elif fname.endswith((".css", ".scss")):
        est = round(est * 1.1)

    word_est = round(words * 4 * 1.3)
    return max(est, word_est)


def _resolve_agent_candidates(agent_key: str, owner: str):
    """Resolve LLM candidates for a specific agent.

    If the agent has a custom endpoint+model in factory_agent_models settings,
    resolve that specific endpoint. Otherwise fall back to the default task chain.
    """
    from src.task_endpoint import resolve_task_candidates

    if agent_key:
        try:
            from src.settings import get_setting
            agent_models = get_setting("factory_agent_models", {}) or {}
            cfg = agent_models.get(agent_key)
            if cfg and cfg.get("endpoint_id"):
                from src.endpoint_resolver import resolve_endpoint_by_id
                resolved = resolve_endpoint_by_id(
                    cfg["endpoint_id"], model=cfg.get("model"), owner=owner,
                )
                if resolved:
                    return [resolved]
        except Exception as e:
            logger.warning(f"Factory: agent model resolution failed for {agent_key}: {e}")

    return resolve_task_candidates(owner=owner)


async def _llm(messages: List[Dict], owner: str, timeout: int = 90,
               max_tokens: int = 4096, agent_key: str = "") -> str:
    """Call the configured task endpoint with fallback chain.

    Bypasses the interactive-quiet gate (task_llm_call_async) so factory
    tasks run immediately even while the user has the browser open.
    Uses max_retries=1 so a slow endpoint doesn't compound across retries.

    If agent_key is set and the factory_agent_models setting has a custom
    endpoint+model for that agent, use it instead of the default task chain.
    """
    from src.llm_core import llm_call_async_with_fallback

    candidates = _resolve_agent_candidates(agent_key, owner)

    if not candidates:
        raise RuntimeError("No LLM endpoint configured for factory tasks")
    return await llm_call_async_with_fallback(
        candidates, messages=messages,
        timeout=timeout, max_tokens=max_tokens,
        max_retries=1,
        workload="background",
    )

def _get_system_prompt(agent_key: str) -> str:
    """Get the system prompt for an agent — custom override or built-in default."""
    try:
        from src.settings import get_setting
        custom = get_setting("factory_agent_prompts", {}) or {}
        if custom.get(agent_key):
            return custom[agent_key]
    except Exception:
        pass
    return AGENTS.get(agent_key, {}).get("system", "")


DEFAULT_MAX_TOKENS = 16384

def _get_max_tokens(agent_key: str) -> int:
    """Get max_tokens for an agent — custom override or default."""
    try:
        from src.settings import get_setting
        custom = get_setting("factory_agent_max_tokens", {}) or {}
        if agent_key in custom and custom[agent_key]:
            return int(custom[agent_key])
    except Exception:
        pass
    return DEFAULT_MAX_TOKENS


async def _call_agent(agent_key: str, user_prompt: str, owner: str,
                      timeout: int = 90) -> str:
    """Call a named agent with its system prompt."""
    return await _llm(
        [
            {"role": "system", "content": _get_system_prompt(agent_key)},
            {"role": "user", "content": user_prompt},
        ],
        owner=owner,
        timeout=timeout,
        max_tokens=_get_max_tokens(agent_key),
        agent_key=agent_key,
    )


# ═══════════════════════════════════════════════════════════════
# Phase 1: Planning (Fredrix)
# ═══════════════════════════════════════════════════════════════

async def plan_project(project_id: int, owner: str = "default") -> bool:
    """Decompose the project description into tasks via LLM, then auto-start."""
    project = _service.get_project(project_id)
    if not project:
        return False

    desc = project.get("description") or ""
    if not desc.strip():
        return False

    logger.info(f"Factory: Fredrix planning project {project_id} ({desc[:60]}...)")

    try:
        raw = await _call_agent("fredrix", f"Project description:\n{desc}", owner, timeout=120)
    except Exception as e:
        logger.error(f"Factory: planner failed for project {project_id}: {e}")
        _service.set_project_status(project_id, "failed")
        return False

    plan = _extract_json(raw)
    if not plan or not isinstance(plan.get("tasks"), list) or not plan["tasks"]:
        logger.error(f"Factory: invalid plan for project {project_id}:\n{raw[:300]}")
        _service.set_project_status(project_id, "failed")
        return False

    # Extract architecture directives from the planner output (folded in to
    # avoid a second blocking LLM call before the project can start).
    arch_directives = plan.get("architecture", "")
    if arch_directives:
        logger.info(f"Factory: architecture directives extracted ({len(arch_directives)} chars)")
        _service._log_event_safe(project_id, None, "Architecture directives generated",
                                 event_type="architecture_done")

    # Create nodes + dependency edges
    node_ids: List[int] = []
    for i, t in enumerate(plan["tasks"]):
        if not isinstance(t, dict):
            continue
        deps_indices = t.get("dependencies", []) or []
        deps = [node_ids[d] for d in deps_indices if isinstance(d, int) and 0 <= d < len(node_ids)]
        try:
            node = _service.add_node(
                project_id=project_id,
                task_type=t.get("task_type", "backend"),
                title=t.get("title", f"Task {i + 1}"),
                description=t.get("description", ""),
                dependencies=deps,
                assigned_agent=_route(t.get("task_type", ""))[0].capitalize(),
                filename=t.get("filename", ""),
            )
            node_ids.append(node["id"])
        except Exception as e:
            logger.error(f"Factory: add_node {i} failed: {e}")
            node_ids.append(0)

    logger.info(f"Factory: planned {len(node_ids)} tasks for project {project_id}")

    # Start the project immediately — no separate architect call blocking
    try:
        _service.start_project(project_id)
    except Exception as e:
        logger.error(f"Factory: start failed for project {project_id}: {e}")
        return False

    launch(project_id, owner, arch_directives)
    return True


async def iterate_project(project_id: int, prompt: str, owner: str = "default") -> bool:
    """Add new tasks to an existing project based on user's new prompt.

    Works on completed or in-progress projects. The planner sees existing
    files (names + descriptions) and plans ONLY new work.
    """
    project = _service.get_project(project_id)
    if not project:
        return False

    existing_nodes = _service.get_nodes(project_id)
    existing_summary = _build_project_context(project_id)

    # Re-open the project if it was completed
    if project.get("status") == "completed":
        _service.set_project_status(project_id, "running")

    # Build the iteration planner prompt
    user_prompt = (
        f"Original project: {project.get('description', '')}\n\n"
        f"Files already built:\n{existing_summary}\n\n"
        f"New request from user: {prompt}\n\n"
        f"Plan ONLY the new tasks needed to fulfil this request. "
        f"Do NOT recreate existing files — they are already working. "
        f"New tasks may depend on existing completed tasks."
    )

    logger.info(f"Factory: iterating project {project_id} — {prompt[:60]}...")

    try:
        raw = await _call_agent("fredrix", user_prompt, owner, timeout=120)
    except Exception as e:
        logger.error(f"Factory: iterate planner failed for project {project_id}: {e}")
        return False

    plan = _extract_json(raw)
    if not plan or not isinstance(plan.get("tasks"), list) or not plan["tasks"]:
        logger.error(f"Factory: iterate returned invalid plan:\n{raw[:300]}")
        return False

    # Map old node ids for dependency edges
    existing_ids = [n["id"] for n in existing_nodes]

    node_ids: List[int] = []
    for i, t in enumerate(plan["tasks"]):
        if not isinstance(t, dict):
            continue
        deps_indices = t.get("dependencies", []) or []
        deps = []
        for d in deps_indices:
            if isinstance(d, int) and 0 <= d < len(node_ids):
                deps.append(node_ids[d])
        try:
            node = _service.add_node(
                project_id=project_id,
                task_type=t.get("task_type", "backend"),
                title=t.get("title", f"Task {i + 1}"),
                description=t.get("description", ""),
                dependencies=deps,
                assigned_agent=_route(t.get("task_type", ""))[0].capitalize(),
                filename=t.get("filename", ""),
            )
            node_ids.append(node["id"])
        except Exception as e:
            logger.error(f"Factory: iterate add_node {i} failed: {e}")
            node_ids.append(0)

    logger.info(f"Factory: iteration added {len(node_ids)} new tasks to project {project_id}")

    arch = plan.get("architecture", "")
    _service._log_event_safe(project_id, None, f"Iteration: {len(node_ids)} new tasks added",
                             event_type="iteration_planned")

    # Mark new root tasks (no incoming edges) as ready so the orchestrator picks them up
    _service.mark_ready_tasks(project_id)

    launch(project_id, owner, arch)
    return True

def _build_project_context(project_id: int) -> str:
    """Build a summary of all files in the project for agent context."""
    nodes = _service.get_nodes(project_id)
    lines = []
    for n in nodes:
        fname = n.get("filename") or "?"
        status = n.get("status", "?")
        title = n.get("title", "")
        lines.append(f"  - {fname} [{status}] — {title}")
    return "\n".join(lines) if lines else "  (no files yet)"


def _get_dependency_code(project_id: int, task: Dict) -> str:
    """Get full output from completed tasks this task depends on."""
    deps = task.get("dependencies") or []
    if not deps:
        return ""
    nodes = _service.get_nodes(project_id)
    dep_nodes = {n["id"]: n for n in nodes}
    parts = []
    for dep_id in deps:
        dep = dep_nodes.get(dep_id)
        if not dep or dep.get("status") != "completed":
            continue
        fname = dep.get("filename") or dep.get("title", f"task_{dep_id}")
        output = _extract_output(dep.get("result"))
        if output:
            parts.append(f"--- {fname} (existing code, dependency) ---\n{output[:3000]}")
    return "\n\n".join(parts)


def _get_feedback_code(project_id: int, feedback: str) -> str:
    """Find filenames mentioned in review feedback and fetch their code."""
    if not feedback:
        return ""
    nodes = _service.get_nodes(project_id)
    parts = []
    for n in nodes:
        fname = n.get("filename")
        if not fname or n.get("status") != "completed":
            continue
        if fname.lower() in feedback.lower():
            output = _extract_output(n.get("result"))
            if output:
                parts.append(f"--- {fname} (referenced in review feedback) ---\n{output[:3000]}")
    return "\n\n".join(parts)


async def _produce(agent_key: str, task: Dict, feedback: str,
                   owner: str, project_desc: str, arch: str,
                   project_id: int = 0) -> str:
    """Routed producer agent produces output for a task.

    If the model returns truncated output (e.g. it hit a per-request output
    token cap and cut off mid-tag), the factory_continuation module sends
    "continue from here" turns and stitches the chunks together. This defeats
    the "truncated HTML -> rejected by reviewer -> human_intervention" loop.
    """
    prompt = f"Project: {project_desc}\n\nTask: {task.get('title', '')}\nDescription: {task.get('description', '')}\n"

    # Include the filename this task should produce
    fname = task.get("filename")
    if fname:
        prompt += f"Output file: {fname}\n"

    # Project file context — all files in the project
    if project_id:
        ctx = _build_project_context(project_id)
        prompt += f"\nProject files (import from these by filename):\n{ctx}\n"

    if arch:
        prompt += f"\nArchitecture directives:\n{arch}\n"

    # Dependency code — full output from direct dependencies
    if project_id:
        dep_code = _get_dependency_code(project_id, task)
        if dep_code:
            prompt += f"\n\nExisting dependency code:\n{dep_code}\n"

    prompt += (
        "\n\n=== OUTPUT FORMAT — CRITICAL ===\n"
        "Output ONLY the raw file content. No markdown fences (no ```), no explanations, "
        "no planning text, no commentary, no 'Here is the file:' preamble. "
        "Start directly with the first line of the file and end with the last line. "
        "If this is code, the first character must be an import, a comment, or code — never prose.\n"
        "Write the COMPLETE file content — do NOT truncate or use placeholders or ellipsis. "
        "If the file is long, prioritize completing all functions over adding extensive comments."
    )

    if feedback:
        truncation_hint = ""
        if any(w in feedback.lower() for w in ("truncat", "incomplete", "missing", "cut off")):
            truncation_hint = "\nYour previous output was truncated. Be more concise if needed, but output the COMPLETE file — no placeholders, no '...' shortcuts."
        prompt += f"\n\nPrevious attempt was rejected. Feedback: {feedback}\nAddress this and try again.{truncation_hint}"
        # Include code from files mentioned in the feedback
        if project_id:
            fb_code = _get_feedback_code(project_id, feedback)
            if fb_code:
                prompt += f"\n\nRelevant existing code:\n{fb_code}"

    # Per-call read timeout: bounded so multiple continuation rounds fit inside
    # the outer TASK_TIMEOUT wait_for guard in _process_task.
    call_timeout = min(_PRODUCE_CALL_TIMEOUT, TASK_TIMEOUT - 15)
    sys_prompt = _get_system_prompt(agent_key)
    max_tokens = min(_get_max_tokens(agent_key), _get_produce_max_tokens())

    async def _llm_adapter(messages):
        return await _llm(
            messages, owner=owner, timeout=call_timeout,
            max_tokens=max_tokens, agent_key=agent_key,
        )

    from services.factory_continuation import produce_with_continuation, strip_code_fences
    output = await produce_with_continuation(
        system_prompt=sys_prompt,
        user_prompt=prompt,
        llm_call=_llm_adapter,
        fname=fname or "",
    )
    return strip_code_fences(output)


async def _review(agent_key: str, task: Dict, output: str, owner: str) -> Dict:
    """Routed reviewer agent evaluates the output."""
    prompt = (
        f"Task: {task.get('title', '')}\n"
        f"Description: {task.get('description', '')}\n\n"
        f"THE FULL OUTPUT TO REVIEW (complete, not truncated):\n{output[:16000]}"
    )
    raw = await _llm(
        [
            {"role": "system", "content": _get_system_prompt(agent_key)},
            {"role": "user", "content": prompt},
        ],
        owner=owner,
        max_tokens=_get_max_tokens(agent_key),
        agent_key=agent_key,
    )
    result = _extract_json(raw)
    if result and isinstance(result, dict):
        return {
            "approved": bool(result.get("approved", False)),
            "feedback": result.get("feedback", ""),
        }
    logger.warning(f"Factory: unparseable review from {agent_key}: {raw[:200]}")
    return {"approved": True, "feedback": ""}


TASK_TIMEOUT = 600  # total budget per produce call (incl. continuation rounds)
_PRODUCE_CALL_TIMEOUT = 90  # per-LLM-call read timeout inside _produce
def _get_produce_max_tokens() -> int:
    """Per-call token cap for producer agent calls.

    Lower values mean faster per-call response times (good for continuation),
    higher values mean fewer continuation rounds needed. 16384 is the default
    that provides a generous output budget for complete files while still
    fitting inside most model context windows.

    Override via the `factory_produce_max_tokens` setting.
    """
    try:
        from src.settings import get_setting
        v = get_setting("factory_produce_max_tokens", None)
        if v is not None:
            return max(1024, int(v))
    except Exception:
        pass
    return 16384


# ═══════════════════════════════════════════════════════════════
# Auto-split support
# ═══════════════════════════════════════════════════════════════

async def _try_split_task(project_id: int, task: Dict, owner: str) -> bool:
    """Check if a task is too large for a single produce call and auto-split it.

    Uses _estimate_task_tokens to predict output size. If the estimate exceeds
    85% of the produce token budget, calls the planner LLM to decompose the task
    into 2-3 smaller sub-tasks — each producing a separate file. Creates the
    sub-tasks as new nodes, re-routes dependencies, and marks the original as
    completed (split).

    Returns True if the task was split (caller should skip normal processing),
    False if the task should proceed normally.
    """
    est = _estimate_task_tokens(task)
    budget = _get_produce_max_tokens()
    threshold = int(budget * 0.85)

    if est <= threshold:
        return False

    fname = (task.get("filename") or "").strip()
    if not fname:
        return False  # can't split without a filename

    task_id = task["id"]
    logger.info(f"Factory: task {task_id} estimated ~{est} tokens > {threshold} threshold — attempting auto-split")

    # Build a split prompt for the planner
    split_prompt = (
        f"This task is too large for a single code generation call "
        f"(estimated ~{est} tokens, max budget {budget} tokens).\n\n"
        f"Split it into 2-3 smaller tasks, each producing a SEPARATE file.\n\n"
        f"Original task:\n"
        f"  Title: {task.get('title', '')}\n"
        f"  Description: {task.get('description', '')}\n"
        f"  File: {fname}\n\n"
        f"Split rules:\n"
        f"- Break features into logical groups, one group per file.\n"
        f"- Name files descriptively based on what they contain.\n"
        f"- Each file must be self-contained (own imports, own init).\n"
        f"- Distribute features so each file is under ~250 lines.\n"
        f"- Keep the same base directory as the original file.\n\n"
        f"Return ONLY JSON — no markdown fences:\n"
        f'{{"split": true, "tasks": [{{"title": "...", "description": "...", "filename": "..."}}]}}\n\n'
        f"If the task genuinely cannot be split (single-purpose, config file), return:\n"
        f'{{"split": false}}'
    )

    try:
        raw = await asyncio.wait_for(
            _call_agent("fredrix", split_prompt, owner, timeout=60),
            timeout=65,
        )
    except Exception as e:
        logger.warning(f"Factory: splitter LLM call failed for task {task_id}: {e}")
        return False

    plan = _extract_json(raw)
    if not plan or not plan.get("split") or not isinstance(plan.get("tasks"), list):
        logger.info(f"Factory: splitter returned no split for task {task_id}")
        return False

    sub_task_defs = plan["tasks"]
    # Filter to valid sub-tasks with filenames
    sub_task_defs = [t for t in sub_task_defs if isinstance(t, dict) and t.get("filename")]
    if len(sub_task_defs) < 2:
        logger.info(f"Factory: splitter produced {len(sub_task_defs)} sub-tasks for task {task_id} — need at least 2")
        return False

    # Mark original task as running (state machine: ready -> running -> completed)
    try:
        _service.update_task_status(task_id, "running")
    except Exception:
        pass  # already running or other state — proceed anyway

    # Create sub-task nodes
    deps = task.get("dependencies") or []
    sub_ids: List[int] = []
    for st in sub_task_defs:
        try:
            node = _service.add_node(
                project_id=project_id,
                task_type=task.get("task_type", "backend"),
                title=st.get("title", f"Split sub-task"),
                description=st.get("description", ""),
                dependencies=list(deps),  # inherit original's deps
                assigned_agent=task.get("assigned_agent", ""),
                filename=st.get("filename", ""),
            )
            sub_ids.append(node["id"])
        except Exception as e:
            logger.error(f"Factory: failed to create split sub-task: {e}")

    if not sub_ids:
        logger.warning(f"Factory: no sub-tasks created for task {task_id} — proceeding normally")
        return False

    # Re-route: tasks that depended on the original now depend on all sub-tasks
    _service.reroute_dependencies(project_id, task_id, sub_ids)

    # Mark sub-tasks as ready (their inherited deps should already be completed)
    for sid in sub_ids:
        try:
            _service.update_task_status(sid, "ready")
        except Exception:
            pass  # state machine might reject if deps aren't met — orchestrator will promote later

    # Mark original task as completed with split metadata
    split_summary = ", ".join(f"T{sid}" for sid in sub_ids)
    _service.complete_task(task_id, result={
        "output": f"(Auto-split into {len(sub_ids)} sub-tasks: {split_summary} — estimated {est} tokens exceeded {threshold} threshold)",
        "split_into": sub_ids,
        "estimated_tokens": est,
        "producer": "Auto-Splitter",
        "reviewer": "—",
    })

    logger.info(f"Factory: task {task_id} auto-split into {len(sub_ids)} sub-tasks: {split_summary}")
    _service._log_event_safe(
        project_id, task_id,
        f"Task auto-split into {len(sub_ids)} sub-tasks ({split_summary}) — was ~{est} tokens, budget {budget}",
        event_type="task_auto_split",
    )

    return True


async def _process_task(project_id: int, task: Dict, owner: str,
                        project_desc: str, arch: str) -> None:
    """Run the Produce → Review pipeline for a single task."""
    task_id = task["id"]

    # ── Auto-split check ──────────────────────────────────────
    # Before producing, check if this task is too large for a single
    # produce call. If the token estimate exceeds 85% of the budget,
    # decompose it into smaller sub-tasks automatically.
    try:
        if await _try_split_task(project_id, task, owner):
            return  # Task was split — sub-tasks are now ready and will
                    # be picked up by the orchestrator loop on the next pass.
    except Exception as e:
        logger.warning(f"Factory: auto-split check failed for task {task_id}: {e} — proceeding with normal production")

    # ── Normal Produce → Review pipeline ──────────────────────
    producer_key, reviewer_key = _route(task.get("task_type"))
    producer_name = AGENTS[producer_key]["name"]
    reviewer_name = AGENTS[reviewer_key]["name"]
    _service.update_task_status(task_id, "running")

    logger.info(
        f"Factory: task {task_id} [{task.get('task_type', '?')}] "
        f"→ {producer_name} (produce) → {reviewer_name} (review)"
    )

    feedback = ""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        logger.info(f"Factory: task {task_id} attempt {attempt}/{MAX_ATTEMPTS}")

        _service.set_task_progress(task_id, f"{producer_name} producing",
                                   attempt=attempt, max_attempts=MAX_ATTEMPTS)

        try:
            output = await asyncio.wait_for(
                _produce(producer_key, task, feedback, owner, project_desc, arch, project_id),
                timeout=TASK_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.warning(f"Factory: produce timed out for task {task_id} on attempt {attempt}")
            _service.fail_task(task_id, error=f"Produce timed out after {TASK_TIMEOUT}s")
            return
        except Exception as e:
            logger.error(f"Factory: produce failed for task {task_id}: {e}")
            _service.fail_task(task_id, error=f"Produce error: {e}")
            return

        # Pre-review truncation guard: if the output STILL looks truncated after
        # the continuation module exhausted its rounds, don't waste a review round
        # (the reviewer will reject truncated output, burning all MAX_ATTEMPTS).
        # Log a warning and proceed anyway — the reviewer may still find it useful.
        from services.factory_continuation import looks_truncated
        fname = task.get("filename") or ""
        if looks_truncated(output, fname):
            logger.warning(
                f"Factory: task {task_id} output still truncated after continuation "
                f"(len={len(output)}, file={fname}). Proceeding to review — "
                f"reviewer may reject."
            )
            _service.set_task_progress(
                task_id,
                f"Output may be incomplete ({len(output)} chars) — under review",
                attempt=attempt, max_attempts=MAX_ATTEMPTS,
            )

        _service.set_task_progress(task_id, f"{reviewer_name} reviewing",
                                   attempt=attempt, max_attempts=MAX_ATTEMPTS)

        try:
            review = await asyncio.wait_for(
                _review(reviewer_key, task, output, owner),
                timeout=30,
            )
        except asyncio.TimeoutError:
            logger.warning(f"Factory: review timed out for task {task_id}")
            review = {"approved": True, "feedback": ""}
        except Exception as e:
            logger.error(f"Factory: review failed for task {task_id}: {e}")
            review = {"approved": True, "feedback": ""}

        if review["approved"]:
            logger.info(f"Factory: task {task_id} approved on attempt {attempt}")
            _service.complete_task(task_id, result={
                "output": output, "attempts": attempt,
                "producer": producer_name,
                "reviewer": reviewer_name,
            })
            return

        feedback = review.get("feedback", "")
        fb_short = feedback[:80] if feedback else "no details"
        logger.info(f"Factory: task {task_id} rejected by {reviewer_name}: {feedback[:100]}")
        if attempt < MAX_ATTEMPTS:
            _service.set_task_progress(task_id, f"Rejected by {reviewer_name}, retrying",
                                       attempt=attempt + 1, max_attempts=MAX_ATTEMPTS,
                                       detail=fb_short)

    _service.update_task_status(task_id, "human_intervention")
    reason = (feedback or "Reviewer rejected output without specific feedback")[:500]
    _service.set_task_error(task_id, error=reason)
    _service._log_event_safe(project_id, task_id,
                             f"Blocked after {MAX_ATTEMPTS} attempts — last feedback: {reason}"
                             f" (producer: {AGENTS[producer_key]['name']}, "
                             f"reviewer: {AGENTS[reviewer_key]['name']})")


# ═══════════════════════════════════════════════════════════════
# Delivery compilation
# ═══════════════════════════════════════════════════════════════

import os
import zipfile
import re as _re
from src.constants import DATA_DIR

_DELIVERY_DIR = os.path.join(DATA_DIR, "factory", "deliveries")

_EXT_BY_TYPE = {
    "backend": ".py", "code": ".py", "test": ".py",
    "frontend": ".html", "design": ".html", "ui": ".html",
    "network": ".py", "security": ".py", "api": ".py",
    "devops": ".yml", "infra": ".yml",
    "docs": ".md",
}


def _slugify(text: str) -> str:
    s = _re.sub(r'[^\w\.-]+', '_', text or "").strip("_")
    return s or "output"


def _resolve_filename(node: Dict, used: set) -> str:
    """Determine the output filename for a node, avoiding collisions."""
    raw = (node.get("filename") or "").strip()
    if not raw:
        raw = _slugify(node.get("title", "output"))
    # Ensure it has an extension
    if "." not in os.path.basename(raw):
        ext = _EXT_BY_TYPE.get((node.get("task_type") or "").lower(), ".txt")
        raw += ext
    # Sanitize — no path traversal
    raw = os.path.basename(raw)
    # Collision avoidance
    if raw.lower() in used:
        base, ext = os.path.splitext(raw)
        i = 2
        while f"{base}_{i}{ext}".lower() in used:
            i += 1
        raw = f"{base}_{i}{ext}"
    used.add(raw.lower())
    return raw


def _extract_output(result) -> str:
    """Extract the text output from a node's result JSON."""
    if not result:
        return ""
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        return result.get("output") or result.get("text") or ""
    return str(result)


def compile_delivery(project_id: int) -> Optional[str]:
    """Build a ZIP of all completed task outputs. Returns the zip path."""
    project = _service.get_project(project_id)
    if not project:
        return None

    nodes = _service.get_nodes(project_id)
    completed = [n for n in nodes if n.get("status") == "completed"]
    if not completed:
        return None

    os.makedirs(_DELIVERY_DIR, exist_ok=True)
    zip_path = os.path.join(_DELIVERY_DIR, f"{project_id}.zip")
    used_names: set = set()

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        # README
        readme_lines = [
            f"# {project.get('title', f'Project {project_id}')}",
            f"",
            f"{project.get('description', '')}",
            f"",
            f"## Files",
            f"",
        ]
        for n in completed:
            fname = _resolve_filename(n, used_names)
            output = _extract_output(n.get("result"))
            zf.writestr(fname, output)
            agent = n.get("assigned_agent") or n.get("task_type") or "?"
            readme_lines.append(f"- **{fname}** — {n.get('title', '')} ({agent})")

        readme_lines.extend(["", f"## Incomplete Tasks", ""])
        for n in nodes:
            if n.get("status") != "completed":
                readme_lines.append(f"- [{n.get('status', '?')}] {n.get('title', '')}")

        zf.writestr("README.md", "\n".join(readme_lines))

    logger.info(f"Factory: delivery compiled for project {project_id} "
                f"({len(completed)} files → {zip_path})")
    _service._log_event_safe(project_id, None, f"Delivery compiled ({len(completed)} files)",
                             event_type="delivery_compiled")
    return zip_path


# ═══════════════════════════════════════════════════════════════
# Orchestrator loop
# ═══════════════════════════════════════════════════════════════

async def _orchestrator_loop(project_id: int, owner: str,
                             arch: str = "", autonomous: bool = False) -> None:
    """Main loop: find ready tasks, process them, repeat until done or stuck."""
    logger.info(f"Factory: orchestrator started for project {project_id}")
    _auto_iteration = 0

    while True:
        try:
            project = _service.get_project(project_id)
        except Exception:
            break
        if not project:
            break
        status = project.get("status")
        if status in ("completed", "cancelled", "failed", "paused"):
            logger.info(f"Factory: orchestrator stopping — project {project_id} is {status}")
            break

        # Recover tasks left 'running' by a previous orchestrator that died
        # mid-produce (e.g. server restart). Re-queue anything stale so the
        # loop can pick it up instead of orphaning it.
        try:
            requeued = _service.requeue_stale_running(project_id, TASK_TIMEOUT + 60)
            if requeued:
                logger.info(f"Factory: re-queued {requeued} stale running task(s) in project {project_id}")
        except Exception as e:
            logger.warning(f"Factory: stale-run requeue failed for project {project_id}: {e}")

        ready = _service.get_next_ready_tasks(project_id)
        if not ready:
            dag = _service.get_dag(project_id)
            if dag.get("pending_tasks", 0) == 0 and dag.get("running_tasks", 0) == 0:
                # Autonomous mode: review and iterate
                if autonomous and _auto_iteration < MAX_AUTO_ITERATIONS:
                    _auto_iteration += 1
                    logger.info(f"Factory: project {project_id} — auto-iteration {_auto_iteration}/{MAX_AUTO_ITERATIONS}")
                    _service._log_event_safe(project_id, None,
                                             f"Auto-iteration {_auto_iteration}: reviewing project output")
                    try:
                        ctx = _build_project_context(project_id)
                        review_prompt = (
                            f"Original project: {project.get('description', '')}\n\n"
                            f"Files already built:\n{ctx}\n\n"
                            f"You are reviewing a completed project. "
                            f"Look at what was built and decide if more work is needed.\n\n"
                            f"If the project is complete and working — respond with:\n"
                            f'{{"complete": true}}\n\n'
                            f"If features are missing, bugs exist, or improvements are needed — "
                            f"plan new tasks using the standard format:\n"
                            f'{{"tasks": [...], "architecture": "..."}}'
                        )
                        raw = await _call_agent("fredrix", review_prompt, owner, timeout=90)
                        plan = _extract_json(raw)
                        if plan and plan.get("complete"):
                            logger.info(f"Factory: project {project_id} — reviewer says complete")
                            _service._log_event_safe(project_id, None, "Auto-review: project is complete")
                            break
                        if plan and isinstance(plan.get("tasks"), list) and plan["tasks"]:
                            # Add new tasks (reuse iterate_project's task creation logic)
                            existing_ids = [n["id"] for n in _service.get_nodes(project_id)]
                            for t in plan["tasks"]:
                                if not isinstance(t, dict):
                                    continue
                                try:
                                    _service.add_node(
                                        project_id=project_id,
                                        task_type=t.get("task_type", "backend"),
                                        title=t.get("title", "New task"),
                                        description=t.get("description", ""),
                                        dependencies=[],  # new tasks don't depend on old ones
                                        assigned_agent=_route(t.get("task_type", ""))[0].capitalize(),
                                        filename=t.get("filename", ""),
                                    )
                                except Exception:
                                    pass
                            _service.set_project_status(project_id, "running")
                            _service.mark_ready_tasks(project_id)
                            arch = plan.get("architecture", "") or arch
                            _service._log_event_safe(project_id, None,
                                                     f"Auto-iteration {_auto_iteration}: planned {len(plan['tasks'])} new tasks")
                            continue  # restart the loop with new tasks
                        else:
                            logger.info(f"Factory: project {project_id} — no more tasks from reviewer, stopping")
                            break
                    except Exception as e:
                        logger.error(f"Factory: auto-iteration failed for project {project_id}: {e}")
                        break
                else:
                    logger.info(f"Factory: project {project_id} — all tasks done")
                    break
            # No ready tasks but work remains. DON'T exit — keep polling so
            # that when a blocked task is retried from the UI (human_intervention
            # -> ready), the orchestrator picks it up and its dependents advance.
            # Use a longer sleep when blocked to reduce DB load + log spam.
            try:
                nodes = _service.get_nodes(project_id)
            except Exception:
                nodes = []
            blocked = [n for n in nodes if n.get("status") in ("human_intervention", "failed")]
            if blocked:
                names = ", ".join(f"#{n.get('id')}" for n in blocked)
                logger.info(f"Factory: project {project_id} waiting — blocked task(s): {names}; retry from UI to continue")
                await asyncio.sleep(10)
            else:
                await asyncio.sleep(3)
            continue

        project_desc = project.get("description", "")
        for task in ready:
            p = _service.get_project(project_id)
            if not p or p.get("status") in ("paused", "cancelled"):
                break
            try:
                await _process_task(project_id, task, owner, project_desc, arch)
            except Exception as e:
                logger.error(f"Factory: error processing task {task.get('id')}: {e}")
                try:
                    _service.fail_task(task["id"], error=str(e))
                except Exception:
                    pass

        await asyncio.sleep(1)

    _running.pop(project_id, None)

    # Compile delivery ZIP if the project completed
    final_status = _service.get_project(project_id)
    if final_status and final_status.get("status") == "completed":
        try:
            compile_delivery(project_id)
        except Exception as e:
            logger.error(f"Factory: delivery compilation failed for project {project_id}: {e}")

    logger.info(f"Factory: orchestrator finished for project {project_id}")


# ═══════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════

def launch_planning(project_id: int, owner: str = "default") -> None:
    """Launch the planning phase as a background task with a strong reference."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    task = loop.create_task(plan_project(project_id, owner))
    _planning_tasks.add(task)
    task.add_done_callback(_planning_tasks.discard)
    logger.info(f"Factory: launched planning for project {project_id}")


def launch_iteration(project_id: int, prompt: str, owner: str = "default") -> None:
    """Launch project iteration as a background task with a strong reference.

    Same pattern as launch_planning — fires iterate_project as a non-blocking
    asyncio task so the HTTP route returns immediately. The frontend polls
    for new tasks via the status endpoint.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    task = loop.create_task(iterate_project(project_id, prompt, owner))
    _planning_tasks.add(task)
    task.add_done_callback(_planning_tasks.discard)
    logger.info(f"Factory: launched iteration for project {project_id}")


def launch(project_id: int, owner: str = "default", arch: str = "",
           autonomous: bool = False) -> None:
    """Launch (or re-launch) the orchestrator for a project.

    If an orchestrator task already exists and is still alive, it is left
    running UNLESS force=True — in which case it is cancelled and replaced
    (used by restart/retry to recover a stuck loop).

    Set autonomous=True to enable self-iteration after all tasks complete.
    """
    existing = _running.get(project_id)
    if existing and not existing.done():
        return
    _start_orchestrator_task(project_id, owner, arch, autonomous)


def relaunch(project_id: int, owner: str = "default", arch: str = "",
             autonomous: bool = False) -> None:
    """Cancel any existing orchestrator for this project and start a fresh one.

    Use this from restart/retry paths so a stuck (e.g. hung-inside-wait_for)
    orchestrator is replaced instead of silently no-oping.
    """
    stop(project_id)
    _start_orchestrator_task(project_id, owner, arch, autonomous)


def _start_orchestrator_task(project_id: int, owner: str, arch: str,
                              autonomous: bool = False) -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    def _log_crash(t):
        _running.pop(project_id, None)
        if t.cancelled():
            return
        exc = t.exception()
        if exc:
            logger.error(f"Factory: orchestrator crashed for project {project_id}: {exc}",
                         exc_info=exc)

    task = loop.create_task(_orchestrator_loop(project_id, owner, arch, autonomous))
    _running[project_id] = task
    task.add_done_callback(_log_crash)


def is_running(project_id: int) -> bool:
    t = _running.get(project_id)
    return t is not None and not t.done()


def stop(project_id: int) -> None:
    t = _running.pop(project_id, None)
    if t and not t.done():
        t.cancel()


def list_agents() -> List[Dict[str, str]]:
    """Return the agent roster for display."""
    return [
        {"key": k, "name": v["name"], "role": v["role"]}
        for k, v in AGENTS.items()
    ]
