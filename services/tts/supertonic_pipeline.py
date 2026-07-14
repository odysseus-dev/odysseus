"""Sherpa-onnx Supertonic-3 TTS — CPU-only, for Fugassa GM narration."""

from __future__ import annotations

import io
import logging
import wave
from pathlib import Path
from typing import Optional

from src.constants import TTS_MODELS_DIR

logger = logging.getLogger(__name__)

_REQUIRED_FILES = (
    "duration_predictor.int8.onnx",
    "text_encoder.int8.onnx",
    "vector_estimator.int8.onnx",
    "vocoder.int8.onnx",
    "tts.json",
    "unicode_indexer.bin",
    "voice.bin",
)

_SUPPORTED_LANGS = frozenset({"en", "cs", "uk"})


def find_supertonic_model_dir() -> Optional[Path]:
    root = Path(TTS_MODELS_DIR)
    if not root.is_dir():
        return None
    candidates = [root, *root.iterdir()] if root.is_dir() else []
    for candidate in candidates:
        if not candidate.is_dir():
            continue
        if all((candidate / name).is_file() for name in _REQUIRED_FILES):
            return candidate
    return None


class SupertonicPipeline:
    """Lazy-loaded sherpa-onnx OfflineTts for Supertonic-3."""

    def __init__(self) -> None:
        self._tts = None
        self._model_dir: Optional[Path] = None
        self.available = False

    @property
    def model_dir(self) -> Optional[Path]:
        return self._model_dir

    def _ensure_loaded(self) -> bool:
        if self._tts is not None:
            return True
        model_dir = find_supertonic_model_dir()
        if model_dir is None:
            return False
        try:
            import sherpa_onnx

            config = sherpa_onnx.OfflineTtsConfig(
                model=sherpa_onnx.OfflineTtsModelConfig(
                    supertonic=sherpa_onnx.OfflineTtsSupertonicModelConfig(
                        duration_predictor=str(model_dir / "duration_predictor.int8.onnx"),
                        text_encoder=str(model_dir / "text_encoder.int8.onnx"),
                        vector_estimator=str(model_dir / "vector_estimator.int8.onnx"),
                        vocoder=str(model_dir / "vocoder.int8.onnx"),
                        tts_json=str(model_dir / "tts.json"),
                        unicode_indexer=str(model_dir / "unicode_indexer.bin"),
                        voice_style=str(model_dir / "voice.bin"),
                    ),
                    debug=False,
                    num_threads=2,
                    provider="cpu",
                ),
            )
            self._tts = sherpa_onnx.OfflineTts(config)
            self._model_dir = model_dir
            self.available = True
            logger.info("Supertonic-3 TTS loaded from %s", model_dir)
            return True
        except ImportError:
            logger.warning("sherpa-onnx not installed — Fugassa TTS unavailable")
            return False
        except Exception as exc:
            logger.error("Supertonic-3 init failed: %s", exc, exc_info=True)
            return False

    def synthesize_raw(
        self,
        text: str,
        *,
        lang: str = "cs",
        speaker_id: int = 0,
        speed: float = 1.0,
    ) -> Optional[bytes]:
        if not text or not text.strip():
            return None
        if not self._ensure_loaded() or self._tts is None:
            return None

        lang = (lang or "cs").strip().lower()
        if lang not in _SUPPORTED_LANGS:
            lang = "cs"
        speaker_id = max(0, min(9, int(speaker_id)))
        speed = float(speed) if float(speed) > 0 else 1.0

        try:
            import sherpa_onnx

            gen_config = sherpa_onnx.GenerationConfig()
            gen_config.sid = speaker_id
            gen_config.num_steps = 8
            gen_config.speed = speed
            gen_config.extra["lang"] = lang

            audio = self._tts.generate(text.strip(), gen_config)
            if audio is None or audio.samples is None or len(audio.samples) == 0:
                return None

            samples = audio.samples
            sample_rate = int(getattr(audio, "sample_rate", 24000) or 24000)

            pcm = bytearray()
            for sample in samples:
                clipped = max(-1.0, min(1.0, float(sample)))
                pcm.extend(int(clipped * 32767).to_bytes(2, byteorder="little", signed=True))

            buf = io.BytesIO()
            with wave.open(buf, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(sample_rate)
                wf.writeframes(pcm)
            return buf.getvalue()
        except Exception as exc:
            logger.error("Supertonic synthesis failed: %s", exc, exc_info=True)
            return None


_pipeline: Optional[SupertonicPipeline] = None


def get_supertonic_pipeline() -> SupertonicPipeline:
    global _pipeline
    if _pipeline is None:
        _pipeline = SupertonicPipeline()
        _pipeline._ensure_loaded()
    return _pipeline
