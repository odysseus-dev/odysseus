"""Error-path tests for src/settings.py load_settings().

Covers the fallback-to-defaults behaviour when the settings file is
missing, corrupt, or unreadable — including the PermissionError case
that was previously uncaught and would crash the app.
"""

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

_TMP = Path(tempfile.mkdtemp(prefix="odysseus-settings-test-"))
os.environ.setdefault("DATA_DIR", str(_TMP))
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TMP / 'app.db'}")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _fresh_load(settings_path, content=None):
    """Write content to settings_path, clear cache, and call load_settings()."""
    import src.settings as s

    if content is not None:
        settings_path.write_text(content, encoding="utf-8")

    # Force cache invalidation so each test reads fresh from disk.
    s._settings_cache = None
    with patch.object(s, "SETTINGS_FILE", str(settings_path)):
        return s.load_settings()


def test_missing_file_returns_defaults(tmp_path):
    """FileNotFoundError → defaults, no crash."""
    import src.settings as s
    missing = tmp_path / "nonexistent_settings.json"
    s._settings_cache = None
    with patch.object(s, "SETTINGS_FILE", str(missing)):
        result = s.load_settings()
    assert isinstance(result, dict)
    assert result == {**s.DEFAULT_SETTINGS, **result}  # superset of defaults


def test_corrupted_json_returns_defaults(tmp_path):
    """Invalid JSON → defaults, no crash."""
    result = _fresh_load(tmp_path / "settings.json", content="{not valid json")
    import src.settings as s
    assert result == {**s.DEFAULT_SETTINGS, **result}


def test_wrong_type_returns_defaults(tmp_path):
    """JSON array instead of object → defaults, no crash."""
    result = _fresh_load(tmp_path / "settings.json", content="[1, 2, 3]")
    import src.settings as s
    assert result == {**s.DEFAULT_SETTINGS, **result}


def test_permission_error_returns_defaults(tmp_path):
    """PermissionError on unreadable file → defaults, no crash.

    Pre-fix: PermissionError was not in the except tuple, so it would
    propagate and crash any code path that calls load_settings() at
    startup or request time.
    """
    import src.settings as s
    settings_path = tmp_path / "settings.json"
    settings_path.write_text('{"theme": "dark"}', encoding="utf-8")

    s._settings_cache = None
    with patch.object(s, "SETTINGS_FILE", str(settings_path)):
        # Simulate unreadable file by patching open() to raise PermissionError.
        with patch("builtins.open", side_effect=PermissionError("Permission denied")):
            result = s.load_settings()

    assert isinstance(result, dict), "Should return defaults dict, not raise"
    assert result == {**s.DEFAULT_SETTINGS, **result}


def test_valid_settings_merged_with_defaults(tmp_path):
    """Valid file → custom values merged over defaults."""
    import src.settings as s
    result = _fresh_load(
        tmp_path / "settings.json",
        content=json.dumps({"theme": "dark", "web_search_enabled": True}),
    )
    assert result["theme"] == "dark"
    assert result["web_search_enabled"] is True
    # Defaults still present for keys not in file.
    for key in s.DEFAULT_SETTINGS:
        assert key in result
