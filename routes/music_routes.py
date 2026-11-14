"""Music library API — local playback via mpv."""

from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import time
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Query, Request
from starlette.responses import FileResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel, Field

from pathlib import Path

from services.music import music_service as ms
from services.music import slskd_source
from src import music_channels, music_downloads, music_history, music_ytdlp

from src import music_access, music_ticket
from src.auth_helpers import get_current_user

logger = logging.getLogger(__name__)


class MusicPlayRequest(BaseModel):
    path: str = Field(default="", description="Relative path, folder, or search query")
    query: str = Field(default="", description="Alias for path when searching")
    shuffle: bool = False
    chapter_index: int | None = Field(default=None, description="Embedded chapter index")
    scope: str | None = Field(default=None, description="Scoped folder for playback/shuffle")


class MusicControlRequest(BaseModel):
    action: str
    value: float | None = Field(
        default=None,
        description="Volume 0-100, seek percent 0-100, seek seconds, chapter index, or relative seek delta",
    )


class MusicPrefsUpdate(BaseModel):
    updates: Dict[str, Any] = Field(default_factory=dict)


class MusicQueueAction(BaseModel):
    index: int = Field(default=0, ge=0)


class MusicFavoriteRequest(BaseModel):
    path: str = Field(default="", description="Relative path or filename of track to toggle")


class MusicDownloadRequest(BaseModel):
    url: str = Field(default="", description="YouTube or YouTube Music URL")


class MusicSlskdDownloadRequest(BaseModel):
    username: str = Field(min_length=1)
    remote_filename: str = Field(min_length=1)
    size_bytes: int = Field(ge=0)


class MusicYtdlpSearchRequest(BaseModel):
    query: str = ""


class MusicYtdlpDownloadRequest(BaseModel):
    id: str = ""


class MusicPlayReport(BaseModel):
    path: str = Field(min_length=1, max_length=1024)
    artist: str | None = Field(default=None, max_length=300)
    title: str | None = Field(default=None, max_length=300)
    album: str | None = Field(default=None, max_length=300)
    secs_played: float = Field(default=0.0, ge=0)
    completed: bool = False


class MusicScopeRequest(BaseModel):
    scope: str | None = None


class MusicRevealRequest(BaseModel):
    path: str = Field(default="")


class MusicRenameRequest(BaseModel):
    old_path: str = Field(..., min_length=1)
    new_name: str = Field(..., min_length=1)


class MusicEqualizerRequest(BaseModel):
    bands: list[float] | None = Field(default=None)
    preset: str | None = Field(default=None)



async def _transcode_opus(path: str, br: int):
    """Trocea ffmpeg a WebM/Opus. Si el cliente corta, el proceso se mata (sin huérfanos)."""
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", path,
        "-vn", "-c:a", "libopus", "-b:a", f"{br}k", "-f", "webm", "pipe:1",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        while True:
            chunk = await proc.stdout.read(64 * 1024)
            if not chunk:
                break
            yield chunk
        await proc.wait()
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()


def _local_to_row(row: Dict[str, Any]) -> Dict[str, Any]:
    path = str(row.get("path") or "")
    folder = str(row.get("folder") or "")
    name = str(row.get("name") or (path.rsplit("/", 1)[-1] if path else ""))
    parts = [x for x in folder.replace("\\", "/").split("/") if x]
    stem = Path(name).stem
    # "Artist - Title" en el nombre gana sobre la carpeta (álbum)
    artist, title = "", stem
    if " - " in stem:
        left, right = stem.split(" - ", 1)
        if left.strip() and right.strip():
            artist, title = left.strip(), right.strip()
    if not artist:
        artist = parts[-1] if parts else ""
    return {
        "title": title,
        "artist": artist,
        "source": "local",
        "quality": row.get("audio_quality") or Path(name).suffix.lstrip(".").upper(),
        "duration_sec": row.get("duration_sec"),
        "playable_now": True,
        "path": path or name,
        "size_bytes": row.get("size"),
    }


async def _search_multi(query: str, local_result: Dict[str, Any], started: float, limit: int = 20) -> Dict[str, Any]:
    local_rows = [_local_to_row(r) for r in (local_result.get("results") or [])]
    remote_rows, meta = await asyncio.to_thread(slskd_source.search, query, limit)
    meta["retried"] = False
    # Medido: el adaptador corta a los 3 s con peers pero sin respuestas aún (InProgress, 0 filas).
    # Un único reintento si la búsqueda termina sin filas.
    if meta.get("ok") and not remote_rows:
        remote_rows, meta = await asyncio.to_thread(slskd_source.search, query, limit)
        meta["retried"] = True
    for row in remote_rows:
        # slskd da FLAC sin bitrate: se calcula el kbps medio para que el badge sea real.
        q, dur, size = str(row.get("quality") or ""), float(row.get("duration_sec") or 0), int(row.get("size_bytes") or 0)
        if q.startswith("FLAC") and dur > 0 and size > 0 and "kbps" not in q:
            row["quality"] = f"{q} · {int(size * 8 / dur / 1000)} kbps"
    merged, dupes = slskd_source.merge(local_rows, remote_rows, limit=limit)
    elapsed = int((time.perf_counter() - started) * 1000)
    logger.info(
        "music multi-search q_len=%d local=%d remote=%d dupes=%d elapsed_ms=%d",
        len(query), len(local_rows), len(remote_rows), dupes, elapsed,
    )
    return {
        "query": query,
        "mode": "bm25",
        "results": merged,
        "counts": {
            "local": len(local_rows),
            "remote": len(remote_rows),
            "duplicates_removed": dupes,
            "total": len(merged),
        },
        "sources": {"local": {"ok": True}, "slskd": meta},
        "elapsed_ms": elapsed,
    }


def setup_music_routes() -> APIRouter:
    router = APIRouter(prefix="/api/music", tags=["music"])

    try:
        ms.init_music_subsystem()
    except Exception as exc:
        logger.warning("music subsystem init failed: %s", exc)

    def _auth(request: Request) -> str | None:
        user = get_current_user(request)
        ms.load_music_prefs_for_user(user)
        return user

    @router.get("/status")
    async def music_status(request: Request):
        _auth(request)
        try:
            return ms.get_status()
        except Exception as exc:
            logger.error("music status failed: %s", exc)
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @router.post("/remote-ticket")
    async def music_remote_ticket(request: Request):
        """Ticket firmado (10 min) para EventSource y <audio>: solo music:stream y music:events."""
        user = _auth(request)
        if not user:
            raise HTTPException(status_code=401, detail="Not authenticated")
        guest = bool(getattr(request.state, "music_guest", False))
        if not music_access.can_read_library(user, guest):
            raise HTTPException(status_code=403, detail="Music library not shared with this user")
        return music_ticket.issue_ticket(user, guest_music=guest)

    @router.get("/events")
    async def music_events(request: Request):
        """SSE: estado completo al conectar y después solo cambios (pista, estado, posición tras salto)."""
        _auth(request)
        keys = ("state", "filename", "relative_path", "title", "artist", "volume", "muted")

        async def _gen():
            dl_q = music_downloads.subscribe()
            try:
                last_snap: Dict[str, Any] | None = None
                last_pos = 0.0
                last_t = time.monotonic()
                last_ping = last_t
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        st = await asyncio.to_thread(ms.get_status)
                    except Exception as exc:
                        logger.debug("music events status failed: %s", exc)
                        st = None
                    now = time.monotonic()
                    if st is not None:
                        snap = {k: st.get(k) for k in keys}
                        pos = float(st.get("position_sec") or 0.0)
                        expected = last_pos + (now - last_t) if snap["state"] == "playing" else last_pos
                        seeked = last_snap is not None and abs(pos - expected) > 2.0
                        if last_snap is None or snap != last_snap or seeked:
                            payload = {**snap, "position_sec": pos, "duration_sec": float(st.get("duration_sec") or 0.0)}
                            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                            last_snap = snap
                        last_pos, last_t = pos, now
                    if now - last_ping >= 15:
                        yield ": ping\n\n"
                        last_ping = now
                    while not dl_q.empty():
                        yield f"data: {json.dumps(dl_q.get_nowait(), ensure_ascii=False)}\n\n"
                    await asyncio.sleep(1)
            finally:
                music_downloads.unsubscribe(dl_q)

        return StreamingResponse(
            _gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @router.get("/stream")
    async def music_stream(
        request: Request,
        path: str = Query(default=""),
        fmt: str = Query(default="flac"),
        br: str = Query(default="128"),
    ):
        """Audio de una pista.

        Sin fmt (o fmt=flac): el fichero original con Range, byte a byte.
        fmt=opus&br=auto|64|96|128|192|320: transcodificado al vuelo a WebM/Opus con ffmpeg.
        br=auto se resuelve a 128 en el servidor (el cliente decide la escalera).
        La rama transcodificada responde 200 SIN Accept-Ranges: no admite saltos por Range.
        """
        _auth(request)
        # Invitado: siempre Opus 128. Nunca el máster FLAC por internet: varios
        # invitados a la vez saturan la subida y cortan el buffer de todos.
        if getattr(request.state, "music_guest", False):
            fmt, br = "opus", "128"
        if fmt not in ("flac", "opus"):
            raise HTTPException(status_code=400, detail="fmt debe ser flac u opus")
        bitrate = 0
        if fmt == "opus":
            if br == "auto":
                bitrate = 128
            else:
                try:
                    bitrate = int(br)
                except ValueError:
                    raise HTTPException(status_code=400, detail="br debe ser auto, 64, 96, 128, 192 o 320")
                if bitrate not in (64, 96, 128, 192, 320):
                    raise HTTPException(status_code=400, detail="br debe ser auto, 64, 96, 128, 192 o 320")
        target_path = None
        if path and path.strip():
            try:
                target_path = ms._resolve_audio_file(path.strip())
            except Exception as exc:
                logger.debug("Failed resolving requested stream path %r: %s", path, exc)
        if not target_path or not target_path.is_file():
            target_path = ms._current_track_path()
        if not target_path or not target_path.is_file():
            raise HTTPException(status_code=404, detail="No active audio track to stream")

        ext = target_path.suffix.lower()
        mime_map = {
            ".flac": "audio/flac",
            ".opus": "audio/ogg",
            ".ogg": "audio/ogg",
            ".mp3": "audio/mpeg",
            ".m4a": "audio/mp4",
            ".wav": "audio/wav",
            ".aac": "audio/aac",
        }
        mime = mime_map.get(ext) or mimetypes.guess_type(str(target_path))[0] or "audio/mpeg"

        if fmt == "opus":
            return StreamingResponse(
                _transcode_opus(str(target_path), bitrate),
                media_type="audio/webm",
                headers={"Cache-Control": "no-store"},
            )

        return FileResponse(
            str(target_path),
            media_type=mime,
            headers={"Accept-Ranges": "bytes"},
        )

    @router.get("/browse")
    async def music_browse(request: Request, path: str = Query(default="")):
        _auth(request)
        try:
            return ms.browse(path)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except NotADirectoryError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/flat")
    async def music_flat(
        request: Request,
        path: str = Query(default=""),
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=200, ge=1, le=1000),
        sort: str = Query(default="folder"),
    ):
        _auth(request)
        try:
            return ms.list_flat(path, offset=offset, limit=limit, sort=sort)
        except NotADirectoryError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    @router.get("/search")
    async def music_search(request: Request, q: str = Query(default=""), remote: bool = Query(default=False)):
        _auth(request)
        started = time.perf_counter()
        result = ms.search(q)
        if not remote:
            logger.info("music search q_len=%d elapsed_ms=%.0f", len(q), (time.perf_counter() - started) * 1000)
            return result
        return await _search_multi(q, result, started)

    @router.get("/search_all")
    async def music_search_all(request: Request, q: str = Query(default=""), limit: int = Query(default=20, ge=1, le=60)):
        """Búsqueda multifuente: biblioteca local + Soulseek, fusionadas y deduplicadas."""
        _auth(request)
        started = time.perf_counter()
        local = await asyncio.to_thread(ms.search, q)
        return await _search_multi(q, local, started, limit=limit)

    @router.get("/chapters")
    async def music_chapters(request: Request, path: str = Query(default="")):
        _auth(request)
        try:
            chapters = ms.get_chapters_for_path(path)
            return {"path": path, "chapters": chapters}
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get("/queue")
    async def music_queue(request: Request):
        _auth(request)
        return ms.get_queue_state()

    @router.post("/queue/clear")
    async def music_queue_clear(request: Request):
        _auth(request)
        return ms.clear_queue()

    @router.post("/queue/jump")
    async def music_queue_jump(request: Request, body: MusicQueueAction):
        _auth(request)
        try:
            return ms.queue_jump(body.index)
        except (RuntimeError, IndexError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/queue/remove")
    async def music_queue_remove(request: Request, body: MusicQueueAction):
        _auth(request)
        try:
            return ms.queue_remove(body.index)
        except (RuntimeError, IndexError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/options")
    async def music_options_get(request: Request):
        _auth(request)
        return {"prefs": ms.get_music_prefs(), "library_root": str(ms.MUSIC_ROOT)}

    @router.post("/options")
    async def music_options_set(request: Request, body: MusicPrefsUpdate):
        user = _auth(request)
        try:
            prefs = ms.save_music_prefs_for_user(user, body.updates or {})
            return {"prefs": prefs}
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/reindex-chapters")
    async def music_reindex(request: Request, force: bool = Query(default=False)):
        _auth(request)
        try:
            started = time.perf_counter()
            result = await asyncio.to_thread(ms.reindex_chapters, force=force)
            logger.info("music reindex force=%s elapsed_ms=%.0f", force, (time.perf_counter() - started) * 1000)
            return result
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @router.post("/scope")
    async def music_scope_set(request: Request, body: MusicScopeRequest):
        _auth(request)
        ms.set_queue_scope(body.scope)
        return {"scope": ms.get_queue_scope(), "status": ms.get_status()}

    @router.post("/play")
    async def music_play(request: Request, body: MusicPlayRequest):
        _auth(request)
        target = (body.path or body.query or "").strip()
        if not target:
            raise HTTPException(status_code=400, detail="path or query required")
        extra_kwargs = {}
        if body.scope is not None:
            extra_kwargs["scope"] = body.scope
        try:
            return ms.play_query(
                target,
                shuffle=body.shuffle,
                chapter_index=body.chapter_index,
                **extra_kwargs,
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (PermissionError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @router.post("/control")
    async def music_control(request: Request, body: MusicControlRequest):
        user = _auth(request)
        try:
            result = ms.control(body.action, value=body.value)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        act = str(result.get("action") or "").lower()
        if act in {"shuffle_on", "shuffle_off"}:
            ms.save_music_prefs_for_user(user, {"shuffle_enabled": act == "shuffle_on"})
        return result

    @router.delete("/file")
    async def music_delete_file(request: Request, path: str = Query(default="")):
        user = _auth(request)
        if not path.strip():
            raise HTTPException(status_code=400, detail="path required")
        try:
            return ms.delete_file(path.strip(), user=user)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except IsADirectoryError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except OSError as exc:
            logger.error("music delete failed: %s", exc)
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @router.get("/quality-check")
    async def music_quality_check(request: Request):
        _auth(request)
        try:
            return ms.get_quality_check()
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @router.get("/lyrics")
    async def music_lyrics(
        request: Request,
        artist: str = Query(default=""),
        title: str = Query(default=""),
        duration: float = Query(default=0.0),
    ):
        _auth(request)
        started = time.perf_counter()
        # Llamada de red síncrona: fuera del bucle de eventos o congela controles.
        result = await asyncio.to_thread(ms.get_lyrics, artist=artist, title=title, duration_sec=duration)
        logger.info("music lyrics elapsed_ms=%.0f", (time.perf_counter() - started) * 1000)
        return result

    @router.get("/equalizer")
    async def music_equalizer_get(request: Request):
        _auth(request)
        return ms.get_equalizer()

    @router.post("/equalizer")
    async def music_equalizer_set(request: Request, body: MusicEqualizerRequest):
        user = _auth(request)
        try:
            return ms.set_equalizer(bands=body.bands, preset=body.preset, user=user)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/rename")
    async def music_rename(request: Request, body: MusicRenameRequest):
        user = _auth(request)
        try:
            return ms.rename_track_file(body.old_path, body.new_name, user=user)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (IsADirectoryError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            logger.error("music rename failed: %s", exc)
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @router.post("/reveal")
    async def music_reveal(request: Request, body: MusicRevealRequest):
        _auth(request)
        try:
            return ms.reveal_in_file_manager(body.path or "")
        except (PermissionError, FileNotFoundError) as exc:
            code = 403 if isinstance(exc, PermissionError) else 404
            raise HTTPException(status_code=code, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @router.get("/favorites")
    async def music_favorites_list(request: Request):
        _auth(request)
        return {"favorites": ms.get_favorites()}

    @router.post("/favorite/toggle")
    async def music_favorite_toggle(request: Request, body: MusicFavoriteRequest):
        _auth(request)
        if not body.path:
            raise HTTPException(status_code=400, detail="path required")
        return ms.toggle_favorite(body.path)

    @router.post("/download")
    async def music_download_start(request: Request, body: MusicDownloadRequest):
        _auth(request)
        if not body.url:
            raise HTTPException(status_code=400, detail="url required")
        try:
            result = ms.start_download(body.url)
            logger.info("music download started url_len=%d", len(body.url))
            return result
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            logger.error("music download failed: %s", exc)
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @router.get("/download/tasks")
    async def music_download_tasks(request: Request):
        _auth(request)
        return {"tasks": ms.get_download_tasks()}

    @router.post("/download/slskd")
    async def music_download_slskd(request: Request, body: MusicSlskdDownloadRequest):
        _auth(request)
        if not slskd_source.available():
            raise HTTPException(status_code=503, detail="ODYSSEUS_SLSKD_KEY ausente")
        try:
            slskd_source._api(
                "POST",
                f"/api/v0/transfers/downloads/{body.username}",
                [{"filename": body.remote_filename, "size": body.size_bytes}],
            )
        except Exception as exc:
            logger.error("slskd download enqueue failed: %s", exc)
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        music_downloads.start(body.username, body.remote_filename)
        return {"status": "downloading"}

    @router.post("/search-ytdlp")
    async def music_search_ytdlp(request: Request, body: MusicYtdlpSearchRequest):
        """Cualquier invitado con música puede buscar; descargar es solo del owner."""
        _auth(request)
        try:
            rows = await asyncio.to_thread(music_ytdlp.search, body.query)
        except TimeoutError as exc:
            return {"results": [], "error": str(exc)}
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {"results": rows}

    @router.post("/download-ytdlp")
    async def music_download_ytdlp(request: Request, body: MusicYtdlpDownloadRequest):
        user = _auth(request)
        if not user or user != music_access.get_policy()["owner"]:
            raise HTTPException(status_code=403, detail="Solo el owner agrega música a la biblioteca")
        try:
            music_ytdlp.start_download(body.id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"status": "downloading", "id": body.id}

    @router.post("/plays")
    async def music_play_report(request: Request, body: MusicPlayReport):
        _auth(request)
        try:
            return music_history.record(body.model_dump())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except OSError as exc:
            logger.error("music plays write failed: %s", exc)
            raise HTTPException(status_code=500, detail=f"No se pudo guardar el historial: {exc}") from exc

    @router.get("/related")
    async def music_related(request: Request, path: str = Query(default=""), limit: int = Query(default=20, ge=1, le=100)):
        _auth(request)
        if not path:
            raise HTTPException(status_code=400, detail="path requerido")
        return {"seed": path, "items": music_history.related(path, limit=limit)}

    @router.get("/radio")
    async def music_radio(
        request: Request,
        path: str = Query(default=""),
        limit: int = Query(default=30, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ):
        _auth(request)
        if not path:
            raise HTTPException(status_code=400, detail="path requerido")
        return {"seed": path, "offset": offset, "items": music_history.radio(path, offset=offset, limit=limit)}

    @router.get("/channels")
    async def music_channels_list(request: Request):
        _auth(request)
        return {"channels": await asyncio.to_thread(music_channels.channels_info)}

    @router.get("/channel/{name}")
    async def music_channel_now(request: Request, name: str):
        _auth(request)
        try:
            return await asyncio.to_thread(music_channels.now_playing, name)
        except KeyError:
            raise HTTPException(status_code=404, detail="canal desconocido")
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc))

    @router.get("/channel/{name}/stream")
    async def music_channel_stream(request: Request, name: str):
        """302 a /api/music/stream de la pista actual. El cliente hace seek a offset_sec."""
        _auth(request)
        try:
            info = await asyncio.to_thread(music_channels.now_playing, name)
        except KeyError:
            raise HTTPException(status_code=404, detail="canal desconocido")
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        from urllib.parse import quote

        target = "/api/music/stream?path=" + quote(info["track"])
        ticket = request.query_params.get("ticket")
        if ticket:
            target += "&ticket=" + quote(ticket)
        return RedirectResponse(url=target, status_code=302)

    @router.get("/playlists")
    async def music_playlists_list(request: Request):
        _auth(request)
        from src import music_playlists
        return {"playlists": await asyncio.to_thread(music_playlists.list_playlists)}

    @router.post("/playlists")
    async def music_playlists_create(request: Request, body: dict):
        _auth(request)
        from src import music_playlists
        row = await asyncio.to_thread(music_playlists.create, body.get("name", ""), body.get("tracks"))
        return row

    @router.get("/playlists/{pid}")
    async def music_playlists_get(request: Request, pid: str):
        _auth(request)
        from src import music_playlists
        row = await asyncio.to_thread(music_playlists.get_playlist, pid)
        if not row:
            raise HTTPException(404, "playlist no existe")
        return row

    @router.patch("/playlists/{pid}")
    async def music_playlists_update(request: Request, pid: str, body: dict):
        _auth(request)
        from src import music_playlists
        row = await asyncio.to_thread(music_playlists.update, pid, name=body.get("name"), tracks=body.get("tracks"))
        if not row:
            raise HTTPException(404, "playlist no existe")
        return row

    @router.delete("/playlists/{pid}")
    async def music_playlists_delete(request: Request, pid: str):
        _auth(request)
        from src import music_playlists
        ok = await asyncio.to_thread(music_playlists.delete, pid)
        if not ok:
            raise HTTPException(404, "playlist no existe")
        return {"ok": True}

    @router.post("/playlists/{pid}/tracks")
    async def music_playlists_add(request: Request, pid: str, body: dict):
        _auth(request)
        from src import music_playlists
        paths = body.get("paths") or ([body["path"]] if body.get("path") else [])
        row = await asyncio.to_thread(music_playlists.add_tracks, pid, paths, body.get("position"))
        if not row:
            raise HTTPException(404, "playlist no existe")
        return row

    @router.delete("/playlists/{pid}/tracks")
    async def music_playlists_remove(request: Request, pid: str, path: str = Query(...)):
        _auth(request)
        from src import music_playlists
        row = await asyncio.to_thread(music_playlists.remove_track, pid, path)
        if not row:
            raise HTTPException(404, "playlist no existe")
        return row

    @router.post("/playlists/{pid}/play")
    async def music_playlists_play(request: Request, pid: str):
        _auth(request)
        from src import music_playlists
        try:
            return await asyncio.to_thread(music_playlists.play, pid)
        except FileNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(503, str(exc)) from exc

    @router.post("/playlists/import")
    async def music_playlists_import(request: Request, body: dict):
        _auth(request)
        from src import music_playlists
        return await asyncio.to_thread(music_playlists.import_m3u, body.get("m3u", ""), body.get("name", ""))

    @router.get("/playlists/{pid}/export")
    async def music_playlists_export(request: Request, pid: str):
        _auth(request)
        from src import music_playlists
        text = await asyncio.to_thread(music_playlists.export_m3u, pid)
        if text is None:
            raise HTTPException(404, "playlist no existe")
        return {"m3u": text}

    @router.get("/rating")
    async def music_rating_get(request: Request, path: str = Query(...)):
        _auth(request)
        from src import music_playlists
        return {"path": path, "rating": (await asyncio.to_thread(music_playlists.get_ratings)).get(path, 0)}

    @router.post("/rating")
    async def music_rating_set(request: Request, body: dict):
        _auth(request)
        from src import music_playlists
        return {"rating": await asyncio.to_thread(music_playlists.set_rating, body.get("path", ""), int(body.get("rating") or 0))}

    @router.get("/instant-mix")
    async def music_instant_mix(request: Request, path: str = Query(...), limit: int = Query(30, ge=1, le=200)):
        _auth(request)
        from src import music_playlists
        return {"items": await asyncio.to_thread(music_playlists.instant_mix, path, limit)}

    @router.get("/smart")
    async def music_smart(
        request: Request,
        bpm_min: float | None = Query(default=None),
        bpm_max: float | None = Query(default=None),
        key: str | None = Query(default=None),
        mode: str | None = Query(default=None),
        energy_min: float | None = Query(default=None),
        valence_min: float | None = Query(default=None),
        not_played_days: float | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=500),
    ):
        _auth(request)
        from src.music_features import smart
        items = await asyncio.to_thread(
            smart, bpm_min=bpm_min, bpm_max=bpm_max, key=key, mode=mode,
            energy_min=energy_min, valence_min=valence_min,
            not_played_days=not_played_days, limit=limit,
        )
        return {"items": items, "count": len(items)}

    @router.get("/discovery")
    async def music_discovery(request: Request, limit: int = Query(default=30, ge=1, le=100)):
        _auth(request)
        return {"items": music_history.discovery(limit=limit)}

    return router

