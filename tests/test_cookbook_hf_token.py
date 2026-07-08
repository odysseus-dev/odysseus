"""Cookbook HF token persistence and lookup."""

import json
import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.middleware import INTERNAL_TOOL_HEADER, INTERNAL_TOOL_TOKEN
from routes import cookbook_routes
from routes.cookbook_helpers import load_stored_hf_token
from src.secret_storage import encrypt


def test_load_stored_hf_token_reads_encrypted_state(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    state_path = tmp_path / "cookbook_state.json"
    state_path.write_text(
        json.dumps({"env": {"hfToken": encrypt("hf_test_token_12345")}}),
        encoding="utf-8",
    )
    assert load_stored_hf_token() == "hf_test_token_12345"
    assert load_stored_hf_token(state_path=state_path) == "hf_test_token_12345"


def test_load_stored_hf_token_falls_back_to_env_when_state_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HF_TOKEN", "hf_from_env")
    assert load_stored_hf_token() == "hf_from_env"


def test_load_stored_hf_token_prefers_state_over_env(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HF_TOKEN", "hf_from_env")
    state_path = tmp_path / "cookbook_state.json"
    state_path.write_text(
        json.dumps({"env": {"hfToken": encrypt("hf_from_state")}}),
        encoding="utf-8",
    )
    assert load_stored_hf_token() == "hf_from_state"


def test_cookbook_state_post_persists_new_hf_token(tmp_path, monkeypatch):
    state_path = tmp_path / "cookbook_state.json"
    monkeypatch.setattr(cookbook_routes, "COOKBOOK_STATE_FILE", str(state_path))

    app = FastAPI()
    app.include_router(cookbook_routes.setup_cookbook_routes())
    client = TestClient(app)

    response = client.post(
        "/api/cookbook/state",
        headers={INTERNAL_TOOL_HEADER: INTERNAL_TOOL_TOKEN},
        json={"env": {"hfToken": "hf_new_token_12345", "servers": [{"host": ""}]}},
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    raw = json.loads(state_path.read_text(encoding="utf-8"))
    assert raw["env"]["hfToken"] != "hf_new_token_12345"
    assert load_stored_hf_token(state_path=state_path) == "hf_new_token_12345"

    response = client.get(
        "/api/cookbook/state",
        headers={INTERNAL_TOOL_HEADER: INTERNAL_TOOL_TOKEN},
    )
    env = response.json()["env"]
    assert "hfToken" not in env
    assert env["hfTokenConfigured"] is True
