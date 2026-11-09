import json

from scripts import review_sft_environment_expansion as review


def test_remove_trace_sessions_is_owner_file_scoped_and_atomic(tmp_path, monkeypatch):
    monkeypatch.setattr(review, "TRACE_DIR", tmp_path)
    path = tmp_path / "owner.jsonl"
    rows = [
        {"session_id": "keep", "user": "a"},
        {"session_id": "reject", "user": "b"},
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")

    assert review.remove_trace_sessions("owner", {"reject"}) == 1
    remaining = [json.loads(line) for line in path.read_text().splitlines()]
    assert [row["session_id"] for row in remaining] == ["keep"]


def test_trace_rows_selects_only_requested_sessions(tmp_path, monkeypatch):
    monkeypatch.setattr(review, "TRACE_DIR", tmp_path)
    path = tmp_path / "owner.jsonl"
    path.write_text(
        json.dumps({"session_id": "one"}) + "\n" + json.dumps({"session_id": "two"}) + "\n"
    )
    assert review.trace_rows("owner", {"two"}) == [{"session_id": "two"}]
