"""Duración real de una pista de audio, con respaldo explícito.

Cadena: mutagen → ffprobe → None. Cada fallo se registra con su motivo (nunca se traga
en silencio). Resultados cacheados por (ruta, mtime), también los negativos, para que
un fichero malo no vuelva a abrirse en cada reconstrucción del índice.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_CACHE: Dict[str, Tuple[float, Optional[float]]] = {}  # ruta -> (mtime, segundos o None)
_WARNED: set = set()


def _mutagen_length(path: Path) -> Optional[float]:
    from mutagen import File as MFile  # un ImportError aquí debe verse, no ocultarse

    f = MFile(str(path))
    length = getattr(getattr(f, "info", None), "length", None) if f is not None else None
    return float(length) if length else None


def _ffprobe_length(path: Path) -> Optional[float]:
    exe = shutil.which("ffprobe")
    if not exe:
        raise RuntimeError("ffprobe no está en PATH")
    proc = subprocess.run(
        [exe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, timeout=15, check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe rc={proc.returncode}: {proc.stderr.strip()}")
    out = proc.stdout.strip()
    return float(out) if out and out != "N/A" else None


def duration_seconds(path: Path) -> Optional[float]:
    """Segundos de la pista, o None si ninguna fuente puede darlos (se avisa una vez)."""
    key = str(path)
    try:
        mtime = path.stat().st_mtime
    except OSError as exc:
        _warn_once(key, None, f"no se puede leer el fichero: {exc}")
        return None
    with _LOCK:
        hit = _CACHE.get(key)
    if hit and hit[0] == mtime:
        return hit[1]

    result: Optional[float] = None
    reasons = []
    for name, probe in (("mutagen", _mutagen_length), ("ffprobe", _ffprobe_length)):
        try:
            result = probe(path)
        except Exception as exc:
            reasons.append(f"{name}: {type(exc).__name__}: {exc}")
            continue
        if result:
            break
        reasons.append(f"{name}: sin duración")

    if not result:
        _warn_once(key, mtime, "; ".join(reasons))
    with _LOCK:
        _CACHE[key] = (mtime, result)
    return result


def _warn_once(key: str, mtime: Optional[float], reason: str) -> None:
    with _LOCK:
        if (key, mtime) in _WARNED:
            return
        _WARNED.add((key, mtime))
    logger.warning("Sin duración para %s (%s)", key, reason)
