import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.hermes_local_api import LOCAL_PREFIX, LocalProjectsApi  # noqa: E402


class FakeDashboard:
    def __init__(self):
        self.calls = []

    def sessions(self):
        return [{'id': 's1', 'title': 'One', 'workspace': 'F:/x', 'state': 'idle'}]

    def overview(self):
        self.calls.append('overview')
        return {'projects': [{'id': 'aaaaaaaaaa', 'name': 'One'}]}

    def detail(self, project_id, port=7000, related=True):
        self.calls.append(('detail', project_id, port, related))
        return {'id': project_id, 'related_pending': True} if project_id == 'aaaaaaaaaa' else None

    def related_detail(self, project_id, port=7000):
        self.calls.append(('related', project_id, port))
        return [] if project_id == 'aaaaaaaaaa' else None


def call(path, method='GET', grant=object(), headers=None, dash=None):
    api = LocalProjectsApi(lambda: dash or FakeDashboard())
    sent = []

    async def send(message):
        sent.append(message)

    scope = {'type': 'http', 'method': method, 'path': path, 'query_string': b'', 'headers': [(b'host', b'hermes.localhost:7000')] + list((headers or {}).items())}
    asyncio.run(api(scope, None, send, grant))
    start, body = sent[0], json.loads(sent[1]['body'])
    return start['status'], dict(start['headers']), body


def test_routes_serve_the_dashboard_read_only_and_never_cacheable():
    dash = FakeDashboard()
    status, headers, body = call(LOCAL_PREFIX + 'projects', dash=dash)
    assert status == 200 and body['projects'][0]['name'] == 'One' and headers[b'cache-control'] == b'no-store' and headers[b'x-content-type-options'] == b'nosniff'
    assert call(LOCAL_PREFIX + 'projects/aaaaaaaaaa', dash=dash)[0] == 200 and ('detail', 'aaaaaaaaaa', 7000, False) in dash.calls          # basic detail only: the slow part is separate
    assert call(LOCAL_PREFIX + 'projects/aaaaaaaaaa/related', dash=dash)[2] == {'related_sessions': []}
    assert call(LOCAL_PREFIX + 'sessions', dash=dash)[2]['sessions'][0]['id'] == 's1' and call(LOCAL_PREFIX + 'sessions', method='POST')[0] == 405 and call(LOCAL_PREFIX + 'sessions', grant=None)[0] == 401


def test_no_grant_wrong_method_cross_site_unknown_and_malformed_ids_are_refused():
    assert call(LOCAL_PREFIX + 'projects', grant=None)[0] == 401
    for method in ('POST', 'PUT', 'DELETE', 'PATCH'):
        assert call(LOCAL_PREFIX + 'projects', method=method)[0] == 405
    assert call(LOCAL_PREFIX + 'projects', headers={b'sec-fetch-site': b'cross-site'})[0] == 403
    assert call(LOCAL_PREFIX + 'projects', headers={b'sec-fetch-site': b'same-origin'})[0] == 200
    for bad in ('../x', 'AAAAAAAAAA', 'aaaaaaaaaaa', 'aaaaaaaaa', 'a%2fb', 'aaaaaaaaaa/other', 'aaaaaaaaaa/related/x'):
        assert call(LOCAL_PREFIX + 'projects/' + bad)[0] == 404
    assert call(LOCAL_PREFIX + 'projects/bbbbbbbbbb')[0] == 404 and call(LOCAL_PREFIX + 'other')[0] == 404


def test_a_failing_or_slow_read_gives_a_clear_error_instead_of_hanging(monkeypatch):
    import src.hermes_local_api as module

    class Broken(FakeDashboard):
        def detail(self, *a, **k):
            raise RuntimeError('secret internals')
    status, _, body = call(LOCAL_PREFIX + 'projects/aaaaaaaaaa', dash=Broken())
    assert status == 500 and 'secret' not in json.dumps(body)

    class Slow(FakeDashboard):
        def overview(self):
            import time
            time.sleep(1.5)
    monkeypatch.setattr(module, 'TIMEOUT_S', 0.2)
    assert call(LOCAL_PREFIX + 'projects', dash=Slow())[0] == 504


def test_the_proxy_answers_the_local_prefix_itself_only_with_a_grant_and_in_gateway_mode():
    source = (Path(__file__).resolve().parents[2] / 'src/hermes_proxy.py').read_text(encoding='utf-8')
    gate = source.index('self.local_api is not None')
    forward = source.index('await self._forward(scope, receive, send, grant)', gate)
    grant_check = source.index('grant = self._authorize(scope)', source.index('async def __call__'))
    assert grant_check < gate < forward and "self.mode == 'gateway'" in source[gate:forward]


def test_usage_costs_route_is_read_only_grant_gated_and_never_writes_the_store(tmp_path):
    import sqlite3
    db = sqlite3.connect(tmp_path / 'state.db')
    db.execute('CREATE TABLE sessions (id TEXT, cwd TEXT, profile_name TEXT)')
    db.execute('CREATE TABLE session_model_usage (session_id, model, billing_provider, billing_mode, task, api_call_count, input_tokens, output_tokens, '
               'cache_read_tokens, cache_write_tokens, reasoning_tokens, estimated_cost_usd, actual_cost_usd, cost_status, cost_source, first_seen, last_seen)')
    db.commit()
    db.close()
    before = (tmp_path / 'state.db').read_bytes()

    class Dash(FakeDashboard):
        hermes_home = tmp_path

    status, headers, body = call(LOCAL_PREFIX + 'usage/costs', dash=Dash())
    assert status == 200 and body['available'] is True and headers[b'cache-control'] == b'no-store'
    assert set(body['periods']) == {'today', 'last_7_days', 'last_30_days', 'all_recorded'}
    assert call(LOCAL_PREFIX + 'usage/costs', grant=None)[0] == 401 and call(LOCAL_PREFIX + 'usage/costs', method='POST')[0] == 405
    assert call(LOCAL_PREFIX + 'usage/costs', headers={b'sec-fetch-site': b'cross-site'}, dash=Dash())[0] == 403
    assert call(LOCAL_PREFIX + 'usage/other')[0] == 404
    assert (tmp_path / 'state.db').read_bytes() == before
