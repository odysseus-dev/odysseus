"""Model-picker backend: a slow/dead endpoint must not stall /api/models refresh.

Covers the fixes in routes/model_routes.py:
- the local endpoint refresh timeout default is 2s (was 10s);
- a recently failed/timed-out endpoint keeps a ~60s cooldown even on a
  user-forced refresh, so a forced refresh does not re-probe a dead endpoint
  on every picker open;
- probing is parallel, so one slow endpoint does not push the whole refresh
  past its own timeout.
"""
import json
import sys
import time
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

from tests.helpers.import_state import (
    clear_fake_endpoint_resolver_modules,
    preserve_import_state,
)


def _load_model_routes():
    with preserve_import_state(
        "core.database", "src.database", "core.session_manager", "routes.model_routes"
    ):
        clear_fake_endpoint_resolver_modules()
        if "core.database" not in sys.modules:
            stub = types.ModuleType("core.database")
            for name in (
                "SessionLocal", "ModelEndpoint", "Session", "ChatMessage", "Document",
                "DocumentVersion", "GalleryImage", "GalleryAlbum", "Note", "CalendarCal",
                "CalendarEvent", "ScheduledTask", "TaskRun", "McpServer",
                "ProviderAuthSession", "Base",
            ):
                setattr(stub, name, MagicMock())
            stub.utcnow_naive = MagicMock()
            sys.modules["core.database"] = stub
        import routes.model_routes as model_routes
    return model_routes


def _endpoint(**kw):
    base = dict(
        id="ep", base_url="http://127.0.0.1:9/v1", api_key="k",
        provider_auth_id=None, cached_models="[]", pinned_models=[],
        is_enabled=True, model_refresh_mode="auto",
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_local_refresh_timeout_is_two_seconds():
    model_routes = _load_model_routes()
    ep = _endpoint(base_url="http://100.67.207.85:8071/v1")
    assert model_routes._endpoint_refresh_timeout(ep, "local") == 2.0
    assert model_routes._endpoint_refresh_timeout(ep, "remote") == 2.0


def test_recent_failure_is_not_reprobed_even_when_forced():
    model_routes = _load_model_routes()
    router = model_routes.setup_model_routes(model_discovery=None)
    should = router._should_refresh_endpoint
    ep = _endpoint(id="remote-dead", base_url="http://100.67.207.85:8071/v1")

    now = time.time()
    ok, info = should(ep, now, force=True)
    assert ok is True and info.get("base")
    key = info["key"]

    # A failure one second ago keeps a 60s cooldown even on a forced refresh.
    router._refresh_state[key] = {
        "fail_count": 1, "last_failure": now - 1.0, "inflight": False,
    }
    ok_recent, _ = should(ep, time.time(), force=True)
    assert ok_recent is False

    # A failure long past the 60s floor is re-probed again.
    router._refresh_state[key] = {
        "fail_count": 1, "last_failure": now - 120.0, "inflight": False,
    }
    ok_old, _ = should(ep, time.time(), force=True)
    assert ok_old is True


class _FakeQuery:
    def __init__(self, rows, first_value):
        self._rows = rows
        self._first = first_value

    def filter(self, *args, **kwargs):
        return self

    def all(self):
        return list(self._rows)

    def first(self):
        return self._first


class _FakeDB:
    def __init__(self, rows, first_value):
        self._rows = rows
        self._first = first_value
        self.committed = False

    def query(self, model):
        return _FakeQuery(self._rows, self._first)

    def commit(self):
        self.committed = True

    def close(self):
        pass


def test_one_slow_endpoint_does_not_stall_the_forced_refresh(monkeypatch):
    model_routes = _load_model_routes()
    router = model_routes.setup_model_routes(model_discovery=None)

    healthy = _endpoint(id="healthy", base_url="http://127.0.0.1:7021/v1")
    dead = _endpoint(id="dead", base_url="http://127.0.0.1:9/v1")

    monkeypatch.setattr(model_routes, "SessionLocal", lambda: _FakeDB([healthy, dead], healthy))
    monkeypatch.setattr(
        model_routes, "_disable_stale_cookbook_local_endpoints", lambda db: False
    )

    def _fake_probe(base, api_key=None, timeout=5):
        if "127.0.0.1:9" in base:
            time.sleep(min(float(timeout or 2), 2.0))
            raise RuntimeError("timed out")
        return ["healthy-model"]

    monkeypatch.setattr(model_routes, "_probe_endpoint", _fake_probe)

    started = time.time()
    done = router._refresh_caches_bg(force=True)
    assert done is not None
    assert done.wait(5) is True
    elapsed = time.time() - started

    assert elapsed <= 3.0, f"forced refresh took {elapsed:.2f}s"
    assert json.loads(healthy.cached_models) == ["healthy-model"]
