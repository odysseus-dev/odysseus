import json

from src.preset_manager import PresetManager


def test_user_templates_ignore_malformed_store(tmp_path):
    manager = PresetManager(str(tmp_path))
    manager.presets["user_templates"] = "not-a-list"

    assert manager.get_user_templates() == []
    assert manager.save_user_template({"id": "t1", "name": "One"}) is True
    assert manager.get_user_templates() == [{"id": "t1", "name": "One"}]

    saved = json.loads((tmp_path / "presets.json").read_text())
    assert saved["user_templates"] == [{"id": "t1", "name": "One"}]


def test_user_templates_skip_malformed_rows(tmp_path):
    manager = PresetManager(str(tmp_path))
    manager.presets["user_templates"] = [
        {"id": "t1", "name": "One"},
        "bad-row",
        None,
    ]

    assert manager.get_user_templates() == [{"id": "t1", "name": "One"}]
    assert manager.delete_user_template("t1") is True
    assert manager.get_user_templates() == []


def test_save_user_template_rejects_non_dict_template(tmp_path):
    manager = PresetManager(str(tmp_path))

    assert manager.save_user_template("bad-template") is False
