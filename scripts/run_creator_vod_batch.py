"""M10-governed local faster-whisper batch runner for creator clips and stream VODs."""

from __future__ import annotations

import os
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("HF_DATASETS_OFFLINE", "1")




import argparse
import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from faster_whisper import WhisperModel
from scripts.stream_pipeline import decode_audio_16k_mono, evaluate_workstation_state

ROLE_GROUPS = {
    "all": ("creator_video_clip", "stream_vod_recording"),
    "creator": ("creator_video_clip",),
    "vod": ("stream_vod_recording",),
    "dji": ("dji_camera_clip",),
}


def run_batch(limit: int = 10, role_filter: str = "all", model_size: str = "base.en", ignore_gpu_governor: bool = False, paired_only: bool = False) -> None:
    roles = ROLE_GROUPS[role_filter]
    placeholders = ", ".join("?" for _ in roles)
    paired_clause = "AND paired_audio_path IS NOT NULL AND TRIM(paired_audio_path) != ''" if paired_only else ""

    con = sqlite3.connect(ROOT / "data" / "stream_media_catalog.db")
    con.row_factory = sqlite3.Row

    fts_cols = [r["name"] for r in con.execute("PRAGMA table_info(transcript_segments_fts)").fetchall()]
    text_col = next((c for c in ("segment_text", "transcript_text", "content", "text", "body") if c in fts_cols), fts_cols[-1])
    has_time_cols = "start_sec" in fts_cols and "end_sec" in fts_cols

    rows = con.execute(
        f"""
        SELECT * FROM media_items
        WHERE transcript_status = 'pending'
          AND is_duplicate = 0
          AND media_role IN ({placeholders})
          {paired_clause}
        ORDER BY size_bytes ASC
        LIMIT ?
        """,
        (*roles, limit),
    ).fetchall()

    if not rows:
        print(f"No pending items found for role_filter={role_filter!r}.")
        con.close()
        return

    model: WhisperModel | None = None
    processed = 0
    deferred = 0

    for r in rows:
        gov = evaluate_workstation_state()
        blockers = list(gov.get("blockers") or [])
        if ignore_gpu_governor:
            blockers = [b for b in blockers if not (b.startswith("gpu_util_high") or b.startswith("vram_high"))]
        if blockers:
            print(f"[M10 Governor] Yielding batch -> blockers={blockers}")
            deferred += 1
            break

        src = Path(r["paired_audio_path"] or r["source_path"])
        if not src.exists():
            con.execute("UPDATE media_items SET transcript_status = 'missing_source' WHERE id = ?", (r["id"],))
            con.commit()
            print(f"  [MISSING] [{r['id']}] {r['filename']}")
            continue

        if model is None:
            model = WhisperModel(model_size, device="cpu", compute_type="int8")

        t0 = time.monotonic()
        try:
            audio_16k = decode_audio_16k_mono(src)
            if audio_16k.size == 0:
                con.execute("UPDATE media_items SET transcript_status = 'no_audio_stream' WHERE id = ?", (r["id"],))
                con.commit()
                print(f"  [NO AUDIO] [{r['id']}] {r['filename']}")
                continue

            segments_iter, info = model.transcribe(audio_16k, beam_size=1, vad_filter=True)
            segs = [
                {"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip()}
                for s in segments_iter
                if s.text.strip()
            ]
        except Exception as exc:
            con.execute("UPDATE media_items SET transcript_status = 'decode_error' WHERE id = ?", (r["id"],))
            con.commit()
            print(f"  [ERROR] [{r['id']}] {r['filename']}: {type(exc).__name__}")
            continue

        out_dir = ROOT / "data" / "stream_transcripts" / f"media_{r['id']}"
        out_dir.mkdir(parents=True, exist_ok=True)
        json_path = out_dir / f"{src.stem}.transcript.json"
        srt_path = out_dir / f"{src.stem}.srt"

        json_path.write_text(
            json.dumps(
                {
                    "media_id": r["id"],
                    "source_path": str(src),
                    "duration_sec": round(info.duration, 2),
                    "transcribed_at": datetime.now(timezone.utc).isoformat(),
                    "segments": segs,
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        srt_blocks = []
        for idx, s in enumerate(segs, 1):
            sh, sm, ss = int(s["start"] // 3600), int((s["start"] % 3600) // 60), s["start"] % 60
            eh, em, es = int(s["end"] // 3600), int((s["end"] % 3600) // 60), s["end"] % 60
            start_ts = f"{sh:02d}:{sm:02d}:{ss:06.3f}".replace(".", ",")
            end_ts = f"{eh:02d}:{em:02d}:{es:06.3f}".replace(".", ",")
            srt_blocks.append(f"{idx}\n{start_ts} --> {end_ts}\n{s['text']}\n")
        srt_path.write_text("\n".join(srt_blocks), encoding="utf-8")

        con.execute("DELETE FROM transcript_segments_fts WHERE media_id = ?", (str(r["id"]),))
        for s in segs:
            if has_time_cols:
                con.execute(
                    f"INSERT INTO transcript_segments_fts (media_id, filename, start_sec, end_sec, {text_col}) VALUES (?, ?, ?, ?, ?)",
                    (str(r["id"]), r["filename"], str(s["start"]), str(s["end"]), s["text"]),
                )
            else:
                con.execute(
                    f"INSERT INTO transcript_segments_fts (media_id, filename, {text_col}) VALUES (?, ?, ?)",
                    (str(r["id"]), r["filename"], s["text"]),
                )

        con.execute(
            """
            UPDATE media_items
            SET transcript_status = 'completed',
                duration_sec = ?,
                transcript_json_path = ?,
                transcript_srt_path = ?
            WHERE id = ?
            """,
            (round(info.duration, 2), str(json_path.resolve()), str(srt_path.resolve()), r["id"]),
        )
        con.commit()

        processed += 1
        preview = " ".join(s["text"] for s in segs)[:110]
        print(f"  [OK] [{r['id']}] {r['filename']} ({info.duration:.1f}s in {time.monotonic() - t0:.2f}s) -> {preview!r}")

    remaining = con.execute(
        f"""
        SELECT COUNT(*) FROM media_items
        WHERE transcript_status = 'pending'
          AND is_duplicate = 0
          AND media_role IN ({placeholders})
        """,
        roles,
    ).fetchone()[0]
    fts_total = con.execute("SELECT COUNT(*) FROM transcript_segments_fts").fetchone()[0]
    con.close()
    print(f"\n=== M9 CREATOR/VOD BATCH FINISHED | processed={processed} | deferred={deferred} | remaining={remaining} | total_fts_segments={fts_total} ===")


def main() -> None:
    parser = argparse.ArgumentParser(description="M10-governed Whisper batch transcription for creator clips and stream VODs.")
    parser.add_argument("--limit", type=int, default=10, help="Maximum number of pending items to process in this batch.")
    parser.add_argument("--role", choices=tuple(ROLE_GROUPS.keys()), default="all", help="Target role group: all, creator, or vod.")
    parser.add_argument("--model", default="base.en", help="faster-whisper model size (default: base.en).")
    parser.add_argument("--ignore-gpu-governor", action="store_true", help="Ignore GPU/VRAM blockers when running on CPU int8.")
    parser.add_argument("--paired-only", action="store_true", help="Only process items that have a paired_audio_path (.WAV mic track).")
    args = parser.parse_args()
    run_batch(limit=args.limit, role_filter=args.role, model_size=args.model, ignore_gpu_governor=args.ignore_gpu_governor, paired_only=args.paired_only)


if __name__ == "__main__":
    main()
