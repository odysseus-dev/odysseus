"""Focused tests for the product/API layer (PR 3).

Covers:
  - brain endpoints register and return structured snapshots (no raw
    exception strings leaked to clients — CodeQL finding fixed)
  - the Brain tab + sleep ledger are integrated directly in the repo UI
  - claim-audit degrades unsupported strong claims on output
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "memory_platform"))


class FakeManager:
    def load(self):
        return [{"id": "e1", "text": "a plain fact", "category": "fact"}]


def test_brain_endpoints_register():
    from fastapi import FastAPI
    from routes.memory.graph_routes import setup_graph_routes
    app = FastAPI()
    app.include_router(setup_graph_routes(FakeManager(), None))
    paths = [getattr(r, "path", "") for r in app.router.routes]
    for p in ("/api/memory-brain/overview",
              "/api/memory-brain/pressure",
              "/api/memory-brain/sleep"):
        assert p in paths, f"{p} not registered"


def test_overview_returns_structured_snapshot():
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    from routes.memory.graph_routes import setup_graph_routes
    app = FastAPI()
    app.include_router(setup_graph_routes(FakeManager(), None))
    c = TestClient(app)
    r = c.get("/api/memory-brain/overview")
    assert r.status_code == 200
    d = r.json()
    assert "associations" in d
    assert "neurons" in d
    # CodeQL fix: no raw exception string in the response
    assert "note" not in d or "Error" not in str(d["note"])


def test_sleep_without_engine_is_graceful():
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    from routes.memory.graph_routes import setup_graph_routes
    app = FastAPI()
    app.include_router(setup_graph_routes(FakeManager(), None))  # no sleep engine
    c = TestClient(app)
    r = c.post("/api/memory-brain/sleep")
    assert r.status_code == 200
    assert r.json().get("ran") is False


def test_brain_tab_in_repo_ui():
    """The Brain tab is committed directly in the repo (not a post-install
    patch script)."""
    idx = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "index.html")).read()
    assert 'data-memory-tab="associations"' in idx
    memjs = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "js", "memory.js")).read()
    assert "loadBrain" in memjs


def test_claim_audit_degrades_strong_claim():
    import claim_audit
    draft = "This is definitely the best system ever built, proven to work 100%."
    claims = claim_audit.scan(draft)
    assert claims, "strong claim should be flagged"
    softened, _note = claim_audit.degrade(draft, "DEGRADE")
    assert softened != draft
