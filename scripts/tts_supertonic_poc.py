#!/usr/bin/env python3
"""PoC: synthesize one line with Supertonic-3 via fugassa_supertonic."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from services.tts.supertonic_pipeline import find_supertonic_model_dir
from services.tts import fugassa_supertonic as fugassa_tts


def main() -> int:
    parser = argparse.ArgumentParser(description="Fugassa Supertonic-3 TTS PoC")
    parser.add_argument("--text", default="Krátký test syntézy hlasu.")
    parser.add_argument("--lang", default="cs", choices=["en", "cs", "uk"])
    parser.add_argument("--speaker", type=int, default=0)
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("-o", "--output", default="supertonic_poc.wav")
    args = parser.parse_args()

    model_dir = find_supertonic_model_dir()
    if model_dir is None:
        print("Supertonic model not found — install via Model Hub to DATA_DIR/tts_models/", file=sys.stderr)
        return 1

    if not fugassa_tts.supertonic_available():
        print("sherpa-onnx or model load failed", file=sys.stderr)
        return 2

    audio = fugassa_tts.synthesize_supertonic(
        args.text,
        lang=args.lang,
        speaker_id=args.speaker,
        speed=args.speed,
        use_cache=False,
    )
    if not audio:
        print("Synthesis returned no audio", file=sys.stderr)
        return 3

    out = Path(args.output)
    out.write_bytes(audio)
    print(f"Wrote {len(audio)} bytes to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
