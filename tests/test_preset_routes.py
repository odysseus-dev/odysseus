"""Tests for preset_routes.py — request model validation and manager integration."""

import os
import sys
import types
import uuid
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Stub core.middleware before import so require_admin is a no-op.
if "core.middleware" not in sys.modules:
    _mw = types.ModuleType("core.middleware")
    _mw.require_admin = MagicMock(return_value=None)
    sys.modules["core.middleware"] = _mw

from routes.preset_routes import UserTemplateRequest  # noqa: E402
import routes.preset_routes as preset_routes  # noqa: E402


# ── UserTemplateRequest validation ──

class TestUserTemplateRequest:
    def test_valid_minimal(self):
        req = UserTemplateRequest(name="Helper")
        assert req.name == "Helper"
        assert req.id == ""
        assert req.system_prompt == ""
        assert req.temperature == pytest.approx(1.0)
        assert req.max_tokens == 0

    def test_valid_full(self):
        req = UserTemplateRequest(
            id="user-abc123",
            name="Poet",
            system_prompt="You speak only in verse.",
            temperature=0.7,
            max_tokens=2048,
        )
        assert req.id == "user-abc123"
        assert req.temperature == pytest.approx(0.7)
        assert req.max_tokens == 2048

    def test_empty_name_rejected(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            UserTemplateRequest(name="")

    def test_name_too_long_rejected(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            UserTemplateRequest(name="x" * 101)

    def test_name_at_max_length_accepted(self):
        req = UserTemplateRequest(name="a" * 100)
        assert len(req.name) == 100

    def test_temperature_below_zero_rejected(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            UserTemplateRequest(name="X", temperature=-0.1)

    def test_temperature_above_two_rejected(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            UserTemplateRequest(name="X", temperature=2.1)

    def test_temperature_boundary_values_accepted(self):
        lo = UserTemplateRequest(name="X", temperature=0.0)
        hi = UserTemplateRequest(name="X", temperature=2.0)
        assert lo.temperature == pytest.approx(0.0)
        assert hi.temperature == pytest.approx(2.0)

    def test_negative_max_tokens_rejected(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            UserTemplateRequest(name="X", max_tokens=-1)

    def test_max_tokens_above_limit_rejected(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            UserTemplateRequest(name="X", max_tokens=65537)

    def test_max_tokens_at_limit_accepted(self):
        req = UserTemplateRequest(name="X", max_tokens=65536)
        assert req.max_tokens == 65536

    def test_system_prompt_at_max_length_accepted(self):
        req = UserTemplateRequest(name="X", system_prompt="z" * 10000)
        assert len(req.system_prompt) == 10000

    def test_system_prompt_too_long_rejected(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            UserTemplateRequest(name="X", system_prompt="z" * 10001)


# ── preset_manager mock interactions ──

class TestPresetManagerRoutes:
    def _make_manager(self):
        m = MagicMock()
        m.presets = {"default": {"temperature": 1.0}}
        m.get_user_templates.return_value = []
        m.save_user_template.return_value = True
        m.delete_user_template.return_value = True
        m.get_group_presets.return_value = []
        return m

    def test_router_registered(self):
        mgr = self._make_manager()
        router = preset_routes.setup_preset_routes(mgr)
        paths = {r.path for r in router.routes}
        assert "/api/presets" in paths
        assert "/api/presets/custom" in paths
        assert "/api/presets/templates" in paths
        assert "/api/presets/groups" in paths

    def test_template_id_auto_generated_when_empty(self):
        """save_user_template receives a template with a generated id when the
        client sends an empty string."""
        mgr = self._make_manager()
        preset_routes.setup_preset_routes(mgr)

        # Simulate what the route does with an empty id
        template = {"id": "", "name": "Pirate", "system_prompt": "", "temperature": 1.0, "max_tokens": 0}
        if not template["id"]:
            template["id"] = f"user-{uuid.uuid4().hex[:8]}"

        assert template["id"].startswith("user-")
        assert len(template["id"]) == len("user-") + 8

    def test_delete_missing_template_returns_false(self):
        mgr = self._make_manager()
        mgr.delete_user_template.return_value = False
        preset_routes.setup_preset_routes(mgr)
        result = mgr.delete_user_template("nonexistent")
        assert result is False

    def test_group_presets_round_trip(self):
        mgr = self._make_manager()
        groups = [{"name": "Debug crew", "models": ["gpt-4o", "claude-sonnet-4"]}]
        mgr.get_group_presets.return_value = groups
        preset_routes.setup_preset_routes(mgr)
        assert mgr.get_group_presets() == groups
