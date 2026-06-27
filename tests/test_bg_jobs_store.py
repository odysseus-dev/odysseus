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


def test_refresh_ignores_invalid_runtime_numbers(tmp_path, monkeypatch):
    store = tmp_path / "bg_jobs.json"
    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir()
    store.write_text(
        json.dumps(
            {
                "job1": {
                    "id": "job1",
                    "status": "running",
                    "pid": 999999,
                    "started_at": "bad",
                    "max_runtime_s": "bad",
                    "exit_path": str(jobs_dir / "job1.exit"),
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(bg_jobs, "_STORE", store)
    monkeypatch.setattr(bg_jobs, "_JOBS_DIR", jobs_dir)
    monkeypatch.setattr(bg_jobs, "_pid_alive", lambda pid: True)

    assert bg_jobs.refresh()["job1"]["status"] == "running"
