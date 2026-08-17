#!/usr/bin/env python3
"""Trusted loopback-to-Unix bridge inside a Bubblewrap network namespace."""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import threading
from typing import Sequence


BROKER_SOCKET = "/run/odysseus-egress/broker.sock"
PROXY_HOST = "127.0.0.1"
PROXY_PORT = 3128
MAX_CONNECTIONS = 16
COPY_BUFFER_BYTES = 64 * 1024


class BridgeError(RuntimeError):
    """The trusted bridge could not be established safely."""


def _copy(source: socket.socket, destination: socket.socket) -> None:
    try:
        while True:
            data = source.recv(COPY_BUFFER_BYTES)
            if not data:
                break
            destination.sendall(data)
    except OSError:
        pass
    finally:
        try:
            destination.shutdown(socket.SHUT_WR)
        except OSError:
            pass


def _relay(left: socket.socket, right: socket.socket) -> None:
    upstream = threading.Thread(target=_copy, args=(left, right), daemon=True)
    downstream = threading.Thread(target=_copy, args=(right, left), daemon=True)
    upstream.start()
    downstream.start()
    upstream.join()
    downstream.join()
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
    ) -> None:
        if not os.path.isabs(broker_socket):
            raise BridgeError("broker socket path must be absolute")
        self.broker_socket = broker_socket
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind((host, port))
        self.listener.listen(max_connections)
        self.listener.settimeout(0.5)
        self.stop = threading.Event()
        self.slots = threading.BoundedSemaphore(max_connections)
        self.accept_thread: threading.Thread | None = None
        self.connections: list[threading.Thread] = []

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
            self.connections = [
                worker for worker in self.connections if worker.is_alive()
            ]
            self.connections.append(thread)
            thread.start()

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
            _relay(client, broker)
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
        for thread in self.connections:
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
