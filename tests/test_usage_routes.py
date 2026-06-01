from datetime import date, datetime

from routes.usage_routes import (
    ALL_USERS,
    _aggregate_usage_rows,
    _parse_message_metrics,
    _resolve_owner_scope,
)


def test_parse_message_metrics_ignores_missing_or_zero_tokens():
    assert _parse_message_metrics(None) is None
    assert _parse_message_metrics("{}") is None
    assert _parse_message_metrics('{"input_tokens": 0, "output_tokens": 0}') is None


def test_aggregate_usage_rows_buckets_stacked_tokens_by_local_day():
    rows = [
        (datetime(2026, 6, 1, 4, 30), '{"input_tokens": 100, "output_tokens": 25, "usage_source": "real", "model": "gpt-4o"}', "alice", "assistant"),
        (datetime(2026, 6, 1, 18, 0), '{"input_tokens": 50, "output_tokens": 75, "usage_source": "estimated", "model": "claude-3-5-sonnet"}', "alice", "assistant"),
        (datetime(2026, 6, 2, 2, 0), '{"input_tokens": 10, "output_tokens": 5, "usage_source": "real", "model": "gpt-4o"}', "bob", "assistant"),
        (datetime(2026, 6, 2, 3, 0), '{"not_tokens": true}', "bob", "assistant"),
        (datetime(2026, 6, 2, 4, 0), '{"input_tokens": 999, "output_tokens": 999}', "bob", "user"),
    ]

    result = _aggregate_usage_rows(
        rows,
        start=date(2026, 5, 31),
        end=date(2026, 6, 1),
        tz_offset_minutes=300,
    )

    by_day = {row["date"]: row for row in result["daily"]}
    assert by_day["2026-05-31"]["input_tokens"] == 100
    assert by_day["2026-05-31"]["output_tokens"] == 25
    assert by_day["2026-05-31"]["message_count"] == 1
    assert by_day["2026-06-01"]["input_tokens"] == 60
    assert by_day["2026-06-01"]["output_tokens"] == 80
    assert by_day["2026-06-01"]["message_count"] == 4
    assert result["totals"]["total_tokens"] == 265
    assert result["totals"]["message_count"] == 5
    by_user_daily = {row["user"]: row["daily"] for row in result["daily_by_user"]}
    alice_daily = {row["date"]: row for row in by_user_daily["alice"]}
    bob_daily = {row["date"]: row for row in by_user_daily["bob"]}
    assert alice_daily["2026-05-31"]["total_tokens"] == 125
    assert alice_daily["2026-06-01"]["total_tokens"] == 125
    assert bob_daily["2026-06-01"]["total_tokens"] == 15
    assert bob_daily["2026-06-01"]["message_count"] == 3
    assert result["models"] == [
        {"model": "gpt-4o", "total_tokens": 140},
        {"model": "claude-3-5-sonnet", "total_tokens": 125},
    ]
    by_model_daily = {row["model"]: row["daily"] for row in result["daily_by_model"]}
    gpt_daily = {row["date"]: row for row in by_model_daily["gpt-4o"]}
    claude_daily = {row["date"]: row for row in by_model_daily["claude-3-5-sonnet"]}
    assert gpt_daily["2026-05-31"]["total_tokens"] == 125
    assert gpt_daily["2026-06-01"]["total_tokens"] == 15
    assert claude_daily["2026-06-01"]["input_tokens"] == 50
    assert claude_daily["2026-06-01"]["output_tokens"] == 75


def test_resolve_owner_scope_admin_all_users():
    owner_scope, selected_user = _resolve_owner_scope("admin", True, ALL_USERS)
    assert owner_scope is None
    assert selected_user == ALL_USERS


def test_resolve_owner_scope_admin_single_user():
    owner_scope, selected_user = _resolve_owner_scope("admin", True, "alice")
    assert owner_scope == "alice"
    assert selected_user == "alice"


def test_resolve_owner_scope_non_admin_forces_own_user():
    owner_scope, selected_user = _resolve_owner_scope("alice", False, "bob")
    assert owner_scope == "alice"
    assert selected_user == "alice"
