from src.readiness import check_readiness


def test_readiness_includes_sidecar_checks(monkeypatch):
    monkeypatch.setattr("src.readiness._port_open", lambda h, p, timeout=None: h == "localhost" and p == 8100)
    monkeypatch.setattr(
        "src.readiness._http_ok",
        lambda url, timeout=2.0: url == "http://localhost:8080/",
    )

    result = check_readiness()
    checks = result["checks"]

    assert "chromadb" in checks
    assert checks["chromadb"]["ok"] is True
    assert checks["chromadb"]["host"] == "localhost"
    assert checks["chromadb"]["port"] == 8100

    assert "searxng" in checks
    assert checks["searxng"]["ok"] is True
    assert "http://localhost:8080" in checks["searxng"]["url"]


def test_sidecar_checks_are_informational_do_not_gate_ready(monkeypatch):
    monkeypatch.setattr("src.readiness._port_open", lambda *a, **k: False)
    monkeypatch.setattr("src.readiness._http_ok", lambda *a, **k: False)

    result = check_readiness()

    assert result["checks"]["chromadb"]["ok"] is False
    assert result["checks"]["searxng"]["ok"] is False
    assert result["ready"] is True
