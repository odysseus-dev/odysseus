"""Supertonic TTS integration tests (skip when model or sherpa-onnx missing)."""

import pytest

from services.tts.supertonic_pipeline import find_supertonic_model_dir
from services.tts import fugassa_supertonic as fugassa_tts


pytestmark = pytest.mark.skipif(
    find_supertonic_model_dir() is None,
    reason="Supertonic model not installed",
)


def test_supertonic_synthesize_short_cs():
    audio = fugassa_tts.synthesize_supertonic(
        "Krátký test.",
        lang="cs",
        speaker_id=0,
        speed=1.0,
        use_cache=False,
    )
    assert audio is not None
    assert audio[:4] == b"RIFF"
