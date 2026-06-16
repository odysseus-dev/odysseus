"""Tests for the plugin contract (schema validation, host facade, discovery)."""
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import APIRouter

from src.plugin_schema import PluginValidationError, validate_manifest
from src.plugin_host import PluginHost
from src.plugin_manager import PluginManager, _load_manifest_from_dir


class TestValidateManifest:
    def test_valid_manifest(self):
        validate_manifest({
            "name": "hello-world",
            "version": "1.0.0",
            "entry_point": "hello:register",
            "odysseus_compat": ">=0.1.0",
            "description": "A test plugin",
            "author": "Test",
            "capabilities": ["tools"],
        })

    def test_missing_required_fields(self):
        with pytest.raises(PluginValidationError) as exc:
            validate_manifest({})
        assert "Missing required fields" in str(exc.value)

    def test_invalid_name(self):
        with pytest.raises(PluginValidationError) as exc:
            validate_manifest({
                "name": "Hello World",
                "version": "1.0.0",
                "entry_point": "hello:register",
                "odysseus_compat": ">=0.1.0",
                "description": "x",
                "author": "x",
                "capabilities": ["tools"],
            })
        assert "kebab-case" in str(exc.value)

    def test_invalid_version(self):
        with pytest.raises(PluginValidationError) as exc:
            validate_manifest({
                "name": "hello",
                "version": "not-a-version",
                "entry_point": "hello:register",
                "odysseus_compat": ">=0.1.0",
                "description": "x",
                "author": "x",
                "capabilities": ["tools"],
            })
        assert "version" in str(exc.value)

    def test_invalid_entry_point(self):
        with pytest.raises(PluginValidationError) as exc:
            validate_manifest({
                "name": "hello",
                "version": "1.0.0",
                "entry_point": "register",
                "odysseus_compat": ">=0.1.0",
                "description": "x",
                "author": "x",
                "capabilities": ["tools"],
            })
        assert "entry_point" in str(exc.value)

    def test_unknown_capability(self):
        with pytest.raises(PluginValidationError) as exc:
            validate_manifest({
                "name": "hello",
                "version": "1.0.0",
                "entry_point": "hello:register",
                "odysseus_compat": ">=0.1.0",
                "description": "x",
                "author": "x",
                "capabilities": ["not_real"],
            })
        assert "Unknown capabilities" in str(exc.value)

    def test_privileged_capability_is_allowed(self):
        validate_manifest({
            "name": "hello",
            "version": "1.0.0",
            "entry_point": "hello:register",
            "odysseus_compat": ">=0.1.0",
            "description": "x",
            "author": "x",
            "capabilities": ["manage_plugins"],
        })


class TestPluginHost:
    def test_add_router_requires_routes_capability(self):
        app = MagicMock()
        host = PluginHost("test", ["tools"], app)
        with pytest.raises(PermissionError) as exc:
            host.add_router(APIRouter())
        assert "lacks capability 'routes'" in str(exc.value)

    def test_add_router_succeeds_with_capability(self):
        app = MagicMock()
        host = PluginHost("test", ["routes"], app)
        host.add_router(APIRouter())
        app.include_router.assert_called_once()

    def test_admin_router_requires_privileged_capability(self):
        app = MagicMock()
        host = PluginHost("test", ["routes"], app)
        with pytest.raises(PermissionError) as exc:
            host.add_router(APIRouter(), admin=True)
        assert "lacks capability 'manage_plugins'" in str(exc.value)

    def test_add_tool_requires_tools_capability(self):
        app = MagicMock()
        host = PluginHost("test", ["routes"], app)
        with pytest.raises(PermissionError) as exc:
            host.add_tool("test", {}, lambda: None)
        assert "lacks capability 'tools'" in str(exc.value)

    def test_register_provider_requires_provider_capability(self):
        app = MagicMock()
        host = PluginHost("test", ["routes"], app)
        with pytest.raises(PermissionError) as exc:
            host.register_provider("test", object)
        assert "lacks capability 'provider'" in str(exc.value)


class TestPluginManager:
    def test_list_installed_empty(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("src.plugin_manager.PLUGINS_DIR", tmpdir):
                pm = PluginManager()
                assert pm.list_installed() == []

    def test_load_manifest_from_dir_valid(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / "odysseus-plugin.json"
            manifest_path.write_text(json.dumps({
                "name": "test-plugin",
                "version": "1.0.0",
                "entry_point": "test:register",
                "odysseus_compat": ">=0.1.0",
                "description": "x",
                "author": "x",
                "capabilities": ["tools"],
            }))
            manifest = _load_manifest_from_dir(tmpdir)
            assert manifest["name"] == "test-plugin"

    def test_load_manifest_from_dir_invalid(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / "odysseus-plugin.json"
            manifest_path.write_text(json.dumps({"name": "bad"}))
            assert _load_manifest_from_dir(tmpdir) is None

    def test_enable_disable(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("src.plugin_manager.PLUGINS_DIR", tmpdir):
                pm = PluginManager()
                pm.set_enabled("test", False)
                assert not pm.is_enabled("test")
                pm.set_enabled("test", True)
                assert pm.is_enabled("test")
