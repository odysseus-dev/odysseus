"""Local administration and exact Cookbook caller-to-route regressions."""
import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from starlette.requests import Request

from core.middleware import INTERNAL_TOOL_HEADER, INTERNAL_TOOL_TOKEN, INTERNAL_TOOL_USER
from routes import cookbook_routes, shell_routes
from src import builtin_actions, tool_execution
from src.agent_runtime.authority import ExactOperation, OperationGrant, RequestAuthority, bind_request_authority, seal_task_authority, restore_task_authority
from src.agent_runtime.remote_resources import bind_backend_for_operation, bind_backend_operation
from src.agent_runtime.local_model_control import CAPABILITY_HEADER, model_control_headers
from src.agent_runtime.resources import ResourceIdentityError
from src.tools import cookbook
from src.tool_capabilities import ToolRunSecurityContext
from src.tool_types import ToolBlock


def request(host='127.0.0.1', headers=None, user=None):
    req = Request({'type': 'http', 'method': 'POST', 'scheme': 'http', 'path': '/api/shell/exec',
                   'server': ('127.0.0.1', 7000), 'client': (host, 1234),
                   'headers': [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
                   'app': SimpleNamespace(state=SimpleNamespace(auth_manager=SimpleNamespace(is_admin=lambda u: u == 'alice')))})
    req.state.current_user = user
    return req


@pytest.mark.parametrize('host,headers,allowed', [
    ('127.0.0.1', {}, True), ('::1', {}, True), ('192.0.2.1', {}, False),
    ('127.0.0.1', {'x-forwarded-for': '192.0.2.1'}, False),
    ('127.0.0.1', {'forwarded': 'for=192.0.2.1'}, False),
    ('127.0.0.1', {'cf-ray': 'proxy'}, False),
    ('127.0.0.1', {'x-forwarded-proto': 'https'}, False),
    ('127.0.0.1', {'sec-fetch-site': 'cross-site'}, False),
    ('127.0.0.1', {'origin': 'https://evil.example'}, False),
    ('127.0.0.1', {INTERNAL_TOOL_HEADER: 'forged'}, False),
    ('127.0.0.1', {INTERNAL_TOOL_HEADER: INTERNAL_TOOL_TOKEN}, False),
])
def test_auth_disabled_operator_transport(monkeypatch, host, headers, allowed):
    monkeypatch.setenv('AUTH_ENABLED', 'false')
    req = request(host, headers)
    if allowed:
        shell_routes._require_admin(req)
    else:
        with pytest.raises(HTTPException) as error:
            shell_routes._require_admin(req)
        assert error.value.status_code == 403


@pytest.mark.parametrize('user,allowed', [('alice', True), ('bob', False), (None, False), ('api', False), (INTERNAL_TOOL_USER, False)])
def test_auth_enabled_administration(monkeypatch, user, allowed):
    monkeypatch.setenv('AUTH_ENABLED', 'true')
    if allowed:
        shell_routes._require_admin(request('192.0.2.1', user=user))
    else:
        with pytest.raises(HTTPException):
            shell_routes._require_admin(request(user=user))


@pytest.fixture
def control_app(tmp_path, monkeypatch):
    # Auth tests can reload middleware after collection. Authenticate the live
    # transport token used by real producers, rather than a collection snapshot.
    from core.middleware import INTERNAL_TOOL_HEADER, INTERNAL_TOOL_TOKEN, INTERNAL_TOOL_USER
    monkeypatch.setenv('AUTH_ENABLED', 'true')
    manager = SimpleNamespace(is_configured=True, users={'alice': {}}, is_admin=lambda u: u == 'alice')
    import core.auth
    monkeypatch.setattr(core.auth, 'AuthManager', lambda: manager)
    monkeypatch.setattr(tool_execution, '_owner_is_admin', lambda u: u == 'alice')
    monkeypatch.setattr(cookbook_routes, 'TMUX_LOG_DIR', tmp_path / 'tmux')
    state = tmp_path / 'cookbook.json'
    state.write_text(json.dumps({'presets': [{'name': 'preset', 'model': 'samplepkg', 'cmd': 'python -m pip install samplepkg'}]}))
    monkeypatch.setattr(cookbook_routes, 'COOKBOOK_STATE_FILE', str(state))
    monkeypatch.setattr(builtin_actions, 'COOKBOOK_STATE_FILE', str(state))
    spawned = []
    async def spawn(command, **kwargs):
        spawned.append(command)
        async def wait(): return 0
        async def read(): return b''
        return SimpleNamespace(returncode=0, wait=wait, stderr=SimpleNamespace(read=read))
    monkeypatch.setattr(asyncio, 'create_subprocess_shell', spawn)
    async def remote_probe(*args, **kwargs):
        async def communicate(): return b'tmux', b''
        return SimpleNamespace(returncode=0, communicate=communicate)
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', remote_probe)
    import src.assistant_log
    monkeypatch.setattr(src.assistant_log, 'log_to_assistant', lambda *a, **k: None)
    async def endpoint(**kwargs): return {'added': True, 'endpoint_id': 'endpoint'}
    monkeypatch.setattr(cookbook, '_ensure_served_endpoint', endpoint)
    app = FastAPI()
    app.state.auth_manager = manager
    @app.middleware('http')
    async def attribution(req, next):
        if req.headers.get(INTERNAL_TOOL_HEADER) == INTERNAL_TOOL_TOKEN:
            req.state.current_user = req.headers.get('X-Odysseus-Owner') or INTERNAL_TOOL_USER
        return await next(req)
    app.include_router(cookbook_routes.setup_cookbook_routes())
    app.include_router(shell_routes.setup_shell_routes())
    real_client = httpx.AsyncClient
    def client_factory(*args, **kwargs):
        kwargs.setdefault('transport', httpx.ASGITransport(app=app))
        return real_client(*args, **kwargs)
    monkeypatch.setattr(httpx, 'AsyncClient', client_factory)
    return app, spawned, tmp_path


@pytest.mark.parametrize('tool,args', [
    ('download_model', {'repo_id': 'org/model', 'local': True}),
    ('serve_model', {'repo_id': 'samplepkg', 'cmd': 'python -m pip install samplepkg', 'local': True}),
    ('serve_preset', {'name': 'preset'}),
])
async def test_real_local_tool_dispatch_reaches_real_model_route(control_app, tool, args):
    app, spawned, work = control_app
    authority = RequestAuthority('request', 'alice', 'thread', str(work), (OperationGrant(tool),))
    _, result = await tool_execution.execute_tool_block(ToolBlock(tool, json.dumps(args)), owner='alice',
        session_id='thread', workspace=str(work), request_authority=authority, security_context=ToolRunSecurityContext())
    assert result['exit_code'] == 0, result
    assert result['session_id'].startswith('cookbook-' if tool == 'download_model' else 'serve-')
    assert len(spawned) == 1 and 'tmux new-session' in spawned[0]


async def test_real_scheduled_local_action_uses_restored_exact_authority(control_app):
    app, spawned, work = control_app
    command = json.dumps({'repo_id': 'samplepkg', 'cmd': 'python -m pip install samplepkg', 'set_default': False})
    snapshot = seal_task_authority(command, 'action', 'cookbook_serve', owner='alice')
    authority = restore_task_authority(snapshot, command, 'action', 'cookbook_serve', owner='alice')
    with bind_request_authority(authority):
        message, ok = await builtin_actions.action_cookbook_serve('alice', command=command)
    assert ok, message
    assert len(spawned) == 1
    with bind_request_authority(authority):
        _, ok = await builtin_actions.action_cookbook_serve('alice', command=command.replace('samplepkg', 'changedpkg'))
    assert not ok and len(spawned) == 1


@pytest.mark.parametrize('host,headers', [
    ('127.0.0.1', {INTERNAL_TOOL_HEADER: INTERNAL_TOOL_TOKEN}),
    ('192.0.2.1', {INTERNAL_TOOL_HEADER: INTERNAL_TOOL_TOKEN}),
    ('127.0.0.1', {INTERNAL_TOOL_HEADER: 'forged'}),
    ('127.0.0.1', {CAPABILITY_HEADER: 'forged', INTERNAL_TOOL_HEADER: INTERNAL_TOOL_TOKEN}),
])
async def test_header_only_cannot_launch(control_app, host, headers):
    app, spawned, _ = control_app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=(host, 123)), base_url='http://127.0.0.1') as client:
        for path in ('/api/model/download', '/api/model/serve', '/api/shell/exec'):
            r = await client.post(path, json={'repo_id': 'org/model', 'cmd': 'printf nope', 'command': 'printf nope'}, headers=headers)
            assert r.status_code == 403
    assert not spawned


@pytest.mark.parametrize('substitute', ['body', 'route', 'owner', 'remote', 'proxy', 'replay'])
async def test_capability_exact_transport_binding(control_app, substitute):
    app, spawned, work = control_app
    content = json.dumps({'repo_id': 'org/model', 'local': True})
    authority = RequestAuthority('request', 'alice', 'thread', str(work), (OperationGrant('download_model'),))
    operation = ExactOperation.normalize('download_model', content)
    backend = bind_backend_for_operation(authority, operation)
    body = {'repo_id': 'org/model'}
    with bind_request_authority(authority), bind_backend_operation(backend), model_control_headers('download_model', content, 'alice', body) as headers:
        changed = dict(headers); payload = dict(body); path = '/api/model/download'; host = '127.0.0.1'
        if substitute == 'body': payload['repo_id'] = 'org/changed'
        if substitute == 'route': path = '/api/model/serve'
        if substitute == 'owner': changed['X-Odysseus-Owner'] = 'bob'
        if substitute == 'remote': host = '192.0.2.1'
        if substitute == 'proxy': changed['x-forwarded-for'] = '192.0.2.1'
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=(host, 123)), base_url='http://127.0.0.1') as client:
            if substitute == 'replay':
                assert (await client.post(path, json=payload, headers=changed)).status_code == 200
            r = await client.post(path, json=payload, headers=changed)
            assert r.status_code == 403
    assert len(spawned) == (1 if substitute == 'replay' else 0)


@pytest.mark.parametrize('field', ['owner', 'request_id', 'session_id'])
def test_producer_wrong_application_binding(control_app, field):
    _, _, work = control_app
    content = '{"repo_id":"org/model","local":true}'
    authority = RequestAuthority('request', 'alice', 'thread', str(work), (OperationGrant('download_model'),))
    backend = bind_backend_for_operation(authority, ExactOperation.normalize('download_model', content))
    changed = replace(authority, **{field: 'replacement'}, resource_roots=None, backend_resources=None, owned_scopes=None)
    with bind_request_authority(changed), bind_backend_operation(backend), pytest.raises(ResourceIdentityError):
        with model_control_headers('download_model', content, 'alice', {'repo_id': 'org/model'}):
            pytest.fail('Substituted producer obtained a capability')


async def test_remote_route_semantics_remain_unchanged(control_app):
    app, spawned, _ = control_app
    async with httpx.AsyncClient(base_url='http://127.0.0.1') as client:
        r = await client.post('/api/model/download', json={'repo_id': 'org/model', 'remote_host': 'gpu.example'},
                              headers={INTERNAL_TOOL_HEADER: INTERNAL_TOOL_TOKEN})
    assert r.status_code == 200 and r.json()['ok'], r.text
    assert len(spawned) == 1 and 'ssh ' in spawned[0]


async def test_auth_disabled_local_route_usage(control_app, monkeypatch):
    app, spawned, _ = control_app
    monkeypatch.setenv('AUTH_ENABLED', 'false')
    async with httpx.AsyncClient(base_url='http://127.0.0.1') as client:
        shell = await client.post('/api/shell/exec', json={'command': ''})
        state = await client.post('/api/cookbook/state', json={'tasks': []})
        launch = await client.post('/api/model/download', json={'repo_id': 'org/model'})
    assert shell.status_code == state.status_code == launch.status_code == 200
    assert launch.json()['ok'] and len(spawned) == 1
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=('192.0.2.1', 1)), base_url='http://127.0.0.1') as client:
        for path, body in [('/api/shell/exec', {'command': ''}), ('/api/cookbook/state', {'tasks': []}), ('/api/model/download', {'repo_id': 'org/model'})]:
            assert (await client.post(path, json=body)).status_code == 403


@pytest.mark.parametrize('host,internal', [('127.0.0.1', True), ('192.0.2.1', False)])
async def test_scoped_wrapper_cannot_bypass_native_control(control_app, monkeypatch, host, internal):
    from routes.codex_routes import setup_codex_routes
    app, spawned, _ = control_app
    app.include_router(setup_codex_routes())
    monkeypatch.setenv('AUTH_ENABLED', 'false')
    headers = {INTERNAL_TOOL_HEADER: INTERNAL_TOOL_TOKEN} if internal else {}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=(host, 123)), base_url='http://127.0.0.1') as client:
        r = await client.post('/api/codex/cookbook/serve', json={'repo_id': 'samplepkg', 'cmd': 'python -m pip install samplepkg'}, headers=headers)
    assert r.status_code == 403 and not spawned


@pytest.mark.parametrize('path', ['/api/codex/cookbook/serve', '/api/codex/cookbook/stop/job', '/api/codex/%63ookbook/serve'])
async def test_generic_app_api_cannot_substitute_scoped_wrapper(control_app, path):
    _, spawned, work = control_app
    authority = RequestAuthority('request', 'alice', 'thread', str(work), (OperationGrant('app_api'),))
    content = json.dumps({'action': 'call', 'method': 'POST', 'path': path, 'body': {'repo_id': 'samplepkg', 'cmd': 'python -m pip install samplepkg'}})
    _, result = await tool_execution.execute_tool_block(ToolBlock('app_api', content), owner='alice',
        session_id='thread', workspace=str(work), request_authority=authority, security_context=ToolRunSecurityContext())
    assert result['failure_kind'] == 'resource_identity_denied' and not spawned


async def test_direct_endpoint_call_keeps_producer_gate(control_app, monkeypatch):
    from routes.cookbook_helpers import ModelDownloadRequest
    app, spawned, _ = control_app
    router = cookbook_routes.setup_cookbook_routes()
    endpoint = next(route.endpoint for route in router.routes if getattr(route, 'path', '') == '/api/model/download')
    monkeypatch.setenv('AUTH_ENABLED', 'false')
    req = request('192.0.2.1')
    with pytest.raises(HTTPException) as exc:
        await endpoint(req, ModelDownloadRequest(repo_id='org/model'))
    assert exc.value.status_code == 403 and not spawned
