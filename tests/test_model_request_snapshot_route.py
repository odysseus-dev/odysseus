from pathlib import Path


def test_chat_route_forwards_model_request_snapshot_event():
    source = (Path(__file__).resolve().parents[1] / "routes" / "chat_routes.py").read_text(encoding="utf-8")
    assert '"model_request_snapshot"' in source
