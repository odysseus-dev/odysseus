"""Minimal Chrome DevTools Protocol client over a stdlib WebSocket.

Deliberately dependency-free: localhost CDP needs no TLS, no extensions, no
fragmentation handling beyond the basics — a full websocket library would be
overkill for send-command/await-response traffic (design decision D5; swap
for Playwright later without touching the browser_act contract).

Client frames are masked per RFC 6455; server frames arrive unmasked.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import socket
import struct
import time
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_OP_TEXT = 0x1
_OP_CLOSE = 0x8
_OP_PING = 0x9
_OP_PONG = 0xA

DEFAULT_TIMEOUT = 20.0


def encode_frame(payload: bytes, opcode: int = _OP_TEXT) -> bytes:
    """Encode one masked client->server frame (FIN set, no fragmentation)."""
    header = bytes([0x80 | opcode])
    length = len(payload)
    mask_bit = 0x80
    if length < 126:
        header += bytes([mask_bit | length])
    elif length < (1 << 16):
        header += bytes([mask_bit | 126]) + struct.pack(">H", length)
    else:
        header += bytes([mask_bit | 127]) + struct.pack(">Q", length)
    mask = os.urandom(4)
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    return header + mask + masked


def _read_exact(sock: socket.socket, n: int) -> bytes:
    chunks = b""
    while len(chunks) < n:
        chunk = sock.recv(n - len(chunks))
        if not chunk:
            raise ConnectionError("websocket closed mid-frame")
        chunks += chunk
    return chunks


def read_frame(sock: socket.socket) -> Tuple[int, bytes]:
    """Read one server->client frame; returns (opcode, payload)."""
    first, second = _read_exact(sock, 2)
    opcode = first & 0x0F
    masked = bool(second & 0x80)
    length = second & 0x7F
    if length == 126:
        (length,) = struct.unpack(">H", _read_exact(sock, 2))
    elif length == 127:
        (length,) = struct.unpack(">Q", _read_exact(sock, 8))
    mask = _read_exact(sock, 4) if masked else b""
    payload = _read_exact(sock, length) if length else b""
    if masked:
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    return opcode, payload


class CdpSession:
    """One websocket connection to a CDP target ("page")."""

    def __init__(self, ws_url: str, timeout: float = DEFAULT_TIMEOUT):
        self.ws_url = ws_url
        self.timeout = timeout
        self._next_id = 0
        parsed = urlparse(ws_url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 80
        path = parsed.path or "/"
        if parsed.query:
            path += f"?{parsed.query}"
        self._sock = socket.create_connection((host, port), timeout=timeout)
        self._handshake(host, port, path)

    def _handshake(self, host: str, port: int, path: str) -> None:
        key = base64.b64encode(os.urandom(16)).decode()
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        )
        self._sock.sendall(request.encode("ascii"))
        response = b""
        while b"\r\n\r\n" not in response:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise ConnectionError("websocket handshake failed: connection closed")
            response += chunk
        status_line = response.split(b"\r\n", 1)[0].decode("latin-1")
        if "101" not in status_line:
            raise ConnectionError(f"websocket handshake rejected: {status_line}")

    def command(self, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Send one CDP command and wait for its matching response.

        CDP events arriving in between are ignored; ping frames are ponged.
        """
        self._next_id += 1
        msg_id = self._next_id
        payload = json.dumps({"id": msg_id, "method": method, "params": params or {}})
        self._sock.sendall(encode_frame(payload.encode("utf-8")))

        deadline = time.monotonic() + self.timeout
        while True:
            if time.monotonic() > deadline:
                raise TimeoutError(f"CDP command timed out: {method}")
            opcode, frame = read_frame(self._sock)
            if opcode == _OP_PING:
                self._sock.sendall(encode_frame(frame, opcode=_OP_PONG))
                continue
            if opcode == _OP_CLOSE:
                raise ConnectionError("websocket closed by browser")
            if opcode != _OP_TEXT:
                continue
            try:
                message = json.loads(frame.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if message.get("id") != msg_id:
                continue  # event or stale response
            if "error" in message:
                raise RuntimeError(
                    f"CDP error for {method}: {message['error'].get('message', message['error'])}"
                )
            return message.get("result") or {}

    def close(self) -> None:
        try:
            self._sock.sendall(encode_frame(b"", opcode=_OP_CLOSE))
        except OSError:
            pass
        try:
            self._sock.close()
        except OSError:
            pass

    def __enter__(self) -> "CdpSession":
        return self

    def __exit__(self, *args) -> None:
        self.close()
