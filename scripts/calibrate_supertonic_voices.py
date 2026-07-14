#!/usr/bin/env python3
"""Generate Supertonic voice samples (sid 0–9) for listening tests / calibration."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from services.tts.supertonic_pipeline import find_supertonic_model_dir
from services.tts.tts_service import get_tts_service
from services.tts.voice_manifest import load_voice_manifest

_SAMPLES = {
    "cs": "Temná chodba se táhne před tebou.",
    "en": "A dark corridor stretches before you.",
    "uk": "Темний коридор тягнеться перед тобою.",
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Calibrate / sample Supertonic voices")
    parser.add_argument("-o", "--output", default="data/tts/voice_samples")
    parser.add_argument("--langs", default="cs,en,uk", help="Comma-separated langs")
    parser.add_argument("--write-manifest", action="store_true", help="No-op if manifest already calibrated")
    args = parser.parse_args()

    if find_supertonic_model_dir() is None:
        print("Supertonic model not found", file=sys.stderr)
        return 1

    out_root = Path(args.output)
    out_root.mkdir(parents=True, exist_ok=True)
    tts = get_tts_service()
    langs = [x.strip() for x in args.langs.split(",") if x.strip()]
    manifest = load_voice_manifest()
    report: list[dict] = []

    for entry in manifest.get("speakers") or []:
        sid = int(entry.get("id", -1))
        if sid < 0 or sid > 9:
            continue
        code = entry.get("code") or f"sid{sid}"
        for lang in langs:
            text = _SAMPLES.get(lang, _SAMPLES["cs"])
            audio = tts.synthesize_supertonic(text, lang=lang, speaker_id=sid, use_cache=False)
            if not audio:
                print(f"Failed sid={sid} lang={lang}", file=sys.stderr)
                return 2
            path = out_root / lang / f"{code}_sid{sid}.wav"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(audio)
            report.append({"speaker_id": sid, "code": code, "lang": lang, "path": str(path), "bytes": len(audio)})
            print(f"Wrote {path}")

    (out_root / "calibration_report.json").write_text(
        json.dumps({"samples": report, "manifest": manifest}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"Report: {out_root / 'calibration_report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
