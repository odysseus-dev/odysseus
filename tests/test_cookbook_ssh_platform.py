"""Connected server metadata is proven by SSH and remains bound to its profile."""
import asyncio
import base64
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from routes import cookbook_helpers as helpers

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.asyncio
@pytest.mark.parametrize('replies,expected', [
    ([(0, b'Darwin\n')], 'darwin'),
    ([(0, b'Linux\n'), (0, b'linux\n')], 'linux'),
    ([(0, b'Linux\n'), (0, b'termux\n')], 'termux'),
    ([(1, b''), (0, b'windows\r\n')], 'windows'),
    ([(0, b'FreeBSD\n'), (127, b'')], ''),
    ([(0, b'Linux\n'), (1, b'')], ''),
    ([(0, b'Linux\n'), (0, b'unexpected\n')], ''),
    ([(0, b'banner\nDarwin\n'), (0, b'not-windows\n')], ''),
])
async def test_platform_probe_uses_positive_os_evidence(replies, expected):
    calls = []

    async def runner(host, port, command, **kwargs):
        calls.append((host, port, command, kwargs))
        code, stdout = replies[len(calls) - 1]
        return code, stdout, b''

    assert await helpers.probe_ssh_platform('user@owned', '2222', runner) == expected
    assert len(calls) == len(replies)
    assert all(host == 'user@owned' and port == '2222' for host, port, _, _ in calls)
    assert all(0 < kwargs['timeout'] <= 8 and kwargs['strict_host_key_checking'] is False
               for _, _, _, kwargs in calls)
    assert all(b[3]['timeout'] <= a[3]['timeout'] for a, b in zip(calls, calls[1:]))
    if expected == 'windows':
        command = calls[-1][2]
        assert command.startswith('powershell.exe -NoProfile -NonInteractive -EncodedCommand ')
        script = base64.b64decode(command.split()[-1]).decode('utf-16-le')
        assert '[System.PlatformID]::Win32NT' in script


@pytest.mark.asyncio
@pytest.mark.parametrize('error', [asyncio.TimeoutError, OSError])
async def test_optional_platform_probe_failure_does_not_invent_an_os(error):
    async def runner(*args, **kwargs):
        raise error('owned probe failure')

    assert await helpers.probe_ssh_platform('user@owned', '2222', runner) == ''


@pytest.mark.asyncio
async def test_exhausted_platform_budget_does_not_start_another_ssh_process(monkeypatch):
    from src import constants
    monkeypatch.setattr(constants, 'COOKBOOK_SSH_TEST_TIMEOUT', 0)

    async def runner(*args, **kwargs):
        pytest.fail('no budget remains for another process')

    assert await helpers.probe_ssh_platform('user@owned', '2222', runner) == ''


@pytest.mark.parametrize('mode', ['success', 'permission', 'timeout', 'probe-failure', 'non-admin', 'invalid-host'])
def test_ssh_route_keeps_connectivity_and_authorization_contracts(monkeypatch, mode):
    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient
    from routes import cookbook_routes as routes
    calls = []

    def require_admin(request):
        if mode == 'non-admin':
            raise HTTPException(403, 'admin required')

    async def runner(host, port, command, **kwargs):
        calls.append(command)
        if mode == 'timeout':
            raise asyncio.TimeoutError
        if mode == 'permission':
            return 255, b'', b'Permission denied'
        if command == 'echo ok':
            return 0, b'ok\n', b''
        if mode == 'probe-failure':
            raise OSError('owned unavailable probe')
        return 0, b'Darwin\n', b''

    monkeypatch.setattr(routes, 'require_admin', require_admin)
    monkeypatch.setattr(routes, 'run_ssh_command_async', runner)
    app = FastAPI()
    app.include_router(routes.setup_cookbook_routes())
    with TestClient(app) as client:
        response = client.post('/api/cookbook/test-ssh', json={
            'host': '-oInvalid' if mode == 'invalid-host' else 'user@owned', 'ssh_port': '2222',
        })
    if mode in {'non-admin', 'invalid-host'}:
        assert response.status_code == (403 if mode == 'non-admin' else 400)
        assert not calls
        return
    assert response.status_code == 200
    data = response.json()
    if mode == 'permission':
        assert data['exit_code'] == 255 and 'platform' not in data and len(calls) == 1
    elif mode == 'timeout':
        assert data['exit_code'] == 124 and 'platform' not in data and len(calls) == 1
    else:
        assert data['stdout'] == 'ok\n' and data['exit_code'] == 0
        assert data['platform'] == ('darwin' if mode == 'success' else '')


def run_ui(config):
    if not shutil.which('node'):
        pytest.skip('requires Node')
    script = r'''
import fs from 'node:fs';import vm from 'node:vm';import {parse} from 'acorn';
const config=JSON.parse(process.argv[1]),root=process.argv[2];
function functions(file,names) {
 const source=fs.readFileSync(root+'/'+file,'utf8'),found=[];
 function walk(node){if(!node||typeof node!=='object')return;
  if(node.type==='FunctionDeclaration'&&names.includes(node.id.name))found.push(source.slice(node.start,node.end));
  for(const value of Object.values(node)){if(Array.isArray(value))value.forEach(walk);else if(value&&typeof value==='object')walk(value)}
 }
 walk(parse(source,{ecmaVersion:'latest',sourceType:'module'}));return found.join('\n');
}
const a={name:'First',host:'user@owned',port:'2222',envPath:'/owned/one',env:'venv',platform:config.initial??''};
const b={name:'Second',host:'user@owned',port:'2223',envPath:'/owned/two',env:'conda',platform:'windows'};
const fields={},dot={className:'',title:''},msg={textContent:'',style:{}};
for(const [key,selector]of Object.entries({name:'.cookbook-srv-name',host:'.cookbook-srv-host',port:'.cookbook-srv-port',envPath:'.cookbook-srv-path',env:'.cookbook-srv-env'}))fields[selector]={value:a[key]};
const entry={dataset:{platform:a.platform},isConnected:true,querySelector(s){if(s==='.cookbook-server-row')return this;if(s==='.cookbook-srv-status')return dot;if(s==='.cookbook-srv-test-msg')return msg;return fields[s]??null},querySelectorAll:()=>[]};
const second={dataset:{platform:b.platform},querySelector(s){if(s==='.cookbook-server-row')return this;const key={'.cookbook-srv-name':'name','.cookbook-srv-host':'host','.cookbook-srv-port':'port','.cookbook-srv-path':'envPath','.cookbook-srv-env':'env'}[s];return key?{value:b[key]}:null},querySelectorAll:()=>[]};
const state={servers:[a,b],remoteHost:a.host,platform:config.mode==='other'?'windows':a.platform};
const persisted=[],pending=[];let rebuilds=0;
const context={WeakMap,_serverConnectionTests:new WeakMap(),Date,JSON,encodeURIComponent,
 _envState:state,document:{querySelectorAll:()=>[entry,second]},
 _persistEnvState:()=>persisted.push(structuredClone(state)),_rebuildServerSelect:()=>rebuilds++,
 _fetchHwfitWithTimeout:()=>new Promise((resolve,reject)=>pending.push({resolve,reject}))};
vm.createContext(context);
vm.runInContext(functions('static/js/cookbook.js',['_isLocalEntry','_serverKey','_serverByVal'])+
 functions('static/js/cookbook-hwfit.js',['_normalizeCookbookModelDir','_syncServers','_setServerPlatform','_testServerConnection']),context);
const oldKey=context._serverKey(a),otherKey=context._serverKey(b);
state.remoteServerKey=config.mode==='other'?otherKey:config.mode==='legacy'?'':oldKey;
state.defaultServer=config.mode==='other'?otherKey:oldKey;
const response=platform=>({ok:config.httpOk!==false,status:config.httpOk===false?403:200,json:async()=>({stdout:config.stdout??'ok\n',exit_code:config.exitCode??0,platform})});
const first=context._testServerConnection(entry);
if(config.mode==='host-change')fields['.cookbook-srv-host'].value='user@changed';
if(config.mode==='port-change')fields['.cookbook-srv-port'].value='2299';
if(config.mode==='detached')entry.isConnected=false;
if(config.mode==='latest'||config.mode==='latest-error'){
 const latest=context._testServerConnection(entry);pending[1].resolve(response('darwin'));await latest;
 if(config.mode==='latest-error')pending[0].reject(Error('old failure'));else pending[0].resolve(response('windows'));
}else pending[0].resolve(response(config.platform));
await first;
console.log(JSON.stringify({state,persisted,rebuilds,entryPlatform:entry.dataset.platform,status:dot.className,message:msg.textContent,oldKey,otherKey,resolvedKey:context._serverKey(state.servers[0])}));
'''
    result = subprocess.run(['node', '--input-type=module', '-e', script, json.dumps(config), str(ROOT)],
                            cwd=ROOT, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize('platform', ['linux', 'windows', 'termux', 'darwin'])
def test_successful_ssh_metadata_reaches_saved_profile_and_keeps_selection(platform):
    result = run_ui({'platform': platform})
    assert result['entryPlatform'] == result['state']['platform'] == platform
    assert result['state']['servers'][0]['platform'] == platform
    assert result['state']['remoteServerKey'] == result['state']['defaultServer'] == result['resolvedKey']
    assert result['state']['remoteServerKey'] != result['oldKey']
    assert result['persisted'][-1]['servers'][0]['platform'] == platform


@pytest.mark.parametrize('mode', ['other', 'legacy'])
def test_platform_discovery_preserves_other_same_host_profiles_and_legacy_selection(mode):
    result = run_ui({'mode': mode, 'platform': 'darwin'})
    assert result['state']['servers'][0]['platform'] == 'darwin'
    assert result['state']['servers'][1]['platform'] == 'windows'
    if mode == 'other':
        assert result['state']['remoteServerKey'] == result['state']['defaultServer'] == result['otherKey']
        assert result['state']['platform'] == 'windows'
    else:
        assert result['state']['remoteServerKey'] == '' and result['state']['platform'] == 'darwin'


@pytest.mark.parametrize('platform', ['', None, 'freebsd', {'unexpected': True}])
def test_unknown_metadata_preserves_current_known_platform(platform):
    result = run_ui({'initial': 'linux', 'platform': platform})
    assert result['entryPlatform'] == result['state']['platform'] == 'linux'
    assert result['status'].endswith(' ok') and result['persisted'] == []


@pytest.mark.parametrize('mode', ['host-change', 'port-change', 'detached'])
def test_stale_connection_result_cannot_relabel_an_edited_or_removed_server(mode):
    result = run_ui({'mode': mode, 'initial': 'linux', 'platform': 'windows'})
    assert result['entryPlatform'] == 'linux' and result['persisted'] == []


@pytest.mark.parametrize('mode', ['latest', 'latest-error'])
def test_latest_check_keeps_metadata_and_status_when_old_request_finishes_last(mode):
    result = run_ui({'mode': mode})
    assert result['entryPlatform'] == result['state']['platform'] == 'darwin'
    assert result['status'].endswith(' ok') and len(result['persisted']) == 1


@pytest.mark.parametrize('extra', [{'httpOk': False}, {'exitCode': 255}, {'stdout': 'not connected'}])
def test_failed_checks_do_not_replace_known_platform(extra):
    result = run_ui({'initial': 'linux', 'platform': 'windows', **extra})
    assert result['entryPlatform'] == 'linux' and result['persisted'] == []
    assert result['status'].endswith(' fail')
