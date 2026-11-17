from pathlib import Path


def test_chat_route_forwards_tool_routing_audit_events():
    source = (Path(__file__).parents[1] / "routes" / "chat_routes.py").read_text()

    assert '"tool_routing_audit"' in source
    assert '"tool_resolution_audit"' in source
