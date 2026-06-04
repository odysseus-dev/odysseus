"""Regression test: audit teacher model resolution must be owner-scoped.

`_resolve_audit_models` passes ``owner`` to ``resolve_endpoint`` for the worker
model but historically called ``_resolve_model(spec)`` without ``owner`` for the
teacher, letting the teacher step match any enabled ModelEndpoint across users.
"""

import src.ai_interaction as ai_interaction
import src.endpoint_resolver as endpoint_resolver
import src.settings as settings
from routes.skills_routes import _resolve_audit_models


def test_audit_teacher_model_resolution_is_owner_scoped(monkeypatch):
    captured = {}

    def fake_resolve_model(spec, owner=None):
        captured["spec"] = spec
        captured["owner"] = owner
        return ("http://teacher.local/v1/chat", "gpt-test", {})

    def fake_resolve_endpoint(role, owner=None):
        return ("http://worker.local/v1/chat", "worker-model", {})

    def fake_get_setting(key, default=None):
        if key == "teacher_enabled":
            return True
        if key == "teacher_model":
            return "gpt-test"
        return default

    monkeypatch.setattr(ai_interaction, "_resolve_model", fake_resolve_model)
    monkeypatch.setattr(endpoint_resolver, "resolve_endpoint", fake_resolve_endpoint)
    monkeypatch.setattr(settings, "get_setting", fake_get_setting)

    url, model, headers, teacher = _resolve_audit_models(owner="alice")

    assert teacher == ("http://teacher.local/v1/chat", "gpt-test", {})
    assert captured["spec"] == "gpt-test"
    assert captured["owner"] == "alice"
