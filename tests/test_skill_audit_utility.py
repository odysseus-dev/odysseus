from routes.skills_routes import _audit_utility_blocker
from routes.skills_routes import _audit_finalize_status
from routes.skills_routes import _finalize_audit_batch
from routes.skills_routes import _audit_one_skill
from routes.skills_routes import _skill_audit_jobs
from routes.skills_routes import run_scheduled_skill_audit
from routes.skills_routes import _skill_baseline_task, _skill_test_task


def test_audit_arms_receive_the_same_task_text():
    skill = {
        "name": "release-checklist",
        "when_to_use": "Prepare and verify a release checklist.",
    }

    assert _skill_baseline_task(skill) == _skill_test_task(skill)


def test_equal_baseline_does_not_block_a_functionally_passing_skill():
    reason = _audit_utility_blocker(
        {"name": "release-checklist"},
        {"necessary": True},
        {
            "verdict": "pass",
            "baseline_verdict": "same",
            "usefulness": 0.1,
            "saved_turns": 0,
            "saved_tool_calls": 0,
            "summary": "The procedure completed correctly.",
            "issues": [],
        },
    )

    assert reason is None


def test_inconclusive_audit_preserves_existing_approval(monkeypatch):
    class FakeSkills:
        def __init__(self):
            self.row = {
                "name": "release-checklist",
                "status": "published",
                "confidence": 0.9,
                "baseline_verdict": "worse",
                "usefulness": 0.1,
            }
            self.updated = []

        def load(self, owner=None):
            return [dict(self.row)]

        def update_skill(self, name, fields, owner=None):
            self.updated.append((name, fields, owner))
            self.row.update(fields)

    monkeypatch.setattr(
        "routes.skills_routes._audit_auto_publish_policy",
        lambda owner: (True, 0.8),
    )
    fake = FakeSkills()

    status = _audit_finalize_status(
        fake,
        "release-checklist",
        "alice",
        "inconclusive",
        0.9,
        {"necessary": False, "reason": "Audit could not establish utility"},
    )

    assert status == "published"
    assert fake.updated == []


def test_batch_finalizer_does_not_demote_inconclusive_skill(monkeypatch):
    class FakeSkills:
        def __init__(self):
            self.updated = []

        def load(self, owner=None):
            return [{
                "name": "release-checklist",
                "source": "learned",
                "status": "published",
                "confidence": 0.9,
                "audit_verdict": "inconclusive",
            }]

        def update_skill(self, name, fields, owner=None):
            self.updated.append((name, fields, owner))

    monkeypatch.setattr(
        "routes.skills_routes._audit_auto_publish_policy",
        lambda owner: (True, 0.8),
    )
    fake = FakeSkills()

    _finalize_audit_batch(
        fake,
        [{"skill": "release-checklist", "result": "inconclusive"}],
        "alice",
        lambda message: None,
    )

    assert fake.updated == []


def test_app_does_not_start_a_second_ownerless_skill_audit():
    from pathlib import Path

    app_source = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")

    assert "_skill_audit_nightly_loop" not in app_source
    assert "run_scheduled_skill_audit(skills_manager, owner=None" not in app_source


def test_task_audit_resolves_the_owners_model_settings(monkeypatch):
    from services.memory import skills as skills_module
    from src import builtin_actions
    from src import llm_core

    class FakeSkills:
        def load(self, owner=None):
            return [{"name": "release-checklist", "audit_verdict": None}]

    seen = {}

    def fake_resolve(owner=None, model_spec=None, endpoint_url=None):
        seen["owner"] = owner
        assert model_spec is None
        assert endpoint_url is None
        return "http://example.test", "audit-model", None, None

    async def fake_run(key, skills_manager, names, url, model, headers, teacher, owner, workload="foreground"):
        seen["workload"] = workload
        job = _skill_audit_jobs[key]
        job["done"] = 1
        job["results"] = [{"skill": names[0], "result": "pass"}]

    monkeypatch.setattr(skills_module, "SkillsManager", lambda data_dir: FakeSkills())
    monkeypatch.setattr("routes.skills_routes._resolve_audit_models", fake_resolve)
    monkeypatch.setattr("routes.skills_routes._run_audit_all_job", fake_run)
    monkeypatch.setattr(llm_core, "seconds_since_model_activity", lambda url, model: None)

    try:
        text, ok = __import__("asyncio").run(
            builtin_actions.action_audit_skills("alice")
        )
    finally:
        _skill_audit_jobs.pop(("alice",), None)

    assert ok is True
    assert "Audited 1/1" in text
    assert seen["owner"] == "alice"
    assert seen["workload"] == "background"


def test_generic_tag_is_not_a_skill_audit_blocker():
    skill = {"tags": ["weather", "generic"], "name": "weather-check"}

    assert _audit_utility_blocker(skill, None, None) is None


def test_skill_that_is_better_than_baseline_is_not_blocked():
    verdict = {
        "baseline_verdict": "better",
        "usefulness": 0.8,
        "saved_turns": 3,
        "saved_tool_calls": 1,
    }

    assert _audit_utility_blocker({}, None, verdict) is None


def test_skill_worse_than_baseline_is_blocked():
    verdict = {
        "baseline_verdict": "worse",
        "usefulness": 0.2,
        "saved_turns": -1,
        "saved_tool_calls": -1,
    }

    assert _audit_utility_blocker({}, None, verdict) == "Skill performed worse than the no-skill baseline"


def test_skill_same_as_baseline_with_no_savings_is_not_a_functional_failure():
    verdict = {
        "baseline_verdict": "same",
        "usefulness": 0.1,
        "saved_turns": 0,
        "saved_tool_calls": 0,
    }

    assert _audit_utility_blocker({}, None, verdict) is None


def test_duplicate_necessity_signal_is_still_blocked():
    necessity = {
        "necessary": False,
        "redundant_with": ["existing-skill"],
        "reason": "Duplicates the existing skill.",
    }

    assert _audit_utility_blocker({}, necessity, None) == "Duplicates the existing skill."


def test_audit_skips_obvious_duplicate_before_llm_work(monkeypatch):
    class FakeSkills:
        def __init__(self):
            self.updated = []
            self.audit = []
            self.necessity = []

        def read_skill_md(self, name, owner=None):
            return "---\nname: weather-check-1\n---\n# Weather check\n"

        def load(self, owner=None):
            return [
                {
                    "name": "weather-check",
                    "description": "Check tomorrow's weather.",
                    "when_to_use": "weather tomorrow",
                    "procedure": ["Search weather and answer."],
                    "tags": ["weather"],
                    "status": "published",
                    "confidence": 0.95,
                    "uses": 2,
                },
                {
                    "name": "weather-check-1",
                    "description": "Check tomorrow's weather.",
                    "when_to_use": "weather tomorrow",
                    "procedure": ["Search weather and answer."],
                    "tags": ["weather"],
                    "status": "draft",
                    "confidence": 0.5,
                    "uses": 0,
                },
            ]

        def update_skill(self, name, fields, owner=None):
            self.updated.append((name, fields))

        def set_audit(self, *args, **kwargs):
            self.audit.append((args, kwargs))

        def set_necessity(self, *args, **kwargs):
            self.necessity.append((args, kwargs))

    async def fail_necessity(*args, **kwargs):
        raise AssertionError("duplicate cleanup should run before LLM necessity check")

    async def fail_run(*args, **kwargs):
        raise AssertionError("duplicate cleanup should run before skill/baseline tests")

    monkeypatch.setattr("routes.skills_routes._eval_skill_necessity", fail_necessity)
    monkeypatch.setattr("routes.skills_routes._run_skill_audit_arm", fail_run)

    import asyncio

    result = asyncio.run(
        _audit_one_skill(
            FakeSkills(),
            {"name": "weather-check-1"},
            "http://example.test",
            "model",
            None,
            None,
            "owner",
            lambda _msg: None,
        )
    )

    assert result["result"] == "skipped_duplicate"


def test_audit_persists_saved_turn_estimate_from_baseline_comparison(monkeypatch):
    class FakeSkills:
        def __init__(self):
            self.row = {
                "name": "release-checklist",
                "description": "Prepare a release checklist.",
                "when_to_use": "release prep",
                "procedure": ["Build and verify the checklist."],
                "tags": ["release"],
                "status": "draft",
                "confidence": 0.5,
                "uses": 0,
                "necessity": None,
            }
            self.audit = []

        def read_skill_md(self, name, owner=None):
            return "---\nname: release-checklist\n---\n# Release checklist\n"

        def load(self, owner=None):
            return [dict(self.row)]

        def update_skill(self, name, fields, owner=None):
            self.row.update(fields)
            return True

        def set_audit(self, *args, **kwargs):
            self.audit.append((args, kwargs))
            self.row["audit_verdict"] = args[1]
            self.row["saved_turns"] = kwargs.get("saved_turns")
            self.row["saved_tool_calls"] = kwargs.get("saved_tool_calls")

        def set_necessity(self, name, necessary, redundant_with=None, reason="", owner=None):
            self.row["necessity"] = {
                "necessary": necessary,
                "redundant_with": list(redundant_with or []),
                "reason": reason,
            }

    async def fake_necessity(*args, **kwargs):
        return {"necessary": True, "redundant_with": [], "reason": ""}

    calls = []

    async def fake_arm(messages, *args, **kwargs):
        calls.append(messages)
        if len(calls) == 1:
            return "skill run transcript", {"turns": 1, "tool_calls": 1}, None
        return "baseline transcript", {"turns": 3, "tool_calls": 2}, None

    async def fake_eval(*args, **kwargs):
        assert kwargs["skill_stats"] == {"turns": 1, "tool_calls": 1}
        assert kwargs["baseline_stats"] == {"turns": 3, "tool_calls": 2}
        assert kwargs["baseline_transcript"] == "baseline transcript"
        return {
            "verdict": "pass",
            "confidence": 0.95,
            "summary": "Skill is faster.",
            "issues": [],
            "baseline_verdict": "better",
            "usefulness": 0.9,
            "saved_turns": 2,
            "saved_tool_calls": 1,
        }

    monkeypatch.setattr("routes.skills_routes._eval_skill_necessity", fake_necessity)
    monkeypatch.setattr("routes.skills_routes._run_skill_audit_arm", fake_arm)
    monkeypatch.setattr("routes.skills_routes._eval_skill_run", fake_eval)

    import asyncio

    fake = FakeSkills()
    result = asyncio.run(
        _audit_one_skill(
            fake,
            fake.row,
            "http://example.test",
            "model",
            None,
            None,
            "owner",
            lambda _msg: None,
        )
    )

    assert result["result"] == "pass"
    assert len(calls) == 2
    assert fake.audit[-1][1]["saved_turns"] == 2
    assert fake.audit[-1][1]["saved_tool_calls"] == 1
    assert fake.audit[-1][1]["baseline_verdict"] == "better"
    assert fake.audit[-1][1]["usefulness"] == 0.9
    assert fake.row["status"] == "published"


def test_scheduled_skill_audit_marks_model_work_as_background(monkeypatch):
    class FakeSkills:
        def load(self, owner=None):
            return [{"name": "release-checklist", "audited_at": None}]

    captured = {}

    def fake_resolve(owner=None):
        return "http://127.0.0.1:11434/v1", "model", None, None

    async def fake_run(key, skills_manager, names, url, model, headers, teacher,
                       owner, workload="foreground"):
        captured["workload"] = workload
        job = _skill_audit_jobs[key]
        job["status"] = "done"

    monkeypatch.setattr("routes.skills_routes._resolve_audit_models", fake_resolve)
    monkeypatch.setattr("routes.skills_routes._run_audit_all_job", fake_run)

    try:
        result = __import__("asyncio").run(
            run_scheduled_skill_audit(FakeSkills(), owner="owner", max_skills=1)
        )
    finally:
        _skill_audit_jobs.pop(("owner",), None)

    assert result["status"] == "done"
    assert captured["workload"] == "background"


def test_background_audit_propagates_workload_to_every_model_phase(monkeypatch):
    class FakeSkills:
        def __init__(self):
            self.row = {
                "name": "release-checklist",
                "description": "Prepare a release checklist.",
                "when_to_use": "release prep",
                "procedure": ["Build and verify the checklist."],
                "tags": ["release"],
                "status": "draft",
                "confidence": 0.5,
                "uses": 0,
                "necessity": None,
            }

        def read_skill_md(self, name, owner=None):
            return "---\nname: release-checklist\n---\n# Release checklist\n"

        def load(self, owner=None):
            return [dict(self.row)]

        def update_skill(self, name, fields, owner=None):
            self.row.update(fields)
            return True

        def set_audit(self, name, verdict, **kwargs):
            self.row["audit_verdict"] = verdict

        def set_necessity(self, name, necessary, redundant_with=None, reason="", owner=None):
            self.row["necessity"] = {"necessary": necessary}

    seen = []

    async def fake_necessity(*args, **kwargs):
        seen.append(("necessity", kwargs.get("workload")))
        return {"necessary": True, "redundant_with": [], "reason": ""}

    async def fake_arm(*args, **kwargs):
        seen.append(("arm", kwargs.get("workload")))
        return "transcript", {"turns": 1, "tool_calls": 0}, None

    async def fake_eval(*args, **kwargs):
        seen.append(("review", kwargs.get("workload")))
        return {
            "verdict": "pass",
            "confidence": 0.95,
            "summary": "works",
            "issues": [],
            "baseline_verdict": "better",
            "usefulness": 0.9,
            "saved_turns": 1,
            "saved_tool_calls": 0,
        }

    monkeypatch.setattr("routes.skills_routes._eval_skill_necessity", fake_necessity)
    monkeypatch.setattr("routes.skills_routes._run_skill_audit_arm", fake_arm)
    monkeypatch.setattr("routes.skills_routes._eval_skill_run", fake_eval)

    fake = FakeSkills()
    result = __import__("asyncio").run(
        _audit_one_skill(
            fake,
            fake.row,
            "http://127.0.0.1:11434/v1",
            "model",
            None,
            None,
            "owner",
            lambda _msg: None,
            workload="background",
        )
    )

    assert result["result"] == "pass"
    assert seen == [
        ("necessity", "background"),
        ("arm", "background"),
        ("arm", "background"),
        ("review", "background"),
    ]
