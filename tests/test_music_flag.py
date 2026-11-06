"""The music module is opt-in; a stock install does not mount it."""
from src.music_flag import music_enabled


def _clear(monkeypatch):
    monkeypatch.delenv("ODYSSEUS_MUSIC_ENABLED", raising=False)
    monkeypatch.delenv("ODYSSEUS_MUSIC_ROOT", raising=False)


def test_off_without_configuration(monkeypatch):
    _clear(monkeypatch)
    assert music_enabled() is False


def test_library_root_enables_it(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("ODYSSEUS_MUSIC_ROOT", "/srv/music")
    assert music_enabled() is True


def test_explicit_enable_without_a_root(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("ODYSSEUS_MUSIC_ENABLED", "true")
    assert music_enabled() is True


def test_explicit_disable_wins_over_the_root(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("ODYSSEUS_MUSIC_ROOT", "/srv/music")
    monkeypatch.setenv("ODYSSEUS_MUSIC_ENABLED", "0")
    assert music_enabled() is False


def test_unknown_value_falls_back_to_the_root(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("ODYSSEUS_MUSIC_ENABLED", "maybe")
    assert music_enabled() is False
    monkeypatch.setenv("ODYSSEUS_MUSIC_ROOT", "/srv/music")
    assert music_enabled() is True
