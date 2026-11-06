"""Historial de reproducción, relacionados, radio y descubrimiento. Sin APIs externas ni ML.

Cada reproducción real (la página remota la reporta al cambiar de pista o pausar) es una
línea en data/music_plays.jsonl. Con eso:
- co-escucha: pistas sonadas en la misma sesión (<30 min entre ellas) son vecinas;
- relacionado: score = 0.45·co-escucha + 0.25·mismo artista + 0.15·mismo álbum
  + 0.10·misma carpeta + 0.05·recencia;
- radio: orden determinista a partir de la semilla, sin repetir en 10 pistas;
- descubrimiento: pistas nunca sonadas o sin sonar en 30 días, ordenadas por hash de la
  semilla de la última escucha (determinista, no aleatorio en cada petición).
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import threading
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

MAX_FILE_BYTES = 5 * 1024 * 1024
SESSION_GAP_SEC = 30 * 60
WINDOW = 5
_LOCK = threading.Lock()
ALLOWED = {"path", "artist", "title", "album", "secs_played", "completed"}


def _path() -> Path:
    from src.constants import DATA_DIR

    return Path(DATA_DIR) / "music_plays.jsonl"


def record(play: Dict[str, Any]) -> Dict[str, Any]:
    row = {k: play.get(k) for k in ALLOWED}
    row["path"] = str(row["path"] or "")
    if not row["path"]:
        raise ValueError("path requerido")
    # El artista lo deriva el servidor del fichero: el cliente puede mandar el prefijo de pista ("1-02").
    if not row.get("artist") or re.fullmatch(r"[\d\s.\-]+", str(row["artist"]).strip()):
        row["artist"] = artist_of(row["path"])
    row["secs_played"] = max(0.0, float(row.get("secs_played") or 0.0))
    row["completed"] = bool(row.get("completed"))
    row["ts"] = time.time()
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        if p.exists() and p.stat().st_size > MAX_FILE_BYTES:
            p.replace(p.with_suffix(".jsonl.1"))
        # Un fallo de escritura se propaga (OSError): la ruta lo convierte en 500, nunca en 200.
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            f.flush()
    try:
        from src import music_scrobble
        music_scrobble.submit(row)
    except Exception:
        pass
    return row


def plays(limit: int = 5000) -> List[Dict[str, Any]]:
    p = _path()
    if not p.exists():
        return []
    with _LOCK:
        lines = p.read_text(encoding="utf-8").splitlines()[-limit:]
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    out.sort(key=lambda r: r.get("ts", 0))
    return out


def artist_of(rel: str) -> str:
    """Mismo criterio que la búsqueda: 'Artista - Título' en el nombre, si no, la carpeta."""
    name = rel.replace("\\", "/").rsplit("/", 1)[-1]
    stem = name.rsplit(".", 1)[0]
    if " - " in stem:
        left = stem.split(" - ", 1)[0].strip()
        # "01 - Título" o "1-02 - Título": el prefijo es número de pista, no artista.
        if left and not re.fullmatch(r"[\d\s.\-]+", left):
            return left
    parts = [x for x in rel.replace("\\", "/").split("/")[:-1] if x]
    return parts[-1] if parts else ""


def album_of(rel: str) -> str:
    parts = [x for x in rel.replace("\\", "/").split("/")[:-1] if x]
    return parts[-1] if parts else ""


def _cooccurrence(history: List[Dict[str, Any]]) -> Dict[str, Dict[str, float]]:
    co: Dict[str, Dict[str, float]] = defaultdict(lambda: defaultdict(float))
    session: List[Dict[str, Any]] = []
    for row in history + [None]:
        gap = row is None or (session and row.get("ts", 0) - session[-1].get("ts", 0) > SESSION_GAP_SEC)
        if gap and session:
            for i, a in enumerate(session):
                for j in range(i + 1, min(i + 1 + WINDOW, len(session))):
                    b = session[j]
                    if a["path"] == b["path"]:
                        continue
                    w = 1.0 / (j - i)
                    co[a["path"]][b["path"]] += w
                    co[b["path"]][a["path"]] += w
        if row is None:
            break
        if gap:
            session = []
        session.append(row)
    return co


def _library() -> List[str]:
    from services.music import music_service as ms

    root = ms.MUSIC_ROOT.resolve()
    out = []
    for p in ms._library_tracks():
        try:
            out.append(str(p.resolve().relative_to(root)))
        except ValueError:
            continue
    return out


def _digest(seed: str, value: str) -> int:
    return int(hashlib.sha1((seed + "\n" + value).encode("utf-8")).hexdigest()[:12], 16)


def related(seed: str, limit: int = 20) -> List[Dict[str, Any]]:
    hist = plays()
    co = _cooccurrence(hist)
    co_max = max((max(v.values()) for v in co.values() if v), default=0.0)
    seed_artist = artist_of(seed)
    seed_album = album_of(seed)
    try:
        from src import music_features as _mf
        _feats = _mf.load()
    except Exception:
        _mf, _feats = None, {}
    _seed_feat = _feats.get(seed)
    now = time.time()
    last_seen: Dict[str, float] = {}
    for r in hist:
        last_seen[r["path"]] = max(last_seen.get(r["path"], 0.0), float(r.get("ts", 0)))

    pool = set(co.get(seed, {}).keys()) | set(last_seen.keys()) | set(_library())
    pool.discard(seed)
    rows = []
    for path in pool:
        c = (co.get(seed, {}).get(path, 0.0) / co_max) if co_max else 0.0
        a = 1.0 if seed_artist and artist_of(path).lower() == seed_artist.lower() else 0.0
        al = 1.0 if seed_album and album_of(path) == seed_album else 0.0
        fo = 1.0 if album_of(path) == seed_album and not al else 0.0
        rec = max(0.0, 1.0 - (now - last_seen[path]) / (7 * 86400)) if path in last_seen else 0.0
        fs = 0.0
        if _mf is not None and _seed_feat:
            fs = _mf.similarity(_seed_feat, _feats.get(path, {}))
        # La carpeta es el fallback cuando aún no hay features de audio.
        extra = fs if fs > 0 else fo
        score = 0.45 * c + 0.25 * a + 0.15 * al + 0.10 * extra + 0.05 * rec
        if score <= 0:
            continue
        parts = [("co-escucha", 0.45 * c), ("artist", 0.25 * a), ("album", 0.15 * al),
                 ("features" if fs > 0 else "folder", 0.10 * extra), ("recency", 0.05 * rec)]
        reason = max(parts, key=lambda x: x[1])[0]
        rows.append({
            "path": path,
            "artist": artist_of(path),
            "title": Path(path).stem.split(" - ", 1)[-1],
            "score": round(score, 4),
            "reason": reason,
        })
    rows.sort(key=lambda r: (-r["score"], _digest(seed, r["path"])))
    return rows[:limit]


def discovery(seed: str = "", limit: int = 30) -> List[Dict[str, Any]]:
    hist = plays()
    now = time.time()
    last_seen: Dict[str, float] = {}
    for r in hist:
        last_seen[r["path"]] = max(last_seen.get(r["path"], 0.0), float(r.get("ts", 0)))
    cutoff = now - 30 * 86400
    cands = [p for p in _library() if p not in last_seen or last_seen[p] < cutoff]
    cands.sort(key=lambda p: _digest(seed or "discovery", p))
    return [{"path": p, "artist": artist_of(p), "title": Path(p).stem.split(" - ", 1)[-1], "reason": "discovery"}
            for p in cands[:limit]]


def radio(seed: str, offset: int = 0, limit: int = 30) -> List[Dict[str, Any]]:
    """Orden: 3 relacionadas, luego alterna relacionada / mismo artista / descubrimiento."""
    need = offset + limit
    rel = related(seed, limit=max(need, 60))
    disc = discovery(seed, limit=max(need, 60))
    rel_iter = iter(rel)
    disc_iter = iter(disc)
    same_artist = [r for r in rel if r["reason"] == "artist"]
    artist_iter = iter(same_artist)
    out: List[Dict[str, Any]] = []
    recent: List[str] = [seed]
    step = 0
    while len(out) < need:
        kind = "related" if step < 3 else ["related", "artist", "discovery"][(step - 3) % 3]
        pick: Optional[Dict[str, Any]] = None
        preferred = {"related": [rel_iter], "artist": [artist_iter], "discovery": [disc_iter]}[kind]
        # Si la fuente del turno se agota, se completa con las demás para no cortar la radio.
        for source in preferred + [rel_iter, artist_iter, disc_iter]:
            for cand in source:
                if cand["path"] not in recent[-10:]:
                    pick = dict(cand)
                    break
            if pick:
                break
        if pick is None:
            break
        pick["reason"] = pick.get("reason") if kind != "discovery" else "discovery"
        out.append(pick)
        recent.append(pick["path"])
        step += 1
    return out[offset:need]
