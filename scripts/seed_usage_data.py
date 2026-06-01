#!/usr/bin/env python3
"""Seed local usage analytics data for dashboard demos.

This writes synthetic sessions and chat_messages into data/app.db. Rows created
by this script use a distinct ID prefix so they can be reset without touching
real chats or older QA seed data.

Usage:
    python scripts/seed_usage_data.py
    python scripts/seed_usage_data.py --reset
    python scripts/seed_usage_data.py --days 120 --sessions-per-owner 18
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sqlite3
from datetime import date, datetime, time, timedelta
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = REPO_ROOT / "data" / "app.db"
SEED_PREFIX = "usage-seed-ext"
SEED_NAME = "usage-seed-ext"
DEFAULT_OWNERS = ["zapulam", "test", "brother"]
DEFAULT_MODELS = [
    "gpt-5.5-extra-high",
    "claude-opus-4-8-thinking-high",
    "composer-2.5",
    "claude-4.6-sonnet-thinking",
    "gemini-2.5-pro",
    "deepseek-v3",
    "composer-2.5-fast",
]
ENDPOINT_URL = "https://api.openai.com/v1/chat/completions"
FOLDER = "Usage QA Seed"

TOPICS = [
    "usage dashboard validation",
    "local model routing",
    "calendar assistant polish",
    "email triage workflow",
    "document summarization",
    "tool-call reliability",
    "gallery metadata cleanup",
    "agent memory review",
    "scheduled task audit",
    "chat renderer QA",
]


def _csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _dt(value: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(value, time(hour=hour, minute=minute))


def _fmt(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S.%f")


def _session_dates(*, end: date, days: int, count: int, owner_index: int) -> list[date]:
    """Evenly spread sessions across the date window, with a per-owner offset."""
    if count <= 1:
        return [end - timedelta(days=max(days - 1, 0))]

    start = end - timedelta(days=max(days - 1, 0))
    span = max(days - 1, 0)
    dates: list[date] = []
    for index in range(count):
        day_offset = round(index * span / (count - 1))
        # Stagger owners by a couple of days while keeping dates in bounds.
        stagger = (owner_index * 2 + index % 3) % 5
        seeded_day = min(start + timedelta(days=day_offset + stagger), end)
        dates.append(seeded_day)
    return dates


def _turn_count(owner_index: int, session_index: int) -> int:
    return 4 + ((owner_index + session_index) % 5)


def _token_counts(owner_index: int, session_index: int, turn_index: int) -> tuple[int, int]:
    base = 1200 + owner_index * 140 + session_index * 35
    input_tokens = base + turn_index * 115 + (session_index % 4) * 45
    output_tokens = 620 + owner_index * 80 + turn_index * 75 + (session_index % 5) * 30
    return input_tokens, output_tokens


def _model_for(models: list[str], owner_index: int, session_index: int) -> str:
    # Owner offset makes every model appear across early/middle/late windows.
    return models[(session_index + owner_index * 3) % len(models)]


def _session_hour(owner_index: int, session_index: int) -> int:
    return 9 + ((owner_index * 2 + session_index) % 8)


def build_dataset(
    *,
    days: int,
    end: date,
    owners: list[str],
    models: list[str],
    sessions_per_owner: int,
) -> tuple[list[dict], list[dict]]:
    sessions: list[dict] = []
    messages: list[dict] = []
    rng = random.Random(522)

    for owner_index, owner in enumerate(owners):
        for session_index, session_day in enumerate(
            _session_dates(end=end, days=days, count=sessions_per_owner, owner_index=owner_index)
        ):
            model = _model_for(models, owner_index, session_index)
            topic = TOPICS[(session_index + owner_index) % len(TOPICS)]
            session_id = f"{SEED_PREFIX}-{owner}-{session_index + 1:02d}"
            started_at = _dt(session_day, _session_hour(owner_index, session_index), (owner_index * 7) % 45)
            turns = _turn_count(owner_index, session_index)
            total_input = 0
            total_output = 0
            last_message_at = started_at

            for turn_index in range(turns):
                user_ts = started_at + timedelta(minutes=turn_index * 11)
                input_tokens, output_tokens = _token_counts(owner_index, session_index, turn_index)
                assistant_ts = user_ts + timedelta(minutes=3 + (turn_index % 3))
                user_id = f"{session_id}-t{turn_index + 1:02d}-user"
                assistant_id = f"{session_id}-t{turn_index + 1:02d}-assistant"

                messages.append(
                    {
                        "id": user_id,
                        "session_id": session_id,
                        "role": "user",
                        "content": (
                            f"Synthetic prompt {turn_index + 1} for {owner} about {topic} "
                            f"on {session_day.isoformat()}."
                        ),
                        "metadata": None,
                        "timestamp": _fmt(user_ts),
                    }
                )
                messages.append(
                    {
                        "id": assistant_id,
                        "session_id": session_id,
                        "role": "assistant",
                        "content": (
                            "Synthetic assistant response for Usage dashboard validation. "
                            f"Model: {model}. Topic: {topic}."
                        ),
                        "metadata": json.dumps(
                            {
                                "input_tokens": input_tokens,
                                "output_tokens": output_tokens,
                                "total_tokens": input_tokens + output_tokens,
                                "usage_source": "real",
                                "model": model,
                                "seed": SEED_NAME,
                            },
                            separators=(",", ":"),
                        ),
                        "timestamp": _fmt(assistant_ts),
                    }
                )
                total_input += input_tokens
                total_output += output_tokens
                last_message_at = assistant_ts

            last_accessed = last_message_at + timedelta(minutes=rng.randint(5, 90))
            sessions.append(
                {
                    "id": session_id,
                    "name": f"Usage History {session_index + 1} - {owner}",
                    "endpoint_url": ENDPOINT_URL,
                    "model": model,
                    "owner": owner,
                    "rag": 0,
                    "archived": 0,
                    "folder": FOLDER,
                    "headers": "{}",
                    "last_accessed": _fmt(last_accessed),
                    "last_message_at": _fmt(last_message_at),
                    "is_important": 0,
                    "message_count": turns * 2,
                    "total_input_tokens": total_input,
                    "total_output_tokens": total_output,
                    "mode": "chat",
                    "crew_member_id": None,
                    "created_at": _fmt(started_at),
                    "updated_at": _fmt(last_accessed),
                }
            )

    return sessions, messages


def reset_seed(con: sqlite3.Connection) -> tuple[int, int]:
    session_ids = [row[0] for row in con.execute("SELECT id FROM sessions WHERE id LIKE ?", (f"{SEED_PREFIX}-%",))]
    if not session_ids:
        return 0, 0

    placeholders = ",".join("?" for _ in session_ids)
    message_count = con.execute(
        f"SELECT COUNT(*) FROM chat_messages WHERE session_id IN ({placeholders})",
        session_ids,
    ).fetchone()[0]
    con.execute(f"DELETE FROM chat_messages WHERE session_id IN ({placeholders})", session_ids)
    con.execute(f"DELETE FROM sessions WHERE id IN ({placeholders})", session_ids)
    return len(session_ids), int(message_count or 0)


def insert_dataset(con: sqlite3.Connection, sessions: list[dict], messages: list[dict]) -> None:
    con.executemany(
        """
        INSERT OR REPLACE INTO sessions (
            id, name, endpoint_url, model, owner, rag, archived, folder, headers,
            last_accessed, last_message_at, is_important, message_count,
            total_input_tokens, total_output_tokens, mode, crew_member_id,
            created_at, updated_at
        ) VALUES (
            :id, :name, :endpoint_url, :model, :owner, :rag, :archived, :folder, :headers,
            :last_accessed, :last_message_at, :is_important, :message_count,
            :total_input_tokens, :total_output_tokens, :mode, :crew_member_id,
            :created_at, :updated_at
        )
        """,
        sessions,
    )
    con.executemany(
        """
        INSERT OR REPLACE INTO chat_messages (
            id, session_id, role, content, metadata, timestamp
        ) VALUES (
            :id, :session_id, :role, :content, :metadata, :timestamp
        )
        """,
        messages,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed synthetic usage history into data/app.db")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="SQLite database path")
    parser.add_argument("--days", type=int, default=90, help="Number of days to cover, ending at --end")
    parser.add_argument("--end", type=date.fromisoformat, default=date.today(), help="End date, YYYY-MM-DD")
    parser.add_argument("--owners", type=_csv, default=DEFAULT_OWNERS, help="Comma-separated owners")
    parser.add_argument("--models", type=_csv, default=DEFAULT_MODELS, help="Comma-separated model names")
    parser.add_argument(
        "--sessions-per-owner",
        type=int,
        default=None,
        help="Sessions per owner; default is roughly one session per week",
    )
    parser.add_argument("--reset", action="store_true", help="Delete this script's prior seed rows first")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.days < 1:
        raise SystemExit("--days must be at least 1")
    if not args.owners:
        raise SystemExit("--owners must include at least one owner")
    if not args.models:
        raise SystemExit("--models must include at least one model")

    sessions_per_owner = args.sessions_per_owner or math.ceil(args.days / 7)
    if sessions_per_owner < 1:
        raise SystemExit("--sessions-per-owner must be at least 1")
    if not args.db.exists():
        raise SystemExit(f"database not found: {args.db}")

    sessions, messages = build_dataset(
        days=args.days,
        end=args.end,
        owners=args.owners,
        models=args.models,
        sessions_per_owner=sessions_per_owner,
    )

    con = sqlite3.connect(str(args.db))
    try:
        removed_sessions = 0
        removed_messages = 0
        with con:
            if args.reset:
                removed_sessions, removed_messages = reset_seed(con)
            insert_dataset(con, sessions, messages)
        if args.reset:
            print(f"removed {removed_sessions} session(s), {removed_messages} message(s) for prefix {SEED_PREFIX}-")
        print(
            f"seeded {len(sessions)} session(s), {len(messages)} message(s) "
            f"from {(args.end - timedelta(days=args.days - 1)).isoformat()} to {args.end.isoformat()} "
            f"using {len(args.models)} model(s)"
        )
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
