#!/usr/bin/env python3
"""Fixed-purpose public HTTP(S) egress broker for process sandboxes.

The broker runs outside Bubblewrap with an empty environment.  A unique Unix
socket is mounted read-only into one private network namespace, where the
trusted loopback bridge exposes it as a conventional HTTP proxy.  The broker
resolves every requested destination itself and connects only to globally
routable addresses on TCP port 80 or 443.
"""

from __future__ import annotations

import ipaddress
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from typing import Callable, Iterable, Sequence
from urllib.parse import urlsplit


TRUSTED_LAUNCHER = "/usr/local/libexec/odysseus-seccomp-launcher"
TRUSTED_BWRAP = "/usr/bin/bwrap"
SANDBOX_RUNTIME_DIR = "/run/odysseus-egress"
SANDBOX_SOCKET = f"{SANDBOX_RUNTIME_DIR}/broker.sock"

MAX_CONNECTIONS = 16
MAX_HEADER_BYTES = 64 * 1024
MAX_HTTP_BODY_BYTES = 16 * 1024 * 1024
MAX_TUNNEL_BYTES_PER_DIRECTION = 1024 * 1024 * 1024
MAX_RESOLVED_TARGETS = 16
CONNECT_TIMEOUT_SECONDS = 10.0
HEADER_TIMEOUT_SECONDS = 10.0
IDLE_TIMEOUT_SECONDS = 60.0
CONNECTION_LIFETIME_SECONDS = 15 * 60.0
BROKER_LIFETIME_SECONDS = 60 * 60.0

_TOKEN = re.compile(rb"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authorization",
    "proxy-connection",
    "te",
    "trailer",
    "upgrade",
}
_CONNECTION_PROTECTED_HEADERS = {
    "content-length",
    "expect",
    "host",
    "transfer-encoding",
}
_NAT64_WELL_KNOWN = ipaddress.ip_network("64:ff9b::/96")
_NAT64_LOCAL_USE = ipaddress.ip_network("64:ff9b:1::/48")


class BrokerError(RuntimeError):
    """A fail-closed broker setup or destination-policy error."""


class RequestError(BrokerError):
    """A client request that the constrained proxy must reject."""

    def __init__(self, status: int, reason: str):
        super().__init__(reason)
        self.status = status
        self.reason = reason


Resolver = Callable[..., Iterable[tuple]]
Connector = Callable[[int, tuple, float], socket.socket]


@dataclass(frozen=True)
class PublicTarget:
    family: int
    sockaddr: tuple


def _public_ip(value: object) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    if not isinstance(value, str):
        raise BrokerError("destination did not resolve to an IP address")
    try:
        address = ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError as exc:
        raise BrokerError("destination did not resolve to an IP address") from exc
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    if isinstance(address, ipaddress.IPv6Address):
        if address in _NAT64_LOCAL_USE:
            raise BrokerError("destination resolved to a non-public address")
        if address in _NAT64_WELL_KNOWN:
            embedded = ipaddress.IPv4Address(address.packed[-4:])
            if (
                not embedded.is_global
                or embedded.is_multicast
                or embedded.is_private
                or embedded.is_loopback
                or embedded.is_link_local
                or embedded.is_reserved
                or embedded.is_unspecified
            ):
                raise BrokerError("destination resolved to a non-public address")
            return embedded
    # is_global rejects loopback, RFC1918, link-local/metadata, CGNAT, ULA,
    # multicast, unspecified, benchmarking, documentation, and reserved space.
    if (
        not address.is_global
        or address.is_multicast
        or address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_unspecified
    ):
        raise BrokerError("destination resolved to a non-public address")
    return address


def resolve_public_targets(
    host: str,
    port: int,
    *,
    resolver: Resolver = socket.getaddrinfo,
) -> list[PublicTarget]:
    """Resolve once, reject mixed public/private answers, and retain IP tuples."""
    if not isinstance(host, str) or not host or len(host) > 253 or "\x00" in host:
        raise BrokerError("invalid destination host")
    try:
        answers = list(
            resolver(
                host,
                port,
                family=socket.AF_UNSPEC,
                type=socket.SOCK_STREAM,
                proto=socket.IPPROTO_TCP,
            )
        )
    except (OSError, UnicodeError, ValueError) as exc:
        raise BrokerError("destination resolution failed") from exc
    if not answers:
        raise BrokerError("destination resolution failed")

    targets: list[PublicTarget] = []
    seen: set[tuple[int, tuple]] = set()
    too_many_targets = False
    for answer in answers:
        if not isinstance(answer, tuple) or len(answer) < 5:
            raise BrokerError("destination resolution returned an invalid answer")
        family, socket_type, _protocol, _canonical, sockaddr = answer[:5]
        if family not in (socket.AF_INET, socket.AF_INET6):
            raise BrokerError("destination resolution returned an unsupported family")
        if socket_type not in (0, socket.SOCK_STREAM):
            raise BrokerError("destination resolution returned an unsupported socket type")
        if not isinstance(sockaddr, tuple) or len(sockaddr) < 2:
            raise BrokerError("destination resolution returned an invalid address")
        _public_ip(sockaddr[0])
        normalized = (family, sockaddr)
        if normalized not in seen:
            if len(seen) >= MAX_RESOLVED_TARGETS:
                too_many_targets = True
                continue
            seen.add(normalized)
            targets.append(PublicTarget(family, sockaddr))
    if too_many_targets:
        raise BrokerError("destination resolved to too many addresses")
    if not targets:
        raise BrokerError("destination resolution failed")
    return targets


def _default_connector(family: int, sockaddr: tuple, timeout: float) -> socket.socket:
    outbound = socket.socket(family, socket.SOCK_STREAM, socket.IPPROTO_TCP)
    try:
        outbound.settimeout(timeout)
        outbound.connect(sockaddr)
        return outbound
    except Exception:
        outbound.close()
        raise


def connect_public_target(
    host: str,
    port: int,
    *,
    resolver: Resolver = socket.getaddrinfo,
    connector: Connector = _default_connector,
) -> socket.socket:
    """Connect to the exact approved sockaddr without a second name lookup."""
    targets = resolve_public_targets(host, port, resolver=resolver)
    for target in targets:
        target_address = _public_ip(target.sockaddr[0])
        target_port = int(target.sockaddr[1])
        try:
            outbound = connector(
                target.family,
                target.sockaddr,
                CONNECT_TIMEOUT_SECONDS,
            )
        except OSError:
            continue
        try:
            peer = outbound.getpeername()
            if not isinstance(peer, tuple) or len(peer) < 2:
                raise BrokerError("connected peer address is unavailable")
            peer_address = _public_ip(peer[0])
            if peer_address != target_address or int(peer[1]) != target_port:
                raise BrokerError("connected peer does not match the approved target")
        except Exception:
            outbound.close()
            continue
        outbound.settimeout(IDLE_TIMEOUT_SECONDS)
        return outbound
    raise BrokerError("public destination connection failed")


def _read_headers(client: socket.socket) -> tuple[bytes, bytes]:
    client.settimeout(HEADER_TIMEOUT_SECONDS)
    data = bytearray()
    while True:
        marker = data.find(b"\r\n\r\n")
        if marker >= 0:
            return bytes(data[:marker]), bytes(data[marker + 4:])
        if len(data) >= MAX_HEADER_BYTES:
            raise RequestError(431, "request headers too large")
        chunk = client.recv(min(4096, MAX_HEADER_BYTES - len(data)))
        if not chunk:
            raise RequestError(400, "incomplete proxy request")
        data.extend(chunk)


def _parse_headers(block: bytes) -> tuple[str, str, str, list[tuple[str, str]]]:
    try:
        lines = block.split(b"\r\n")
        request_line = lines[0].decode("ascii")
    except (IndexError, UnicodeDecodeError) as exc:
        raise RequestError(400, "invalid proxy request line") from exc
    parts = request_line.split(" ")
    if len(parts) != 3:
        raise RequestError(400, "invalid proxy request line")
    method, target, version = parts
    if not _TOKEN.fullmatch(method.encode("ascii", errors="ignore")):
        raise RequestError(400, "invalid HTTP method")
    if version not in {"HTTP/1.0", "HTTP/1.1"}:
        raise RequestError(400, "unsupported HTTP version")
    if any(ord(character) < 0x21 or ord(character) == 0x7F for character in target):
        raise RequestError(400, "invalid proxy destination")

    headers: list[tuple[str, str]] = []
    for raw in lines[1:]:
        if not raw or raw[:1] in {b" ", b"\t"} or b":" not in raw:
            raise RequestError(400, "invalid proxy request header")
        name, value = raw.split(b":", 1)
        if not _TOKEN.fullmatch(name):
            raise RequestError(400, "invalid proxy request header")
        try:
            decoded_value = value.decode("iso-8859-1").strip()
        except UnicodeDecodeError as exc:  # pragma: no cover - latin-1 is total
            raise RequestError(400, "invalid proxy request header") from exc
        if any(ord(character) < 0x20 or ord(character) == 0x7F for character in decoded_value):
            raise RequestError(400, "invalid proxy request header")
        headers.append((name.decode("ascii"), decoded_value))
    return method, target, version, headers


def _parse_authority(authority: str, *, default_port: int) -> tuple[str, int]:
    if not authority or "@" in authority or any(char.isspace() for char in authority):
        raise RequestError(400, "invalid proxy destination")
    if authority.startswith("["):
        end = authority.find("]")
        if end <= 1:
            raise RequestError(400, "invalid proxy destination")
        host = authority[1:end]
        suffix = authority[end + 1:]
        if suffix:
            if not suffix.startswith(":"):
                raise RequestError(400, "invalid proxy destination")
            port_text = suffix[1:]
        else:
            port_text = str(default_port)
    else:
        if authority.count(":") > 1:
            raise RequestError(400, "IPv6 proxy destinations must be bracketed")
        if ":" in authority:
            host, port_text = authority.rsplit(":", 1)
        else:
            host, port_text = authority, str(default_port)
    if not host or not port_text.isascii() or not port_text.isdigit():
        raise RequestError(400, "invalid proxy destination")
    port = int(port_text)
    if not 1 <= port <= 65535:
        raise RequestError(400, "invalid proxy destination port")
    return host, port


def _format_authority(host: str, port: int) -> str:
    bracketed = f"[{host}]" if ":" in host and not host.startswith("[") else host
    return f"{bracketed}:{port}"


def _send_error(client: socket.socket, status: int, reason: str) -> None:
    labels = {
        400: "Bad Request",
        403: "Forbidden",
        431: "Request Header Fields Too Large",
        502: "Bad Gateway",
        503: "Service Unavailable",
    }
    label = labels.get(status, "Proxy Error")
    body = f"{reason}\n".encode("utf-8", errors="replace")[:512]
    response = (
        f"HTTP/1.1 {status} {label}\r\n"
        "Content-Type: text/plain\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Connection: close\r\n\r\n"
    ).encode("ascii") + body
    try:
        client.sendall(response)
    except OSError:
        pass


def _relay(
    client: socket.socket,
    outbound: socket.socket,
    *,
    client_bytes_remaining: int | None,
) -> None:
    stop = threading.Event()
    activity_lock = threading.Lock()
    last_activity = [time.monotonic()]
    deadline = time.monotonic() + CONNECTION_LIFETIME_SECONDS

    def touch() -> None:
        with activity_lock:
            last_activity[0] = time.monotonic()

    def idle_expired() -> bool:
        with activity_lock:
            return time.monotonic() - last_activity[0] >= IDLE_TIMEOUT_SECONDS

    def pump(
        source: socket.socket,
        destination: socket.socket,
        limit: int | None,
        *,
        stop_after_eof: bool,
    ) -> None:
        remaining = limit
        try:
            source.settimeout(min(IDLE_TIMEOUT_SECONDS, 5.0))
            destination.settimeout(min(IDLE_TIMEOUT_SECONDS, 5.0))
            while not stop.is_set() and time.monotonic() < deadline:
                if remaining == 0:
                    break
                size = 64 * 1024 if remaining is None else min(64 * 1024, remaining)
                try:
                    data = source.recv(size)
                except socket.timeout:
                    if idle_expired():
                        stop.set()
                    continue
                if not data:
                    if stop_after_eof:
                        stop.set()
                    break
                destination.sendall(data)
                touch()
                if remaining is not None:
                    remaining -= len(data)
            try:
                destination.shutdown(socket.SHUT_WR)
            except OSError:
                pass
        except OSError:
            stop.set()

    client_limit = (
        MAX_TUNNEL_BYTES_PER_DIRECTION
        if client_bytes_remaining is None
        else client_bytes_remaining
    )
    upstream = threading.Thread(
        target=pump,
        args=(client, outbound, client_limit),
        kwargs={"stop_after_eof": False},
        daemon=True,
    )
    downstream = threading.Thread(
        target=pump,
        args=(outbound, client, MAX_TUNNEL_BYTES_PER_DIRECTION),
        kwargs={"stop_after_eof": True},
        daemon=True,
    )
    upstream.start()
    downstream.start()
    while (upstream.is_alive() or downstream.is_alive()) and not stop.is_set():
        if time.monotonic() >= deadline or idle_expired():
            stop.set()
            break
        time.sleep(0.05)
    if stop.is_set():
        for stream in (client, outbound):
            try:
                stream.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
    upstream.join(timeout=1.0)
    downstream.join(timeout=1.0)


def _single_header(headers: Sequence[tuple[str, str]], name: str) -> str | None:
    values = [value for key, value in headers if key.casefold() == name]
    if len(values) > 1:
        raise RequestError(400, f"multiple {name} headers are not allowed")
    return values[0] if values else None


def _connection_options(headers: Sequence[tuple[str, str]]) -> set[str]:
    """Parse hop-by-hop names without allowing request-framing removal."""
    options: set[str] = set()
    for name, value in headers:
        if name.casefold() != "connection":
            continue
        for raw_option in value.split(","):
            option = raw_option.strip()
            if not option:
                continue
            try:
                encoded = option.encode("ascii")
            except UnicodeEncodeError as exc:
                raise RequestError(400, "invalid Connection header") from exc
            if not _TOKEN.fullmatch(encoded):
                raise RequestError(400, "invalid Connection header")
            options.add(option.casefold())
    if options & _CONNECTION_PROTECTED_HEADERS:
        raise RequestError(400, "Connection header cannot remove request framing")
    return options


def _serve_connect(
    client: socket.socket,
    target: str,
    remainder: bytes,
    *,
    resolver: Resolver,
    connector: Connector,
) -> None:
    host, port = _parse_authority(target, default_port=443)
    if port != 443:
        raise RequestError(403, "CONNECT is limited to public TCP port 443")
    outbound = connect_public_target(
        host,
        port,
        resolver=resolver,
        connector=connector,
    )
    try:
        client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        if len(remainder) > MAX_TUNNEL_BYTES_PER_DIRECTION:
            raise RequestError(400, "tunnel preface too large")
        if remainder:
            outbound.sendall(remainder)
        _relay(
            client,
            outbound,
            client_bytes_remaining=MAX_TUNNEL_BYTES_PER_DIRECTION - len(remainder),
        )
    finally:
        outbound.close()


def _serve_http(
    client: socket.socket,
    method: str,
    target: str,
    version: str,
    headers: Sequence[tuple[str, str]],
    remainder: bytes,
    *,
    resolver: Resolver,
    connector: Connector,
) -> None:
    try:
        parsed = urlsplit(target)
        parsed_port = parsed.port
    except ValueError as exc:
        raise RequestError(400, "invalid HTTP proxy destination") from exc
    if (
        parsed.scheme.casefold() != "http"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise RequestError(400, "plain proxy requests require an absolute HTTP URL")
    host = parsed.hostname
    port = 80 if parsed_port is None else parsed_port
    if port != 80:
        raise RequestError(403, "plain HTTP proxying is limited to public TCP port 80")

    host_header = _single_header(headers, "host")
    if host_header is not None:
        header_host, header_port = _parse_authority(host_header, default_port=80)
        if header_host.rstrip(".").casefold() != host.rstrip(".").casefold() or header_port != port:
            raise RequestError(400, "Host header does not match proxy destination")
    if _single_header(headers, "expect") is not None:
        raise RequestError(400, "Expect requests are not supported")
    if _single_header(headers, "transfer-encoding") is not None:
        raise RequestError(400, "streaming HTTP request bodies are not supported")
    content_length = _single_header(headers, "content-length")
    if content_length is None:
        body_length = 0
    elif not content_length.isascii() or not content_length.isdigit():
        raise RequestError(400, "invalid Content-Length")
    else:
        body_length = int(content_length)
    if body_length > MAX_HTTP_BODY_BYTES:
        raise RequestError(400, "HTTP request body too large")
    if len(remainder) > body_length:
        raise RequestError(400, "pipelined proxy requests are not supported")
    connection_tokens = _connection_options(headers)

    outbound = connect_public_target(
        host,
        port,
        resolver=resolver,
        connector=connector,
    )
    try:
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"
        forwarded = [f"{method} {path} {version}\r\n"]
        forwarded.append(f"Host: {_format_authority(host, port)}\r\n")
        for name, value in headers:
            folded = name.casefold()
            if folded == "host" or folded in _HOP_BY_HOP or folded in connection_tokens:
                continue
            forwarded.append(f"{name}: {value}\r\n")
        forwarded.append("Connection: close\r\n\r\n")
        outbound.sendall("".join(forwarded).encode("iso-8859-1") + remainder)
        _relay(
            client,
            outbound,
            client_bytes_remaining=body_length - len(remainder),
        )
    finally:
        outbound.close()


def serve_proxy_client(
    client: socket.socket,
    *,
    resolver: Resolver = socket.getaddrinfo,
    connector: Connector = _default_connector,
) -> None:
    try:
        header_block, remainder = _read_headers(client)
        method, target, version, headers = _parse_headers(header_block)
        if method == "CONNECT":
            _serve_connect(
                client,
                target,
                remainder,
                resolver=resolver,
                connector=connector,
            )
        else:
            _serve_http(
                client,
                method,
                target,
                version,
                headers,
                remainder,
                resolver=resolver,
                connector=connector,
            )
    except RequestError as exc:
        _send_error(client, exc.status, exc.reason)
    except BrokerError:
        _send_error(client, 403, "destination blocked by sandbox egress policy")
    except (OSError, UnicodeError, ValueError):
        _send_error(client, 502, "public destination connection failed")
    finally:
        try:
            client.close()
        except OSError:
            pass


class BrokerServer:
    def __init__(
        self,
        listener: socket.socket,
        *,
        resolver: Resolver = socket.getaddrinfo,
        connector: Connector = _default_connector,
        max_connections: int = MAX_CONNECTIONS,
    ) -> None:
        self.listener = listener
        self.resolver = resolver
        self.connector = connector
        self.stop = threading.Event()
        self.slots = threading.BoundedSemaphore(max_connections)
        self.threads: list[threading.Thread] = []
        self.threads_lock = threading.Lock()
        self.accept_thread: threading.Thread | None = None

    def start(self) -> None:
        self.listener.settimeout(0.5)
        self.accept_thread = threading.Thread(target=self._accept, daemon=True)
        self.accept_thread.start()

    def _accept(self) -> None:
        while not self.stop.is_set():
            try:
                client, _address = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            if not self.slots.acquire(blocking=False):
                _send_error(client, 503, "sandbox egress connection limit reached")
                client.close()
                continue
            thread = threading.Thread(
                target=self._handle,
                args=(client,),
                daemon=True,
            )
            with self.threads_lock:
                self.threads = [worker for worker in self.threads if worker.is_alive()]
                thread.start()
                self.threads.append(thread)

    def _handle(self, client: socket.socket) -> None:
        try:
            serve_proxy_client(
                client,
                resolver=self.resolver,
                connector=self.connector,
            )
        finally:
            self.slots.release()

    def close(self) -> None:
        self.stop.set()
        try:
            self.listener.close()
        except OSError:
            pass
        if self.accept_thread is not None:
            self.accept_thread.join(timeout=1.0)
        with self.threads_lock:
            threads = list(self.threads)
        for thread in threads:
            thread.join(timeout=0.2)


def build_child_argv(arguments: Sequence[str], runtime_dir: str) -> list[str]:
    """Validate the fixed launch chain and inject one read-only broker mount."""
    if (
        len(arguments) < 4
        or arguments[0] != TRUSTED_LAUNCHER
        or arguments[1] != TRUSTED_BWRAP
    ):
        raise BrokerError("invalid trusted sandbox launch chain")
    try:
        separator = arguments.index("--", 2)
    except ValueError as exc:
        raise BrokerError("invalid Bubblewrap arguments") from exc
    setup = list(arguments[2:separator])
    if setup.count("--unshare-net") != 1:
        raise BrokerError("private network namespace is required")
    if any(value == "--share-net" or value.startswith("--share-net=") for value in setup):
        raise BrokerError("raw network sharing is forbidden")
    if SANDBOX_RUNTIME_DIR in setup or SANDBOX_SOCKET in setup:
        raise BrokerError("caller-supplied broker mounts are forbidden")
    injected = [
        "--dir",
        "/run",
        "--ro-bind",
        runtime_dir,
        SANDBOX_RUNTIME_DIR,
    ]
    return [*arguments[:separator], *injected, *arguments[separator:]]


def _unix_listener(path: str) -> socket.socket:
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        listener.bind(path)
        os.chmod(path, 0o600)
        listener.listen(MAX_CONNECTIONS)
        return listener
    except Exception:
        listener.close()
        raise


def _terminate(child: subprocess.Popen[bytes]) -> None:
    if child.poll() is not None:
        return
    child.terminate()
    try:
        child.wait(timeout=5)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait(timeout=5)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    # The broker never needs application configuration or credentials.  Clear
    # them before opening the socket or starting any worker threads.
    os.environ.clear()
    runtime_dir: str | None = None
    server: BrokerServer | None = None
    child: subprocess.Popen[bytes] | None = None
    try:
        runtime_dir = tempfile.mkdtemp(prefix="odysseus-egress-", dir="/tmp")
        os.chmod(runtime_dir, 0o700)
        socket_path = os.path.join(runtime_dir, "broker.sock")
        listener = _unix_listener(socket_path)
        server = BrokerServer(listener)
        server.start()
        child_argv = build_child_argv(arguments, runtime_dir)
        child = subprocess.Popen(child_argv, env={}, close_fds=True)

        def forward_signal(_signum: int, _frame: object) -> None:
            if child is not None and child.poll() is None:
                child.terminate()

        signal.signal(signal.SIGTERM, forward_signal)
        signal.signal(signal.SIGINT, forward_signal)
        deadline = time.monotonic() + BROKER_LIFETIME_SECONDS
        while child.poll() is None and time.monotonic() < deadline:
            time.sleep(0.1)
        if child.poll() is None:
            print(
                "odysseus-egress-broker: sandbox network lifetime exceeded",
                file=sys.stderr,
            )
            _terminate(child)
            return 72
        return int(child.returncode or 0)
    except BrokerError as exc:
        print(f"odysseus-egress-broker: {exc}", file=sys.stderr)
        return 64
    except (OSError, subprocess.SubprocessError):
        print("odysseus-egress-broker: trusted broker setup failed", file=sys.stderr)
        if child is not None:
            _terminate(child)
        return 70
    finally:
        if server is not None:
            server.close()
        if runtime_dir is not None:
            shutil.rmtree(runtime_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
