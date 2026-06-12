# routes/stt_stream_routes.py
"""Streaming STT over WebSocket.

WS /api/stt/stream — dictation mode:
  client sends PCM16 mono 16 kHz binary frames + JSON control messages
  ({"event": "end" | "abort" | "flush"}). Server pushes {"partial": text}
  on a rolling interval while audio accumulates, and {"final": text} after
  "end". "flush" forces an immediate partial (used by tests; harmless live).

The rolling buffer is re-transcribed in-process via
STTService.transcribe_array — local (faster-whisper) provider only.

Auth: HTTP auth middleware (BaseHTTPMiddleware) does not cover WebSocket
scopes, so the route enforces auth itself via the injected `auth_check`
callable (WebSocket -> bool). `None` means no auth (single-user mode /
tests). On rejection the socket is closed with policy violation (1008)
before any audio is processed.
"""
import asyncio
import json
import logging
import time

import numpy as np
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from services.stt.wakeword import get_wakeword_detector

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000
PARTIAL_INTERVAL_S = 1.2
MAX_UTTERANCE_S = 60


def setup_stt_stream_routes(stt_service, auth_check=None):
    router = APIRouter(prefix="/api/stt", tags=["stt"])

    @router.websocket("/stream")
    async def stt_stream(ws: WebSocket):
        if auth_check is not None and not auth_check(ws):
            await ws.close(code=1008)
            return
        await ws.accept()
        buf = bytearray()
        last_partial_at = time.monotonic()  # start clock now; first partial only after interval
        last_partial_text = None
        mode = "dictate"            # 'dictate' | 'wake'
        detector = None

        async def _transcribe() -> str | None:
            if not buf:
                return ""
            audio = np.frombuffer(bytes(buf), dtype=np.int16).astype(np.float32) / 32768.0
            return await asyncio.to_thread(stt_service.transcribe_array, audio)

        async def _send_partial(force: bool = False):
            nonlocal last_partial_at, last_partial_text
            now = time.monotonic()
            if not force and (now - last_partial_at) < PARTIAL_INTERVAL_S:
                return
            last_partial_at = now
            text = await _transcribe()
            if text is None:
                await ws.send_json({"error": "STT local provider unavailable"})
                return
            if text and text != last_partial_text:
                last_partial_text = text
                await ws.send_json({"partial": text})

        try:
            while True:
                msg = await ws.receive()
                if msg.get("type") == "websocket.disconnect":
                    break

                if msg.get("bytes") is not None:
                    if mode == "wake":
                        if detector is None:
                            continue
                        fired = await asyncio.to_thread(detector.feed, msg["bytes"])
                        if fired:
                            detector.reset()
                            mode = "dictate"
                            buf.clear()
                            last_partial_text = None
                            await ws.send_json({"wake": True})
                        continue

                    if not getattr(stt_service, "available", False):
                        await ws.send_json({"error": "STT service not available"})
                        continue
                    buf.extend(msg["bytes"])
                    if len(buf) >= SAMPLE_RATE * 2 * MAX_UTTERANCE_S:
                        text = await _transcribe()
                        await ws.send_json({"final": text or ""})
                        buf.clear()
                        last_partial_text = None
                        continue
                    await _send_partial()
                    continue

                if msg.get("text") is not None:
                    try:
                        payload = json.loads(msg["text"])
                        event = payload.get("event")
                        req_mode = payload.get("mode")
                    except (json.JSONDecodeError, AttributeError):
                        continue
                    if req_mode == "wake":
                        detector = get_wakeword_detector()
                        if detector is None:
                            await ws.send_json({"error": "wake word unavailable"})
                        else:
                            detector.reset()
                            mode = "wake"
                            buf.clear()
                            last_partial_text = None
                        continue
                    if req_mode == "dictate":
                        mode = "dictate"
                        continue
                    if event == "abort":
                        buf.clear()
                        last_partial_text = None
                    elif event == "flush":
                        await _send_partial(force=True)
                    elif event == "end":
                        text = await _transcribe()
                        if text is None:
                            await ws.send_json({"error": "STT local provider unavailable"})
                        else:
                            await ws.send_json({"final": text})
                        buf.clear()
                        last_partial_text = None
        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.error(f"STT stream error: {e}", exc_info=True)
            try:
                await ws.send_json({"error": str(e)})
            except Exception:
                pass

    return router
