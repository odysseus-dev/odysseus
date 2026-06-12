"""Piper TTS provider: voice scanning, voice resolution (per-user pref ->
global default -> first installed), and graceful degradation when the
piper-tts package or voices are missing. PiperVoice itself is never loaded
here — CI must not need onnxruntime."""
import json

from services.tts.tts_service import TTSService, _PiperPipeline

_VOICE = "en_US-lessac-medium"


def _install_fake_voice(voices_dir, voice_id=_VOICE, language="English"):
    voices_dir.mkdir(parents=True, exist_ok=True)
    (voices_dir / f"{voice_id}.onnx").write_bytes(b"fake-onnx")
    (voices_dir / f"{voice_id}.onnx.json").write_text(
        json.dumps({"language": {"name_english": language}}), encoding="utf-8"
    )


def _settings(provider="piper", voice="alloy", default_voice=_VOICE):
    return {
        "tts_enabled": True,
        "tts_provider": provider,
        "tts_model": "tts-1",
        "tts_voice": voice,
        "tts_speed": "1",
        "tts_piper_default_voice": default_voice,
    }


class _FakePiper:
    available = True

    def __init__(self, voice_ids):
        self._ids = voice_ids
        self.calls = []
        self.evicted = []

    def list_voices(self):
        return [{"id": v, "name": v, "language": "English"} for v in self._ids]

    def evict_voice(self, voice_id):
        self.evicted.append(voice_id)

    def synthesize_raw(self, text, voice_id, length_scale=1.0):
        self.calls.append((text, voice_id, length_scale))
        return b"RIFFfakewav"


# ── _PiperPipeline voice scanning (no piper import needed) ──

def test_list_voices_scans_onnx_json_pairs(tmp_path):
    voices_dir = tmp_path / "voices"
    _install_fake_voice(voices_dir, "en_US-lessac-medium")
    _install_fake_voice(voices_dir, "de_DE-thorsten-medium", language="German")
    # Orphan .onnx without config must be ignored
    (voices_dir / "broken.onnx").write_bytes(b"x")

    pipeline = _PiperPipeline(voices_dir)
    voices = pipeline.list_voices()
    assert [v["id"] for v in voices] == ["de_DE-thorsten-medium", "en_US-lessac-medium"]
    assert voices[0]["language"] == "German"


def test_list_voices_empty_when_dir_missing(tmp_path):
    pipeline = _PiperPipeline(tmp_path / "nope")
    assert pipeline.list_voices() == []


# ── Voice resolution: user pref → global default → first installed ──

def test_resolve_prefers_user_voice_when_installed(tmp_path, monkeypatch):
    service = TTSService(cache_dir=str(tmp_path / "cache"), piper_voices_dir=str(tmp_path / "v"))
    monkeypatch.setattr(service, "_get_piper", lambda: _FakePiper(["a-voice", "b-voice"]))
    assert service._resolve_piper_voice(_settings(voice="b-voice", default_voice="a-voice")) == "b-voice"


def test_resolve_falls_back_to_global_default(tmp_path, monkeypatch):
    service = TTSService(cache_dir=str(tmp_path / "cache"), piper_voices_dir=str(tmp_path / "v"))
    monkeypatch.setattr(service, "_get_piper", lambda: _FakePiper([_VOICE]))
    # User pref "alloy" (an OpenAI voice) isn't an installed Piper voice
    assert service._resolve_piper_voice(_settings(voice="alloy")) == _VOICE


def test_resolve_falls_back_to_first_installed(tmp_path, monkeypatch):
    service = TTSService(cache_dir=str(tmp_path / "cache"), piper_voices_dir=str(tmp_path / "v"))
    monkeypatch.setattr(service, "_get_piper", lambda: _FakePiper(["z-voice"]))
    assert service._resolve_piper_voice(_settings(voice="alloy", default_voice="missing")) == "z-voice"


def test_resolve_none_when_no_voices(tmp_path, monkeypatch):
    service = TTSService(cache_dir=str(tmp_path / "cache"), piper_voices_dir=str(tmp_path / "v"))
    monkeypatch.setattr(service, "_get_piper", lambda: _FakePiper([]))
    assert service._resolve_piper_voice(_settings()) is None


# ── synthesize() with the piper provider ──

def test_synthesize_uses_piper_and_inverts_speed(tmp_path, monkeypatch):
    service = TTSService(cache_dir=str(tmp_path / "cache"), piper_voices_dir=str(tmp_path / "v"))
    fake = _FakePiper([_VOICE])
    monkeypatch.setattr(service, "_get_piper", lambda: fake)
    settings = _settings(voice=_VOICE)
    settings["tts_speed"] = "2"
    monkeypatch.setattr(service, "_load_settings", lambda owner="": dict(settings))

    audio = service.synthesize("Hello **world**", use_cache=False)
    assert audio == b"RIFFfakewav"
    text, voice_id, length_scale = fake.calls[0]
    assert text == "Hello world"  # markdown stripped before synthesis
    assert voice_id == _VOICE
    assert length_scale == 0.5  # 2x speed = half length_scale


def test_synthesize_returns_none_without_voices(tmp_path, monkeypatch):
    # Server-side synthesis declines (effective provider degrades to browser,
    # which is client-side) — but the feature stays available.
    service = TTSService(cache_dir=str(tmp_path / "cache"), piper_voices_dir=str(tmp_path / "v"))
    monkeypatch.setattr(service, "_get_piper", lambda: _FakePiper([]))
    monkeypatch.setattr(service, "_load_settings", lambda owner="": _settings())
    assert service.synthesize("hello", use_cache=False) is None
    assert service.available is True  # browser fallback keeps TTS usable


# ── effective_provider fallback matrix ──

class _UnavailablePiper:
    available = False

    def list_voices(self):
        return []


class _UnavailableKokoro:
    available = False


def _service(tmp_path, monkeypatch, piper=None, kokoro=None):
    service = TTSService(cache_dir=str(tmp_path / "cache"), piper_voices_dir=str(tmp_path / "v"))
    if piper is not None:
        monkeypatch.setattr(service, "_get_piper", lambda: piper)
    if kokoro is not None:
        monkeypatch.setattr(service, "_get_kokoro", lambda: kokoro)
    return service


def test_effective_provider_piper_ok(tmp_path, monkeypatch):
    service = _service(tmp_path, monkeypatch, piper=_FakePiper([_VOICE]))
    assert service.effective_provider(_settings()) == ("piper", "")


def test_effective_provider_piper_pkg_missing_falls_back_to_browser(tmp_path, monkeypatch):
    service = _service(tmp_path, monkeypatch, piper=_UnavailablePiper())
    provider, reason = service.effective_provider(_settings())
    assert provider == "browser"
    assert "piper-tts" in reason


def test_effective_provider_piper_no_voices_falls_back_to_browser(tmp_path, monkeypatch):
    service = _service(tmp_path, monkeypatch, piper=_FakePiper([]))
    provider, reason = service.effective_provider(_settings())
    assert provider == "browser"
    assert "voices" in reason.lower()


def test_effective_provider_kokoro_missing_falls_back_to_browser(tmp_path, monkeypatch):
    # Kokoro runs on CPU or GPU; only a missing package degrades to browser.
    service = _service(tmp_path, monkeypatch, kokoro=_UnavailableKokoro())
    provider, reason = service.effective_provider(_settings(provider="local"))
    assert provider == "browser"
    assert "Kokoro" in reason


def test_effective_provider_disabled_when_toggled_off(tmp_path, monkeypatch):
    service = _service(tmp_path, monkeypatch, piper=_FakePiper([_VOICE]))
    settings = _settings()
    settings["tts_enabled"] = False
    assert service.effective_provider(settings) == ("disabled", "")


def test_stats_report_fallback_reason(tmp_path, monkeypatch):
    service = _service(tmp_path, monkeypatch, piper=_FakePiper([]))
    monkeypatch.setattr(service, "_load_settings", lambda owner="": _settings())
    stats = service.get_stats()
    assert stats["available"] is True
    assert stats["provider"] == "browser"
    assert stats["configured_provider"] == "piper"
    assert stats["fallback_reason"]


# ── Voice catalog + downloads (httpx mocked — no network in CI) ──

_CATALOG = {
    "en_US-lessac-low": {
        "language": {"name_english": "English", "code": "en_US"},
        "quality": "low",
        "files": {
            "en/en_US/lessac/low/en_US-lessac-low.onnx": {"size_bytes": 60 * 1024 * 1024},
            "en/en_US/lessac/low/en_US-lessac-low.onnx.json": {"size_bytes": 4096},
            "en/en_US/lessac/low/MODEL_CARD": {"size_bytes": 200},
        },
    },
    "de_DE-thorsten-medium": {
        "language": {"name_english": "German", "code": "de_DE"},
        "quality": "medium",
        "files": {
            "de/de_DE/thorsten/medium/de_DE-thorsten-medium.onnx": {"size_bytes": 75 * 1024 * 1024},
            "de/de_DE/thorsten/medium/de_DE-thorsten-medium.onnx.json": {"size_bytes": 4096},
        },
    },
}


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeStream:
    """Context manager mimicking httpx.stream()."""

    def __init__(self, data=b"fake-model-bytes"):
        self._data = data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def raise_for_status(self):
        pass

    def iter_bytes(self, chunk_size=None):
        yield self._data


def test_catalog_parses_marks_installed_and_caches(tmp_path, monkeypatch):
    import services.tts.tts_service as mod
    voices_dir = tmp_path / "v"
    _install_fake_voice(voices_dir, "de_DE-thorsten-medium", language="German")
    service = TTSService(cache_dir=str(tmp_path / "cache"), piper_voices_dir=str(voices_dir))
    monkeypatch.setattr(mod.httpx, "get", lambda *a, **k: _FakeResponse(_CATALOG))

    rows = service.get_piper_catalog()
    assert [r["id"] for r in rows] == ["en_US-lessac-low", "de_DE-thorsten-medium"]
    en = rows[0]
    assert en["language"] == "English" and en["quality"] == "low"
    assert en["installed"] is False
    assert en["size_mb"] == 60.0  # MODEL_CARD bytes excluded
    assert rows[1]["installed"] is True
    # Raw catalog cached on disk for subsequent calls
    assert (voices_dir / "_catalog.json").exists()


def test_catalog_served_from_fresh_cache_without_network(tmp_path, monkeypatch):
    import services.tts.tts_service as mod
    voices_dir = tmp_path / "v"
    voices_dir.mkdir(parents=True)
    (voices_dir / "_catalog.json").write_text(json.dumps(_CATALOG), encoding="utf-8")
    service = TTSService(cache_dir=str(tmp_path / "cache"), piper_voices_dir=str(voices_dir))

    def _no_network(*a, **k):
        raise AssertionError("network call despite fresh cache")

    monkeypatch.setattr(mod.httpx, "get", _no_network)
    rows = service.get_piper_catalog()
    assert len(rows) == 2


def test_download_voice_writes_onnx_pair(tmp_path, monkeypatch):
    import services.tts.tts_service as mod
    voices_dir = tmp_path / "v"
    service = TTSService(cache_dir=str(tmp_path / "cache"), piper_voices_dir=str(voices_dir))
    monkeypatch.setattr(mod.httpx, "get", lambda *a, **k: _FakeResponse(_CATALOG))
    monkeypatch.setattr(mod.httpx, "stream", lambda *a, **k: _FakeStream())

    service.download_piper_voice("en_US-lessac-low")
    assert (voices_dir / "en_US-lessac-low.onnx").read_bytes() == b"fake-model-bytes"
    assert (voices_dir / "en_US-lessac-low.onnx.json").exists()
    # MODEL_CARD is not downloaded
    assert not (voices_dir / "MODEL_CARD").exists()


def test_download_unknown_voice_raises(tmp_path, monkeypatch):
    import pytest
    import services.tts.tts_service as mod
    service = TTSService(cache_dir=str(tmp_path / "cache"), piper_voices_dir=str(tmp_path / "v"))
    monkeypatch.setattr(mod.httpx, "get", lambda *a, **k: _FakeResponse(_CATALOG))
    with pytest.raises(ValueError):
        service.download_piper_voice("xx_XX-nope-high")


def test_delete_voice_removes_pair_and_guards_missing(tmp_path, monkeypatch):
    voices_dir = tmp_path / "v"
    _install_fake_voice(voices_dir, _VOICE)
    service = TTSService(cache_dir=str(tmp_path / "cache"), piper_voices_dir=str(voices_dir))
    monkeypatch.setattr(service, "_get_piper", lambda: _FakePiper([_VOICE]))

    assert service.delete_piper_voice(_VOICE) is True
    assert not (voices_dir / f"{_VOICE}.onnx").exists()
    assert not (voices_dir / f"{_VOICE}.onnx.json").exists()
    assert service.delete_piper_voice(_VOICE) is False


# ── Startup bootstrap of the default voice ──

def test_ensure_default_voice_downloads_when_none_installed(tmp_path, monkeypatch):
    service = TTSService(cache_dir=str(tmp_path / "cache"), piper_voices_dir=str(tmp_path / "v"))
    monkeypatch.setattr(service, "_get_piper", lambda: _FakePiper([]))
    monkeypatch.setattr(service, "_load_settings", lambda owner="": _settings(default_voice="en_US-lessac-low"))
    downloads = []
    monkeypatch.setattr(service, "download_piper_voice", downloads.append)

    service.ensure_default_voice()
    assert downloads == ["en_US-lessac-low"]


def test_ensure_default_voice_noop_when_voice_installed(tmp_path, monkeypatch):
    service = TTSService(cache_dir=str(tmp_path / "cache"), piper_voices_dir=str(tmp_path / "v"))
    monkeypatch.setattr(service, "_get_piper", lambda: _FakePiper([_VOICE]))
    monkeypatch.setattr(service, "_load_settings", lambda owner="": _settings())
    downloads = []
    monkeypatch.setattr(service, "download_piper_voice", downloads.append)

    service.ensure_default_voice()
    assert downloads == []


def test_ensure_default_voice_noop_when_piper_not_installed(tmp_path, monkeypatch):
    service = TTSService(cache_dir=str(tmp_path / "cache"), piper_voices_dir=str(tmp_path / "v"))
    monkeypatch.setattr(service, "_get_piper", lambda: _UnavailablePiper())
    monkeypatch.setattr(service, "_load_settings", lambda owner="": _settings())
    downloads = []
    monkeypatch.setattr(service, "download_piper_voice", downloads.append)

    service.ensure_default_voice()
    assert downloads == []  # offline/CPU boxes just use the browser fallback


def test_ensure_default_voice_noop_for_other_providers(tmp_path, monkeypatch):
    service = TTSService(cache_dir=str(tmp_path / "cache"), piper_voices_dir=str(tmp_path / "v"))
    monkeypatch.setattr(service, "_load_settings", lambda owner="": _settings(provider="browser"))
    downloads = []
    monkeypatch.setattr(service, "download_piper_voice", downloads.append)

    service.ensure_default_voice()
    assert downloads == []


# ── Download/delete endpoints ──

def _make_client(monkeypatch, service):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import routes.tts_routes as routes_mod

    monkeypatch.setenv("AUTH_ENABLED", "false")  # require_admin passes
    monkeypatch.setattr(routes_mod, "_download_jobs", {})
    app = FastAPI()
    app.include_router(routes_mod.setup_tts_routes(service))
    return TestClient(app)


def test_download_endpoint_runs_job_and_reports_done(tmp_path, monkeypatch):
    import time
    service = TTSService(cache_dir=str(tmp_path / "cache"), piper_voices_dir=str(tmp_path / "v"))
    done = []
    monkeypatch.setattr(service, "download_piper_voice", done.append)
    client = _make_client(monkeypatch, service)

    r = client.post("/api/tts/voices/download", json={"voice_id": "en_US-lessac-low"})
    assert r.status_code == 200
    assert r.json()["status"] == "downloading"

    for _ in range(50):  # background thread should finish near-instantly
        status = client.get("/api/tts/voices/download/en_US-lessac-low/status").json()
        if status["status"] == "done":
            break
        time.sleep(0.05)
    assert status["status"] == "done"
    assert done == ["en_US-lessac-low"]


def test_download_endpoint_reports_error(tmp_path, monkeypatch):
    import time
    service = TTSService(cache_dir=str(tmp_path / "cache"), piper_voices_dir=str(tmp_path / "v"))

    def _boom(voice_id):
        raise RuntimeError("catalog offline")

    monkeypatch.setattr(service, "download_piper_voice", _boom)
    client = _make_client(monkeypatch, service)

    client.post("/api/tts/voices/download", json={"voice_id": "en_US-lessac-low"})
    for _ in range(50):
        status = client.get("/api/tts/voices/download/en_US-lessac-low/status").json()
        if status["status"] == "error":
            break
        time.sleep(0.05)
    assert status["status"] == "error"
    assert "catalog offline" in status["error"]


def test_download_endpoint_rejects_bad_voice_id(tmp_path, monkeypatch):
    service = TTSService(cache_dir=str(tmp_path / "cache"), piper_voices_dir=str(tmp_path / "v"))
    client = _make_client(monkeypatch, service)
    r = client.post("/api/tts/voices/download", json={"voice_id": "../../etc/passwd"})
    assert r.status_code == 400


def test_delete_endpoint_404_when_not_installed(tmp_path, monkeypatch):
    service = TTSService(cache_dir=str(tmp_path / "cache"), piper_voices_dir=str(tmp_path / "v"))
    client = _make_client(monkeypatch, service)
    r = client.delete("/api/tts/voices/en_US-lessac-low")
    assert r.status_code == 404


def test_voices_endpoint_includes_kokoro_catalog(tmp_path, monkeypatch):
    service = TTSService(cache_dir=str(tmp_path / "cache"), piper_voices_dir=str(tmp_path / "v"))
    client = _make_client(monkeypatch, service)
    data = client.get("/api/tts/voices").json()
    assert data["voices"] == []
    kokoro_ids = [v["id"] for v in data["kokoro"]]
    assert "af_heart" in kokoro_ids and "bm_george" in kokoro_ids


# ── Per-user voice resolution in _load_settings ──

def test_load_settings_resolves_per_user_voice(tmp_path, monkeypatch):
    import src.settings as settings_mod
    monkeypatch.setattr(settings_mod, "load_settings", lambda: {
        "tts_enabled": True, "tts_provider": "piper", "tts_model": "tts-1",
        "tts_voice": "global-voice", "tts_speed": "1",
    })
    monkeypatch.setattr(
        settings_mod, "get_user_setting",
        lambda key, owner="", default=None: "alice-voice" if owner == "alice" else default,
    )
    service = TTSService(cache_dir=str(tmp_path / "cache"), piper_voices_dir=str(tmp_path / "v"))
    assert service._load_settings("alice")["tts_voice"] == "alice-voice"
