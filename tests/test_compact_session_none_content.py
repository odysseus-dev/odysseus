"""Regression: the manual compact endpoint must not crash when the session
history contains a tool-call turn (assistant ChatMessage with content == None).

POST /api/session/{session_id}/compact builds the text to summarize in
compact_session (routes/history_routes.py) with::

    convo_text = "\\n".join(
        f"{(m.role if isinstance(m, ChatMessage) else m.get('role', '')).upper()}: "
        f"{(m.content if isinstance(m, ChatMessage) else m.get('content', ''))[:2000]}"
        for m in older
    )

When ``m`` is a ChatMessage whose ``content`` is None (the model issued only
native tool_calls, no text), ``None[:2000]`` raises
``TypeError: 'NoneType' object is not subscriptable``. The dict branch is the
same trap: ``m.get('content', '')`` returns None when the key exists with value
None. The handler catches the error and returns 500, so any session with
tool-use history cannot be compacted. The same shape was fixed in
context_compactor.maybe_compact by PR #1777; this endpoint was missed.

The test drives the real compact_session handler. Importing that module pulls
in the heavy app graph (core.database, core/__init__, src.*), so the driver
runs in a subprocess (the same isolation pattern used by
test_rag_vector_id_stability). All module mocking lives in the child process
and dies with it, leaving the parent test session's import state untouched.
"""

import os
import subprocess
import sys
import textwrap

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Exit code the child uses to signal "the real app graph is not importable here"
# (no httpx / sqlalchemy installed), so the parent skips instead of failing.
_SKIP_CODE = 77


# Child program: import the REAL route module, then patch the handler's lazy
# collaborators (token math + summary LLM call + endpoint resolution) so no
# network or DB engine is touched. Build a history whose older half holds a
# None-content tool-call turn (ChatMessage or raw dict, selected by argv), call
# the real handler, and report the outcome. On buggy code the convo_text join
# raises TypeError and the handler returns a 500 HTTPException; the child prints
# RESULT:<status> so the parent can assert.
_CHILD = textwrap.dedent(
    r'''
    import asyncio, sys
    from unittest.mock import MagicMock

    # The real route module needs the full app graph (httpx, sqlalchemy). If
    # those are not installed in this environment, signal the parent to skip
    # rather than fail.
    try:
        from core.models import ChatMessage
        import routes.history_routes as hr
        import src.model_context as model_context
        import src.endpoint_resolver as endpoint_resolver
        import src.llm_core as llm_core
    except ModuleNotFoundError as e:
        sys.stderr.write("SKIP_DEP:" + str(e) + "\n")
        raise SystemExit(77)

    setup_history_routes = hr.setup_history_routes

    # The handler imports its summary/token collaborators lazily
    # (from src.X import Y inside the function body), so patching the source
    # modules takes effect at call time. The None-content crash fires in the
    # convo_text join before any of these run, so the test still goes RED on
    # buggy code.

    model_context.estimate_tokens = lambda messages: 1
    model_context.get_context_length = lambda url, model: 1000
    endpoint_resolver.resolve_endpoint = lambda which: (None, None, None)

    async def _fake_call(*a, **k):
        return "compact summary text"
    llm_core.llm_call_async = _fake_call

    # SessionLocal / _verify_session_owner are bound in the route module's
    # namespace at import time, so patch them there to keep the handler off the
    # real DB and ownership check.
    hr.SessionLocal = lambda: MagicMock()
    hr._verify_session_owner = lambda request, session_id, session_manager=None: None

    shape = sys.argv[1]  # "chatmessage" or "dict"

    def turn(role, content, **extra):
        if shape == "dict":
            d = {"role": role, "content": content}
            d.update(extra)
            return d
        return ChatMessage(role=role, content=content,
                           metadata=(extra or None))

    # >= 6 messages so compaction runs; the older half (all but last 4) holds a
    # tool-call turn whose content is None.
    history = [
        turn("user", "turn 1: search the web"),
        turn("assistant", None, tool_calls=[{"id": "c1"}]),
        turn("tool", "search results"),
        turn("assistant", "Here is what I found."),
        turn("user", "turn 2"),
        turn("assistant", "reply 2"),
        turn("user", "turn 3"),
        turn("assistant", "reply 3"),
    ]

    class _Session:
        history = history
        endpoint_url = "http://local/v1/chat/completions"
        model = "local-model"
        headers = {}
        message_count = len(history)
        def get_context_messages(self):
            return []

    class _Manager:
        def get_session(self, session_id):
            return _Session()
        def save_sessions(self):
            pass

    router = setup_history_routes(_Manager())
    handler = next(r.endpoint for r in router.routes
                   if getattr(r, "path", "").endswith("/compact"))
    result = asyncio.run(handler(MagicMock(), "sess-1"))
    print("RESULT:" + str(result.get("status")))
    '''
)


def _run_child(shape, db_url):
    env = dict(os.environ)
    env["PYTHONPATH"] = _REPO_ROOT + os.pathsep + env.get("PYTHONPATH", "")
    # core.database creates its engine and runs init_db() at import time against
    # DATABASE_URL (default sqlite:///./data/app.db). Point the child at an
    # isolated temp DB so importing the route module never touches the real app
    # database.
    env["DATABASE_URL"] = db_url
    return subprocess.run(
        [sys.executable, "-c", _CHILD, shape],
        capture_output=True, text=True, timeout=60,
        cwd=_REPO_ROOT, env=env,
    )


def _assert_compact_ok(shape, tmp_path):
    db_url = "sqlite:///" + str(tmp_path / "compact_test.db")
    proc = _run_child(shape, db_url)
    if proc.returncode == _SKIP_CODE:
        pytest.skip("real app graph not importable: " + proc.stderr.strip())
    # On buggy code the handler re-raises the TypeError as HTTPException(500),
    # so the child exits non-zero and never prints RESULT:ok.
    assert proc.returncode == 0, (
        f"compact handler crashed on {shape} None content:\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr}"
    )
    assert "RESULT:ok" in proc.stdout, (
        f"unexpected handler result for {shape}:\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr}"
    )


def test_compact_does_not_crash_on_chatmessage_none_content(tmp_path):
    _assert_compact_ok("chatmessage", tmp_path)


def test_compact_does_not_crash_on_dict_none_content(tmp_path):
    _assert_compact_ok("dict", tmp_path)
