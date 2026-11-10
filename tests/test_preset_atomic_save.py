"""Regression: PresetManager.save() must persist presets atomically.

save() used a plain open("w") + json.dump, which truncates presets.json before
writing the new content. A crash / power loss / serialization error mid-write
leaves the file truncated or empty — the user loses every saved preset. The
save now goes through core.atomic_io.atomic_write_json (tmp file + os.replace),
which the rest of the codebase already uses for JSON state files.
"""
import inspect
import json

from src.preset_manager import PresetManager


class _Unserializable:
    """json.dump cannot serialize this — stands in for a mid-write failure."""


def test_save_uses_atomic_write_json():
    src = inspect.getsource(PresetManager.save)
    assert "atomic_write_json" in src, "save() must persist via atomic_write_json"
    assert "open(" not in src, "save() must not write presets.json with a plain open('w')"


def test_failed_save_does_not_truncate_existing_file(tmp_path):
    mgr = PresetManager(str(tmp_path))
    assert mgr.save({"custom": {"name": "keep"}}) is True
    before = (tmp_path / "presets.json").read_text(encoding="utf-8")

    # A payload that cannot be serialized must not clobber the good file.
    assert mgr.save({"custom": {"obj": _Unserializable()}}) is False

    after = (tmp_path / "presets.json").read_text(encoding="utf-8")
    assert after == before
    assert json.loads(after) == {"custom": {"name": "keep"}}


def test_save_round_trip(tmp_path):
    mgr = PresetManager(str(tmp_path))
    assert mgr.save({"custom": {"name": "X", "temperature": 0.5}}) is True

    reloaded = PresetManager(str(tmp_path))
    assert reloaded.presets["custom"]["name"] == "X"


def test_custom_persona_memory_follows_persona_lifecycle(tmp_path):
    mgr = PresetManager(str(tmp_path))
    assert mgr.save({
        "custom": {
            "name": "Old",
            "character_name": "Old",
            "persona_memory": "- Existing note",
            "enabled": True,
        },
        "user_templates": [
            {
                "id": "p1",
                "name": "Saved",
                "system_prompt": "Be saved.",
                "persona_memory": "- Saved note",
                "persona_memory_schema": "health",
            }
        ],
    })

    assert mgr.update_custom(1.0, 0, "Be old.", name="Old", enabled=True)
    assert mgr.presets["custom"]["persona_memory"] == "- Existing note"

    assert mgr.update_custom(1.0, 0, "Be saved.", name="Saved", enabled=True)
    assert mgr.presets["custom"]["persona_memory"] == "- Saved note"
    assert mgr.presets["custom"]["persona_memory_schema"] == "health"

    assert mgr.update_persona_memory("Saved", "- Updated note")
    assert mgr.presets["custom"]["persona_memory"] == "- Updated note"
    assert mgr.presets["user_templates"][0]["persona_memory"] == "- Updated note"

    assert mgr.update_custom(1.0, 0, "", name="Saved", enabled=False)
    assert mgr.presets["custom"]["persona_memory"] == ""
    assert mgr.presets["custom"]["persona_memory_schema"] == "general"
