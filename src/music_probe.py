"""Library metadata probe: native binary when built, mutagen otherwise.

The native probe (``native/music_probe``) walks the whole library with
libavformat: tags, audio properties and the embedded cover in one process,
instead of one ``ffprobe`` process per file. It is optional; the mutagen
fallback produces the same rows when the binary is not built.

Rows are dicts with: path (relative to the root), title, artist, album,
album_artist, genre, date, track, duration_sec, sample_rate, channels, bits,
codec and, when the file carries one, cover (file name inside the covers dir).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

AUDIO_EXTS = {
    ".flac", ".wav", ".mp3", ".m4a", ".aac", ".ogg",
    ".opus", ".aiff", ".aif", ".alac", ".wv", ".ape",
}


def native_binary() -> Optional[str]:
    override = os.environ.get("ODYSSEUS_MUSIC_PROBE", "").strip()
    if override and os.access(override, os.X_OK):
        return override
    candidate = Path(__file__).resolve().parents[1] / "native" / "music_probe" / "music_probe"
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return str(candidate)
    return None


def _json_escape(text: str) -> str:
    return json.dumps(text, ensure_ascii=False)[1:-1]


def _cover_name(rel: str) -> str:
    h = 1469598103934665603
    for byte in rel.encode("utf-8", "surrogateescape"):
        h ^= byte
        h = (h * 1099511628211) & 0xFFFFFFFFFFFFFFFF
    return f"{h:016x}"


def _write_cover(data: bytes, rel: str, covers: Path) -> str:
    ext = ".png" if data[:8] == b"\x89PNG\r\n\x1a\n" else ".jpg"
    name = _cover_name(rel) + ext
    covers.mkdir(parents=True, exist_ok=True)
    (covers / name).write_bytes(data)
    return name


def probe_native(root: Path, covers: Optional[Path] = None, limit: int = 0) -> Optional[List[Dict[str, Any]]]:
    binary = native_binary()
    if not binary:
        return None
    command = [binary, "--root", str(root)]
    if covers is not None:
        command += ["--covers", str(covers)]
    if limit:
        command += ["--limit", str(limit)]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=900)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    rows: List[Dict[str, Any]] = []
    for line in completed.stdout.splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def _first(tags: Dict[str, Any], key: str) -> str:
    value = tags.get(key)
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ""
    return str(value or "").strip()


def _mutagen_cover(audio: Any) -> Optional[bytes]:
    pictures = getattr(audio, "pictures", None)
    if pictures:
        try:
            return pictures[0].data
        except (AttributeError, IndexError):
            pass
    tags = getattr(audio, "tags", None)
    if tags:
        for key in tags:
            if str(key).upper().startswith("APIC:"):
                try:
                    return tags[key].data
                except AttributeError:
                    continue
        try:
            covers = tags.get("covr")
            if covers:
                return bytes(covers[0])
        except (AttributeError, IndexError, TypeError):
            pass
    return None


def probe_mutagen(root: Path, covers: Optional[Path] = None, limit: int = 0) -> List[Dict[str, Any]]:
    from mutagen import File as MutagenFile

    rows: List[Dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in AUDIO_EXTS:
            continue
        rel = str(path.relative_to(root))
        try:
            audio = MutagenFile(path, easy=True)
        except Exception:
            continue
        if audio is None:
            continue
        tags = getattr(audio, "tags", None) or {}
        info = getattr(audio, "info", None)
        title = _first(tags, "title") or path.stem
        cover = _mutagen_cover(audio)
        row: Dict[str, Any] = {
            "path": rel,
            "title": title,
            "artist": _first(tags, "artist"),
            "album": _first(tags, "album"),
            "album_artist": _first(tags, "albumartist") or _first(tags, "album_artist"),
            "genre": _first(tags, "genre"),
            "date": _first(tags, "date"),
            "track": _first(tags, "tracknumber"),
            "duration_sec": float(getattr(info, "length", 0.0) or 0.0),
            "sample_rate": int(getattr(info, "sample_rate", 0) or 0),
            "channels": int(getattr(info, "channels", 0) or 0),
            "bits": int(getattr(info, "bits_per_sample", 0) or 0),
            "codec": type(audio).__name__.lower(),
        }
        if cover and covers is not None:
            row["cover"] = _write_cover(cover, rel, covers)
        rows.append(row)
        if limit and len(rows) >= limit:
            break
    return rows


def probe_library(root: Path, covers: Optional[Path] = None, limit: int = 0) -> List[Dict[str, Any]]:
    root = Path(root)
    rows = probe_native(root, covers=covers, limit=limit)
    if rows is None:
        rows = probe_mutagen(root, covers=covers, limit=limit)
    return rows
