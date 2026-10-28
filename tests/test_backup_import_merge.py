"""Restoring a backup must merge saved templates, not replace them.

``presets.user_templates`` and ``presets.group_presets`` are lists, and
/api/import assigned the backed-up list over the current one. Every character
template or group preset created after the backup was taken disappeared, even
though the endpoint says it merges with existing data.
"""
import asyncio
import json
from unittest.mock import MagicMock

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
