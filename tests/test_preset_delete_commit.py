"""A failed template deletion must leave committed and in-memory state intact."""
from copy import deepcopy
from pathlib import Path

from src.preset_manager import PresetManager


def seeded(tmp_path):
    manager = PresetManager(str(tmp_path))
    assert manager.save_user_template({'id': 'keep', 'name': 'Other persona', 'persona_memory': 'Other continuity'})
    assert manager.save_user_template({'id': 'delete', 'name': 'Socrates', 'persona_memory': 'Saved continuity'})
    return manager


def test_delete_write_failure_preserves_templates_and_same_live_state(tmp_path, monkeypatch):
    manager = seeded(tmp_path)
    live = manager.presets
    before = deepcopy(live)
    original_file = Path(manager.presets_file).read_bytes()

    def fail(*args, **kwargs):
        raise OSError('owned persistence failure')

    monkeypatch.setattr('core.atomic_io.atomic_write_json', fail)
    assert manager.delete_user_template('delete') is False
    assert manager.presets is live and manager.presets == before
    assert manager.get_user_templates() == before['user_templates']
    assert Path(manager.presets_file).read_bytes() == original_file


def test_successful_delete_commits_only_target_and_can_be_reloaded(tmp_path):
    manager = seeded(tmp_path)
    before = deepcopy(manager.presets)
    assert manager.delete_user_template('delete') is True
    after = PresetManager(str(tmp_path)).presets
    assert after == manager.presets
    assert after['user_templates'] == [before['user_templates'][0]]
    assert {k: v for k, v in after.items() if k != 'user_templates'} == {
        k: v for k, v in before.items() if k != 'user_templates'}
