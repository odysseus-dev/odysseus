"""Tests for homelab routes — Phase 2.1 performance hardening."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch, mock_open

import pytest
from fastapi import HTTPException

from routes.homelab_routes import (
    _load_services,
    _check_service_health,
    _inspect_container_status,
    _get_concurrency,
    setup_homelab_routes,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _request(*, api_token=True, scopes=None, owner='alice', current_user='browser-user'):
    state = SimpleNamespace(
        api_token=api_token,
        api_token_scopes=scopes or [],
        api_token_owner=owner,
        current_user=current_user,
    )
    return SimpleNamespace(state=state, app=SimpleNamespace(state=SimpleNamespace(auth_manager=None)))


def _endpoint(router, path: str, method: str):
    for route in router.routes:
        if getattr(route, 'path', None) == path and method in getattr(route, 'methods', set()):
            return route.endpoint
    raise AssertionError(f'route not found: {method} {path}')


# ---------------------------------------------------------------------------
# _load_services
# ---------------------------------------------------------------------------

def test_load_services_returns_empty_when_file_missing(monkeypatch):
    monkeypatch.setattr('os.path.exists', lambda path: False)
    assert _load_services() == []


def test_load_services_loads_valid_config(monkeypatch):
    monkeypatch.setattr('os.path.exists', lambda path: True)
    mock_data = '{"services": [{"name": "pihole", "host": "1.2.3.4"}]}'
    with patch('builtins.open', mock_open(read_data=mock_data)):
        services = _load_services()
    assert len(services) == 1
    assert services[0]['name'] == 'pihole'


# ---------------------------------------------------------------------------
# _get_concurrency
# ---------------------------------------------------------------------------

def test_concurrency_defaults_to_5(monkeypatch):
    monkeypatch.delenv('HOMELAB_HEALTH_CONCURRENCY', raising=False)
    assert _get_concurrency() == 5


def test_concurrency_reads_from_env(monkeypatch):
    monkeypatch.setenv('HOMELAB_HEALTH_CONCURRENCY', '10')
    assert _get_concurrency() == 10


def test_concurrency_clamps_to_5_on_invalid_value(monkeypatch):
    monkeypatch.setenv('HOMELAB_HEALTH_CONCURRENCY', 'not-a-number')
    assert _get_concurrency() == 5


def test_concurrency_clamps_zero_to_1(monkeypatch):
    monkeypatch.setenv('HOMELAB_HEALTH_CONCURRENCY', '0')
    assert _get_concurrency() == 1


def test_concurrency_clamps_above_50(monkeypatch):
    monkeypatch.setenv('HOMELAB_HEALTH_CONCURRENCY', '999')
    assert _get_concurrency() == 50


# ---------------------------------------------------------------------------
# _inspect_container_status (sync helper)
# ---------------------------------------------------------------------------

def test_inspect_container_status_running(monkeypatch):
    class FakeResult:
        returncode = 0
        stdout = 'running\n'

    monkeypatch.setattr('subprocess.run', lambda *a, **kw: FakeResult())
    cstatus, is_running = _inspect_container_status('my_container')
    assert cstatus == 'running'
    assert is_running is True


def test_inspect_container_status_stopped(monkeypatch):
    class FakeResult:
        returncode = 0
        stdout = 'exited\n'

    monkeypatch.setattr('subprocess.run', lambda *a, **kw: FakeResult())
    cstatus, is_running = _inspect_container_status('my_container')
    assert cstatus == 'exited'
    assert is_running is False


def test_inspect_container_status_not_found(monkeypatch):
    class FakeResult:
        returncode = 1
        stdout = ''

    monkeypatch.setattr('subprocess.run', lambda *a, **kw: FakeResult())
    cstatus, is_running = _inspect_container_status('missing_container')
    assert cstatus == 'not_found'
    assert is_running is False


def test_inspect_container_status_exception(monkeypatch):
    def raise_exc(*a, **kw):
        raise OSError('docker not found')

    monkeypatch.setattr('subprocess.run', raise_exc)
    cstatus, is_running = _inspect_container_status('any')
    assert cstatus == 'check_failed'
    assert is_running is False


def test_inspect_uses_shell_false(monkeypatch):
    """Ensure shell=False is always passed, preventing arbitrary shell injection."""
    calls = []

    class FakeResult:
        returncode = 0
        stdout = 'running\n'

    def capture(*a, **kw):
        calls.append(kw)
        return FakeResult()

    monkeypatch.setattr('subprocess.run', capture)
    _inspect_container_status('c1')
    assert calls[0].get('shell') is False


# ---------------------------------------------------------------------------
# _check_service_health — Docker path uses asyncio.to_thread
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_docker_check_uses_to_thread(monkeypatch):
    """_check_service_health must call asyncio.to_thread for container checks."""
    to_thread_calls = []

    async def fake_to_thread(fn, *args, **kwargs):
        to_thread_calls.append(fn)
        return fn(*args, **kwargs)

    monkeypatch.setattr('asyncio.to_thread', fake_to_thread)
    monkeypatch.setattr(
        'routes.homelab_routes._inspect_container_status',
        lambda c: ('running', True),
    )

    await _check_service_health({'name': 'svc', 'container': 'c1'})
    assert len(to_thread_calls) == 1


@pytest.mark.asyncio
async def test_check_service_health_docker(monkeypatch):
    monkeypatch.setattr(
        'routes.homelab_routes._inspect_container_status',
        lambda c: ('running', True) if 'running' in c else ('exited', False),
    )

    res1 = await _check_service_health({'name': 'test1', 'container': 'running_container'})
    assert res1['status'] == 'ok'
    assert res1['container_status'] == 'running'

    res2 = await _check_service_health({'name': 'test2', 'container': 'stopped_container'})
    assert res2['status'] == 'error'
    assert res2['container_status'] == 'exited'


# ---------------------------------------------------------------------------
# _check_service_health — HTTP client reuse
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_http_client_is_reused_when_provided(monkeypatch):
    """When a shared client is passed, no new AsyncClient is created."""
    new_client_calls = []
    original_init = __import__('httpx').AsyncClient.__init__

    class TrackingClient:
        def __init__(self, **kw):
            new_client_calls.append(1)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        async def get(self, url):
            return MagicMock(status_code=200)

    monkeypatch.setattr('httpx.AsyncClient', TrackingClient)
    monkeypatch.setattr(
        'routes.homelab_routes._inspect_container_status',
        lambda c: ('running', True),
    )

    shared = MagicMock()
    shared.get = AsyncMock(return_value=MagicMock(status_code=200))

    # Pass a shared client — TrackingClient constructor must NOT be called.
    new_client_calls.clear()
    await _check_service_health({'name': 's', 'health_url': 'http://ok.local'}, client=shared)
    assert len(new_client_calls) == 0
    shared.get.assert_awaited_once()


@pytest.mark.asyncio
async def test_check_service_health_http(monkeypatch):
    class MockResponse:
        def __init__(self, status_code):
            self.status_code = status_code

    class MockAsyncClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

        async def get(self, url):
            if 'fail' in url:
                raise Exception('connection refused')
            return MockResponse(200 if 'ok' in url else 500)

    monkeypatch.setattr('httpx.AsyncClient', MockAsyncClient)

    res1 = await _check_service_health({'name': 'test1', 'health_url': 'http://ok.local'})
    assert res1['status'] == 'ok'
    assert res1['http_status'] == 200

    res2 = await _check_service_health({'name': 'test2', 'url': 'http://error.local'})
    assert res2['status'] == 'error'
    assert res2['http_status'] == 500

    res3 = await _check_service_health({'name': 'test3', 'health_url': 'http://fail.local'})
    assert res3['status'] == 'error'
    assert res3['http_status'] == 'unreachable'


# ---------------------------------------------------------------------------
# homelab_health endpoint — concurrent gather + serial event recording
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_concurrent_checks_use_gather(monkeypatch):
    """asyncio.gather must be called when multiple services are present."""
    gather_calls = []
    real_gather = asyncio.gather

    async def spy_gather(*coros, **kw):
        gather_calls.append(len(coros))
        return await real_gather(*coros, **kw)

    monkeypatch.setattr('asyncio.gather', spy_gather)
    monkeypatch.setattr(
        'routes.homelab_routes._load_services',
        lambda: [{'name': 's1'}, {'name': 's2'}, {'name': 's3'}],
    )

    async def fake_check(srv, *, client=None):
        return {'name': srv['name'], 'status': 'ok'}

    monkeypatch.setattr('routes.homelab_routes._check_service_health', fake_check)

    router = setup_homelab_routes()
    health = _endpoint(router, '/api/homelab/health', 'GET')
    result = await health(_request(scopes=['homelab:read']))

    assert len(gather_calls) == 1
    assert gather_calls[0] == 3  # one coroutine per service
    assert result['status'] == 'ok'
    assert len(result['services']) == 3


@pytest.mark.asyncio
async def test_health_response_shape_unchanged(monkeypatch):
    router = setup_homelab_routes()
    health = _endpoint(router, '/api/homelab/health', 'GET')

    monkeypatch.setattr(
        'routes.homelab_routes._load_services',
        lambda: [{'name': 's1', 'container': 'c1'}, {'name': 's2', 'url': 'http://ok.local'}],
    )

    async def mock_check(srv, *, client=None):
        if srv['name'] == 's1':
            return {'name': 's1', 'status': 'error', 'container_status': 'exited'}
        return {'name': 's2', 'status': 'ok', 'http_status': 200}

    monkeypatch.setattr('routes.homelab_routes._check_service_health', mock_check)

    data = await health(_request(scopes=['homelab:read']))
    assert data['status'] == 'error'
    assert len(data['services']) == 2
    assert data['services'][0]['status'] == 'error'
    assert data['services'][1]['status'] == 'ok'


@pytest.mark.asyncio
async def test_event_recording_is_serial_after_gather(monkeypatch, tmp_path):
    """Events are recorded serially after all concurrent checks complete."""
    from src.event_store import EventStore

    store = EventStore(file_path=str(tmp_path / 'events.json'))
    monkeypatch.setattr('routes.homelab_routes.EventStore', lambda: store)
    monkeypatch.setattr(
        'routes.homelab_routes._load_services',
        lambda: [
            {'name': 'pihole', 'display_name': 'Pi-hole'},
            {'name': 'plex', 'display_name': 'Plex'},
        ],
    )

    async def mock_check(srv, *, client=None):
        return {'name': srv['name'], 'status': 'error', 'container_status': 'exited'}

    monkeypatch.setattr('routes.homelab_routes._check_service_health', mock_check)

    router = setup_homelab_routes()
    health = _endpoint(router, '/api/homelab/health', 'GET')
    req = _request(scopes=['homelab:read', 'events:write'])
    await health(req, record_events=True)

    events = store.get_events()
    assert len(events) == 2
    names = {e['service'] for e in events}
    assert names == {'pihole', 'plex'}


@pytest.mark.asyncio
async def test_event_recording_dedupes_correctly(monkeypatch, tmp_path):
    """Calling health?record_events=true twice dedupes into the same event."""
    from src.event_store import EventStore

    store = EventStore(file_path=str(tmp_path / 'events.json'))
    monkeypatch.setattr('routes.homelab_routes.EventStore', lambda: store)
    monkeypatch.setattr(
        'routes.homelab_routes._load_services',
        lambda: [{'name': 'pihole'}],
    )

    async def mock_check(srv, *, client=None):
        return {'name': 'pihole', 'status': 'error', 'container_status': 'exited'}

    monkeypatch.setattr('routes.homelab_routes._check_service_health', mock_check)

    router = setup_homelab_routes()
    health = _endpoint(router, '/api/homelab/health', 'GET')
    req = _request(scopes=['homelab:read', 'events:write'])
    await health(req, record_events=True)
    await health(req, record_events=True)

    events = store.get_events()
    assert len(events) == 1
    assert events[0]['count'] == 2


# ---------------------------------------------------------------------------
# Existing scope / 404 tests preserved
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_services_requires_scope(monkeypatch):
    router = setup_homelab_routes()
    list_services = _endpoint(router, '/api/homelab/services', 'GET')
    with pytest.raises(HTTPException) as exc:
        await list_services(_request(scopes=['chat']))
    assert exc.value.status_code == 403
    assert 'homelab:read' in exc.value.detail


@pytest.mark.asyncio
async def test_get_service_returns_404_if_missing(monkeypatch):
    router = setup_homelab_routes()
    get_service = _endpoint(router, '/api/homelab/services/{name}', 'GET')
    monkeypatch.setattr('routes.homelab_routes._load_services', lambda: [])
    with pytest.raises(HTTPException) as exc:
        await get_service(_request(scopes=['homelab:read']), name='missing')
    assert exc.value.status_code == 404
