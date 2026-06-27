# Windows venv + Sidecars Setup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up a native Windows Python venv for Odysseus with all bundled Docker sidecars (ChromaDB, SearXNG, ntfy) running on loopback, verified by automated checks before claiming success.

**Architecture:** Hybrid Windows layout — the app runs natively from `venv/` (FastAPI via uvicorn); sidecars run via existing `docker-compose.yml` service definitions only (`chromadb`, `searxng`, `ntfy`). A new `scripts/verify_local_setup.py` probes venv + sidecar health using the same ports/URLs as `.env.example`. A new `scripts/start-sidecars.ps1` wraps `docker compose up -d chromadb searxng ntfy` with readiness waits. Every automation step follows TDD (test first, watch fail, implement, watch pass) and verification-before-completion (fresh command output before any success claim).

**Tech Stack:** Python 3.11+ (host has 3.13.9), Docker Compose v2, PowerShell 5.1+, pytest, existing `src/service_health.py` probe patterns.

---

## File map

| File | Responsibility |
|------|----------------|
| `scripts/verify_local_setup.py` | **Create.** CLI that checks venv Python, core imports, and sidecar TCP/HTTP reachability; exits 0 only when all required checks pass. |
| `tests/test_verify_local_setup.py` | **Create.** Unit tests with mocked sockets/HTTP — no live Docker required in CI. |
| `scripts/start-sidecars.ps1` | **Create.** Idempotent Docker sidecar launcher + wait loop for SearXNG health. |
| `tests/test_start_sidecars_script.py` | **Create.** Static contract test — script must invoke the three sidecar services and readiness checks. |
| `.env` | **Create** (from `.env.example`) if missing; native-sidecar URLs already match compose port bindings. |
| `venv/` | **Create** by `python -m venv venv` + `pip install -r requirements.txt`. |
| `data/`, `logs/` | **Create** by `python setup.py`. |
| `docker-compose.yml` | **Read only.** Sidecar services: `chromadb` (8100→8000), `searxng` (8080), `ntfy` (8091). |
| `launch-windows.ps1` | **Read only** for venv/setup/uvicorn conventions; do not modify in this plan. |

---

### Task 1: Sidecar launcher script contract test

**Files:**
- Create: `tests/test_start_sidecars_script.py`
- Read: `docker-compose.yml:90-159`

- [ ] **Step 1: Write the failing test**

```python
"""Static contract for scripts/start-sidecars.ps1."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "start-sidecars.ps1"


def test_start_sidecars_script_exists_and_targets_all_sidecars():
    assert SCRIPT.is_file(), "scripts/start-sidecars.ps1 must exist"
    text = SCRIPT.read_text(encoding="utf-8")
    for service in ("chromadb", "searxng", "ntfy"):
        assert service in text, f"missing docker compose service {service}"
    assert "docker compose up" in text.lower() or "docker-compose up" in text.lower()
    assert "8080" in text, "must wait on SearXNG loopback port 8080"
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```powershell
cd "E:\Knowledge-Base\03-Development\Active-Projects\AI Workspace\odysseus"
python -m pytest tests/test_start_sidecars_script.py::test_start_sidecars_script_exists_and_targets_all_sidecars -v
```
Expected: **FAIL** — `scripts/start-sidecars.ps1 must exist`

- [ ] **Step 3: Write minimal implementation**

Create `scripts/start-sidecars.ps1`:

```powershell
#Requires -Version 5.1
<#
  Start Odysseus bundled sidecars only (ChromaDB, SearXNG, ntfy).
  Safe to re-run. Does NOT start the odysseus app container.

  Usage:
    powershell -ExecutionPolicy Bypass -File .\scripts\start-sidecars.ps1
#>
$ErrorActionPreference = "Stop"
Set-Location -Path (Split-Path -Parent $PSScriptRoot)

function Write-Step($msg) { Write-Host ""; Write-Host ("==> " + $msg) -ForegroundColor Cyan }
function Fail($msg) {
    Write-Host ("ERROR: " + $msg) -ForegroundColor Red
    exit 1
}

Write-Step "Starting Docker sidecars (chromadb, searxng, ntfy)"
docker compose up -d chromadb searxng ntfy
if ($LASTEXITCODE -ne 0) { Fail "docker compose up failed (exit $LASTEXITCODE)" }

Write-Step "Waiting for sidecars to become reachable"
$deadline = (Get-Date).AddMinutes(3)
while ((Get-Date) -lt $deadline) {
    $chromadb = Test-NetConnection -ComputerName 127.0.0.1 -Port 8100 -WarningAction SilentlyContinue
    $searxng  = Test-NetConnection -ComputerName 127.0.0.1 -Port 8080 -WarningAction SilentlyContinue
    $ntfy     = Test-NetConnection -ComputerName 127.0.0.1 -Port 8091 -WarningAction SilentlyContinue
    if ($chromadb.TcpTestSucceeded -and $searxng.TcpTestSucceeded -and $ntfy.TcpTestSucceeded) {
        Write-Host "All sidecar ports open on loopback." -ForegroundColor Green
        docker compose ps chromadb searxng ntfy
        exit 0
    }
    Start-Sleep -Seconds 3
}
Fail "Sidecars did not become reachable within 3 minutes. Check: docker compose logs searxng"
```

- [ ] **Step 4: Run test to verify it passes**

Run:
```powershell
python -m pytest tests/test_start_sidecars_script.py::test_start_sidecars_script_exists_and_targets_all_sidecars -v
```
Expected: **PASS**

- [ ] **Step 5: Commit**

```powershell
git add scripts/start-sidecars.ps1 tests/test_start_sidecars_script.py
git commit -m "feat(scripts): add Windows sidecar launcher with contract test"
```

---

### Task 2: Local setup verification module (TDD)

**Files:**
- Create: `scripts/verify_local_setup.py`
- Create: `tests/test_verify_local_setup.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_verify_local_setup.py`:

```python
"""Unit tests for scripts/verify_local_setup.py (no live Docker required)."""

import socket
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import scripts.verify_local_setup as vls


def test_port_open_true_for_listening_socket():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    _host, port = srv.getsockname()
    try:
        assert vls.port_open("127.0.0.1", port, timeout=1.0) is True
    finally:
        srv.close()


def test_port_open_false_for_closed_port():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    _host, port = srv.getsockname()
    srv.close()
    assert vls.port_open("127.0.0.1", port, timeout=0.5) is False


def test_check_venv_reports_missing_interpreter(tmp_path):
    missing = tmp_path / "venv" / "Scripts" / "python.exe"
    result = vls.check_venv(missing)
    assert result.ok is False
    assert "missing" in result.detail.lower()


def test_check_sidecars_all_ok_when_probes_succeed(monkeypatch):
    monkeypatch.setattr(vls, "port_open", lambda *_a, **_k: True)
    monkeypatch.setattr(vls, "http_ok", lambda *_a, **_k: True)
    results = vls.check_sidecars()
    assert len(results) == 3
    assert all(r.ok for r in results)


def test_check_sidecars_reports_searxng_down(monkeypatch):
    monkeypatch.setattr(vls, "port_open", lambda *_a, **_k: True)

    def http(url, timeout):
        return url.endswith("/v1/health") or url.endswith(":8091/v1/health")

    monkeypatch.setattr(vls, "http_ok", http)
    results = vls.check_sidecars()
    by_name = {r.name: r for r in results}
    assert by_name["searxng"].ok is False


def test_run_checks_exits_nonzero_when_any_required_check_fails(tmp_path, monkeypatch):
    repo = tmp_path
    venv_py = repo / "venv" / "Scripts" / "python.exe"
    venv_py.parent.mkdir(parents=True)
    venv_py.write_text("", encoding="utf-8")

    monkeypatch.setattr(vls, "check_venv", lambda _p: vls.CheckResult("venv", False, "bad venv"))
    monkeypatch.setattr(vls, "check_sidecars", lambda: [vls.CheckResult("chromadb", True, "ok")])
    code = vls.run_checks(repo_root=repo, venv_python=venv_py)
    assert code == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```powershell
python -m pytest tests/test_verify_local_setup.py -v
```
Expected: **FAIL** — `ModuleNotFoundError: No module named 'scripts.verify_local_setup'`

- [ ] **Step 3: Write minimal implementation**

Create `scripts/verify_local_setup.py`:

```python
#!/usr/bin/env python3
"""Verify native Odysseus venv and Docker sidecar readiness."""

from __future__ import annotations

import argparse
import socket
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

SIDECAR_SPECS = (
    {"name": "chromadb", "host": "127.0.0.1", "port": 8100, "kind": "tcp"},
    {"name": "searxng", "url": "http://127.0.0.1:8080/", "kind": "http"},
    {"name": "ntfy", "url": "http://127.0.0.1:8091/v1/health", "kind": "http"},
)


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    detail: str


def port_open(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def http_ok(url: str, timeout: float = 3.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return 200 <= resp.status < 500
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def default_venv_python(repo_root: Path = REPO_ROOT) -> Path:
    if sys.platform == "win32":
        return repo_root / "venv" / "Scripts" / "python.exe"
    return repo_root / "venv" / "bin" / "python"


def check_venv(python_path: Path) -> CheckResult:
    if not python_path.is_file():
        return CheckResult("venv", False, f"missing interpreter: {python_path}")
    return CheckResult("venv", True, str(python_path))


def check_sidecars() -> list[CheckResult]:
    results: list[CheckResult] = []
    for spec in SIDECAR_SPECS:
        if spec["kind"] == "tcp":
            ok = port_open(spec["host"], spec["port"])
            detail = f"{spec['host']}:{spec['port']} {'open' if ok else 'closed'}"
        else:
            ok = http_ok(spec["url"])
            detail = f"{spec['url']} {'ok' if ok else 'unreachable'}"
        results.append(CheckResult(spec["name"], ok, detail))
    return results


def run_checks(repo_root: Path = REPO_ROOT, venv_python: Path | None = None) -> int:
    py = venv_python or default_venv_python(repo_root)
    checks = [check_venv(py), *check_sidecars()]
    failed = False
    for c in checks:
        status = "OK" if c.ok else "FAIL"
        print(f"[{status}] {c.name}: {c.detail}")
        failed = failed or not c.ok
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify Odysseus venv + sidecars")
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--venv-python", type=Path, default=None)
    args = parser.parse_args(argv)
    return run_checks(repo_root=args.repo_root, venv_python=args.venv_python)


if __name__ == "__main__":
    raise SystemExit(main())
```

Ensure `scripts/` is importable — add empty `scripts/__init__.py` if pytest cannot import the module:

```python
# scripts/__init__.py
"""Odysseus helper scripts package (importable for tests)."""
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```powershell
python -m pytest tests/test_verify_local_setup.py -v
```
Expected: **PASS** (6 passed)

- [ ] **Step 5: Commit**

```powershell
git add scripts/__init__.py scripts/verify_local_setup.py tests/test_verify_local_setup.py
git commit -m "feat(scripts): add local setup verifier with unit tests"
```

---

### Task 3: Preflight host prerequisites

**Files:**
- Read: `docs/setup.md:227-275`, `launch-windows.ps1:65-118`

- [ ] **Step 1: Verify Python 3.11+ is available**

Run:
```powershell
python -c "import sys; assert sys.version_info >= (3, 11), sys.version; print('Python OK:', sys.version)"
```
Expected: `Python OK: 3.13.x ...` (or any 3.11+)

If this fails, install Python 3.11+ from https://www.python.org/downloads/ and re-run.

- [ ] **Step 2: Verify Docker daemon is running**

Run:
```powershell
docker info --format "{{.ServerVersion}}"
docker compose version
```
Expected: exit code 0 with a server version string and compose v2.x.

- [ ] **Step 3: Verify repo is present**

Run:
```powershell
cd "E:\Knowledge-Base\03-Development\Active-Projects\AI Workspace\odysseus"
Test-Path README.md; Test-Path docker-compose.yml; Test-Path requirements.txt
```
Expected: all `True`

---

### Task 4: Create `.env` for native app + Docker sidecars

**Files:**
- Create: `.env` (from `.env.example`)
- Read: `.env.example:44-112`

- [ ] **Step 1: Copy example env if missing**

Run:
```powershell
cd "E:\Knowledge-Base\03-Development\Active-Projects\AI Workspace\odysseus"
if (-not (Test-Path .env)) { Copy-Item .env.example .env; Write-Host "Created .env" } else { Write-Host ".env already exists" }
```

- [ ] **Step 2: Confirm native-sidecar URLs (no edit needed if defaults kept)**

Verify these lines exist uncommented in `.env`:

```
SEARXNG_INSTANCE=http://localhost:8080
CHROMADB_HOST=localhost
CHROMADB_PORT=8100
NTFY_BASE_URL=http://localhost:8091
```

Run:
```powershell
Select-String -Path .env -Pattern 'SEARXNG_INSTANCE|CHROMADB_HOST|CHROMADB_PORT|NTFY_BASE_URL'
```
Expected: four matching lines with the values above (localhost / 8080 / 8100 / 8091).

- [ ] **Step 3: Optional — pre-seed admin password for non-interactive setup**

Add to `.env` (do **not** commit real passwords):

```
ODYSSEUS_ADMIN_PASSWORD=ChangeMeBeforeFirstLogin123
```

Minimum length is enforced by `setup.py` (see `PASSWORD_MIN_LENGTH` in `src/constants.py`).

---

### Task 5: Start Docker sidecars

**Files:**
- Run: `scripts/start-sidecars.ps1`
- Read: `docker-compose.yml:90-159`

- [ ] **Step 1: Pull and start sidecar containers**

Run:
```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start-sidecars.ps1
```
Expected: `All sidecar ports open on loopback.` and three services `Up` in `docker compose ps`.

- [ ] **Step 2: Verify containers individually**

Run:
```powershell
docker compose ps chromadb searxng ntfy
docker compose logs --tail=30 searxng
```
Expected:
- `chromadb` — Up, port `127.0.0.1:8100->8000/tcp`
- `searxng` — Up (healthy), port `127.0.0.1:8080->8080/tcp`
- `ntfy` — Up, port `127.0.0.1:8091->80/tcp`
- SearXNG logs show no `KeyError: 'default_doi_resolver'` (pinned image `2026.5.31-7159b8aed`)

- [ ] **Step 3: HTTP smoke probes**

Run:
```powershell
curl.exe -sI http://127.0.0.1:8080/ | Select-Object -First 1
curl.exe -sI http://127.0.0.1:8091/v1/health | Select-Object -First 1
```
Expected: both return `HTTP/1.1 200` (or `HTTP/2 200`).

---

### Task 6: Create Python venv and install dependencies

**Files:**
- Create: `venv/`
- Read: `requirements.txt`, `launch-windows.ps1:122-136`

- [ ] **Step 1: Create venv**

Run:
```powershell
cd "E:\Knowledge-Base\03-Development\Active-Projects\AI Workspace\odysseus"
if (-not (Test-Path .\venv\Scripts\python.exe)) {
  python -m venv venv
}
.\venv\Scripts\python.exe -c "import sys; print(sys.executable, sys.version)"
```
Expected: path under `...\odysseus\venv\Scripts\python.exe` and version ≥ 3.11.

- [ ] **Step 2: Upgrade pip and install requirements**

Run:
```powershell
.\venv\Scripts\python.exe -m pip install --upgrade pip
.\venv\Scripts\python.exe -m pip install -r requirements.txt
```
Expected: exit code 0 (first run may take several minutes).

- [ ] **Step 3: Guard against chromadb-client conflict**

Run:
```powershell
.\venv\Scripts\python.exe -m pip show chromadb-client 2>$null
if ($LASTEXITCODE -eq 0) {
  .\venv\Scripts\python.exe -m pip uninstall -y chromadb-client
  .\venv\Scripts\python.exe -m pip install --force-reinstall chromadb
}
```
Expected: either `chromadb-client` not installed, or removed and full `chromadb` reinstalled. See `docs/setup.md:279-286`.

- [ ] **Step 4: Verify core imports**

Run:
```powershell
.\venv\Scripts\python.exe -c "import fastapi, uvicorn, sqlalchemy, bcrypt, httpx, chromadb; print('imports OK')"
```
Expected: `imports OK`

---

### Task 7: Run first-time Odysseus setup

**Files:**
- Run: `setup.py`
- Creates: `data/`, `logs/`, `data/app.db`, `data/auth.json`

- [ ] **Step 1: Run setup.py**

Run:
```powershell
$env:ODYSSEUS_SKIP_RUN_HINT = "1"
.\venv\Scripts\python.exe setup.py
```
Expected output includes:
- `[ok]` for directory creation
- `[ok] Database initialized`
- `[ok] Initial admin user created` or `[skip] auth.json already exists`

Note the printed temporary admin password if `ODYSSEUS_ADMIN_PASSWORD` was not set.

- [ ] **Step 2: Verify data artifacts**

Run:
```powershell
Test-Path .\data\auth.json
Test-Path .\data\app.db
Test-Path .\logs
```
Expected: all `True`

---

### Task 8: Automated verification gate (required before completion claim)

**Files:**
- Run: `scripts/verify_local_setup.py`
- Run: focused pytest subset

- [ ] **Step 1: Run local setup verifier against live sidecars**

Run:
```powershell
.\venv\Scripts\python.exe .\scripts\verify_local_setup.py
```
Expected (all `[OK]`):
```
[OK] venv: E:\...\odysseus\venv\Scripts\python.exe
[OK] chromadb: 127.0.0.1:8100 open
[OK] searxng: http://127.0.0.1:8080/ ok
[OK] ntfy: http://127.0.0.1:8091/v1/health ok
```
Exit code: **0**

If any `[FAIL]`, do **not** claim setup complete — inspect `docker compose logs <service>` and fix before proceeding.

- [ ] **Step 2: Run unit tests for new automation**

Run:
```powershell
.\venv\Scripts\python.exe -m pytest tests/test_verify_local_setup.py tests/test_start_sidecars_script.py -v
```
Expected: all tests **PASS**, 0 failures.

- [ ] **Step 3: Run Chroma client regression tests**

Run:
```powershell
.\venv\Scripts\python.exe -m pytest tests/test_chroma_client.py tests/test_service_health.py -v --tb=short
```
Expected: all tests **PASS** (uses mocks / injected probes — no live Chroma required).

- [ ] **Step 4: App startup smoke test**

Run in one terminal:
```powershell
.\venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 7000
```

In a second terminal (after ~10s):
```powershell
curl.exe -sI http://127.0.0.1:7000/ | Select-Object -First 1
docker compose logs --tail=50 odysseus 2>$null   # only if odysseus container exists; skip for native-only
```

Expected: `HTTP/1.1 200` or `HTTP/1.1 307` from the app root.

Stop uvicorn with Ctrl+C in the first terminal when done.

- [ ] **Step 5: Check degraded services in app logs (native run)**

After uvicorn starts, watch its stdout for lines containing `DEGRADED`. With sidecars up, ChromaDB and SearXNG should **not** be degraded.

Optional API check once logged in:
```powershell
# Requires session cookie after login — manual check in browser:
# Settings -> System health should show chromadb/searxng OK when sidecars are running.
```

---

### Task 9: Document daily start/stop commands

**Files:**
- Read: `docs/setup.md`, `launch-windows.ps1`

- [ ] **Step 1: Record start sequence**

Daily dev start (two terminals or background sidecars):

```powershell
# Terminal 1 — sidecars (once per boot, or after docker restart)
powershell -ExecutionPolicy Bypass -File .\scripts\start-sidecars.ps1

# Terminal 2 — app
powershell -ExecutionPolicy Bypass -File .\launch-windows.ps1
# OR manually:
.\venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 7000
```

Open: http://127.0.0.1:7000

- [ ] **Step 2: Record stop sequence**

```powershell
# Stop app: Ctrl+C in uvicorn terminal
# Stop sidecars:
docker compose stop chromadb searxng ntfy
```

- [ ] **Step 3: Record full teardown (removes sidecar volumes — deletes Chroma/SearXNG state)**

```powershell
docker compose down -v chromadb searxng ntfy
Remove-Item -Recurse -Force .\venv, .\data, .\logs   # only when intentionally wiping local data
```

---

## Self-review checklist

| Requirement | Task |
|-------------|------|
| Python venv created with 3.11+ | Task 6 |
| `requirements.txt` installed | Task 6 |
| `setup.py` run (data/db/auth) | Task 7 |
| ChromaDB sidecar on 8100 | Tasks 5, 8 |
| SearXNG sidecar on 8080 | Tasks 5, 8 |
| ntfy sidecar on 8091 | Tasks 5, 8 |
| `.env` configured for native + loopback sidecars | Task 4 |
| TDD for new automation | Tasks 1–2 |
| Verification before completion claim | Task 8 |
| No secrets committed | Task 4 (`.env` is gitignored) |

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| SearXNG exits code 127 | Re-pull pinned image: `docker compose pull searxng && docker compose up -d searxng` |
| Port 7000 in use | `launch-windows.ps1 -Port 7001` or set `APP_PORT=7001` for Docker |
| `ChromaDB is not reachable` on app start | Run `scripts/start-sidecars.ps1`; confirm `CHROMADB_PORT=8100` |
| `Recv failure` during git/docker pull | Retry; use `gh repo clone` pattern if git HTTPS fails |
| `Permission denied` on `.cursor/` | Cursor workspace lock — harmless for app runtime |

---

**Plan complete and saved to `docs/superpowers/plans/2026-06-24-windows-venv-sidecars.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
