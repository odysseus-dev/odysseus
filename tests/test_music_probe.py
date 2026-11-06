"""Metadata probe: mutagen fallback and the optional native binary."""
import json
import shutil
import subprocess
import wave

import pytest

from src import music_probe


def _wav(path, seconds=0.2, rate=8000):
    with wave.open(str(path), "w") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x00\x00" * int(rate * seconds))


def test_native_binary_is_optional(monkeypatch):
    monkeypatch.delenv("ODYSSEUS_MUSIC_PROBE", raising=False)
    value = music_probe.native_binary()
    assert value is None or value.endswith("music_probe")


def test_mutagen_fallback_infers_title_and_audio_info(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    _wav(root / "Aster - Midnight Drive.wav")
    rows = music_probe.probe_mutagen(root)
    assert len(rows) == 1
    row = rows[0]
    assert row["path"] == "Aster - Midnight Drive.wav"
    assert row["title"] == "Aster - Midnight Drive"
    assert row["sample_rate"] == 8000
    assert row["channels"] == 1
    assert 0.1 < row["duration_sec"] < 0.4
    assert "cover" not in row


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg needed to build a FLAC fixture")
def test_native_and_fallback_agree_on_tags_and_cover(tmp_path, monkeypatch):
    from mutagen.flac import FLAC, Picture

    root = tmp_path / "library"
    root.mkdir()
    wav = root / "source.wav"
    _wav(wav, seconds=0.3, rate=44100)
    flac = root / "tagged.flac"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(wav), str(flac)], check=True)
    wav.unlink()
    audio = FLAC(flac)
    audio["title"] = "Near Light"
    audio["artist"] = "Vela"
    audio["album"] = "Glasshouse"
    picture = Picture()
    picture.type = 3
    picture.mime = "image/png"
    picture.data = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    audio.add_picture(picture)
    audio.save()

    covers = tmp_path / "covers"
    rows = music_probe.probe_library(root, covers=covers)
    assert len(rows) == 1
    row = rows[0]
    assert row["title"] == "Near Light"
    assert row["artist"] == "Vela"
    assert row["album"] == "Glasshouse"
    assert row["cover"]
    assert (covers / row["cover"]).is_file()

    binary = music_probe.native_binary()
    if binary:
        native = music_probe.probe_native(root, covers=tmp_path / "covers-native")
        assert native and native[0]["title"] == "Near Light"
        assert native[0]["artist"] == "Vela"
        assert native[0]["cover"]
