"""Control local music under ODYSSEUS_MUSIC_ROOT via mpv IPC + playerctl.

Chapter metadata: mpv IPC ``chapter-list`` is authoritative when available;
otherwise ``ffprobe -show_chapters`` with on-disk cache keyed by file mtime.
"""

from __future__ import annotations

import json
import logging
import os
import random
import re
import shutil
import socket
import subprocess
import unicodedata
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, TypedDict

logger = logging.getLogger(__name__)

AUDIO_EXTS = {".mp3", ".flac", ".ogg", ".m4a", ".opus", ".wav", ".aac", ".wma", ".ape", ".wv"}
def _default_music_root() -> Path:
    from src.constants import DATA_DIR
    return Path(DATA_DIR) / "music"

# Set ODYSSEUS_MUSIC_ROOT to the library directory. The default is <data>/music.
# Playback uses mpv over a local IPC socket (ODYSSEUS_MPV_SOCKET) and playerctl.
MUSIC_ROOT = Path(os.getenv("ODYSSEUS_MUSIC_ROOT") or _default_music_root()).expanduser()
MPV_SOCKET = Path(
    os.getenv(
        "ODYSSEUS_MPV_SOCKET",
        f"/run/user/{os.getuid()}/odysseus-mpv.sock",
    )
)
MPV_PLAYER_NAME = os.getenv("ODYSSEUS_MPV_PLAYER", "odysseus-music")
MPV_DAEMON_CONF = Path(
    os.getenv(
        "ODYSSEUS_MPV_CONFIG",
        Path(__file__).resolve().parents[2] / "config" / "mpv-daemon.conf",
    )
).expanduser()
_MAX_BROWSE = 400
_MAX_SEARCH = 80
_MAX_LIBRARY = 100_000
_RECENT_RANDOM = 40

_shuffle_library = False
_play_history: List[str] = []
_history_index = -1
_library_cache: Optional[List[Path]] = None
_library_cache_at = 0.0
_queue_tracks: List[Path] = []
_queue_index = -1

_DATA_DIR = Path(
    os.getenv("ODYSSEUS_DATA_DIR", Path(__file__).resolve().parents[2] / "data")
).expanduser()
_CHAPTER_CACHE_FILE = _DATA_DIR / "music_chapters.json"
_MUSIC_STATE_FILE = _DATA_DIR / "music_state.json"
_MUSIC_STATS_FILE = _DATA_DIR / "music_stats.json"
_MUSIC_QUALITY_CACHE_FILE = _DATA_DIR / "music_quality_cache.json"
_track_play_counts: Dict[str, int] = {}
_quality_cache_dirty = False


def _load_track_stats() -> None:
    global _track_play_counts
    if not _MUSIC_STATS_FILE.is_file():
        return
    try:
        with open(_MUSIC_STATS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                _track_play_counts = {
                    str(k): int(v) for k, v in data.items() if isinstance(v, (int, float))
                }
    except Exception as exc:
        logger.debug("load_track_stats error: %s", exc)


def _save_track_stats() -> None:
    try:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(_MUSIC_STATS_FILE, "w", encoding="utf-8") as f:
            json.dump(_track_play_counts, f, indent=2)
    except Exception as exc:
        logger.debug("save_track_stats error: %s", exc)


def get_track_play_count(path: Path) -> int:
    try:
        rel = str(path.resolve().relative_to(MUSIC_ROOT.resolve()))
    except (ValueError, OSError):
        rel = str(path)
    return _track_play_counts.get(rel, 0)


_MUSIC_FAVORITES_FILE = _DATA_DIR / "music_favorites.json"
_DOWNLOAD_TASKS_FILE = _DATA_DIR / "music_download_tasks.json"
_favorites: Set[str] = set()
_download_tasks: Dict[str, Dict[str, Any]] = {}
_download_tasks_lock = threading.Lock()


def _load_favorites() -> None:
    global _favorites
    if not _MUSIC_FAVORITES_FILE.is_file():
        return
    try:
        with open(_MUSIC_FAVORITES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                _favorites = set(str(p).strip("/") for p in data if p)
    except Exception as exc:
        logger.debug("load_favorites error: %s", exc)


def _save_favorites() -> None:
    try:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(_MUSIC_FAVORITES_FILE, "w", encoding="utf-8") as f:
            json.dump(sorted(_favorites), f, indent=2, ensure_ascii=False)
    except Exception as exc:
        logger.debug("save_favorites error: %s", exc)


def get_favorites() -> List[str]:
    _load_favorites()
    return sorted(_favorites)


def is_favorite(path_or_rel: str) -> bool:
    if not path_or_rel:
        return False
    _load_favorites()
    cleaned = str(path_or_rel).strip()
    root = MUSIC_ROOT.resolve()
    try:
        p = Path(cleaned)
        if p.is_absolute():
            rel = str(p.resolve().relative_to(root)).strip("/")
        else:
            rel = cleaned.strip("/")
    except Exception:
        rel = cleaned.strip("/")
    return rel in _favorites


def toggle_favorite(path_or_rel: str) -> Dict[str, Any]:
    if not path_or_rel:
        return {"error": "Path required", "is_favorite": False, "favorites": get_favorites()}
    _load_favorites()
    cleaned = str(path_or_rel).strip()
    root = MUSIC_ROOT.resolve()
    try:
        p = Path(cleaned)
        if p.is_absolute():
            rel = str(p.resolve().relative_to(root)).strip("/")
        else:
            rel = cleaned.strip("/")
    except Exception:
        rel = cleaned.strip("/")

    if rel in _favorites:
        _favorites.remove(rel)
        fav = False
    else:
        _favorites.add(rel)
        fav = True
    _save_favorites()
    return {"path": rel, "is_favorite": fav, "favorites": get_favorites()}


def _load_download_tasks() -> None:
    global _download_tasks
    if _download_tasks:
        return
    if not _DOWNLOAD_TASKS_FILE.is_file():
        return
    try:
        with open(_DOWNLOAD_TASKS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                _download_tasks = data
    except Exception as exc:
        logger.debug("load_download_tasks error: %s", exc)


def _save_download_tasks() -> None:
    try:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(_DOWNLOAD_TASKS_FILE, "w", encoding="utf-8") as f:
            json.dump(_download_tasks, f, indent=2, ensure_ascii=False)
    except Exception as exc:
        logger.debug("save_download_tasks error: %s", exc)


def get_download_tasks() -> List[Dict[str, Any]]:
    _load_download_tasks()
    with _download_tasks_lock:
        tasks = [dict(v) for v in _download_tasks.values()]
    tasks.sort(key=lambda t: float(t.get("created_at") or 0.0), reverse=True)
    return tasks


def _update_task(
    task_id: str,
    *,
    state: Optional[str] = None,
    message: Optional[str] = None,
    title: Optional[str] = None,
    destination: Optional[str] = None,
) -> None:
    with _download_tasks_lock:
        task = _download_tasks.get(task_id)
        if not task:
            return
        if state is not None:
            task["state"] = state
        if message is not None:
            task["message"] = message
        if title is not None:
            task["title"] = title
        if destination is not None:
            task["destination_file"] = destination
        task["updated_at"] = time.time()
        _save_download_tasks()


def start_download(url: str) -> Dict[str, Any]:
    raw_url = (url or "").strip()
    if not raw_url:
        raise ValueError("URL requerida")
    from urllib.parse import urlparse
    _host = (urlparse(raw_url if "://" in raw_url else "https://" + raw_url).hostname or "").lower()
    _allowed_hosts = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}
    if _host not in _allowed_hosts:
        raise ValueError("Solo enlaces de YouTube o YouTube Music son compatibles")

    _load_download_tasks()
    task_id = f"dl_{int(time.time())}_{uuid.uuid4().hex[:6]}"
    now = time.time()
    task = {
        "id": task_id,
        "url": raw_url,
        "title": raw_url,
        "state": "pending",
        "message": "En cola...",
        "created_at": now,
        "updated_at": now,
        "destination_file": None,
    }
    with _download_tasks_lock:
        _download_tasks[task_id] = task
        _save_download_tasks()

    worker_thread = threading.Thread(
        target=_run_download_and_upgrade_worker,
        args=(task_id, raw_url),
        daemon=True,
    )
    worker_thread.start()
    return dict(task)


def _run_download_and_upgrade_worker(task_id: str, url: str) -> None:
    logger.info("Iniciando tarea de descarga %s: %s", task_id, url)
    unsorted_dir = MUSIC_ROOT / "Unsorted"
    unsorted_dir.mkdir(parents=True, exist_ok=True)

    _update_task(task_id, state="downloading_youtube", message="Descargando audio de YouTube con yt-dlp...")

    cmd = [
        "/usr/bin/yt-dlp",
        "--no-playlist",
        "--extract-audio",
        "--audio-format", "best",
        "--audio-quality", "0",
        "--embed-metadata",
        "--print", "after_move:filepath",
        "--print", "title",
        "-o", str(unsorted_dir / "%(title)s.%(ext)s"),
        url,
    ]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180.0, check=False)
    except Exception as exc:
        logger.error("Error ejecutando yt-dlp: %s", exc)
        _update_task(task_id, state="error", message=f"Fallo en yt-dlp: {exc}")
        return

    if proc.returncode != 0:
        err = proc.stderr.strip() or proc.stdout.strip() or f"Código de salida {proc.returncode}"
        logger.error("yt-dlp falló: %s", err)
        _update_task(task_id, state="error", message=f"Fallo yt-dlp: {err[-150:]}")
        return

    lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    downloaded_path: Optional[Path] = None
    media_title: Optional[str] = None

    for line in lines:
        p_cand = Path(line)
        if p_cand.is_file() and _is_audio(p_cand):
            downloaded_path = p_cand
        elif not media_title and not line.startswith("/"):
            media_title = line

    if not downloaded_path:
        recent = sorted(unsorted_dir.glob("*.*"), key=lambda f: f.stat().st_mtime, reverse=True)
        for cand in recent[:5]:
            if _is_audio(cand) and time.time() - cand.stat().st_mtime < 120:
                downloaded_path = cand
                break

    if not downloaded_path or not downloaded_path.is_file():
        _update_task(task_id, state="error", message="Audio descargado no encontrado en disco")
        return

    rel_path = str(downloaded_path.resolve().relative_to(MUSIC_ROOT.resolve()))
    clean_title = media_title or downloaded_path.stem
    _invalidate_library_cache()
    get_track_audio_quality(downloaded_path, fast_only=True)

    _update_task(
        task_id,
        state="downloaded_youtube",
        title=clean_title,
        destination=rel_path,
        message="Guardado en Unsorted. Buscando reemplazo FLAC Hi-Fi en Soulseek...",
    )

    try:
        _search_and_upgrade_soulseek(task_id, downloaded_path, clean_title)
    except Exception as exc:
        logger.error("Error en búsqueda Soulseek: %s", exc)
        _update_task(task_id, state="completed_youtube", message="Conservando audio de YouTube.")


_BRACKET_CHARS = re.compile(r"[\[\](){}【】〔〕「」『』《》<>]")
_NOISE_WORDS = re.compile(
    r"\b(official(?:\s+music)?\s+video|official\s+audio|music\s+video|lyric\s+video|lyrics|"
    r"audio|mv|m/v|4k|8k|hd|hq|hi-?res|remaster(?:ed)?|full|version|ver|topic|visualizer)\b\.?",
    re.IGNORECASE,
)


def _clean_search_title(raw: str) -> str:
    """Título de YouTube -> consulta útil para Soulseek (sin 【】, MV, Official Video...)."""
    text = unicodedata.normalize("NFKC", raw or "")
    text = _BRACKET_CHARS.sub(" ", text)
    text = _NOISE_WORDS.sub(" ", text)
    text = re.sub(r"[\s\-–—_/|,.:;]+", " ", text).strip()
    tokens = text.split()
    return " ".join(tokens[:10]) or (raw or "").strip()


def _slskd_candidates(responses: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Candidatos FLAC ordenados: sin ficheros bloqueados, cola razonable, tamaño real."""
    exclude_vinyl = ("vinyl", "needle drop", "needledrop", "lp rip", "turntable")
    candidates: List[Dict[str, Any]] = []
    for resp in responses or []:
        if resp.get("hasFreeUploadSlot") is False:
            continue
        try:
            queue = int(resp.get("queueLength") or 0)
            speed = int(resp.get("uploadSpeed") or 0)
        except (TypeError, ValueError):
            continue
        if queue > 50:
            continue
        for file_info in resp.get("files") or []:
            fname = str(file_info.get("filename") or "")
            if not fname.lower().endswith(".flac") or file_info.get("isLocked"):
                continue
            if any(k in fname.lower() for k in exclude_vinyl):
                continue
            try:
                size = int(file_info.get("size") or 0)
            except (TypeError, ValueError):
                continue
            if size < 6 * 1024 * 1024:
                continue
            score = 1000 - min(queue, 40) * 10 + min(speed // 20000, 100)
            try:
                hd = int(file_info.get("bitDepth") or 16) > 16
                hsr = int(file_info.get("sampleRate") or 44100) > 48000
            except (TypeError, ValueError):
                hd = hsr = False
            if hd or hsr:
                score += 200
            candidates.append(
                {"username": resp.get("username"), "file": file_info, "score": score}
            )
    candidates.sort(key=lambda c: c["score"], reverse=True)
    return candidates


def _slskd_snapshot(roots: List[Path]) -> Dict[Path, int]:
    """Tamaños de los FLAC ya presentes: sirve para detectar lo que llega después."""
    snap: Dict[Path, int] = {}
    for root in roots:
        try:
            for f in root.rglob("*.flac"):
                if ".incomplete" in f.parts:
                    continue
                try:
                    snap[f] = f.stat().st_size
                except OSError:
                    continue
        except OSError:
            continue
    return snap


def _slskd_fetch(
    client: Any,
    username: str,
    file_info: Dict[str, Any],
    staging_dir: Path,
    timeout: float = 900.0,
) -> Optional[Path]:
    """Encola un fichero del resultado de búsqueda y espera a que slskd lo baje.

    slskd NO respeta el directorio pedido ni el nombre remoto: escribe en su carpeta
    de descargas configurada y suele renombrar el fichero (sufijo `_<id>`, prefijo del
    álbum). Por eso el resultado se localiza por "fichero nuevo con el tamaño exacto
    del remoto", no por nombre.
    """
    remote = str(file_info.get("filename") or "").replace("\\", "/").strip("/")
    name = Path(remote).name
    size = int(file_info.get("size") or 0)
    if not name or not username:
        return None

    roots = [staging_dir]
    if staging_dir.name == "backfill":
        roots.append(staging_dir.parent)
    before = _slskd_snapshot(roots)
    enqueued_at = time.time()

    def _arrived() -> Optional[Path]:
        fresh = {f: sz for f, sz in _slskd_snapshot(roots).items() if f not in before}
        if not fresh:
            return None
        if size:
            exact = [f for f, sz in fresh.items() if sz == size]
            if exact:
                return max(exact, key=lambda f: f.stat().st_mtime)
            close = [f for f, sz in fresh.items() if abs(sz - size) <= max(4096, size // 1000)]
            if close:
                return max(close, key=lambda f: f.stat().st_mtime)
        if len(fresh) == 1:
            return next(iter(fresh))
        return None

    try:
        # slskd solo acepta {filename, size}: el objeto de búsqueda completo devuelve 500
        payload = [
            {"filename": str(file_info.get("filename") or ""), "size": size}
        ]
        if not client.transfers.enqueue(username=username, files=payload):
            logger.warning("slskd rechazó el encolado de %s (%s)", name, username)
            return None
    except Exception as exc:
        logger.warning("slskd enqueue falló para %s (%s): %s", name, username, exc)
        return None

    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(3)
        got = _arrived()
        if got and got.stat().st_mtime >= enqueued_at - 5:
            return got
        try:
            downloads = client.transfers.get_downloads(username) or {}
        except Exception:
            continue
        for directory in downloads.get("directories") or []:
            for item in directory.get("files") or []:
                if Path(str(item.get("filename") or "").replace("\\", "/")).name != name:
                    continue
                state = str(item.get("state") or "")
                if "Completed" in state or "Succeeded" in state:
                    for _ in range(6):
                        got = _arrived()
                        if got:
                            return got
                        time.sleep(1)
                    return None
                if any(
                    bad in state
                    for bad in ("Errored", "Cancelled", "Aborted", "Rejected", "TimedOut")
                ):
                    logger.warning("transferencia slskd %s terminó en %s (%s)", name, state, username)
                    return None
    logger.warning("timeout esperando %s de %s", name, username)
    return None


def _slskd_downloads_dir(client: Any) -> Path:
    """Carpeta de descargas que slskd tiene configurada, para no adivinar la ruta."""
    env = os.getenv("ODYSSEUS_MUSIC_STAGING", "").strip()
    if env:
        return Path(env)
    try:
        cfg = client.options.get() or {}
        configured = (cfg.get("directories") or {}).get("downloads")
        if configured:
            return Path(str(configured))
    except Exception as exc:
        logger.debug("slskd options no disponible: %s", exc)
    return MUSIC_ROOT.parent / "Music_Downloads_Staging"


def _slskd_responses_for(
    client: Any, search_id: Any, query_title: str
) -> List[Dict[str, Any]]:
    """Respuestas de la búsqueda; si slskd la saltó o limitó, reusa la última con resultados.

    El servidor de Soulseek limita peticiones seguidas de búsqueda: la búsqueda recién
    creada se queda en 0 respuestas y antes eso se reportaba como "no hay FLAC".
    """
    try:
        responses = client.searches.search_responses(search_id) or []
    except Exception:
        responses = []
    if responses:
        return responses
    try:
        history = client.searches.get_all() or []
    except Exception:
        return []
    wanted = query_title.strip().lower()
    for entry in reversed(history):
        if str(entry.get("searchText") or "").strip().lower() != wanted:
            continue
        if entry.get("id") == search_id or int(entry.get("responseCount") or 0) <= 0:
            continue
        try:
            reused = client.searches.search_responses(entry["id"]) or []
        except Exception:
            continue
        if reused:
            logger.info("slskd sin respuestas nuevas para '%s': reusando búsqueda previa", query_title)
            return reused
    return []


def _search_and_upgrade_soulseek(task_id: str, current_audio_path: Path, raw_title: str) -> None:
    slskd_url = os.getenv("ODYSSEUS_SLSKD_URL", "http://127.0.0.1:5030")
    # Clave de la API de slskd desde entorno (ODYSSEUS_SLSKD_KEY). Nunca en código.
    slskd_key = os.getenv("ODYSSEUS_SLSKD_KEY", "")
    try:
        import slskd_api

        client = slskd_api.SlskdClient(slskd_url, slskd_key)
    except Exception as exc:
        logger.warning("slskd_api no disponible o no conectable: %s", exc)
        _update_task(task_id, state="completed_youtube", message="Soulseek no disponible. Audio de YouTube listo.")
        return

    query_title = _clean_search_title(raw_title)
    _update_task(task_id, state="searching_soulseek", message=f"Buscando '{query_title}' en Soulseek...")

    try:
        search_obj = client.searches.search_text(searchText=query_title)
        search_id = search_obj.get("id")
    except Exception as exc:
        logger.warning("Error iniciando búsqueda en slskd: %s", exc)
        _update_task(task_id, state="completed_youtube", message="Fallo de conexión a Soulseek. Audio de YouTube listo.")
        return

    start_t = time.time()
    responses: List[Dict[str, Any]] = []
    while time.time() - start_t < 40:
        time.sleep(2)
        try:
            st = client.searches.state(id=search_id) or {}
            responses = client.searches.search_responses(id=search_id) or []
        except Exception:
            continue
        if len(_slskd_candidates(responses)) >= 3 or st.get("isComplete"):
            break

    responses = _slskd_responses_for(client, search_id, query_title) or responses
    candidates = _slskd_candidates(responses)
    if not candidates:
        logger.info(
            "Sin candidatos FLAC en Soulseek para '%s' (%d respuestas)", query_title, len(responses)
        )
        _update_task(task_id, state="completed_youtube", message="No se halló FLAC en Soulseek. Audio de YouTube listo.")
        return

    staging_dir = _slskd_downloads_dir(client)
    staging_dir.mkdir(parents=True, exist_ok=True)

    downloaded_flac: Optional[Path] = None
    peer_user = ""
    for cand in candidates[:5]:
        peer_user = str(cand["username"] or "")
        _update_task(
            task_id,
            state="searching_soulseek",
            message=f"En cola de descarga FLAC en Soulseek ({peer_user})...",
        )
        downloaded_flac = _slskd_fetch(client, peer_user, cand["file"], staging_dir)
        if downloaded_flac:
            break

    if not downloaded_flac:
        logger.warning("No se pudo descargar ningún FLAC de Soulseek para '%s'", query_title)
        _update_task(task_id, state="completed_youtube", message="Descarga FLAC agotada. Audio de YouTube listo.")
        return

    test_proc = subprocess.run(["flac", "-t", str(downloaded_flac)], capture_output=True, text=True, timeout=15)
    if test_proc.returncode != 0:
        logger.warning("FLAC descargado corrupto: %s", downloaded_flac)
        _update_task(task_id, state="completed_youtube", message="FLAC de Soulseek con fallas. Conservando audio de YouTube.")
        return

    backup_dir = Path(os.getenv("ODYSSEUS_MUSIC_BACKUP", str(Path.home() / "Descargas" / "Unsorted")))
    backup_dir.mkdir(parents=True, exist_ok=True)
    if current_audio_path.is_file():
        try:
            shutil.move(str(current_audio_path), str(backup_dir / current_audio_path.name))
        except Exception as e:
            logger.warning("No se pudo trasladar archivo previo: %s", e)

    final_dest = MUSIC_ROOT / "Unsorted" / f"{query_title}.flac"
    shutil.move(str(downloaded_flac), str(final_dest))

    _invalidate_library_cache()
    get_track_audio_quality(final_dest, fast_only=True)
    rel_flac = str(final_dest.resolve().relative_to(MUSIC_ROOT.resolve()))

    _update_task(
        task_id,
        state="upgraded_flac",
        title=final_dest.stem,
        destination=rel_flac,
        message=f"Sustituido con éxito por máster FLAC Hi-Fi desde Soulseek ({peer_user}).",
    )
    logger.info("Sustitución FLAC completada exitosamente: %s", final_dest)


DEFAULT_MUSIC_PREFS: Dict[str, Any] = {
    "navigate_mode": "logical",
    "shuffle_chapters": True,
    "auto_advance": True,
    "default_volume": 100.0,
    "dock_open_tab": "auto",
    "poll_ms": 1000,
    "service_start_mode": "pause_last",
    "shuffle_enabled": True,
    "music_root": str(MUSIC_ROOT),
    "equalizer_bands": [0.0] * 10,
    "equalizer_preset": "flat",
    "remote_audio": False,
}

_music_prefs: Dict[str, Any] = dict(DEFAULT_MUSIC_PREFS)
_odysseus_start_handled = False
_music_session_initialized = False
_watcher_thread: Optional[threading.Thread] = None
_watcher_stop_event = threading.Event()
_auto_advance_lock = threading.Lock()
_was_actively_playing = False
_last_observed_eof = False
_chapters_disk_cache: Dict[str, Any] = {}
_logical_queue: List[Dict[str, Any]] = []
_logical_queue_index = -1
_active_logical: Optional[Dict[str, Any]] = None
_queue_scope: Optional[str] = None
_DEFAULT_SCOPE_SENTINEL = object()


def get_queue_scope() -> Optional[str]:
    return _queue_scope


def set_queue_scope(scope: Optional[str], *, rebuild_queue: bool = True) -> None:
    global _queue_scope
    if scope:
        cleaned = str(scope).strip("/")
        _queue_scope = cleaned if cleaned else None
    else:
        _queue_scope = None
    if rebuild_queue and _shuffle_library:
        cur = dict(_active_logical) if _active_logical else None
        if not cur and _current_track_path():
            cur = _logical_track_dict(_current_track_path())
        _rebuild_shuffle_queue(current=cur)


class LogicalTrack(TypedDict, total=False):
    path: str
    chapter_index: Optional[int]


def _mpv_launch_cmd() -> List[str]:
    cmd = [
        "mpv",
        "--config=no",
        "--idle=yes",
        "--force-window=no",
        f"--input-ipc-server={MPV_SOCKET}",
        "--no-video",
        "--vo=null",
        "--audio-display=no",
        "--ontop=no",
        "--keep-open=yes",
        "--no-terminal",
        f"--audio-client-name={MPV_PLAYER_NAME}",
        "--gapless-audio=weak",
        "--osc=no",
        "--osd-level=0",
        "--save-position-on-quit=no",
        "--ao=pipewire",
    ]
    if MPV_DAEMON_CONF.is_file():
        cmd.insert(2, f"--include={MPV_DAEMON_CONF}")
    return cmd


def _run(cmd: List[str], timeout: float = 8.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _resolve_under_root(raw: str = "") -> Path:
    root = MUSIC_ROOT.resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Music library not found: {root}")

    text = (raw or "").strip()
    if not text:
        return root

    candidate = Path(text).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()
    if resolved != root and root not in resolved.parents:
        raise PermissionError("Path outside music library")
    return resolved


def _relative_track_path(path: Path) -> Optional[str]:
    """Path relative to the library root, or None when it lives outside it.

    mpv can still hold a track from a previous ODYSSEUS_MUSIC_ROOT (for example
    after the default moved). Status and scope changes must not raise just
    because that file is no longer under the configured root.
    """
    try:
        return str(path.resolve().relative_to(MUSIC_ROOT.resolve()))
    except (OSError, ValueError):
        return None


def _is_audio(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in AUDIO_EXTS


def _invalidate_library_cache() -> None:
    global _library_cache, _library_cache_at
    _library_cache = None
    _library_cache_at = 0.0


def _library_tracks(*, force_refresh: bool = False) -> List[Path]:
    global _library_cache, _library_cache_at
    now = time.time()
    if (
        not force_refresh
        and _library_cache is not None
        and (now - _library_cache_at) < 300
    ):
        return list(_library_cache)

    root = MUSIC_ROOT.resolve()
    tracks = _collect_audio_files(root, limit=_MAX_LIBRARY)
    _library_cache = tracks
    _library_cache_at = now
    return list(tracks)


def _current_track_path() -> Optional[Path]:
    if not _mpv_responding():
        return None
    raw = _ipc_command("get_property", "path")
    if not raw:
        return None
    try:
        path = Path(str(raw)).expanduser()
    except (TypeError, ValueError):
        return None
    return path if path.is_file() else None


def _resolve_audio_file(raw: str = "") -> Path:
    """Resolve an audio file path robustly within MUSIC_ROOT.

    Supports:
    1. Direct relative or absolute path under MUSIC_ROOT (e.g. 'Unsorted/track.opus').
    2. Fallback to active mpv playback track if raw is just the filename ('track.opus').
    3. Fallback to active logical track.
    4. Fallback to searching the music library for a unique file matching the filename.
    """
    text = (raw or "").strip()
    if not text:
        raise ValueError("No file path specified")

    root = MUSIC_ROOT.resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Music library not found: {root}")

    # 1. Direct resolution check
    try:
        candidate = _resolve_under_root(text)
        if candidate.is_dir():
            raise IsADirectoryError("Cannot operate on directories via music file API")
        if candidate.is_file():
            if not _is_audio(candidate):
                raise ValueError(f"Not an audio file: {candidate.name}")
            return candidate
    except PermissionError:
        raise
    except FileNotFoundError:
        pass

    target_name = Path(text).name.strip().lower()

    # 2. Check active mpv track
    current = _current_track_path()
    if current and current.is_file() and _is_audio(current):
        res = current.resolve()
        if (root in res.parents or res == root) and res.name.lower() == target_name:
            return res

    # 3. Check active logical track
    if _active_logical and _active_logical.get("path"):
        try:
            cand_log = _resolve_under_root(str(_active_logical["path"]))
            if cand_log.is_file() and _is_audio(cand_log) and (cand_log.name.lower() == target_name):
                return cand_log.resolve()
        except Exception:
            pass

    # 4. Search library for unique filename match
    matches = [t for t in _library_tracks() if t.name.lower() == target_name]
    if len(matches) == 1:
        return matches[0].resolve()
    elif len(matches) > 1:
        raise ValueError(f"Ambiguous track filename: multiple files named '{Path(text).name}' exist")

    # If all failed, raise standard FileNotFoundError
    raise FileNotFoundError(f"Not found: {root / text}")


def _remember_track(path: Path) -> None:
    global _history_index
    rel = str(path)
    if _history_index >= 0 and _history_index < len(_play_history):
        _play_history[_history_index + 1 :] = []
    if not _play_history or _play_history[-1] != rel:
        _play_history.append(rel)
    _history_index = len(_play_history) - 1
    if len(_play_history) > 512:
        del _play_history[: len(_play_history) - 512]
        _history_index = len(_play_history) - 1

    try:
        rel_key = str(path.resolve().relative_to(MUSIC_ROOT.resolve()))
    except (ValueError, OSError):
        rel_key = str(path)
    _track_play_counts[rel_key] = _track_play_counts.get(rel_key, 0) + 1
    _mark_track_stats_dirty()


_TRACK_STATS_SAVE_INTERVAL = 10.0
_track_stats_dirty = False
_track_stats_last_save = 0.0


def _mark_track_stats_dirty() -> None:
    """Escribe music_stats.json como mucho cada 10 s (antes: una escritura por reproducción)."""
    global _track_stats_dirty
    _track_stats_dirty = True
    if time.monotonic() - _track_stats_last_save >= _TRACK_STATS_SAVE_INTERVAL:
        _flush_track_stats()


def _flush_track_stats() -> None:
    global _track_stats_dirty, _track_stats_last_save
    if not _track_stats_dirty:
        return
    _save_track_stats()
    _track_stats_dirty = False
    _track_stats_last_save = time.monotonic()


import atexit as _atexit
_atexit.register(_flush_track_stats)


def _recent_played_keys(count: int) -> set:
    """Claves relativas de los últimos `count` temas reproducidos."""
    root = MUSIC_ROOT.resolve()
    keys = set()
    for raw in _play_history[-count:] if count > 0 else []:
        try:
            keys.add(str(Path(raw).resolve().relative_to(root)))
        except (ValueError, OSError):
            keys.add(raw)
    return keys


def _filter_recent(pool: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Quita del pool lo ya reproducido recientemente para que el aleatorio no repita."""
    window = min(_RECENT_RANDOM, len(pool) // 2)
    if window <= 0:
        return pool
    recent = _recent_played_keys(window)
    if not recent:
        return pool
    return [p for p in pool if p.get("path") not in recent] or pool


def _pick_random_track(*, exclude: Optional[Path] = None) -> Path:
    tracks = _library_tracks()
    if not tracks:
        raise FileNotFoundError("No audio files in music library")

    exclude_resolved = exclude.resolve() if exclude else None
    recent = {_play_history[i] for i in range(max(0, _history_index - _RECENT_RANDOM + 1), _history_index + 1)}
    pool = [t for t in tracks if t.resolve() != exclude_resolved and str(t) not in recent]
    if not pool:
        pool = [t for t in tracks if t.resolve() != exclude_resolved]
    if not pool:
        pool = tracks
    return random.choice(pool)


def _set_sequential_queue(tracks: List[Path], *, start_index: int = 0) -> None:
    global _queue_tracks, _queue_index
    _queue_tracks = list(tracks)
    _queue_index = max(0, min(start_index, len(_queue_tracks) - 1)) if _queue_tracks else -1


def _sync_queue_index_from_mpv() -> None:
    global _queue_index
    if not _queue_tracks or not _mpv_responding():
        return
    pos = _ipc_command("get_property", "playlist-pos")
    if isinstance(pos, int) and 0 <= pos < len(_queue_tracks):
        _queue_index = pos


def _tracks_for_target(target: Path) -> tuple[List[Path], int]:
    if target.is_file():
        parent = target.parent
        siblings = _collect_audio_files(parent)
        if not siblings:
            return [target], 0
        resolved = target.resolve()
        for idx, track in enumerate(siblings):
            if track.resolve() == resolved:
                return siblings, idx
        return siblings, 0
    if target.is_dir():
        tracks = _collect_audio_files(target)
        if not tracks:
            raise FileNotFoundError(f"No audio files in {target}")
        return tracks, 0
    raise FileNotFoundError(f"Not found: {target}")


def _load_queue_from_index(index: int, *, rebuild: bool = True) -> None:
    global _queue_index
    if not _queue_tracks:
        raise RuntimeError("No playback queue")
    index = max(0, min(index, len(_queue_tracks) - 1))
    _queue_index = index

    if rebuild or _ipc_command("get_property", "playlist-count") != len(_queue_tracks):
        _ipc_command("playlist-clear")
        _ipc_command("set_property", "shuffle", False)
        _ipc_command("loadfile", str(_queue_tracks[0]), "replace")
        for track in _queue_tracks[1:]:
            _ipc_command("loadfile", str(track), "append")

    _sync_hardware_rate_for_track(_queue_tracks[index])
    _ipc_command("set_property", "playlist-pos", index)
    _ipc_command("set_property", "pause", False)
    _remember_track(_queue_tracks[index])


def _restore_queue_from_mpv() -> bool:
    """Rebuild in-memory queue after Odysseus restart (mpv playlist may still exist)."""
    if not _mpv_responding():
        return False

    count = _ipc_command("get_property", "playlist-count")
    tracks: List[Path] = []
    if isinstance(count, (int, float)) and int(count) > 0:
        for i in range(int(count)):
            entry = _ipc_command("playlist-entry-filename", i)
            if not entry:
                continue
            path = Path(str(entry)).expanduser()
            if path.is_file():
                tracks.append(path)

    if not tracks:
        current = _current_track_path()
        if not current:
            return False
        tracks, start_index = _tracks_for_target(current)
        _set_sequential_queue(tracks, start_index=start_index)
        return True

    pos = _ipc_command("get_property", "playlist-pos")
    idx = int(pos) if isinstance(pos, int) and pos >= 0 else 0
    idx = min(idx, len(tracks) - 1)
    _set_sequential_queue(tracks, start_index=idx)
    return True


def _ensure_sequential_queue() -> None:
    if _queue_tracks:
        return
    if _restore_queue_from_mpv():
        return
    current = _current_track_path()
    if current:
        tracks, start_index = _tracks_for_target(current)
        _set_sequential_queue(tracks, start_index=start_index)
        return
    raise RuntimeError("Elige una canción o carpeta en la biblioteca antes de usar siguiente/anterior")


def _sequential_next() -> Dict[str, Any]:
    if _music_prefs.get("navigate_mode") == "logical" and _logical_queue:
        global _logical_queue_index
        if _logical_queue_index + 1 < len(_logical_queue):
            _logical_queue_index += 1
            return _play_logical_track(_logical_queue[_logical_queue_index])
        if len(_logical_queue) > 1:
            _logical_queue_index = 0
            return _play_logical_track(_logical_queue[0])
    _ensure_sequential_queue()

    if _queue_index + 1 < len(_queue_tracks):
        _load_queue_from_index(_queue_index + 1, rebuild=False)
    elif len(_queue_tracks) > 1:
        _load_queue_from_index(0, rebuild=False)
    else:
        ensure_mpv()
        _ipc_command("set_property", "pause", True)

    return {"action": "next", "status": get_status()}


def _sequential_previous() -> Dict[str, Any]:
    if _music_prefs.get("navigate_mode") == "logical" and _logical_queue:
        global _logical_queue_index
        if _logical_queue_index > 0:
            _logical_queue_index -= 1
            return _play_logical_track(_logical_queue[_logical_queue_index])
        if len(_logical_queue) > 1:
            _logical_queue_index = len(_logical_queue) - 1
            return _play_logical_track(_logical_queue[_logical_queue_index])
    _ensure_sequential_queue()

    if _queue_index > 0:
        _load_queue_from_index(_queue_index - 1, rebuild=False)
    elif len(_queue_tracks) > 1:
        _load_queue_from_index(len(_queue_tracks) - 1, rebuild=False)
    else:
        ensure_mpv()
        _ipc_command("set_property", "pause", True)

    return {"action": "previous", "status": get_status()}


def _play_single(path: Path) -> Dict[str, Any]:
    ensure_mpv()
    _ipc_command("playlist-clear")
    _ipc_command("set_property", "shuffle", False)
    _sync_hardware_rate_for_track(path)
    _ipc_command("loadfile", str(path), "replace")
    _ipc_command("set_property", "pause", False)
    _remember_track(path)
    return {
        "action": "play",
        "count": 1,
        "shuffle": _shuffle_library,
        "first": str(path),
        "status": get_status(),
    }


def _collect_audio_files(folder: Path, *, limit: int = 5000) -> List[Path]:
    files: List[Path] = []
    for dirpath, dirnames, filenames in os.walk(folder):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for name in sorted(filenames):
            if name.startswith("."):
                continue
            path = Path(dirpath) / name
            if _is_audio(path):
                files.append(path)
                if len(files) >= limit:
                    return files
    return files


def _ipc_command(*args: Any, timeout: float = 2.0) -> Any:
    payload = json.dumps({"command": list(args)}) + "\n"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        sock.connect(str(MPV_SOCKET))
        sock.sendall(payload.encode("utf-8"))
        chunks: List[bytes] = []
        while True:
            part = sock.recv(4096)
            if not part:
                break
            chunks.append(part)
            if b"\n" in part:
                break
    if not chunks:
        return None
    line = b"".join(chunks).split(b"\n", 1)[0]
    try:
        data = json.loads(line.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return None
    if isinstance(data, dict) and data.get("error") == "success":
        return data.get("data")
    return data


def _ipc_property_float(name: str, default: float) -> float:
    """Propiedad numérica de mpv con valor por defecto si mpv no responde."""
    try:
        value = _ipc_command("get_property", name, timeout=0.5)
        return float(value) if value is not None else default
    except Exception:
        return default


def _play_queue_index(index: int) -> None:
    """Reproduce la pista `index` de la cola secuencial (reconstruye mpv si hace falta)."""
    ensure_mpv()
    _load_queue_from_index(index, rebuild=True)


def _mpv_responding() -> bool:
    if not MPV_SOCKET.exists():
        return False
    try:
        _ipc_command("get_property", "idle-active")
        return True
    except OSError:
        return False


def ensure_mpv() -> None:
    if _mpv_responding():
        return

    if MPV_SOCKET.exists():
        try:
            MPV_SOCKET.unlink()
        except OSError:
            pass

    MPV_SOCKET.parent.mkdir(parents=True, exist_ok=True)
    cmd = _mpv_launch_cmd()
    subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )

    for _ in range(30):
        if _mpv_responding():
            return
        time.sleep(0.1)
    raise RuntimeError("mpv did not start")


def _playerctl_base() -> List[str]:
    return ["playerctl", f"--player={MPV_PLAYER_NAME}"]


def _playerctl_available() -> bool:
    if shutil.which("playerctl") is None:
        return False
    proc = _run(["playerctl", "--list-all"], timeout=3.0)
    if proc.returncode != 0:
        return False
    names = {line.strip() for line in proc.stdout.splitlines() if line.strip()}
    if MPV_PLAYER_NAME in names:
        return True
    return "mpv" in names


def _playerctl_cmd(*extra: str) -> subprocess.CompletedProcess[str]:
    base = _playerctl_base()
    proc = _run(base + list(extra))
    if proc.returncode != 0 and MPV_PLAYER_NAME != "mpv":
        return _run(["playerctl", "--player=mpv"] + list(extra))
    return proc


def _load_chapters_disk_cache() -> None:
    global _chapters_disk_cache
    if _chapters_disk_cache:
        return
    try:
        if _CHAPTER_CACHE_FILE.is_file():
            raw = json.loads(_CHAPTER_CACHE_FILE.read_text(encoding="utf-8"))
            _chapters_disk_cache = raw if isinstance(raw, dict) else {}
        else:
            _chapters_disk_cache = {}
    except (OSError, json.JSONDecodeError):
        _chapters_disk_cache = {}


def _save_chapters_disk_cache() -> None:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    _CHAPTER_CACHE_FILE.write_text(
        json.dumps(_chapters_disk_cache, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _chapters_via_ffprobe(path: Path) -> List[Dict[str, Any]]:
    if shutil.which("ffprobe") is None:
        return []
    proc = _run(
        [
            "ffprobe",
            "-hide_banner",
            "-loglevel",
            "error",
            "-show_chapters",
            "-of",
            "json",
            str(path),
        ],
        timeout=30.0,
    )
    if proc.returncode != 0:
        return []
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return []
    chapters: List[Dict[str, Any]] = []
    for idx, ch in enumerate(data.get("chapters") or []):
        try:
            start = float(ch.get("start_time") or ch.get("start", 0) / 1000.0)
            end = float(ch.get("end_time") or ch.get("end", 0) / 1000.0)
        except (TypeError, ValueError):
            continue
        tags = ch.get("tags") if isinstance(ch.get("tags"), dict) else {}
        title = str(tags.get("title") or f"Capítulo {idx + 1}")
        chapters.append(
            {"index": idx, "title": title, "start_sec": start, "end_sec": end}
        )
    return chapters


def _chapters_via_mpv_loaded(path: Path) -> List[Dict[str, Any]]:
    if not _mpv_responding():
        return []
    loaded = _ipc_command("get_property", "path")
    if not loaded:
        return []
    try:
        if Path(str(loaded)).resolve() != path.resolve():
            return []
    except OSError:
        return []
    raw = _ipc_command("get_property", "chapter-list")
    if not isinstance(raw, list) or not raw:
        return []
    chapters: List[Dict[str, Any]] = []
    for idx, entry in enumerate(raw):
        if not isinstance(entry, dict):
            continue
        try:
            start = float(entry.get("time", 0.0))
        except (TypeError, ValueError):
            start = 0.0
        title = str(entry.get("title") or f"Capítulo {idx + 1}")
        end = start
        if idx + 1 < len(raw):
            try:
                end = float(raw[idx + 1].get("time", start))
            except (TypeError, ValueError):
                end = start
        chapters.append(
            {"index": idx, "title": title, "start_sec": start, "end_sec": end}
        )
    if chapters:
        dur = _ipc_command("get_property", "duration")
        try:
            file_dur = float(dur or 0.0)
        except (TypeError, ValueError):
            file_dur = 0.0
        if file_dur > chapters[-1]["start_sec"]:
            chapters[-1]["end_sec"] = file_dur
    return chapters


def get_chapters_for_path(raw: str = "") -> List[Dict[str, Any]]:
    """Return chapter list for a library file (mpv if loaded, else ffprobe cache)."""
    path = _resolve_under_root(raw) if raw else Path()
    if raw:
        if not path.is_file():
            raise FileNotFoundError(f"Not found: {path}")
    else:
        path = _current_track_path() or Path()
        if not path.is_file():
            return []

    loaded = _chapters_via_mpv_loaded(path)
    if loaded:
        return loaded

    _load_chapters_disk_cache()
    key = str(path.resolve())
    mtime = path.stat().st_mtime
    cached = _chapters_disk_cache.get(key)
    if isinstance(cached, dict) and cached.get("mtime") == mtime:
        items = cached.get("chapters")
        if isinstance(items, list):
            return list(items)

    chapters = _chapters_via_ffprobe(path)
    _chapters_disk_cache[key] = {"mtime": mtime, "chapters": chapters}
    _save_chapters_disk_cache()
    return chapters


def reindex_chapters(*, force: bool = False) -> Dict[str, Any]:
    _load_chapters_disk_cache()
    if force:
        _chapters_disk_cache.clear()
    count = 0
    for track in _library_tracks(force_refresh=force):
        ch = get_chapters_for_path(str(track.relative_to(MUSIC_ROOT.resolve())))
        if ch:
            count += 1
    return {"indexed_files_with_chapters": count}


def load_music_prefs_for_user(user: Optional[str] = None) -> Dict[str, Any]:
    global _music_prefs, _shuffle_library, _music_session_initialized, MUSIC_ROOT, _library_cache
    try:
        from routes.prefs_routes import _load_for_user

        blob = _load_for_user(user) or {}
        stored = blob.get("music")
        merged = dict(DEFAULT_MUSIC_PREFS)
        if isinstance(stored, dict):
            merged.update(stored)
        _music_prefs = merged
    except Exception:
        _music_prefs = dict(DEFAULT_MUSIC_PREFS)
    custom_root = _music_prefs.get("music_root")
    if custom_root and isinstance(custom_root, str) and custom_root.strip():
        p = Path(custom_root.strip()).expanduser()
        if p.is_dir() and p.resolve() != MUSIC_ROOT.resolve():
            MUSIC_ROOT = p
            _library_cache = None
    if not _music_session_initialized:
        _music_session_initialized = True
        _shuffle_library = bool(_music_prefs.get("shuffle_enabled", True))
        init_music_subsystem()
    return dict(_music_prefs)


def _auto_advance_tick() -> None:
    global _was_actively_playing, _last_observed_eof
    if not _mpv_responding():
        _was_actively_playing = False
        _last_observed_eof = False
        return

    try:
        pause = _ipc_command("get_property", "pause", timeout=0.3)
        eof = _ipc_command("get_property", "eof-reached", timeout=0.3)
    except Exception:
        return

    is_paused = bool(pause) if pause is not None else True
    is_eof = bool(eof) if eof is not None else False

    with _auto_advance_lock:
        if is_eof and not _last_observed_eof and _was_actively_playing:
            _last_observed_eof = True
            _was_actively_playing = False
            if _music_prefs.get("auto_advance", True):
                logger.info("Auto-advance: track reached EOF, advancing queue")
                try:
                    if _shuffle_library:
                        _shuffle_next()
                    elif (_logical_queue and len(_logical_queue) > 1) or (_queue_tracks and len(_queue_tracks) > 1):
                        _sequential_next()
                except Exception as exc:
                    logger.warning("Auto-advance advance failed: %s", exc)
            return

        if not is_eof:
            _last_observed_eof = False

        if not is_paused and not is_eof:
            _was_actively_playing = True
        elif is_paused:
            _was_actively_playing = False


def _auto_advance_loop() -> None:
    while not _watcher_stop_event.is_set():
        try:
            _auto_advance_tick()
        except Exception as exc:
            logger.debug("auto_advance loop error: %s", exc)
        _watcher_stop_event.wait(0.5)


def music_runtime_enabled() -> bool:
    """False when ODYSSEUS_MUSIC_RUNTIME=0 (set by the test suite): no boot pause,
    no preloaded track, no watcher thread, so importing the app never touches mpv."""
    return os.getenv("ODYSSEUS_MUSIC_RUNTIME", "1") != "0"


def start_auto_advance_watcher() -> None:
    global _watcher_thread
    if not music_runtime_enabled():
        return
    if _watcher_thread and _watcher_thread.is_alive():
        return
    _watcher_stop_event.clear()
    _watcher_thread = threading.Thread(
        target=_auto_advance_loop,
        name="odysseus-music-watcher",
        daemon=True,
    )
    _watcher_thread.start()
    logger.info("odysseus-music-watcher thread started")


def stop_auto_advance_watcher() -> None:
    _watcher_stop_event.set()


def save_music_state() -> None:
    try:
        cur = _current_track_path()
        if not cur or not cur.is_file():
            return
        pos = _ipc_property_float("time-pos", 0.0)
        vol = _ipc_property_float("volume", 80.0)
        state = {
            "path": str(cur),
            "time_pos": pos,
            "volume": vol,
            "shuffle": _shuffle_library,
            "chapter_index": _active_logical.get("chapter_index") if _active_logical else None,
        }
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(_MUSIC_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
    except Exception as exc:
        logger.debug("save_music_state error: %s", exc)


def load_saved_music_state() -> Optional[Dict[str, Any]]:
    if not _MUSIC_STATE_FILE.is_file():
        return None
    try:
        with open(_MUSIC_STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        logger.warning("Failed to read music_state.json: %s", exc)
        return None


def init_music_subsystem() -> None:
    """Initialize music module: apply startup prefs once and start watcher."""
    global _odysseus_start_handled
    if not music_runtime_enabled():
        return
    _load_track_stats()
    if _odysseus_start_handled:
        return
    _odysseus_start_handled = True

    try:
        ensure_mpv()
        mode = str(_music_prefs.get("service_start_mode") or "pause_last")
        logger.info("init_music_subsystem: mode=%s", mode)
        if mode == "random_library" and _music_prefs.get("shuffle_enabled", True):
            _set_shuffle_mode(True)
        if mode != "resume_last" and _mpv_responding():
            try:
                _ipc_command("set_property", "pause", True)
                logger.info("init_music_subsystem: boot pause applied to mpv")
            except OSError:
                pass
        if _current_track_path() is None and _mpv_responding():
            saved = load_saved_music_state()
            if saved and Path(saved.get("path", "")).is_file():
                try:
                    track = Path(saved["path"])
                    _ipc_command("loadfile", str(track), "replace")
                    _ipc_command("set_property", "pause", True)
                    t_pos = float(saved.get("time_pos", 0.0))
                    if t_pos > 0:
                        _ipc_command("seek", t_pos, "absolute")
                    vol = float(saved.get("volume", 80.0))
                    _ipc_command("set_property", "volume", vol)
                    _remember_track(track)
                    logger.info("init_music_subsystem: restored saved track paused: %s at %.1fs", track.name, t_pos)
                except Exception as e:
                    logger.warning("init_music_subsystem could not restore saved track: %s", e)
            else:
                try:
                    track = _pick_random_track()
                    _ipc_command("loadfile", str(track), "replace")
                    _ipc_command("set_property", "pause", True)
                    _remember_track(track)
                    logger.info("init_music_subsystem: preloaded track paused: %s", track.name)
                except Exception as e:
                    logger.warning("init_music_subsystem could not preload track: %s", e)
    except Exception as exc:
        logger.warning("init_music_subsystem startup hook failed: %s", exc)

    start_auto_advance_watcher()


def maybe_apply_service_start() -> None:
    init_music_subsystem()


def _sync_shuffle_pref(enabled: bool) -> None:
    global _shuffle_library
    _shuffle_library = bool(enabled)
    _music_prefs["shuffle_enabled"] = bool(enabled)


def save_music_prefs_for_user(user: Optional[str], updates: Dict[str, Any]) -> Dict[str, Any]:
    global _music_prefs, MUSIC_ROOT, _library_cache
    from routes.prefs_routes import _load_for_user, _save_for_user

    prefs = dict(_load_for_user(user) or {})
    music = prefs.get("music")
    music = dict(music) if isinstance(music, dict) else {}
    allowed = set(DEFAULT_MUSIC_PREFS.keys())
    for key, val in updates.items():
        if key in allowed:
            music[key] = val
    prefs["music"] = music
    _save_for_user(user, prefs)
    _music_prefs = {**DEFAULT_MUSIC_PREFS, **music}
    custom_root = _music_prefs.get("music_root")
    if custom_root and isinstance(custom_root, str) and custom_root.strip():
        p = Path(custom_root.strip()).expanduser()
        if p.is_dir() and p.resolve() != MUSIC_ROOT.resolve():
            MUSIC_ROOT = p
            _library_cache = None
            logger.info("Music library root changed to: %s", MUSIC_ROOT)
    if "shuffle_enabled" in updates:
        _sync_shuffle_pref(bool(updates["shuffle_enabled"]))
    if updates.get("shuffle_enabled") is True:
        _set_shuffle_mode(True)
    elif updates.get("shuffle_enabled") is False:
        _set_shuffle_mode(False)
    if "remote_audio" in updates:
        is_remote = bool(updates["remote_audio"])
        try:
            if _mpv_responding():
                _ipc_command("set_property", "mute", is_remote)
        except Exception as exc:
            logger.debug("Failed to set mpv mute for remote audio: %s", exc)
    return dict(_music_prefs)


def get_music_prefs() -> Dict[str, Any]:
    return dict(_music_prefs)


def _playback_window(path: Optional[Path] = None) -> Tuple[float, float, int, str]:
    """Effective start/end (sec), chapter index, chapter title for progress UI."""
    path = path or _current_track_path()
    if not path or not path.is_file():
        return 0.0, 0.0, -1, ""

    chapter_idx = -1
    title = ""
    if _mpv_responding():
        try:
            loaded = Path(str(_ipc_command("get_property", "path") or "")).resolve()
            if loaded == path.resolve():
                ch = _ipc_command("get_property", "chapter")
                if isinstance(ch, int) and ch >= 0:
                    chapter_idx = ch
        except OSError:
            pass

    if _active_logical and _active_logical.get("chapter_index") is not None:
        if not chapter_idx >= 0:
            chapter_idx = int(_active_logical["chapter_index"])

    rel = _relative_track_path(path)
    chapters = get_chapters_for_path(rel) if rel else []
    if chapter_idx >= 0 and chapters:
        if chapter_idx < len(chapters):
            ch = chapters[chapter_idx]
            return (
                float(ch["start_sec"]),
                float(ch["end_sec"]),
                chapter_idx,
                str(ch.get("title") or ""),
            )

    dur = 0.0
    if _mpv_responding():
        try:
            dur = float(_ipc_command("get_property", "duration") or 0.0)
        except (TypeError, ValueError):
            dur = 0.0
    if dur <= 0 and chapters:
        dur = float(chapters[-1].get("end_sec") or 0.0)
    return 0.0, dur, chapter_idx, title


def _logical_track_dict(path: Path, chapter_index: Optional[int] = None) -> Dict[str, Any]:
    rel = _relative_track_path(path) or str(path)
    return {"path": rel, "chapter_index": chapter_index}


def _build_logical_pool(
    *, include_chapters: Optional[bool] = None, scope: Any = _DEFAULT_SCOPE_SENTINEL
) -> List[Dict[str, Any]]:
    target_scope = _queue_scope if scope is _DEFAULT_SCOPE_SENTINEL else scope
    include = (
        _music_prefs.get("shuffle_chapters", True)
        if include_chapters is None
        else include_chapters
    )
    pool: List[Dict[str, Any]] = []
    for track in _library_tracks():
        rel = str(track.relative_to(MUSIC_ROOT.resolve()))
        if target_scope:
            clean_scope = target_scope.strip("/")
            if clean_scope == "__favorites__":
                _load_favorites()
                if rel.strip("/") not in _favorites:
                    continue
            else:
                rel_clean = rel.strip("/")
                if (
                    rel_clean != clean_scope
                    and not rel_clean.startswith(clean_scope + "/")
                    and rel_clean.lower() != clean_scope.lower()
                    and not rel_clean.lower().startswith(clean_scope.lower() + "/")
                ):
                    continue
        chapters = get_chapters_for_path(rel) if include else []
        if include and chapters:
            for ch in chapters:
                pool.append(_logical_track_dict(track, int(ch["index"])))
        else:
            pool.append(_logical_track_dict(track, None))
    return pool


def _logical_same(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    return a.get("path") == b.get("path") and a.get("chapter_index") == b.get(
        "chapter_index"
    )


def _pick_random_logical(
    *, exclude: Optional[Dict[str, Any]] = None, scope: Any = _DEFAULT_SCOPE_SENTINEL
) -> Dict[str, Any]:
    pool = _build_logical_pool(scope=scope)
    if not pool:
        raise FileNotFoundError("No audio files in music library for requested scope")

    if exclude:
        pool = [p for p in pool if not _logical_same(p, exclude)]
    if not pool:
        pool = _build_logical_pool(scope=scope)
    return random.choice(_filter_recent(pool))


def _rebuild_shuffle_queue(
    *, current: Optional[Dict[str, Any]] = None, scope: Any = _DEFAULT_SCOPE_SENTINEL
) -> None:
    """Baraja el pool de biblioteca en cola lógica para que la UI muestre próximas pistas."""
    pool = _build_logical_pool(scope=scope)
    if not pool:
        _set_logical_queue([])
        return
    shuffled = _filter_recent(list(pool))
    random.shuffle(shuffled)
    if current:
        shuffled = [p for p in shuffled if not _logical_same(p, current)]
        shuffled.insert(0, dict(current))
    _set_logical_queue(shuffled, start_index=0)


def _play_logical_track(track: Dict[str, Any]) -> Dict[str, Any]:
    global _active_logical, _logical_queue_index, _queue_index
    rel = track.get("path") or ""
    path = _resolve_under_root(rel)
    if not path.is_file():
        raise FileNotFoundError(f"Not found: {path}")

    chapter_index = track.get("chapter_index")
    ensure_mpv()
    _ipc_command("playlist-clear")
    _ipc_command("set_property", "shuffle", False)
    _sync_hardware_rate_for_track(path)
    _ipc_command("loadfile", str(path), "replace")
    if chapter_index is not None:
        _ipc_command("set_property", "chapter", int(chapter_index))
    _ipc_command("set_property", "pause", False)
    _remember_track(path)
    _active_logical = dict(track)
    if _queue_tracks:
        for i, queued in enumerate(_queue_tracks):
            try:
                qrel = str(queued.relative_to(MUSIC_ROOT.resolve()))
            except ValueError:
                qrel = str(queued)
            if qrel == rel or str(queued) == rel:
                _queue_index = i
                break
    if _logical_queue:
        for i, item in enumerate(_logical_queue):
            if item.get("path") == track.get("path") and item.get(
                "chapter_index"
            ) == track.get("chapter_index"):
                _logical_queue_index = i
                break
    return {
        "action": "play",
        "logical_track": dict(track),
        "status": get_status(),
    }


def _set_logical_queue(tracks: List[Dict[str, Any]], *, start_index: int = 0) -> None:
    global _logical_queue, _logical_queue_index, _queue_tracks, _queue_index
    _logical_queue = list(tracks)
    _logical_queue_index = (
        max(0, min(start_index, len(_logical_queue) - 1)) if _logical_queue else -1
    )
    _queue_tracks = []
    _queue_index = -1


def get_queue_state() -> Dict[str, Any]:
    if not _logical_queue and _shuffle_library:
        cur = dict(_active_logical) if _active_logical else None
        if not cur and _current_track_path():
            cur = _logical_track_dict(_current_track_path())
        _rebuild_shuffle_queue(current=cur)
    items = []
    for i, lt in enumerate(_logical_queue):
        label = lt.get("path", "")
        if lt.get("chapter_index") is not None:
            label = f"{label} · cap {int(lt['chapter_index']) + 1}"
        items.append({"index": i, "logical_track": dict(lt), "label": label})
    return {
        "queue": items,
        "queue_index": _logical_queue_index,
        "shuffle": _shuffle_library,
        "logical": bool(_logical_queue),
    }


def clear_queue() -> Dict[str, Any]:
    global _logical_queue, _logical_queue_index
    _logical_queue = []
    _logical_queue_index = -1
    _set_sequential_queue([])
    return get_queue_state()


def queue_jump(index: int) -> Dict[str, Any]:
    if not _logical_queue:
        raise RuntimeError("Cola vacía")
    idx = max(0, min(int(index), len(_logical_queue) - 1))
    return _play_logical_track(_logical_queue[idx])


def queue_remove(index: int) -> Dict[str, Any]:
    global _logical_queue_index
    if not _logical_queue:
        raise RuntimeError("Cola vacía")
    idx = int(index)
    if idx < 0 or idx >= len(_logical_queue):
        raise IndexError("Índice fuera de rango")
    _logical_queue.pop(idx)
    if _logical_queue_index >= len(_logical_queue):
        _logical_queue_index = len(_logical_queue) - 1
    return get_queue_state()


def _seek_playback(
    *,
    seconds: Optional[float] = None,
    percent: Optional[float] = None,
    relative: Optional[float] = None,
) -> None:
    ensure_mpv()
    start, end, _, _ = _playback_window()
    window = max(0.0, end - start) if end > start else float(
        _ipc_command("get_property", "duration") or 0.0
    )
    pos = float(_ipc_command("get_property", "time-pos") or 0.0)
    if relative is not None:
        target = pos + float(relative)
    elif percent is not None:
        pct = max(0.0, min(100.0, float(percent)))
        target = start + window * (pct / 100.0)
    elif seconds is not None:
        target = start + float(seconds) if window and start else float(seconds)
    else:
        raise ValueError("seek needs seconds, percent, or relative")
    if end > start:
        target = max(start, min(end - 0.05, target))
    _ipc_command("seek", target, "absolute")


def _ffprobe_audio_stream(path: Path) -> Dict[str, Any]:
    if not path.is_file() or shutil.which("ffprobe") is None:
        return {}
    proc = _run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=codec_name,sample_rate,bit_rate,channels,bits_per_raw_sample,bits_per_sample",
            "-of",
            "json",
            str(path),
        ],
        timeout=12.0,
    )
    if proc.returncode != 0:
        return {}
    try:
        data = json.loads(proc.stdout or "{}")
        streams = data.get("streams") or []
        if not streams:
            return {}
        st = streams[0]
        bits = int(st.get("bits_per_raw_sample") or 0) or int(
            st.get("bits_per_sample") or 0
        )
        return {
            "codec": str(st.get("codec_name") or ""),
            "samplerate_hz": int(float(st.get("sample_rate") or 0) or 0),
            "bitrate_kbps": int((int(st.get("bit_rate") or 0) or 0) / 1000),
            "channels": int(st.get("channels") or 0),
            "bits_per_sample": bits,
        }
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}


def _set_pipewire_clock_rate(samplerate_hz: int) -> None:
    """Fuerza la tasa de reloj del hardware en PipeWire para reproducción Bit-Perfect."""
    if not (44100 <= samplerate_hz <= 384000):
        return
    try:
        subprocess.run(
            ["pw-metadata", "-n", "settings", "0", "clock.force-rate", str(samplerate_hz)],
            capture_output=True,
            timeout=2.0,
            check=False,
        )
        logger.info("PipeWire clock.force-rate ajustado a %d Hz", samplerate_hz)
    except Exception as exc:
        logger.warning("No se pudo ajustar clock.force-rate en PipeWire: %s", exc)


def _reset_pipewire_clock_rate() -> None:
    """Restaura el reloj de PipeWire a modo dinámico (0)."""
    try:
        subprocess.run(
            ["pw-metadata", "-n", "settings", "0", "clock.force-rate", "0"],
            capture_output=True,
            timeout=2.0,
            check=False,
        )
        logger.info("PipeWire clock.force-rate restaurado a modo dinámico (0)")
    except Exception as exc:
        logger.warning("No se pudo restaurar clock.force-rate en PipeWire: %s", exc)


def _sync_hardware_rate_for_track(path: Path) -> None:
    """Lee la frecuencia nativa de la pista y sincroniza el DAC vía PipeWire."""
    if not path.is_file():
        return
    probe = _ffprobe_audio_stream(path)
    sr = int(probe.get("samplerate_hz") or 0)
    if sr >= 44100:
        _set_pipewire_clock_rate(sr)


def _lossless_ext(path: Path) -> bool:
    return path.suffix.lower() in {".flac", ".wav", ".ape", ".wv"}



_CODEC_SHORT: Dict[str, str] = {
    "mp3": "MP3",
    "flac": "FLAC",
    "opus": "Opus",
    "vorbis": "Vorbis",
    "aac": "AAC",
    "alac": "ALAC",
    "wav": "WAV",
    "ape": "APE",
    "wv": "WavPack",
    "m4a": "AAC",
    "ogg": "Vorbis",
}


def _short_codec_name(codec: str) -> str:
    raw = (codec or "").strip()
    if "(" in raw:
        raw = raw.split("(", 1)[0].strip()
    low = raw.lower()
    if low in _CODEC_SHORT:
        return _CODEC_SHORT[low]
    for key, label in _CODEC_SHORT.items():
        if key in low:
            return label
    if not raw:
        return ""
    token = raw.split()[0]
    return token.upper() if len(token) <= 8 else token[:8].upper()


_AUDIO_QUALITY_CACHE: Dict[str, Dict[str, Any]] = {}


def _load_quality_cache() -> None:
    global _AUDIO_QUALITY_CACHE
    if _AUDIO_QUALITY_CACHE:
        return
    if not _MUSIC_QUALITY_CACHE_FILE.is_file():
        return
    try:
        with open(_MUSIC_QUALITY_CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                _AUDIO_QUALITY_CACHE = data
    except Exception as exc:
        logger.debug("load_quality_cache error: %s", exc)


def _save_quality_cache() -> None:
    global _quality_cache_dirty
    if not _quality_cache_dirty:
        return
    try:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(_MUSIC_QUALITY_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(_AUDIO_QUALITY_CACHE, f, indent=2, ensure_ascii=False)
        _quality_cache_dirty = False
    except Exception as exc:
        logger.debug("save_quality_cache error: %s", exc)


def _parse_flac_header(path: Path) -> Optional[Dict[str, Any]]:
    """Extrae metadatos de audio leyendo directamente STREAMINFO del archivo FLAC."""
    try:
        with open(path, "rb") as f:
            if f.read(4) != b"fLaC":
                return None
            block_hdr = f.read(4)
            if len(block_hdr) < 4:
                return None
            block_len = int.from_bytes(block_hdr[1:4], "big")
            streaminfo = f.read(block_len)
            if len(streaminfo) < 18:
                return None
            sr_chan_bps = int.from_bytes(streaminfo[10:18], "big")
            sr = sr_chan_bps >> 44
            channels = ((sr_chan_bps >> 41) & 0x07) + 1
            bps = ((sr_chan_bps >> 36) & 0x1F) + 1
            total_samples = sr_chan_bps & 0x0FFFFFFFFF
            duration = round(total_samples / sr, 2) if sr > 0 else 0
            is_hires = bps > 16 or sr > 48000
            sr_khz = sr / 1000.0
            sr_str = f"{int(sr_khz)} kHz" if sr_khz.is_integer() else f"{sr_khz:.1f} kHz"
            try:
                st_size = path.stat().st_size
                bitrate_kbps = int((st_size * 8) / (duration * 1000)) if duration > 0 else 0
            except OSError:
                bitrate_kbps = 0

            parts = [f"{bps}-bit", sr_str]
            if bitrate_kbps > 0:
                parts.append(f"{bitrate_kbps} kbps")
            parts.append("FLAC")
            label = " · ".join(parts)
            return {
                "codec": "FLAC",
                "samplerate_hz": sr,
                "bits_per_sample": bps,
                "channels": channels,
                "duration_sec": duration,
                "bitrate_kbps": bitrate_kbps,
                "quality_label": label,
                "is_hires": is_hires,
                "lossless": True,
            }
    except Exception:
        return None


def _parse_opus_header(path: Path) -> Optional[Dict[str, Any]]:
    """Extrae metadatos de audio leyendo OpusHead sin subprocesos."""
    try:
        with open(path, "rb") as f:
            head = f.read(128)
            idx = head.find(b"OpusHead")
            if idx == -1 or len(head) < idx + 16:
                return None
            channels = head[idx + 9]
            sr = int.from_bytes(head[idx + 12 : idx + 16], "little")
            if sr == 0:
                sr = 48000
            sr_khz = sr / 1000.0
            sr_str = f"{int(sr_khz)} kHz" if sr_khz.is_integer() else f"{sr_khz:.1f} kHz"
            return {
                "codec": "OPUS",
                "samplerate_hz": sr,
                "bits_per_sample": 0,
                "channels": channels,
                "bitrate_kbps": 0,
                "quality_label": f"{sr_str} · OPUS",
                "is_hires": False,
                "lossless": False,
            }
    except Exception:
        return None


_MP3_BITRATES = [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0]
_MP3_SAMPLERATES = [44100, 48000, 32000, 0]


def _parse_mp3_header(path: Path) -> Optional[Dict[str, Any]]:
    """Extrae metadatos de audio leyendo el primer frame MPEG sin subprocesos."""
    try:
        with open(path, "rb") as f:
            data = f.read(8192)
        idx = 0
        if data[:3] == b"ID3" and len(data) >= 10:
            sz = data[6:10]
            id3_sz = (sz[0] << 21) | (sz[1] << 14) | (sz[2] << 7) | sz[3]
            idx = 10 + id3_sz
            if idx >= len(data):
                with open(path, "rb") as f:
                    f.seek(idx)
                    data = f.read(8192)
                idx = 0
        for i in range(idx, len(data) - 4):
            if data[i] == 0xFF and (data[i + 1] & 0xE0) == 0xE0:
                v_bits = (data[i + 1] >> 3) & 0x03
                l_bits = (data[i + 1] >> 1) & 0x03
                if v_bits == 3 and l_bits == 1:
                    br_idx = (data[i + 2] >> 4) & 0x0F
                    sr_idx = (data[i + 2] >> 2) & 0x03
                    channels = 1 if ((data[i + 3] >> 6) & 0x03) == 3 else 2
                    br = _MP3_BITRATES[br_idx]
                    sr = _MP3_SAMPLERATES[sr_idx]
                    if br > 0 and sr > 0:
                        sr_khz = sr / 1000.0
                        sr_str = f"{int(sr_khz)} kHz" if sr_khz.is_integer() else f"{sr_khz:.1f} kHz"
                        parts = [sr_str, f"{br} kbps", "MP3"]
                        return {
                            "codec": "MP3",
                            "samplerate_hz": sr,
                            "bits_per_sample": 0,
                            "channels": channels,
                            "bitrate_kbps": br,
                            "quality_label": " · ".join(parts),
                            "is_hires": False,
                            "lossless": False,
                        }
        return None
    except Exception:
        return None


def _parse_m4a_header(path: Path) -> Optional[Dict[str, Any]]:
    """Extrae metadatos de audio analizando atom mp4a sin subprocesos."""
    try:
        with open(path, "rb") as f:
            for _ in range(16):
                hdr = f.read(8)
                if len(hdr) < 8:
                    break
                sz = int.from_bytes(hdr[:4], "big")
                atype = hdr[4:8]
                if sz == 1:
                    sz = int.from_bytes(f.read(8), "big")
                    if sz < 16:
                        break
                    f.seek(sz - 16, 1)
                elif sz == 0:
                    break
                elif atype == b"moov":
                    moov = f.read(min(sz - 8, 250000))
                    idx = moov.find(b"mp4a")
                    if idx != -1 and len(moov) >= idx + 36:
                        ch = int.from_bytes(moov[idx + 20 : idx + 22], "big")
                        sr_raw = int.from_bytes(moov[idx + 28 : idx + 32], "big")
                        sr = sr_raw >> 16
                        sr_khz = sr / 1000.0
                        sr_str = f"{int(sr_khz)} kHz" if sr_khz.is_integer() else f"{sr_khz:.1f} kHz"
                        return {
                            "codec": "AAC",
                            "samplerate_hz": sr,
                            "bits_per_sample": 0,
                            "channels": ch,
                            "bitrate_kbps": 0,
                            "quality_label": f"{sr_str} · AAC",
                            "is_hires": False,
                            "lossless": False,
                        }
                    break
                else:
                    f.seek(sz - 8, 1)
        return None
    except Exception:
        return None


def _parse_wav_header(path: Path) -> Optional[Dict[str, Any]]:
    """Extrae metadatos de audio de encabezado RIFF/WAVE sin subprocesos."""
    try:
        with open(path, "rb") as f:
            riff = f.read(12)
            if len(riff) < 12 or riff[:4] != b"RIFF" or riff[8:12] != b"WAVE":
                return None
            while True:
                chdr = f.read(8)
                if len(chdr) < 8:
                    break
                cid = chdr[:4]
                csz = int.from_bytes(chdr[4:8], "little")
                if cid == b"fmt " and csz >= 16:
                    fmt_data = f.read(min(csz, 32))
                    channels = int.from_bytes(fmt_data[2:4], "little")
                    sr = int.from_bytes(fmt_data[4:8], "little")
                    bps = int.from_bytes(fmt_data[14:16], "little")
                    is_hires = bps > 16 or sr > 48000
                    sr_khz = sr / 1000.0
                    sr_str = f"{int(sr_khz)} kHz" if sr_khz.is_integer() else f"{sr_khz:.1f} kHz"
                    return {
                        "codec": "WAV",
                        "samplerate_hz": sr,
                        "bits_per_sample": bps,
                        "channels": channels,
                        "bitrate_kbps": 0,
                        "quality_label": f"{bps}-bit · {sr_str} · WAV",
                        "is_hires": is_hires,
                        "lossless": True,
                    }
                f.seek(csz, 1)
        return None
    except Exception:
        return None


def get_track_audio_quality(path: Path, *, fast_only: bool = True) -> Dict[str, Any]:
    """Retorna resolucion tecnica de audio con cache persistente y cero subprocesos en lote."""
    if not path.is_file():
        return {}
    _load_quality_cache()
    try:
        st = path.stat()
        cache_key = f"{path.resolve()}:{st.st_mtime}:{st.st_size}:v5"
    except OSError:
        return {}

    if cache_key in _AUDIO_QUALITY_CACHE:
        return _AUDIO_QUALITY_CACHE[cache_key]

    ext = path.suffix.lower()
    info = None
    if ext == ".flac":
        info = _parse_flac_header(path)
    elif ext == ".opus":
        info = _parse_opus_header(path)
    elif ext == ".mp3":
        info = _parse_mp3_header(path)
    elif ext == ".m4a":
        info = _parse_m4a_header(path)
    elif ext == ".wav":
        info = _parse_wav_header(path)

    if not info:
        if fast_only:
            lossless = ext in {".flac", ".wav", ".ape", ".wv", ".alac"}
            sr = 44100 if ext in {".wav", ".mp3", ".m4a"} else 0
            bps = 16 if lossless else 0
            sr_khz = sr / 1000.0 if sr > 0 else 0
            sr_str = f"{int(sr_khz)} kHz" if sr_khz.is_integer() else f"{sr_khz:.1f} kHz" if sr > 0 else ""
            codec_name = ext.upper().lstrip(".")
            label = f"{sr_str} · {codec_name}" if sr_str else codec_name
            info = {
                "codec": codec_name,
                "samplerate_hz": sr,
                "bits_per_sample": bps,
                "channels": 2,
                "bitrate_kbps": 0,
                "quality_label": label,
                "is_hires": False,
                "lossless": lossless,
            }
        else:
            probe = _ffprobe_audio_stream(path)
            if probe:
                sr = int(probe.get("samplerate_hz") or 0)
                bps = int(probe.get("bits_per_sample") or 0)
                codec = _short_codec_name(str(probe.get("codec") or ext.lstrip(".")))
                lossless = ext in {".flac", ".wav", ".ape", ".wv", ".alac"}
                is_hires = (bps > 16 or sr > 48000) and lossless
                sr_khz = sr / 1000.0 if sr > 0 else 0
                sr_str = f"{int(sr_khz)} kHz" if sr_khz.is_integer() else f"{sr_khz:.1f} kHz" if sr > 0 else ""
                br = int(probe.get("bitrate_kbps") or 0)

                parts = []
                if bps > 0:
                    parts.append(f"{bps}-bit")
                if sr_str:
                    parts.append(sr_str)
                if br > 0:
                    parts.append(f"{br} kbps")
                if codec:
                    parts.append(codec)
                label = " · ".join(parts) if parts else (codec or ext.upper().lstrip("."))

                info = {
                    "codec": codec or ext.upper().lstrip("."),
                    "samplerate_hz": sr,
                    "bits_per_sample": bps,
                    "channels": int(probe.get("channels") or 0),
                    "bitrate_kbps": br,
                    "quality_label": label,
                    "is_hires": is_hires,
                    "lossless": lossless,
                }
            else:
                lossless = ext in {".flac", ".wav", ".ape", ".wv", ".alac"}
                info = {
                    "codec": ext.upper().lstrip("."),
                    "samplerate_hz": 0,
                    "bits_per_sample": 0,
                    "channels": 0,
                    "bitrate_kbps": 0,
                    "quality_label": ext.upper().lstrip("."),
                    "is_hires": False,
                    "lossless": lossless,
                }

    # Opus/AAC no traen bitrate en la cabecera: kbps medio = tamaño·8 / duración (mutagen, cacheado).
    if not info.get("lossless") and not info.get("bitrate_kbps"):
        from src.media_duration import duration_seconds

        dur = duration_seconds(path)
        if dur:
            info["bitrate_kbps"] = int(st.st_size * 8 / dur / 1000)
            info["quality_label"] = _quality_label_from_aq(info)

    _AUDIO_QUALITY_CACHE[cache_key] = info
    global _quality_cache_dirty
    _quality_cache_dirty = True
    return info


def _quality_label_from_aq(aq: Dict[str, Any]) -> str:
    parts: List[str] = []
    bits = int(aq.get("bits_per_sample") or 0)
    if bits > 0:
        parts.append(f"{bits}-bit")
    sr = int(aq.get("samplerate_hz") or 0)
    if sr >= 1000:
        parts.append(f"{sr // 1000} kHz")
    elif sr > 0:
        parts.append(f"{sr} Hz")
    br = int(aq.get("bitrate_kbps") or 0)
    if br > 0:
        parts.append(f"{br} kbps")
    codec = _short_codec_name(str(aq.get("codec") or ""))
    if codec:
        parts.append(codec)
    return " · ".join(parts)


def _build_audio_quality(current: Optional[Path]) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "codec": "",
        "bitrate_kbps": 0,
        "samplerate_hz": 0,
        "channels": 0,
        "bits_per_sample": 0,
        "lossless": False,
        "quality_label": "",
        "output_samplerate_hz": 0,
    }
    if not current or not current.is_file():
        return out
    fast_info = get_track_audio_quality(current)
    if fast_info:
        out.update(fast_info)
    out["lossless"] = _lossless_ext(current)
    if _mpv_responding():
        try:
            ab = _ipc_command("get_property", "audio-bitrate")
            if ab is not None:
                try:
                    out["bitrate_kbps"] = int(float(ab) / 1000)
                except (TypeError, ValueError):
                    pass
            sr = _ipc_command("get_property", "audio-out-params/samplerate")
            if sr is None:
                sr = _ipc_command("get_property", "audio-params/samplerate")
            if sr is not None:
                try:
                    out["output_samplerate_hz"] = int(float(sr))
                except (TypeError, ValueError):
                    pass
            fmt = _ipc_command("get_property", "audio-out-params/format")
            if fmt:
                out["output_format"] = str(fmt)
            ch = _ipc_command("get_property", "audio-params/channels")
            if ch is not None and not out["channels"]:
                try:
                    out["channels"] = int(ch)
                except (TypeError, ValueError):
                    pass
        except OSError:
            pass
    rebuilt_label = _quality_label_from_aq(out)
    if rebuilt_label:
        out["quality_label"] = rebuilt_label
    return out


def get_quality_check() -> Dict[str, Any]:
    if not _mpv_responding():
        raise RuntimeError("mpv not running")
    current = _current_track_path()
    aq = _build_audio_quality(current)
    file_sr = int(aq.get("samplerate_hz") or 0)
    out_sr = int(aq.get("output_samplerate_hz") or 0)
    if file_sr > 0 and out_sr > 0:
        if abs(file_sr - out_sr) <= 1:
            state = "ok"
            message = "Sin remuestreo detectado en la salida mpv."
        else:
            state = "resampling"
            message = f"Remuestreo: archivo {file_sr} Hz, salida {out_sr} Hz (PipeWire puede forzar tasa global)."
    else:
        state = "unknown"
        message = "No se pudo comparar tasas de muestreo."
    return {
        "state": state,
        "file_samplerate_hz": file_sr or None,
        "output_samplerate_hz": out_sr or None,
        "message": message,
        "audio_quality": aq,
    }


def _purge_path_from_state(resolved: Path) -> None:
    global _quality_cache_dirty
    global _queue_tracks, _queue_index, _logical_queue, _logical_queue_index
    global _play_history, _history_index, _active_logical
    try:
        rel = str(resolved.relative_to(MUSIC_ROOT.resolve()))
    except ValueError:
        rel = str(resolved)
    _queue_tracks = [p for p in _queue_tracks if p.resolve() != resolved.resolve()]
    if _queue_index >= len(_queue_tracks):
        _queue_index = max(0, len(_queue_tracks) - 1)
    _logical_queue = [lt for lt in _logical_queue if lt.get("path") != rel]
    if _logical_queue_index >= len(_logical_queue):
        _logical_queue_index = max(0, len(_logical_queue) - 1)
    _play_history = [h for h in _play_history if Path(h).resolve() != resolved.resolve()]
    _history_index = min(_history_index, len(_play_history) - 1)
    if _active_logical and _active_logical.get("path") == rel:
        _active_logical = None
    key = str(resolved.resolve())
    if key in _chapters_disk_cache:
        del _chapters_disk_cache[key]
        try:
            _save_chapters_disk_cache()
        except OSError:
            pass
    keys_to_del = [k for k in _AUDIO_QUALITY_CACHE if k.startswith(key)]
    for k in keys_to_del:
        del _AUDIO_QUALITY_CACHE[k]
    if keys_to_del:
        _quality_cache_dirty = True
        _save_quality_cache()


def delete_file(relative: str, *, user: Optional[str] = None) -> Dict[str, Any]:
    path = _resolve_audio_file(relative)
    resolved = path.resolve()
    rel_path = str(resolved.relative_to(MUSIC_ROOT.resolve()))
    removed_from_playback = False
    current = _current_track_path()
    if current and current.resolve() == resolved:
        removed_from_playback = True
        ensure_mpv()
        try:
            if _shuffle_library:
                _shuffle_next()
            elif _queue_tracks and len(_queue_tracks) > 1:
                _sequential_next()
            else:
                _ipc_command("stop")
        except Exception as exc:
            logger.warning("music delete playback advance failed: %s", exc)
            try:
                _ipc_command("stop")
            except OSError:
                pass
    _purge_path_from_state(resolved)
    path.unlink()
    _invalidate_library_cache()
    logger.info("music delete path=%s resolved=%s user=%s", relative, rel_path, user or "")
    return {
        "ok": True,
        "path": rel_path,
        "removed_from_playback": removed_from_playback,
    }


def rename_track_file(relative: str, new_name: str, *, user: Optional[str] = None) -> Dict[str, Any]:
    path = _resolve_audio_file(relative)
    clean_name = Path(new_name).name.strip()
    if not clean_name or "/" in clean_name or "\\" in clean_name:
        raise ValueError("Invalid filename")
    ext = path.suffix
    if not clean_name.endswith(ext):
        clean_name = f"{clean_name}{ext}"
    new_path = path.parent / clean_name
    if new_path.exists() and new_path.resolve() != path.resolve():
        raise ValueError("Target file already exists")
    resolved = path.resolve()
    current = _current_track_path()
    was_playing = current and current.resolve() == resolved
    time_pos = 0.0
    if was_playing:
        time_pos = _ipc_property_float("time-pos", 0.0)
    path.rename(new_path)
    _purge_path_from_state(resolved)
    _invalidate_library_cache()
    if was_playing and _mpv_responding():
        try:
            _ipc_command("loadfile", str(new_path), "replace")
            if time_pos > 0:
                _ipc_command("seek", time_pos, "absolute")
            _remember_track(new_path)
        except Exception as e:
            logger.warning("Could not reload renamed track in mpv: %s", e)
    rel_new = str(new_path.resolve().relative_to(MUSIC_ROOT.resolve()))
    rel_old = str(resolved.relative_to(MUSIC_ROOT.resolve()))
    save_music_state()
    logger.info("music rename path=%s -> %s user=%s", rel_old, rel_new, user or "")
    return {
        "ok": True,
        "old_path": rel_old,
        "new_path": rel_new,
        "new_name": clean_name,
    }


def gui_actions_enabled() -> bool:
    """False cuando ODYSSEUS_NO_GUI=1 (lo pone la suite de tests).

    Abrir un gestor de archivos es un efecto GUI: bajo tests aparecia una
    ventana encima del escritorio del usuario en cada corrida. Mismo patron
    que music_runtime_enabled() para mpv.
    """
    return os.getenv("ODYSSEUS_NO_GUI", "0") != "1"


def reveal_in_file_manager(relative: str = "") -> Dict[str, Any]:
    if not gui_actions_enabled():
        return {"ok": True, "skipped": "gui-disabled"}
    text = (relative or "").strip()
    if not text:
        path = _resolve_under_root("")
    else:
        try:
            path = _resolve_audio_file(text)
        except Exception:
            path = _resolve_under_root(text)
    if path.is_file():
        folder = path.parent
    elif path.is_dir():
        folder = path
    else:
        raise FileNotFoundError(f"Not found: {path}")
    if shutil.which("xdg-open"):
        proc = subprocess.run(
            ["xdg-open", str(folder)],
            capture_output=True,
            text=True,
            timeout=8.0,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or "xdg-open failed")
    else:
        raise RuntimeError("xdg-open not available")
    return {"ok": True, "folder": str(folder)}


def get_status() -> Dict[str, Any]:
    status: Dict[str, Any] = {
        "library_root": str(MUSIC_ROOT),
        "mpv_socket": str(MPV_SOCKET),
        "mpv_running": _mpv_responding(),
        "player": MPV_PLAYER_NAME,
        "state": "stopped",
        "title": "",
        "artist": "",
        "album": "",
        "position_sec": 0.0,
        "duration_sec": 0.0,
        "shuffle": False,
        "filename": "",
        "path": "",
        "relative_path": "",
        "volume": 100.0,
        "muted": False,
        "seekable": False,
        "chapter": -1,
        "chapter_count": 0,
        "chapter_title": "",
        "logical_track": None,
        "logical_position_sec": 0.0,
        "logical_duration_sec": 0.0,
        "chapters": [],
        "music_prefs": get_music_prefs(),
        "audio_quality": {},
        "eof_reached": False,
    }

    if not status["mpv_running"]:
        return status

    try:
        status["filename"] = str(_ipc_command("get_property", "filename") or "")
        status["title"] = str(_ipc_command("get_property", "media-title") or "")
        pos = _ipc_command("get_property", "time-pos")
        dur = _ipc_command("get_property", "duration")
        status["position_sec"] = float(pos or 0.0)
        status["duration_sec"] = float(dur or 0.0)
        pause = _ipc_command("get_property", "pause")
        status["state"] = "paused" if pause else "playing"
        eof = _ipc_command("get_property", "eof-reached")
        status["eof_reached"] = bool(eof) if eof is not None else False
        vol = _ipc_command("get_property", "volume")
        mute = _ipc_command("get_property", "mute")
        if vol is not None:
            try:
                status["volume"] = max(0.0, min(100.0, float(vol)))
            except (TypeError, ValueError):
                pass
        if mute is not None:
            status["muted"] = bool(mute)
        seekable = _ipc_command("get_property", "seekable")
        status["seekable"] = bool(seekable) if seekable is not None else True
        ch = _ipc_command("get_property", "chapter")
        ch_count = _ipc_command("get_property", "chapters")
        if isinstance(ch, int):
            status["chapter"] = ch
        if isinstance(ch_count, (int, float)):
            status["chapter_count"] = int(ch_count)
        current = _current_track_path()
        if current:
            start, end, ch_idx, ch_title = _playback_window(current)
            pos_abs = status["position_sec"]
            if end > start:
                status["logical_position_sec"] = max(0.0, pos_abs - start)
                status["logical_duration_sec"] = max(0.0, end - start)
            else:
                status["logical_position_sec"] = pos_abs
                status["logical_duration_sec"] = status["duration_sec"]
            if ch_idx >= 0:
                status["chapter"] = ch_idx
                status["chapter_title"] = ch_title
            try:
                rel = str(current.resolve().relative_to(MUSIC_ROOT.resolve()))
                status["path"] = rel
                status["relative_path"] = rel
                if status["chapter_count"] > 0:
                    summary = get_chapters_for_path(rel)
                    status["chapters"] = [
                        {
                            "index": c["index"],
                            "title": c["title"],
                            "start_sec": c["start_sec"],
                        }
                        for c in summary[:64]
                    ]
            except (OSError, ValueError):
                pass
        if _active_logical:
            status["logical_track"] = dict(_active_logical)
            if not status["path"] and _active_logical.get("path"):
                status["path"] = str(_active_logical["path"])
                status["relative_path"] = str(_active_logical["path"])
        elif current:
            ci = status["chapter"] if status["chapter"] >= 0 else None
            status["logical_track"] = _logical_track_dict(current, ci)
    except Exception as exc:
        logger.debug("mpv status via IPC failed: %s", exc)

    if _playerctl_available():
        meta = _playerctl_cmd(
            "metadata",
            "--format",
            "{{artist}}|||{{album}}|||{{title}}|||{{status}}|||{{position}}|||{{mpris:length}}",
        )
        if meta.returncode == 0 and meta.stdout.strip():
            parts = meta.stdout.strip().split("|||")
            if len(parts) >= 6:
                status["artist"] = parts[0] or status["artist"]
                status["album"] = parts[1] or status["album"]
                status["title"] = parts[2] or status["title"]
                if parts[3]:
                    status["state"] = parts[3]
                try:
                    status["position_sec"] = float(parts[4] or 0.0)
                except ValueError:
                    pass
                try:
                    status["duration_sec"] = float(parts[5] or 0.0) / 1_000_000
                except ValueError:
                    pass

    status["shuffle"] = _shuffle_library
    if not _shuffle_library:
        _sync_queue_index_from_mpv()
    status["queue_size"] = len(_logical_queue) if _logical_queue else len(_queue_tracks)
    status["queue_index"] = (
        _logical_queue_index if _logical_queue else _queue_index
    )
    status["at_queue_end"] = (
        not _shuffle_library
        and bool(_queue_tracks)
        and _queue_index >= len(_queue_tracks) - 1
    )
    if (
        not _shuffle_library
        and _music_prefs.get("navigate_mode") != "logical"
        and _queue_tracks
        and 0 <= _queue_index < len(_queue_tracks)
    ):
        queued = _queue_tracks[_queue_index]
        status["filename"] = str(queued)
        if not status["title"]:
            status["title"] = queued.stem
    if not status["title"] and status["filename"]:
        status["title"] = Path(status["filename"]).stem
    if not status["path"] and status["filename"]:
        try:
            cand = Path(status["filename"]).expanduser()
            root = MUSIC_ROOT.resolve()
            if cand.is_file():
                status["path"] = str(cand.resolve().relative_to(root))
                status["relative_path"] = status["path"]
        except Exception:
            pass
    status["audio_quality"] = _build_audio_quality(_current_track_path())
    status["queue_scope"] = _queue_scope
    status["is_favorite"] = is_favorite(status.get("path") or status.get("relative_path") or str(_current_track_path() or ""))
    return status


def browse(relative: str = "") -> Dict[str, Any]:
    if relative == "__favorites__":
        _load_favorites()
        root = MUSIC_ROOT.resolve()
        entries: List[Dict[str, Any]] = []
        for fav_rel in sorted(_favorites):
            p = root / fav_rel
            if p.is_file() and _is_audio(p):
                entries.append(
                    {
                        "name": p.name,
                        "kind": "file",
                        "path": fav_rel,
                        "audio_quality": get_track_audio_quality(p, fast_only=True),
                        "play_count": get_track_play_count(p),
                    }
                )
        if _quality_cache_dirty:
            _save_quality_cache()
        return {
            "path": "__favorites__",
            "parent": ".",
            "entries": entries[:_MAX_BROWSE],
            "truncated": len(entries) > _MAX_BROWSE,
        }

    folder = _resolve_under_root(relative)
    if not folder.is_dir():
        raise NotADirectoryError(f"Not a directory: {folder}")

    entries: List[Dict[str, Any]] = []
    try:
        with os.scandir(folder) as it:
            for entry in it:
                if entry.name.startswith("."):
                    continue
                try:
                    if entry.is_dir(follow_symlinks=False):
                        kind = "dir"
                    elif _is_audio(Path(entry.path)):
                        kind = "file"
                    else:
                        continue
                    entry_dict = {
                        "name": entry.name,
                        "kind": kind,
                        "path": str(Path(entry.path).resolve().relative_to(MUSIC_ROOT.resolve())),
                    }
                    if kind == "file":
                        entry_dict["audio_quality"] = get_track_audio_quality(Path(entry.path), fast_only=True)
                    entries.append(entry_dict)
                except OSError:
                    continue
    except OSError as exc:
        raise PermissionError(str(exc)) from exc

    if _quality_cache_dirty:
        _save_quality_cache()

    entries.sort(key=lambda e: (0 if e["kind"] == "dir" else 1, e["name"].lower()))
    if folder == MUSIC_ROOT.resolve():
        _load_favorites()
        fav_entry = {
            "name": "Favoritos",
            "kind": "dir",
            "path": "__favorites__",
            "is_virtual": True,
            "track_count": len(_favorites),
        }
        entries.insert(0, fav_entry)

    truncated = len(entries) > _MAX_BROWSE
    rel = "." if folder == MUSIC_ROOT.resolve() else str(folder.relative_to(MUSIC_ROOT.resolve()))
    parent = None
    if folder != MUSIC_ROOT.resolve():
        parent_path = folder.parent
        parent = "." if parent_path == MUSIC_ROOT.resolve() else str(parent_path.relative_to(MUSIC_ROOT.resolve()))

    return {
        "path": rel,
        "parent": parent,
        "entries": entries[:_MAX_BROWSE],
        "truncated": truncated,
    }


def _quality_sort_key(path: Path) -> tuple:
    aq = get_track_audio_quality(path, fast_only=True)
    tier = 2 if aq.get("is_hires") else (1 if aq.get("lossless") else 0)
    sr = int(aq.get("samplerate_hz") or 0)
    bps = int(aq.get("bits_per_sample") or 0)
    br = int(aq.get("bitrate_kbps") or 0)
    return (tier, bps, sr, br)


def list_flat(
    relative: str = "",
    *,
    offset: int = 0,
    limit: int = 200,
    sort: str = "folder_asc",
) -> Dict[str, Any]:
    """Lista pistas sin jerarquia de carpetas, con paginado y orden."""
    root = MUSIC_ROOT.resolve()
    target_folder = _resolve_under_root(relative) if relative and relative not in (".", "") else root
    if not target_folder.is_dir():
        raise NotADirectoryError(f"Not a directory: {target_folder}")

    if target_folder == root:
        all_tracks = _library_tracks()
    else:
        all_tracks = _collect_audio_files(target_folder, limit=_MAX_LIBRARY)

    total = len(all_tracks)
    s = (sort or "folder_asc").lower()
    if s in ("name", "name_asc"):
        all_tracks.sort(key=lambda p: p.name.lower())
    elif s == "name_desc":
        all_tracks.sort(key=lambda p: p.name.lower(), reverse=True)
    elif s in ("quality", "quality_desc"):
        all_tracks.sort(key=lambda p: (_quality_sort_key(p), p.name.lower()), reverse=True)
    elif s == "quality_asc":
        all_tracks.sort(key=lambda p: (_quality_sort_key(p), p.name.lower()))
    elif s in ("plays", "plays_desc"):
        all_tracks.sort(key=lambda p: (get_track_play_count(p), p.name.lower()), reverse=True)
    elif s == "plays_asc":
        all_tracks.sort(key=lambda p: (get_track_play_count(p), p.name.lower()))
    elif s == "folder_desc":
        all_tracks.sort(key=lambda p: (str(p.parent.relative_to(root)).lower(), p.name.lower()), reverse=True)
    else:  # "folder", "folder_asc"
        all_tracks.sort(key=lambda p: (str(p.parent.relative_to(root)).lower(), p.name.lower()))

    sliced = all_tracks[offset : offset + limit]
    results: List[Dict[str, Any]] = []
    for track in sliced:
        rel = str(track.resolve().relative_to(root))
        parent_rel = str(track.parent.resolve().relative_to(root))
        if parent_rel == ".":
            parent_rel = ""
        results.append(
            {
                "name": track.name,
                "path": rel,
                "folder": parent_rel,
                "audio_quality": get_track_audio_quality(track, fast_only=True),
                "play_count": get_track_play_count(track),
            }
        )

    if _quality_cache_dirty:
        _save_quality_cache()

    return {
        "path": "" if target_folder == root else str(target_folder.relative_to(root)),
        "offset": offset,
        "limit": limit,
        "total": total,
        "sort": sort,
        "tracks": results,
    }


def search(query: str, *, limit: int = _MAX_SEARCH) -> Dict[str, Any]:
    q = (query or "").strip().lower()
    if not q:
        return {"query": q, "results": []}

    tokens = [t for t in re.split(r"\s+", q) if t]
    results: List[Dict[str, Any]] = []
    root = MUSIC_ROOT.resolve()
    # Usa la caché de biblioteca (300 s) en vez de recorrer el disco en cada búsqueda.
    for path in _library_tracks():
        name = path.name
        if name.startswith("."):
            continue
        rel = str(path.relative_to(root))
        hay = rel.lower()
        if all(tok in hay for tok in tokens):
            results.append(
                {
                    "name": name,
                    "path": rel,
                    "folder": str(Path(rel).parent),
                    "audio_quality": get_track_audio_quality(path, fast_only=True),
                }
            )
            if len(results) >= limit:
                break
    if _quality_cache_dirty:
        _save_quality_cache()
    return {"query": q, "results": results}


def _load_tracks(paths: List[Path], *, shuffle: bool = False, replace: bool = True) -> Dict[str, Any]:
    if not paths:
        raise FileNotFoundError("No audio files to play")

    global _shuffle_library
    _shuffle_library = bool(shuffle)

    if _shuffle_library:
        _set_sequential_queue([])
        lt = _pick_random_logical()
        _rebuild_shuffle_queue(current=lt)
        result = _play_logical_track(lt)
        result["count"] = len(_build_logical_pool())
        result["shuffle"] = True
        return result

    ordered, start_index = (list(paths), 0)
    if len(ordered) == 1:
        ordered, start_index = _tracks_for_target(ordered[0])

    logical_items = [_logical_track_dict(p, None) for p in ordered]
    _set_logical_queue(logical_items, start_index=start_index)
    _set_sequential_queue(ordered, start_index=start_index)
    ensure_mpv()
    if _music_prefs.get("navigate_mode") == "logical":
        return _play_logical_track(_logical_queue[_logical_queue_index])
    _load_queue_from_index(_queue_index)

    return {
        "action": "play",
        "count": len(_queue_tracks),
        "shuffle": False,
        "first": str(_queue_tracks[_queue_index]),
        "status": get_status(),
    }


def play_path(
    raw: str,
    *,
    shuffle: bool = False,
    chapter_index: Optional[int] = None,
    scope: Any = _DEFAULT_SCOPE_SENTINEL,
) -> Dict[str, Any]:
    if raw == "__favorites__":
        _load_favorites()
        root = MUSIC_ROOT.resolve()
        tracks = [root / f for f in sorted(_favorites) if (root / f).is_file() and _is_audio(root / f)]
        if not tracks:
            raise FileNotFoundError("No hay canciones en favoritos")
        set_queue_scope("__favorites__", rebuild_queue=False)
        is_shuffle = shuffle or _shuffle_library
        if is_shuffle:
            _set_shuffle_mode(True)
            _rebuild_shuffle_queue(scope="__favorites__")
            if _logical_queue:
                return _play_logical_track(_logical_queue[0])
            lt = _logical_track_dict(tracks[0], None)
            return _play_logical_track(lt)
        return _load_tracks(tracks, shuffle=shuffle)

    target = _resolve_under_root(raw)
    is_shuffle = shuffle or _shuffle_library
    if scope is not _DEFAULT_SCOPE_SENTINEL:
        set_queue_scope(scope, rebuild_queue=False)

    if target.is_file():
        if not _is_audio(target):
            raise ValueError("Not an audio file")
        if chapter_index is not None:
            lt = _logical_track_dict(target, int(chapter_index))
            if is_shuffle:
                _set_shuffle_mode(True)
                _rebuild_shuffle_queue(current=lt)
                return _play_logical_track(lt)
            tracks, start_index = _tracks_for_target(target)
            logical_items = [_logical_track_dict(p, None) for p in tracks]
            if start_index < len(logical_items):
                logical_items[start_index] = lt
            _set_logical_queue(logical_items, start_index=start_index)
            _set_sequential_queue(tracks, start_index=start_index)
            return _play_logical_track(lt)
        tracks, start_index = _tracks_for_target(target)
        if is_shuffle:
            _set_shuffle_mode(True)
            lt = _logical_track_dict(target, None)
            _rebuild_shuffle_queue(current=lt)
            return _play_logical_track(lt)
        logical_items = [_logical_track_dict(p, None) for p in tracks]
        _set_logical_queue(logical_items, start_index=start_index)
        _set_sequential_queue(tracks, start_index=start_index)
        ensure_mpv()
        if _music_prefs.get("navigate_mode") == "logical":
            return _play_logical_track(_logical_queue[_logical_queue_index])
        _load_queue_from_index(_queue_index)
        return {
            "action": "play",
            "count": len(_queue_tracks),
            "shuffle": False,
            "first": str(_queue_tracks[_queue_index]),
            "status": get_status(),
        }

    if target.is_dir():
        rel_dir = str(target.relative_to(MUSIC_ROOT.resolve()))
        set_queue_scope(rel_dir, rebuild_queue=False)
        tracks, start_index = _tracks_for_target(target)
        if not tracks:
            raise FileNotFoundError(f"No audio files found in directory: {raw}")
        if is_shuffle:
            _set_shuffle_mode(True)
            _rebuild_shuffle_queue()
            if _logical_queue:
                return _play_logical_track(_logical_queue[0])
            lt = _logical_track_dict(tracks[0], None)
            return _play_logical_track(lt)
        return _load_tracks(tracks, shuffle=shuffle)

    raise FileNotFoundError(f"Not found: {target}")


def play_query(
    query: str,
    *,
    shuffle: bool = False,
    chapter_index: Optional[int] = None,
    scope: Any = _DEFAULT_SCOPE_SENTINEL,
) -> Dict[str, Any]:
    q = (query or "").strip()
    if not q:
        raise ValueError("Empty query")

    try:
        return play_path(q, shuffle=shuffle, chapter_index=chapter_index, scope=scope)
    except (FileNotFoundError, PermissionError, ValueError):
        pass

    q_lower = q.lower()
    try:
        for entry in browse("")["entries"]:
            if entry["kind"] == "dir" and entry["name"].lower() == q_lower:
                return play_path(entry["path"], shuffle=shuffle, scope=scope)
    except OSError:
        pass

    hits = search(q, limit=40)["results"]
    if hits:
        folders = {h["folder"] for h in hits if h.get("folder") not in (".", "")}
        if len(folders) == 1:
            folder = next(iter(folders))
            try:
                return play_path(folder, shuffle=shuffle, scope=scope)
            except FileNotFoundError:
                pass
        return play_path(hits[0]["path"], shuffle=shuffle, scope=scope)

    try:
        for entry in browse("")["entries"]:
            if entry["kind"] == "dir" and q_lower in entry["name"].lower():
                return play_path(entry["path"], shuffle=shuffle, scope=scope)
    except OSError:
        pass

    raise FileNotFoundError(f"No music match for '{q}'")


def _shuffle_next() -> Dict[str, Any]:
    if not _logical_queue or not _shuffle_library or len(_logical_queue) <= 1:
        cur = dict(_active_logical) if _active_logical else None
        if not cur and _current_track_path():
            cur = _logical_track_dict(_current_track_path())
        _rebuild_shuffle_queue(current=cur)

    if _logical_queue and len(_logical_queue) > 1:
        idx = _logical_queue_index if _logical_queue_index >= 0 else 0
        nxt = idx + 1
        if nxt < len(_logical_queue):
            result = _play_logical_track(_logical_queue[nxt])
            result["action"] = "next"
            return result

    # Al agotar la permutacion completa de la biblioteca, regenerar nuevo ciclo sin repeticion contigua
    ex = dict(_active_logical) if _active_logical else None
    if not ex and _current_track_path():
        ex = _logical_track_dict(_current_track_path())
    pool = _build_logical_pool()
    if not pool:
        raise FileNotFoundError("No audio files in music library")
    base = _filter_recent(pool)
    shuffled = [p for p in base if not _logical_same(p, ex)] if ex else list(base)
    if not shuffled:
        shuffled = list(pool)
    random.shuffle(shuffled)
    if ex:
        shuffled.insert(0, dict(ex))
    target_idx = 1 if len(shuffled) > 1 else 0
    _set_logical_queue(shuffled, start_index=target_idx)
    result = _play_logical_track(_logical_queue[target_idx])
    result["action"] = "next"
    return result


def _shuffle_previous() -> Dict[str, Any]:
    global _history_index
    if _logical_queue and _shuffle_library and _logical_queue_index > 0:
        prev_idx = _logical_queue_index - 1
        result = _play_logical_track(_logical_queue[prev_idx])
        result["action"] = "previous"
        return result
    if _history_index > 0:
        _history_index -= 1
        path = Path(_play_history[_history_index])
        if path.is_file():
            ensure_mpv()
            _sync_hardware_rate_for_track(path)
            _ipc_command("playlist-clear")
            _ipc_command("loadfile", str(path), "replace")
            _ipc_command("set_property", "pause", False)
            return {"action": "previous", "status": get_status()}
    raise RuntimeError("No previous track in shuffle history")


def _set_shuffle_mode(enabled: bool) -> None:
    global _shuffle_library
    _shuffle_library = enabled
    _music_prefs["shuffle_enabled"] = bool(enabled)
    if enabled:
        _set_sequential_queue([])
        cur: Optional[Dict[str, Any]] = None
        if _active_logical:
            cur = dict(_active_logical)
        elif _current_track_path():
            cur = _logical_track_dict(_current_track_path())
        _rebuild_shuffle_queue(current=cur)
    if _mpv_responding():
        _ipc_command("set_property", "shuffle", False)


def _clamp_volume(level: float) -> float:
    return max(0.0, min(100.0, float(level)))


def _read_volume() -> float:
    if not _mpv_responding():
        return 100.0
    vol = _ipc_command("get_property", "volume")
    try:
        return _clamp_volume(float(vol if vol is not None else 100.0))
    except (TypeError, ValueError):
        return 100.0


def _set_volume_level(level: float) -> None:
    ensure_mpv()
    _ipc_command("set_property", "volume", _clamp_volume(level))
    _ipc_command("set_property", "mute", False)


def _adjust_volume(delta: float) -> None:
    ensure_mpv()
    _ipc_command("add", "volume", float(delta))
    _ipc_command("set_property", "mute", False)


def _set_muted(muted: bool) -> None:
    ensure_mpv()
    _ipc_command("set_property", "mute", bool(muted))


def _toggle_mute() -> None:
    ensure_mpv()
    _ipc_command("cycle", "mute")


def _volume_control(action: str, arg: str = "") -> None:
    act = (action or "").strip().lower()
    if act in {"mute", "silencio"}:
        _set_muted(True)
        return
    if act in {"unmute", "sonido"}:
        _set_muted(False)
        return
    if act in {"toggle_mute", "mute_toggle"}:
        _toggle_mute()
        return
    if act == "volume_up":
        _adjust_volume(5.0)
        return
    if act == "volume_down":
        _adjust_volume(-5.0)
        return
    if act == "volume":
        text = (arg or "").strip().replace(",", ".")
        if not text:
            raise ValueError("volume needs 0-100 or +N / -N")
        if text[0] in "+-":
            _adjust_volume(float(text))
        else:
            _set_volume_level(float(text))
        return
    raise ValueError(f"Unknown volume action '{action}'")


def _ipc_control(action: str) -> None:
    if action == "shuffle_on":
        _set_shuffle_mode(True)
        return
    if action == "shuffle_off":
        _set_shuffle_mode(False)
        return
    if action == "next" and _shuffle_library:
        _shuffle_next()
        return
    if action == "previous" and _shuffle_library:
        _shuffle_previous()
        return
    if action == "next":
        _sequential_next()
        return
    if action == "previous":
        _sequential_previous()
        return

    if action in {"play", "play-pause"}:
        cur = _current_track_path()
        if cur is None:
            if _shuffle_library:
                _shuffle_next()
                return
            if _queue_tracks and len(_queue_tracks) > 0:
                _play_queue_index(max(0, _queue_index))
                return
            try:
                track = _pick_random_track()
                _play_logical_track(_logical_track_dict(track, None))
                return
            except Exception as e:
                logger.warning("Failed to start initial track: %s", e)

    mapping = {
        "play": ("set_property", "pause", False),
        "pause": ("set_property", "pause", True),
        "play-pause": ("cycle", "pause"),
        "stop": ("stop",),
    }
    cmd = mapping.get(action)
    if not cmd:
        raise ValueError(f"Unknown ipc action '{action}'")
    if action == "stop":
        _reset_pipewire_clock_rate()
    elif action in {"play", "play-pause"}:
        cur = _current_track_path()
        if cur:
            _sync_hardware_rate_for_track(cur)
    result = _ipc_command(*cmd)
    if isinstance(result, dict) and result.get("error") and result.get("error") != "success":
        raise RuntimeError(str(result.get("error")))


def control(action: str, *, volume_arg: str = "", value: Optional[float] = None) -> Dict[str, Any]:
    action = (action or "").strip().lower()
    if action in {"shuffle", "shuffle-on"}:
        action = "shuffle_on"
    if action in {"shuffle-off", "unshuffle"}:
        action = "shuffle_off"
    if action == "prev":
        action = "previous"
    if action in {"seek_relative", "seek_rel"}:
        if value is None:
            raise ValueError("seek_relative needs value in seconds")
        ensure_mpv()
        _seek_playback(relative=float(value))
        return {"action": action, "status": get_status()}
    if action == "seek":
        ensure_mpv()
        if value is not None:
            if 0 <= value <= 100:
                _seek_playback(percent=float(value))
            else:
                _seek_playback(seconds=float(value))
        elif volume_arg:
            text = volume_arg.strip().replace(",", ".")
            if text.endswith("%"):
                _seek_playback(percent=float(text[:-1]))
            else:
                _seek_playback(seconds=float(text))
        else:
            raise ValueError("seek needs seconds, percent 0-100, or value")
        return {"action": action, "status": get_status()}
    if action == "chapter" and value is not None:
        global _active_logical
        ensure_mpv()
        _ipc_command("set_property", "chapter", int(value))
        _ipc_command("set_property", "pause", False)
        cur = _current_track_path()
        if cur:
            _active_logical = _logical_track_dict(cur, int(value))
        return {"action": action, "status": get_status()}

    volume_actions = {
        "volume",
        "volume_up",
        "volume_down",
        "mute",
        "unmute",
        "toggle_mute",
        "silencio",
        "sonido",
    }
    if action in volume_actions:
        ensure_mpv()
        try:
            if action in {"silencio"}:
                _volume_control("mute")
            elif action in {"sonido"}:
                _volume_control("unmute")
            elif action == "volume":
                if value is not None:
                    _set_volume_level(value)
                elif volume_arg:
                    _volume_control("volume", volume_arg)
                else:
                    raise ValueError("volume needs 0-100 or +N / -N")
            else:
                _volume_control(action)
        except Exception as ipc_err:
            if _playerctl_available() and action == "volume" and value is not None:
                level = _clamp_volume(value) / 100.0
                proc = _playerctl_cmd("volume", str(level))
                if proc.returncode != 0:
                    raise RuntimeError(proc.stderr.strip() or str(ipc_err)) from ipc_err
            elif _playerctl_available() and action in {"mute", "unmute", "silencio", "sonido"}:
                mute = action in {"mute", "silencio"}
                level = "0" if mute else str(max(0.05, _read_volume() / 100.0))
                proc = _playerctl_cmd("volume", level)
                if proc.returncode != 0:
                    raise RuntimeError(proc.stderr.strip() or str(ipc_err)) from ipc_err
            else:
                raise RuntimeError(str(ipc_err)) from ipc_err
        return {"action": action, "status": get_status()}

    ensure_mpv()

    try:
        _ipc_control(action)
    except Exception as ipc_err:
        if action in {"play", "pause", "play-pause", "stop"} and _playerctl_available():
            proc = _playerctl_cmd(action if action != "play-pause" else "play-pause")
            if proc.returncode != 0:
                raise RuntimeError(proc.stderr.strip() or str(ipc_err)) from ipc_err
        elif action in {"next", "previous"} and _playerctl_available():
            if action == "next" and _shuffle_library:
                try:
                    return _shuffle_next()
                except Exception:
                    pass
            proc = _playerctl_cmd(action)
            if proc.returncode != 0:
                raise RuntimeError(proc.stderr.strip() or str(ipc_err)) from ipc_err
        else:
            raise RuntimeError(str(ipc_err)) from ipc_err

    return {"action": action, "status": get_status()}


async def do_music_control(content: str) -> Dict[str, Any]:
    """Agent-facing music control."""
    lines = [ln.strip() for ln in (content or "").strip().splitlines() if ln.strip()]
    if not lines:
        return {"error": "No action specified"}

    head = lines[0]
    parts = head.split(None, 1)
    action = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    shuffle = False
    chapter_index: Optional[int] = None
    for ln in lines[1:]:
        low = ln.lower()
        if low in {"shuffle", "shuffle on", "random", "aleatorio"}:
            shuffle = True
        elif low in {"shuffle off", "no shuffle", "sequential"}:
            shuffle = False
        elif low.startswith("chapter") or low.startswith("capítulo") or low.startswith("capitulo"):
            parts = ln.split(None, 1)
            if len(parts) > 1:
                chapter_index = int(parts[1].strip())

    try:
        if action in {"status", "now", "current"}:
            return {"results": "Current playback status", "status": get_status()}

        if action in {"browse", "list"}:
            data = browse(arg)
            return {"results": f"Library browse at '{data.get('path', '.')}'", **data}

        if action in {"search", "find"}:
            if not arg:
                return {"error": "search needs a query"}
            data = search(arg)
            return {"results": f"Found {len(data['results'])} track(s)", **data}

        if action in {"play", "queue", "folder"}:
            if not arg:
                return {"error": f"{action} needs a path or query"}
            result = play_query(arg, shuffle=shuffle, chapter_index=chapter_index)
            return {
                "results": f"Playing {result.get('count', 1)} track(s)",
                "ui_event": "music_refresh",
                **result,
            }

        if action in {"pause", "play-pause", "next", "previous", "prev", "stop", "shuffle_on", "shuffle_off"}:
            mapped = "previous" if action == "prev" else action
            result = control(mapped)
            return {"results": f"Music {mapped}", "ui_event": "music_refresh", **result}

        if action in {"seek", "seek_relative", "seek_rel", "chapter"}:
            if action == "seek" and not arg:
                return {"error": "seek needs seconds or percent"}
            if action == "chapter" and not arg:
                return {"error": "chapter needs index"}
            if action == "seek":
                text = arg.strip().replace(",", ".")
                if text.endswith("%"):
                    result = control("seek", volume_arg=text)
                else:
                    try:
                        val = float(text)
                        result = control(
                            "seek",
                            value=val if ("%" in text or val <= 100) and "." not in text else val,
                        )
                    except ValueError:
                        result = control("seek", volume_arg=text)
            elif action in {"seek_relative", "seek_rel"}:
                result = control("seek_relative", value=float(arg or "0"))
            else:
                result = control("chapter", value=float(arg))
            return {"results": f"Music {action}", "ui_event": "music_refresh", **result}

        if action in {
            "volume",
            "volume_up",
            "volume_down",
            "mute",
            "unmute",
            "toggle_mute",
            "silencio",
            "sonido",
        }:
            if action == "volume" and not arg:
                return {"error": "volume needs 0-100 or +N / -N"}
            result = control(action, volume_arg=arg)
            return {"results": f"Music {action}", "ui_event": "music_refresh", **result}

        return {
            "error": (
                "Unknown action. Use: status, browse [path], search <query>, "
                "play <path|folder|query> [chapter N], pause, next, previous, stop, shuffle_on, shuffle_off, "
                "seek <sec|N%>, seek_relative <sec>, chapter <index>, "
                "volume <0-100|+N|-N>, volume_up, volume_down, mute, unmute"
            )
        }
    except Exception as exc:
        logger.warning("music_control failed: %s", exc)
        return {"error": str(exc)}


EQ_FREQUENCIES = [31.25, 62.5, 125.0, 250.0, 500.0, 1000.0, 2000.0, 4000.0, 8000.0, 16000.0]

EQ_PRESETS: Dict[str, List[float]] = {
    "flat": [0.0] * 10,
    "scarlett_monitors": [-1.0, -0.5, 0.0, 0.5, 1.0, 1.0, 0.5, 0.0, -0.5, -1.0],
    "bass_boost": [6.0, 5.0, 4.0, 2.0, 0.0, 0.0, 0.0, 0.0, 1.0, 1.5],
    "vocal": [-2.0, -1.5, -0.5, 1.0, 3.0, 3.5, 2.0, 1.0, 0.0, -1.0],
    "rock": [4.0, 3.0, 1.5, 0.0, -1.0, -0.5, 1.5, 3.0, 3.5, 4.0],
    "night": [-4.0, -3.0, -2.0, -1.0, 0.0, 1.0, 2.0, 1.0, 0.0, -2.0],
    # Sennheiser HD 660S2, oratory1990/Harman (FluidEQ), con preamp -6.6 dB
    # aplicado como bandas negativas para no clipar.
    "hd660s2": [-1.3, -2.2, -6.2, -7.9, -6.6, -7.1, -4.7, -4.9, -0.1, -5.4],
}


def apply_equalizer_hardware() -> None:
    """Aplica las bandas de ecualizador actuales al proceso mpv vía IPC."""
    bands = _music_prefs.get("equalizer_bands") or [0.0] * 10
    clamped = [max(-12.0, min(12.0, float(b))) for b in bands[:10]]
    if len(clamped) < 10:
        clamped.extend([0.0] * (10 - len(clamped)))

    # Si todas las bandas son 0, quitar filtro para ruta bit-perfect directa
    if all(abs(b) < 0.05 for b in clamped):
        if _mpv_responding():
            try:
                _ipc_command("set_property", "af", [])
            except Exception:
                pass
        return

    af_filters = []
    for freq, gain in zip(EQ_FREQUENCIES, clamped):
        af_filters.append({
            "name": "equalizer",
            "params": {"f": str(freq), "width_type": "o", "w": "1", "g": f"{gain:.1f}"}
        })
    if _mpv_responding():
        try:
            _ipc_command("set_property", "af", af_filters)
        except Exception as exc:
            logger.warning("Error al aplicar filtro de ecualizador a mpv: %s", exc)


def get_equalizer() -> Dict[str, Any]:
    bands = list(_music_prefs.get("equalizer_bands") or [0.0] * 10)
    preset = str(_music_prefs.get("equalizer_preset") or "flat")
    return {
        "frequencies": EQ_FREQUENCIES,
        "bands": bands,
        "preset": preset,
        "presets": list(EQ_PRESETS.keys()),
    }


def set_equalizer(
    bands: Optional[List[float]] = None,
    preset: Optional[str] = None,
    user: Optional[str] = None,
) -> Dict[str, Any]:
    if preset and preset in EQ_PRESETS:
        target_bands = list(EQ_PRESETS[preset])
        target_preset = preset
    elif bands is not None:
        if len(bands) != 10:
            raise ValueError("El ecualizador requiere exactamente 10 bandas de frecuencia")
        target_bands = [max(-12.0, min(12.0, float(b))) for b in bands]
        target_preset = preset or "custom"
    else:
        target_bands = list(_music_prefs.get("equalizer_bands") or [0.0] * 10)
        target_preset = str(_music_prefs.get("equalizer_preset") or "flat")

    save_music_prefs_for_user(user, {
        "equalizer_bands": target_bands,
        "equalizer_preset": target_preset,
    })
    apply_equalizer_hardware()
    return get_equalizer()


def get_lyrics(artist: str = "", title: str = "", duration_sec: float = 0.0) -> Dict[str, Any]:
    """Consulta la API de LRCLIB para obtener letras sincronizadas o normales."""
    artist = (artist or "").strip()
    title = (title or "").strip()
    if not title:
        st = get_status()
        title = st.get("title") or st.get("filename") or ""
        artist = st.get("artist") or ""
        duration_sec = float(st.get("duration_sec") or st.get("logical_duration_sec") or 0.0)

    # Limpieza de sufijos típicos de YouTube para maximizar coincidencias
    clean_title = re.sub(
        r"\[(?:Official|Lyric|Video|Audio|HD|4K|HQ|Music Video|Visualizer|Full Album|Remastered|Topic)[^\]]*\]",
        "",
        title,
        flags=re.IGNORECASE,
    )
    clean_title = re.sub(
        r"\((?:Official|Lyric|Video|Audio|HD|4K|HQ|Music Video|Visualizer|Full Album|Remastered|Topic)[^\)]*\)",
        "",
        clean_title,
        flags=re.IGNORECASE,
    )
    clean_title = re.sub(r"^[0-9]+[\s\.\-_]+", "", clean_title).strip(" -_·|")

    query = f"{artist} {clean_title}".strip()
    if not query:
        return {"synced": False, "lines": [], "plain": "", "source": "none"}

    try:
        import httpx
        headers = {"User-Agent": "Odysseus-Music/1.0 (https://github.com/lognhugr)"}
        with httpx.Client(timeout=6.0, headers=headers) as client:
            resp = client.get("https://lrclib.net/api/search", params={"q": query})
            if resp.status_code != 200:
                return {"synced": False, "lines": [], "plain": "", "source": "none", "error": f"HTTP {resp.status_code}"}
            hits = resp.json()
            if duration_sec and isinstance(hits, list):
                hits.sort(key=lambda h: abs(float(h.get("duration") or 0.0) - float(duration_sec))
                          if isinstance(h, dict) else float("inf"))
            if not hits:
                return {"synced": False, "lines": [], "plain": "", "source": "none"}

            best = next((h for h in hits if h.get("syncedLyrics")), hits[0])
            synced_raw = best.get("syncedLyrics") or ""
            plain_raw = best.get("plainLyrics") or ""

            lines = []
            if synced_raw:
                for line in synced_raw.splitlines():
                    m = re.match(r"^\[(\d+):(\d+(?:\.\d+)?)\](.*)$", line.strip())
                    if m:
                        mins, secs, txt = m.groups()
                        t = int(mins) * 60 + float(secs)
                        lines.append({"time": round(t, 2), "text": txt.strip()})

            return {
                "synced": len(lines) > 0,
                "lines": lines,
                "plain": plain_raw,
                "track_name": best.get("trackName"),
                "artist_name": best.get("artistName"),
                "source": "lrclib",
            }
    except Exception as exc:
        logger.warning("Error al obtener letras de lrclib: %s", exc)
        return {"synced": False, "lines": [], "plain": "", "source": "error", "error": str(exc)}

