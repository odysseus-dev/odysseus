"""Static guards for the music UI bundle.

The playlists tab used to call ``_pollStatus`` — an undefined name. Optional
chaining does not save an undeclared identifier, so the click threw before the
refresh. A source check is enough to keep that class of typo out.
"""
from pathlib import Path


def _source() -> str:
    return (Path(__file__).resolve().parents[1] / "static" / "js" / "music.js").read_text(
        encoding="utf-8"
    )


def test_no_undefined_poll_status_call():
    assert "_pollStatus" not in _source()


def test_playlist_play_refreshes_through_the_exported_helper():
    source = _source()
    marker = "/play`, {method:'POST'})"
    index = source.find(marker)
    assert index != -1
    assert "refreshStatus()" in source[index:index + 120]
