from src.misumi_observability import EVENT_FIELDS, MisumiEventLog


def test_event_log_emits_fixed_structured_fields(tmp_path):
    log = MisumiEventLog(tmp_path / "events.jsonl")
    record = log.emit({"request_id": "r1", "persona": "aoteru", "outcome": "planned"})

    assert set(EVENT_FIELDS).issubset(record)
    assert log.recent()[0]["request_id"] == "r1"
