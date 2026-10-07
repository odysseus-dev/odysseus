#!/usr/bin/env python3
"""Trusted loopback-to-Unix bridge inside a Bubblewrap network namespace."""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import threading
import time
from typing import Callable, Sequence


BROKER_SOCKET = "/run/odysseus-egress/broker.sock"
PROXY_HOST = "127.0.0.1"
PROXY_PORT = 3128
MAX_CONNECTIONS = 16
COPY_BUFFER_BYTES = 64 * 1024
IDLE_TIMEOUT_SECONDS = 60.0
CONNECTION_LIFETIME_SECONDS = 15 * 60.0


class BridgeError(RuntimeError):
    """The trusted bridge could not be established safely."""


def _copy(
    source: socket.socket,
    destination: socket.socket,
    stop: threading.Event,
    touch: Callable[[], None],
    expired: Callable[[], bool],
    *,
    stop_after_eof: bool,
) -> None:
    try:
        while not stop.is_set():
            if expired():
                stop.set()
                break
            try:
                data = source.recv(COPY_BUFFER_BYTES)
            except socket.timeout:
                continue
            if not data:
                if stop_after_eof:
                    stop.set()
                break
            destination.sendall(data)
            touch()
    except OSError:
        stop.set()
    finally:
        try:
            destination.shutdown(socket.SHUT_WR)
        except OSError:
            pass


def _relay(
    left: socket.socket,
    right: socket.socket,
    *,
    idle_timeout: float = IDLE_TIMEOUT_SECONDS,
    connection_lifetime: float = CONNECTION_LIFETIME_SECONDS,
) -> None:
    stop = threading.Event()
    activity_lock = threading.Lock()
    last_activity = [time.monotonic()]
    deadline = time.monotonic() + connection_lifetime

    def touch() -> None:
        with activity_lock:
            last_activity[0] = time.monotonic()

    def expired() -> bool:
        with activity_lock:
            idle = time.monotonic() - last_activity[0] >= idle_timeout
        return idle or time.monotonic() >= deadline

    timeout = min(idle_timeout, 1.0)
    left.settimeout(timeout)
    right.settimeout(timeout)
    upstream = threading.Thread(
        target=_copy,
        args=(left, right, stop, touch, expired),
        kwargs={"stop_after_eof": False},
        daemon=True,
    )
    downstream = threading.Thread(
        target=_copy,
        args=(right, left, stop, touch, expired),
        kwargs={"stop_after_eof": True},
        daemon=True,
    )
    upstream.start()
    downstream.start()
    while (upstream.is_alive() or downstream.is_alive()) and not stop.is_set():
        if expired():
            stop.set()
            break
        time.sleep(0.05)
    if stop.is_set():
        # Preserve any broker response already delivered to the client while
        # interrupting the opposite read direction that would otherwise keep
        # the slot alive after broker EOF.
        try:
            left.shutdown(socket.SHUT_RD)
        except OSError:
            pass
        try:
            right.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
    upstream.join(timeout=1.0)
    downstream.join(timeout=1.0)
    left.close()
    right.close()


class LoopbackBridge:
    def __init__(
        self,
        broker_socket: str = BROKER_SOCKET,
        *,
        host: str = PROXY_HOST,
        port: int = PROXY_PORT,
        max_connections: int = MAX_CONNECTIONS,
        idle_timeout: float = IDLE_TIMEOUT_SECONDS,
        connection_lifetime: float = CONNECTION_LIFETIME_SECONDS,
    ) -> None:
        if not os.path.isabs(broker_socket):
            raise BridgeError("broker socket path must be absolute")
        if idle_timeout <= 0 or connection_lifetime <= 0:
            raise BridgeError("bridge time limits must be positive")
        self.broker_socket = broker_socket
        self.idle_timeout = idle_timeout
        self.connection_lifetime = connection_lifetime
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind((host, port))
        self.listener.listen(max_connections)
        self.listener.settimeout(0.5)
        self.stop = threading.Event()
        self.slots = threading.BoundedSemaphore(max_connections)
        self.accept_thread: threading.Thread | None = None
        self.connections: list[threading.Thread] = []
        self.connections_lock = threading.Lock()

    @property
    def address(self) -> tuple[str, int]:
        host, port = self.listener.getsockname()[:2]
        return str(host), int(port)

    def verify_broker(self) -> None:
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            probe.settimeout(2.0)
            probe.connect(self.broker_socket)
        except OSError as exc:
            raise BridgeError("trusted egress broker is unavailable") from exc
        finally:
            probe.close()

    def start(self) -> None:
        self.verify_broker()
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
                client.close()
                continue
            thread = threading.Thread(
                target=self._connect,
                args=(client,),
                daemon=True,
            )
            with self.connections_lock:
                self.connections = [
                    worker for worker in self.connections if worker.is_alive()
                ]
                thread.start()
                self.connections.append(thread)

    def _connect(self, client: socket.socket) -> None:
        broker = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            broker.connect(self.broker_socket)
        except OSError:
            client.close()
            broker.close()
            self.slots.release()
            return
        try:
            _relay(
                client,
                broker,
                idle_timeout=self.idle_timeout,
                connection_lifetime=self.connection_lifetime,
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
        with self.connections_lock:
            connections = list(self.connections)
        for thread in connections:
            thread.join(timeout=0.2)


def _parse_command(arguments: Sequence[str]) -> list[str]:
    if len(arguments) < 3 or arguments[0] != BROKER_SOCKET or arguments[1] != "--":
        raise BridgeError("invalid trusted bridge arguments")
    command = list(arguments[2:])
    if not command or not os.path.isabs(command[0]):
        raise BridgeError("trusted bridge requires an absolute payload executable")
    return command


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    bridge: LoopbackBridge | None = None
    child: subprocess.Popen[bytes] | None = None
    try:
        command = _parse_command(arguments)
        bridge = LoopbackBridge()
        bridge.start()
        child = subprocess.Popen(command, close_fds=True)

        def forward_signal(_signum: int, _frame: object) -> None:
            if child is not None and child.poll() is None:
                child.terminate()

        signal.signal(signal.SIGTERM, forward_signal)
        signal.signal(signal.SIGINT, forward_signal)
        return int(child.wait())
    except BridgeError as exc:
        print(f"odysseus-egress-bridge: {exc}", file=sys.stderr)
        return 64
    except (OSError, subprocess.SubprocessError):
        print("odysseus-egress-bridge: trusted bridge setup failed", file=sys.stderr)
        if child is not None and child.poll() is None:
            child.terminate()
        return 70
    finally:
        if bridge is not None:
            bridge.close()


if __name__ == "__main__":
    raise SystemExit(main())
