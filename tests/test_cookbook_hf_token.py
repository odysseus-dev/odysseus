"""Cookbook HF token persistence and lookup, and ollama download detection."""

import json
import os

import pytest

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


# ---------------------------------------------------------------------------
# Ollama download detection heuristic
# ---------------------------------------------------------------------------

def _is_ollama(repo_id: str, backend: str | None = None) -> bool:
    """Mirror the detection logic from cookbook_routes.setup_cookbook_routes."""
    be = (backend or "").strip().lower()
    return be == "ollama" or ("/" not in repo_id and ":" in repo_id)


def test_ollama_colon_tag_format_detected_as_ollama():
    # Ollama model IDs: name:tag, no slash
    assert _is_ollama("llama3:latest") is True
    assert _is_ollama("mistral:7b") is True
    assert _is_ollama("qwen2:0.5b") is True


def test_explicit_ollama_backend_overrides_slash_check():
    # Even a HF-style ID should be treated as ollama when backend is set
    assert _is_ollama("meta-llama/Llama-3.2-1B", backend="ollama") is True


def test_hf_repo_id_not_detected_as_ollama():
    # HF model IDs always contain a slash
    assert _is_ollama("meta-llama/Llama-3.2-1B") is False
    assert _is_ollama("org/some-model-GGUF") is False


def test_hf_id_with_colon_but_slash_not_ollama():
    # Edge case: ID with both slash and colon (invalid but shouldn't falsely
    # classify as ollama — the slash is the discriminator).
    assert _is_ollama("org/model:quant") is False


def test_ollama_id_without_tag_not_detected():
    # A bare name with no colon is ambiguous but NOT classified as ollama
    # (missing the ":" requirement). The user must specify backend="ollama".
    assert _is_ollama("llama3") is False
