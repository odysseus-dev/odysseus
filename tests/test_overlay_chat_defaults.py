"""Overlay interactive chat defaults are 9router routes, not ModelEndpoints.

Agents: GET /api/default-chat must return automatic/fast/balanced/best with
empty endpoint fields. Leftover endpoint names must not become overlay chat.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

from tests.helpers.import_state import preserve_import_state

with preserve_import_state("core.database", "src.database", "routes.model_routes", "routes.prefs_routes"):
    import routes.model_routes as model_routes
    import routes.prefs_routes as prefs_routes


def _get_default_chat():
    router = model_routes.setup_model_routes(model_discovery=None)
    for route in router.routes:
        if getattr(route, "path", "") == "/api/default-chat" and "GET" in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError("GET /api/default-chat missing")


def _request(user="alice", admin=False):
    return SimpleNamespace(
        state=SimpleNamespace(current_user=user),
        app=SimpleNamespace(
            state=SimpleNamespace(
                auth_manager=SimpleNamespace(is_admin=lambda _u: admin)
            )
        ),
        client=SimpleNamespace(host="127.0.0.1"),
    )


def test_default_chat_returns_saved_overlay_route(monkeypatch):
    monkeypatch.setattr(
        model_routes,
        "_load_settings",
        lambda: {"default_model": "fast", "share_defaults_with_users": False},
    )
    monkeypatch.setattr(prefs_routes, "_load_for_user", lambda _user: {})
    result = _get_default_chat()(_request(admin=True))
    assert result["model"] == "fast"
    assert result["route"] == "fast"
    assert result["endpoint_id"] == ""
    assert result["endpoint_url"] == ""


def test_default_chat_leftover_endpoint_name_becomes_automatic(monkeypatch):
    monkeypatch.setattr(
        model_routes,
        "_load_settings",
        lambda: {
            "default_model": "qwen-3.6",
            "default_endpoint_id": "ep-1",
            "share_defaults_with_users": True,
        },
    )
    monkeypatch.setattr(prefs_routes, "_load_for_user", lambda _user: {})
    result = _get_default_chat()(_request(admin=True))
    assert result["model"] == "automatic"
    assert result["endpoint_id"] == ""


def test_default_chat_empty_prefs_are_automatic(monkeypatch):
    monkeypatch.setattr(model_routes, "_load_settings", lambda: {})
    monkeypatch.setattr(prefs_routes, "_load_for_user", lambda _user: {})
    result = _get_default_chat()(_request(admin=False))
    assert result["model"] == "automatic"
    assert result["route"] == "automatic"


def test_overlay_session_bind_empty_url_uses_9router_v1(monkeypatch):
    """Chat Native Hello! posts empty endpoint_url; bind 9router, skip leftover."""
    monkeypatch.setenv("NINE_ROUTER_METADATA_URL", "http://9router:20128")
    bound = model_routes.overlay_session_bind("automatic", "", "")
    assert bound == ("http://9router:20128/v1", "automatic")
    assert model_routes.overlay_session_bind("fast", None, None) == (
        "http://9router:20128/v1",
        "fast",
    )
    assert model_routes.overlay_session_bind("", "", "") == (
        "http://9router:20128/v1",
        "automatic",
    )
    leftover = model_routes.overlay_session_bind(
        "llama3", "", "http://127.0.0.1:11434/v1"
    )
    assert leftover is None
    leftover_id = model_routes.overlay_session_bind("automatic", "ep-1", "")
    assert leftover_id is None


def test_session_create_binds_overlay_before_endpoint_url_gate():
    """POST /api/session must not 400 overlay composer empty leftover URL."""
    from pathlib import Path

    src = Path(__file__).resolve().parent.parent.joinpath(
        "routes/session_routes.py"
    ).read_text(encoding="utf-8")
    assert "overlay_session_bind" in src
    assert src.index("overlay_session_bind") < src.index(
        'endpoint_url is required (choose from /api/models)'
    )
    sessions_js = Path(__file__).resolve().parent.parent.joinpath(
        "static/js/sessions.js"
    ).read_text(encoding="utf-8")
    assert "overlayRoutes" in sessions_js
    assert "if (dc && dc.model) return dc;" in sessions_js
    slash = Path(__file__).resolve().parent.parent.joinpath(
        "static/js/slashCommands.js"
    ).read_text(encoding="utf-8")
    assert "if (dc.model)" in slash
    assert "overlayOk" in slash


def test_is_overlay_ninerouter_url_matches_compose_host(monkeypatch):
    monkeypatch.setenv("NINE_ROUTER_METADATA_URL", "http://9router:20128")
    assert model_routes.is_overlay_ninerouter_url("http://9router:20128/v1") is True
    assert model_routes.is_overlay_ninerouter_url(
        "http://9router:20128/v1/chat/completions"
    ) is True
    assert model_routes.is_overlay_ninerouter_url("http://127.0.0.1:11434/v1") is False
    assert model_routes.is_overlay_ninerouter_url("") is False


def test_clear_orphaned_skips_overlay_9router_session(monkeypatch):
    """Hello! on overlay chat must not 400 leftover ModelEndpoint orphan clear."""
    import routes.chat_routes as chat_routes

    sess = SimpleNamespace(
        id="s1",
        endpoint_url="http://9router:20128/v1",
        model="automatic",
        headers={},
    )

    def _no_leftover_db():
        raise AssertionError("overlay 9router is not a leftover ModelEndpoint")

    monkeypatch.setattr(chat_routes, "SessionLocal", _no_leftover_db)
    assert chat_routes._clear_orphaned_session_endpoint(sess) is False
    assert sess.model == "automatic"
    assert sess.endpoint_url == "http://9router:20128/v1"


class _FakeQuery:
    def __init__(self, rows):
        self._rows = list(rows)

    def filter(self, *args, **kwargs):
        return self

    def all(self):
        return list(self._rows)


class _FakeDb:
    def __init__(self, endpoints, sessions):
        self.endpoints = list(endpoints)
        self.sessions = list(sessions)
        self.deleted = []
        self.committed = False

    def query(self, model):
        name = getattr(model, "__name__", "")
        if name == "ModelEndpoint":
            return _FakeQuery(self.endpoints)
        return _FakeQuery(self.sessions)

    def delete(self, row):
        self.deleted.append(row)
        if row in self.endpoints:
            self.endpoints.remove(row)

    def commit(self):
        self.committed = True


def test_purge_deletes_cloud_rows_and_keeps_local(monkeypatch):
    """Slice D leftover: openai.com keys leave Odysseus; ollama stays."""
    provider_auth_calls = []

    def _record_provider_auth(db, auth_id, exclude_ep_id=None):
        provider_auth_calls.append((auth_id, exclude_ep_id))
        return False

    monkeypatch.setattr(
        model_routes, "_delete_orphaned_provider_auth", _record_provider_auth
    )
    cloud = SimpleNamespace(
        id="ep-cloud",
        base_url="https://api.openai.com/v1",
        api_key="sk-secret",
        provider_auth_id="auth-chatgpt",
    )
    local = SimpleNamespace(
        id="ep-local",
        base_url="http://ollama:11434/v1",
        api_key="",
        provider_auth_id=None,
    )
    sess = SimpleNamespace(
        endpoint_url="https://api.openai.com/v1/chat/completions",
        model="gpt-4o",
        headers={"Authorization": "Bearer sk-secret"},
    )
    saved = {}
    monkeypatch.setenv("NINE_ROUTER_METADATA_URL", "http://9router:20128")
    monkeypatch.setattr(model_routes, "_load_settings", lambda: {"default_endpoint_id": "ep-cloud"})
    monkeypatch.setattr(model_routes, "_save_settings", lambda s: saved.update(s))
    monkeypatch.setattr(
        model_routes,
        "_clear_user_pref_endpoint_refs",
        lambda prefs, ep_id: 0,
    )
    db = _FakeDb([cloud, local], [sess])
    result = model_routes.purge_leftover_cloud_model_endpoints(db)
    assert result["deleted"] == 1
    assert result["sessions"] == 1
    assert provider_auth_calls == [("auth-chatgpt", "ep-cloud")]
    assert db.committed is True
    assert cloud in db.deleted
    assert local not in db.deleted
    assert db.endpoints == [local]
    assert sess.endpoint_url == "http://9router:20128/v1"
    assert sess.model == "automatic"
    assert sess.headers == {}
    assert saved.get("default_endpoint_id") == ""


def test_purge_noop_when_only_local_leftover(monkeypatch):
    local = SimpleNamespace(id="ep-lan", base_url="http://192.168.1.10:8080/v1", api_key="")
    monkeypatch.setattr(model_routes, "_load_settings", lambda: {})
    monkeypatch.setattr(model_routes, "_save_settings", lambda _s: None)
    db = _FakeDb([local], [])
    result = model_routes.purge_leftover_cloud_model_endpoints(db)
    assert result["deleted"] == 0
    assert db.deleted == []
    assert db.committed is False
