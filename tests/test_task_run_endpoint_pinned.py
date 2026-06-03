"""Regression: _resolve_run_endpoint must match admin-pinned model IDs.

The recent-runs listing (GET /api/tasks/{id}/runs) exposes an `endpoint_url`
for reopening a task run in chat, computed by `_resolve_run_endpoint`. It
matched the task/run model only against each endpoint's `cached_models`. Admin
pinned model IDs (cloud deployment IDs) live in `pinned_models` and never
appear in `cached_models`, so a task pinned to such a model resolved to an
empty endpoint URL and could not be reopened against its real endpoint.
"""
import os
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import MagicMock

for _m in ["pyotp"]:
    if _m not in sys.modules:
        try:
            __import__(_m)
        except Exception:
            sys.modules[_m] = MagicMock()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_DB_PATH = tempfile.mktemp(suffix=".db")
os.environ["DATABASE_URL"] = "sqlite:///" + _DB_PATH


def test_resolve_run_endpoint_matches_pinned_model():
    import core.database as db
    db.init_db()
    import routes.task_routes as tr

    s = db.SessionLocal()
    try:
        s.add(db.ModelEndpoint(
            id="ep-pin", name="Cloud", base_url="https://cloud.example.com/v1",
            is_enabled=True, cached_models=None,
            pinned_models='["my-cloud-deploy-id"]', model_type="llm",
        ))
        s.commit()
    finally:
        s.close()

    task = SimpleNamespace(endpoint_url=None, session_id=None, model="my-cloud-deploy-id")
    run = SimpleNamespace(model=None)

    s2 = db.SessionLocal()
    try:
        url = tr._resolve_run_endpoint(s2, task, run)
    finally:
        s2.close()

    # Old code matched cached_models only (empty here) → "". With pinned
    # matching, the endpoint resolves.
    assert url == "https://cloud.example.com/v1", repr(url)
