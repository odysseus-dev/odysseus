from src import settings


def test_load_settings_falls_back_for_non_object_json(tmp_path, monkeypatch):
    settings_file = tmp_path / "settings.json"
    settings_file.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(settings, "SETTINGS_FILE", str(settings_file))
    settings._invalidate_caches()

    assert settings.load_settings() == settings.DEFAULT_SETTINGS
    assert settings.is_setting_overridden("default_model") is False


def test_load_features_falls_back_for_non_object_json(tmp_path, monkeypatch):
    features_file = tmp_path / "features.json"
    features_file.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(settings, "FEATURES_FILE", str(features_file))
    settings._invalidate_caches()

    assert settings.load_features() == settings.DEFAULT_FEATURES
