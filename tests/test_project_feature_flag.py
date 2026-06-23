# tests/test_project_feature_flag.py
from src.settings import load_features, DEFAULT_FEATURES


def test_projects_enabled_flag_defaults_to_true():
    """Projects feature is stable — default ON. Operators can disable
    by writing data/features.json with projects_enabled: false."""
    assert DEFAULT_FEATURES["projects_enabled"] is True


def test_projects_enabled_can_be_disabled(tmp_path, monkeypatch):
    """Verify an operator can flip the flag off via data/features.json."""
    monkeypatch.setenv("ODYSSEUS_DATA_DIR", str(tmp_path))
    from core.atomic_io import atomic_write_json
    from src.constants import FEATURES_FILE
    atomic_write_json(FEATURES_FILE, {**DEFAULT_FEATURES, "projects_enabled": False})
    assert load_features()["projects_enabled"] is False
