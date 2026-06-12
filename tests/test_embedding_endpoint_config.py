import json

import routes.embedding_routes as embedding_routes


def test_load_custom_endpoint_ignores_non_object_json(tmp_path, monkeypatch):
    endpoint_file = tmp_path / "embedding_endpoint.json"
    endpoint_file.write_text(json.dumps(["not", "an", "endpoint", "object"]), encoding="utf-8")
    monkeypatch.setattr(embedding_routes, "_ENDPOINT_FILE", str(endpoint_file))

    assert embedding_routes._load_custom_endpoint() == {}


def test_load_custom_endpoint_keeps_object_json(tmp_path, monkeypatch):
    endpoint_file = tmp_path / "embedding_endpoint.json"
    endpoint_file.write_text(
        json.dumps({"url": "http://127.0.0.1:11434", "model": "nomic-embed-text"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(embedding_routes, "_ENDPOINT_FILE", str(endpoint_file))

    assert embedding_routes._load_custom_endpoint() == {
        "url": "http://127.0.0.1:11434",
        "model": "nomic-embed-text",
    }
