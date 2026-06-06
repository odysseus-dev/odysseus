"""Local LLM router model picker — backend wiring (PR1; JS tests live on UI branch)."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHAT_ROUTES = ROOT / "routes" / "chat_routes.py"


def test_chat_routes_wires_route_reasons_and_requested_model():
    source = CHAT_ROUTES.read_text(encoding="utf-8")
    assert "route_reasons" in source
    assert "requested_model" in source
