"""Regression: APIKeyManager.load() must not crash on a corrupt/wrong-shape file.

load() is called during startup (app_initializer). It had no try/except around
`json.load` and called `encrypted_keys.items()` directly, so a corrupt/truncated
api_keys.json raised JSONDecodeError and a legacy list-shaped file raised
AttributeError — both crashing app startup. It now returns {} instead.
"""
from src.api_key_manager import APIKeyManager


def _mgr(tmp_path):
    return APIKeyManager(str(tmp_path))


def test_corrupt_json_returns_empty(tmp_path):
    (tmp_path / "api_keys.json").write_text("{not valid json", encoding="utf-8")
    assert _mgr(tmp_path).load() == {}


def test_list_shape_returns_empty(tmp_path):
    (tmp_path / "api_keys.json").write_text('["openai", "anthropic"]', encoding="utf-8")
    assert _mgr(tmp_path).load() == {}


def test_missing_file_returns_empty(tmp_path):
    assert _mgr(tmp_path).load() == {}


def test_valid_roundtrip(tmp_path):
    mgr = _mgr(tmp_path)
    mgr.save("openai", "sk-secret")
    assert mgr.load() == {"openai": "sk-secret"}
