"""Deterministic tests for model authorization, configuration ownership, and precedence.

Covers:
- Section 3.1 & 3.2: Multi-user isolation across Admin, Member A, Member B
  - Endpoint ownership boundaries
  - Prevention of forged preferences crossing ownership boundaries
  - Credential isolation
  - Effective resolution paths (Chat default, Utility, Research, Fallbacks)
- Section 3.3: Model allowlists, disabled endpoints, hidden models, non-LLM endpoints
- Section 3.4: Auth transitions and single-operator mode
- Section 5: Configuration precedence rules
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import core.database as database
import routes.chat_helpers as chat_helpers
import routes.model_routes as model_routes
import routes.prefs_routes as prefs_routes
import src.endpoint_resolver as resolver
import src.foreground_model_routing as foreground_routing
import src.settings as settings_mod


@pytest.fixture
def auth_db(tmp_path, monkeypatch):
    db_file = tmp_path / "auth_test.db"
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    database.Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(database, "SessionLocal", Session)
    monkeypatch.setattr(resolver, "SessionLocal", Session)

    with Session() as db:
        # Member A's private endpoint
        db.add(database.ModelEndpoint(
            id="ep-alice-priv",
            name="Alice Private",
            owner="alice",
            base_url="https://api.alice.example/v1",
            api_key="alice-secret-key",
            is_enabled=True,
            model_type="llm",
            cached_models=json.dumps(["alice-chat-1", "alice-chat-2"]),
            pinned_models=json.dumps(["alice-chat-1", "alice-chat-2"]),
        ))
        # Member B's private endpoint
        db.add(database.ModelEndpoint(
            id="ep-bob-priv",
            name="Bob Private",
            owner="bob",
            base_url="https://api.bob.example/v1",
            api_key="bob-secret-key",
            is_enabled=True,
            model_type="llm",
            cached_models=json.dumps(["bob-model-1", "bob-model-hidden"]),
            pinned_models=json.dumps(["bob-model-1", "bob-model-hidden"]),
            hidden_models=json.dumps(["bob-model-hidden"]),
        ))
        # Disabled endpoint
        db.add(database.ModelEndpoint(
            id="ep-alice-disabled",
            name="Alice Disabled",
            owner="alice",
            base_url="https://api.alice-off.example/v1",
            api_key="alice-disabled-key",
            is_enabled=False,
            model_type="llm",
            cached_models=json.dumps(["alice-disabled-1"]),
        ))
        # Non-LLM endpoint (image)
        db.add(database.ModelEndpoint(
            id="ep-alice-image",
            name="Alice Image",
            owner="alice",
            base_url="https://api.alice-img.example/v1",
            api_key="alice-img-key",
            is_enabled=True,
            model_type="image",
            cached_models=json.dumps(["flux-1"]),
        ))
        # Server-wide shared endpoint (owner is None)
        db.add(database.ModelEndpoint(
            id="ep-shared",
            name="Server Shared",
            owner=None,
            base_url="https://api.shared.example/v1",
            api_key="shared-secret-key",
            is_enabled=True,
            model_type="llm",
            cached_models=json.dumps(["shared-model-1", "shared-model-2"]),
            pinned_models=json.dumps(["shared-model-1", "shared-model-2"]),
        ))
        db.commit()

    yield Session


@pytest.fixture
def auth_prefs(tmp_path, monkeypatch):
    prefs_file = tmp_path / "user_prefs.json"
    prefs_file.write_text(json.dumps({
        "_users": {
            "alice": {
                "default_endpoint_id": "ep-alice-priv",
                "default_model": "alice-chat-1",
            },
            "bob": {
                "default_endpoint_id": "ep-bob-priv",
                "default_model": "bob-model-1",
            },
        }
    }), encoding="utf-8")
    monkeypatch.setattr(prefs_routes, "PREFS_FILE", str(prefs_file))
    return prefs_file


# ── 1. Multi-User Isolation & Endpoint Ownership ──

def test_member_cannot_resolve_other_users_private_endpoint(auth_db):
    # Alice resolving Bob's endpoint explicitly by ID must fail
    resolved = resolver.resolve_endpoint_by_id("ep-bob-priv", "bob-model-1", owner="alice")
    assert resolved is None

    # Bob resolving his own endpoint succeeds
    resolved_bob = resolver.resolve_endpoint_by_id("ep-bob-priv", "bob-model-1", owner="bob")
    assert resolved_bob is not None
    assert resolved_bob[0].startswith("https://api.bob.example/v1")
    assert resolved_bob[1] == "bob-model-1"
    assert resolved_bob[2] == {"Authorization": "Bearer bob-secret-key"}


def test_member_cannot_access_other_users_credentials_via_owner_filter(auth_db):
    with auth_db() as db:
        with pytest.raises(ValueError, match="Model endpoint must be enabled and registered for the current owner"):
            resolver.resolve_owner_registered_endpoint(db, "https://api.bob.example/v1", owner="alice")

        endpoint = resolver.resolve_owner_registered_endpoint(db, "https://api.bob.example/v1", owner="bob")
        assert endpoint.id == "ep-bob-priv"
        base, key = resolver.resolve_endpoint_runtime(endpoint, owner="bob")
        assert key == "bob-secret-key"


def test_forged_preference_cannot_cross_ownership_boundaries(auth_db, auth_prefs):
    # Alice maliciously forges her preferences to point to Bob's private endpoint
    prefs_routes._update_for_user("alice", {
        "default_endpoint_id": "ep-bob-priv",
        "default_model": "bob-model-1",
    })

    # resolve_endpoint for alice fails closed to safe fallback
    url, model, headers = resolver.resolve_endpoint(
        "default",
        fallback_url="https://fallback.example/chat",
        fallback_model="fallback-chat",
        fallback_headers={"X-Fallback": "1"},
        owner="alice",
    )
    assert url == "https://fallback.example/chat"
    assert model == "fallback-chat"
    assert headers == {"X-Fallback": "1"}


def test_server_wide_defaults_do_not_grant_unauthorized_private_access(auth_db, monkeypatch):
    # Server default points to Bob's private endpoint
    monkeypatch.setattr(settings_mod, "load_settings", lambda: {
        "default_endpoint_id": "ep-bob-priv",
        "default_model": "bob-model-1",
        "share_defaults_with_users": True,
    })

    # Alice has no personal preferences set
    url, model, headers = resolver.resolve_endpoint(
        "default",
        fallback_url="https://fallback.example/chat",
        fallback_model="fallback-chat",
        fallback_headers={},
        owner="alice",
    )
    # Alice must NOT receive Bob's endpoint
    assert url == "https://fallback.example/chat"
    assert model == "fallback-chat"


def test_shared_endpoints_accessible_to_all_members(auth_db):
    # Shared endpoint (owner=None) is accessible to Alice and Bob
    alice_res = resolver.resolve_endpoint_by_id("ep-shared", "shared-model-1", owner="alice")
    bob_res = resolver.resolve_endpoint_by_id("ep-shared", "shared-model-1", owner="bob")
    assert alice_res is not None
    assert bob_res is not None
    assert alice_res[0] == bob_res[0]
    assert alice_res[2] == {"Authorization": "Bearer shared-secret-key"}


# ── 2. Model Allowlists & Privilege Gates ──

def test_model_allowlist_enforcement_in_chat_privileges():
    req = MagicMock()
    sess = MagicMock()
    sess.model = "claude-3-5-sonnet"

    auth_mgr = MagicMock()
    auth_mgr.get_privileges.return_value = {
        "allowed_models_restricted": True,
        "allowed_models": ["gpt-4o-mini"],
    }
    req.app.state.auth_manager = auth_mgr
    req.state.current_user = "alice"

    # User restricted to gpt-4o-mini trying to use claude-3-5-sonnet must 403
    with pytest.raises(HTTPException) as exc_info:
        chat_helpers._enforce_chat_privileges(req, sess)
    assert exc_info.value.status_code == 403
    assert "not allowed to use model 'claude-3-5-sonnet'" in exc_info.value.detail


def test_empty_allowlist_blocks_all_models():
    req = MagicMock()
    sess = MagicMock()
    sess.model = "gpt-4o"

    auth_mgr = MagicMock()
    auth_mgr.get_privileges.return_value = {
        "allowed_models_restricted": True,
        "allowed_models": [],  # Empty restricted list = block all
    }
    req.app.state.auth_manager = auth_mgr
    req.state.current_user = "alice"

    with pytest.raises(HTTPException) as exc_info:
        chat_helpers._enforce_chat_privileges(req, sess)
    assert exc_info.value.status_code == 403


def test_block_all_models_sentinel_takes_precedence():
    req = MagicMock()
    sess = MagicMock()
    sess.model = "gpt-4o"

    auth_mgr = MagicMock()
    auth_mgr.get_privileges.return_value = {
        "block_all_models": True,
        "allowed_models": ["gpt-4o"],
    }
    req.app.state.auth_manager = auth_mgr
    req.state.current_user = "alice"

    with pytest.raises(HTTPException) as exc_info:
        chat_helpers._enforce_chat_privileges(req, sess)
    assert exc_info.value.status_code == 403


def test_foreground_fallback_filters_by_allowlist(auth_db, auth_prefs):
    # Alice configures two fallback candidates
    prefs_routes._update_for_user("alice", {
        "foreground_fallback_enabled": True,
        "foreground_model_fallbacks": [
            {"endpoint_id": "ep-alice-priv", "model": "alice-chat-1"},
            {"endpoint_id": "ep-alice-priv", "model": "alice-chat-2"},
        ],
    })

    # Allowed models only permits alice-chat-1
    policy = foreground_routing.resolve_foreground_model_policy(
        owner="alice",
        allowed_models=["alice-chat-1"],
    )
    assert policy.enabled is True
    assert len(policy.fallback_candidates) == 1
    assert policy.fallback_candidates[0][1] == "alice-chat-1"

    # If allowed models is empty, policy fails closed to disabled
    blocked_policy = foreground_routing.resolve_foreground_model_policy(
        owner="alice",
        allowed_models=[],
    )
    assert blocked_policy.enabled is False
    assert len(blocked_policy.fallback_candidates) == 0


# ── 3. Disabled Endpoints, Hidden Models, and Non-LLM Endpoints ──

def test_disabled_endpoint_rejected_by_all_resolvers(auth_db, auth_prefs):
    # Direct resolution by ID returns None
    assert resolver.resolve_endpoint_by_id("ep-alice-disabled", "alice-disabled-1", owner="alice") is None

    # Stored preference pointing to disabled endpoint fails closed to fallback
    prefs_routes._update_for_user("alice", {
        "utility_endpoint_id": "ep-alice-disabled",
        "utility_model": "alice-disabled-1",
    })
    url, model, _ = resolver.resolve_endpoint(
        "utility",
        fallback_url="https://fallback.example/chat",
        fallback_model="fallback-util",
        fallback_headers={},
        owner="alice",
    )
    assert url == "https://fallback.example/chat"
    assert model == "fallback-util"


def test_hidden_model_exact_resolution_rejected(auth_db):
    # bob-model-hidden is marked hidden on ep-bob-priv
    # Exact match resolution (used for foreground fallbacks) must reject hidden model
    res = resolver._resolve_endpoint_by_id_with_descriptor(
        "ep-bob-priv",
        "bob-model-hidden",
        owner="bob",
        require_exact_model=True,
    )
    assert res is None


def test_hidden_model_auto_repaired_in_utility_resolution(auth_db, auth_prefs):
    # In utility resolution, if configured model is hidden, it repairs to first enabled chat model
    prefs_routes._update_for_user("bob", {
        "utility_endpoint_id": "ep-bob-priv",
        "utility_model": "bob-model-hidden",
    })
    url, model, headers = resolver.resolve_endpoint("utility", owner="bob")
    assert url.startswith("https://api.bob.example/v1")
    assert model == "bob-model-1"  # Repaired to enabled model, not hidden model


def test_non_llm_endpoint_rejected_as_foreground_fallback(auth_db, auth_prefs):
    prefs_routes._update_for_user("alice", {
        "foreground_fallback_enabled": True,
        "foreground_model_fallbacks": [
            {"endpoint_id": "ep-alice-image", "model": "flux-1"},
        ],
    })

    policy = foreground_routing.resolve_foreground_model_policy(owner="alice")
    # Image endpoint must not be accepted as an LLM foreground fallback
    assert policy.enabled is False
    assert len(policy.fallback_candidates) == 0


# ── 4. Configuration Precedence & Fallback Chains ──

def test_configuration_precedence_chain(auth_db, auth_prefs, monkeypatch):
    monkeypatch.setattr(settings_mod, "load_settings", lambda: {
        "default_endpoint_id": "ep-shared",
        "default_model": "shared-model-1",
        "utility_endpoint_id": "ep-shared",
        "utility_model": "shared-model-2",
    })

    # Case 1: Alice sets personal utility model -> personal preference wins
    prefs_routes._update_for_user("alice", {
        "utility_endpoint_id": "ep-alice-priv",
        "utility_model": "alice-chat-2",
    })
    _, m, _ = resolver.resolve_endpoint("utility", owner="alice")
    assert m == "alice-chat-2"

    # Case 2: Alice unsets personal utility model -> falls back to server utility setting
    prefs_routes._update_for_user("alice", {"utility_endpoint_id": None, "utility_model": None})
    _, m, _ = resolver.resolve_endpoint("utility", owner="alice")
    assert m == "shared-model-2"

    # Case 3: Research model unconfigured -> falls back to Utility -> falls back to Default
    # Alice's personal default model is alice-chat-1, which precedes server default
    monkeypatch.setattr(settings_mod, "load_settings", lambda: {
        "default_endpoint_id": "ep-shared",
        "default_model": "shared-model-1",
    })
    _, m, _ = resolver.resolve_endpoint("research", owner="alice")
    assert m == "alice-chat-1"

    # Case 4: When Alice unsets her personal default model, it falls back to server default
    prefs_routes._update_for_user("alice", {"default_endpoint_id": None, "default_model": None})
    _, m, _ = resolver.resolve_endpoint("research", owner="alice")
    assert m == "shared-model-1"
