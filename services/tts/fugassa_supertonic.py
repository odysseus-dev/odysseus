"""Fugassa GM TTS via Supertonic-3 — thin facade over TTSService."""

from __future__ import annotations

from typing import Any, Optional

from services.tts.tts_service import get_tts_service


def supertonic_available() -> bool:
    return get_tts_service().supertonic_available()


def get_supertonic_stats() -> dict[str, Any]:
    stats = get_tts_service().get_stats()
    return {
        "supertonic_ready": stats.get("supertonic_ready", False),
        "supertonic_model_path": stats.get("supertonic_model_path"),
    }


def list_supertonic_voices(lang: str) -> list[dict[str, Any]]:
    return get_tts_service().list_supertonic_voices(lang)


def synthesize_supertonic(
    text: str,
    *,
    lang: str = "cs",
    speaker_id: int = 0,
    speed: float = 1.0,
    use_cache: bool = True,
) -> Optional[bytes]:
    return get_tts_service().synthesize_supertonic(
        text,
        lang=lang,
        speaker_id=speaker_id,
        speed=speed,
        use_cache=use_cache,
    )
