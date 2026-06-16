"""_resolve_model must not hold a DB session open across the model-list probe.

ai_interaction imports src.database lazily (inside functions), so importing the
module at collection time is safe under the conftest src.database stub. The
deferred `from src.database import SessionLocal, ModelEndpoint` inside
_resolve_model reads sys.modules['src.database'] at call time, so we inject our
fakes by setting attributes on that stub module.
"""
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx

from src import ai_interaction


def test_resolve_model_closes_session_before_network_probe(monkeypatch):
    events = []

    endpoint = SimpleNamespace(
        base_url="http://localhost:8000/v1",
        api_key="sk-test",
        provider_auth_id=None,
        cached_models='["gpt-4o"]',
    )

    class _DB:
        def query(self, *a, **k):
            return self

        def filter(self, *a, **k):
            return self

        def all(self):
            return [endpoint]

        def close(self):
            events.append("close")

    src_database = sys.modules["src.database"]
    monkeypatch.setattr(src_database, "SessionLocal", lambda: _DB())
    monkeypatch.setattr(src_database, "ModelEndpoint", MagicMock())

    def _fake_get(url, **kw):
        events.append("probe")
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        resp.json.return_value = {"data": [{"id": "gpt-4o"}]}
        return resp

    monkeypatch.setattr(httpx, "get", _fake_get)

    url, model, headers = ai_interaction._resolve_model("gpt-4o")

    assert "close" in events, "the DB session must be closed"
    assert "probe" in events, "the model-list probe must run"
    assert events.index("close") < events.index("probe"), \
        f"session must be closed BEFORE the network probe, got order: {events}"
    assert model == "gpt-4o"
