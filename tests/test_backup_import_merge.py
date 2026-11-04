"""Restoring a backup must merge saved templates, not replace them.

``presets.user_templates`` and ``presets.group_presets`` are lists, and
/api/import assigned the backed-up list over the current one. Every character
template or group preset created after the backup was taken disappeared, even
though the endpoint says it merges with existing data.
"""
import asyncio
import json
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
import routes.backup_routes as br
from src.preset_manager import PresetManager


class _Req:
    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body


def _endpoint(monkeypatch, preset_manager):
    monkeypatch.setattr(br, "require_admin", lambda request: None)
    monkeypatch.setattr(br, "get_current_user", lambda request: "alice")

    mem = MagicMock()
    mem.load_all_for_update.return_value = []
    router = br.setup_backup_routes(mem, preset_manager, MagicMock())
    for r in router.routes:
        if r.path == "/api/import" and "POST" in getattr(r, "methods", set()):
            return r.endpoint
    raise AssertionError("import route not found")


def _template(tid, name):
    return {"id": tid, "name": name, "system_prompt": f"You are {name}."}


def test_restore_keeps_templates_created_after_the_backup(monkeypatch, tmp_path):
    presets = PresetManager(str(tmp_path))
    presets.save_user_template(_template("user-old", "Ada"))
    presets.save_user_template(_template("user-new", "Grace"))
    endpoint = _endpoint(monkeypatch, presets)

    backup = {"presets": {"user_templates": [_template("user-old", "Ada")]}}
    result = asyncio.run(endpoint(_Req(backup)))

    assert result["ok"] is True
    on_disk = json.loads((tmp_path / "presets.json").read_text(encoding="utf-8"))
    assert [t["id"] for t in on_disk["user_templates"]] == ["user-old", "user-new"]


def test_restore_replaces_a_template_with_the_same_id(monkeypatch, tmp_path):
    presets = PresetManager(str(tmp_path))
    presets.save_user_template(_template("user-ada", "Ada (edited)"))
    endpoint = _endpoint(monkeypatch, presets)

    backup = {"presets": {"user_templates": [_template("user-ada", "Ada"), _template("user-bob", "Bob")]}}
    asyncio.run(endpoint(_Req(backup)))

    templates = presets.get_user_templates()
    assert [(t["id"], t["name"]) for t in templates] == [("user-ada", "Ada"), ("user-bob", "Bob")]


def test_restore_keeps_group_presets_created_after_the_backup(monkeypatch, tmp_path):
    presets = PresetManager(str(tmp_path))
    presets.save_group_presets([
        {"id": "grp-1", "name": "Ada & Bob", "participants": []},
        {"id": "grp-2", "name": "Ada & Grace", "participants": []},
    ])
    endpoint = _endpoint(monkeypatch, presets)

    backup = {"presets": {"group_presets": [{"id": "grp-1", "name": "Ada & Bob", "participants": []}]}}
    asyncio.run(endpoint(_Req(backup)))

    assert [g["id"] for g in presets.get_group_presets()] == ["grp-1", "grp-2"]


def test_restore_still_overwrites_the_custom_preset(monkeypatch, tmp_path):
    presets = PresetManager(str(tmp_path))
    presets.update_custom(0.7, 0, "current prompt", name="Ada")
    endpoint = _endpoint(monkeypatch, presets)

    custom = dict(presets.get("custom"), system_prompt="backed-up prompt")
    asyncio.run(endpoint(_Req({"presets": {"custom": custom}})))

    assert presets.get("custom")["system_prompt"] == "backed-up prompt"


@pytest.mark.parametrize("key", ["user_templates", "group_presets"])
@pytest.mark.parametrize("bad_id", [["bad"], {"bad": "id"}, 42, True, None, ""])
def test_invalid_id_is_rejected_before_any_import(monkeypatch, tmp_path, key, bad_id):
    presets = PresetManager(str(tmp_path))
    presets.save_user_template(_template("keep", "Keep"))
    before = (tmp_path / "presets.json").read_bytes()
    monkeypatch.setattr(br, "require_admin", lambda request: None)
    monkeypatch.setattr(br, "get_current_user", lambda request: "alice")
    memories, skills = MagicMock(), MagicMock()
    app = FastAPI()
    app.include_router(br.setup_backup_routes(memories, presets, skills))

    with TestClient(app) as client:
        response = client.post("/api/import", json={
            "memories": [{"text": "Must not be imported"}],
            "skills": [{"name": "must-not-be-imported"}],
            "presets": {key: [{"id": bad_id, "name": "Malformed"}]},
        })

    assert response.status_code == 400
    assert key in response.json()["detail"]
    memories.save.assert_not_called()
    skills.add_skill.assert_not_called()
    assert (tmp_path / "presets.json").read_bytes() == before


@pytest.mark.parametrize("key", ["user_templates", "group_presets"])
def test_duplicate_saved_ids_are_reconciled_idempotently(monkeypatch, tmp_path, key):
    presets = PresetManager(str(tmp_path))
    saved = dict(presets.get_all())
    saved[key] = [
        _template("x", "Stale"), _template("keep", "Unrelated"),
        _template("x", "Current"), _template("untouched", "Old"),
        _template("untouched", "Last saved"),
    ]
    presets.save(saved)
    endpoint = _endpoint(monkeypatch, presets)
    backup = {"presets": {key: [_template("x", "Restored"), _template("new", "New")]}}

    asyncio.run(endpoint(_Req(backup)))
    first = (tmp_path / "presets.json").read_bytes()
    asyncio.run(endpoint(_Req(backup)))

    assert (tmp_path / "presets.json").read_bytes() == first
    restored = PresetManager(str(tmp_path)).get_all()[key]
    assert [(item["id"], item["name"]) for item in restored] == [
        ("x", "Restored"), ("keep", "Unrelated"),
        ("untouched", "Last saved"), ("new", "New"),
    ]


def test_malformed_saved_ids_do_not_break_a_valid_restore():
    legacy = [{"id": ["legacy"]}, {"name": "Without ID"}]
    assert br._merge_by_id(legacy, [_template("valid", "Valid")]) == [
        *legacy, _template("valid", "Valid"),
    ]
