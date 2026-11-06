"""Canales 24/7 de música: una lista determinista + el reloj. Sin procesos de fondo.

Una petición calcula en qué pista va el canal ahora (offset = reloj mod duración total).
Entrar a mitad de pista funciona porque el offset sale del reloj, como una radio.
La biblioteca no tiene metadatos de género: los canales filtran por nombre de carpeta.
Un canal sin pistas no se anuncia.
"""

from __future__ import annotations

import hashlib
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# nombre -> (etiqueta, regex sobre la carpeta relativa a la raíz, o None = todo)
CHANNELS: Dict[str, Tuple[str, Optional[str]]] = {
    "todo": ("Todo", None),
    "del-dia": ("Del día", None),
    "bandas": ("Bandas sonoras", r"soundtrack|\bost\b|original soundtrack"),
    "workout": ("Workout", r"workout"),
}

def _root() -> Path:
    from services.music import music_service as ms

    return ms.MUSIC_ROOT.resolve()


def _library() -> List[str]:
    from services.music import music_service as ms

    root = _root()
    out = []
    for p in ms._library_tracks():
        try:
            out.append(str(p.resolve().relative_to(root)))
        except ValueError:
            continue
    return out


def _duration(rel: str) -> Optional[float]:
    from src.media_duration import duration_seconds

    return duration_seconds(_root() / rel)


def _seed(channel: str, now: float) -> str:
    if channel == "del-dia":
        # Cambia a medianoche LOCAL, no UTC.
        return "del-dia:" + datetime.fromtimestamp(now).strftime("%Y-%m-%d")
    return channel


def _candidates(channel: str) -> List[str]:
    if channel not in CHANNELS:
        raise KeyError(channel)
    pattern = CHANNELS[channel][1]
    rels = _library()
    if channel == "del-dia":
        # "Del dia" = incorporado en las ultimas 24 h. Si no hay nada, el canal no se
        # anuncia (channels_info omite listas vacias) en vez de mentir sirviendo todo.
        import time as _t
        cutoff = _t.time() - 86400
        out = []
        for r in rels:
            try:
                if (_root() / r).stat().st_mtime >= cutoff:
                    out.append(r)
            except OSError:
                continue
        return out
    if pattern is None:
        return rels
    rx = re.compile(pattern, re.IGNORECASE)
    return [r for r in rels if rx.search("/".join(r.replace("\\", "/").split("/")[:-1]))]


def _pool(channel: str) -> List[str]:
    # Una pista sin duración real no entra: no se inventa un tiempo para ella.
    return [r for r in _candidates(channel) if _duration(r) is not None]


def playlist(channel: str, now: Optional[float] = None) -> List[str]:
    """Orden determinista por md5(semilla + ruta). Mismo día y canal → mismo orden en cualquier proceso."""
    now = time.time() if now is None else now
    seed = _seed(channel, now)
    return sorted(_pool(channel), key=lambda r: hashlib.md5((seed + "\n" + r).encode("utf-8")).hexdigest())


def channels_info() -> List[Dict[str, Any]]:
    out = []
    for name, (label, _) in CHANNELS.items():
        pl = _pool(name)
        if not pl:
            continue
        total = sum(_duration(r) for r in pl)
        out.append({"name": name, "label": label, "tracks": len(pl), "total_sec": round(total, 1)})
    return out


def now_playing(channel: str, now: Optional[float] = None) -> Dict[str, Any]:
    now = time.time() if now is None else now
    pl = playlist(channel, now)
    if not pl:
        raise LookupError(f"el canal {channel} no tiene pistas")
    durs = [_duration(r) for r in pl]
    total = sum(durs)
    pos = now % total
    acc = 0.0
    for i, d in enumerate(durs):
        if pos < acc + d:
            nxt = pl[(i + 1) % len(pl)]
            return {
                "channel": channel,
                "track": pl[i],
                "offset_sec": round(pos - acc, 2),
                "duration_sec": d,
                "estimated": False,
                "next": nxt,
                "started_at": round(now - (pos - acc), 2),
                "server_time": round(now, 2),
            }
        acc += d
    raise RuntimeError("cálculo de canal fuera de rango")  # no debería ocurrir
