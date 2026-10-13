import json

from scripts.curate_sft_trace_run import (
    curate_rows,
    load_passing_report_sessions,
    load_trace_rows,
    row_runtime_revision,
    write_jsonl,
)


def test_curate_rows_keeps_only_report_passing_sessions_and_enriches_rows() -> None:
    passing = {
        "s1": {"id": "email_000", "domain": "email"},
        "s2": {"id": "notes_000", "domain": "notes"},
    }
    rows = [
        {"session_id": "s1", "session_name": "SFT trace batch sft_maya_ops email 000", "thinking": "t", "tool_events": [{}]},
        {"session_id": "failed", "session_name": "SFT trace batch sft_maya_ops email 999", "thinking": "bad"},
        {"session_id": "s2", "session_name": "SFT trace batch sft_maya_ops notes 000", "thinking": "", "tool_events": [{}]},
        {"session_id": "s1", "session_name": "duplicate", "thinking": "dup"},
    ]

    curated, summary = curate_rows(rows, passing)

    assert [row["session_id"] for row in curated] == ["s1", "s2"]
    assert curated[0]["eval_case_id"] == "email_000"
    assert curated[1]["eval_domain"] == "notes"
    assert summary["rows"] == 2
    assert summary["duplicate_sessions_skipped"] == 1
    assert summary["domains"] == {"email": 1, "notes": 1}
    assert summary["rows_with_thinking"] == 1
    assert summary["missing_sessions"] == 0


def test_curate_rows_can_require_thinking() -> None:
    passing = {
        "s1": {"id": "email_000", "domain": "email"},
        "s2": {"id": "calendar_000", "domain": "calendar"},
    }
    rows = [
        {"session_id": "s1", "session_name": "SFT trace batch sft_maya_ops email 000", "thinking": "t"},
        {"session_id": "s2", "session_name": "SFT trace batch sft_maya_ops calendar 000", "thinking": ""},
    ]

    curated, summary = curate_rows(rows, passing, require_thinking=True)

    assert [row["session_id"] for row in curated] == ["s1"]
    assert summary["skipped_no_thinking"] == 1
    assert summary["missing_sessions"] == 1
    assert summary["missing_without_reason"] == 0
    assert summary["missing_session_ids"] == ["s2"]


def test_curate_rows_requires_runtime_revision_when_requested() -> None:
    passing = {
        "s1": {"id": "email_000", "domain": "email"},
        "s2": {"id": "notes_000", "domain": "notes"},
        "s3": {"id": "calendar_000", "domain": "calendar"},
    }
    rows = [
        {"session_id": "s1", "runtime_revision": "revision-current"},
        {"session_id": "s2"},
        {"session_id": "s3", "metadata": {"runtime_revision": "revision-old"}},
    ]

    curated, summary = curate_rows(
        rows,
        passing,
        require_runtime_revision=True,
        expected_runtime_revision="revision-current",
    )

    assert [row["session_id"] for row in curated] == ["s1"]
    assert curated[0]["runtime_revision"] == "revision-current"
    assert summary["skipped_missing_runtime_revision"] == 1
    assert summary["skipped_mismatched_runtime_revision"] == 1
    assert summary["missing_without_reason"] == 0
    assert summary["rows_with_runtime_revision"] == 1
    assert summary["missing_runtime_revision_session_ids"] == ["s2"]
    assert summary["mismatched_runtime_revision_session_ids"] == ["s3"]


def test_row_runtime_revision_accepts_metadata_json_string() -> None:
    assert row_runtime_revision({"metadata": "{\"runtime_revision\":\"revision-meta\"}"}) == "revision-meta"


def test_load_and_write_jsonl_round_trip(tmp_path) -> None:
    report = tmp_path / "actual_results.json"
    report.write_text(
        json.dumps(
            {
                "results": [
                    {"session_id": "s1", "id": "email_000", "domain": "email", "pass": True},
                    {"session_id": "s2", "id": "email_001", "domain": "email", "pass": False},
                ]
            }
        ),
        encoding="utf-8",
    )
    trace = tmp_path / "trace.jsonl"
    write_jsonl(trace, [{"session_id": "s1", "assistant": "ok"}])

    assert load_passing_report_sessions(report) == {
        "s1": {"session_id": "s1", "id": "email_000", "domain": "email", "pass": True}
    }
    assert load_trace_rows(trace) == [{"session_id": "s1", "assistant": "ok"}]
