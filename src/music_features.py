"""Features de audio por pista y filtros de smart playlists (caché JSONL)."""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

_LOCK = threading.Lock()
_CACHE: Optional[Dict[str, Dict[str, Any]]] = None
_MTIME: float = 0.0

KEY_ORDER = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def _path() -> Path:
    from src.constants import DATA_DIR
    return Path(DATA_DIR) / "music_features.jsonl"


def load() -> Dict[str, Dict[str, Any]]:
    global _CACHE, _MTIME
    p = _path()
    try:
        mtime = p.stat().st_mtime
    except OSError:
        return {}
    with _LOCK:
        if _CACHE is not None and _MTIME == mtime:
            return _CACHE
        out: Dict[str, Dict[str, Any]] = {}
        try:
            for line in p.read_text(encoding="utf-8").splitlines():
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if row.get("path") and "error" not in row:
                    out[row["path"]] = row
        except OSError:
            out = {}
        _CACHE, _MTIME = out, mtime
        return out


def key_similarity(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    ta, tb = a.get("tonic"), b.get("tonic")
    if not ta or not tb or ta not in KEY_ORDER or tb not in KEY_ORDER:
        return 0.0
    ia, ib = KEY_ORDER.index(ta), KEY_ORDER.index(tb)
    if ia == ib and a.get("mode") == b.get("mode"):
        return 1.0
    if (ia - ib) % 12 in (5, 7):
        return 0.65
    return 0.2


def similarity(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    """0..1 entre dos pistas; 0 si faltan datos."""
    if not a or not b:
        return 0.0
    try:
        dbpm = abs(float(a.get("bpm", 0)) - float(b.get("bpm", 0)))
        bpm = max(0.0, 1.0 - min(dbpm, 60.0) / 60.0)
        de = abs(float(a.get("energy", 0)) - float(b.get("energy", 0)))
        dv = abs(float(a.get("valence", 0)) - float(b.get("valence", 0)))
        da = abs(float(a.get("arousal", 0)) - float(b.get("arousal", 0)))
    except (TypeError, ValueError):
        return 0.0
    return round(
        0.30 * key_similarity(a, b)
        + 0.30 * bpm
        + 0.15 * max(0.0, 1.0 - de * 8)
        + 0.15 * max(0.0, 1.0 - dv)
        + 0.10 * max(0.0, 1.0 - da),
        4,
    )


def _tracks() -> List[str]:
    from services.music import music_service as ms
    root = ms.MUSIC_ROOT.resolve()
    out = []
    for p in ms._library_tracks():
        try:
            out.append(str(p.resolve().relative_to(root)))
        except ValueError:
            continue
    return out


def _last_played() -> Dict[str, float]:
    from src import music_history
    seen: Dict[str, float] = {}
    for row in music_history.plays():
        seen[row["path"]] = max(seen.get(row["path"], 0.0), float(row.get("ts", 0)))
    return seen


def smart(
    *,
    bpm_min: Optional[float] = None,
    bpm_max: Optional[float] = None,
    key: Optional[str] = None,
    mode: Optional[str] = None,
    energy_min: Optional[float] = None,
    valence_min: Optional[float] = None,
    not_played_days: Optional[float] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    import time
    feats = load()
    seen = _last_played()
    now = time.time()
    out = []
    for rel in _tracks():
        f = feats.get(rel)
        if not f:
            continue
        if bpm_min is not None and float(f.get("bpm", 0)) < bpm_min:
            continue
        if bpm_max is not None and float(f.get("bpm", 0)) > bpm_max:
            continue
        if key and (f.get("tonic") or "").lower() != key.lower():
            continue
        if mode and (f.get("mode") or "").lower() != mode.lower():
            continue
        if energy_min is not None and float(f.get("energy", 0)) < energy_min:
            continue
        if valence_min is not None and float(f.get("valence", 0)) < valence_min:
            continue
        if not_played_days is not None:
            last = seen.get(rel)
            if last and now - last < not_played_days * 86400:
                continue
        out.append({
            "path": rel,
            "artist": __import__("src.music_history", fromlist=["artist_of"]).artist_of(rel),
            "title": Path(rel).stem.split(" - ", 1)[-1],
            "bpm": f.get("bpm"),
            "key": f.get("key"),
            "energy": f.get("energy"),
            "valence": f.get("valence"),
            "arousal": f.get("arousal"),
        })
    out.sort(key=lambda r: (-float(r.get("energy") or 0), r["path"]))
    return out[: max(1, min(int(limit), 500))]
