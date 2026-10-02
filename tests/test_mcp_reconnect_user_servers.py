"""A stale MCP session must be recovered for user-configured servers too.

`call_tool` only attempted a reconnect when `is_builtin(server_id)` was true.
A server registered through the UI therefore stayed broken for the rest of the
process once its session went stale — the usual cause being that it was
registered from a request task that has since ended, taking the scope of the
stdio streams with it. The call then failed with anyio's ClosedResourceError,
whose str() is empty, so both the log line and the tool result ended in a bare
colon and the model invented a cause for the failure.
"""
import uuid

import pytest

from src.mcp_manager import McpManager


class _DeadSession:
    """Raises the way a closed anyio stream does: no message at all."""

    async def call_tool(self, name, arguments):
        import anyio
        raise anyio.ClosedResourceError()


class _LiveSession:
    async def call_tool(self, name, arguments):
        class _C:
            type = "text"
            text = "ok"
        class _R:
            content = [_C()]
            isError = False
        return _R()


def test_describe_exception_never_returns_empty():
    # Bewusst lokal importiert: auf dev fehlt die Funktion, und ein
    # Modulimport wuerde die beiden Verhaltenstests mit herunterreissen.
    import anyio
    from src.mcp_manager import _describe_exception
    assert str(anyio.ClosedResourceError()) == ""
    assert _describe_exception(anyio.ClosedResourceError()) == "ClosedResourceError"
    assert _describe_exception(RuntimeError("boom")) == "boom"


async def test_user_server_reconnects_after_stale_session(monkeypatch):
    m = McpManager()
    sid = "u" + uuid.uuid4().hex[:6]          # kein builtin-Präfix
    assert not m.is_builtin(sid)

    m._sessions[sid] = _DeadSession()

    called = {"n": 0}

    async def fake_reconnect(server_id):
        called["n"] += 1
        m._sessions[server_id] = _LiveSession()
        return True

    monkeypatch.setattr(m, "_reconnect_configured", fake_reconnect, raising=False)

    res = await m.call_tool(f"mcp__{sid}__send_message", {"text": "hi"})

    assert called["n"] == 1, "kein Reconnect für einen selbst registrierten Server"
    assert res.get("exit_code", 0) == 0, res


async def test_failure_message_is_never_empty(monkeypatch):
    """Even when reconnect fails, the caller must learn what went wrong."""
    m = McpManager()
    sid = "u" + uuid.uuid4().hex[:6]
    m._sessions[sid] = _DeadSession()

    async def fake_reconnect(server_id):
        return False

    monkeypatch.setattr(m, "_reconnect_configured", fake_reconnect, raising=False)

    res = await m.call_tool(f"mcp__{sid}__send_message", {"text": "hi"})

    assert res["exit_code"] == 1
    assert "ClosedResourceError" in res["error"], res["error"]
    assert res["error"].strip() not in ("", ":"), "leere Fehlermeldung"
