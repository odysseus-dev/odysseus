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


def test_compact_tailscale_status_removes_keys_and_keeps_peer_summary():
    from routes.openclaw_homelab_routes import _compact_tailscale_status
    result = _compact_tailscale_status({
        'Version': '1.0',
        'BackendState': 'Running',
        'Self': {'HostName': 'heimdal', 'PublicKey': 'nodekey:secret', 'TailscaleIPs': ['100.1.1.1'], 'Online': True},
        'Peer': {
            'nodekey:secret': {'HostName': 'mac', 'PublicKey': 'nodekey:secret2', 'TailscaleIPs': ['100.2.2.2'], 'Online': True}
        },
    })
    assert result['self']['host_name'] == 'heimdal'
    assert result['peers'][0]['host_name'] == 'mac'
    assert 'PublicKey' not in result['self']
    assert 'PublicKey' not in result['peers'][0]



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
        lambda: {'status': 'ok', 'tailscale': {'peer_count': 1, 'peers': [{'host_name': 'one'}]}, 'check': {'status': 'ok'}},
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


@pytest.mark.asyncio
async def test_memory_usage_endpoint_returns_table(monkeypatch):
    monkeypatch.setattr(
        'routes.openclaw_homelab_routes._run_static_command',
        lambda args, timeout=8: {'status': 'ok', 'returncode': 0, 'stdout': 'Mem: 16G', 'stderr': ''},
    )
    router = setup_openclaw_homelab_routes()
    ep = _endpoint(router, '/api/openclaw/homelab/ops/memory-usage', 'GET')
    result = await ep(_request(scopes=['homelab:read']))
    assert result['ops']['kind'] == 'memory_usage'
    assert 'Mem:' in result['ops']['table']


@pytest.mark.asyncio
async def test_dns_check_endpoint_returns_ips(monkeypatch):
    import socket
    monkeypatch.setattr(socket, 'gethostbyname_ex', lambda d: (d, [], ['192.168.1.1']))
    router = setup_openclaw_homelab_routes()
    ep = _endpoint(router, '/api/openclaw/homelab/ops/dns-check', 'GET')
    result = await ep(_request(scopes=['homelab:read']), domain='router.local')
    assert result['ops']['kind'] == 'dns_check'
    assert '192.168.1.1' in result['ops']['check']['ips']


@pytest.mark.asyncio
async def test_caddy_routes_endpoint_returns_config(monkeypatch):
    import httpx
    class MockResponse:
        status_code = 200
        def json(self): return {"apps": {"http": {"servers": {}}}}
    
    class MockAsyncClient:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, url): return MockResponse()

    monkeypatch.setattr(httpx, 'AsyncClient', MockAsyncClient)
    router = setup_openclaw_homelab_routes()
    ep = _endpoint(router, '/api/openclaw/homelab/ops/caddy-routes', 'GET')
    result = await ep(_request(scopes=['homelab:read']))
    assert result['ops']['kind'] == 'caddy_routes'
    assert 'apps' in result['ops']['check']['config']


@pytest.mark.asyncio
async def test_netbox_sync_status_endpoint_returns_output(monkeypatch):
    monkeypatch.setattr(
        'routes.openclaw_homelab_routes._run_static_command',
        lambda args, timeout=8: {'status': 'ok', 'returncode': 0, 'stdout': 'Sync complete', 'stderr': ''},
    )
    router = setup_openclaw_homelab_routes()
    ep = _endpoint(router, '/api/openclaw/homelab/ops/netbox-sync-status', 'GET')
    result = await ep(_request(scopes=['homelab:read']))
    assert result['ops']['kind'] == 'netbox_sync_status'
    assert 'Sync complete' in result['ops']['output']


@pytest.mark.asyncio
async def test_redmine_status_endpoint_returns_health(monkeypatch):
    import httpx
    monkeypatch.setattr('os.getenv', lambda k, d=None: "http://redmine:3000" if k == "CONVERGE_BASE_URL" else ("secret" if k == "CONVERGE_API_KEY" else d))
    
    class MockResponse:
        status_code = 200
        def json(self): return {"status": "ok", "db": "connected"}
    
    class MockAsyncClient:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, url, headers=None): return MockResponse()

    monkeypatch.setattr(httpx, 'AsyncClient', MockAsyncClient)
    router = setup_openclaw_homelab_routes()
    ep = _endpoint(router, '/api/openclaw/homelab/ops/redmine-status', 'GET')
    result = await ep(_request(scopes=['homelab:read']))
    assert result['ops']['kind'] == 'redmine_status'
    assert result['ops']['check']['health']['db'] == 'connected'


@pytest.mark.asyncio
async def test_github_failed_endpoint_returns_output(monkeypatch):
    monkeypatch.setattr(
        'routes.openclaw_homelab_routes._run_static_command',
        lambda args, timeout=8: {'status': 'ok', 'returncode': 0, 'stdout': 'build-push-image  failure', 'stderr': ''},
    )
    router = setup_openclaw_homelab_routes()
    ep = _endpoint(router, '/api/openclaw/homelab/ops/github-failed', 'GET')
    result = await ep(_request(scopes=['homelab:read']))
    assert result['ops']['kind'] == 'github_failed'
    assert 'build-push-image' in result['ops']['output']


@pytest.mark.asyncio
async def test_ollama_models_endpoint_returns_table(monkeypatch):
    monkeypatch.setattr(
        'routes.openclaw_homelab_routes._run_static_command',
        lambda args, timeout=8: {'status': 'ok', 'returncode': 0, 'stdout': 'llama3:8b', 'stderr': ''},
    )
    router = setup_openclaw_homelab_routes()
    ep = _endpoint(router, '/api/openclaw/homelab/ops/ollama-models', 'GET')
    result = await ep(_request(scopes=['homelab:read']))
    assert result['ops']['kind'] == 'ollama_models'
    assert 'llama3' in result['ops']['table']


@pytest.mark.asyncio
async def test_daily_brief_endpoint(monkeypatch, mock_event_store):
    monkeypatch.setattr('routes.openclaw_inbox_routes._triage_state', lambda o: {'total_unread': 5, 'total_urgent': 1})
    mock_event_store.record_event('source', 'svc', 'critical', 'T', 'S', 'k')
    
    class MockN8nClient:
        configured = True
        async def get_failed_executions_summary(self):
            return {'failed_count': 3}
    monkeypatch.setattr('src.n8n_client.N8nClient', MockN8nClient)
    
    router = setup_openclaw_homelab_routes()
    ep = _endpoint(router, '/api/openclaw/homelab/ops/daily-brief', 'GET')
    result = await ep(_request(scopes=['homelab:read']))
    
    assert result['ops']['kind'] == 'daily_brief'
    brief = result['ops']
    assert brief['inbox']['total_unread'] == 5
    assert brief['events']['critical'] == 1
    assert brief['n8n']['failed_count'] == 3
    assert 'overall_status' in brief['health']


# ---------------------------------------------------------------------------
# Write Ops
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_docker_restart_requires_confirm(monkeypatch):
    from routes.openclaw_homelab_routes import DockerRestartRequest
    router = setup_openclaw_homelab_routes()
    ep = _endpoint(router, '/api/openclaw/homelab/ops/docker-restart', 'POST')
    with pytest.raises(HTTPException) as exc:
        await ep(_request(scopes=['homelab:write']), body=DockerRestartRequest(container='caddy', confirm=False))
    assert exc.value.status_code == 400
    assert 'confirm=true' in exc.value.detail


@pytest.mark.asyncio
async def test_docker_restart_success(monkeypatch):
    from routes.openclaw_homelab_routes import DockerRestartRequest
    monkeypatch.setattr(
        'routes.openclaw_homelab_routes._docker_api_request',
        lambda path, method, timeout: {'status': 'ok', 'http_status': 204}
    )
    router = setup_openclaw_homelab_routes()
    ep = _endpoint(router, '/api/openclaw/homelab/ops/docker-restart', 'POST')
    result = await ep(_request(scopes=['homelab:write']), body=DockerRestartRequest(container='pihole', confirm=True))
    assert result['ops']['kind'] == 'docker_restart'
    assert result['ops']['container'] == 'pihole'


@pytest.mark.asyncio
async def test_create_redmine_ticket_requires_confirm(mock_event_store):
    from routes.openclaw_homelab_routes import RedmineTicketRequest
    router = setup_openclaw_homelab_routes()
    ep = _endpoint(router, '/api/openclaw/homelab/events/{event_id}/redmine-ticket', 'POST')
    with pytest.raises(HTTPException) as exc:
        await ep(_request(scopes=['homelab:write']), event_id='evt-123', body=RedmineTicketRequest(confirm=False))
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_create_redmine_ticket_success(mock_event_store, monkeypatch):
    from routes.openclaw_homelab_routes import RedmineTicketRequest
    event = mock_event_store.record_event('source', 'svc', 'critical', 'Title', 'Sum', 'k')
    
    import httpx
    monkeypatch.setattr('routes.openclaw_bridge_routes._converge_config', lambda: ("http://base", "key"))
    
    class MockResponse:
        status_code = 201
        def json(self): return {"issue": {"id": 999}}
        
    class MockAsyncClient:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, url, headers=None, json=None): return MockResponse()

    monkeypatch.setattr(httpx, 'AsyncClient', MockAsyncClient)
    router = setup_openclaw_homelab_routes()
    ep = _endpoint(router, '/api/openclaw/homelab/events/{event_id}/redmine-ticket', 'POST')
    result = await ep(_request(scopes=['homelab:write']), event_id=event['id'], body=RedmineTicketRequest(confirm=True))
    assert result['ops']['kind'] == 'create_redmine_ticket'
    assert result['ops']['issue_id'] == 999


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
