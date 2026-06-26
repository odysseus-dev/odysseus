"""Forking a session must not mutate the source session's messages.

ChatMessage.metadata is a dict. add_message() -> _persist_message() stamps
_db_id (and timestamp) onto that dict in place. The fork handler used to pass
the source message's metadata dict by reference into the new session, so
persisting the fork rewrote the SOURCE messages' _db_id — breaking
edit/delete-by-id on the original conversation. The fork must copy the dict.
"""
import asyncio
from types import SimpleNamespace

from core.models import ChatMessage
import routes.history_routes as mod


class _FakeSession:
    def __init__(self, name="", owner=None):
        self.name = name
        self.owner = owner
        self.endpoint_url = ""
        self.model = ""
        self.history = []

    def add_message(self, message):
        # Mirror _persist_message: stamp the in-memory message's metadata.
        if message.metadata is None:
            message.metadata = {}
        message.metadata["_db_id"] = f"new-{len(self.history)}"
        self.history.append(message)


class _FakeSessionManager:
    def __init__(self, source):
        self.sessions = {"src-id": source}
        self.created = None

    def create_session(self, session_id=None, name=None, endpoint_url=None,
                       model=None, rag=False, owner=None):
        self.created = _FakeSession(name=name, owner=owner)
        return self.created

    def save_sessions(self):
        pass


def _fork_handler(router):
    for route in router.routes:
        if "/fork" in getattr(route, "path", "") and "POST" in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError("fork route not found")


def test_fork_does_not_corrupt_source_message_metadata(monkeypatch):
    monkeypatch.setattr(mod, "_verify_session_owner", lambda *a, **k: None)

    source = _FakeSession(name="Original", owner="alice")
    source.history = [
        ChatMessage("user", "hi", {"_db_id": "src-0"}),
        ChatMessage("assistant", "yo", {"_db_id": "src-1"}),
    ]
    sm = _FakeSessionManager(source)

    req = SimpleNamespace()

    async def _json():
        return {"keep_count": 2}

    req.json = _json

    router = mod.setup_history_routes(sm)
    fork = _fork_handler(router)
    result = asyncio.run(fork(request=req, session_id="src-id"))

    assert result["status"] == "ok"
    assert result["kept"] == 2

    # The forked session got its own metadata dicts...
    new_session = sm.created
    assert new_session.history[0].metadata is not source.history[0].metadata
    assert new_session.history[1].metadata is not source.history[1].metadata

    # ...and the source session's _db_id values are untouched.
    assert source.history[0].metadata["_db_id"] == "src-0"
    assert source.history[1].metadata["_db_id"] == "src-1"
