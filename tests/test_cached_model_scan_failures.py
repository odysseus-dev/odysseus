"""Cache inventory failures must not become confident empty-inventory claims."""
import json
import httpx
import pytest

from src.tools.cookbook import do_list_cached_models


@pytest.mark.asyncio
async def test_failed_scan_is_not_reported_as_no_cached_models(monkeypatch):
    from src import tool_implementations
    monkeypatch.setattr(tool_implementations, '_internal_headers', lambda: {})
    client = httpx.AsyncClient
    def respond(request):
        if request.url.path == '/api/cookbook/state':
            return httpx.Response(200, json={'env': {'servers': []}, 'tasks': []})
        return httpx.Response(503, json={'detail': 'unavailable'})
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: client(
        transport=httpx.MockTransport(respond), **kwargs))
    result = await do_list_cached_models('{}', owner='alice')
    assert result['exit_code'] == 1
    assert result.get('scan_errors')
    assert 'No cached models found' not in result.get('output', '')


@pytest.mark.asyncio
async def test_partial_scan_keeps_verified_models_but_marks_inventory_incomplete(monkeypatch):
    from src import tool_implementations
    monkeypatch.setattr(tool_implementations, '_internal_headers', lambda: {})
    client = httpx.AsyncClient
    def respond(request):
        if request.url.path == '/api/cookbook/state':
            return httpx.Response(200, json={'env': {'servers': [{'name': 'offline', 'host': 'offline.test'}]}})
        if request.url.params.get('host'):
            raise httpx.ReadTimeout('unavailable', request=request)
        return httpx.Response(200, json={'models': [{'repo_id': 'fixture/model'}]})
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: client(
        transport=httpx.MockTransport(respond), **kwargs))
    result = await do_list_cached_models('{}', owner='alice')
    assert result['models'][0]['repo_id'] == 'fixture/model'
    assert result['exit_code'] == 1
    assert result['partial'] is True
    assert result['scan_errors'] == [{'host': 'offline', 'reason': 'ReadTimeout'}]


@pytest.mark.asyncio
async def test_failed_server_discovery_does_not_claim_complete_local_only_inventory(monkeypatch):
    from src import tool_implementations
    monkeypatch.setattr(tool_implementations, '_internal_headers', lambda: {})
    client = httpx.AsyncClient
    def respond(request):
        if request.url.path == '/api/cookbook/state':
            return httpx.Response(503, json={'detail': 'unavailable'})
        return httpx.Response(200, json={'models': [{'repo_id': 'fixture/local'}]})
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: client(
        transport=httpx.MockTransport(respond), **kwargs))
    result = await do_list_cached_models('{}', owner='alice')
    assert result['models'][0]['repo_id'] == 'fixture/local'
    assert result['exit_code'] == 1 and result['partial']


@pytest.mark.asyncio
async def test_named_server_retries_its_ssh_alias_when_saved_address_is_stale(monkeypatch):
    from src import tool_implementations
    monkeypatch.setattr(tool_implementations, '_internal_headers', lambda: {})
    client = httpx.AsyncClient

    def respond(request):
        if request.url.path == '/api/cookbook/state':
            return httpx.Response(200, json={'env': {'servers': [
                {'name': 'Ajax', 'host': 'pewds@192.168.1.9'},
            ]}})
        if request.url.params.get('host') == 'pewds@192.168.1.9':
            return httpx.Response(200, json={
                'models': [], 'error': 'ssh: connect to host 192.168.1.9 port 22: timed out',
            })
        if request.url.params.get('host') == 'Ajax':
            return httpx.Response(200, json={'models': [{
                'repo_id': 'fixture/model', 'size': '1 GB', 'nb_files': 1,
                'has_incomplete': False,
            }]})
        raise AssertionError(f'unexpected request: {request.url}')

    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: client(
        transport=httpx.MockTransport(respond), **kwargs))
    result = await do_list_cached_models('{"host":"Ajax"}', owner='alice')
    assert result['exit_code'] == 0
    assert result['models'][0]['repo_id'] == 'fixture/model'
    assert not result.get('scan_errors')
