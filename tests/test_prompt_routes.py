"""Saved prompts API — CRUD and owner scoping."""

import tempfile
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from tests.helpers.import_state import clear_fake_database_modules

clear_fake_database_modules()

import core.database as cdb
import routes.prompt_routes as proutes
from core.database import SavedPrompt
from routes.prompt_routes import PromptCreate, PromptUpdate

_TMPDB = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_ENGINE = create_engine(
    f"sqlite:///{_TMPDB.name}",
    connect_args={"check_same_thread": False},
    poolclass=NullPool,
)
cdb.Base.metadata.create_all(_ENGINE)
_TS = sessionmaker(bind=_ENGINE, autoflush=False, autocommit=False)


def _req(user="alice"):
    return SimpleNamespace(state=SimpleNamespace(current_user=user))


def _endpoint(method, path):
    router = proutes.setup_prompt_routes()
    for route in router.routes:
        if getattr(route, "path", None) == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise RuntimeError(f"{method} {path} not found")


def _bind_test_db():
    previous = proutes.SessionLocal
    proutes.SessionLocal = _TS
    return previous


def _seed():
    alice_id = str(uuid.uuid4())
    bob_id = str(uuid.uuid4())
    db = _TS()
    try:
        db.add(SavedPrompt(
            id=alice_id,
            owner="alice",
            title="Alice prompt",
            body="Hello from alice",
        ))
        db.add(SavedPrompt(
            id=bob_id,
            owner="bob",
            title="Bob prompt",
            body="Hello from bob",
        ))
        db.commit()
        return alice_id, bob_id
    finally:
        db.close()


def test_list_prompts_scoped_to_owner():
    previous = _bind_test_db()
    try:
        list_prompts = _endpoint("GET", "/api/prompts")
        alice_id, bob_id = _seed()

        alice = list_prompts(_req("alice"))
        assert len(alice["prompts"]) == 1
        assert alice["prompts"][0]["id"] == alice_id

        bob = list_prompts(_req("bob"))
        assert len(bob["prompts"]) == 1
        assert bob["prompts"][0]["id"] == bob_id
    finally:
        proutes.SessionLocal = previous


def test_create_and_update_prompt():
    previous = _bind_test_db()
    try:
        create_prompt = _endpoint("POST", "/api/prompts")
        update_prompt = _endpoint("PATCH", "/api/prompts/{prompt_id}")

        created = create_prompt(_req("alice"), PromptCreate(title="My prompt", body="Do the thing"))
        assert created["title"] == "My prompt"
        assert created["body"] == "Do the thing"
        assert created["owner"] == "alice"

        updated = update_prompt(_req("alice"), created["id"], PromptUpdate(body="Updated body"))
        assert updated["body"] == "Updated body"
        assert updated["title"] == "My prompt"
    finally:
        proutes.SessionLocal = previous


def test_cross_owner_cannot_read_or_mutate():
    previous = _bind_test_db()
    try:
        update_prompt = _endpoint("PATCH", "/api/prompts/{prompt_id}")
        delete_prompt = _endpoint("DELETE", "/api/prompts/{prompt_id}")
        alice_id, _bob_id = _seed()

        with pytest.raises(HTTPException) as exc:
            update_prompt(_req("bob"), alice_id, PromptUpdate(title="Hacked"))
        assert exc.value.status_code == 404

        with pytest.raises(HTTPException) as exc:
            delete_prompt(_req("bob"), alice_id)
        assert exc.value.status_code == 404

        db = _TS()
        try:
            row = db.query(SavedPrompt).filter(SavedPrompt.id == alice_id).first()
            assert row.title == "Alice prompt"
        finally:
            db.close()
    finally:
        proutes.SessionLocal = previous


def test_delete_prompt():
    previous = _bind_test_db()
    try:
        delete_prompt = _endpoint("DELETE", "/api/prompts/{prompt_id}")
        alice_id, _bob_id = _seed()

        result = delete_prompt(_req("alice"), alice_id)
        assert result["ok"] is True

        db = _TS()
        try:
            assert db.query(SavedPrompt).filter(SavedPrompt.id == alice_id).first() is None
        finally:
            db.close()
    finally:
        proutes.SessionLocal = previous
