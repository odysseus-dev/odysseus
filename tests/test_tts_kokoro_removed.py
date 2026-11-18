"""The retired local Kokoro TTS provider must not linger in dependencies or docs."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_kokoro_is_not_an_optional_dependency():
    requirements = (ROOT / "requirements-optional.txt").read_text(encoding="utf-8").lower()
    assert "kokoro" not in requirements
    assert "soundfile" not in requirements


def test_setup_docs_do_not_advertise_kokoro():
    setup = (ROOT / "website" / "setup.md").read_text(encoding="utf-8").lower()
    assert "kokoro" not in setup
    assert "local kokoro" not in setup
