import json

from src.preset_manager import PresetManager


def test_non_object_preset_store_falls_back_to_defaults(tmp_path):
    (tmp_path / "presets.json").write_text(json.dumps([]))

    manager = PresetManager(str(tmp_path))

    assert manager.presets == PresetManager.DEFAULT_PRESETS
    assert manager.get("custom")["enabled"] is False
