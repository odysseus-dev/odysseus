"""Real HTTP contract checks; no app/process restart rehearsals."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import threading

import pytest

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Native Windows runner')


@contextmanager
def endpoint(body, content_type='application/json', status=200):
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass
        def do_GET(self):
            requests.append(self.path)
            if self.path == '/health':
                self.send_response(302)
                self.send_header('Location', '/login')
                self.end_headers()
                return
            raw = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(status)
            if status == 302:
                self.send_header('Location', '/login')
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port, requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_app_uses_actual_public_api_health_not_login_redirect():
    from src.windows_app_restart import health
    with endpoint({'status': 'healthy', 'timestamp': 'fixture-only'}) as (port, requests):
        assert health(port) is True
        assert requests == ['/api/health']


def test_app_rejects_spa_html_with_status_200():
    from src.windows_app_restart import health
    with endpoint(b'<!doctype html><html>SPA/login</html>', 'text/html') as (port, _):
        assert health(port) is False


@pytest.mark.parametrize(('kind', 'body', 'valid'), [
    ('gateway', {'ok': True, 'auth_required': False, 'displayVersion': 'fixture'}, True),
    ('gateway', {'status': 'healthy'}, False),
    ('gateway', {'ok': True, 'auth_required': 'false', 'displayVersion': 'fixture'}, False),
    ('chroma', {'nanosecond heartbeat': 1}, True),
    ('chroma', {'ok': True}, False),
    ('chroma', {'nanosecond heartbeat': True}, False),
    ('app', {'status': 'unhealthy'}, False),
])
def test_health_validates_the_expected_service(kind, body, valid):
    from src.windows_app_restart import health
    path = '/api/v2/heartbeat' if kind == 'chroma' else '/api/health'
    with endpoint(body) as (port, requests):
        assert health(port, path, kind=kind) is valid
        assert requests == [path]


@pytest.mark.parametrize(('body', 'status'), [
    (b'not-json', 200), (b' ' * 65537, 200),
    ({'status': 'healthy'}, 302), ({'status': 'healthy'}, 503),
])
def test_invalid_redirect_or_unhealthy_response_fails_closed(body, status):
    from src.windows_app_restart import health
    with endpoint(body, status=status) as (port, requests):
        assert health(port) is False
        assert requests == ['/api/health'], 'Never follow login/external redirects'


def test_protected_services_recheck_real_role_contracts():
    from src.windows_app_restart import protected_state, verify_protected
    with endpoint({'ok': True, 'auth_required': False, 'displayVersion': 'fixture'}) as (gateway, gw_requests):
        with endpoint({'nanosecond heartbeat': 1}) as (chroma, chroma_requests):
            records = protected_state({'gateway_port': gateway, 'chroma_port': chroma})
            assert {row['kind'] for row in records} == {'gateway', 'chroma'}
            assert all(row['pid'] == os.getpid() for row in records)
            verify_protected(records)
            assert gw_requests == ['/api/health', '/api/health']
            assert chroma_requests == ['/api/v2/heartbeat', '/api/v2/heartbeat']
