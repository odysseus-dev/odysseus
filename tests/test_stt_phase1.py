"""Phase 1 (#6319) STT tests: validation, cache invalidation, language compat,
attachment transcribe path, ownership, cleanup.

Conventions follow tests/test_speech_service_toggles.py (direct service unit
tests) and tests/test_upload_routes_owner_scope.py (fake Request + direct
route-endpoint calls, no TestClient).
"""

import io
import os
import sys
import tempfile
import types
from types import SimpleNamespace

import pytest
from fastapi import UploadFile

from services.stt.stt_service import STTService


# ── sanitizers ──

def test_sanitize_model_size_accepts_common_ids():
    assert STTService.sanitize_model_size("base") == "base"
    assert STTService.sanitize_model_size(" tiny ") == "tiny"
    assert STTService.sanitize_model_size("large-v3") == "large-v3"
    assert STTService.sanitize_model_size("turbo") == "turbo"
    assert STTService.sanitize_model_size("Systran/faster-whisper-base") == "Systran/faster-whisper-base"


def test_sanitize_model_size_rejects_malformed():
    assert STTService.sanitize_model_size("") is None
    assert STTService.sanitize_model_size("   ") is None
    assert STTService.sanitize_model_size(None) is None
    assert STTService.sanitize_model_size(123) is None
    assert STTService.sanitize_model_size("../evil") is None
    assert STTService.sanitize_model_size("/abs/path") is None
    assert STTService.sanitize_model_size("bad;cmd") is None
    assert STTService.sanitize_model_size("a" * 129) is None


def test_sanitize_language_auto_and_codes():
    assert STTService.sanitize_language("") == ""
    assert STTService.sanitize_language(None) == ""
    assert STTService.sanitize_language("en") == "en"
    assert STTService.sanitize_language("SK") == "sk"
    assert STTService.sanitize_language("cs") == "cs"
    assert STTService.sanitize_language("pt-BR") == "pt-br"


def test_sanitize_language_rejects_malformed():
    assert STTService.sanitize_language(123) is None
    assert STTService.sanitize_language("xx!") is None
    assert STTService.sanitize_language("a" * 17) is None


def test_suffix_for_filename():
    assert STTService.suffix_for_filename("audio.m4a") == ".m4a"
    assert STTService.suffix_for_filename("VOICE.MP3") == ".mp3"
    assert STTService.suffix_for_filename("recording.wav") == ".wav"
    assert STTService.suffix_for_filename("note.ogg") == ".ogg"
    assert STTService.suffix_for_filename("audio.webm") == ".webm"
    assert STTService.suffix_for_filename("notes.txt") == ".webm"
    assert STTService.suffix_for_filename(None) == ".webm"


# ── model cache invalidation ──

def _install_fake_whisper(monkeypatch, seen):
    mod = types.ModuleType("faster_whisper")

    class FakeModel:
        def __init__(self, size, device="cpu", compute_type="int8"):
            seen.append(size)

        def transcribe(self, *args, **kwargs):
            class _Seg:
                text = "hi"
            class _Info:
                language = "en"
                language_probability = 0.9
            return iter([_Seg()]), _Info()

    mod.WhisperModel = FakeModel
    monkeypatch.setitem(sys.modules, "faster_whisper", mod)
    # torch probe is optional; force CPU path deterministically
    torch_mod = types.ModuleType("torch")
    torch_mod.cuda = SimpleNamespace(is_available=lambda: False)
    monkeypatch.setitem(sys.modules, "torch", torch_mod)


def test_model_cache_reloads_on_setting_change(monkeypatch):
    seen = []
    _install_fake_whisper(monkeypatch, seen)
    service = STTService()
    current = {"model": "base"}
    monkeypatch.setattr(service, "_load_settings", lambda: {
        "stt_enabled": True, "stt_provider": "local",
        "stt_model": current["model"], "stt_language": "",
    })
    assert service._get_whisper() is not None
    assert seen == ["base"]
    # Same model → no reload
    assert service._get_whisper() is not None
    assert seen == ["base"]
    # Changed model → reload instead of silently reusing old singleton
    current["model"] = "small"
    assert service._get_whisper() is not None
    assert seen == ["base", "small"]
    assert service._loaded_model_size == "small"


def test_invalidate_model_clears_cache(monkeypatch):
    seen = []
    _install_fake_whisper(monkeypatch, seen)
    service = STTService()
    monkeypatch.setattr(service, "_load_settings", lambda: {
        "stt_enabled": True, "stt_provider": "local",
        "stt_model": "base", "stt_language": "",
    })
    service._get_whisper()
    assert service._whisper_model is not None
    service.invalidate_model()
    assert service._whisper_model is None
    assert service._loaded_model_size is None


def test_invalid_model_does_not_crash(monkeypatch):
    service = STTService()
    monkeypatch.setattr(service, "_load_settings", lambda: {
        "stt_enabled": True, "stt_provider": "local",
        "stt_model": "../evil", "stt_language": "",
    })
    assert service._get_whisper() is None


# ── transcribe compat ──

def test_transcribe_keeps_text_only_compat(monkeypatch):
    service = STTService()
    monkeypatch.setattr(service, "_load_settings", lambda: {
        "stt_enabled": True, "stt_provider": "local",
        "stt_model": "base", "stt_language": "sk",
    })
    monkeypatch.setattr(
        service, "_transcribe_local_with_info",
        lambda audio, lang="", hint="": ("raw transcript", "sk"),
    )
    assert service.transcribe(b"audio") == "raw transcript"
    assert service.transcribe_with_info(b"audio") == ("raw transcript", "sk")


def test_malformed_language_falls_back_to_autodetect(monkeypatch):
    captured = {}

    class MockModel:
        def transcribe(self, path, **kwargs):
            captured.update(kwargs)
            class _Seg:
                text = "ahoj"
            class _Info:
                language = "sk"
                language_probability = 0.8
            return iter([_Seg()]), _Info()

    service = STTService()
    service._get_whisper = lambda: MockModel()
    text, detected = service._transcribe_local_with_info(b"bytes", "../../", "a.m4a")
    assert text == "ahoj"
    assert detected == "sk"
    assert "language" not in captured


def test_local_transcribe_with_info_cleans_temp_file():
    service = STTService()

    class MockWhisper:
        def transcribe(self, *args, **kwargs):
            raise ValueError("boom")

    service._get_whisper = lambda: MockWhisper()
    temp_dir = tempfile.gettempdir()
    before = {f for f in os.listdir(temp_dir) if f.endswith((".webm", ".m4a"))}
    assert service._transcribe_local_with_info(b"dummy", "", "talk.m4a") is None
    after = {f for f in os.listdir(temp_dir) if f.endswith((".webm", ".m4a"))}
    assert (after - before) == set()


# ── settings validation ──

def test_stt_settings_validators():
    from routes.auth_routes import (
        _validate_stt_provider, _validate_stt_model, _validate_stt_language,
    )
    from fastapi import HTTPException

    assert _validate_stt_provider("local") == "local"
    assert _validate_stt_provider("endpoint:abc") == "endpoint:abc"
    with pytest.raises(HTTPException):
        _validate_stt_provider("endpoint:")
    with pytest.raises(HTTPException):
        _validate_stt_provider("robot")
    assert _validate_stt_model("base") == "base"
    with pytest.raises(HTTPException):
        _validate_stt_model("../evil")
    assert _validate_stt_language("") == ""
    assert _validate_stt_language("SK") == "sk"
    with pytest.raises(HTTPException):
        _validate_stt_language("xx!")


# ── /api/stt/transcribe-upload route ──

class _FakeUploadHandler:
    def __init__(self, info=None):
        self._info = info
        self.seen = {}

    def validate_upload_id(self, file_id):
        return isinstance(file_id, str) and len(file_id) == 32 and all(
            c in "0123456789abcdefABCDEF" for c in file_id
        )

    def is_audio_file(self, filename, mime=None):
        low = (filename or "").lower()
        return low.endswith((".webm", ".wav", ".mp3", ".m4a", ".ogg"))

    def resolve_upload(self, upload_id, owner=None, auth_manager=None, allow_admin=True):
        self.seen["owner"] = owner
        return self._info


class _FakeSTT:
    def __init__(self, available=True, result=("raw transcript", "sk")):
        self.available = available
        self._result = result

    def transcribe_with_info(self, audio_bytes, filename_hint=""):
        return self._result

    def get_stats(self):
        return {"available": self.available, "provider": "local"}


class _Req:
    def __init__(self, body=None, user="alice", auth_manager=None):
        self.state = SimpleNamespace(current_user=user, api_token=False, api_token_owner=None)
        self.app = SimpleNamespace(state=SimpleNamespace(auth_manager=auth_manager))
        self._body = body

    async def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


def _endpoints(stt, uploads, monkeypatch=None):
    if monkeypatch is not None:
        import fastapi.dependencies.utils as dependency_utils
        monkeypatch.setattr(dependency_utils, "ensure_multipart_is_installed", lambda: None)
    from routes.stt_routes import setup_stt_routes
    router = setup_stt_routes(stt, uploads)
    return {r.endpoint.__name__: r.endpoint for r in router.routes}


def _audio_info(tmp_path, name="talk.m4a", data=b"RIFFfakeaudio"):
    p = tmp_path / name
    p.write_bytes(data)
    return {
        "id": "a" * 32, "path": str(p), "mime": "audio/mp4",
        "name": name, "size": len(data),
    }


async def _call(coro):
    return await coro


def test_transcribe_upload_success_and_ownership(tmp_path, monkeypatch):
    import asyncio
    info = _audio_info(tmp_path)
    uploads = _FakeUploadHandler(info)
    eps = _endpoints(_FakeSTT(), uploads, monkeypatch)
    out = asyncio.run(_call(eps["transcribe_upload"](_Req({"file_id": "a" * 32}))))
    assert out["text"] == "raw transcript"
    assert out["language"] == "sk"
    assert out["file_id"] == "a" * 32
    assert out["file_name"] == "talk.m4a"
    assert uploads.seen["owner"] == "alice"


def test_transcribe_upload_rejects_bad_input(tmp_path, monkeypatch):
    import asyncio
    from fastapi import HTTPException
    info = _audio_info(tmp_path)
    eps = _endpoints(_FakeSTT(), _FakeUploadHandler(info), monkeypatch)
    with pytest.raises(HTTPException) as e:
        asyncio.run(_call(eps["transcribe_upload"](_Req({}))))
    assert e.value.status_code == 400
    with pytest.raises(HTTPException) as e:
        asyncio.run(_call(eps["transcribe_upload"](_Req({"file_id": "nope"}))))
    assert e.value.status_code == 400


def test_transcribe_upload_404_when_not_owner(tmp_path, monkeypatch):
    import asyncio
    from fastapi import HTTPException
    eps = _endpoints(_FakeSTT(), _FakeUploadHandler(None), monkeypatch)
    with pytest.raises(HTTPException) as e:
        asyncio.run(_call(eps["transcribe_upload"](_Req({"file_id": "b" * 32}))))
    assert e.value.status_code == 404


def test_transcribe_upload_rejects_non_audio(tmp_path, monkeypatch):
    import asyncio
    from fastapi import HTTPException
    info = _audio_info(tmp_path, name="notes.txt", data=b"hello")
    eps = _endpoints(_FakeSTT(), _FakeUploadHandler(info), monkeypatch)
    with pytest.raises(HTTPException) as e:
        asyncio.run(_call(eps["transcribe_upload"](_Req({"file_id": "c" * 32}))))
    assert e.value.status_code == 400


def test_transcribe_upload_503_when_disabled(monkeypatch):
    import asyncio
    from fastapi import HTTPException
    eps = _endpoints(_FakeSTT(available=False), _FakeUploadHandler({"id": "x"}), monkeypatch)
    with pytest.raises(HTTPException) as e:
        asyncio.run(_call(eps["transcribe_upload"](_Req({"file_id": "d" * 32}))))
    assert e.value.status_code == 503


def test_transcribe_upload_500_when_backend_fails(tmp_path, monkeypatch):
    import asyncio
    from fastapi import HTTPException
    info = _audio_info(tmp_path)
    eps = _endpoints(_FakeSTT(result=None), _FakeUploadHandler(info), monkeypatch)
    with pytest.raises(HTTPException) as e:
        asyncio.run(_call(eps["transcribe_upload"](_Req({"file_id": "e" * 32}))))
    assert e.value.status_code == 500


def test_transcribe_upload_413_when_over_limit(tmp_path, monkeypatch):
    import asyncio
    from fastapi import HTTPException
    import routes.stt_routes as stt_routes
    info = _audio_info(tmp_path, data=b"12345")
    eps = _endpoints(_FakeSTT(), _FakeUploadHandler(info), monkeypatch)
    monkeypatch.setattr(stt_routes, "STT_MAX_AUDIO_BYTES", 4)
    with pytest.raises(HTTPException) as e:
        asyncio.run(_call(eps["transcribe_upload"](_Req({"file_id": "f" * 32}))))
    assert e.value.status_code == 413


def test_transcribe_route_returns_additive_language(monkeypatch):
    import asyncio
    import fastapi.dependencies.utils as dependency_utils
    monkeypatch.setattr(dependency_utils, "ensure_multipart_is_installed", lambda: None)
    from routes.stt_routes import setup_stt_routes

    class _Svc:
        available = True

        def transcribe_with_info(self, audio_bytes, filename_hint=""):
            assert audio_bytes == b"abc"
            return ("hello", "cs")

    router = setup_stt_routes(_Svc(), None)
    eps = {r.endpoint.__name__: r.endpoint for r in router.routes}
    up = UploadFile(filename="a.webm", file=io.BytesIO(b"abc"))
    out = asyncio.run(eps["transcribe_audio"](up))
    # Back-compat: text key preserved, language additive
    assert out["text"] == "hello"
    assert out["language"] == "cs"


def test_stt_unavailable_hint_names_the_cause():
    from routes.stt_routes import _stt_unavailable_hint

    class _Svc:
        def __init__(self, settings, importable=True):
            self._s = settings
            self._importable = importable

        def _load_settings(self):
            return self._s

        def _is_faster_whisper_importable(self):
            return self._importable

    assert "disabled" in _stt_unavailable_hint(
        _Svc({"stt_enabled": False, "stt_provider": "local"})).lower()
    assert "faster-whisper" in _stt_unavailable_hint(
        _Svc({"stt_enabled": True, "stt_provider": "local"}, importable=False))
    assert "logs" in _stt_unavailable_hint(
        _Svc({"stt_enabled": True, "stt_provider": "local"}, importable=True))
    # Mocks without settings helpers still get a generic message, never a crash.
    assert _stt_unavailable_hint(object())


def test_suffix_for_filename_covers_extended_audio_set():
    for name, ext in (("a.opus", ".opus"), ("a.flac", ".flac"),
                      ("a.aac", ".aac"), ("a.oga", ".oga")):
        assert STTService.suffix_for_filename(name) == ext
