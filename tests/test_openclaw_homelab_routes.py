"""Tests for OpenClaw homelab command-facing routes (Phase 3)."""

from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from routes.openclaw_homelab_routes import (
    _compact_event,
    _safe_actions,
    _sanitize_service,
    setup_openclaw_homelab_routes,
)
from routes.api_token_routes import ALLOWED_SCOPES, TOKEN_PROFILES


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _request(*, api_token=True, scopes=None, owner='alice'):
    state = SimpleNamespace(
        api_token=api_token,
        api_token_scopes=scopes or [],
        api_token_owner=owner,
        current_user='browser-user',
    )
    return SimpleNamespace(state=state)


def _endpoint(router, path: str, method: str = 'GET'):
    """Retrieve the raw endpoint callable from a router by path+method."""
    for route in router.routes:
        if route.path == path and method in [m.upper() for m in route.methods]:
            return route.endpoint
    raise KeyError(f'{method} {path} not found in router')


@pytest.fixture()
def mock_event_store(tmp_path, monkeypatch):
    from src.event_store import EventStore
    store = EventStore(file_path=str(tmp_path / 'events.json'))
    monkeypatch.setattr('routes.openclaw_homelab_routes.EventStore', lambda: store)
    return store


# ---------------------------------------------------------------------------
# Scope / token profile tests
# ---------------------------------------------------------------------------

def test_homelab_scopes_registered_in_allowed_scopes():
    for scope in ('homelab:read', 'events:read', 'events:write', 'events:ack', 'events:resolve'):
        assert scope in ALLOWED_SCOPES, f'{scope} missing from ALLOWED_SCOPES'


def test_openclaw_bridge_profile_updated_with_homelab_scopes():
    profile = TOKEN_PROFILES['openclaw_bridge']
    for scope in ('homelab:read', 'events:read', 'events:ack', 'events:resolve'):
        assert scope in profile, f'{scope} missing from openclaw_bridge profile'


# ---------------------------------------------------------------------------
# _safe_actions – no destructive verbs (allowlist)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('forbidden', ['restart', 'restart_service', 'shell', 'exec', 'delete', 'workflow', 'unknown'])
def test_safe_actions_strips_forbidden(forbidden):
    actions = ['ack', 'investigate', forbidden, 'view_service']
    result = _safe_actions(actions)
    assert forbidden not in result
    assert 'ack' in result
    assert 'investigate' in result


def test_safe_actions_keeps_safe_verbs():
    actions = ['ack', 'investigate', 'resolve', 'ignore', 'view_service']
    assert set(_safe_actions(actions)) == set(actions)


# ---------------------------------------------------------------------------
# _sanitize_service – redact sensitive fields
# ---------------------------------------------------------------------------

def test_sanitize_service_redacts_sensitive_fields():
    raw = {
        'name': 'pihole',
        'url': 'http://pihole.local',
        'api_key': 'secret123',
        'headers': {'X-Test': 'value', 'Authorization': 'Bearer 1234'},
        'auth': {'username': 'admin'},
        'secrets': ['a', 'b'],
        'apiKey': 'camelCaseKey',
        'accessToken': 'tokenValue',
        'authHeader': 'Bearer xyz',
        'nested': [{'token': 'abc', 'public': 'yes'}],
        'public_list': ['one', 'two'],
        'author': 'Jane Doe',
    }
    # Original dict should not be mutated
    raw_copy = copy.deepcopy(raw)
    
    clean = _sanitize_service(raw)
    assert clean['name'] == 'pihole'
    assert clean['url'] == 'http://pihole.local'
    assert clean['api_key'] == '***REDACTED***'
    assert clean['headers'] == '***REDACTED***'
    assert clean['auth'] == '***REDACTED***'
    assert clean['secrets'] == '***REDACTED***'
    assert clean['apiKey'] == '***REDACTED***'
    assert clean['accessToken'] == '***REDACTED***'
    assert clean['authHeader'] == '***REDACTED***'
    assert clean['nested'][0]['token'] == '***REDACTED***'
    assert clean['nested'][0]['public'] == 'yes'
    assert clean['public_list'] == ['one', 'two']
    assert clean['author'] == 'Jane Doe'
    
    assert raw == raw_copy


def test_run_static_command_uses_shell_false(monkeypatch):
    calls = {}

    def fake_run(args, **kwargs):
        calls['args'] = args
        calls['kwargs'] = kwargs
        return SimpleNamespace(returncode=0, stdout='ok\n', stderr='')

    monkeypatch.setattr('routes.openclaw_homelab_routes.subprocess.run', fake_run)
    from routes.openclaw_homelab_routes import _run_static_command
    result = _run_static_command(['df', '-h'])
    assert result['status'] == 'ok'
    assert calls['args'] == ['df', '-h']
    assert calls['kwargs']['shell'] is False


def test_json_lines_sanitizes_sensitive_fields():
    from routes.openclaw_homelab_routes import _json_lines
    rows = _json_lines('{"Names":"web","Token":"secret"}\nnot-json')
    assert rows[0]['Token'] == '***REDACTED***'
    assert rows[1]['raw'] == 'not-json'


def test_docker_unhealthy_containers_uses_socket_response(monkeypatch):
    monkeypatch.setattr(
        'routes.openclaw_homelab_routes._docker_api_request',
        lambda path, timeout=8: {
            'status': 'ok',
            'http_status': 200,
            'body': '[{"Id":"abc","Names":["/web"],"Image":"nginx","Status":"unhealthy","State":"running"}]',
        },
    )
    from routes.openclaw_homelab_routes import _docker_unhealthy_containers
    result = _docker_unhealthy_containers()
    assert result['containers'][0]['ID'] == 'abc'
    assert result['containers'][0]['Names'] == 'web'


def test_docker_logs_uses_socket_response(monkeypatch):
    calls = {}

    def fake_request(path, timeout=8):
        calls['path'] = path
        return {'status': 'ok', 'http_status': 200, 'body': '\x02\x00\x00recent logs'}

    monkeypatch.setattr('routes.openclaw_homelab_routes._docker_api_request', fake_request)
    from routes.openclaw_homelab_routes import _docker_container_logs
    result = _docker_container_logs('caddy', 50)
    assert 'tail=50' in calls['path']
    assert result['logs'] == 'recent logs'
    assert 'body' not in result['check']



# ---------------------------------------------------------------------------
# _compact_event – response shape
# ---------------------------------------------------------------------------

def test_compact_event_shape():
    raw = {
        'id': 'abc-123',
        'service': 'pihole',
        'severity': 'critical',
        'status': 'new',
        'title': 'Pihole is error',
        'summary': 'Container: exited, HTTP: unreachable',
        'count': 3,
        'first_seen': '2026-01-01T00:00:00+00:00',
        'last_seen': '2026-01-01T01:00:00+00:00',
        'owner': 'alice',
        'suggested_actions': ['ack', 'restart', 'view_service'],
    }
    out = _compact_event(raw)
    assert out['id'] == 'abc-123'
    assert 'restart' not in out['suggested_actions']
    assert 'ack' in out['suggested_actions']
    assert 'links' in out
    assert 'ack' in out['links']
    assert 'resolve' in out['links']
    assert 'self' in out['links']


# ---------------------------------------------------------------------------
# Scope enforcement on mutation routes
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ack_requires_events_ack_scope(mock_event_store):
    router = setup_openclaw_homelab_routes()
    ack = _endpoint(router, '/api/openclaw/homelab/events/{event_id}/ack', 'POST')

    req = _request(scopes=['events:read'])  # wrong scope
    with pytest.raises(HTTPException) as exc:
        await ack(req, event_id='does-not-matter')
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_resolve_requires_events_resolve_scope(mock_event_store):
    router = setup_openclaw_homelab_routes()
    resolve = _endpoint(router, '/api/openclaw/homelab/events/{event_id}/resolve', 'POST')

    req = _request(scopes=['events:ack'])  # wrong scope
    with pytest.raises(HTTPException) as exc:
        await resolve(req, event_id='does-not-matter')
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_ignore_requires_events_resolve_scope(mock_event_store):
    router = setup_openclaw_homelab_routes()
    ignore = _endpoint(router, '/api/openclaw/homelab/events/{event_id}/ignore', 'POST')

    req = _request(scopes=['events:ack'])  # wrong scope
    with pytest.raises(HTTPException) as exc:
        await ignore(req, event_id='does-not-matter')
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_investigate_requires_events_ack_scope(mock_event_store):
    router = setup_openclaw_homelab_routes()
    investigate = _endpoint(
        router, '/api/openclaw/homelab/events/{event_id}/investigate', 'POST'
    )

    req = _request(scopes=['events:resolve'])  # wrong scope
    with pytest.raises(HTTPException) as exc:
        await investigate(req, event_id='does-not-matter')
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_list_events_requires_events_read_scope(mock_event_store):
    router = setup_openclaw_homelab_routes()
    list_ep = _endpoint(router, '/api/openclaw/homelab/events', 'GET')

    req = _request(scopes=['homelab:read'])  # wrong scope
    with pytest.raises(HTTPException) as exc:
        await list_ep(req)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_list_services_requires_homelab_read_scope(mock_event_store):
    router = setup_openclaw_homelab_routes()
    list_ep = _endpoint(router, '/api/openclaw/homelab/services', 'GET')

    req = _request(scopes=['events:read'])  # wrong scope
    with pytest.raises(HTTPException) as exc:
        await list_ep(req)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_get_service_requires_homelab_read_scope(mock_event_store):
    router = setup_openclaw_homelab_routes()
    get_ep = _endpoint(router, '/api/openclaw/homelab/services/{name}', 'GET')

    req = _request(scopes=['events:read'])  # wrong scope
    with pytest.raises(HTTPException) as exc:
        await get_ep(req, name='does-not-matter')
    assert exc.value.status_code == 403


# ---------------------------------------------------------------------------
# Missing event → 404, not 500
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ack_missing_event_returns_404(mock_event_store):
    router = setup_openclaw_homelab_routes()
    ack = _endpoint(router, '/api/openclaw/homelab/events/{event_id}/ack', 'POST')
    req = _request(scopes=['events:ack'])
    with pytest.raises(HTTPException) as exc:
        await ack(req, event_id='no-such-id')
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_investigate_missing_event_returns_404(mock_event_store):
    router = setup_openclaw_homelab_routes()
    investigate = _endpoint(
        router, '/api/openclaw/homelab/events/{event_id}/investigate', 'POST'
    )
    req = _request(scopes=['events:ack'])
    with pytest.raises(HTTPException) as exc:
        await investigate(req, event_id='no-such-id')
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_resolve_missing_event_returns_404(mock_event_store):
    router = setup_openclaw_homelab_routes()
    resolve = _endpoint(router, '/api/openclaw/homelab/events/{event_id}/resolve', 'POST')
    req = _request(scopes=['events:resolve'])
    with pytest.raises(HTTPException) as exc:
        await resolve(req, event_id='no-such-id')
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_ignore_missing_event_returns_404(mock_event_store):
    router = setup_openclaw_homelab_routes()
    ignore = _endpoint(router, '/api/openclaw/homelab/events/{event_id}/ignore', 'POST')
    req = _request(scopes=['events:resolve'])
    with pytest.raises(HTTPException) as exc:
        await ignore(req, event_id='no-such-id')
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_get_missing_event_returns_404(mock_event_store):
    router = setup_openclaw_homelab_routes()
    get_ep = _endpoint(router, '/api/openclaw/homelab/events/{event_id}', 'GET')
    req = _request(scopes=['events:read'])
    with pytest.raises(HTTPException) as exc:
        await get_ep(req, event_id='no-such-id')
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_get_missing_service_returns_404(mock_event_store, monkeypatch):
    router = setup_openclaw_homelab_routes()
    get_ep = _endpoint(router, '/api/openclaw/homelab/services/{name}', 'GET')
    monkeypatch.setattr('routes.openclaw_homelab_routes._load_services', lambda: [])
    req = _request(scopes=['homelab:read'])
    with pytest.raises(HTTPException) as exc:
        await get_ep(req, name='no-such-name')
    assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# Successful lifecycle flow — response envelope shape
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_full_lifecycle_response_shape(mock_event_store):
    e = mock_event_store.record_event(
        'openclaw_health', 'pihole', 'critical', 'Pihole is error', 'down', 'homelab:pihole:health'
    )
    event_id = e['id']
    router = setup_openclaw_homelab_routes()

    for path, method, scope, new_status in [
        ('/api/openclaw/homelab/events/{event_id}/ack', 'POST', 'events:ack', 'acknowledged'),
        ('/api/openclaw/homelab/events/{event_id}/investigate', 'POST', 'events:ack', 'investigating'),
        ('/api/openclaw/homelab/events/{event_id}/resolve', 'POST', 'events:resolve', 'resolved'),
    ]:
        ep = _endpoint(router, path, method)
        req = _request(scopes=[scope])
        result = await ep(req, event_id=event_id)
        assert result['status'] == 'ok'
        assert 'message' in result
        assert result['event']['status'] == new_status
        assert 'links' in result['event']
        assert 'restart' not in result['event']['suggested_actions']
        assert 'requires_approval' in result


@pytest.mark.asyncio
async def test_list_events_envelope(mock_event_store):
    mock_event_store.record_event(
        'openclaw_health', 'pihole', 'critical', 'title', 'summary', 'k1'
    )
    router = setup_openclaw_homelab_routes()
    list_ep = _endpoint(router, '/api/openclaw/homelab/events', 'GET')
    req = _request(scopes=['events:read'])
    result = await list_ep(req)
    assert result['status'] == 'ok'
    assert isinstance(result['events'], list)
    assert len(result['events']) == 1
    assert 'links' in result


@pytest.mark.asyncio
async def test_list_events_invalid_limit_returns_400(mock_event_store):
    router = setup_openclaw_homelab_routes()
    list_ep = _endpoint(router, '/api/openclaw/homelab/events', 'GET')
    req = _request(scopes=['events:read'])
    with pytest.raises(HTTPException) as exc:
        await list_ep(req, limit=0)
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_list_services_envelope(mock_event_store, monkeypatch):
    monkeypatch.setattr(
        'routes.openclaw_homelab_routes._load_services',
        lambda: [{'name': 'pihole'}]
    )
    router = setup_openclaw_homelab_routes()
    list_ep = _endpoint(router, '/api/openclaw/homelab/services', 'GET')
    req = _request(scopes=['homelab:read'])
    result = await list_ep(req)
    assert result['status'] == 'ok'
    assert isinstance(result['services'], list)
    assert len(result['services']) == 1
    assert 'links' in result


@pytest.mark.asyncio
async def test_get_service_envelope(mock_event_store, monkeypatch):
    monkeypatch.setattr(
        'routes.openclaw_homelab_routes._load_services',
        lambda: [{'name': 'pihole'}]
    )
    router = setup_openclaw_homelab_routes()
    get_ep = _endpoint(router, '/api/openclaw/homelab/services/{name}', 'GET')
    req = _request(scopes=['homelab:read'])
    result = await get_ep(req, name='pihole')
    assert result['status'] == 'ok'
    assert result['service']['name'] == 'pihole'
    assert 'links' in result


@pytest.mark.asyncio
async def test_docker_unhealthy_endpoint_returns_containers(monkeypatch):
    monkeypatch.setattr(
        'routes.openclaw_homelab_routes._docker_unhealthy_containers',
        lambda: {'containers': [{'Names': 'bad', 'Status': 'unhealthy'}], 'check': {'status': 'ok'}},
    )
    router = setup_openclaw_homelab_routes()
    ep = _endpoint(router, '/api/openclaw/homelab/ops/docker-unhealthy', 'GET')
    result = await ep(_request(scopes=['homelab:read']))
    assert result['status'] == 'ok'
    assert result['ops']['kind'] == 'docker_unhealthy'
    assert result['ops']['containers'][0]['Names'] == 'bad'


@pytest.mark.asyncio
async def test_tailscale_status_endpoint_returns_peer_count(monkeypatch):
    monkeypatch.setattr(
        'routes.openclaw_homelab_routes._tailscale_status',
        lambda: {'status': 'ok', 'tailscale': {'Peer': {'one': {}}}, 'check': {'status': 'ok'}},
    )
    router = setup_openclaw_homelab_routes()
    ep = _endpoint(router, '/api/openclaw/homelab/ops/tailscale-status', 'GET')
    result = await ep(_request(scopes=['homelab:read']))
    assert result['ops']['kind'] == 'tailscale_status'
    assert '1 peer' in result['message']


@pytest.mark.asyncio
async def test_ping_heimdal_endpoint_uses_static_target(monkeypatch):
    calls = {}

    def fake_run(args, timeout=8):
        calls['args'] = args
        return {'status': 'ok', 'returncode': 0, 'stdout': 'pong', 'stderr': ''}

    monkeypatch.setattr('routes.openclaw_homelab_routes._run_static_command', fake_run)
    router = setup_openclaw_homelab_routes()
    ep = _endpoint(router, '/api/openclaw/homelab/ops/ping-heimdal', 'GET')
    result = await ep(_request(scopes=['homelab:read']))
    assert result['ops']['kind'] == 'ping_heimdal'
    assert calls['args'][:3] == ['ping', '-c', '3']


@pytest.mark.asyncio
async def test_grafana_endpoint_reports_missing_config(monkeypatch):
    monkeypatch.setattr('routes.openclaw_homelab_routes._find_grafana_url', lambda: None)
    router = setup_openclaw_homelab_routes()
    ep = _endpoint(router, '/api/openclaw/homelab/ops/grafana', 'GET')
    result = await ep(_request(scopes=['homelab:read']))
    assert result['ops']['kind'] == 'grafana'
    assert result['ops']['status'] == 'degraded'


@pytest.mark.asyncio
async def test_caddy_logs_endpoint_limits_lines(monkeypatch):
    router = setup_openclaw_homelab_routes()
    ep = _endpoint(router, '/api/openclaw/homelab/ops/caddy-logs', 'GET')
    with pytest.raises(HTTPException) as exc:
        await ep(_request(scopes=['homelab:read']), lines=0)
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_caddy_logs_endpoint_returns_logs(monkeypatch):
    monkeypatch.setattr(
        'routes.openclaw_homelab_routes._docker_container_logs',
        lambda container, lines: {'logs': 'line1', 'check': {'status': 'ok'}},
    )
    router = setup_openclaw_homelab_routes()
    ep = _endpoint(router, '/api/openclaw/homelab/ops/caddy-logs', 'GET')
    result = await ep(_request(scopes=['homelab:read']))
    assert result['ops']['kind'] == 'caddy_logs'
    assert result['ops']['logs'] == 'line1'


@pytest.mark.asyncio
async def test_disk_usage_endpoint_returns_table(monkeypatch):
    monkeypatch.setattr(
        'routes.openclaw_homelab_routes._run_static_command',
        lambda args, timeout=8: {'status': 'ok', 'returncode': 0, 'stdout': 'Filesystem Size', 'stderr': ''},
    )
    router = setup_openclaw_homelab_routes()
    ep = _endpoint(router, '/api/openclaw/homelab/ops/disk-usage', 'GET')
    result = await ep(_request(scopes=['homelab:read']))
    assert result['ops']['kind'] == 'disk_usage'
    assert 'Filesystem' in result['ops']['table']


# ---------------------------------------------------------------------------
# Persistence failure → 500 (not 404 swallow)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_persistence_failure_returns_500(mock_event_store, monkeypatch):
    e = mock_event_store.record_event(
        'openclaw_health', 'pihole', 'critical', 'title', 'summary', 'k1'
    )
    router = setup_openclaw_homelab_routes()

    def mock_save(*args):
        raise IOError('disk full')
    monkeypatch.setattr('src.event_store.EventStore._save', mock_save)

    for path, scope in [
        ('/api/openclaw/homelab/events/{event_id}/ack', 'events:ack'),
        ('/api/openclaw/homelab/events/{event_id}/investigate', 'events:ack'),
        ('/api/openclaw/homelab/events/{event_id}/resolve', 'events:resolve'),
        ('/api/openclaw/homelab/events/{event_id}/ignore', 'events:resolve'),
    ]:
        ep = _endpoint(router, path, 'POST')
        req = _request(scopes=[scope])
        with pytest.raises(HTTPException) as exc:
            await ep(req, event_id=e['id'])
        assert exc.value.status_code == 500
