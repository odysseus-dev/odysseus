#!/usr/bin/env python3
"""Persistent Faster Whisper worker for local speech-to-text.

The worker speaks newline-delimited JSON on stdin/stdout. It loads the model
once, then keeps it warm between requests. It never opens a network port and
never writes audio or transcripts to disk.
"""

import json
import os
import sys

from faster_whisper import WhisperModel


MODEL_PATH = os.environ.get("ODYSSEUS_FW_MODEL", "/opt/faster-whisper/tiny")
CPU_THREADS = int(os.environ.get("ODYSSEUS_FW_CPU_THREADS", "4"))


def _send(payload):
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main():
    model = WhisperModel(
        MODEL_PATH,
        device="cpu",
        compute_type="int8",
        cpu_threads=CPU_THREADS,
        local_files_only=True,
    )
    import faster_whisper

    _send({"ready": True, "model": MODEL_PATH, "version": faster_whisper.__version__})

    for line in sys.stdin:
        request_id = None
        try:
            request = json.loads(line)
            request_id = request.get("id")
            audio_path = request["path"]
            if not isinstance(audio_path, str) or not os.path.isfile(audio_path):
                raise ValueError("audio path is not a readable file")

            language = request.get("language") or None
            if language is not None and not isinstance(language, str):
                raise ValueError("language must be a string")

            segments, info = model.transcribe(
                audio_path,
                language=language,
                beam_size=1,
                best_of=1,
                temperature=0.0,
                vad_filter=False,
                condition_on_previous_text=False,
                without_timestamps=True,
                max_initial_timestamp=0.0,
            )
            text = " ".join(segment.text.strip() for segment in segments).strip()

            _send({
                "id": request_id,
                "ok": True,
                "text": text,
                "language": info.language,
                "language_probability": float(info.language_probability),
            })
        except Exception as exc:
            _send({
                "id": request_id,
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
            })


if __name__ == "__main__":
    main()
