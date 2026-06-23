# tests/test_project_feature_flag.py
from src.settings import load_features, DEFAULT_FEATURES


def test_projects_enabled_flag_defaults_to_false():
    """Projects ship behind a flag and default to OFF (Phase 1 rollout per spec §7)."""
    assert DEFAULT_FEATURES["projects_enabled"] is False


def test_projects_enabled_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("ODYSSEUS_DATA_DIR", str(tmp_path))
    # Touch a fresh features file with projects_enabled flipped on.
    from core.atomic_io import atomic_write_json
    from src.constants import FEATURES_FILE
    atomic_write_json(FEATURES_FILE, {**DEFAULT_FEATURES, "projects_enabled": True})
    assert load_features()["projects_enabled"] is True
