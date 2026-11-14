#!/usr/bin/env python3
"""Extract audio features (BPM, key, energy, valence/arousal) per track.

Writes data/music_features.jsonl incrementally. Features are optional: the
player works without this file, and smart playlists/radio simply fall back to
the tracks that have no features.

Usage:
  python scripts/music_features.py [--limit N] [--jobs J] [--force] [--root PATH]

The analyzer uses `sonoglyph` (https://pypi.org/project/sonoglyph/), which is
not a runtime dependency of Odysseus. Install it in a separate virtualenv:

  python3 -m venv .venv-music-features
  . .venv-music-features/bin/activate
  pip install sonoglyph
  python scripts/music_features.py --root "$ODYSSEUS_MUSIC_ROOT" --jobs 4
"""
import argparse
import json
import multiprocessing as mp
import os
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
EXTS = {".flac", ".mp3", ".m4a", ".opus", ".ogg", ".wav", ".aiff", ".alac"}


def _data_dir() -> Path:
    override = os.environ.get("ODYSSEUS_DATA_DIR")
    if override:
        return Path(override).expanduser()
    try:
        from src.constants import DATA_DIR

        return Path(DATA_DIR)
    except Exception:
        return REPO_ROOT / "data"


ROOT = Path(os.environ.get("ODYSSEUS_MUSIC_ROOT") or (_data_dir() / "music")).expanduser()
OUT = _data_dir() / "music_features.jsonl"


def analyze_one(task):
    path, rel = task
    try:
        import sonoglyph

        r = sonoglyph.analyze(str(path))
        meta = r.metadata
        return {
            "path": rel,
            "mtime": path.stat().st_mtime,
            "size": path.stat().st_size,
            "duration": float(getattr(meta, "duration_sec", 0) or 0),
            "bpm": round(float(r.tempo.bpm), 2),
            "key": getattr(r.key, "name", ""),
            "tonic": getattr(r.key, "tonic", ""),
            "mode": getattr(r.key, "mode", ""),
            "key_confidence": round(float(getattr(r.key, "confidence", 0) or 0), 3),
            "energy": round(float(r.energy.rms_mean), 4),
            "dynamic_range_db": round(float(getattr(r.energy, "dynamic_range_db", 0) or 0), 2),
            "valence": round(float(r.mood.valence), 3),
            "arousal": round(float(r.mood.arousal), 3),
            "analyzed_at": time.time(),
        }
    except Exception as exc:
        return {
            "path": rel,
            "error": f"{type(exc).__name__}: {exc}",
            "mtime": path.stat().st_mtime,
            "size": path.stat().st_size,
        }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--jobs", type=int, default=min(4, os.cpu_count() or 4))
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--root", default=str(ROOT))
    a = ap.parse_args()
    root = Path(a.root)
    cached = {}
    if OUT.exists() and not a.force:
        for line in OUT.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
                cached[row["path"]] = row
            except Exception:
                pass
    todo = []
    for p in root.rglob("*"):
        if not p.is_file() or p.suffix.lower() not in EXTS:
            continue
        rel = str(p.relative_to(root))
        row = cached.get(rel)
        try:
            st = p.stat()
        except OSError:
            continue
        if row and row.get("mtime") == st.st_mtime and row.get("size") == st.st_size and "error" not in row:
            continue
        todo.append((p, rel))
    if a.limit:
        todo = todo[: a.limit]
    print(f"a analizar: {len(todo)} (cacheadas: {len(cached)})", flush=True)
    results = []
    ctx = mp.get_context("spawn")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with ProcessPoolExecutor(max_workers=max(1, a.jobs), mp_context=ctx) as ex:
        for i, row in enumerate(ex.map(analyze_one, todo, chunksize=4), 1):
            cached[row["path"]] = row
            results.append(row)
            if i % 25 == 0:
                with OUT.open("w", encoding="utf-8") as fh:
                    for rel in sorted(cached):
                        fh.write(json.dumps(cached[rel], ensure_ascii=False) + "\n")
                print(f"  {i}/{len(todo)}", flush=True)
    with OUT.open("w", encoding="utf-8") as fh:
        for rel in sorted(cached):
            fh.write(json.dumps(cached[rel], ensure_ascii=False) + "\n")
    ok = sum(1 for r in results if "error" not in r)
    print(f"listo: {len(results)} analizadas, {ok} ok, {len(results)-ok} error; total cache {len(cached)}")


if __name__ == "__main__":
    main()
