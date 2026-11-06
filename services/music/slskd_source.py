"""Buscador multifuente: local (biblioteca) + slskd (Soulseek).

Una consulta natural se normaliza a términos de búsqueda; cada fuente devuelve
la MISMA forma de resultado y la fusión deduplica por (artista, título, duración
±2 s) quedando el local como reproducible inmediata.

Sin embeddings: si no hay vector disponible se usa BM25/cobertura de tokens sobre
la consulta cruda (documentado en la respuesta con "mode": "bm25").
"""

from __future__ import annotations

import json
import os
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

SLSKD_URL = os.environ.get("ODYSSEUS_SLSKD_URL", "http://127.0.0.1:5030").rstrip("/")
SLSKD_KEY = os.environ.get("ODYSSEUS_SLSKD_KEY", "").strip()
SLSKD_TIMEOUT = float(os.environ.get("ODYSSEUS_SLSKD_TIMEOUT", "10"))

# Palabras que en lenguaje natural no aportan a una búsqueda por título/artista.
_STOP = {
    "la", "el", "los", "las", "de", "del", "que", "un", "una", "unos", "unas",
    "para", "por", "con", "sin", "algo", "algun", "alguna", "tema", "temas",
    "cancion", "canciones", "musica", "pon", "ponme", "poner", "quiero",
    "busca", "buscame", "tocar", "suena", "suene", "esa", "ese", "eso",
    "tranquilo", "tranquila", "relajado", "relajante", "trabajar", "estudiar",
    "fondo", "ambiente", "tipo", "estilo", "como", "muy", "mas", "mejor",
    "y", "o", "a", "en", "mi", "mis", "su", "sus",
}

_AUDIO_EXT = {".flac", ".opus", ".mp3", ".m4a", ".ogg", ".wav", ".aac", ".wma", ".alac"}


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def normalize(text: str) -> str:
    return _strip_accents((text or "").lower()).strip()


def search_terms(query: str) -> List[str]:
    """Términos útiles de una frase natural, sin LLM."""
    raw = re.split(r"[^\w]+", normalize(query))
    terms = [t for t in raw if t and t not in _STOP and not t.isdigit()]
    return terms or [t for t in raw if t]


# --- parsing de nombre de fichero de Soulseek ---------------------------------

_TRACK_PREFIX = re.compile(r"^\s*\d{1,3}\s*[-._)]\s*")


def _split_artist_title(parts: List[str], filename: str) -> Tuple[str, str]:
    stem = Path(filename).stem
    stem = _TRACK_PREFIX.sub("", stem).strip()
    if " - " in stem:
        left, right = stem.split(" - ", 1)
        if left.strip() and right.strip():
            return left.strip(), right.strip()
    # carpeta/fichero: el artista suele ser la carpeta anterior al álbum
    dirs = [p for p in parts if p and not p.startswith("@@")]
    artist = dirs[-2] if len(dirs) >= 2 else (dirs[-1] if dirs else "")
    return artist.strip(), stem


def _quality_from_ext(ext: str, bit_depth: Any = None, sample_rate: Any = None, bit_rate: Any = None) -> str:
    ext = ext.lstrip(".").lower()
    if ext in {"flac", "alac", "wav"}:
        bits = f"/{int(bit_depth)}bit" if isinstance(bit_depth, int) and bit_depth else ""
        khz = f"/{round(int(sample_rate) / 1000)}kHz" if isinstance(sample_rate, int) and sample_rate else ""
        return f"{ext.upper()}{bits}{khz}"
    if ext == "opus":
        return "Opus"
    if ext == "mp3":
        return f"MP3 {int(bit_rate)}k" if isinstance(bit_rate, int) and bit_rate else "MP3"
    if ext in {"m4a", "aac"}:
        return f"AAC {int(bit_rate)}k" if isinstance(bit_rate, int) and bit_rate else "AAC"
    return ext.upper() or "?"


def _remote_row(username: str, f: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    name = str(f.get("filename") or "")
    ext = Path(name.replace("\\", "/")).suffix.lower()
    if ext not in _AUDIO_EXT:
        return None
    parts = [p for p in name.split("\\") if p]
    artist, title = _split_artist_title(parts, parts[-1] if parts else name)
    if not title:
        return None
    length = f.get("length")
    duration = round(float(length) / 1000.0, 2) if isinstance(length, (int, float)) and length else None
    size = f.get("size") if isinstance(f.get("size"), (int, float)) else None
    return {
        "title": title,
        "artist": artist,
        "source": "slskd",
        "quality": _quality_from_ext(ext, f.get("bitDepth"), f.get("sampleRate"), f.get("bitRate")),
        "duration_sec": duration,
        "playable_now": False,
        "path": None,
        "size_bytes": size,
        "username": username,
        "remote_filename": name,
    }


# --- cliente slskd ------------------------------------------------------------


def available() -> bool:
    return bool(SLSKD_KEY)


def _api(method: str, path: str, payload: Optional[Dict[str, Any]] = None, timeout: float = SLSKD_TIMEOUT) -> Any:
    url = f"{SLSKD_URL}{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("X-API-Key", SLSKD_KEY)
    if data:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read()
    return json.loads(body) if body else None


def search(query: str, limit: int = 20, timeout: float = SLSKD_TIMEOUT, wait: float = 10.0) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Búsqueda en Soulseek. Degrada limpio: error -> lista vacía + motivo."""
    meta: Dict[str, Any] = {"source": "slskd", "ok": False, "elapsed_ms": 0, "peers": 0, "files": 0}
    if not available():
        meta["error"] = "ODYSSEUS_SLSKD_KEY ausente"
        return [], meta
    terms = search_terms(query)
    if not terms:
        meta["error"] = "consulta vacía tras normalizar"
        return [], meta
    text = " ".join(terms)
    sid = str(uuid.uuid4())
    t0 = time.time()
    try:
        _api("POST", "/api/v0/searches", {"searchText": text, "id": sid, "filterResponses": True}, timeout=5)
        deadline = t0 + wait
        state = None
        while time.time() < deadline:
            time.sleep(0.35)
            info = _api("GET", f"/api/v0/searches/{sid}") or {}
            state = info.get("state")
            state = state if isinstance(state, str) else ",".join(state or [])
            meta["peers"] = info.get("responseCount") or 0
            meta["files"] = info.get("fileCount") or 0
            done = "Completed" in (state or "") or "FileLimitReached" in (state or "")
            # Parcial: si ya hay peers respondiendo, no esperar los 10 s completos.
            if done or (meta["peers"] and time.time() - t0 >= 3.0):
                break
        responses = _api("GET", f"/api/v0/searches/{sid}/responses") or []
        rows: List[Dict[str, Any]] = []
        seen: set = set()
        for resp in responses:
            user = resp.get("username") or ""
            for f in resp.get("files") or []:
                row = _remote_row(user, f)
                if not row:
                    continue
                key = (normalize(row["artist"]), normalize(row["title"]), row["duration_sec"])
                if key in seen:
                    continue
                seen.add(key)
                rows.append(row)
                if len(rows) >= limit * 4:
                    break
            if len(rows) >= limit * 4:
                break
        meta.update({"ok": True, "state": state, "query": text, "elapsed_ms": int((time.time() - t0) * 1000)})
        return rows[: limit * 2], meta
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, ValueError) as exc:
        meta["error"] = f"{type(exc).__name__}: {exc}"
        meta["elapsed_ms"] = int((time.time() - t0) * 1000)
        return [], meta
    finally:
        try:
            _api("DELETE", f"/api/v0/searches/{sid}", timeout=3)
        except Exception:
            pass


# --- fusión -------------------------------------------------------------------

_RRF_K = 60


def _compatible(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    """Mismo título y artistas compatibles (uno vacío cuenta) y duración ±2 s."""
    if normalize(a.get("title") or "") != normalize(b.get("title") or ""):
        return False
    aa, ab = normalize(a.get("artist") or ""), normalize(b.get("artist") or "")
    if aa and ab and aa != ab and aa not in ab and ab not in aa:
        return False
    da, db = a.get("duration_sec"), b.get("duration_sec")
    if da is None or db is None:
        return True
    return abs(float(da) - float(db)) <= 2.0


def merge(local: List[Dict[str, Any]], remote: List[Dict[str, Any]], limit: int = 20) -> Tuple[List[Dict[str, Any]], int]:
    """Fusiona por título + artista compatible + duración ±2 s. Devuelve (filas, duplicados)."""
    merged: List[Dict[str, Any]] = []
    duplicates = 0

    def add(row: Dict[str, Any], rank: int) -> None:
        nonlocal duplicates
        for i, other in enumerate(merged):
            if _compatible(other, row):
                duplicates += 1
                if not other.get("playable_now") and row.get("playable_now"):
                    merged[i] = {**other, **row, "_rrf": other.get("_rrf", 0.0)}
                return
        row = dict(row)
        row["_rrf"] = row.get("_rrf", 0.0) + 1.0 / (_RRF_K + rank + 1) + (0.15 if row.get("playable_now") else 0.0)
        merged.append(row)

    for rank, row in enumerate(local):
        add(row, rank)
    for rank, row in enumerate(remote):
        add(row, rank)

    merged.sort(key=lambda r: r.get("_rrf", 0.0), reverse=True)
    return [{k: v for k, v in r.items() if k != "_rrf"} for r in merged[:limit]], duplicates
