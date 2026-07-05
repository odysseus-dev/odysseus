#!/usr/bin/env python3
"""Pre-download STT/TTS models into the HuggingFace cache.

Run this at container startup or manually to eliminate first-use latency.
Models are cached in data/huggingface and persist across container rebuilds.
"""
import os
import sys


def prefetch_stt(model_size: str = "base") -> None:
    """Download faster-whisper model."""
    try:
        from faster_whisper import WhisperModel
        print(f"Downloading faster-whisper {model_size}...")
        WhisperModel(model_size, device="cpu", compute_type="int8")
        print(f"  done.")
    except ImportError:
        print("faster-whisper not installed, skipping STT prefetch.")
    except Exception as e:
        print(f"  STT download failed: {e}", file=sys.stderr)


def prefetch_tts() -> None:
    """Download Kokoro-82M pipeline."""
    try:
        from kokoro import KPipeline
        print("Downloading Kokoro-82M pipeline...")
        KPipeline(lang_code="a")
        print("  done.")
    except ImportError:
        print("kokoro not installed, skipping TTS prefetch.")
    except Exception as e:
        print(f"  TTS download failed: {e}", file=sys.stderr)


if __name__ == "__main__":
    model = os.environ.get("STT_MODEL", "base")
    prefetch_stt(model)
    prefetch_tts()
    print("Speech model prefetch complete.")
