"""Buscar y agregar música de YouTube a la biblioteca (Unsorted) con yt-dlp.

Sigue la skill yt-dlp-music-unsorted: Opus para YouTube (remux si el origen ya es Opus),
metadatos y portada incrustados, verificación con ffprobe. Solo el owner descarga; el
resultado llega por /api/music/events como evento de tipo "download" con kind "ytdlp".
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Set

from services.music import music_service as ms
from src import music_downloads

logger = logging.getLogger(__name__)

SEARCH_TIMEOUT_SEC = 12.0
DOWNLOAD_TIMEOUT_SEC = 15 * 60
MAX_QUERY_CHARS = 120
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_tasks: Set[asyncio.Task] = set()


def _ytdlp() -> List[str]:
    # yt-dlp del venv del servicio: usa el mismo Python que mutagen (postprocesado de metadatos).
    return [sys.executable, "-m", "yt_dlp"]


def search(query: str, limit: int = 8) -> List[Dict[str, Any]]:
    """Resultados de YouTube. Lanza TimeoutError si yt-dlp no responde en 12 s."""
    text = " ".join((query or "").split())[:MAX_QUERY_CHARS]
    if not text:
        return []
    cmd = [*_ytdlp(), "--flat-playlist", "--dump-json", "--no-warnings", f"ytsearch{int(limit)}:{text}"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=SEARCH_TIMEOUT_SEC, check=False)
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError("búsqueda de YouTube sin respuesta en 12 s") from exc
    if proc.returncode != 0 and not proc.stdout.strip():
        raise RuntimeError((proc.stderr or "yt-dlp falló").strip()[-300:])
    rows: List[Dict[str, Any]] = []
    for line in proc.stdout.splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        vid = d.get("id") or ""
        if not _ID_RE.match(vid):
            continue
        rows.append({
            "id": vid,
            "title": d.get("title") or "",
            "artist": d.get("channel") or d.get("uploader") or "",
            "duration_sec": int(d.get("duration") or 0),
            "url": f"https://www.youtube.com/watch?v={vid}",
            "origin": "YouTube",
            "format": "opus",
        })
    return rows


def _download_sync(video_id: str) -> Dict[str, Any]:
    root = Path(ms.MUSIC_ROOT).resolve()
    out_dir = root / "Unsorted"
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        *_ytdlp(), "-f", "bestaudio[acodec=opus]/bestaudio", "-x", "--audio-format", "opus",
        "--embed-metadata", "--embed-thumbnail", "--convert-thumbnails", "jpg",
        "--no-playlist", "--no-overwrites", "--no-simulate", "--print", "after_move:filepath",
        "-o", str(out_dir / "%(title).200B.%(ext)s"),
        f"https://www.youtube.com/watch?v={video_id}",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=DOWNLOAD_TIMEOUT_SEC, check=False)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or "yt-dlp falló").strip().splitlines()[-1][:300])
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    if not lines:
        raise RuntimeError("yt-dlp no devolvió la ruta del fichero")
    path = Path(lines[-1].strip())
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=codec_name,sample_rate",
         "-of", "json", str(path)],
        capture_output=True, text=True, timeout=30, check=False,
    )
    stream = (json.loads(probe.stdout or "{}").get("streams") or [{}])[0]
    if probe.returncode != 0 or not stream.get("codec_name"):
        raise RuntimeError(f"ffprobe no pudo leer {path.name}: {probe.stderr.strip()[:200]}")
    ms._invalidate_library_cache()
    return {"file": str(path.relative_to(root)), "codec": stream.get("codec_name"), "sample_rate": stream.get("sample_rate")}


async def _run(video_id: str) -> None:
    evt: Dict[str, Any] = {"type": "download", "kind": "ytdlp", "id": video_id, "state": "failed"}
    try:
        info = await asyncio.to_thread(_download_sync, video_id)
        evt.update(state="done", **info)
    except Exception as exc:
        logger.warning("ytdlp download %s failed: %s", video_id, exc)
        evt["reason"] = str(exc)[:300]
    finally:
        music_downloads.publish(evt)


def start_download(video_id: str) -> None:
    if not _ID_RE.match(video_id or ""):
        raise ValueError("id de YouTube no válido")
    task = asyncio.get_running_loop().create_task(_run(video_id))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
