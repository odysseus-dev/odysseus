import src.model_discovery as md
from src.model_discovery import ModelDiscovery


class _Resp:
    is_success = True

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def test_check_port_handles_non_list_data(monkeypatch):
    # A misconfigured /v1/models endpoint can return {"data": {...}} instead of
    # a list; the old comprehension iterated the dict KEYS and m.get crashed.
    monkeypatch.setattr(md.httpx, "get", lambda *a, **k: _Resp({"data": {"id": "x"}}))
    disc = ModelDiscovery("localhost")
    assert disc._check_port("h", 1234) is None


def test_check_port_returns_models_and_skips_non_dict_items(monkeypatch):
    monkeypatch.setattr(
        md.httpx, "get",
        lambda *a, **k: _Resp({"data": [{"id": "llama"}, "junk", {"id": "qwen"}]}),
    )
    disc = ModelDiscovery("localhost")
    monkeypatch.setattr(disc, "_fingerprint_provider", lambda host, port: None)
    res = disc._check_port("h", 1234)
    assert res is not None
    assert res["models"] == ["llama", "qwen"]
