"""The admin research diagnostic must use the caller's configured model."""
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.diagnostics_routes import setup_diagnostics_routes
from src import endpoint_resolver


@pytest.fixture
def diagnostic(monkeypatch):
    monkeypatch.setenv('AUTH_ENABLED', 'true')
    called = []

    async def research(query, url, model, **kwargs):
        called.append((query, url, model, kwargs))
        return 'Synthetic diagnostic result'

    app = FastAPI()
    app.state.auth_manager = SimpleNamespace(is_configured=True, is_admin=lambda user: user == 'alice')

    @app.middleware('http')
    async def identity(request, call_next):
        request.state.current_user = request.headers.get('x-fixture-user', 'alice')
        return await call_next(request)

    app.include_router(setup_diagnostics_routes(None, False, SimpleNamespace(call_research_service=research)))
    return TestClient(app), called


def test_configured_research_endpoint_model_headers_and_owner(diagnostic, monkeypatch):
    client, called = diagnostic
    resolved = []
    headers = {'Authorization': 'Bearer synthetic-fixture-key'}

    def resolve(prefix, *, owner):
        resolved.append((prefix, owner))
        return 'http://127.0.0.1:18973/v1/chat/completions', 'configured-research', headers

    monkeypatch.setattr(endpoint_resolver, 'resolve_endpoint', resolve)
    response = client.post('/api/test-research', data={'query': 'Synthetic question'})
    assert response.status_code == 200
    assert response.json()['status'] == 'success'
    assert resolved == [('research', 'alice')]
    assert called == [('Synthetic question', 'http://127.0.0.1:18973/v1/chat/completions', 'configured-research', {'llm_headers': headers})]


@pytest.mark.parametrize('selected', ['utility', 'default'])
def test_missing_research_selection_uses_configured_fallback(diagnostic, monkeypatch, selected):
    client, called = diagnostic
    resolved = []

    def resolve(prefix, *, owner):
        resolved.append((prefix, owner))
        return ('http://model.test/v1/chat/completions', selected, {}) if prefix == selected else (None, None, None)

    monkeypatch.setattr(endpoint_resolver, 'resolve_endpoint', resolve)
    data = client.post('/api/test-research').json()
    assert data['status'] == 'success'
    assert called[0][1:3] == ('http://model.test/v1/chat/completions', selected)
    assert resolved == [(prefix, 'alice') for prefix in ('research', 'utility', 'default')[:('research', 'utility', 'default').index(selected) + 1]]


@pytest.mark.parametrize('unusable', [(None, None, None), ('http://model.test/v1/chat/completions', '', {}), (None, 'model', {})])
def test_unconfigured_or_partial_selection_does_not_launch(diagnostic, monkeypatch, unusable):
    client, called = diagnostic
    monkeypatch.setattr(endpoint_resolver, 'resolve_endpoint', lambda *a, **k: unusable)
    data = client.post('/api/test-research', data={'query': 'Synthetic question'}).json()
    assert data['status'] == 'error'
    assert 'configured' in data['error'].lower()
    assert data['query'] == 'Synthetic question'
    assert called == []


def test_nonadmin_cannot_resolve_or_launch(diagnostic, monkeypatch):
    client, called = diagnostic
    monkeypatch.setattr(endpoint_resolver, 'resolve_endpoint', lambda *a, **k: pytest.fail('non-admin resolved model'))
    response = client.post('/api/test-research', headers={'x-fixture-user': 'bob'})
    assert response.status_code == 403
    assert called == []


def test_resolver_failure_remains_a_diagnostic_error(diagnostic, monkeypatch):
    client, called = diagnostic

    def fail(*a, **k):
        raise RuntimeError('Synthetic resolver unavailable')

    monkeypatch.setattr(endpoint_resolver, 'resolve_endpoint', fail)
    data = client.post('/api/test-research').json()
    assert data['status'] == 'error'
    assert called == []
