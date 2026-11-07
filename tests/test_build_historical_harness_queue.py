import importlib.util
from pathlib import Path


PATH = Path(__file__).parents[1] / "scripts" / "build_historical_harness_queue.py"
SPEC = importlib.util.spec_from_file_location("historical_queue", PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_infer_family_handles_variants():
    assert MODULE.infer_family("[verify-agent-contract now] typo_notes-00") == "notes"
    assert MODULE.infer_family("[verify-agent-contract now] ambiguous_calendar-00") == "calendar"
    assert MODULE.infer_family("[verify-agent-contract now] search_ai-11") == "search_browser"
    assert MODULE.infer_family("[verify-agent-contract now] email_to_calendar_schedule-00") == "switching"
    assert MODULE.infer_family("[verify-agent-contract now] browser_ikea-11") == "search_browser"


def test_build_queue_deduplicates_and_keeps_newest_source():
    older = {
        "source_session_id": "old", "source_name": "[verify-agent-contract x] notes-00",
        "created_at": "1", "family": "notes",
        "turns": [{"user": "Show notes", "assistant": "old", "metadata": {}}],
    }
    newer = {
        **older, "source_session_id": "new", "created_at": "2",
        "turns": [{"user": "Show notes", "assistant": "new", "metadata": {}}],
    }
    queue = MODULE.build_queue([newer, older])
    assert queue["source_sessions"] == 2
    assert queue["unique_flows"] == 1
    assert queue["workstreams"]["replay_first"][0]["source_session_id"] == "new"
    assert queue["workstreams"]["replay_first"][0]["duplicate_runs"] == 2
    assert queue["source_user_turns"] == 2
    assert queue["seed_count"] == 2
    assert len(queue["seeds"]) == 2


def test_build_seeds_accounts_for_every_turn_and_keeps_context():
    sessions = [{
        "source_session_id": "s1", "source_name": "Calendar then notes",
        "created_at": "1", "family": "switching",
        "turns": [
            {"user": "Show my calendar", "family": "calendar", "metadata": {}},
            {"user": "Now my notes", "family": "notes", "metadata": {}},
            {"user": "Open the first one", "family": "notes", "metadata": {}},
        ],
    }]
    seeds = MODULE.build_seeds(sessions)
    assert len(seeds) == 3
    assert seeds[-1]["seed_id"] == "s1:3"
    assert [row["user"] for row in seeds[-1]["context"]] == [
        "Show my calendar", "Now my notes", "Open the first one",
    ]
    assert seeds[-1]["family"] == "notes"


def test_classifier_separates_harness_sft_and_backend_evidence():
    harness = [{"assistant": "There is no preceding answer in this conversation.", "metadata": {}}]
    harness.insert(0, {"assistant": "Done", "metadata": {}})
    assert MODULE.classify(harness)[0] == "harness"

    sft = [{"assistant": "No", "metadata": {"tool_events": [{
        "tool": "read_email", "exit_code": 1, "error": True,
        "output": "uid must be an exact identifier; placeholders are not executable",
    }]}}]
    assert MODULE.classify(sft)[0] == "model_sft"

    backend = [{"assistant": "No", "metadata": {"tool_events": [{
        "tool": "list_emails", "exit_code": 1, "error": True,
        "output": "connection refused",
    }]}}]
    assert MODULE.classify(backend)[0] == "backend"


def test_allowed_policy_decision_is_not_a_harness_failure():
    turns = [{"assistant": "Done", "metadata": {"policy_decisions": [{
        "allowed": True, "reason": "allowed", "tool": "manage_notes",
    }]}}]
    assert MODULE.classify(turns)[0] == "replay_first"
