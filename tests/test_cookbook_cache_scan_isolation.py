"""Concurrent cache requests must not exchange their directory configuration."""
import asyncio
import json
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

import routes.cookbook_routes as routes
from core.middleware import INTERNAL_TOOL_HEADER, INTERNAL_TOOL_TOKEN


@pytest.mark.asyncio
@pytest.mark.parametrize('remote', ['', 'linux', 'windows'])
async def test_concurrent_cache_scans_keep_their_own_directories(monkeypatch, tmp_path, remote):
    monkeypatch.setattr(routes, 'TMUX_LOG_DIR', tmp_path)
    app = FastAPI()
    app.include_router(routes.setup_cookbook_routes())
    ready = asyncio.Event()
    started = []
    directories = [str(tmp_path / 'fixture-alpha'), str(tmp_path / 'fixture-beta')]

    async def spawn(*args, **kwargs):
        started.append(args)
        if len(started) == 2:
            ready.set()
        class Process:
            returncode = 0
            async def communicate(self, input=None):
                await asyncio.wait_for(ready.wait(), timeout=2)
                source = input.decode() if input is not None else Path(args[-1]).read_text()
                selected = next(directory for directory in directories if directory in source)
                return json.dumps([{'repo_id': selected, 'size_bytes': 16,
                                    'nb_files': 1, 'has_incomplete': False}]).encode(), b''
        return Process()

    monkeypatch.setattr(routes.asyncio, 'create_subprocess_exec', spawn)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test',
                                headers={INTERNAL_TOOL_HEADER: INTERNAL_TOOL_TOKEN}) as client:
        responses = await asyncio.gather(*[
            client.get('/api/model/cached', params={'model_dir': directory, **(
                {'host': 'fixture.invalid', 'ssh_port': '2222', 'platform': remote}
                if remote and index else {})})
            for index, directory in enumerate(directories)
        ])
    assert all(response.status_code == 200 for response in responses)
    assert [response.json()['models'][0]['repo_id'] for response in responses] == directories


@pytest.mark.asyncio
@pytest.mark.parametrize('authorized,params', [(False, {}), (True, {'host': 'host; unsafe'})])
async def test_cache_scan_rejection_never_launches_process(monkeypatch, authorized, params):
    monkeypatch.setenv('AUTH_ENABLED', 'true')
    app = FastAPI()
    app.include_router(routes.setup_cookbook_routes())
    async def spawn(*args, **kwargs):
        pytest.fail('A rejected request must not start a process')
    monkeypatch.setattr(routes.asyncio, 'create_subprocess_exec', spawn)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test',
            headers={INTERNAL_TOOL_HEADER: INTERNAL_TOOL_TOKEN} if authorized else {}) as client:
        response = await client.get('/api/model/cached', params=params)
    assert response.status_code == (400 if authorized else 403)


@pytest.mark.asyncio
@pytest.mark.parametrize('cancel,stubborn', [(False, False), (True, False), (False, True)])
async def test_abandoned_scan_stops_its_own_process(monkeypatch, tmp_path, cancel, stubborn):
    monkeypatch.setattr(routes, 'TMUX_LOG_DIR', tmp_path)
    app = FastAPI()
    app.include_router(routes.setup_cookbook_routes())
    started = asyncio.Event()
    class Process:
        returncode = None
        stopped = False
        killed = False
        async def communicate(self, input=None):
            started.set()
            if not cancel:
                raise asyncio.TimeoutError()
            await asyncio.Event().wait()
        def terminate(self):
            self.stopped = True
            if not stubborn:
                self.returncode = -15
        def kill(self):
            self.killed = True
            self.returncode = -9
        async def wait(self):
            if self.returncode is None:
                raise asyncio.TimeoutError()
            return self.returncode
    process = Process()
    async def spawn(*args, **kwargs):
        return process
    monkeypatch.setattr(routes.asyncio, 'create_subprocess_exec', spawn)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url='http://test', headers={INTERNAL_TOOL_HEADER: INTERNAL_TOOL_TOKEN}) as client:
        request = asyncio.create_task(client.get('/api/model/cached'))
        await asyncio.wait_for(started.wait(), timeout=2)
        if cancel:
            request.cancel()
            with pytest.raises(asyncio.CancelledError):
                await request
        else:
            assert (await request).status_code == 500
    assert process.stopped
    assert process.killed == stubborn
