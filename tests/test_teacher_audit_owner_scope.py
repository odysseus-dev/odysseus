"""Owner-scope tests for the remaining _resolve_model call sites.

Both the teacher-escalation path and the skill-audit teacher resolution map a
model spec to an endpoint (and its decrypted api_key). Like /presets/expand,
that lookup must be scoped to the calling user, otherwise it can resolve another
owner's ModelEndpoint in a multi-user deployment. See #2283.
"""

import asyncio
import json

import src.teacher_escalation as teacher_escalation
import routes.skills_routes as skills_routes
from services.memory.skills import SkillsManager


def test_call_teacher_scopes_model_resolution_to_owner(monkeypatch):
    seen = {}

    def fake_resolve_model(spec, owner=None):
        seen["spec"] = spec
        seen["owner"] = owner
        return ("http://endpoint.local/v1", "teacher-model", {})

    async def fake_llm_call_async(url, model, messages, **kwargs):
        return "teacher reply"

    monkeypatch.setattr("src.ai_interaction._resolve_model", fake_resolve_model)
    monkeypatch.setattr("src.ai_interaction._TEACHER_SYSTEM_PROMPT", "sys", raising=False)
    monkeypatch.setattr("src.llm_core.llm_call_async", fake_llm_call_async)

    result = asyncio.run(
        teacher_escalation._call_teacher("teacher-model", "prompt", owner="alice")
    )

    assert result == "teacher reply"
    assert seen["owner"] == "alice"
    assert seen["spec"] == "teacher-model"


def test_audit_teacher_resolution_scoped_to_owner(monkeypatch):
    seen = {}

    def fake_resolve_endpoint(role, owner=None):
        return ("http://worker.local/v1", "worker-model", {})

    def fake_get_setting(key, default=None):
        return {"teacher_enabled": True, "teacher_model": "teacher-model"}.get(key, default)

    def fake_resolve_model(spec, owner=None):
        seen["spec"] = spec
        seen["owner"] = owner
        return ("http://endpoint.local/v1", "teacher-model", {})

    monkeypatch.setattr("src.endpoint_resolver.resolve_endpoint", fake_resolve_endpoint)
    monkeypatch.setattr("src.settings.get_setting", fake_get_setting)
    monkeypatch.setattr("src.ai_interaction._resolve_model", fake_resolve_model)
    # list_model_ids is best-effort; force it to no-op so the worker model passes through.
    monkeypatch.setattr("src.llm_core.list_model_ids", lambda url, headers=None: [])

    url, model, headers, teacher = skills_routes._resolve_audit_models(owner="alice")

    assert (url, model) == ("http://worker.local/v1", "worker-model")
    assert teacher == ("http://endpoint.local/v1", "teacher-model", {})
    assert seen["owner"] == "alice"
    assert seen["spec"] == "teacher-model"


def test_teacher_existing_skill_hint_is_owner_scoped(tmp_path, monkeypatch):
    sm = SkillsManager(str(tmp_path))
    sm.add_skill(
        name="alice-only",
        description="Alice private workflow",
        category="ops",
        owner="alice",
        source="user",
        status="published",
    )
    sm.add_skill(
        name="bob-only",
        description="Bob private workflow",
        category="ops",
        owner="bob",
        source="user",
        status="published",
    )
    captured = {}

    async def fake_call_teacher(spec, prompt, owner=None):
        captured["prompt"] = prompt
        captured["owner"] = owner
        return None

    monkeypatch.setattr("src.constants.DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        "src.settings.get_setting",
        lambda key, default=None: "teacher-model" if key == "teacher_model" else default,
    )
    monkeypatch.setattr(teacher_escalation, "_call_teacher", fake_call_teacher)

    result = asyncio.run(
        teacher_escalation.escalate_and_learn(
            "do work",
            [],
            "failed",
            "failure",
            owner="alice",
        )
    )

    assert result is None
    assert captured["owner"] == "alice"
    assert "alice-only" in captured["prompt"]
    assert "bob-only" not in captured["prompt"]


def test_teacher_posthoc_dedup_is_owner_scoped(tmp_path, monkeypatch):
    sm = SkillsManager(str(tmp_path))
    sm.add_skill(
        name="deploy-helper",
        description="Deploy helper",
        category="ops",
        owner="bob",
        source="user",
        status="published",
        when_to_use="deploy app safely",
        procedure=["deploy app safely"],
        tags=["deploy"],
    )
    proposed = {
        "name": "deploy-helper-copy",
        "description": "Deploy helper",
        "category": "ops",
        "when_to_use": "deploy app safely",
        "procedure": ["deploy app safely"],
        "tags": ["deploy"],
        "status": "draft",
    }
    saved = {}

    async def fake_call_teacher(spec, prompt, owner=None):
        return "```json\n" + json.dumps(proposed) + "\n```"

    async def fake_evaluate_turn(**kwargs):
        return {"failure": False, "reason": "", "severity": "none"}

    async def fake_manage_skills(content, owner=None):
        saved["owner"] = owner
        saved["payload"] = json.loads(content)
        return {"results": "saved", "exit_code": 0}

    monkeypatch.setattr("src.constants.DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        "src.settings.get_setting",
        lambda key, default=None: "teacher-model" if key == "teacher_model" else default,
    )
    monkeypatch.setattr(teacher_escalation, "_call_teacher", fake_call_teacher)
    monkeypatch.setattr(teacher_escalation.turn_judge, "evaluate_turn", fake_evaluate_turn)
    monkeypatch.setattr("src.ai_interaction._resolve_model", lambda spec, owner=None: ("u", "m", {}))
    monkeypatch.setattr("src.tool_implementations.do_manage_skills", fake_manage_skills)

    result = asyncio.run(
        teacher_escalation.escalate_and_learn(
            "deploy",
            [],
            "failed",
            "failure",
            owner="alice",
        )
    )

    assert result == "deploy-helper-copy"
    assert saved["owner"] == "alice"
    assert saved["payload"]["name"] == "deploy-helper-copy"
