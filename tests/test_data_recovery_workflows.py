import tarfile
from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.backup_routes import build_recovery_guidance, setup_backup_routes


def _build_test_app(monkeypatch):
    monkeypatch.setattr("routes.backup_routes.require_admin", lambda req: None)
    monkeypatch.setattr("routes.backup_routes.get_current_user", lambda req: "admin")

    mem = MagicMock()
    mem.load_all_for_update.return_value = [
        {"owner": "admin", "text": "Existing memory one"},
    ]

    skills = MagicMock()
    skills.load_all.return_value = [
        {"owner": "admin", "id": "sk-1", "name": "deploy-app", "title": "Deploy App"},
    ]

    presets = MagicMock()
    presets.get_all.return_value = {"fast": {"temperature": 0.2}}

    app = FastAPI()
    app.include_router(setup_backup_routes(mem, presets, skills))
    return TestClient(app), mem, skills, presets


def test_import_preview_counts_additions_duplicates_and_warnings(monkeypatch):
    client, mem, skills, _ = _build_test_app(monkeypatch)

    payload = {
        "version": 1,
        "exported_at": "2026-11-15T12:00:00",
        "exported_by": "admin",
        "memories": [
            {"text": "existing memory one"},  # duplicate (case-insensitive)
            {"text": "Brand new memory"},     # should add
            {"text": ""},                     # invalid empty
        ],
        "skills": [
            {"name": "deploy-app", "title": "Deploy App"},  # duplicate
            {"name": "new-skill", "title": "New Skill"},    # should add
        ],
        "presets": {"balanced": {"temperature": 0.5}},
        "settings": {"theme": "dark"},
    }

    resp = client.post("/api/import/preview", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["sections"]["memories"] == {
        "total_in_file": 3,
        "to_add": 1,
        "duplicates_or_invalid_skipped": 2,
    }
    assert data["sections"]["skills"] == {
        "total_in_file": 2,
        "to_add": 1,
        "duplicates_or_invalid_skipped": 1,
    }
    assert data["sections"]["presets"]["keys_to_merge"] == 1
    assert data["sections"]["settings"]["keys"] == ["theme"]
    # Preview must not mutate stores
    mem.save.assert_not_called()
    skills.add_skill.assert_not_called()


def test_import_preview_warns_on_empty_or_malformed_sections(monkeypatch):
    client, _, _, _ = _build_test_app(monkeypatch)

    resp = client.post("/api/import/preview", json={"version": 99, "memories": "not-a-list"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert any("version" in w.lower() for w in data["warnings"])
    assert any("memories" in w.lower() for w in data["warnings"])


def test_build_recovery_guidance_reports_stores_snapshots_and_commands(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "odysseus.db").write_bytes(b"SQLite format 3\x00")
    (data_dir / "settings.json").write_text("{}", encoding="utf-8")

    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()

    # Without snapshots, guidance warns the operator
    empty_status = build_recovery_guidance(data_dir=data_dir, backup_dir=backup_dir)
    assert len(empty_status["persistent_stores"]["sqlite_databases"]) == 1
    assert any("No full data/ snapshot archives" in w for w in empty_status["warnings"])
    assert "odysseus-backup snapshot" in empty_status["workflows"]["full_snapshot_cli"]["create_command"]

    # Create a snapshot archive and verify it appears with concrete verify/restore commands
    snap = backup_dir / "odysseus-backup-20261115-150000.tar.gz"
    with tarfile.open(snap, "w:gz") as tar:
        tar.add(data_dir / "settings.json", arcname="data/settings.json")

    with_snap = build_recovery_guidance(data_dir=data_dir, backup_dir=backup_dir)
    assert len(with_snap["snapshots"]) == 1
    assert with_snap["snapshots"][0]["name"] == snap.name
    assert snap.name in with_snap["workflows"]["full_snapshot_cli"]["verify_command"]
    assert f"{snap.name} --yes" in with_snap["workflows"]["full_snapshot_cli"]["restore_command"]


def test_recovery_status_route(monkeypatch):
    client, _, _, _ = _build_test_app(monkeypatch)
    resp = client.get("/api/recovery/status")
    assert resp.status_code == 200
    body = resp.json()
    assert "persistent_stores" in body
    assert "workflows" in body
    assert body["workflows"]["portable_json_bundle"]["preview_endpoint"] == "POST /api/import/preview"
