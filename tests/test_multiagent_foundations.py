# tests/test_multiagent_foundations.py
"""Multiagent slice-1 Task 2: multi-owner memory read, agent: login guard,
settings defaults."""
import json

import pytest


# ----------------------------------------------------------- memory read ----


def _mk_memory(tmp_path, entries):
    from src.memory import MemoryManager
    mf = tmp_path / "memory.json"
    mf.write_text(json.dumps(entries))
    m = MemoryManager.__new__(MemoryManager)
    m.memory_file = str(mf)
    return m


ENTRIES = [
    {"text": "human fact", "owner": "oleg"},
    {"text": "shared fact"},                                  # NULL owner
    {"text": "agent fact", "owner": "agent:oleg/researcher"},
    {"text": "other human", "owner": "bob"},
    {"text": "other agent", "owner": "agent:bob/researcher"},
]


def test_load_multi_reads_own_human_and_shared(tmp_path):
    m = _mk_memory(tmp_path, ENTRIES)
    got = {e["text"] for e in
           m.load_multi(["agent:oleg/researcher", "oleg"])}
    assert got == {"human fact", "shared fact", "agent fact"}


def test_load_multi_never_leaks_other_humans(tmp_path):
    m = _mk_memory(tmp_path, ENTRIES)
    got = {e["text"] for e in m.load_multi(["agent:oleg/researcher", "oleg"])}
    assert "other human" not in got and "other agent" not in got


def test_load_single_owner_behavior_unchanged(tmp_path):
    m = _mk_memory(tmp_path, ENTRIES)
    assert [e["text"] for e in m.load(owner="oleg")] == ["human fact"]


# ------------------------------------------------------------ login guard ----


def test_login_rejects_agent_prefixed_username():
    import inspect
    import routes.auth_routes as ar
    src = inspect.getsource(ar)
    # Guard must run BEFORE password verification (no oracle for agent ids).
    assert 'startswith("agent:")' in src


def test_login_agent_id_401():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes.auth_routes import setup_auth_routes

    class _AuthMgr:
        is_configured = True
        signup_enabled = True

        def verify_password(self, u, p):  # would "succeed" — guard must fire first
            return True

        def totp_enabled(self, u):
            return False

        def create_session_trusted(self, u):
            return "tok"

        def status(self, token):
            return {}

    app = FastAPI()
    app.include_router(setup_auth_routes(_AuthMgr()))
    client = TestClient(app)
    r = client.post("/api/auth/login", json={
        "username": "agent:oleg/researcher", "password": "irrelevant"})
    assert r.status_code == 401
    r2 = client.post("/api/auth/signup", json={
        "username": "agent:evil/x", "password": "longenough"})
    assert r2.status_code == 400


# --------------------------------------------------------------- settings ----


def test_settings_defaults_present():
    from src.settings import DEFAULT_SETTINGS
    assert DEFAULT_SETTINGS["agent_max_depth"] == 2
    assert DEFAULT_SETTINGS["agent_max_parallel"] == 2
