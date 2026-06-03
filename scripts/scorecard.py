#!/usr/bin/env python3
"""Re-runnable JSON-first engineering-modernization scorecard generator.

Measures the seven D-14 baseline metrics for Odysseus by shelling out to the
mature tools (ruff, coverage/pytest-cov, bandit, pip-audit) and by reading the
FastAPI route table plus cold-import perf. The JSON document is the single source
of truth (D-10); ``SCORECARD.md`` is a rendered view of it. ``--check`` recomputes
every metric and ratchet-compares against the committed baseline, exiting nonzero
on any regression (D-11) so a CI ``scorecard`` job can fail a PR.

This script only MEASURES existing code — it changes nothing in the application,
preserving behavior. It must itself stay ruff-clean, ruff-format-clean, and
mypy-clean.

Usage:
    python scripts/scorecard.py --write     # regenerate baseline.json + SCORECARD.md
    python scripts/scorecard.py --check      # recompute + ratchet-compare; nonzero on regression
"""

from __future__ import annotations

import argparse
import ast
import json
import logging
import os
import re
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

# Output locations (D-13: the baseline lives under .planning/scorecard/).
SCORECARD_DIR = Path(REPO_ROOT) / ".planning" / "scorecard"
BASELINE_PATH = SCORECARD_DIR / "baseline.json"
MARKDOWN_PATH = SCORECARD_DIR / "SCORECARD.md"
TRIAGE_PATH = SCORECARD_DIR / "security-triage.md"

# Source roots scanned for line counts / typed % (excludes tests/, static/, data/,
# logs/, vendored). Keep this include set documented and deterministic.
SOURCE_DIRS = ["src", "routes", "core", "services", "mcp_servers", "companion"]
SOURCE_FILES = ["app.py"]

# Number of cold-import timing runs averaged for the perf guardrail metric.
PERF_RUNS = 5

# --- Auth allow-list (kept in sync with app.py:162-194) -----------------------
# These VALUES are copied verbatim from the inline AuthMiddleware allow-list in
# app.py. They are gated behind ``if AUTH_ENABLED:`` there, so the symbols cannot
# be imported reliably — we mirror the values locally instead. The emitted
# auth_endpoints list is the INPUT for Phase 2 COV-03 and Phase 5 SEC-01, so this
# allow-list MUST be kept in sync with app.py's allow-list; divergence is a
# security gap (T-04-01).
AUTH_EXEMPT_EXACT = {
    "/api/auth/setup",
    "/api/auth/signup",
    "/api/auth/login",
    "/api/auth/logout",
    "/api/auth/status",
    "/api/auth/features",
    "/api/auth/settings",
    "/api/auth/integrations/presets",
    "/api/health",
    "/api/version",
    "/login",
}
AUTH_EXEMPT_PREFIXES = ["/static"]
AUTH_EXEMPT_PATTERNS = [re.compile(r"^/api/tasks/[^/]+/webhook/[^/]+/?$")]


def _is_auth_exempt(path: str) -> bool:
    """Mirror app.py:200-205's _is_auth_exempt — authenticated = NOT exempt."""
    if path in AUTH_EXEMPT_EXACT:
        return True
    if any(path.startswith(p) for p in AUTH_EXEMPT_PREFIXES):
        return True
    return any(p.match(path) for p in AUTH_EXEMPT_PATTERNS)


# --- Small subprocess helper --------------------------------------------------


def _run(cmd: list[str], *, cwd: str | None = None) -> subprocess.CompletedProcess[str]:
    """Run a command capturing text output; never raises.

    A missing binary (FileNotFoundError) is normalized to a synthetic
    CompletedProcess with returncode 127 so every caller can treat "tool
    absent" the same as "tool exited nonzero" without a try/except."""
    logger.debug(f"running: {' '.join(cmd)}")
    try:
        return subprocess.run(
            cmd,
            cwd=cwd or REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        logger.warning(f"binary not found: {cmd[0]}")
        return subprocess.CompletedProcess(
            cmd, 127, stdout="", stderr=f"{cmd[0]}: not found"
        )


def _tool_version(cmd: list[str]) -> str:
    """Best-effort tool version string; 'unavailable' if the tool is missing."""
    proc = _run(cmd)
    if proc.returncode == 127:
        return "unavailable"
    out = (proc.stdout or proc.stderr or "").strip().splitlines()
    return out[0] if out else "unknown"


def _iter_source_files() -> list[Path]:
    """Yield every measured .py file under the documented include set."""
    root = Path(REPO_ROOT)
    files: list[Path] = []
    for rel in SOURCE_DIRS:
        base = root / rel
        if base.is_dir():
            files.extend(sorted(base.rglob("*.py")))
    for rel in SOURCE_FILES:
        f = root / rel
        if f.is_file():
            files.append(f)
    return files


def _rel(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


# --- Metric (1): per-module physical line counts ------------------------------


def metric_module_line_counts() -> dict[str, Any]:
    counts: dict[str, int] = {}
    for f in _iter_source_files():
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            logger.warning(f"could not read {f}: {exc}")
            continue
        counts[_rel(f)] = text.count("\n") + (
            0 if text.endswith("\n") or not text else 1
        )
    max_loc = max(counts.values()) if counts else 0
    logger.info(f"module_line_counts: {len(counts)} files, max_module_loc={max_loc}")
    return {"by_module": counts, "max_module_loc": max_loc}


# --- Metric (2): typed % via AST ----------------------------------------------


def _fn_is_typed(node: ast.AST) -> bool:
    """A def is 'typed' iff it has a return annotation AND every non-self/cls
    positional/keyword arg is annotated. Tool-version-independent (Open Q3)."""
    assert isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    if node.returns is None:
        return False
    args = node.args
    positional = [*args.posonlyargs, *args.args]
    skip = {"self", "cls"}
    to_check: list[ast.arg] = [a for a in positional if a.arg not in skip]
    to_check.extend(args.kwonlyargs)
    if args.vararg is not None:
        to_check.append(args.vararg)
    if args.kwarg is not None:
        to_check.append(args.kwarg)
    return all(a.annotation is not None for a in to_check)


def metric_typed_pct() -> dict[str, Any]:
    by_module: dict[str, float] = {}
    total = 0
    typed = 0
    for f in _iter_source_files():
        try:
            tree = ast.parse(
                f.read_text(encoding="utf-8", errors="replace"), filename=str(f)
            )
        except SyntaxError as exc:
            logger.warning(f"skipping unparseable {f}: {exc}")
            continue
        mod_total = 0
        mod_typed = 0
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                mod_total += 1
                if _fn_is_typed(node):
                    mod_typed += 1
        if mod_total:
            by_module[_rel(f)] = round(mod_typed / mod_total, 4)
        total += mod_total
        typed += mod_typed
    overall = round(typed / total, 4) if total else 0.0
    logger.info(f"typed_pct: overall={overall} ({typed}/{total} defs)")
    return {
        "overall": overall,
        "total_defs": total,
        "typed_defs": typed,
        "by_module": by_module,
    }


# --- Metric (3): ruff findings ------------------------------------------------


def metric_ruff_findings() -> dict[str, Any]:
    proc = _run(["ruff", "check", ".", "--output-format=json"])
    try:
        findings = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError as exc:
        logger.warning(
            f"could not parse ruff output: {exc}; stderr={proc.stderr[:200]}"
        )
        findings = []
    by_code: dict[str, int] = {}
    for item in findings:
        code = item.get("code") or "UNKNOWN"
        by_code[code] = by_code.get(code, 0) + 1
    total = len(findings)
    logger.info(f"ruff_findings: total={total} by_code={by_code}")
    return {"total": total, "by_code": dict(sorted(by_code.items()))}


# --- Metric (4): security findings (bandit + pip-audit) -----------------------


def _bandit_findings() -> list[dict[str, Any]]:
    dirs = [d for d in SOURCE_DIRS if (Path(REPO_ROOT) / d).is_dir()]
    proc = _run(["bandit", "-c", "pyproject.toml", "-r", *dirs, "-f", "json"])
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        logger.warning(
            f"could not parse bandit output: {exc}; stderr={proc.stderr[:200]}"
        )
        return []
    out: list[dict[str, Any]] = []
    for r in data.get("results", []):
        out.append(
            {
                "tool": "bandit",
                "id": r.get("test_id", ""),
                "severity": (r.get("issue_severity") or "").upper(),
                "confidence": (r.get("issue_confidence") or "").upper(),
                "file": r.get("filename", ""),
                "line": r.get("line_number"),
                "message": r.get("issue_text", ""),
                "triaged": False,
            }
        )
    return out


def _pip_audit_findings() -> list[dict[str, Any]]:
    lock = "requirements.lock"
    if not (Path(REPO_ROOT) / lock).is_file():
        logger.warning(f"{lock} not found; skipping pip-audit")
        return []
    proc = _run(["pip-audit", "-r", lock, "-f", "json"])
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        logger.warning(
            f"could not parse pip-audit output: {exc}; stderr={proc.stderr[:200]}"
        )
        return []
    # pip-audit emits either {"dependencies": [...]} or a bare list depending on version.
    deps = data.get("dependencies", data) if isinstance(data, dict) else data
    out: list[dict[str, Any]] = []
    for dep in deps or []:
        name = dep.get("name", "")
        version = dep.get("version", "")
        for vuln in dep.get("vulns", []) or []:
            severity = ""
            for alias in vuln.get("aliases", []) or []:
                if isinstance(alias, str) and alias.upper().startswith("CVE"):
                    severity = severity or "UNKNOWN"
            out.append(
                {
                    "tool": "pip-audit",
                    "id": vuln.get("id", ""),
                    "severity": severity or "UNKNOWN",
                    "package": f"{name}=={version}",
                    "fix_versions": vuln.get("fix_versions", []),
                    "message": (vuln.get("description") or "")[:300],
                    "triaged": False,
                }
            )
    return out


def metric_security_findings() -> list[dict[str, Any]]:
    findings = _bandit_findings() + _pip_audit_findings()
    high = sum(1 for f in findings if f.get("severity") in {"HIGH", "CRITICAL"})
    logger.info(f"security_findings: total={len(findings)} high_critical={high}")
    return findings


def _count_high_critical(findings: list[dict[str, Any]]) -> int:
    return sum(
        1
        for f in findings
        if str(f.get("severity", "")).upper() in {"HIGH", "CRITICAL"}
    )


# --- Metric (5): coverage -----------------------------------------------------


def metric_coverage() -> dict[str, Any]:
    cov_json = Path(REPO_ROOT) / "coverage.json"
    proc = _run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--cov=.",
            "--cov-report=json",
            "-q",
        ]
    )
    logger.info(
        f"pytest --cov exit={proc.returncode} (failures are env artifacts, see deferred-items.md)"
    )
    if not cov_json.is_file():
        logger.warning("coverage.json not produced; recording overall_pct=0.0")
        return {"overall_pct": 0.0, "by_file": {}, "note": "coverage.json not produced"}
    try:
        data = json.loads(cov_json.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        logger.warning(f"could not parse coverage.json: {exc}")
        return {"overall_pct": 0.0, "by_file": {}, "note": "coverage.json unparseable"}
    overall = round(float(data.get("totals", {}).get("percent_covered", 0.0)), 2)
    by_file: dict[str, float] = {}
    for path, fdata in data.get("files", {}).items():
        pct = fdata.get("summary", {}).get("percent_covered")
        if pct is not None:
            by_file[path] = round(float(pct), 2)
    logger.info(f"coverage: overall_pct={overall} ({len(by_file)} files)")
    return {"overall_pct": overall, "by_file": by_file}


# --- Metric (6): startup / key-path perf (guardrail, not a gate) --------------


def metric_perf() -> dict[str, Any]:
    durations: list[float] = []
    snippet = "import time; t = time.perf_counter(); import app; print(time.perf_counter() - t)"
    for i in range(PERF_RUNS):
        proc = _run([sys.executable, "-c", snippet])
        if proc.returncode != 0:
            logger.warning(
                f"perf import run {i + 1} failed: {proc.stderr.strip()[:200]}"
            )
            return {
                "import_app_seconds_mean": None,
                "runs": 0,
                "python": platform_python(),
                "key_path_ms": None,
                "note": "import app failed in this environment (perf is a guardrail, A2)",
            }
        try:
            durations.append(float(proc.stdout.strip().splitlines()[-1]))
        except (ValueError, IndexError):
            logger.warning(f"perf run {i + 1} produced no timing line")
    mean = round(sum(durations) / len(durations), 4) if durations else None
    key_path_ms = _key_path_latency_ms()
    logger.info(f"perf: import_app_seconds_mean={mean} key_path_ms={key_path_ms}")
    return {
        "import_app_seconds_mean": mean,
        "runs": len(durations),
        "python": platform_python(),
        "key_path_ms": key_path_ms,
    }


def _key_path_latency_ms() -> float | None:
    """One cheap TestClient GET /api/health for a key-path sample; falls back to
    None (import-time-only) if TestClient errors (A2 — perf is a guardrail)."""
    try:
        from fastapi.testclient import TestClient

        import app as app_module

        with TestClient(app_module.app) as client:
            t = time.perf_counter()
            client.get("/api/health")
            return round((time.perf_counter() - t) * 1000.0, 2)
    except Exception as exc:  # broad: perf must never block the scorecard
        logger.warning(f"key-path TestClient sample skipped: {exc}")
        return None


def platform_python() -> str:
    return f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"


# --- Metric (7): authenticated-endpoint enumeration ---------------------------


def metric_auth_endpoints() -> list[dict[str, Any]]:
    try:
        from fastapi.routing import APIRoute

        import app as app_module
    except Exception as exc:
        logger.warning(f"could not import app for auth_endpoints: {exc}")
        return []
    out: list[dict[str, Any]] = []
    for r in app_module.app.routes:
        if isinstance(r, APIRoute):
            methods = sorted(
                m for m in (r.methods or set()) if m not in {"HEAD", "OPTIONS"}
            )
            out.append(
                {
                    "path": r.path,
                    "methods": methods,
                    "authenticated": not _is_auth_exempt(r.path),
                }
            )
    out.sort(key=lambda e: (e["path"], e["methods"]))
    authed = sum(1 for e in out if e["authenticated"])
    logger.info(f"auth_endpoints: total={len(out)} authenticated={authed}")
    return out


# --- Assembly -----------------------------------------------------------------


def _git_sha() -> str:
    """HEAD SHA. Prefers SCORECARD_GIT_SHA (set by the host when git is absent in
    the runtime container, e.g. python:3.12-slim has no git), then `git`, then
    'unknown' — never raises (Rule 3: container had no git binary)."""
    env_sha = os.environ.get("SCORECARD_GIT_SHA", "").strip()
    if env_sha:
        return env_sha
    proc = _run(["git", "rev-parse", "HEAD"])
    return proc.stdout.strip() if proc.returncode == 0 else "unknown"


def _tool_versions() -> dict[str, str]:
    return {
        "ruff": _tool_version(["ruff", "--version"]),
        "mypy": _tool_version(["mypy", "--version"]),
        "bandit": _tool_version(["bandit", "--version"]),
        "pip_audit": _tool_version(["pip-audit", "--version"]),
        "coverage": _tool_version(["coverage", "--version"]),
        "python": platform_python(),
    }


def compute_metrics() -> dict[str, Any]:
    """Compute all seven D-14 metrics. Used by both --write and --check."""
    line_counts = metric_module_line_counts()
    return {
        "module_line_counts": line_counts["by_module"],
        "max_module_loc": line_counts["max_module_loc"],
        "typed_pct": metric_typed_pct(),
        "ruff_findings": metric_ruff_findings(),
        "security_findings": metric_security_findings(),
        "coverage": metric_coverage(),
        "perf": metric_perf(),
        "auth_endpoints": metric_auth_endpoints(),
    }


def _build_thresholds(metrics: dict[str, Any]) -> dict[str, Any]:
    """Ratchet thresholds (D-11): absolutes encoded directly, the rest pinned to
    the just-measured baseline so metrics can only improve."""
    return {
        "mode": "ratchet",
        "ruff_findings_total": {"max": 0},
        "typed_pct_overall": {"min": metrics["typed_pct"]["overall"]},
        "max_module_loc": {"max": metrics["max_module_loc"]},
        "coverage_overall_pct": {"min": metrics["coverage"]["overall_pct"]},
        "security_high_critical": {"max": 0},
    }


def build_document(metrics: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "git_sha": _git_sha(),
        "tool_versions": _tool_versions(),
        "metrics": metrics,
        "thresholds": _build_thresholds(metrics),
    }


# --- Markdown rendering (a VIEW of the JSON, D-10) ----------------------------


def render_markdown(doc: dict[str, Any]) -> str:
    m = doc["metrics"]
    t = doc["thresholds"]
    lines: list[str] = []
    lines.append("# Engineering Modernization Scorecard")
    lines.append("")
    lines.append(
        "> Rendered from `baseline.json` — **do not edit by hand**. "
        "Regenerate with `python scripts/scorecard.py --write` (D-10: JSON is the source of truth)."
    )
    lines.append("")
    lines.append(f"- Generated: `{doc['generated_at']}`")
    lines.append(f"- Git SHA: `{doc['git_sha']}`")
    lines.append(f"- Schema version: `{doc['schema_version']}`")
    lines.append("")
    lines.append("## Tool versions")
    lines.append("")
    lines.append("| Tool | Version |")
    lines.append("|------|---------|")
    for name, ver in doc["tool_versions"].items():
        lines.append(f"| {name} | `{ver}` |")
    lines.append("")
    lines.append("## Headline metrics")
    lines.append("")
    lines.append("| Metric | Value | Ratchet |")
    lines.append("|--------|-------|---------|")
    lines.append(
        f"| Max module LOC | {m['max_module_loc']} | <= {t['max_module_loc']['max']} |"
    )
    lines.append(
        f"| Typed % (overall) | {m['typed_pct']['overall']} | >= {t['typed_pct_overall']['min']} |"
    )
    lines.append(
        f"| Ruff findings | {m['ruff_findings']['total']} | <= {t['ruff_findings_total']['max']} |"
    )
    lines.append(
        f"| Coverage % (overall) | {m['coverage']['overall_pct']} | "
        f">= {t['coverage_overall_pct']['min']} |"
    )
    high = _count_high_critical(m["security_findings"])
    lines.append(
        f"| Security high/critical | {high} | <= {t['security_high_critical']['max']} |"
    )
    lines.append("")
    lines.append("## Largest modules (top 15)")
    lines.append("")
    lines.append("| Module | LOC |")
    lines.append("|--------|-----|")
    top = sorted(m["module_line_counts"].items(), key=lambda kv: kv[1], reverse=True)[
        :15
    ]
    for mod, loc in top:
        lines.append(f"| `{mod}` | {loc} |")
    lines.append("")
    lines.append("## Ruff findings by code")
    lines.append("")
    if m["ruff_findings"]["by_code"]:
        lines.append("| Code | Count |")
        lines.append("|------|-------|")
        for code, n in m["ruff_findings"]["by_code"].items():
            lines.append(f"| {code} | {n} |")
    else:
        lines.append("_None — clean ruff baseline._")
    lines.append("")
    lines.append("## Security findings")
    lines.append("")
    lines.append(
        "Triage rationale lives in [`security-triage.md`](./security-triage.md)."
    )
    lines.append("")
    if m["security_findings"]:
        lines.append("| Tool | ID | Severity | Location | Triaged |")
        lines.append("|------|----|----------|----------|---------|")
        for f in m["security_findings"]:
            loc = f.get("file") or f.get("package") or ""
            lines.append(
                f"| {f.get('tool', '')} | {f.get('id', '')} | {f.get('severity', '')} "
                f"| `{loc}` | {f.get('triaged', False)} |"
            )
    else:
        lines.append("_None recorded._")
    lines.append("")
    lines.append("## Perf (guardrail, not a gate)")
    lines.append("")
    perf = m["perf"]
    lines.append(
        f"- Cold `import app` mean: `{perf.get('import_app_seconds_mean')}` s "
        f"over {perf.get('runs')} runs (Python {perf.get('python')})"
    )
    lines.append(f"- Key-path `/api/health` latency: `{perf.get('key_path_ms')}` ms")
    if perf.get("note"):
        lines.append(f"- Note: {perf['note']}")
    lines.append("")
    lines.append("## Authenticated endpoints")
    lines.append("")
    authed = sum(1 for e in m["auth_endpoints"] if e["authenticated"])
    lines.append(
        f"- Total routes: {len(m['auth_endpoints'])} "
        f"({authed} authenticated, {len(m['auth_endpoints']) - authed} public/exempt)"
    )
    lines.append(
        "- This enumeration is the input for Phase 2 COV-03 / Phase 5 SEC-01 and MUST stay in "
        "sync with app.py's inline allow-list."
    )
    lines.append("")
    return "\n".join(lines) + "\n"


# --- Triage stub --------------------------------------------------------------

TRIAGE_STUB = """# Security Triage Notes

Triage rationale for every finding in the scorecard's `security_findings` list
(referenced by the `triaged` flags in `baseline.json`). Populate a row as each
finding arises; high/critical findings must be resolved (bump the pin) or carry a
written rationale here (D-11: `security_high_critical` max is 0).

## bandit findings

| Test ID | File:Line | Severity | Disposition | Rationale |
|---------|-----------|----------|-------------|-----------|
| _none at baseline_ | | | | |

## pip-audit CVEs

| Vulnerability ID | Package | Severity | Fix version | Disposition | Rationale |
|------------------|---------|----------|-------------|-------------|-----------|
| _none at baseline_ | | | | | |
"""


def _ensure_triage_stub() -> None:
    if not TRIAGE_PATH.exists():
        TRIAGE_PATH.write_text(TRIAGE_STUB, encoding="utf-8")
        logger.info(f"wrote triage stub: {TRIAGE_PATH}")


# --- Modes --------------------------------------------------------------------


def write_baseline() -> int:
    SCORECARD_DIR.mkdir(parents=True, exist_ok=True)
    _ensure_triage_stub()
    logger.info("computing metrics for --write ...")
    metrics = compute_metrics()
    doc = build_document(metrics)
    BASELINE_PATH.write_text(
        json.dumps(doc, indent=2, sort_keys=False) + "\n", encoding="utf-8"
    )
    logger.info(f"wrote baseline: {BASELINE_PATH}")
    MARKDOWN_PATH.write_text(render_markdown(doc), encoding="utf-8")
    logger.info(f"wrote rendered scorecard: {MARKDOWN_PATH}")
    return 0


def check_ratchet() -> int:
    if not BASELINE_PATH.exists():
        logger.error(f"no committed baseline at {BASELINE_PATH}; run --write first")
        return 2
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    thresholds = baseline["thresholds"]
    logger.info("recomputing metrics for --check ...")
    metrics = compute_metrics()

    regressions: list[str] = []

    ruff_total = metrics["ruff_findings"]["total"]
    ruff_max = thresholds["ruff_findings_total"]["max"]
    ok = ruff_total <= ruff_max
    logger.info(
        f"ratchet ruff_findings_total: {ruff_total} <= {ruff_max} -> {'OK' if ok else 'FAIL'}"
    )
    if not ok:
        regressions.append(f"ruff_findings_total {ruff_total} > {ruff_max}")

    typed = metrics["typed_pct"]["overall"]
    typed_min = thresholds["typed_pct_overall"]["min"]
    ok = typed >= typed_min
    logger.info(
        f"ratchet typed_pct_overall: {typed} >= {typed_min} -> {'OK' if ok else 'FAIL'}"
    )
    if not ok:
        regressions.append(f"typed_pct_overall {typed} < {typed_min}")

    max_loc = metrics["max_module_loc"]
    loc_max = thresholds["max_module_loc"]["max"]
    ok = max_loc <= loc_max
    logger.info(
        f"ratchet max_module_loc: {max_loc} <= {loc_max} -> {'OK' if ok else 'FAIL'}"
    )
    if not ok:
        regressions.append(f"max_module_loc {max_loc} > {loc_max}")

    cov = metrics["coverage"]["overall_pct"]
    cov_min = thresholds["coverage_overall_pct"]["min"]
    ok = cov >= cov_min
    logger.info(
        f"ratchet coverage_overall_pct: {cov} >= {cov_min} -> {'OK' if ok else 'FAIL'}"
    )
    if not ok:
        regressions.append(f"coverage_overall_pct {cov} < {cov_min}")

    high = _count_high_critical(metrics["security_findings"])
    high_max = thresholds["security_high_critical"]["max"]
    ok = high <= high_max
    logger.info(
        f"ratchet security_high_critical: {high} <= {high_max} -> {'OK' if ok else 'FAIL'}"
    )
    if not ok:
        regressions.append(f"security_high_critical {high} > {high_max}")

    if regressions:
        logger.error(f"RATCHET FAILED — {len(regressions)} regression(s):")
        for r in regressions:
            logger.error(f"  - {r}")
        return 1
    logger.info("RATCHET PASSED — no regression against committed baseline.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write",
        action="store_true",
        help="Regenerate baseline.json + SCORECARD.md",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Recompute + compare to baseline; nonzero exit on regression",
    )
    args = parser.parse_args()

    if args.write == args.check:
        parser.error("exactly one of --write or --check is required")

    if args.write:
        return write_baseline()
    return check_ratchet()


if __name__ == "__main__":
    sys.exit(main())
