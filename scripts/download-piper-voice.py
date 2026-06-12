#!/usr/bin/env python3
"""Download Piper TTS voices into data/piper_voices/.

Usage:
    python scripts/download-piper-voice.py                       # default English voice
    python scripts/download-piper-voice.py en_GB-alba-medium     # specific voice(s)

Voice ids are the standard Piper names (<lang>_<REGION>-<name>-<quality>).
Browse samples at https://rhasspy.github.io/piper-samples/
Requires: pip install piper-tts
"""

import sys
import subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.constants import PIPER_VOICES_DIR

DEFAULT_VOICE = "en_US-lessac-low"


def main():
    voices = sys.argv[1:] or [DEFAULT_VOICE]
    dest = Path(PIPER_VOICES_DIR)
    dest.mkdir(parents=True, exist_ok=True)

    try:
        import piper.download_voices  # noqa: F401
    except ImportError:
        print("piper-tts is not installed. Run: pip install piper-tts")
        return 1

    rc = subprocess.call(
        [sys.executable, "-m", "piper.download_voices", *voices, "--data-dir", str(dest)]
    )
    if rc == 0:
        print(f"Done. Voices are in {dest}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
