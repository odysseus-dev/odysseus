# services/stt/wakeword.py
"""Server-side wake-word detection for voice dialog standby mode.

Wraps openwakeword for use on the streaming STT WebSocket: PCM16 mono 16 kHz
chunks are fed in arbitrary sizes; the detector buffers to openwakeword's
80 ms frame (1280 samples) and reports detection against a threshold.

Model: a custom openwakeword .onnx (e.g. ~/.hermes/voice/hey_soloway_v0.1.onnx,
trained in the hermes-voice-manager project). CPU-only.
"""
import logging
import os
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

FRAME_SAMPLES = 1280  # openwakeword expects 80 ms @ 16 kHz

DEFAULT_MODEL_PATH = os.environ.get(
    "WAKEWORD_MODEL",
    str(Path.home() / ".hermes" / "voice" / "hey_soloway_v0.1.onnx"),
)
DEFAULT_THRESHOLD = float(os.environ.get("WAKEWORD_THRESHOLD", "0.7"))


class WakeWordDetector:
    def __init__(self, model_path: str = DEFAULT_MODEL_PATH,
                 threshold: float = DEFAULT_THRESHOLD):
        from openwakeword.model import Model
        self._model = Model(wakeword_model_paths=[model_path])
        self._threshold = threshold
        self._buffer = np.array([], dtype=np.int16)

    def feed(self, pcm16_bytes: bytes) -> bool:
        """Feed PCM16 bytes; True the moment any model score >= threshold."""
        chunk = np.frombuffer(pcm16_bytes, dtype=np.int16)
        self._buffer = np.concatenate([self._buffer, chunk])
        fired = False
        while len(self._buffer) >= FRAME_SAMPLES:
            frame = self._buffer[:FRAME_SAMPLES]
            self._buffer = self._buffer[FRAME_SAMPLES:]
            scores = self._model.predict(frame)
            if any(s >= self._threshold for s in scores.values()):
                fired = True
        return fired

    def reset(self):
        self._buffer = np.array([], dtype=np.int16)
        try:
            self._model.reset()
        except Exception:
            pass


_detector: Optional[WakeWordDetector] = None


def get_wakeword_detector() -> Optional[WakeWordDetector]:
    """Singleton; prefer new_wakeword_detector() for per-connection use."""
    global _detector
    if _detector is None:
        try:
            _detector = WakeWordDetector()
            logger.info("Wake-word detector loaded: %s", DEFAULT_MODEL_PATH)
        except Exception as e:
            logger.warning("Wake-word detector unavailable: %s", e)
            return None
    return _detector


def new_wakeword_detector() -> Optional[WakeWordDetector]:
    """Fresh detector instance — REQUIRED for per-connection use: the
    detector holds a mutable frame buffer and openwakeword streaming
    state, so sharing one across WebSocket connections cross-contaminates
    detections. Returns None when openwakeword or the model is missing.
    Model load is CPU-bound (~hundreds of ms); call from a worker thread."""
    try:
        return WakeWordDetector()
    except Exception as e:
        logger.warning("Wake-word detector unavailable: %s", e)
        return None
