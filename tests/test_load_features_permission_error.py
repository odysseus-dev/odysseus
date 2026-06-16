"""load_features() must degrade to defaults if features.json is unreadable.

load_settings() already catches PermissionError, but load_features() did not, so
an unreadable data/features.json (e.g. root-owned after a deploy) raised instead
of falling back to DEFAULT_FEATURES, taking down GET /api/auth/features.
"""
import builtins

import src.settings as settings


def test_load_features_degrades_on_permission_error(monkeypatch):
    # Ensure the cache does not short-circuit the read.
    monkeypatch.setattr(settings, "_features_cache", None, raising=False)

    real_open = builtins.open

    def deny(path, *args, **kwargs):
        if str(path) == str(settings.FEATURES_FILE):
            raise PermissionError("denied")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", deny)

    result = settings.load_features()
    assert result == dict(settings.DEFAULT_FEATURES)
