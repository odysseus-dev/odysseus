"""Tests for the odysseus-local-extract web backend.

They need the Hermes runtime (``plugins.web._common`` and ``tools.url_safety``), so run them with that interpreter and the pinned source on
``PYTHONPATH``; without it the module is skipped.
"""
import importlib.util
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
PROVIDER = ROOT / 'integrations/hermes-mode/plugins/odysseus-local-extract/provider.py'

try:
    import plugins.web._common  # noqa: F401
    import tools.url_safety  # noqa: F401
    HERMES = True
except Exception:  # pragma: no cover - outside the Hermes runtime
    HERMES = False

PAGE = b'''<html><head><title>Loader notes</title><style>.x{}</style></head><body>
<nav>Home | Docs</nav><h1>Config loader</h1><p>The loader reads <b>two</b> files.</p>
<ul><li>first.yaml</li><li>second.yaml</li></ul><pre>x = 1
y = 2</pre><script>alert("no")</script><footer>(c) nobody</footer></body></html>'''


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        path = self.path
        if path == '/ok':
            self.reply(200, 'text/html; charset=utf-8', PAGE)
        elif path == '/text':
            self.reply(200, 'text/plain', b'plain words')
        elif path == '/redirect-internal':
            self.send_response(302)
            self.send_header('Location', '/internal')
            self.end_headers()
        elif path == '/internal':
            self.reply(200, 'text/html', b'<p>secret</p>')
        elif path == '/missing':
            self.reply(404, 'text/html', b'<p>nope</p>')
        elif path == '/binary':
            self.reply(200, 'application/octet-stream', b'\x00\x01')
        elif path == '/big':
            self.reply(200, 'text/html', b'<p>' + b'a' * 2_100_000 + b'</p>')
        elif path == '/empty':
            self.reply(200, 'text/html', b'<html><body><script>1</script></body></html>')
        else:
            self.reply(404, 'text/plain', b'')

    def reply(self, status, kind, body):
        self.send_response(status)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@unittest.skipUnless(HERMES, 'needs the Hermes runtime on PYTHONPATH')
class LocalExtractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('odysseus_local_extract_provider', PROVIDER)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.base = f'http://127.0.0.1:{cls.server.server_address[1]}'
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def provider(self):
        return self.module.LocalExtractProvider()

    def allow(self, *fragments):
        return mock.patch.object(self.module, '_is_public_url', lambda url: any(fragment in url for fragment in fragments))

    def test_readable_text_without_scripts_navigation_or_footer(self):
        with self.allow('/ok'):
            [page] = self.provider().extract([self.base + '/ok'])
        self.assertNotIn('error', page)
        self.assertEqual(page['title'], 'Loader notes')
        for expected in ('# Config loader', 'The loader reads two files.', '- first.yaml', '- second.yaml', 'x = 1\ny = 2'):
            self.assertIn(expected, page['content'])
        for dropped in ('alert', 'Home | Docs', 'nobody', '.x{}'):
            self.assertNotIn(dropped, page['content'])
        self.assertEqual(page['raw_content'], page['content'])

    def test_plain_text_is_returned_as_is(self):
        with self.allow('/text'):
            [page] = self.provider().extract([self.base + '/text'])
        self.assertEqual(page['content'], 'plain words')

    def test_the_real_private_address_check_refuses_loopback(self):
        [page] = self.provider().extract([self.base + '/ok'])
        self.assertIn('private or internal', page['error'])
        self.assertEqual(page['content'], '')

    def test_a_redirect_to_a_refused_address_is_not_followed(self):
        with self.allow('/redirect-internal'):
            [page] = self.provider().extract([self.base + '/redirect-internal'])
        self.assertIn('private or internal', page['error'])
        self.assertNotIn('secret', str(page))

    def test_http_errors_and_unreadable_content_are_per_url_errors_in_input_order(self):
        urls = [self.base + path for path in ('/ok', '/missing', '/binary', '/big', '/empty', '/text')]
        with self.allow('127.0.0.1'):
            results = self.provider().extract(urls)
        self.assertEqual([item['url'] for item in results], urls)
        self.assertNotIn('error', results[0])
        self.assertIn('HTTP 404', results[1]['error'])
        self.assertIn('unsupported content type', results[2]['error'])
        self.assertIn('larger than 2 MB', results[3]['error'])
        self.assertIn('no readable text', results[4]['error'])
        self.assertNotIn('error', results[5])

    def test_non_http_schemes_are_refused(self):
        [page] = self.provider().extract(['file:///C:/Windows/win.ini'])
        self.assertIn('only http and https', page['error'])

    def test_it_is_extract_only_and_needs_no_key(self):
        provider = self.provider()
        self.assertTrue(provider.is_available())
        self.assertTrue(provider.supports_extract())
        self.assertFalse(provider.supports_search())
        self.assertEqual(provider.name, 'odysseus-local-extract')


if __name__ == '__main__':
    unittest.main()
