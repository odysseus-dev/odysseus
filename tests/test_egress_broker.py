"""Deterministic coverage for the public-only sandbox HTTP(S) broker."""

from __future__ import annotations

import datetime
import os
import shutil
import socket
import ssl
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from security.egress import odysseus_egress_bridge as bridge
from security.egress import odysseus_egress_broker as broker


PUBLIC_V4 = "93.184.216.34"
PUBLIC_V6 = "2606:2800:220:1:248:1893:25c8:1946"


def _resolver(*addresses: str):
    def resolve(_host, port, **_kwargs):
        answers = []
        for address in addresses:
            if ":" in address:
                answers.append(
                    (
                        socket.AF_INET6,
                        socket.SOCK_STREAM,
                        socket.IPPROTO_TCP,
                        "",
                        (address, port, 0, 0),
                    )
                )
            else:
                answers.append(
                    (
                        socket.AF_INET,
                        socket.SOCK_STREAM,
                        socket.IPPROTO_TCP,
                        "",
                        (address, port),
                    )
                )
        return answers

    return resolve


class _PublicPeerSocket:
    """Delegate I/O to a socketpair while reporting a validated public peer."""

    def __init__(self, stream: socket.socket, peer=(PUBLIC_V4, 443)):
        self.stream = stream
        self.peer = peer

    def getpeername(self):
        return self.peer

    def __getattr__(self, name):
        return getattr(self.stream, name)


def _read_all(stream: socket.socket) -> bytes:
    chunks = []
    while True:
        data = stream.recv(65536)
        if not data:
            return b"".join(chunks)
        chunks.append(data)


class _Transport:
    def __init__(
        self,
        tmp_path,
        connector,
        *,
        resolver=None,
        max_connections=broker.MAX_CONNECTIONS,
    ):
        socket_path = tmp_path / "transport.sock"
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.listener.bind(str(socket_path))
        self.listener.listen(max_connections)
        self.server = broker.BrokerServer(
            self.listener,
            resolver=resolver or _resolver(PUBLIC_V4),
            connector=connector,
            max_connections=max_connections,
        )
        self.server.start()
        self.bridge = bridge.LoopbackBridge(str(socket_path), port=0)
        self.bridge.start()

    @property
    def proxy_url(self):
        host, port = self.bridge.address
        return f"http://{host}:{port}"

    def close(self):
        self.bridge.close()
        self.server.close()


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.1",
        "172.16.0.1",
        "192.168.1.1",
        "100.64.0.1",
        "169.254.169.254",
        "192.0.2.1",
        "198.18.0.1",
        "224.0.0.1",
        "::1",
        "fe80::1",
        "fc00::1",
        "ff02::1",
        "::ffff:169.254.169.254",
        "64:ff9b::a9fe:a9fe",
        "64:ff9b::a00:1",
    ],
)
def test_non_public_destinations_are_rejected(address):
    with pytest.raises(broker.BrokerError, match="non-public"):
        broker.resolve_public_targets(
            "blocked.example",
            443,
            resolver=_resolver(address),
        )


def test_public_ipv4_and_ipv6_destinations_are_allowed():
    targets = broker.resolve_public_targets(
        "public.example",
        443,
        resolver=_resolver(PUBLIC_V4, PUBLIC_V6),
    )

    assert [target.sockaddr[0] for target in targets] == [PUBLIC_V4, PUBLIC_V6]


def test_nat64_allows_only_an_embedded_public_ipv4_destination():
    target = "64:ff9b::808:808"

    targets = broker.resolve_public_targets(
        "public-via-nat64.example",
        443,
        resolver=_resolver(target),
    )

    assert [item.sockaddr[0] for item in targets] == [target]


def test_mixed_public_private_dns_answer_fails_closed():
    with pytest.raises(broker.BrokerError, match="non-public"):
        broker.resolve_public_targets(
            "rebind.example",
            443,
            resolver=_resolver(PUBLIC_V4, "127.0.0.1"),
        )


def test_excessive_dns_answer_set_fails_closed():
    addresses = [f"8.8.8.{index}" for index in range(1, 18)]

    with pytest.raises(broker.BrokerError, match="too many"):
        broker.resolve_public_targets(
            "wide.example",
            443,
            resolver=_resolver(*addresses),
        )


def test_connected_peer_is_revalidated():
    local, remote = socket.socketpair()

    def connector(_family, _sockaddr, _timeout):
        return _PublicPeerSocket(local, peer=("127.0.0.1", 443))

    try:
        with pytest.raises(broker.BrokerError, match="connection failed"):
            broker.connect_public_target(
                "public.example",
                443,
                resolver=_resolver(PUBLIC_V4),
                connector=connector,
            )
    finally:
        remote.close()


def test_connected_peer_must_match_the_resolved_target():
    local, remote = socket.socketpair()

    def connector(_family, _sockaddr, _timeout):
        return _PublicPeerSocket(local, peer=("1.1.1.1", 443))

    try:
        with pytest.raises(broker.BrokerError, match="connection failed"):
            broker.connect_public_target(
                "public.example",
                443,
                resolver=_resolver(PUBLIC_V4),
                connector=connector,
            )
    finally:
        local.close()
        remote.close()


def test_https_connect_reaches_only_validated_public_target():
    client, broker_side = socket.socketpair()
    outbound, origin = socket.socketpair()
    resolver_calls = []

    def resolver(host, port, **kwargs):
        resolver_calls.append((host, port, kwargs))
        return _resolver(PUBLIC_V4)(host, port, **kwargs)

    def connector(_family, sockaddr, _timeout):
        assert sockaddr == (PUBLIC_V4, 443)
        return _PublicPeerSocket(outbound)

    worker = threading.Thread(
        target=broker.serve_proxy_client,
        args=(broker_side,),
        kwargs={"resolver": resolver, "connector": connector},
    )
    worker.start()
    client.settimeout(2)
    client.sendall(
        b"CONNECT public.example:443 HTTP/1.1\r\n"
        b"Host: public.example:443\r\n\r\n"
    )
    assert client.recv(4096) == b"HTTP/1.1 200 Connection Established\r\n\r\n"
    client.sendall(b"encrypted request")
    assert origin.recv(4096) == b"encrypted request"
    origin.sendall(b"encrypted response")
    origin.shutdown(socket.SHUT_WR)
    assert client.recv(4096) == b"encrypted response"
    client.close()
    origin.close()
    worker.join(timeout=2)

    assert not worker.is_alive()
    assert len(resolver_calls) == 1


def test_plain_http_is_rewritten_and_proxy_credentials_are_stripped():
    client, broker_side = socket.socketpair()
    outbound, origin = socket.socketpair()
    received = []

    def connector(_family, sockaddr, _timeout):
        assert sockaddr == (PUBLIC_V4, 80)
        return _PublicPeerSocket(outbound, peer=(PUBLIC_V4, 80))

    def origin_server():
        received.append(origin.recv(65536))
        origin.sendall(
            b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok"
        )
        origin.shutdown(socket.SHUT_WR)

    upstream = threading.Thread(target=origin_server)
    worker = threading.Thread(
        target=broker.serve_proxy_client,
        args=(broker_side,),
        kwargs={
            "resolver": _resolver(PUBLIC_V4),
            "connector": connector,
        },
    )
    upstream.start()
    worker.start()
    client.sendall(
        b"GET http://public.example/package.tgz?x=1 HTTP/1.1\r\n"
        b"Host: public.example\r\n"
        b"Proxy-Authorization: Basic must-not-forward\r\n"
        b"Connection: keep-alive\r\n\r\n"
    )
    client.shutdown(socket.SHUT_WR)
    response = _read_all(client)
    client.close()
    worker.join(timeout=2)
    upstream.join(timeout=2)
    origin.close()

    assert response.endswith(b"\r\n\r\nok")
    request = received[0]
    assert request.startswith(b"GET /package.tgz?x=1 HTTP/1.1\r\n")
    assert b"Host: public.example:80\r\n" in request
    assert b"Proxy-Authorization" not in request
    assert b"Connection: close\r\n" in request


def test_connection_header_cannot_remove_content_length_framing():
    client, broker_side = socket.socketpair()
    outbound, origin = socket.socketpair()
    connector_calls = []

    def connector(_family, _sockaddr, _timeout):
        connector_calls.append(True)
        return _PublicPeerSocket(outbound, peer=(PUBLIC_V4, 80))

    worker = threading.Thread(
        target=broker.serve_proxy_client,
        args=(broker_side,),
        kwargs={
            "resolver": _resolver(PUBLIC_V4),
            "connector": connector,
        },
    )
    worker.start()
    client.settimeout(2)
    client.sendall(
        b"POST http://public.example/upload HTTP/1.1\r\n"
        b"Host: public.example\r\n"
        b"Content-Length: 4\r\n"
        b"Connection: content-length\r\n\r\nbody"
    )
    client.shutdown(socket.SHUT_WR)
    response = client.recv(4096)
    client.close()
    origin.close()
    worker.join(timeout=2)

    assert b" 400 " in response
    assert connector_calls == []
    assert not worker.is_alive()


@pytest.mark.parametrize(
    "raw_request",
    [
        b"CONNECT public.example:80 HTTP/1.1\r\nHost: public.example\r\n\r\n",
        b"CONNECT public.example:22 HTTP/1.1\r\nHost: public.example\r\n\r\n",
        b"GET http://public.example:8080/ HTTP/1.1\r\nHost: public.example:8080\r\n\r\n",
        b"GET https://public.example/ HTTP/1.1\r\nHost: public.example\r\n\r\n",
    ],
)
def test_only_http_80_and_https_connect_443_are_allowed(raw_request):
    client, broker_side = socket.socketpair()
    worker = threading.Thread(
        target=broker.serve_proxy_client,
        args=(broker_side,),
        kwargs={"resolver": lambda *_args, **_kwargs: pytest.fail("DNS must not run")},
    )
    worker.start()
    client.sendall(raw_request)
    client.shutdown(socket.SHUT_WR)
    response = _read_all(client)
    client.close()
    worker.join(timeout=2)

    assert b" 403 " in response or b" 400 " in response


def test_each_new_connection_resolves_again():
    calls = []

    def resolver(host, port, **kwargs):
        calls.append((host, port))
        return _resolver(PUBLIC_V4)(host, port, **kwargs)

    for _index in range(2):
        client, broker_side = socket.socketpair()
        outbound, origin = socket.socketpair()

        def connector(_family, _sockaddr, _timeout, stream=outbound):
            return _PublicPeerSocket(stream)

        worker = threading.Thread(
            target=broker.serve_proxy_client,
            args=(broker_side,),
            kwargs={"resolver": resolver, "connector": connector},
        )
        worker.start()
        client.sendall(b"CONNECT public.example:443 HTTP/1.1\r\n\r\n")
        assert b" 200 " in client.recv(4096)
        client.close()
        origin.close()
        worker.join(timeout=2)

    assert calls == [("public.example", 443), ("public.example", 443)]


def test_broker_wrapper_injects_one_read_only_runtime_mount():
    arguments = [
        broker.TRUSTED_LAUNCHER,
        broker.TRUSTED_BWRAP,
        "--unshare-net",
        "--clearenv",
        "--",
        "/bin/true",
    ]

    child = broker.build_child_argv(arguments, "/tmp/trusted-runtime")

    assert child[:4] == [
        broker.TRUSTED_LAUNCHER,
        broker.TRUSTED_BWRAP,
        "--unshare-net",
        "--clearenv",
    ]
    assert child.count("--unshare-net") == 1
    assert "--share-net" not in child
    separator = child.index("--")
    assert child[separator - 5:separator] == [
        "--dir",
        "/run",
        "--ro-bind",
        "/tmp/trusted-runtime",
        broker.SANDBOX_RUNTIME_DIR,
    ]


@pytest.mark.parametrize(
    "arguments",
    [
        [broker.TRUSTED_LAUNCHER, broker.TRUSTED_BWRAP, "--share-net", "--", "/bin/true"],
        [broker.TRUSTED_LAUNCHER, broker.TRUSTED_BWRAP, "--", "/bin/true"],
        ["/bin/true", broker.TRUSTED_BWRAP, "--unshare-net", "--", "/bin/true"],
    ],
)
def test_broker_wrapper_rejects_raw_or_invalid_launch_chains(arguments):
    with pytest.raises(broker.BrokerError):
        broker.build_child_argv(arguments, "/tmp/trusted-runtime")


def test_broker_failure_does_not_print_commands_or_environment():
    secret = "broker-must-not-log-this-secret"
    completed = subprocess.run(
        [
            sys.executable,
            "-I",
            str(Path(broker.__file__)),
            "/bin/false",
            broker.TRUSTED_BWRAP,
            "--unshare-net",
            "--",
            "/bin/echo",
            secret,
        ],
        env={"API_TOKEN": secret},
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )

    assert completed.returncode == 64
    assert secret not in completed.stdout
    assert secret not in completed.stderr


def test_bridge_refuses_to_launch_without_the_mounted_broker(tmp_path):
    missing = tmp_path / "missing.sock"
    proxy = bridge.LoopbackBridge(str(missing), port=0)

    with pytest.raises(bridge.BridgeError, match="unavailable"):
        proxy.start()

    proxy.close()


def test_loopback_bridge_forwards_to_the_mounted_unix_socket(tmp_path):
    socket_path = tmp_path / "broker.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(socket_path))
    listener.listen(4)
    stop = threading.Event()

    def echo_server():
        while not stop.is_set():
            connection, _address = listener.accept()
            data = connection.recv(4096)
            if data:
                connection.sendall(data.upper())
            connection.close()
            if data:
                return

    echo = threading.Thread(target=echo_server)
    echo.start()
    proxy = bridge.LoopbackBridge(str(socket_path), port=0)
    proxy.start()
    client = socket.create_connection(proxy.address, timeout=2)
    client.sendall(b"brokered")
    client.shutdown(socket.SHUT_WR)
    assert client.recv(4096) == b"BROKERED"
    client.close()
    proxy.close()
    stop.set()
    listener.close()
    echo.join(timeout=2)


def test_loopback_bridge_releases_a_slot_when_the_broker_closes_first(tmp_path):
    socket_path = tmp_path / "broker.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(socket_path))
    listener.listen(2)

    def close_connections():
        for _index in range(2):
            connection, _address = listener.accept()
            connection.close()

    closer = threading.Thread(target=close_connections)
    closer.start()
    proxy = bridge.LoopbackBridge(str(socket_path), port=0, max_connections=1)
    proxy.start()
    client = socket.create_connection(proxy.address, timeout=2)

    deadline = time.monotonic() + 2
    connection_thread = None
    while connection_thread is None and time.monotonic() < deadline:
        with proxy.connections_lock:
            if proxy.connections:
                connection_thread = proxy.connections[0]
        if connection_thread is None:
            time.sleep(0.01)
    assert connection_thread is not None
    connection_thread.join(timeout=1)
    released_before_client_close = not connection_thread.is_alive()

    client.close()
    proxy.close()
    listener.close()
    closer.join(timeout=2)

    assert released_before_client_close
    assert not closer.is_alive()


def test_loopback_bridge_closes_when_its_connection_bound_is_full(tmp_path):
    socket_path = tmp_path / "broker.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(socket_path))
    listener.listen(1)
    accepted = []

    def hold_connection():
        probe, _address = listener.accept()
        probe.close()
        connection, _address = listener.accept()
        accepted.append(connection)
        connection.recv(1)

    holder = threading.Thread(target=hold_connection)
    holder.start()
    proxy = bridge.LoopbackBridge(str(socket_path), port=0, max_connections=1)
    proxy.start()
    first = socket.create_connection(proxy.address, timeout=2)
    first.sendall(b"x")

    deadline = time.monotonic() + 2
    while not accepted and time.monotonic() < deadline:
        time.sleep(0.01)
    assert accepted
    rejected = socket.create_connection(proxy.address, timeout=2)
    rejected.sendall(b"GET http://public.example/ HTTP/1.1\r\n\r\n")
    try:
        response = rejected.recv(4096)
    except ConnectionResetError:
        response = b""

    rejected.close()
    first.close()
    accepted[0].close()
    proxy.close()
    listener.close()
    holder.join(timeout=2)

    assert response == b""
    assert not holder.is_alive()


def test_broker_and_bridge_limits_are_explicit_and_consistent():
    assert broker.MAX_CONNECTIONS == bridge.MAX_CONNECTIONS == 16
    assert broker.MAX_HEADER_BYTES == 64 * 1024
    assert broker.MAX_HTTP_BODY_BYTES == 16 * 1024 * 1024
    assert broker.MAX_RESOLVED_TARGETS == 16
    assert broker.IDLE_TIMEOUT_SECONDS == bridge.IDLE_TIMEOUT_SECONDS > 0
    assert (
        broker.CONNECTION_LIFETIME_SECONDS
        == bridge.CONNECTION_LIFETIME_SECONDS
        < broker.BROKER_LIFETIME_SECONDS
    )


def test_multiple_simultaneous_connect_tunnels_work_within_bounds(tmp_path):
    origins = []
    origin_threads = []

    def connector(_family, _sockaddr, _timeout):
        outbound, origin = socket.socketpair()
        origins.append(origin)

        def echo():
            data = origin.recv(4096)
            origin.sendall(data.upper())
            origin.shutdown(socket.SHUT_WR)

        thread = threading.Thread(target=echo)
        thread.start()
        origin_threads.append(thread)
        return _PublicPeerSocket(outbound)

    transport = _Transport(tmp_path, connector, max_connections=4)
    results = []

    def request(payload):
        client = socket.create_connection(transport.bridge.address, timeout=2)
        client.sendall(b"CONNECT public.example:443 HTTP/1.1\r\n\r\n")
        assert b" 200 " in client.recv(4096)
        client.sendall(payload)
        client.shutdown(socket.SHUT_WR)
        results.append(_read_all(client))
        client.close()

    clients = [
        threading.Thread(target=request, args=(b"first",)),
        threading.Thread(target=request, args=(b"second",)),
    ]
    for client in clients:
        client.start()
    for client in clients:
        client.join(timeout=3)
    transport.close()
    for origin in origins:
        origin.close()
    for thread in origin_threads:
        thread.join(timeout=2)

    assert sorted(results) == [b"FIRST", b"SECOND"]
    assert all(not client.is_alive() for client in clients)


def test_broker_rejects_connections_above_the_per_process_bound(tmp_path):
    origins = []

    def connector(_family, _sockaddr, _timeout):
        outbound, origin = socket.socketpair()
        origins.append(origin)
        return _PublicPeerSocket(outbound)

    transport = _Transport(tmp_path, connector, max_connections=2)
    clients = []
    for _index in range(2):
        client = socket.create_connection(transport.bridge.address, timeout=2)
        client.sendall(b"CONNECT public.example:443 HTTP/1.1\r\n\r\n")
        assert b" 200 " in client.recv(4096)
        clients.append(client)

    rejected = socket.create_connection(transport.bridge.address, timeout=2)
    rejected.sendall(b"CONNECT public.example:443 HTTP/1.1\r\n\r\n")
    try:
        response = rejected.recv(4096)
    except ConnectionResetError:
        response = b""
    rejected.close()
    for client in clients:
        client.close()
    for origin in origins:
        origin.close()
    transport.close()

    # An overloaded broker may close with unread request bytes after its
    # best-effort 503, which is observed as either the response or EOF. The
    # authority invariant is that no additional outbound connection is made.
    assert response == b"" or b" 503 " in response
    assert len(origins) == 2


@pytest.mark.skipif(shutil.which("npm") is None, reason="npm is unavailable")
def test_npm_reads_a_package_document_through_standard_proxy_variables(tmp_path):
    body = (
        b'{"name":"odysseus-fake","dist-tags":{"latest":"1.0.0"},'
        b'"versions":{"1.0.0":{"name":"odysseus-fake","version":"1.0.0"}}}'
    )
    origin_threads = []

    def connector(_family, _sockaddr, _timeout):
        outbound, origin = socket.socketpair()

        def serve():
            request = origin.recv(65536)
            assert request.startswith(b"GET /odysseus-fake HTTP/1.1\r\n")
            origin.sendall(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                + f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
                + body
            )
            origin.shutdown(socket.SHUT_WR)
            origin.close()

        thread = threading.Thread(target=serve)
        thread.start()
        origin_threads.append(thread)
        return _PublicPeerSocket(outbound, peer=(PUBLIC_V4, 80))

    transport = _Transport(tmp_path, connector)
    proxy_environment = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": str(tmp_path / "home"),
        "npm_config_cache": str(tmp_path / "npm-cache"),
        "HTTP_PROXY": transport.proxy_url,
        "HTTPS_PROXY": transport.proxy_url,
        "http_proxy": transport.proxy_url,
        "https_proxy": transport.proxy_url,
    }
    completed = subprocess.run(
        [
            "npm",
            "view",
            "odysseus-fake",
            "version",
            "--registry=http://public.example/",
            "--fetch-retries=0",
            "--fetch-timeout=5000",
            "--json",
        ],
        env=proxy_environment,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    transport.close()
    for thread in origin_threads:
        thread.join(timeout=2)

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip().strip('"') == "1.0.0"
    assert "NO_PROXY" not in proxy_environment
    assert "no_proxy" not in proxy_environment


@pytest.mark.skipif(shutil.which("curl") is None, reason="curl is unavailable")
def test_redirect_to_loopback_is_revalidated_and_blocked(tmp_path):
    connector_calls = []

    def resolver(host, port, **kwargs):
        address = PUBLIC_V4 if host == "public.example" else host
        return _resolver(address)(host, port, **kwargs)

    def connector(_family, _sockaddr, _timeout):
        connector_calls.append(True)
        outbound, origin = socket.socketpair()

        def redirect():
            origin.recv(65536)
            origin.sendall(
                b"HTTP/1.1 302 Found\r\nLocation: http://127.0.0.1/\r\n"
                b"Content-Length: 0\r\nConnection: close\r\n\r\n"
            )
            origin.shutdown(socket.SHUT_WR)
            origin.close()

        threading.Thread(target=redirect).start()
        return _PublicPeerSocket(outbound, peer=(PUBLIC_V4, 80))

    transport = _Transport(tmp_path, connector, resolver=resolver)
    completed = subprocess.run(
        [
            "curl",
            "--silent",
            "--show-error",
            "--fail",
            "--location",
            "--proxy",
            transport.proxy_url,
            "--noproxy",
            "",
            "http://public.example/start",
        ],
        env={"PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")},
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    transport.close()

    assert completed.returncode != 0
    assert len(connector_calls) == 1


@pytest.mark.skipif(shutil.which("curl") is None, reason="curl is unavailable")
def test_https_connect_preserves_end_to_end_ca_validation(tmp_path):
    cryptography = pytest.importorskip("cryptography")
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    assert cryptography
    now = datetime.datetime.now(datetime.timezone.utc)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Odysseus test CA")])
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    server_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    server_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "public.example")])
    server_cert = (
        x509.CertificateBuilder()
        .subject_name(server_name)
        .issuer_name(ca_name)
        .public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("public.example")]),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    ca_path = tmp_path / "ca.pem"
    cert_path = tmp_path / "server.pem"
    key_path = tmp_path / "server-key.pem"
    ca_path.write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))
    cert_path.write_bytes(server_cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        server_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )

    tls_listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    tls_listener.bind(("127.0.0.1", 0))
    tls_listener.listen(1)
    tls_address = tls_listener.getsockname()
    tls_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls_context.load_cert_chain(cert_path, key_path)

    def tls_server():
        raw, _address = tls_listener.accept()
        with tls_context.wrap_socket(raw, server_side=True) as secured:
            assert secured.recv(65536).startswith(b"GET / HTTP/1.1\r\n")
            secured.sendall(
                b"HTTP/1.1 200 OK\r\nContent-Length: 6\r\n"
                b"Connection: close\r\n\r\nsecure"
            )

    server_thread = threading.Thread(target=tls_server)
    server_thread.start()

    def connector(_family, _sockaddr, timeout):
        stream = socket.create_connection(tls_address, timeout=timeout)
        return _PublicPeerSocket(stream)

    transport = _Transport(tmp_path, connector)
    completed = subprocess.run(
        [
            "curl",
            "--silent",
            "--show-error",
            "--fail",
            "--proxy",
            transport.proxy_url,
            "--noproxy",
            "",
            "--cacert",
            str(ca_path),
            "https://public.example/",
        ],
        env={"PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")},
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    transport.close()
    tls_listener.close()
    server_thread.join(timeout=2)

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == "secure"
    assert not server_thread.is_alive()
