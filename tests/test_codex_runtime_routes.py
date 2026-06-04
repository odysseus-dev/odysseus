from types import SimpleNamespace

import routes.codex_runtime_routes as routes


def _handler(method, fragment):
    router = routes.setup_codex_runtime_routes()
    for route in router.routes:
        if fragment in getattr(route, "path", "") and method.upper() in (getattr(route, "methods", None) or set()):
            return route.endpoint
    raise AssertionError(f"missing {method} route containing {fragment}")


def _request():
    return SimpleNamespace(state=SimpleNamespace(current_user="admin"))


def test_status_route_is_read_only(monkeypatch):
    called = {"status": 0, "ensure": 0}
    monkeypatch.setattr(routes, "require_admin", lambda request: None)
    monkeypatch.setattr(routes, "codex_runtime_status", lambda: called.update(status=called["status"] + 1) or {"state": "ready"})
    monkeypatch.setattr(routes, "ensure_codex_runtime_endpoint_registered", lambda: called.update(ensure=called["ensure"] + 1))

    result = _handler("GET", "/status")(_request())

    assert result == {"state": "ready"}
    assert called == {"status": 1, "ensure": 0}


def test_reconcile_route_registers_endpoint(monkeypatch):
    called = {"status": 0, "ensure": 0}
    monkeypatch.setattr(routes, "require_admin", lambda request: None)
    monkeypatch.setattr(routes, "codex_runtime_status", lambda: called.update(status=called["status"] + 1) or {"state": "ready"})
    monkeypatch.setattr(
        routes,
        "ensure_codex_runtime_endpoint_registered",
        lambda: called.update(ensure=called["ensure"] + 1) or {"registered": True, "changed": True},
    )

    result = _handler("POST", "/reconcile")(_request())

    assert result["state"] == "ready"
    assert result["endpoint_registration"] == {"registered": True, "changed": True}
    assert called == {"status": 1, "ensure": 1}


def test_probe_route_returns_probe(monkeypatch):
    monkeypatch.setattr(routes, "require_admin", lambda request: None)
    monkeypatch.setattr(routes, "codex_runtime_probe", lambda: {"state": "ready", "auth_ready": True})

    assert _handler("POST", "/probe")(_request()) == {"state": "ready", "auth_ready": True}
