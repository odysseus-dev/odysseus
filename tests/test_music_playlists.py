"""Playlists, ratings and M3U import/export."""
from __future__ import annotations

import json

import pytest

from src import music_playlists


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    import src.constants as constants

    monkeypatch.setattr(constants, "DATA_DIR", tmp_path, raising=False)
    return tmp_path


def test_create_list_get_update_delete(data_dir):
    row = music_playlists.create("Road trip", ["a.flac", "b.flac"])
    assert row["name"] == "Road trip"
    assert row["tracks"] == ["a.flac", "b.flac"]

    listed = music_playlists.list_playlists()
    assert listed == [{"id": row["id"], "name": "Road trip", "count": 2, "updated": row["updated"]}]

    got = music_playlists.get_playlist(row["id"])
    assert got and got["tracks"] == ["a.flac", "b.flac"]

    updated = music_playlists.update(row["id"], name="Roadtrip", tracks=["c.flac"])
    assert updated and updated["name"] == "Roadtrip" and updated["tracks"] == ["c.flac"]

    assert music_playlists.delete(row["id"]) is True
    assert music_playlists.get_playlist(row["id"]) is None
    assert music_playlists.delete(row["id"]) is False


def test_blank_name_keeps_defaults(data_dir):
    row = music_playlists.create("   ")
    assert row["name"] == "Playlist"
    updated = music_playlists.update(row["id"], name="   ")
    assert updated["name"] == "Playlist"
    assert music_playlists.update("missing", name="x") is None


def test_add_tracks_dedupes_and_respects_position(data_dir):
    row = music_playlists.create("Mix", ["b.flac"])
    result = music_playlists.add_tracks(row["id"], ["a.flac", "b.flac", "c.flac"], position=0)
    assert result["tracks"] == ["a.flac", "c.flac", "b.flac"]

    result = music_playlists.add_tracks(row["id"], ["z.flac"], position=99)
    assert result["tracks"][-1] == "z.flac"
    assert music_playlists.add_tracks("missing", ["a.flac"]) is None


def test_remove_track(data_dir):
    row = music_playlists.create("Mix", ["a.flac", "b.flac"])
    result = music_playlists.remove_track(row["id"], "a.flac")
    assert result["tracks"] == ["b.flac"]
    assert music_playlists.remove_track("missing", "a.flac") is None


def test_m3u_import_skips_comments_and_blank_lines(data_dir):
    row = music_playlists.import_m3u("#EXTM3U\n\na.flac\n# comment\nb.flac\n", name="Imported")
    assert row["name"] == "Imported"
    assert row["tracks"] == ["a.flac", "b.flac"]
    exported = music_playlists.export_m3u(row["id"])
    assert exported == "#EXTM3U\na.flac\nb.flac\n"
    assert music_playlists.export_m3u("missing") is None


def test_ratings_clamp_and_delete_on_zero(data_dir):
    assert music_playlists.set_rating("a.flac", 9) == 5
    assert music_playlists.get_ratings() == {"a.flac": 5}
    assert music_playlists.set_rating("a.flac", -2) == 0
    assert music_playlists.get_ratings() == {}


def test_playlist_play_returns_none_when_files_are_gone(data_dir):
    row = music_playlists.create("Gone", ["does-not-exist.flac"])
    assert music_playlists.play(row["id"]) is None
    assert music_playlists.play("missing") is None


def test_instant_mix_is_sorted_and_bounded(data_dir, monkeypatch):
    from src import music_features, music_history

    monkeypatch.setattr(music_features, "load", lambda: {
        "seed.flac": {"bpm": 120},
        "a.flac": {"bpm": 121},
        "b.flac": {"bpm": 80},
    })
    monkeypatch.setattr(music_features, "similarity", lambda a, b: 1.0 if b.get("bpm") == 121 else 0.1)
    monkeypatch.setattr(music_history, "artist_of", lambda rel: "Artist")

    rows = music_playlists.instant_mix("seed.flac", limit=1)
    assert len(rows) == 1
    assert rows[0]["path"] == "a.flac"
    assert rows[0]["score"] == 1.0


def test_corrupt_file_falls_back_to_empty(data_dir):
    (data_dir / "music_playlists.json").write_text("{not json", encoding="utf-8")
    assert music_playlists.list_playlists() == []
