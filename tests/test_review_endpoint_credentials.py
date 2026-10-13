from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.task_endpoint import _same_endpoint_base, resolve_task_candidates


@pytest.mark.parametrize("url", [
    "https://untrusted.test/https://api.example.test/v1/chat/completions",
    "https://api.example.test.evil.test/v1",
    "http://api.example.test/v1",
    "https://api.example.test:444/v1",
    "https://api.example.test/v10",
    "https://api.example.test/v1?redirect=elsewhere",
    "https://user@api.example.test/v1",
])
def test_unrelated_override_receives_no_saved_credentials(monkeypatch, url):
    import src.database as database
    import src.endpoint_resolver as resolver
    import src.task_endpoint as tasks

    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = [
        SimpleNamespace(base_url="https://api.example.test/v1")
    ]
    monkeypatch.setattr(database, "SessionLocal", lambda: db)
    runtime = MagicMock(return_value=("https://api.example.test/v1", "dummy-secret"))
    monkeypatch.setattr(resolver, "resolve_endpoint_runtime", runtime)
    monkeypatch.setattr(tasks, "resolve_task_endpoint", lambda *a, **k: (None, None, {}))
    monkeypatch.setattr(tasks, "resolve_endpoint", lambda *a, **k: (None, None, {}))
    monkeypatch.setattr(tasks, "resolve_utility_fallback_candidates", lambda **k: [])
    candidates = resolve_task_candidates(override_url=url, override_model="test")
    assert candidates == [(url, "test", {})]
    runtime.assert_not_called()


def test_exact_api_base_allows_normalized_chat_path():
    assert _same_endpoint_base("https://API.example.test:443/v1/chat/completions", "https://api.example.test/v1/")
    assert not _same_endpoint_base("https://api.example.test/v1", "")


@pytest.mark.parametrize("resolver_kind", ["task", "skill"])
@pytest.mark.parametrize("owner,url,expected", [
    ("alice", "https://api.example.test/v1/chat/completions", "Bearer alice-secret"),
    ("bob", "https://api.example.test/v1/chat/completions", None),
    ("alice", "https://api.example.test.evil.test/v1", None),
    ("alice", "https://evil.test/https://api.example.test/v1", None),
])
def test_credential_resolution_is_exact_and_owner_scoped(monkeypatch, tmp_path, resolver_kind, owner, url, expected):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    import src.database as database
    import src.endpoint_resolver as resolver
    import src.task_endpoint as tasks
    import src.llm_core as llm
    import src.settings as settings

    engine = create_engine(f"sqlite:///{tmp_path / 'endpoints.db'}")
    database.ModelEndpoint.__table__.create(engine)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        db.add(database.ModelEndpoint(id="private", name="Private", owner="alice",
                                     base_url="https://api.example.test/v1",
                                     api_key="alice-secret", is_enabled=True))
        db.commit()
    monkeypatch.setattr(database, "SessionLocal", factory)
    monkeypatch.setattr(resolver, "resolve_endpoint_runtime", lambda ep, **kw: (ep.base_url, ep.api_key))
    monkeypatch.setattr(tasks, "resolve_task_endpoint", lambda *a, **kw: (None, None, {}))
    monkeypatch.setattr(tasks, "resolve_endpoint", lambda *a, **kw: (None, None, {}))
    monkeypatch.setattr(tasks, "resolve_utility_fallback_candidates", lambda **kw: [])
    monkeypatch.setattr(llm, "list_model_ids", lambda *a, **kw: [])
    monkeypatch.setattr(settings, "get_setting", lambda key, default=None: default)
    try:
        if resolver_kind == "task":
            headers = tasks.resolve_task_candidates(override_url=url, override_model="test", owner=owner)[0][2]
        else:
            from routes.skills_routes import _resolve_audit_models
            headers = _resolve_audit_models(owner=owner, endpoint_url=url, model_spec="test")[2]
        assert headers.get("Authorization") == expected
    finally:
        engine.dispose()
