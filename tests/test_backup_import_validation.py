"""Backup import applies the same validation as the normal write paths (#6841)."""
import asyncio
from unittest.mock import MagicMock

import pytest

import routes.backup_routes as br


class _Req:
    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body


@pytest.fixture
def harness(monkeypatch):
    monkeypatch.setattr(br, "require_admin", lambda request: None)
    monkeypatch.setattr(br, "get_current_user", lambda request: "alice")

    saved = {}
    monkeypatch.setattr(br, "load_settings", lambda: {"agent_max_rounds": 20, "tts_voice": "alloy"})
    monkeypatch.setattr(br, "save_settings", lambda s: saved.__setitem__("settings", dict(s)))
    monkeypatch.setattr(br, "load_features", lambda: {"web_search": True, "rag": True})
    monkeypatch.setattr(br, "save_features", lambda f: saved.__setitem__("features", dict(f)))

    mem = MagicMock()
    mem.load_all_for_update.return_value = []
    mem.save.side_effect = lambda entries: saved.__setitem__("memories", entries)
    skills = MagicMock()
    skills.load_all.return_value = []
    skills.add_skill.return_value = {"name": "n", "id": "i"}

    router = br.setup_backup_routes(mem, MagicMock(), skills)
    endpoint = next(
        r.endpoint for r in router.routes
        if r.path == "/api/import" and "POST" in getattr(r, "methods", set())
    )
    return lambda body: asyncio.run(endpoint(_Req(body))), saved, skills


def test_settings_import_skips_unknown_retired_and_mistyped(harness):
    run, saved, _ = harness
    run({"settings": {
        "agent_max_rounds": "zzz",      # wrong type
        "tts_voice": "nova",            # ok
        "bogus_key": 1,                 # unknown
        "default_model_fallbacks": [],  # retired
    }})
    assert saved["settings"] == {"agent_max_rounds": 20, "tts_voice": "nova"}


def test_settings_import_accepts_well_typed_int(harness):
    run, saved, _ = harness
    run({"settings": {"agent_max_rounds": 30}})
    assert saved["settings"]["agent_max_rounds"] == 30


def test_features_import_only_known_boolean_keys(harness):
    run, saved, _ = harness
    run({"features": {"web_search": "no", "rag": False, "bogus": True}})
    assert saved["features"] == {"web_search": True, "rag": False}


def test_memory_with_non_string_text_is_skipped(harness):
    run, saved, _ = harness
    result = run({"memories": [{"text": 5}, {"text": "keep me"}]})
    assert result["ok"] is True
    assert [m["text"] for m in saved["memories"]] == ["keep me"]


def test_skill_with_non_string_title_is_skipped(harness):
    run, _, skills = harness
    result = run({"skills": [{"title": 5}, {"title": "real"}]})
    assert result["ok"] is True
    assert skills.add_skill.call_count == 1
    assert skills.add_skill.call_args.kwargs["title"] == "real"


def test_skill_with_malformed_fields_is_skipped_not_fatal(harness):
    run, _, skills = harness
    skills.add_skill.side_effect = [TypeError("'int' object is not iterable"), {"name": "n", "id": "i"}]
    result = run({"skills": [{"title": "bad", "tags": 5}, {"title": "good"}]})
    assert result["ok"] is True
    assert result["imported"] == ["1 skills"]
