from types import SimpleNamespace

from routes.chat_routes import _reconcile_selected_route_from_request


class _Query:
    def __init__(self, endpoint):
        self.endpoint = endpoint

    def filter(self, *args):
        return self

    def first(self):
        return self.endpoint


class _Db:
    def __init__(self, endpoint, session_row):
        self.endpoint = endpoint
        self.session_row = session_row

    def query(self, model):
        if getattr(model, "__name__", "") == "ModelEndpoint":
            return _Query(self.endpoint)
        return _Query(self.session_row)

    def commit(self):
        pass

    def close(self):
        pass


def test_selected_endpoint_hydrates_headers_when_model_and_url_match(monkeypatch):
    endpoint = SimpleNamespace(
        id="endpoint-1",
        is_enabled=True,
        base_url="https://openrouter.ai/api/v1",
        api_key="secret-key",
        owner="alice",
    )
    session_row = SimpleNamespace(
        model="moonshotai/kimi-k3",
        endpoint_url="https://openrouter.ai/api/v1/chat/completions",
        headers={},
        updated_at=None,
    )
    db = _Db(endpoint, session_row)
    monkeypatch.setattr("routes.chat_routes.SessionLocal", lambda: db)
    monkeypatch.setattr("routes.chat_routes.owner_filter", lambda query, *args, **kwargs: query, raising=False)
    sess = SimpleNamespace(
        model="moonshotai/kimi-k3",
        endpoint_url="https://openrouter.ai/api/v1/chat/completions",
        headers={},
    )
    form = {
        "selected_model": "moonshotai/kimi-k3",
        "selected_endpoint_id": "endpoint-1",
        "selected_endpoint_url": "https://openrouter.ai/api/v1",
    }

    changed = _reconcile_selected_route_from_request(
        SimpleNamespace(), sess, "session-1", form, owner=None
    )

    assert changed is True
    assert sess.headers.get("Authorization") == "Bearer secret-key"
    assert session_row.headers == sess.headers
