"""Tests for the readiness / integrity self-check (src/readiness.py)."""

from src.readiness import check_readiness


def test_readiness_reports_core_subsystems():
    result = check_readiness()

    assert {"ready", "version", "checks", "timestamp"}.issubset(result.keys())
    checks = result["checks"]
    for name in (
        "database", "data_dir", "local_first", "auth", "household_repo",
        "skills", "task_scheduler", "vector_memory", "model_backend",
        "misumi_interface",
    ):
        assert name in checks, f"missing check: {name}"

    # In the dev/test environment the local SQLite DB and data dir are present,
    # so the critical checks must pass and overall readiness must be True.
    assert checks["database"]["ok"] is True, checks["database"]
    assert checks["data_dir"]["ok"] is True, checks["data_dir"]
    assert result["ready"] is True, result


def test_local_first_check_is_informational_never_fatal():
    result = check_readiness()
    lf = result["checks"]["local_first"]
    # local_first reports whether storage stays on-host but must never gate
    # readiness — a remote database is a valid deployment.
    assert lf["ok"] is True
    assert "local" in lf


def test_readiness_rejects_unauthenticated_network_bind(monkeypatch):
    monkeypatch.setenv("APP_BIND", "0.0.0.0")
    monkeypatch.setenv("AUTH_ENABLED", "false")

    result = check_readiness()

    assert result["ready"] is False
    assert result["checks"]["auth"]["ok"] is False


def test_misumi_required_makes_household_and_model_critical(monkeypatch, tmp_path):
    monkeypatch.setenv("MISUMI_REQUIRED", "1")
    monkeypatch.setenv("MISUMI_HOUSEHOLD_ROOT", str(tmp_path / "missing"))
    monkeypatch.delenv("MISUMI_MODEL_HEALTH_URL", raising=False)

    result = check_readiness()

    assert result["ready"] is False
    assert result["checks"]["household_repo"]["critical"] is True
    assert result["checks"]["model_backend"]["critical"] is True
