"""Playlists locales, ratings e instant mix del reproductor."""
from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

_LOCK = threading.Lock()


def _path() -> Path:
    from src.constants import DATA_DIR
    return Path(DATA_DIR) / "music_playlists.json"


def _ratings_path() -> Path:
    from src.constants import DATA_DIR
    return Path(DATA_DIR) / "music_ratings.json"


def _load() -> Dict[str, Any]:
    p = _path()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("playlists"), list):
            return data
    except (OSError, ValueError):
        pass
    return {"playlists": []}


def _save(data: Dict[str, Any]) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


def list_playlists() -> List[Dict[str, Any]]:
    return [{"id": p["id"], "name": p["name"], "count": len(p.get("tracks", [])),
             "updated": p.get("updated", 0)} for p in _load()["playlists"]]


def get_playlist(pid: str) -> Optional[Dict[str, Any]]:
    for p in _load()["playlists"]:
        if p.get("id") == pid:
            return dict(p)
    return None


def create(name: str, tracks: Optional[List[str]] = None) -> Dict[str, Any]:
    name = (name or "").strip() or "Playlist"
    with _LOCK:
        data = _load()
        row = {"id": uuid.uuid4().hex[:10], "name": name, "tracks": list(tracks or []),
               "created": time.time(), "updated": time.time()}
        data["playlists"].append(row)
        _save(data)
        return row


def update(pid: str, *, name: Optional[str] = None, tracks: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
    with _LOCK:
        data = _load()
        for row in data["playlists"]:
            if row.get("id") != pid:
                continue
            if name is not None:
                row["name"] = name.strip() or row["name"]
            if tracks is not None:
                row["tracks"] = list(tracks)
            row["updated"] = time.time()
            _save(data)
            return dict(row)
    return None


def delete(pid: str) -> bool:
    with _LOCK:
        data = _load()
        before = len(data["playlists"])
        data["playlists"] = [p for p in data["playlists"] if p.get("id") != pid]
        if len(data["playlists"]) == before:
            return False
        _save(data)
        return True


def add_tracks(pid: str, paths: List[str], position: Optional[int] = None) -> Optional[Dict[str, Any]]:
    with _LOCK:
        data = _load()
        for row in data["playlists"]:
            if row.get("id") != pid:
                continue
            current = list(row.get("tracks", []))
            add = [p for p in paths if p and p not in current]
            if position is None:
                current.extend(add)
            else:
                pos = max(0, min(int(position), len(current)))
                current[pos:pos] = add
            row["tracks"] = current
            row["updated"] = time.time()
            _save(data)
            return dict(row)
    return None


def remove_track(pid: str, path: str) -> Optional[Dict[str, Any]]:
    with _LOCK:
        data = _load()
        for row in data["playlists"]:
            if row.get("id") != pid:
                continue
            row["tracks"] = [p for p in row.get("tracks", []) if p != path]
            row["updated"] = time.time()
            _save(data)
            return dict(row)
    return None


def import_m3u(text: str, name: str = "") -> Dict[str, Any]:
    tracks = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        tracks.append(line)
    return create(name or "Importada", tracks)


def export_m3u(pid: str) -> Optional[str]:
    row = get_playlist(pid)
    if not row:
        return None
    lines = ["#EXTM3U"]
    lines.extend(row.get("tracks", []))
    return "\n".join(lines) + "\n"


def play(pid: str) -> Optional[Dict[str, Any]]:
    row = get_playlist(pid)
    if not row or not row.get("tracks"):
        return None
    from services.music import music_service as ms
    root = ms.MUSIC_ROOT.resolve()
    paths = [(root / rel) for rel in row["tracks"]]
    paths = [p for p in paths if p.is_file()]
    if not paths:
        return None
    return ms._load_tracks(paths, replace=True)


# ---- ratings ----

def get_ratings() -> Dict[str, int]:
    try:
        data = json.loads(_ratings_path().read_text(encoding="utf-8"))
        return {k: int(v) for k, v in data.items() if isinstance(v, (int, float))}
    except (OSError, ValueError):
        return {}


def set_rating(path: str, rating: int) -> int:
    rating = max(0, min(5, int(rating)))
    with _LOCK:
        data = get_ratings()
        if rating:
            data[path] = rating
        else:
            data.pop(path, None)
        p = _ratings_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return rating


# ---- instant mix ----

def instant_mix(seed: str, limit: int = 30) -> List[Dict[str, Any]]:
    from src import music_features
    from src import music_history
    feats = music_features.load()
    seed_feat = feats.get(seed, {})
    root = __import__("services.music.music_service", fromlist=["MUSIC_ROOT"]).MUSIC_ROOT
    rows = []
    for rel, f in feats.items():
        if rel == seed:
            continue
        sim = music_features.similarity(seed_feat, f) if seed_feat else 0.0
        rows.append((sim, rel))
    rows.sort(key=lambda x: -x[0])
    out = []
    for sim, rel in rows[: max(1, min(int(limit), 200))]:
        out.append({"path": rel, "score": round(sim, 4),
                    "artist": music_history.artist_of(rel),
                    "title": Path(rel).stem.split(" - ", 1)[-1]})
    return out
