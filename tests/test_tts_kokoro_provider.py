"""Kokoro ("local") TTS provider: voice resolution, speed pass-through, and
the browser fallback + structured 503 when the package isn't installed.
The kokoro package itself is never loaded — CI must not need torch."""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.tts_routes import setup_tts_routes
from services.tts.tts_service import TTSService, KOKORO_VOICES


def _settings(voice="af_heart", speed="1"):
    return {
        "tts_enabled": True,
        "tts_provider": "local",
        "tts_model": "tts-1",
        "tts_voice": voice,
        "tts_speed": speed,
        "tts_piper_default_voice": "en_US-lessac-low",
    }


class _FakeKokoro:
    available = True
    device = "cuda"

    def __init__(self):
        self.calls = []

    def synthesize_raw(self, text, voice, speed=1.0):
        self.calls.append((text, voice, speed))
        return b"RIFFkokorowav"


class _MissingKokoro:
    available = False
    device = "cpu"


def _service(tmp_path, monkeypatch, kokoro, settings):
    service = TTSService(cache_dir=str(tmp_path / "cache"), piper_voices_dir=str(tmp_path / "v"))
    monkeypatch.setattr(service, "_get_kokoro", lambda: kokoro)
    monkeypatch.setattr(service, "_load_settings", lambda owner="": dict(settings))
    return service


def test_synthesize_passes_resolved_voice_and_speed(tmp_path, monkeypatch):
    fake = _FakeKokoro()
    service = _service(tmp_path, monkeypatch, fake, _settings(voice="bm_george", speed="1.5"))

    audio = service.synthesize("Hello there", use_cache=False)
    assert audio == b"RIFFkokorowav"
    text, voice, speed = fake.calls[0]
    assert voice == "bm_george"
    assert speed == 1.5


def test_foreign_voice_falls_back_to_default_kokoro_voice(tmp_path, monkeypatch):
    # Leftover prefs from another provider ("alloy", a Piper id, ...) must not
    # reach the kokoro package — it raises on unknown voice ids.
    fake = _FakeKokoro()
    service = _service(tmp_path, monkeypatch, fake, _settings(voice="alloy"))

    service.synthesize("Hi", use_cache=False)
    assert fake.calls[0][1] == "af_heart"


def test_effective_provider_local_ok(tmp_path, monkeypatch):
    service = _service(tmp_path, monkeypatch, _FakeKokoro(), _settings())
    assert service.effective_provider(_settings()) == ("local", "")


def test_effective_provider_falls_back_when_kokoro_missing(tmp_path, monkeypatch):
    service = _service(tmp_path, monkeypatch, _MissingKokoro(), _settings())
    provider, reason = service.effective_provider(_settings())
    assert provider == "browser"
    assert "Kokoro" in reason


def test_stats_resolve_kokoro_voice_and_device(tmp_path, monkeypatch):
    service = _service(tmp_path, monkeypatch, _FakeKokoro(), _settings(voice="alloy"))
    stats = service.get_stats()
    assert stats["provider"] == "local"
    assert stats["voice"] == "af_heart"
    assert stats["model"] == "Kokoro-82M (CUDA)"
    assert stats["fallback_reason"] == ""


def test_kokoro_voice_catalog_is_well_formed():
    ids = [v["id"] for v in KOKORO_VOICES]
    assert "af_heart" in ids
    assert len(ids) == len(set(ids))
    assert all(v["language"] for v in KOKORO_VOICES)


def test_synthesize_route_returns_structured_browser_fallback(tmp_path, monkeypatch):
    # Preview/play must get a machine-readable hint to use Web Speech instead
    # of an opaque 500 when the server can't synthesize on this machine.
    service = _service(tmp_path, monkeypatch, _MissingKokoro(), _settings())
    monkeypatch.setenv("AUTH_ENABLED", "false")
    app = FastAPI()
    app.include_router(setup_tts_routes(service))
    client = TestClient(app)

    r = client.post("/api/tts/synthesize", json={"text": "hello", "format": "audio"})
    assert r.status_code == 503
    detail = r.json()["detail"]
    assert detail["fallback"] == "browser"
    assert "Kokoro" in detail["message"]
