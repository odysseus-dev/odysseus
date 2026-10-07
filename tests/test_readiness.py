"""Tests for the readiness / integrity self-check (src/readiness.py)."""

from src.readiness import check_readiness


def test_readiness_reports_core_subsystems():
    result = check_readiness()

    assert {"ready", "version", "checks", "timestamp"}.issubset(result.keys())
    checks = result["checks"]
    for name in ("database", "data_dir", "local_first", "tool_index"):
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


def test_tool_index_is_reported_but_not_critical_by_default(monkeypatch):
    import src.tool_index as tool_index

    monkeypatch.delenv("ODYSSEUS_REQUIRE_TOOL_INDEX_READY", raising=False)
    monkeypatch.setattr(
        tool_index,
        "get_tool_index_status",
        lambda: {"state": "degraded", "ready": False, "error_type": "ProbeFailure"},
    )

    result = check_readiness()

    assert result["ready"] is True
    assert result["checks"]["tool_index"] == {
        "state": "degraded",
        "ready": False,
        "error_type": "ProbeFailure",
        "prewarm_enabled": True,
        "ok": False,
        "critical": False,
    }


def test_harness_can_require_tool_index_readiness(monkeypatch):
    import src.tool_index as tool_index

    monkeypatch.setenv("ODYSSEUS_REQUIRE_TOOL_INDEX_READY", "1")
    monkeypatch.setattr(
        tool_index,
        "get_tool_index_status",
        lambda: {"state": "warming", "ready": False, "attempts": 1},
    )

    result = check_readiness()

    assert result["ready"] is False
    assert result["checks"]["tool_index"]["critical"] is True
    assert result["checks"]["tool_index"]["ok"] is False


def test_required_ready_tool_index_allows_readiness(monkeypatch):
    import src.tool_index as tool_index

    monkeypatch.setenv("ODYSSEUS_REQUIRE_TOOL_INDEX_READY", "true")
    monkeypatch.setattr(
        tool_index,
        "get_tool_index_status",
        lambda: {"state": "ready", "ready": True, "attempts": 1},
    )

    result = check_readiness()

    assert result["ready"] is True
    assert result["checks"]["tool_index"]["critical"] is True
    assert result["checks"]["tool_index"]["ok"] is True
