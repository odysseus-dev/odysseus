"""Copy a save and replay player turns with live LLM to verify C1–C4 end-to-end.

Typical use (Fugassa turn 16 on a disposable copy):

    python -m titan.fugassa.replay_playthrough_verify \\
        --source Fugassa --dest Fugassa_C4_replay --undo 1 --replay-from 16

Wizard input is not re-run — the copied save already reflects the wizard seed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
from typing import Any

from titan.fugassa import campaign_chronicle
from titan.fugassa.game_session import (
    GameSessionError,
    get_summary,
    load_game_state,
    run_interactive_turn_job,
    undo_last_turn,
)
from titan.fugassa.save_store import SaveStoreError, copy_save, delete_save, game_db_path, get_save


def extract_player_turns(save_id: str) -> list[str]:
    state = load_game_state(save_id)
    return [
        str(entry.get("content") or "")
        for entry in (state.get("chat_history") or [])
        if isinstance(entry, dict) and entry.get("role") == "user"
    ]


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def verify_turn_pipeline(
    save_id: str,
    db_path: str,
    turn_number: int,
    player_text: str,
) -> dict[str, Any]:
    """Assert C2 ordering + C4 GM-first delta for one completed turn."""
    checks: dict[str, Any] = {"turn": turn_number, "passed": True, "details": {}}

    conn = _connect(db_path)
    try:
        delta = conn.execute(
            """
            SELECT delta_text, player_excerpt
            FROM scene_turn_deltas
            WHERE turn_number = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (turn_number,),
        ).fetchone()
        if not delta:
            checks["passed"] = False
            checks["details"]["c4_delta"] = "missing scene_turn_deltas row"
        else:
            delta_text = str(delta["delta_text"] or "").strip()
            excerpt = str(delta["player_excerpt"] or "").strip()
            player_stripped = player_text.strip()
            gm_first = bool(delta_text) and delta_text != player_stripped
            if player_stripped and delta_text.startswith(player_stripped[:40]):
                gm_first = False
            checks["details"]["c4_delta"] = {
                "delta_text": delta_text[:180],
                "player_excerpt": excerpt[:120],
                "gm_first": gm_first,
            }
            if not gm_first:
                checks["passed"] = False

        events = conn.execute(
            """
            SELECT id, event_type, summary
            FROM event_log
            WHERE turn_id = ? AND is_active = 1
            ORDER BY id ASC
            """,
            (turn_number,),
        ).fetchall()
        checks["details"]["chronicle_events"] = [
            {"id": int(r["id"]), "type": r["event_type"], "summary": (r["summary"] or "")[:100]}
            for r in events
        ]

        quest_ids = [int(r["id"]) for r in events if r["event_type"] in ("quest_complete", "quest_progress", "quest_fail")]
        turn_event_ids = [int(r["id"]) for r in events if r["event_type"] == "turn"]
        if quest_ids and turn_event_ids and max(quest_ids) >= min(turn_event_ids):
            checks["passed"] = False
            checks["details"]["c2_order"] = "quest event id >= turn event id (pipeline order violation)"
        elif quest_ids and turn_event_ids:
            checks["details"]["c2_order"] = "ok (quest events precede turn event)"

        pipeline = campaign_chronicle.load_pipeline_turn(db_path)
        if pipeline:
            steps = [s.get("step") for s in pipeline]
            if steps:
                quest_idx = next((i for i, s in enumerate(steps) if s == "evaluate_quests_after_gm"), None)
                arch_idx = next((i for i, s in enumerate(steps) if s == "archivist"), None)
                if quest_idx is not None and arch_idx is not None and quest_idx > arch_idx:
                    checks["passed"] = False
                    checks["details"]["c2_pipeline"] = "evaluate_quests_after_gm after archivist in pipeline log"
                else:
                    checks["details"]["c2_pipeline"] = steps
    finally:
        conn.close()

    summary = get_summary(save_id)
    checks["details"]["summary"] = {
        "turn": (summary.get("campaign_state") or {}).get("turn"),
        "chronicle_count": len(summary.get("chronicle") or []),
        "pinned_facts": len(summary.get("pinned_facts") or []),
        "scene_summaries": len(summary.get("scene_summaries") or []),
    }
    return checks


def save_id_from_db(db_path: str) -> str:
    """Best-effort save folder name from campaign_settings."""
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT campaign_name FROM campaign_settings WHERE id = 1").fetchone()
        return str(row["campaign_name"]) if row else ""
    finally:
        conn.close()


async def replay_turns(
    save_id: str,
    *,
    turn_numbers: list[int],
    player_texts: list[str],
) -> list[dict[str, Any]]:
    db_path = game_db_path(save_id)
    results: list[dict[str, Any]] = []
    for turn_num in turn_numbers:
        if turn_num < 1 or turn_num > len(player_texts):
            raise ValueError(f"Turn {turn_num} out of range (1..{len(player_texts)})")
        player_text = player_texts[turn_num - 1]
        print(f"\n=== Replaying turn {turn_num} ({len(player_text)} chars) ===", flush=True)
        turn_result = await run_interactive_turn_job(
            save_id,
            db_path,
            owner=None,
            player_text=player_text,
        )
        state = load_game_state(save_id)
        actual_turn = int(state.get("turn") or 0)
        print(f"GM turn completed → save now at turn {actual_turn}", flush=True)
        print(f"Assistant excerpt: {str(turn_result.get('assistant_text') or '')[:200]}…", flush=True)
        verification = verify_turn_pipeline(save_id, db_path, actual_turn, player_text)
        verification["replay"] = {
            "requested_turn": turn_num,
            "actual_turn": actual_turn,
            "quest_side_effects": turn_result.get("quest"),
        }
        results.append(verification)
        status = "PASS" if verification["passed"] else "FAIL"
        print(f"Verification turn {actual_turn}: {status}", flush=True)
        print(json.dumps(verification["details"], indent=2, ensure_ascii=False), flush=True)
    return results


def prepare_copy(
    source_id: str,
    dest_id: str,
    *,
    overwrite: bool,
    undo_count: int,
) -> tuple[str, list[str]]:
    print(f"Copying {source_id!r} → {dest_id!r} (overwrite={overwrite})", flush=True)
    copy_save(source_id, dest_id, overwrite=overwrite)
    player_texts = extract_player_turns(source_id)
    state = load_game_state(dest_id)
    print(f"Copy at turn {state.get('turn')} with {len(player_texts)} player inputs", flush=True)

    for i in range(undo_count):
        before = int(load_game_state(dest_id).get("turn") or 0)
        try:
            undo_last_turn(dest_id)
        except GameSessionError as exc:
            if getattr(exc, "code", None) == "no_undo":
                print(
                    f"Undo stopped after {i}/{undo_count}: only one autosave_prev snapshot on copy",
                    flush=True,
                )
                break
            raise
        after = int(load_game_state(dest_id).get("turn") or 0)
        print(f"Undo {i + 1}/{undo_count}: turn {before} → {after}", flush=True)

    return dest_id, player_texts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Replay Fugassa turns on a save copy (live LLM).")
    parser.add_argument("--source", default="Fugassa", help="Source save id")
    parser.add_argument("--dest", default="Fugassa_C4_replay", help="Destination copy id")
    parser.add_argument("--overwrite", action="store_true", help="Replace dest if it exists")
    parser.add_argument("--undo", type=int, default=1, help="Undo N turns on the copy before replay")
    parser.add_argument("--replay-from", type=int, help="First turn to replay (1-indexed)")
    parser.add_argument("--replay-to", type=int, help="Last turn to replay (inclusive)")
    parser.add_argument("--keep", action="store_true", help="Keep dest save after run")
    parser.add_argument("--verify-only", action="store_true", help="Verify dest save without replay")
    parser.add_argument("--dry-run", action="store_true", help="Copy + undo only, no LLM replay")
    args = parser.parse_args(argv)

    if args.verify_only:
        try:
            get_save(args.dest)
        except SaveStoreError as exc:
            print(f"Save error: {exc}", file=sys.stderr)
            return 2
        dest_id = args.dest
        db_path = game_db_path(dest_id)
        current = int(load_game_state(dest_id).get("turn") or 0)
        if current < 1:
            print("Save has no completed turns to verify", file=sys.stderr)
            return 2
        source_texts = extract_player_turns(args.source)
        if current > len(source_texts):
            player_text = extract_player_turns(dest_id)[current - 1]
        else:
            player_text = source_texts[current - 1]
        print(f"Verifying {dest_id!r} turn {current} (no LLM replay)", flush=True)
        verification = verify_turn_pipeline(dest_id, db_path, current, player_text)
        print(json.dumps(verification, indent=2, ensure_ascii=False))
        return 0 if verification["passed"] else 1

    try:
        dest_id, player_texts = prepare_copy(
            args.source,
            args.dest,
            overwrite=args.overwrite,
            undo_count=max(0, args.undo),
        )
    except SaveStoreError as exc:
        print(f"Save error: {exc}", file=sys.stderr)
        return 2

    current_turn = int(load_game_state(dest_id).get("turn") or 0)
    replay_from = args.replay_from or (current_turn + 1)
    replay_to = args.replay_to or replay_from
    turn_numbers = list(range(replay_from, replay_to + 1))

    print(
        f"Will replay turns {turn_numbers} using player texts from {args.source!r}",
        flush=True,
    )
    for t in turn_numbers:
        preview = player_texts[t - 1][:100].replace("\n", " ")
        print(f"  turn {t}: {preview}…", flush=True)

    if args.dry_run:
        print("\nDry run — skipping LLM replay.", flush=True)
        if not args.keep:
            delete_save(dest_id)
            print(f"Removed {dest_id!r}", flush=True)
        return 0

    results = asyncio.run(
        replay_turns(dest_id, turn_numbers=turn_numbers, player_texts=player_texts)
    )
    all_pass = all(r["passed"] for r in results)
    print("\n=== SUMMARY ===", flush=True)
    print(json.dumps({"save": dest_id, "all_pass": all_pass, "turns": results}, indent=2, ensure_ascii=False))

    if not args.keep:
        delete_save(dest_id)
        print(f"\nRemoved disposable copy {dest_id!r}", flush=True)
    else:
        print(f"\nKept replay save at {dest_id!r}", flush=True)

    return 0 if all_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
