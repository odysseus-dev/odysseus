import json
from types import SimpleNamespace

from routes.chat_helpers import _append_sft_trace_record, remove_session_sft_trace_rows


def test_remove_session_sft_trace_rows_moves_all_session_rows_to_trash(monkeypatch, tmp_path):
    trace_dir = tmp_path / "traces"
    trace_dir.mkdir()
    path = trace_dir / "sft_alex.jsonl"
    rows = [
        {"session_id": "delete-me", "user": "one"},
        {"session_id": "keep-me", "user": "two"},
        {"session_id": "delete-me", "user": "three"},
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    monkeypatch.setenv("ODYSSEUS_SFT_TRACE_DIR", str(trace_dir))

    removed = remove_session_sft_trace_rows("sft_alex", "delete-me")

    assert removed == 2
    kept = [json.loads(line) for line in path.read_text().splitlines()]
    assert [row["session_id"] for row in kept] == ["keep-me"]
    trash = [json.loads(line) for line in (path.with_suffix(".jsonl.trash")).read_text().splitlines()]
    assert len(trash) == 2
    assert all(row["deleted_from_training"] is True for row in trash)


def test_append_sft_trace_record_writes_runtime_revision(monkeypatch, tmp_path):
    trace_dir = tmp_path / "traces"
    trace_dir.mkdir()
    monkeypatch.setenv("ODYSSEUS_SFT_TRACE_DIR", str(trace_dir))
    monkeypatch.setenv("ODYSSEUS_RUNTIME_REVISION", "revision-under-test")
    sess = SimpleNamespace(
        name="trace session",
        history=[SimpleNamespace(role="user", content="do the task")],
    )

    _append_sft_trace_record(
        owner="sft_alex",
        session_id="s1",
        sess=sess,
        assistant_content="done",
        metadata={"thinking": "used tools", "tool_events": [{"tool": "bash"}]},
        message_id="m1",
    )

    row = json.loads((trace_dir / "sft_alex.jsonl").read_text(encoding="utf-8"))
    assert row["runtime_revision"] == "revision-under-test"
    assert row["metadata"]["runtime_revision"] == "revision-under-test"
