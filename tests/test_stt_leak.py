import os
import tempfile
from services.stt.stt_service import STTService


def test_stt_local_transcribe_leak_on_error():
    service = STTService()

    class MockWhisper:
        def transcribe(self, *args, **kwargs):
            raise ValueError("Simulated transcribe error")

    service._get_whisper = lambda: MockWhisper()

    # Track WebM files in the temp directory before running transcription
    temp_dir = tempfile.gettempdir()
    webm_before = {f for f in os.listdir(temp_dir) if f.endswith(".webm")}

    # Run transcription, which will raise ValueError internally
    result = service._transcribe_local(b"dummy_audio_data")

    # Track WebM files in the temp directory after running transcription
    webm_after = {f for f in os.listdir(temp_dir) if f.endswith(".webm")}

    # Assert that it returned None (failure)
    assert result is None

    # Assert that no new temp files were leaked
    leaked = webm_after - webm_before
    assert len(leaked) == 0, f"Leaked files: {leaked}"
