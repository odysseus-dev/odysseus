import os
import tempfile

from services.stt.stt_service import STTService


def test_stt_local_transcribe_leak_on_error(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    service = STTService()

    def fake_request(path, language=""):
        raise ValueError("Simulated transcribe error")

    service._worker.request = fake_request

    # Track WebM files in the temp directory before running transcription
    temp_dir = tempfile.gettempdir()
    webm_before = {f for f in os.listdir(temp_dir) if f.endswith(".webm")}

    result = service._transcribe_local(b"dummy_audio_data")

    webm_after = {f for f in os.listdir(temp_dir) if f.endswith(".webm")}

    assert result is None
    leaked = webm_after - webm_before
    assert len(leaked) == 0, f"Leaked files: {leaked}"
