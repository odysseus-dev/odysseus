from pathlib import Path


def test_memory_audit_resolves_runtime_credentials():
    source = Path("routes/memory_routes.py").read_text()
    start = source.index("async def api_audit_memories")
    end = source.index('@router.post("/import")', start)
    audit_route = source[start:end]

    assert 'resolve_endpoint(' in audit_route
    assert '"default"' in audit_route
    assert "owner=user" in audit_route
    assert "fallback_headers=fallback_headers" in audit_route
    assert "ModelEndpoint" not in audit_route
    assert ".api_key" not in audit_route
