"""Deterministic regressions for skill-import DNS validation and pinning."""

import gzip
import ipaddress
import socket
import threading

import httpcore
import httpx

from services.memory import skill_importer


PUBLIC_A = ipaddress.ip_address("93.184.216.34")
PUBLIC_B = ipaddress.ip_address("1.1.1.1")


def test_validation_snapshot_is_the_only_connect_destination(monkeypatch):
    answers = iter([
        [str(PUBLIC_A)],
        ["127.0.0.1"],
    ])
    resolver_calls = []

    def _flipping_resolver(host):
        resolver_calls.append(host)
        return next(answers)

    monkeypatch.setattr(skill_importer, "_default_resolver", _flipping_resolver)
    pinned_ips = skill_importer._check_fetch_url("https://rebind.example/skill")

    connected = []

    class _RecordingBackend:
        def connect_tcp(self, host, port, timeout, local_address, socket_options):
            connected.append((host, port))
            return object()

    backend = skill_importer._PinnedBackend(pinned_ips)
    backend._real = _RecordingBackend()
    backend.connect_tcp("rebind.example", 443, timeout=1.0)

    assert resolver_calls == ["rebind.example"]
    assert pinned_ips == [PUBLIC_A]
    assert connected == [(str(PUBLIC_A), 443)]


def test_pinned_backend_falls_back_only_within_validated_snapshot():
    attempts = []

    class _FallbackBackend:
        def connect_tcp(self, host, port, timeout, local_address, socket_options):
            attempts.append((host, timeout))
            if host == str(PUBLIC_A):
                raise httpcore.ConnectError("first address unavailable")
            return "connected"

    backend = skill_importer._PinnedBackend([PUBLIC_A, PUBLIC_B])
    backend._real = _FallbackBackend()

    assert backend.connect_tcp("rebind.example", 443, timeout=1.0) == "connected"
    assert [host for host, _ in attempts] == [str(PUBLIC_A), str(PUBLIC_B)]
    assert all(timeout is not None and 0 <= timeout <= 1.0 for _, timeout in attempts)


def test_transport_preserves_request_authority_and_response_url():
    recorded = []

    class _CoreResponse:
        status = 200
        headers = [(b"content-type", b"text/plain")]
        stream = [b"ok"]
        extensions = {}

        def close(self):
            return None

    class _RecordingPool:
        def handle_request(self, request):
            recorded.append(request)
            return _CoreResponse()

        def close(self):
            return None

    transport = skill_importer._PinnedTransport([PUBLIC_A])
    transport._pool.close()
    transport._pool = _RecordingPool()

    url = "https://github.com:444/octocat/repo?q=1"
    with httpx.Client(transport=transport) as client:
        response = client.get(url)

    core_request = recorded[0]
    assert core_request.url.host == b"github.com"
    assert core_request.url.port == 444
    assert core_request.url.target == b"/octocat/repo?q=1"
    assert (b"host", b"github.com:444") in [
        (name.lower(), value) for name, value in core_request.headers
    ]
    assert str(response.url) == url


def test_get_checked_uses_fresh_transport_per_redirect_hop(monkeypatch):
    first = "https://github.com/owner/repo"
    second = "https://raw.githubusercontent.com/owner/repo/main/SKILL.md"
    snapshots = {
        first: [PUBLIC_A],
        second: [PUBLIC_B],
    }
    clients = []
    requested = []

    monkeypatch.setattr(
        skill_importer,
        "_resolve_and_check_url",
        lambda url: snapshots[url],
    )

    class _Client:
        def __init__(self, *, transport, follow_redirects, timeout):
            assert follow_redirects is False
            clients.append((transport._pinned_ips, timeout))

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url, headers=None):
            requested.append((url, headers))
            request = httpx.Request("GET", url)
            if url == first:
                return httpx.Response(
                    302,
                    headers={"location": second},
                    request=request,
                )
            return httpx.Response(200, content=b"ok", request=request)

    monkeypatch.setattr(skill_importer.httpx, "Client", _Client)

    response = skill_importer._get_checked(first, headers={"Accept": "text/plain"})

    assert [ips for ips, _ in clients] == [[PUBLIC_A], [PUBLIC_B]]
    assert requested == [
        (first, {"Accept": "text/plain"}),
        (second, {"Accept": "text/plain"}),
    ]
    assert str(response.url) == second


def test_dns_rebinding_pinned_transport_dials_pinned_ip():
    """The real pool must dial the pinned IP and keep the logical request intact.

    Everything above this test replaces the pool or the backend, so this is the
    only case that exercises ``httpcore.ConnectionPool`` end to end: the socket
    goes to the pinned address while URL, ``Host``, and the decoded body stay on
    the original hostname.
    """
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.bind(("127.0.0.1", 0))
    server_socket.listen(1)
    port = server_socket.getsockname()[1]

    captured_request = b""
    client_address = None
    server_error = None

    def handle_client():
        nonlocal captured_request, client_address, server_error
        try:
            conn, client_address = server_socket.accept()
            with conn:
                conn.settimeout(2.0)
                while b"\r\n\r\n" not in captured_request:
                    if len(captured_request) >= 16_384:
                        raise AssertionError("request headers exceeded 16 KiB")
                    chunk = conn.recv(min(4096, 16_384 - len(captured_request)))
                    if not chunk:
                        break
                    captured_request += chunk
                if b"\r\n\r\n" not in captured_request:
                    raise AssertionError(
                        "connection closed before request headers completed"
                    )
                body = gzip.compress(b"successfully decoded gzip body")
                conn.sendall(
                    b"HTTP/1.1 200 OK\r\n"
                    b"Content-Encoding: gzip\r\n"
                    b"Content-Length: " + str(len(body)).encode() + b"\r\n"
                    b"\r\n" + body
                )
        except Exception as exc:  # surfaced by the assertion below
            server_error = exc

    server_thread = threading.Thread(target=handle_client, daemon=True)
    server_thread.start()

    # Any hostname works: only the pinned snapshot decides where the socket goes.
    url = f"http://rebind.example:{port}/secret-metadata"
    try:
        with httpx.Client(
            transport=skill_importer._PinnedTransport([ipaddress.ip_address("127.0.0.1")])
        ) as client:
            response = client.get(url)
        server_thread.join(timeout=5.0)
    finally:
        server_socket.close()

    assert server_error is None, server_error
    assert not server_thread.is_alive()
    assert client_address is not None and client_address[0] == "127.0.0.1"
    assert f"host: rebind.example:{port}".encode() in captured_request.lower()
    assert str(response.url) == url
    assert response.text == "successfully decoded gzip body"
