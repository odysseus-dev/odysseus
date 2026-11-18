"""Hermetic tests for local music service (isolated in-memory MPV, zero audio emissions)."""

import asyncio
import re

import pytest

from services.music.music_service import browse, do_music_control, get_status, search


class MockMpv:
    def __init__(self):
        from pathlib import Path
        self.props = {
            "pause": True,
            "eof-reached": False,
            "idle-active": False,
            "volume": 100.0,
            "mute": False,
            "path": "/library/Music/sample_track.flac",
            "filename": "sample_track.flac",
            "media-title": "sample_track",
            "time-pos": 15.0,
            "duration": 240.0,
            "chapter": 0,
            "chapters": 0,
            "chapter-list": [],
            "playlist-pos": 0,
            "playlist-count": 1,
            "audio-bitrate": 320000,
        }
        self.playlist = ["/library/Music/sample_track.flac"]

    def command(self, *args, **kwargs):
        from pathlib import Path
        if not args:
            return None
        cmd = args[0]
        if cmd == "get_property":
            prop = args[1]
            return self.props.get(prop)
        if cmd == "set_property":
            prop, val = args[1], args[2]
            self.props[prop] = val
            if prop == "playlist-pos":
                idx = int(val)
                if 0 <= idx < len(self.playlist):
                    p = self.playlist[idx]
                    self.props["path"] = p
                    self.props["filename"] = Path(p).name
                    self.props["media-title"] = Path(p).stem
            return {"error": "success"}
        if cmd == "cycle":
            prop = args[1]
            if prop == "pause":
                self.props["pause"] = not bool(self.props.get("pause", False))
            elif prop == "mute":
                self.props["mute"] = not bool(self.props.get("mute", False))
            return {"error": "success"}
        if cmd == "loadfile":
            path = str(args[1])
            mode = args[2] if len(args) > 2 else "replace"
            if mode == "replace":
                self.playlist = [path]
                self.props["path"] = path
                self.props["filename"] = Path(path).name
                self.props["media-title"] = Path(path).stem
                self.props["playlist-pos"] = 0
            else:
                self.playlist.append(path)
            self.props["playlist-count"] = len(self.playlist)
            self.props["eof-reached"] = False
            self.props["pause"] = False
            return {"error": "success"}
        if cmd == "playlist-clear":
            self.playlist = []
            self.props["playlist-count"] = 0
            return {"error": "success"}
        if cmd == "stop":
            self.props["path"] = ""
            self.props["filename"] = ""
            self.props["media-title"] = ""
            self.props["pause"] = True
            return {"error": "success"}
        return {"error": "success"}


@pytest.fixture(autouse=True)
def mock_mpv_environment(monkeypatch):
    """Ensure tests NEVER talk to live mpv socket or play audio on real hardware."""
    from services.music import music_service as ms

    mock = MockMpv()
    monkeypatch.setattr(ms, "_mpv_responding", lambda: True)
    monkeypatch.setattr(ms, "ensure_mpv", lambda: None)
    monkeypatch.setattr(ms, "_playerctl_available", lambda: False)
    monkeypatch.setattr(ms, "_ipc_command", mock.command)
    yield mock


@pytest.fixture(autouse=True)
def isolated_music_library(tmp_path, monkeypatch):
    """Give the suite a tiny, valid library instead of the operator's real one."""
    import wave

    from services.music import music_service as ms

    root = tmp_path / "Music"
    files = [
        "Artist/Album/01 - Sample Track.wav",
        "Artist/Album/02 - Second Track.wav",
        "Unsorted/loose track.wav",
    ]
    for rel in files:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), "wb") as fh:
            fh.setnchannels(1)
            fh.setsampwidth(2)
            fh.setframerate(8000)
            fh.writeframes(b"\x00\x00" * 800)  # 0.1 s of silence
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(ms, "MUSIC_ROOT", root)
    monkeypatch.setattr(ms, "_DATA_DIR", data_dir)
    ms._invalidate_library_cache()
    ms._queue_tracks = []
    ms._queue_index = -1
    ms._logical_queue = []
    ms._logical_queue_index = -1
    ms._active_logical = None
    yield root


def _browse_entries():
    data = browse()
    return [e for e in data.get("entries", []) if e.get("kind") in ("dir", "file")]


def _first_entry():
    # Las entradas virtuales (⭐ Favoritos) no son archivos buscables por nombre.
    entries = [e for e in _browse_entries() if not e.get("is_virtual")]
    if not entries:
        pytest.skip("Biblioteca vacía")
    return entries[0]


def _search_token(name: str) -> str:
    tokens = [t for t in re.split(r"[\W_]+", name or "") if len(t) >= 3]
    if tokens:
        return tokens[0][:16]
    text = (name or "").strip()
    if len(text) >= 3:
        return text[:16]
    pytest.skip("Entrada sin texto buscable")


def _group_tracks_by_folder():
    from pathlib import Path

    from services.music import music_service as ms

    groups: dict[str, list] = {}
    root = ms.MUSIC_ROOT.resolve()
    for path in ms._library_tracks():
        try:
            rel = path.relative_to(root)
        except ValueError:
            continue
        folder_key = "." if rel.parent == Path(".") else str(rel.parent)
        groups.setdefault(folder_key, []).append(path)
    return groups


def _first_folder_key(*, min_files: int = 2) -> str:
    groups = _group_tracks_by_folder()
    options = [k for k, files in groups.items() if len(files) >= min_files]
    if not options:
        pytest.skip(f"Sin carpeta con >={min_files} pistas")
    return options[0]


def _path_with_chapters(*, min_count: int = 10):
    from services.music import music_service as ms

    ms._load_chapters_disk_cache()
    root = ms.MUSIC_ROOT.resolve()
    for rel, entry in ms._chapters_disk_cache.items():
        if len(entry.get("chapters", [])) >= min_count:
            try:
                p = ms._resolve_under_root(rel)
                if p.is_file():
                    return str(p.relative_to(root)), entry["chapters"]
            except Exception:
                continue
    sample = ms._library_tracks()[:50]
    for path in sample:
        rel = str(path.relative_to(root))
        chapters = ms.get_chapters_for_path(rel)
        if len(chapters) >= min_count:
            return rel, chapters
    pytest.skip(f"Ningún archivo con >={min_count} capítulos disponible")


def test_browse_root_has_entries():
    data = browse()
    assert data["path"] == "."
    assert len(data["entries"]) > 0


def test_search_finds_random_library_hit():
    entry = _first_entry()
    token = _search_token(entry.get("name") or entry.get("path") or "")
    hits = search(token)["results"]
    assert hits
    needle = token.lower()
    assert any(
        needle in (h.get("name") or "").lower()
        or needle in (h.get("path") or "").lower()
        or needle in (h.get("folder") or "").lower()
        for h in hits
    )


def test_music_control_status():
    result = asyncio.run(do_music_control("status"))
    assert "status" in result
    assert result["status"]["library_root"].endswith("/Music")


def test_get_status_shape():
    st = get_status()
    assert "state" in st
    assert "library_root" in st
    assert "volume" in st
    assert "seekable" in st
    assert "chapter_count" in st
    assert "logical_track" in st
    assert "music_prefs" in st
    assert "audio_quality" in st
    assert "quality_label" in st["audio_quality"]
    assert "eof_reached" in st


def test_quality_label_prefers_hz_bitrate_not_stereo():
    from services.music.music_service import _quality_label_from_aq

    label = _quality_label_from_aq(
        {
            "codec": "MP3 (MPEG AUDIO LAYER 3)",
            "samplerate_hz": 44100,
            "channels": 2,
            "bitrate_kbps": 320,
            "bits_per_sample": 0,
        }
    )
    assert "44 kHz" in label
    assert "320 kbps" in label
    assert "MP3" in label
    assert "stereo" not in label.lower()
    assert "MPEG AUDIO" not in label

    flac = _quality_label_from_aq(
        {
            "codec": "flac",
            "samplerate_hz": 48000,
            "channels": 2,
            "bitrate_kbps": 1050,
            "bits_per_sample": 24,
        }
    )
    assert "48 kHz" in flac
    assert "24-bit" in flac
    assert "stereo" not in flac.lower()


def test_shuffle_builds_logical_queue():
    from services.music import music_service as ms

    ms._set_shuffle_mode(True)
    pool = ms._build_logical_pool()
    if len(pool) < 2:
        pytest.skip("Pool lógico demasiado pequeño")
    ms._rebuild_shuffle_queue(current=pool[0])
    q = ms.get_queue_state()
    assert len(q["queue"]) == len(pool)
    assert q["shuffle"] is True


def test_delete_rejects_escape():
    from services.music.music_service import delete_file

    with pytest.raises(PermissionError):
        delete_file("../outside")


def test_delete_rejects_directory():
    from services.music.music_service import delete_file

    with pytest.raises(IsADirectoryError):
        delete_file(".")


def test_service_start_resume_skips_random(monkeypatch):
    from services.music import music_service as ms

    # El arranque está apagado en tests por defecto; este test lo ejercita con mpv falso.
    monkeypatch.setenv("ODYSSEUS_MUSIC_RUNTIME", "1")
    monkeypatch.setattr(ms, "start_auto_advance_watcher", lambda: None)
    ms._odysseus_start_handled = False
    ms._music_prefs["service_start_mode"] = "resume_last"

    def boom(**_kw):
        raise AssertionError("should not pick random")

    monkeypatch.setattr(ms, "_pick_random_logical", boom)
    ms.maybe_apply_service_start()
    assert ms._odysseus_start_handled is True


def test_service_start_random_never_autoplays(monkeypatch):
    from services.music import music_service as ms

    monkeypatch.setattr(ms, "_odysseus_start_handled", False)
    monkeypatch.setitem(ms._music_prefs, "service_start_mode", "random_library")
    played = []
    monkeypatch.setattr(ms, "_play_logical_track", lambda lt: played.append(lt))
    ms.maybe_apply_service_start()
    assert not played


def test_get_quality_check_shape():
    from services.music import music_service as ms

    qc = ms.get_quality_check()
    assert qc["state"] in {"ok", "resampling", "unknown"}
    assert "message" in qc


def test_load_prefs_does_not_reset_shuffle_on_every_request(monkeypatch):
    from services.music import music_service as ms

    ms._music_session_initialized = False
    ms._odysseus_start_handled = True
    ms._shuffle_library = False
    ms._music_prefs = {**ms.DEFAULT_MUSIC_PREFS, "shuffle_enabled": True}

    monkeypatch.setattr(
        "routes.prefs_routes._load_for_user",
        lambda _user: {"music": {"shuffle_enabled": True}},
    )

    ms.load_music_prefs_for_user("tester")
    assert ms._shuffle_library is True
    ms._shuffle_library = False
    ms.load_music_prefs_for_user("tester")
    assert ms._shuffle_library is False


def test_chapters_on_multitrack_file():
    from services.music import music_service as ms

    rel, chapters = _path_with_chapters(min_count=10)
    again = ms.get_chapters_for_path(rel)
    assert len(again) >= 10
    assert "title" in again[0]
    assert again[0]["start_sec"] == 0.0
    assert len(again) == len(chapters)


def test_build_logical_pool_includes_chapters():
    from services.music import music_service as ms

    rel, _ = _path_with_chapters(min_count=10)
    ms._music_prefs["shuffle_chapters"] = True
    pool = ms._build_logical_pool()
    stem = rel.split("/")[-1]
    hits = [p for p in pool if p.get("path") == rel or stem in (p.get("path") or "")]
    assert len(hits) >= 10
    assert any(p.get("chapter_index") is not None for p in hits)


def test_shuffle_picks_distinct_logical_tracks():
    from services.music import music_service as ms

    ms._set_shuffle_mode(True)
    ms._music_prefs["shuffle_chapters"] = True
    pool = ms._build_logical_pool()
    if len(pool) < 2:
        pytest.skip("Biblioteca demasiado pequeña para shuffle distinto")
    first = ms._pick_random_logical()
    second = ms._pick_random_logical(exclude=first)
    assert first != second or len(pool) == 1
    assert first.get("path") or second.get("path")


def test_control_play_pause_cycle():
    from services.music import music_service as ms
    from services.music.music_service import control, play_query

    folder = _first_folder_key(min_files=1)
    query = folder if folder != "." else _first_entry()["name"]
    play_query(query, shuffle=False)
    st = control("play-pause")
    assert st["status"]["state"] in {"paused", "playing", "stopped"}
    st2 = control("next")
    assert "status" in st2
    ms._set_shuffle_mode(False)


def test_shuffle_library_mode():
    from services.music import music_service as ms

    if len(ms._library_tracks()) < 2:
        pytest.skip("Se necesitan al menos 2 pistas para next distinto")

    ms._set_shuffle_mode(True)
    st = ms.get_status()
    assert st["shuffle"] is True

    first = ms.control("next")["status"]["filename"]
    second = ms.control("next")["status"]["filename"]
    assert first
    assert second
    assert first != second or len(ms._library_tracks()) == 1

    ms._set_shuffle_mode(False)
    assert ms.get_status()["shuffle"] is False


def test_sequential_next_single_file_expands_folder():
    from services.music import music_service as ms

    folder = _first_folder_key(min_files=2)
    target = ms._resolve_under_root(folder if folder != "." else "")
    tracks, _ = ms._tracks_for_target(target)
    if len(tracks) < 2:
        pytest.skip("Carpeta sin suficientes pistas")
    single = tracks[0]
    ms._set_shuffle_mode(False)
    ms.play_path(str(single.relative_to(ms.MUSIC_ROOT.resolve())), shuffle=False)
    assert ms.get_status()["queue_size"] == len(tracks)

    before = ms.get_status()["filename"]
    ms.control("next")
    after = ms.get_status()["filename"]
    assert before != after


def test_sequential_next_wraps_at_end():
    from services.music import music_service as ms

    folder = _first_folder_key(min_files=3)
    target = ms._resolve_under_root(folder if folder != "." else "")
    tracks = ms._collect_audio_files(target)[:3]
    if len(tracks) < 3:
        pytest.skip("Carpeta con menos de 3 pistas")
    ms._set_shuffle_mode(False)
    ms._set_sequential_queue(tracks, start_index=0)
    ms.ensure_mpv()
    ms._load_queue_from_index(2)
    first = ms._queue_tracks[ms._queue_index].name
    ms.control("next")
    wrapped = ms._queue_tracks[ms._queue_index].name
    assert first != wrapped


def test_backend_auto_advance_tick_advances_on_eof(mock_mpv_environment):
    from services.music import music_service as ms

    folder = _first_folder_key(min_files=2)
    target = ms._resolve_under_root(folder if folder != "." else "")
    tracks, _ = ms._tracks_for_target(target)
    if len(tracks) < 2:
        pytest.skip("Carpeta sin suficientes pistas")

    ms._set_shuffle_mode(False)
    ms.play_path(str(tracks[0].relative_to(ms.MUSIC_ROOT.resolve())), shuffle=False)

    # Initial state: actively playing, not at EOF
    mock_mpv_environment.props["pause"] = False
    mock_mpv_environment.props["eof-reached"] = False
    ms._auto_advance_tick()
    assert ms._was_actively_playing is True

    # EOF reached
    first_track = ms.get_status()["filename"]
    mock_mpv_environment.props["eof-reached"] = True
    ms._auto_advance_tick()

    # Track should have advanced!
    second_track = ms.get_status()["filename"]
    assert second_track != first_track


def test_get_status_includes_path_and_relative_path(tmp_path, monkeypatch, mock_mpv_environment):
    from services.music import music_service as ms

    track = tmp_path / "Sub" / "test_track.flac"
    track.parent.mkdir(parents=True, exist_ok=True)
    track.write_bytes(b"audio")

    monkeypatch.setattr(ms, "MUSIC_ROOT", tmp_path)
    mock_mpv_environment.props["path"] = str(track)
    mock_mpv_environment.props["filename"] = track.name

    st = ms.get_status()
    assert "path" in st
    assert "relative_path" in st
    assert st["path"] == "Sub/test_track.flac"
    assert st["relative_path"] == "Sub/test_track.flac"


def test_resolve_audio_file_and_delete_in_subdirectory(tmp_path, monkeypatch, mock_mpv_environment):
    from services.music import music_service as ms

    # Set up mock music library in tmp_path
    sub = tmp_path / "Unsorted"
    sub.mkdir(parents=True, exist_ok=True)
    fake_track = sub / "track_to_delete.opus"
    fake_track.write_bytes(b"dummy audio content")

    monkeypatch.setattr(ms, "MUSIC_ROOT", tmp_path)
    ms._invalidate_library_cache()

    # 1. Test resolution directly with subdirectory path
    resolved = ms._resolve_audio_file("Unsorted/track_to_delete.opus")
    assert resolved == fake_track.resolve()

    # 2. Test fallback by base filename when track is currently playing in mpv
    mock_mpv_environment.props["path"] = str(fake_track)
    mock_mpv_environment.props["filename"] = fake_track.name
    resolved_fallback = ms._resolve_audio_file("track_to_delete.opus")
    assert resolved_fallback == fake_track.resolve()

    # 3. Test delete_file using bare filename
    res = ms.delete_file("track_to_delete.opus")
    assert res["ok"] is True
    assert res["path"] == "Unsorted/track_to_delete.opus"
    assert not fake_track.exists()


def test_resolve_audio_file_exceptions(tmp_path, monkeypatch):
    from services.music import music_service as ms

    sub = tmp_path / "Sub"
    sub.mkdir(parents=True, exist_ok=True)
    text_file = sub / "notes.txt"
    text_file.write_text("hello")

    monkeypatch.setattr(ms, "MUSIC_ROOT", tmp_path)

    # Directory
    with pytest.raises(IsADirectoryError):
        ms._resolve_audio_file("Sub")

    # Non-audio file
    with pytest.raises(ValueError, match="Not an audio file"):
        ms._resolve_audio_file("Sub/notes.txt")

    # Non-existent file
    with pytest.raises(FileNotFoundError):
        ms._resolve_audio_file("Sub/ghost.opus")

    # Path traversal outside library
    with pytest.raises(PermissionError):
        ms._resolve_audio_file("../outside.opus")
    ms._invalidate_library_cache()


def test_mpv_command_args_uses_weak_gapless():
    from services.music import music_service as ms

    args = ms._mpv_launch_cmd()
    assert "--gapless-audio=weak" in args
    assert "--gapless-audio=yes" not in args


def test_quality_check_detects_out_params(mock_mpv_environment):
    from services.music import music_service as ms

    mock_mpv_environment.props["audio-out-params"] = {
        "samplerate": 44100,
        "format": "s16",
    }
    qc = ms.get_quality_check()
    assert qc["state"] in {"ok", "unknown", "resampling"}


def test_play_path_with_shuffle_plays_selected_file(mock_mpv_environment):
    from services.music import music_service as ms

    ms._invalidate_library_cache()
    folder = _first_folder_key(min_files=2)
    target = ms._resolve_under_root(folder if folder != "." else "")
    tracks, _ = ms._tracks_for_target(target)
    if len(tracks) < 2:
        pytest.skip("Carpeta sin suficientes pistas")

    chosen = tracks[1]
    rel = str(chosen.relative_to(ms.MUSIC_ROOT.resolve()))
    res = ms.play_path(rel, shuffle=True)
    assert res["action"] == "play"
    assert res["status"]["filename"] == chosen.name
    assert ms.get_status()["filename"] == chosen.name
    assert ms._shuffle_library is True


def test_equalizer_get_and_set():
    from services.music import music_service as ms

    eq = ms.get_equalizer()
    assert len(eq["frequencies"]) == 10
    assert len(eq["bands"]) == 10
    assert "flat" in eq["presets"]

    updated = ms.set_equalizer(preset="rock")
    assert updated["preset"] == "rock"
    assert updated["bands"] == ms.EQ_PRESETS["rock"]

    custom = ms.set_equalizer(bands=[1.0, 2.0, 3.0, 0.0, 0.0, 0.0, 0.0, -1.0, -2.0, -3.0])
    assert custom["preset"] == "custom"
    assert custom["bands"] == [1.0, 2.0, 3.0, 0.0, 0.0, 0.0, 0.0, -1.0, -2.0, -3.0]

    ms.set_equalizer(preset="flat")


def test_lyrics_structure_with_empty_or_clean_query():
    from services.music import music_service as ms

    res = ms.get_lyrics(artist="UnknownNonExistentArtist12345", title="NonExistentTitle98765")
    assert "synced" in res
    assert "lines" in res
    assert "plain" in res


def test_pipewire_clock_rate_switching(monkeypatch):
    from services.music import music_service as ms

    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        import subprocess
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(ms.subprocess, "run", fake_run)

    ms._set_pipewire_clock_rate(96000)
    assert len(calls) == 1
    assert calls[0] == ["pw-metadata", "-n", "settings", "0", "clock.force-rate", "96000"]

    ms._set_pipewire_clock_rate(32000)  # Fuera de rango
    assert len(calls) == 1

    ms._reset_pipewire_clock_rate()
    assert len(calls) == 2
    assert calls[1] == ["pw-metadata", "-n", "settings", "0", "clock.force-rate", "0"]


def test_audio_quality_bitrate_in_label(tmp_path):
    from services.music import music_service as ms

    aq = {
        "bits_per_sample": 24,
        "samplerate_hz": 96000,
        "bitrate_kbps": 2884,
        "codec": "FLAC",
    }
    label = ms._quality_label_from_aq(aq)
    assert "24-bit" in label
    assert "96 kHz" in label
    assert "2884 kbps" in label
    assert "FLAC" in label

    lossy_aq = {
        "bits_per_sample": 0,
        "samplerate_hz": 44100,
        "bitrate_kbps": 320,
        "codec": "MP3",
    }
    lossy_label = ms._quality_label_from_aq(lossy_aq)
    assert "44 kHz" in lossy_label
    assert "320 kbps" in lossy_label
    assert "MP3" in lossy_label


def test_list_flat_pagination():
    from services.music import music_service as ms

    flat = ms.list_flat(offset=0, limit=10, sort="folder")
    assert "total" in flat
    assert "tracks" in flat
    assert len(flat["tracks"]) <= 10
    if flat["tracks"]:
        first = flat["tracks"][0]
        assert "name" in first
        assert "path" in first
        assert "folder" in first
        assert "audio_quality" in first


def test_queue_scope_filtering_and_lifecycle():
    from services.music import music_service as ms

    all_tracks = ms._build_logical_pool(scope=None)
    assert len(all_tracks) > 0

    # Pick a folder that exists in library
    folder = _first_folder_key(min_files=2)
    if folder == ".":
        pytest.skip("No subfolders available for testing")

    ms.set_queue_scope(folder, rebuild_queue=False)
    assert ms.get_queue_scope() == folder
    status = ms.get_status()
    assert status["queue_scope"] == folder

    scoped_pool = ms._build_logical_pool(scope=folder)
    assert len(scoped_pool) > 0
    assert len(scoped_pool) <= len(all_tracks)
    for t in scoped_pool:
        assert t["path"].startswith(f"{folder}/") or t["path"] == folder

    # Reset scope
    ms.set_queue_scope(None, rebuild_queue=False)
    assert ms.get_queue_scope() is None
    cleared_pool = ms._build_logical_pool()
    assert len(cleared_pool) == len(all_tracks)


def test_list_flat_sorting_modes():
    from services.music import music_service as ms

    # Quality sorting
    flat_q_desc = ms.list_flat(offset=0, limit=20, sort="quality_desc")
    assert "tracks" in flat_q_desc
    assert len(flat_q_desc["tracks"]) > 0

    flat_q_asc = ms.list_flat(offset=0, limit=20, sort="quality_asc")
    assert "tracks" in flat_q_asc
    assert len(flat_q_asc["tracks"]) > 0

    # Name sorting
    flat_name_asc = ms.list_flat(offset=0, limit=10, sort="name_asc")
    names_asc = [t["name"].lower() for t in flat_name_asc["tracks"]]
    assert names_asc == sorted(names_asc)

    flat_name_desc = ms.list_flat(offset=0, limit=10, sort="name_desc")
    names_desc = [t["name"].lower() for t in flat_name_desc["tracks"]]
    assert names_desc == sorted(names_desc, reverse=True)

    # Play count sorting
    flat_plays = ms.list_flat(offset=0, limit=10, sort="plays_desc")
    assert "tracks" in flat_plays
    for t in flat_plays["tracks"]:
        assert "play_count" in t


def test_fast_audio_parsers(tmp_path):
    from services.music import music_service as ms

    # 1. Opus parser test
    opus_file = tmp_path / "test.opus"
    # Construct minimal OpusHead packet
    # magic: OpusHead (8B), ver: 1 (1B), ch: 2 (1B), pre-skip: 0 (2B), sr: 48000 (4B little endian)
    head_data = b"OggS" + b"\x00" * 24 + b"OpusHead\x01\x02\x00\x00\x80\xbb\x00\x00\x00\x00\x00"
    opus_file.write_bytes(head_data)
    opus_res = ms._parse_opus_header(opus_file)
    assert opus_res is not None
    assert opus_res["codec"] == "OPUS"
    assert opus_res["samplerate_hz"] == 48000
    assert opus_res["channels"] == 2
    assert "48 kHz" in opus_res["quality_label"]
    assert opus_res["lossless"] is False

    # 2. MP3 parser test
    mp3_file = tmp_path / "test.mp3"
    # MPEG 1 Layer 3: 0xFF, 0xFB (sync 11 bits + MPEG1 Layer3 no CRC),
    # 0x90 (bitrate idx 9 = 128 kbps, sr idx 0 = 44100 Hz, no padding),
    # 0x00 (stereo)
    mp3_data = b"\xff\xfb\x90\x00" + b"\x00" * 200
    mp3_file.write_bytes(mp3_data)
    mp3_res = ms._parse_mp3_header(mp3_file)
    assert mp3_res is not None
    assert mp3_res["codec"] == "MP3"
    assert mp3_res["bitrate_kbps"] == 128
    assert mp3_res["samplerate_hz"] == 44100
    assert "128 kbps" in mp3_res["quality_label"]
    assert mp3_res["lossless"] is False

    # 3. WAV parser test
    wav_file = tmp_path / "test.wav"
    # RIFF header + fmt chunk (16 bytes): PCM format 1, 2 channels, 96000 Hz, 576000 bytes/sec, 6 block align, 24 bits
    fmt_chunk = (
        b"fmt \x10\x00\x00\x00"  # chunk id and size (16)
        b"\x01\x00"              # PCM
        b"\x02\x00"              # 2 channels
        b"\x00\x77\x01\x00"      # 96000 Hz
        b"\x00\xca\x08\x00"      # byte rate
        b"\x06\x00"              # block align
        b"\x18\x00"              # 24 bits
    )
    wav_data = b"RIFF" + (36).to_bytes(4, "little") + b"WAVE" + fmt_chunk + b"data\x00\x00\x00\x00"
    wav_file.write_bytes(wav_data)
    wav_res = ms._parse_wav_header(wav_file)
    assert wav_res is not None
    assert wav_res["codec"] == "WAV"
    assert wav_res["samplerate_hz"] == 96000
    assert wav_res["bits_per_sample"] == 24
    assert wav_res["is_hires"] is True
    assert wav_res["lossless"] is True


def test_quality_cache_disk_roundtrip(tmp_path, monkeypatch):
    from services.music import music_service as ms

    cache_file = tmp_path / "test_quality_cache.json"
    monkeypatch.setattr(ms, "_MUSIC_QUALITY_CACHE_FILE", cache_file)
    monkeypatch.setattr(ms, "_DATA_DIR", tmp_path)

    test_key = "test_track:100:200:v4"
    test_entry = {
        "codec": "FLAC",
        "samplerate_hz": 192000,
        "bits_per_sample": 24,
        "is_hires": True,
        "lossless": True,
    }

    ms._AUDIO_QUALITY_CACHE[test_key] = test_entry
    ms._quality_cache_dirty = True
    ms._save_quality_cache()

    assert cache_file.is_file()
    assert ms._quality_cache_dirty is False

    # Clear memory cache and reload
    ms._AUDIO_QUALITY_CACHE.clear()
    ms._load_quality_cache()
    assert test_key in ms._AUDIO_QUALITY_CACHE
    assert ms._AUDIO_QUALITY_CACHE[test_key]["samplerate_hz"] == 192000


def test_favorites_lifecycle_and_virtual_browse(tmp_path, monkeypatch):
    from services.music import music_service as ms

    fav_file = tmp_path / "test_music_favorites.json"
    monkeypatch.setattr(ms, "_MUSIC_FAVORITES_FILE", fav_file)
    monkeypatch.setattr(ms, "_favorites", set())

    music_dir = tmp_path / "Music"
    music_dir.mkdir(exist_ok=True)
    track1 = music_dir / "track1.flac"
    track1.write_bytes(b"dummy flac content")
    monkeypatch.setattr(ms, "MUSIC_ROOT", music_dir)

    assert ms.get_favorites() == []
    assert ms.is_favorite("track1.flac") is False

    res = ms.toggle_favorite("track1.flac")
    assert res["is_favorite"] is True
    assert ms.is_favorite("track1.flac") is True
    assert ms.get_favorites() == ["track1.flac"]

    root_browse = ms.browse("")
    assert any(e.get("path") == "__favorites__" and e.get("is_virtual") is True for e in root_browse.get("entries", []))

    fav_browse = ms.browse("__favorites__")
    assert fav_browse["path"] == "__favorites__"
    assert len(fav_browse["entries"]) == 1
    assert fav_browse["entries"][0]["path"] == "track1.flac"

    # Aislado del mpv real: sin esto el test cambiaba la reproducción del usuario.
    monkeypatch.setattr(ms, "_mpv_responding", lambda: True)
    monkeypatch.setattr(ms, "ensure_mpv", lambda: None)
    monkeypatch.setattr(ms, "_ipc_command", lambda *a, **k: None)
    monkeypatch.setattr(ms, "_library_tracks", lambda: [track1])
    monkeypatch.setattr(ms, "_shuffle_library", False)
    play_res = ms.play_path("__favorites__")
    assert play_res.get("ok") is True or "status" in play_res

    res2 = ms.toggle_favorite("track1.flac")
    assert res2["is_favorite"] is False
    assert ms.is_favorite("track1.flac") is False
    assert ms.get_favorites() == []


def test_download_task_queue_and_validation(tmp_path, monkeypatch):
    from services.music import music_service as ms

    tasks_file = tmp_path / "test_download_tasks.json"
    monkeypatch.setattr(ms, "_DOWNLOAD_TASKS_FILE", tasks_file)
    monkeypatch.setattr(ms, "_download_tasks", {})

    with pytest.raises(ValueError, match="URL requerida"):
        ms.start_download("")

    with pytest.raises(ValueError, match="Solo enlaces de YouTube"):
        ms.start_download("https://example.com/audio.mp3")

    monkeypatch.setattr("threading.Thread.start", lambda self: None)

    task = ms.start_download("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert task["id"].startswith("dl_")
    assert task["state"] == "pending"

    tasks = ms.get_download_tasks()
    assert len(tasks) == 1
    assert tasks[0]["id"] == task["id"]
    assert tasks[0]["url"] == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"







