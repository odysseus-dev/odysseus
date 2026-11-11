import json
from types import SimpleNamespace

import pytest

from src import ollama_tool_detection as detection


@pytest.fixture(autouse=True)
def clear_cache():
    detection._CACHE.clear()
    yield
    detection._CACHE.clear()


def test_detects_tools_and_caches_by_model(monkeypatch):
    calls = []
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, n): return json.dumps({'capabilities':['completion','tools']}).encode()
    class Opener:
        def open(self, request, timeout):
            calls.append((request.full_url, json.loads(request.data), timeout))
            return Response()
    monkeypatch.setattr(detection, 'build_opener', lambda *_: Opener())
    assert detection.advertises_tools('http://localhost:11434/v1/chat/completions', 'llama3.1:8b')
    assert detection.advertises_tools('http://127.0.0.1:11434/v1', 'llama3.1:8b')
    assert len(calls) == 1 and calls[0][0] == 'http://127.0.0.1:11434/api/show'
    assert calls[0][1] == {'model':'llama3.1:8b'}


def test_no_tools_and_failure_are_conservative(monkeypatch):
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, n): return b'{"capabilities":["completion"]}'
    monkeypatch.setattr(detection, 'build_opener', lambda *_: SimpleNamespace(open=lambda *a, **k: Response()))
    assert detection.native_tool_capability('http://127.0.0.1:11434/v1', 'no-tools') is False
    assert not detection.advertises_tools('http://127.0.0.1:11434/v1', 'no-tools')
    monkeypatch.setattr(detection, 'build_opener', lambda *_: SimpleNamespace(open=lambda *a, **k: (_ for _ in ()).throw(OSError('offline'))))
    assert detection.native_tool_capability('http://127.0.0.1:11434/v1', 'offline') is None
    assert not detection.advertises_tools('http://127.0.0.1:11434/v1', 'offline')


@pytest.mark.parametrize('url', ['http://example.com:11434/v1','https://localhost:11434/v1','http://localhost:8001/v1','http://user:pass@localhost:11434/v1','http://localhost:11434/other'])
def test_never_probes_unrelated_endpoints(url, monkeypatch):
    monkeypatch.setattr(detection, 'build_opener', lambda *_: pytest.fail('unexpected network request'))
    assert not detection.advertises_tools(url, 'anything')


def test_auto_selects_compact_only_after_verified_support(monkeypatch):
    import core.database
    from src import agent_loop
    class BrokenSession:
        def __init__(self): raise RuntimeError('no endpoint metadata')
    monkeypatch.setattr(core.database, 'SessionLocal', BrokenSession)
    monkeypatch.setattr(detection, 'native_tool_capability', lambda endpoint, model: model == 'tool-model')
    assert agent_loop._configured_model_tool_surface('http://127.0.0.1:11434/v1', 'tool-model') == 'compact'
    assert agent_loop._configured_model_tool_surface('http://127.0.0.1:11434/v1', 'no-tools') == 'compact'

@pytest.mark.parametrize(('mode','flag','expected','probe_called'), [
    ('none', None, 'none', False), ('full', None, 'full', False),
    ('', False, '', False), ('', None, 'compact', True),
])
def test_explicit_mode_and_disabled_endpoint_override_auto(monkeypatch, mode, flag, expected, probe_called):
    import core.database
    from src import agent_loop
    calls=[]
    row=SimpleNamespace(id='endpoint', model_tool_modes=json.dumps({'tool-model':mode}) if mode else None, supports_tools=flag)
    class Query:
        def filter(self, *args): return self
        def all(self): return [row]
    class DB:
        def query(self, *args): return Query()
        def close(self): pass
    monkeypatch.setattr(core.database, 'SessionLocal', lambda: DB())
    monkeypatch.setattr(detection, 'native_tool_capability', lambda *args: calls.append(args) or True)
    assert agent_loop._configured_model_tool_surface('http://127.0.0.1:11434/v1','tool-model') == expected
    assert bool(calls) is probe_called
