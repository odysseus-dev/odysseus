"""Live-HTTP tests for NextcloudClient against an in-process WebDAV-like server.

Exercises the real httpx request paths (PROPFIND/GET) and the multistatus
parser end-to-end — no mocking of the client. Catches signature/HTTP bugs the
stubbed route tests can't see (e.g. a keyword-only max_bytes that asyncio.to_thread
invokes positionally). Loopback is allowed because ODYSSEUS_BLOCK_PRIVATE_NEXTCLOUD
defaults off; an injected resolver avoids real DNS.
"""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from src.nextcloud_client import NextcloudClient, NextcloudError


class _WebDAVHandler(BaseHTTPRequestHandler):
    # Quiet the default stderr logging so pytest output stays clean.
    def log_message(self, *args, **kwargs):
        pass

    def _multistatus(self):
        # Two responses: the collection itself (self) + one file child.
        body = (
            '<?xml version="1.0"?><d:multistatus xmlns:d="DAV:">'
            '<d:response><d:href>/remote.php/dav/files/alice</d:href>'
            '<d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype>'
            '</d:prop></d:propstat></d:response>'
            '<d:response><d:href>/remote.php/dav/files/alice/hello.txt</d:href>'
            '<d:propstat><d:prop><d:resourcetype/>'
            '<d:getcontentlength>11</d:getcontentlength>'
            '<d:getcontenttype>text/plain</d:getcontenttype>'
            '<d:displayname>hello.txt</d:displayname>'
            '</d:prop></d:propstat></d:response>'
            '</d:multistatus>'
        ).encode()
        self.send_response(207)
        self.send_header("Content-Type", "application/xml; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_PROPFIND(self):
        # Drain the request body so the connection doesn't stall.
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length:
            self.rfile.read(length)
        self._multistatus()

    def do_GET(self):
        body = b"hello world"  # 11 bytes — matches the PROPFIND getcontentlength.
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def nc_server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _WebDAVHandler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    yield base
    httpd.shutdown()
    httpd.server_close()


def _client(base):
    # resolver returns the loopback IP so check_outbound_url never hits DNS;
    # loopback is allowed because block_private defaults to False.
    return NextcloudClient(base, "alice", "app-token", resolver=lambda h: ["127.0.0.1"])


def test_ping_reaches_the_server(nc_server):
    assert _client(nc_server).ping() is True


def test_list_dir_returns_children(nc_server):
    entries = _client(nc_server).list_dir("")
    names = [e["name"] for e in entries]
    assert names == ["hello.txt"]
    assert entries[0]["size"] == 11
    assert entries[0]["content_type"] == "text/plain"


def test_get_file_returns_content_positional_max_bytes(nc_server):
    # max_bytes passed POSITIONALLY — this is how the route calls it through
    # asyncio.to_thread. A keyword-only param would TypeError here (and 500 in
    # the route), which is exactly the regression this test pins down.
    content, content_type = _client(nc_server).get_file("hello.txt", 1000)
    assert content == b"hello world"
    assert content_type == "text/plain"


def test_get_file_over_the_limit_raises_413(nc_server):
    with pytest.raises(NextcloudError) as exc:
        _client(nc_server).get_file("hello.txt", 5)
    assert exc.value.status == 413
