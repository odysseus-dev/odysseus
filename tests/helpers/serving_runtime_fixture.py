"""Tiny runtime-shaped HTTP fixture. No model parsing, downloads or inference."""
import argparse
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os

parser = argparse.ArgumentParser()
parser.add_argument('--port', type=int, required=True)
args = parser.parse_args()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        fixtures = {
            '/health': {'status': 'ok'},
            '/models': {'data': [{'id': 'fixture-only-not-a-model'}]},
            '/props': {'default_generation_settings': {'n_ctx': 2048}},
            '/api/version': {'version': 'fixture-only'},
            '/api/tags': {'models': []},
            '/api/ps': {'models': []},
        }
        data = json.dumps(fixtures.get(self.path, {'error': 'not found'})).encode()
        loading = self.path == '/health' and os.getenv('ODYSSEUS_SERVING_FIXTURE_LOADING') == '1'
        self.send_response(503 if loading else (200 if self.path in fixtures else 404))
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format, *args):
        pass


print('Fixture-only HTTP runtime; not inference', flush=True)
HTTPServer(('127.0.0.1', args.port), Handler).serve_forever()
