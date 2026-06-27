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
            ok = http_ok(spec["url"], timeout=3.0)
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
