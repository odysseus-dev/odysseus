#!/usr/bin/env python
"""
Integration smoke test for src/ai_interaction.dispatch_ai_tool.

Drives the REAL dispatcher -> real do_* handlers -> real SessionManager,
MemoryManager and a throwaway SQLite database. Only the I/O boundary is
mocked: the LLM call (llm_call_async), model resolution (_resolve_model) and
endpoint model-listing (_fetch_endpoint_model_ids) — there is no live LLM
endpoint in this environment.

Run:  python scripts/smoke_ai_interaction.py
Exits non-zero if any step fails.

NOT a pytest test on purpose: tests/conftest.py stubs core.database/src.database
with MagicMocks, which would defeat the point of exercising the real stack.
"""
import asyncio
import os
import sys
import tempfile

# ── Point the DB at a throwaway file BEFORE importing core.database ──────────
_TMP = tempfile.mkdtemp(prefix="odysseus_smoke_")
os.environ["DATABASE_URL"] = f"sqlite:///{os.path.join(_TMP, 'smoke.db')}"

# Make the repo root importable when run from anywhere
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.database import init_db, SessionLocal, ModelEndpoint  # noqa: E402
from core.session_manager import SessionManager  # noqa: E402
from core.models import set_session_manager as set_core_sm  # noqa: E402
from src.memory import MemoryManager  # noqa: E402
import src.ai_interaction as ai  # noqa: E402


# ── Counters / assertion helper ─────────────────────────────────────────────
_passed = 0
_failed = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"  PASS  {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}   {detail}")


# ── Environment setup ───────────────────────────────────────────────────────
def setup():
    init_db()  # create schema in the throwaway DB

    # Seed one enabled endpoint so DB-backed tools have something to find
    db = SessionLocal()
    try:
        db.add(ModelEndpoint(
            id="smoke-ep",
            name="Smoke Endpoint",
            base_url="http://fake-host/v1",
            api_key=None,
            is_enabled=True,
            model_type="llm",
            owner="smoke-user",
        ))
        db.commit()
    finally:
        db.close()

    # Real managers backed by the temp dir
    memory_manager = MemoryManager(_TMP)
    session_manager = SessionManager(os.path.join(_TMP, "sessions.json"))
    set_core_sm(session_manager)  # enable Session.add_message persistence

    # Wire the real managers into ai_interaction (same calls app.py makes)
    ai.set_session_manager(session_manager)
    ai.set_memory_manager(memory_manager, None)
    ai.set_rag_manager(None, None)

    # Mock ONLY the I/O boundary
    async def _fake_llm(*args, **kwargs):
        return "FAKE-LLM-RESPONSE"

    ai.llm_call_async = _fake_llm
    ai._resolve_model = lambda spec: ("http://fake-host/v1/chat/completions", "fake-model", {})
    ai._fetch_endpoint_model_ids = lambda base, headers, provider, anth: ["fake-model"]

    return session_manager, memory_manager


# ── The smoke scenarios ─────────────────────────────────────────────────────
async def run(session_manager, memory_manager):
    OWNER = "smoke-user"

    async def dispatch(tool, content):
        return await ai.dispatch_ai_tool(tool, content, session_id=None, owner=OWNER)

    # 1) ui_control: toggle (sync handler through await-agnostic dispatcher)
    desc, res = await dispatch("ui_control", "toggle shell on")
    check("ui_control toggle: alias shell->bash", res.get("toggle_name") == "bash", str(res))
    check("ui_control toggle: state on", res.get("state") is True, str(res))
    check("ui_control desc derived", desc.startswith("ui_control"), desc)

    # 2) ui_control: set_theme
    _, res = await dispatch("ui_control", "set_theme dark")
    check("ui_control set_theme dark", res.get("theme_name") == "dark", str(res))
    _, res = await dispatch("ui_control", "set_theme not_a_real_theme")
    check("ui_control unknown theme -> error", "error" in res, str(res))

    # 3) manage_memory: add -> list -> search (real MemoryManager + temp files)
    _, res = await dispatch("manage_memory", "add\nUser prefers dark mode and Python\npreference")
    check("manage_memory add returns id", bool(res.get("memory_id")), str(res))
    _, res = await dispatch("manage_memory", "list")
    check("manage_memory list shows entry", "dark mode" in res.get("results", ""), str(res))
    _, res = await dispatch("manage_memory", "search\nPython")
    check("manage_memory search finds entry", "Python" in res.get("results", ""), str(res))

    # 4) create_session -> list_sessions -> manage_session rename -> delete
    _, res = await dispatch("create_session", "Smoke Chat\nfake-model")
    sid = res.get("session_id")
    check("create_session returns id", bool(sid), str(res))
    check("create_session persisted to manager", session_manager.get_session(sid) is not None)

    _, res = await dispatch("list_sessions", "")
    check("list_sessions shows new session", sid in res.get("results", ""), str(res)[:200])

    _, res = await dispatch("manage_session", f"rename\n{sid}\nRenamed Smoke Chat")
    check("manage_session rename ok", res.get("action") == "rename", str(res))
    check("rename reflected in manager",
          session_manager.get_session(sid).name == "Renamed Smoke Chat")

    _, res = await dispatch("manage_session", "list")
    check("manage_session list delegates", "Renamed Smoke Chat" in res.get("results", ""), str(res)[:200])

    _, res = await dispatch("manage_session", f"delete\n{sid}")
    check("manage_session delete ok", res.get("action") == "delete", str(res))
    # get_session raises KeyError once a session is gone — absence from the
    # user's session map is the reliable "deleted" signal.
    check("session actually deleted", sid not in session_manager.get_sessions_for_user(OWNER))

    # 5) list_models (mocked endpoint listing, real formatting)
    _, res = await dispatch("list_models", "")
    check("list_models lists fake-model", "fake-model" in res.get("results", ""), str(res)[:200])

    # 6) chat_with_model (mocked llm, real truncation/result shaping)
    _, res = await dispatch("chat_with_model", "fake-model\nHello there")
    check("chat_with_model returns response", res.get("response") == "FAKE-LLM-RESPONSE", str(res))

    # 7) pipeline (mocked llm, real step chaining)
    _, res = await dispatch("pipeline", "fake-model | draft\nfake-model | refine")
    check("pipeline runs 2 steps", len(res.get("steps", [])) == 2, str(res)[:200])

    # 8) unknown tool -> graceful error
    _, res = await dispatch("does_not_exist", "")
    check("unknown tool -> error dict", "error" in res, str(res))


def main():
    print(f"Smoke DB: {os.environ['DATABASE_URL']}")
    sm, mm = setup()
    asyncio.run(run(sm, mm))
    print(f"\nSmoke result: {_passed} passed, {_failed} failed")
    sys.exit(1 if _failed else 0)


if __name__ == "__main__":
    main()
