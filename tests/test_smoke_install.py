"""Fresh-install smoke tests (Phase 1): the floor must be green.

Covers the cheapest release-confidence gate:
1. Python version requirement (3.11+).
2. Core dependency imports (fastapi, uvicorn, sqlalchemy, bcrypt, httpx, dotenv).
3. .env.example parses as KEY=VALUE lines (setup.py copies it verbatim).
4. internal_api_base() honors APP_PORT (never hardcode :7000).
5. Full app boots via TestClient and /api/health is healthy even with
   optional services (Chroma/SearXNG/Ollama) unreachable.

Pattern followed: tests/test_internal_api_base.py (env-scoped base helper +
no-hardcoded-loopback guard) and setup.py::check_deps (same module list).
"""
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
CORE_DEPS = ["fastapi", "uvicorn", "sqlalchemy", "bcrypt", "httpx", "dotenv"]


def test_python_is_311_plus():
    assert sys.version_info >= (3, 11), f"need 3.11+, got {sys.version}"


@pytest.mark.parametrize("mod", CORE_DEPS)
def test_core_dependency_imports(mod):
    __import__(mod)


def test_env_example_parses():
    example = REPO / ".env.example"
    assert example.exists(), ".env.example must exist (setup.py copies it)"
    bad = []
    for n, line in enumerate(example.read_text(encoding="utf-8").splitlines(), 1):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if "=" not in s:
            bad.append((n, line))
    assert not bad, f".env.example has non KEY=VALUE lines: {bad[:5]}"


def test_internal_api_base_honors_app_port(monkeypatch):
    import core.constants as cc

    for k in ("ODYSSEUS_INTERNAL_BASE", "APP_PORT"):
        monkeypatch.delenv(k, raising=False)
    assert cc.internal_api_base() == "http://127.0.0.1:7000"
    monkeypatch.setenv("APP_PORT", "7001")
    assert cc.internal_api_base() == "http://127.0.0.1:7001"


def test_app_boots_and_health_is_healthy(monkeypatch):
    """Full app boots; health is healthy with optional services unreachable."""
    import os

    # Point every optional-service probe at dead ports so the test proves
    # graceful degradation instead of depending on the dev machine.
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("CHROMADB_HOST", "127.0.0.1")
    monkeypatch.setenv("CHROMADB_PORT", "9")  # discard port: nothing listens
    monkeypatch.setenv("SEARXNG_INSTANCE", "http://127.0.0.1:9")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:9/v1")
    # Keep the embedding stack local-only (no HF download in CI).
    monkeypatch.setenv("EMBEDDING_URL", "")
    os.environ.pop("EMBEDDING_MODEL", None)

    from fastapi.testclient import TestClient
    from app import app

    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/api/health")
    assert resp.status_code == 200, resp.text[:500]
    assert resp.json().get("status") == "healthy"
