import json

from src import bg_jobs


def test_load_ignores_non_object_store(tmp_path, monkeypatch):
    store = tmp_path / "bg_jobs.json"
    store.write_text(json.dumps(["not", "a", "job", "store"]), encoding="utf-8")
    monkeypatch.setattr(bg_jobs, "_STORE", store)

    assert bg_jobs._load() == {}


def test_load_keeps_only_object_job_records(tmp_path, monkeypatch):
    store = tmp_path / "bg_jobs.json"
    store.write_text(
        json.dumps(
            {
                "good": {"id": "good", "status": "done"},
                "bad-list": ["not", "a", "job"],
                "bad-null": None,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(bg_jobs, "_STORE", store)

    assert bg_jobs._load() == {"good": {"id": "good", "status": "done"}}
