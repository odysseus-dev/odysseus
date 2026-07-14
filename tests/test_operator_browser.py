"""Tests for browser_act (CDP): frame codec, consent split, actions, degradation."""

from __future__ import annotations

import base64
import hashlib
import json
import socket
import struct
import threading
import time
from unittest.mock import patch

import pytest


# ── low-level frame codec ──

def test_encode_frame_is_masked_and_roundtrips():
    from services.operator.cdp import encode_frame

    frame = encode_frame(b"hello")
    assert frame[0] == 0x81  # FIN + text opcode
    assert frame[1] & 0x80  # mask bit set
    length = frame[1] & 0x7F
    assert length == 5
    mask = frame[2:6]
    masked = frame[6:]
    unmasked = bytes(b ^ mask[i % 4] for i, b in enumerate(masked))
    assert unmasked == b"hello"


def test_encode_frame_extended_length():
    from services.operator.cdp import encode_frame

    payload = b"x" * 200
    frame = encode_frame(payload)
    assert (frame[1] & 0x7F) == 126
    (declared,) = struct.unpack(">H", frame[2:4])
    assert declared == 200


# ── CDP session against a real loopback websocket server ──

_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def _ws_handshake(conn):
    data = b""
    while b"\r\n\r\n" not in data:
        data += conn.recv(1024)
    key = ""
    for line in data.decode("latin-1").split("\r\n"):
        if line.lower().startswith("sec-websocket-key:"):
            key = line.split(":", 1)[1].strip()
    accept = base64.b64encode(hashlib.sha1((key + _GUID).encode()).digest()).decode()
    conn.sendall(
        (
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {accept}\r\n\r\n"
        ).encode("ascii")
    )


def _ws_read(conn):
    from services.operator.cdp import read_frame

    opcode, payload = read_frame(conn)
    return opcode, payload


def _ws_send_text(conn, text):
    # Server frames are unmasked.
    payload = text.encode("utf-8")
    header = bytes([0x81])
    length = len(payload)
    if length < 126:
        header += bytes([length])
    else:
        header += bytes([126]) + struct.pack(">H", length)
    conn.sendall(header + payload)


class _CdpServer:
    """Minimal CDP echo server: replies to each command id with a canned result."""

    def __init__(self, responder):
        self.responder = responder
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.thread = threading.Thread(target=self._serve, daemon=True)

    def start(self):
        self.thread.start()
        return self

    @property
    def ws_url(self):
        return f"ws://127.0.0.1:{self.port}/devtools/page/ABC"

    def _serve(self):
        conn, _ = self.sock.accept()
        try:
            _ws_handshake(conn)
            while True:
                opcode, payload = _ws_read(conn)
                if opcode == 0x8:
                    break
                msg = json.loads(payload.decode("utf-8"))
                reply = self.responder(msg)
                _ws_send_text(conn, json.dumps({"id": msg["id"], "result": reply}))
        except (ConnectionError, OSError):
            pass
        finally:
            conn.close()
            self.sock.close()


def test_cdp_session_command_roundtrip():
    from services.operator.cdp import CdpSession

    def responder(msg):
        if msg["method"] == "Runtime.evaluate":
            return {"result": {"value": "page-title"}}
        return {}

    server = _CdpServer(responder).start()
    time.sleep(0.05)
    with CdpSession(server.ws_url, timeout=5) as session:
        result = session.command("Runtime.evaluate", {"expression": "document.title"})
    assert result["result"]["value"] == "page-title"


def test_cdp_session_surfaces_command_error():
    from services.operator.cdp import CdpSession

    def responder(msg):
        return {}

    # Responder returns result, but we inject an error path by monkeypatching send.
    server = _CdpServer(lambda m: {"nav": True}).start()
    time.sleep(0.05)
    with CdpSession(server.ws_url, timeout=5) as session:
        result = session.command("Page.navigate", {"url": "https://x.test"})
    assert result == {"nav": True}


# ── browser_act service ──

@pytest.fixture(autouse=True)
def _fresh_consent():
    from services.operator.core import reset_consents
    reset_consents()
    yield
    reset_consents()


def _targets(n=1):
    return [
        {"id": f"t{i}", "type": "page", "title": f"Tab {i}", "url": f"https://x{i}.test",
         "webSocketDebuggerUrl": f"ws://127.0.0.1:9/devtools/page/{i}"}
        for i in range(n)
    ]


def test_unknown_action_rejected():
    from services.operator.browser import browser_act
    result = browser_act({"action": "teleport"})
    assert result["reason"] == "unknown_action"


def test_cdp_unreachable_degrades():
    from services.operator import browser
    with patch.object(browser, "_list_targets", return_value=None):
        result = browser.browser_act({"action": "tabs"})
    assert result["degraded"] is True
    assert result["reason"] == "cdp_unreachable"
    assert "9222" in result["hint"]


def test_tabs_is_read_only_no_consent():
    from services.operator import browser
    with patch.object(browser, "_list_targets", return_value=_targets(2)):
        result = browser.browser_act({"action": "tabs"}, session_id="s1")
    assert result["ok"] is True
    assert result["data"]["count"] == 2
    assert result["data"]["tabs"][0]["id"] == "t0"


def test_snapshot_read_only_no_consent():
    from services.operator import browser
    snap = {"ok": True, "capability": "browser_action", "data": {"snapshot": []}, "degraded": False}
    with patch.object(browser, "_list_targets", return_value=_targets(1)):
        with patch.object(browser, "_run_on_target", return_value=snap) as run:
            result = browser.browser_act({"action": "snapshot"}, session_id="s1")
    assert result["ok"] is True
    run.assert_called_once()


def test_navigate_requires_consent():
    from services.operator import browser
    with patch.object(browser, "_list_targets", return_value=_targets(1)):
        with patch.object(browser, "record_audit") as audit:
            result = browser.browser_act(
                {"action": "navigate", "url": "https://x.test"}, session_id="s1",
            )
    assert result["reason"] == "consent_required"
    assert audit.call_args.kwargs["result"] == "denied"


def test_navigate_with_approval_runs_and_audits():
    from services.operator import browser
    ok = {"ok": True, "capability": "browser_action", "data": {"navigated_to": "https://x.test"}, "degraded": False}
    with patch.object(browser, "_list_targets", return_value=_targets(1)):
        with patch.object(browser, "_run_on_target", return_value=ok):
            with patch.object(browser, "record_audit") as audit:
                first = browser.browser_act(
                    {"action": "navigate", "url": "https://x.test", "user_approved": True},
                    session_id="s1",
                )
                second = browser.browser_act(
                    {"action": "navigate", "url": "https://y.test"}, session_id="s1",
                )
    assert first["ok"] is True
    assert second["ok"] is True  # consent persisted
    assert audit.call_count == 2


def test_click_requires_selector():
    from services.operator import browser
    from services.operator.core import grant_consent
    grant_consent("s1")
    with patch.object(browser, "_list_targets", return_value=_targets(1)):
        result = browser.browser_act({"action": "click"}, session_id="s1")
    assert result["reason"] == "selector_required"


def test_no_open_tab_for_snapshot():
    from services.operator import browser
    with patch.object(browser, "_list_targets", return_value=[]):
        result = browser.browser_act({"action": "snapshot"})
    assert result["reason"] == "no_open_tab"


def test_browser_act_registered():
    from src.agent_tools import TOOL_TAGS
    from src.tool_schemas import FUNCTION_TOOL_SCHEMAS

    names = {t["function"]["name"] for t in FUNCTION_TOOL_SCHEMAS}
    assert "browser_act" in names
    assert "browser_act" in TOOL_TAGS
