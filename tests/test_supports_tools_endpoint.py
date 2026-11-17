"""Integration tests for the supports_tools endpoint toggle and validation."""

import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.helpers.import_state import clear_fake_database_modules, clear_fake_endpoint_resolver_modules
from tests.helpers.sqlite_db import make_temp_sqlite

# Evict any bare/fake stubs left by previous test modules before importing real modules
clear_fake_database_modules()
clear_fake_endpoint_resolver_modules()

import routes.model_routes as mr
from core.database import Base, ModelEndpoint
from routes.model_routes import _coerce_supports_tools


# ── Static JS Syntax Validation ─────────────────────────────────────────────────


def test_admin_js_syntax():
    """Ensure static/js/admin.js passes node syntax checking."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node binary not on PATH")
    repo_root = Path(__file__).resolve().parent.parent
    admin_js = repo_root / "static" / "js" / "admin.js"
    proc = subprocess.run(
        [node, "--check", str(admin_js)],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"admin.js syntax error:\n{proc.stderr}"


# ── Coercion Unit Tests ─────────────────────────────────────────────────────────


class TestCoerceSupportsTools:
    """Validate _coerce_supports_tools() normalization and strict rejection."""

    @pytest.mark.parametrize(
        "value",
        [True, 1, "true", "True", "TRUE", "yes", "YES", "on", "ON", "1"],
    )
    def test_truthy_values_map_to_true(self, value):
        assert _coerce_supports_tools(value) is True

    @pytest.mark.parametrize(
        "value",
        [False, 0, "false", "False", "FALSE", "no", "NO", "off", "OFF", "0"],
    )
    def test_falsy_values_map_to_false(self, value):
        assert _coerce_supports_tools(value) is False

    @pytest.mark.parametrize(
        "value",
        [None, "null", "NULL", "auto", "AUTO", "none", "NONE", ""],
    )
    def test_auto_values_map_to_none(self, value):
        assert _coerce_supports_tools(value) is None

    @pytest.mark.parametrize(
        "value",
        [2, -1, 100, "bogus", "enabled", "disabled", [], [True], {}, {"a": 1}],
    )
    def test_malformed_values_raise_value_error(self, value):
        with pytest.raises(ValueError):
            _coerce_supports_tools(value)


# ── Route Integration Fixtures ──────────────────────────────────────────────────


@pytest.fixture
def test_db(monkeypatch):
    """File-backed SQLite database isolated per test using make_temp_sqlite."""
    TestingSessionLocal, engine, tmpfile = make_temp_sqlite(Base.metadata)

    monkeypatch.setattr(mr, "SessionLocal", TestingSessionLocal)
    monkeypatch.setattr("core.database.SessionLocal", TestingSessionLocal)

    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()
        try:
            os.unlink(tmpfile.name)
        except OSError:
            pass


@pytest.fixture
def client(monkeypatch):
    """TestClient with auth disabled for endpoint mutation checks."""
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setattr(mr, "require_admin", lambda req: None)

    app = FastAPI()
    router = mr.setup_model_routes(model_discovery=None)
    app.include_router(router)
    app.state.auth_enabled = False
    return TestClient(app, raise_server_exceptions=True)


# ── PATCH /api/model-endpoints/{ep_id} Integration Tests ───────────────────────


class TestModelEndpointSupportsToolsRoute:
    """Route tests for PATCH /api/model-endpoints/{id} supports_tools handling."""

    def test_patch_tristate_cycle_and_persistence(self, client, test_db):
        """Auto (None) -> On (True) -> Off (False) -> Auto (None) persistence."""
        ep_id = str(uuid.uuid4())[:8]
        ep = ModelEndpoint(
            id=ep_id,
            name="Local Qwen",
            base_url="http://127.0.0.1:11434/v1",
            is_enabled=True,
            model_type="llm",
            supports_tools=None,
        )
        test_db.add(ep)
        test_db.commit()

        # Step 1: Cycle to On (True)
        res1 = client.patch(
            f"/api/model-endpoints/{ep_id}",
            json={"supports_tools": True},
        )
        assert res1.status_code == 200, res1.text
        assert res1.json()["supports_tools"] is True

        test_db.expire_all()
        refreshed = test_db.query(ModelEndpoint).filter(ModelEndpoint.id == ep_id).first()
        assert refreshed.supports_tools is True

        # Step 2: Cycle to Off (False)
        res2 = client.patch(
            f"/api/model-endpoints/{ep_id}",
            json={"supports_tools": False},
        )
        assert res2.status_code == 200, res2.text
        assert res2.json()["supports_tools"] is False

        test_db.expire_all()
        refreshed = test_db.query(ModelEndpoint).filter(ModelEndpoint.id == ep_id).first()
        assert refreshed.supports_tools is False

        # Step 3: Cycle back to Auto (None / null)
        res3 = client.patch(
            f"/api/model-endpoints/{ep_id}",
            json={"supports_tools": None},
        )
        assert res3.status_code == 200, res3.text
        assert res3.json()["supports_tools"] is None

        test_db.expire_all()
        refreshed = test_db.query(ModelEndpoint).filter(ModelEndpoint.id == ep_id).first()
        assert refreshed.supports_tools is None

    def test_patch_accepts_normalized_strings(self, client, test_db):
        """PATCH accepts string aliases 'on', 'off', 'auto'."""
        ep_id = str(uuid.uuid4())[:8]
        ep = ModelEndpoint(
            id=ep_id,
            name="Ollama Server",
            base_url="http://127.0.0.1:11434/v1",
            is_enabled=True,
            model_type="llm",
            supports_tools=None,
        )
        test_db.add(ep)
        test_db.commit()

        # 'on' -> True
        r = client.patch(f"/api/model-endpoints/{ep_id}", json={"supports_tools": "on"})
        assert r.status_code == 200
        assert r.json()["supports_tools"] is True

        # 'off' -> False
        r = client.patch(f"/api/model-endpoints/{ep_id}", json={"supports_tools": "off"})
        assert r.status_code == 200
        assert r.json()["supports_tools"] is False

        # 'auto' -> None
        r = client.patch(f"/api/model-endpoints/{ep_id}", json={"supports_tools": "auto"})
        assert r.status_code == 200
        assert r.json()["supports_tools"] is None

    def test_patch_empty_body_toggles_is_enabled(self, client, test_db):
        """Sending an empty body preserves legacy behavior and toggles is_enabled."""
        ep_id = str(uuid.uuid4())[:8]
        ep = ModelEndpoint(
            id=ep_id,
            name="Toggle Enabled",
            base_url="http://127.0.0.1:11434/v1",
            is_enabled=True,
            model_type="llm",
            supports_tools=None,
        )
        test_db.add(ep)
        test_db.commit()

        # Empty body toggles True -> False
        r = client.patch(f"/api/model-endpoints/{ep_id}", content=b"")
        assert r.status_code == 200
        assert r.json()["is_enabled"] is False
        assert r.json()["supports_tools"] is None

        # Another empty body with whitespace toggles False -> True
        r2 = client.patch(f"/api/model-endpoints/{ep_id}", content=b"   \n  ")
        assert r2.status_code == 200
        assert r2.json()["is_enabled"] is True

    def test_patch_chunked_body_without_content_length(self, client, test_db):
        """Non-empty JSON body without Content-Length header parses and updates supports_tools."""
        ep_id = str(uuid.uuid4())[:8]
        ep = ModelEndpoint(
            id=ep_id,
            name="Chunked Body EP",
            base_url="http://127.0.0.1:11434/v1",
            is_enabled=True,
            model_type="llm",
            supports_tools=None,
        )
        test_db.add(ep)
        test_db.commit()

        # Send raw bytes with chunked encoding (simulating no content-length header)
        payload = b'{"supports_tools": true}'
        r = client.patch(
            f"/api/model-endpoints/{ep_id}",
            content=payload,
            headers={"Content-Type": "application/json", "Transfer-Encoding": "chunked"},
        )
        assert r.status_code == 200
        assert r.json()["supports_tools"] is True
        assert r.json()["is_enabled"] is True

    def test_patch_malformed_json_body_returns_400(self, client, test_db):
        """Malformed JSON or non-object JSON returns HTTP 400 Bad Request."""
        ep_id = str(uuid.uuid4())[:8]
        ep = ModelEndpoint(
            id=ep_id,
            name="Malformed Body EP",
            base_url="http://127.0.0.1:11434/v1",
            is_enabled=True,
            model_type="llm",
            supports_tools=False,
        )
        test_db.add(ep)
        test_db.commit()

        # Malformed syntax
        r1 = client.patch(
            f"/api/model-endpoints/{ep_id}",
            content=b'{"supports_tools": ',
            headers={"Content-Type": "application/json"},
        )
        assert r1.status_code == 400
        assert "Invalid JSON body" in r1.json()["detail"]

        # Array instead of object
        r2 = client.patch(
            f"/api/model-endpoints/{ep_id}",
            content=b'[1, 2, 3]',
            headers={"Content-Type": "application/json"},
        )
        assert r2.status_code == 400
        assert "JSON body must be an object" in r2.json()["detail"]

        # String instead of object
        r3 = client.patch(
            f"/api/model-endpoints/{ep_id}",
            content=b'"hello world"',
            headers={"Content-Type": "application/json"},
        )
        assert r3.status_code == 400
        assert "JSON body must be an object" in r3.json()["detail"]

        # Verify DB was untouched
        refreshed = test_db.query(ModelEndpoint).filter(ModelEndpoint.id == ep_id).first()
        assert refreshed.is_enabled is True
        assert refreshed.supports_tools is False

    def test_patch_rejects_malformed_values_with_400(self, client, test_db):
        """Malformed supports_tools returns HTTP 400 and does NOT corrupt DB state."""
        ep_id = str(uuid.uuid4())[:8]
        ep = ModelEndpoint(
            id=ep_id,
            name="Protected EP",
            base_url="http://127.0.0.1:11434/v1",
            is_enabled=True,
            model_type="llm",
            supports_tools=True,
        )
        test_db.add(ep)
        test_db.commit()

        # Sending invalid string
        r1 = client.patch(f"/api/model-endpoints/{ep_id}", json={"supports_tools": "bogus"})
        assert r1.status_code == 400
        assert "Unrecognized supports_tools" in r1.json()["detail"]

        # Sending array
        r2 = client.patch(f"/api/model-endpoints/{ep_id}", json={"supports_tools": [1, 2]})
        assert r2.status_code == 400
        assert "Unsupported type" in r2.json()["detail"]

        # Verify DB value is unchanged (still True)
        refreshed = test_db.query(ModelEndpoint).filter(ModelEndpoint.id == ep_id).first()
        assert refreshed.supports_tools is True

    def test_patch_chatgpt_endpoint_forced_false(self, client, test_db):
        """ChatGPT subscription endpoints cannot be toggled to True."""
        ep_id = str(uuid.uuid4())[:8]
        ep = ModelEndpoint(
            id=ep_id,
            name="ChatGPT Account",
            base_url="https://chatgpt.com/backend-api/codex",
            is_enabled=True,
            model_type="llm",
            supports_tools=False,
        )
        test_db.add(ep)
        test_db.commit()

        res = client.patch(
            f"/api/model-endpoints/{ep_id}",
            json={"supports_tools": True},
        )
        assert res.status_code == 200
        # Backend forces False for chatgpt subscription base
        assert res.json()["supports_tools"] is False

        refreshed = test_db.query(ModelEndpoint).filter(ModelEndpoint.id == ep_id).first()
        assert refreshed.supports_tools is False

    def test_patch_requires_admin(self, monkeypatch, test_db):
        """When auth is active, require_admin is enforced."""
        from fastapi import HTTPException

        def mock_require_admin(request):
            raise HTTPException(403, "Administrator privilege required")

        monkeypatch.setattr(mr, "require_admin", mock_require_admin)

        app = FastAPI()
        router = mr.setup_model_routes(model_discovery=None)
        app.include_router(router)
        authed_client = TestClient(app, raise_server_exceptions=False)

        res = authed_client.patch(
            "/api/model-endpoints/any-id",
            json={"supports_tools": True},
        )
        assert res.status_code == 403
        assert res.json()["detail"] == "Administrator privilege required"

    def test_post_endpoint_supports_tools_form_values(self, client, test_db, monkeypatch):
        """POST /api/model-endpoints parses supports_tools form field with same coercion."""
        # Stub probing and settings so POST creates the endpoint without network calls
        monkeypatch.setattr(mr, "_probe_endpoint", lambda *a, **k: ["qwen2.5:7b"])
        monkeypatch.setattr(mr, "_ping_endpoint", lambda *a, **k: {"reachable": True, "error": None})
        monkeypatch.setattr(mr, "_load_settings", lambda: {})

        # 'on' -> True
        r1 = client.post(
            "/api/model-endpoints",
            data={
                "name": "Local On",
                "base_url": "http://127.0.0.1:11434/v1",
                "supports_tools": "on",
                "endpoint_kind": "local",
            },
        )
        assert r1.status_code == 200, r1.text
        test_db.expire_all()
        ep1 = test_db.query(ModelEndpoint).filter(ModelEndpoint.id == r1.json()["id"]).first()
        assert ep1.supports_tools is True

        # 'off' -> False
        r2 = client.post(
            "/api/model-endpoints",
            data={
                "name": "Local Off",
                "base_url": "http://127.0.0.1:11435/v1",
                "supports_tools": "off",
                "endpoint_kind": "local",
            },
        )
        assert r2.status_code == 200, r2.text
        test_db.expire_all()
        ep2 = test_db.query(ModelEndpoint).filter(ModelEndpoint.id == r2.json()["id"]).first()
        assert ep2.supports_tools is False

        # 'auto' -> None
        r3 = client.post(
            "/api/model-endpoints",
            data={
                "name": "Local Auto",
                "base_url": "http://127.0.0.1:11436/v1",
                "supports_tools": "auto",
                "endpoint_kind": "local",
            },
        )
        assert r3.status_code == 200, r3.text
        test_db.expire_all()
        ep3 = test_db.query(ModelEndpoint).filter(ModelEndpoint.id == r3.json()["id"]).first()
        assert ep3.supports_tools is None

        # 'invalid' -> 400
        r4 = client.post(
            "/api/model-endpoints",
            data={
                "name": "Local Bad",
                "base_url": "http://127.0.0.1:11437/v1",
                "supports_tools": "invalid_value",
                "endpoint_kind": "local",
            },
        )
        assert r4.status_code == 400
        assert "Unrecognized supports_tools" in r4.json()["detail"]


# ── Endpoint supports_tools vs Per-Model tool_surface Precedence ───────────────


class TestSupportsToolsAndModelToolModesInteraction:
    """Verify precedence between endpoint supports_tools, Odysseus profiles, and per-model tool modes."""

    def test_endpoint_supports_tools_false_sets_is_api_false(self, test_db):
        """Endpoint supports_tools=False forces is_api=False in _agent_route_tool_mode."""
        from src.agent_loop import _agent_route_tool_mode

        ep = ModelEndpoint(
            id="ep-ollama-off",
            name="Ollama Off",
            base_url="http://127.0.0.1:11434/v1",
            is_enabled=True,
            model_type="llm",
            supports_tools=False,
        )
        test_db.add(ep)
        test_db.commit()

        is_api, _, _ = _agent_route_tool_mode("http://127.0.0.1:11434/v1", "qwen2.5:7b")
        assert is_api is False

    def test_endpoint_supports_tools_true_sets_is_api_true(self, test_db):
        """Endpoint supports_tools=True forces is_api=True in _agent_route_tool_mode."""
        from src.agent_loop import _agent_route_tool_mode

        ep = ModelEndpoint(
            id="ep-ollama-on",
            name="Ollama On",
            base_url="http://127.0.0.1:11434/v1",
            is_enabled=True,
            model_type="llm",
            supports_tools=True,
        )
        test_db.add(ep)
        test_db.commit()

        is_api, _, _ = _agent_route_tool_mode("http://127.0.0.1:11434/v1", "qwen2.5:7b")
        assert is_api is True

    def test_endpoint_supports_tools_auto_ollama_sets_is_api_false(self, test_db):
        """Endpoint supports_tools=None (Auto) on Ollama defaults is_api=False."""
        from src.agent_loop import _agent_route_tool_mode

        ep = ModelEndpoint(
            id="ep-ollama-auto",
            name="Ollama Auto",
            base_url="http://127.0.0.1:11434/v1",
            is_enabled=True,
            model_type="llm",
            supports_tools=None,
        )
        test_db.add(ep)
        test_db.commit()

        is_api, _, _ = _agent_route_tool_mode("http://127.0.0.1:11434/v1", "qwen2.5:7b")
        assert is_api is False

    def test_endpoint_supports_tools_auto_openai_sets_is_api_true(self, test_db):
        """Endpoint supports_tools=None (Auto) on cloud API defaults is_api=True for gpt-4o."""
        from src.agent_loop import _agent_route_tool_mode

        ep = ModelEndpoint(
            id="ep-openai-auto",
            name="OpenAI Auto",
            base_url="https://api.openai.com/v1",
            is_enabled=True,
            model_type="llm",
            supports_tools=None,
        )
        test_db.add(ep)
        test_db.commit()

        is_api, _, _ = _agent_route_tool_mode("https://api.openai.com/v1", "gpt-4o")
        assert is_api is True

    def test_odysseus_ajax_provider_override_forces_native_transport_with_endpoint_off(self, test_db):
        """Odysseus/Ajax model names opt into native transport even when endpoint is Off."""
        from src.agent_loop import _agent_route_tool_mode

        ep = ModelEndpoint(
            id="ep-ajax-off",
            name="Ajax EP Off",
            base_url="http://127.0.0.1:11434/v1",
            is_enabled=True,
            model_type="llm",
            supports_tools=False,
        )
        test_db.add(ep)
        test_db.commit()

        # Odysseus models are trained on native compact tools; provider rules force native mode
        is_api, is_native_ollama, is_ollama_compat = _agent_route_tool_mode(
            "http://127.0.0.1:11434/v1", "Odysseus-experiment"
        )
        assert is_api is True
        assert is_native_ollama is False
        assert is_ollama_compat is False

        is_api_ajax, _, _ = _agent_route_tool_mode(
            "http://127.0.0.1:11434/v1", "provider/Ajax_trial_99"
        )
        assert is_api_ajax is True

    def test_per_model_surface_overrides_endpoint_off(self, test_db):
        """Per-model tool modes override endpoint baseline and control schema emission."""
        import json
        from src.agent_loop import (
            _agent_route_tool_mode,
            _apply_tool_surface_to_schemas,
            _configured_model_tool_surface,
        )

        ep = ModelEndpoint(
            id="ep-hybrid",
            name="Hybrid EP",
            base_url="http://127.0.0.1:11434/v1",
            is_enabled=True,
            model_type="llm",
            supports_tools=False,
            model_tool_modes=json.dumps({
                "qwen2.5:7b": "compact",
                "llama3:8b": "none",
                "deepseek:14b": "full",
            }),
        )
        test_db.add(ep)
        test_db.commit()

        # Endpoint baseline is False
        is_api, _, _ = _agent_route_tool_mode("http://127.0.0.1:11434/v1", "qwen2.5:7b")
        assert is_api is False

        # Per-model tool surface returns 'compact' for qwen2.5:7b
        surface_qwen = _configured_model_tool_surface("http://127.0.0.1:11434/v1", "qwen2.5:7b")
        assert surface_qwen == "compact"

        sample_schema = {
            "type": "function",
            "function": {
                "name": "host_shell",
                "description": "Execute command on host",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string", "description": "Shell command to execute"},
                    },
                },
            },
        }

        # Compact surface strips descriptions from schemas
        compact_schemas = _apply_tool_surface_to_schemas([sample_schema], surface_qwen)
        assert len(compact_schemas) == 1
        assert compact_schemas[0]["function"]["name"] == "host_shell"
        assert "description" not in compact_schemas[0]["function"]

        # Per-model tool surface returns 'none' for llama3:8b, suppressing schemas
        surface_llama = _configured_model_tool_surface("http://127.0.0.1:11434/v1", "llama3:8b")
        assert surface_llama == "none"
        suppressed_schemas = _apply_tool_surface_to_schemas([sample_schema], surface_llama)
        assert suppressed_schemas == []

        # Per-model tool surface returns 'full' for deepseek:14b, keeping full schema descriptions
        surface_deepseek = _configured_model_tool_surface("http://127.0.0.1:11434/v1", "deepseek:14b")
        assert surface_deepseek == "full"
        full_schemas = _apply_tool_surface_to_schemas([sample_schema], surface_deepseek)
        assert len(full_schemas) == 1
        assert "description" in full_schemas[0]["function"]

    def test_per_model_none_suppresses_schemas_even_with_endpoint_on(self, test_db):
        """Explicit per-model 'none' suppresses schemas even when endpoint is On."""
        import json
        from src.agent_loop import (
            _agent_route_tool_mode,
            _apply_tool_surface_to_schemas,
            _configured_model_tool_surface,
        )

        ep = ModelEndpoint(
            id="ep-on-suppressed",
            name="Endpoint On With Per-Model Suppressed",
            base_url="http://127.0.0.1:11434/v1",
            is_enabled=True,
            model_type="llm",
            supports_tools=True,
            model_tool_modes=json.dumps({"qwen2.5:7b": "none"}),
        )
        test_db.add(ep)
        test_db.commit()

        is_api, _, _ = _agent_route_tool_mode("http://127.0.0.1:11434/v1", "qwen2.5:7b")
        assert is_api is True

        surface = _configured_model_tool_surface("http://127.0.0.1:11434/v1", "qwen2.5:7b")
        assert surface == "none"

        sample_schema = {"type": "function", "function": {"name": "test_tool"}}
        schemas = _apply_tool_surface_to_schemas([sample_schema], surface)
        assert schemas == []


def test_browser_harness_port_selection_and_isolation():
    """Verify resolveAppPort and getEphemeralPort invariants in test_added_models_tools_toggle.mjs."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node binary not on PATH")
    repo_root = Path(__file__).resolve().parent.parent
    test_script = repo_root / "tests" / "browser" / "test_added_models_tools_toggle.mjs"
    check_code = f"""
    import {{ resolveAppPort, getEphemeralPort }} from '{test_script.as_uri()}';

    // 1. Unset returns null
    if (resolveAppPort(undefined) !== null) throw new Error('Expected undefined PORT to return null');
    if (resolveAppPort('') !== null) throw new Error('Expected empty PORT to return null');

    // 2. Valid PORT returns integer
    if (resolveAppPort('8765') !== 8765) throw new Error('Expected 8765');

    // 3. 8100 throws error for ChromaDB guard isolation
    let threw8100 = false;
    try {{ resolveAppPort('8100'); }} catch (e) {{ threw8100 = true; }}
    if (!threw8100) throw new Error('Expected 8100 to throw error');

    // 4. Invalid values throw error
    for (const bad of ['-1', '0', '65536', 'abc', '3.14']) {{
      let threwBad = false;
      try {{ resolveAppPort(bad); }} catch (e) {{ threwBad = true; }}
      if (!threwBad) throw new Error('Expected bad port to throw: ' + bad);
    }}

    // 5. Ephemeral port excludes forbidden ports
    const port = await getEphemeralPort(new Set([8100, 8765]));
    if (port === 8100 || port === 8765) throw new Error('Ephemeral port was in excluded set');
    if (typeof port !== 'number' || port < 1024) throw new Error('Invalid ephemeral port: ' + port);
    console.log('PORT_TEST_OK');
    """
    proc = subprocess.run(
        [node, "--input-type=module", "-e", check_code],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"Port helper test failed:\n{proc.stderr}"
    assert "PORT_TEST_OK" in proc.stdout


def test_import_state_cleanliness():
    """Verify that sys.modules contains real implementations, not leaked test stubs."""
    for mod_name in [
        "src.endpoint_resolver",
        "src.llm_core",
        "src.tls_overrides",
        "src.settings",
        "src.constants",
        "core.log_safety",
    ]:
        mod = sys.modules.get(mod_name)
        assert mod is not None, f"Expected {mod_name} to be loaded in sys.modules"
        assert not isinstance(mod, MagicMock), f"{mod_name} must not be a MagicMock stub"
        assert getattr(mod, "__file__", None), f"{mod_name} must have a valid on-disk __file__"
