import json
from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

import routes.upload_routes as upload_routes
from routes.upload_routes import audit_upload_storage_integrity, setup_upload_routes


def test_audit_upload_storage_integrity_detects_orphans_sidecar_drift_and_missing_refs(tmp_path):
    up_dir = tmp_path / "uploads"
    meta_dir = up_dir / ".meta"
    vision_dir = up_dir / ".vision"
    meta_dir.mkdir(parents=True)
    vision_dir.mkdir(parents=True)

    # 1. Referenced file with valid sidecar
    f1 = up_dir / "abc12345.pdf"
    f1.write_bytes(b"x" * 1024)
    (meta_dir / "abc12345.json").write_text(json.dumps({"hash": "h-abc", "name": "doc.pdf"}), encoding="utf-8")

    # 2. Unreferenced file (orphan payload growth)
    f2 = up_dir / "orphan99.png"
    f2.write_bytes(b"y" * 2048)
    (meta_dir / "orphan99.json").write_text(json.dumps({"hash": "h-orphan", "name": "old.png"}), encoding="utf-8")

    # 3. File missing its .meta sidecar (referenced by ID)
    f3 = up_dir / "nometa77.txt"
    f3.write_bytes(b"z" * 512)

    # 4. Dangling .meta file with no payload on disk
    (meta_dir / "ghost404.json").write_text(json.dumps({"name": "gone.txt"}), encoding="utf-8")

    # 5. Vision cache sidecar bytes
    (vision_dir / "abc12345.txt").write_text("cached ocr", encoding="utf-8")

    report = audit_upload_storage_integrity(
        up_dir,
        referenced_ids={"abc12345", "nometa77", "missing_on_disk_1"},
        referenced_hashes={"h-abc"},
    )

    assert report["ok"] is False
    assert report["counts"] == {
        "disk_files": 3,
        "referenced_present": 2,
        "unreferenced_files": 1,
        "missing_metadata": 1,
        "dangling_metadata": 1,
        "dangling_references": 1,
    }
    assert report["bytes"]["payload_total"] == 1024 + 2048 + 512
    assert report["bytes"]["unreferenced_reclaimable"] == 2048
    assert report["bytes"]["sidecar_cache"] == len("cached ocr".encode("utf-8"))
    assert report["unreferenced_ids"] == ["orphan99"]
    assert report["missing_metadata_ids"] == ["nometa77"]
    assert report["dangling_metadata_ids"] == ["ghost404"]
    assert report["dangling_referenced_ids"] == ["missing_on_disk_1"]
    assert len(report["warnings"]) == 4


def test_upload_integrity_endpoint(monkeypatch, tmp_path):
    up_dir = tmp_path / "uploads"
    (up_dir / ".meta").mkdir(parents=True)
    (up_dir / "clean111.txt").write_bytes(b"hello")
    (up_dir / ".meta" / "clean111.json").write_text(json.dumps({"hash": "h-111"}), encoding="utf-8")

    monkeypatch.setattr(upload_routes, "require_admin", lambda req: None)
    monkeypatch.setattr(
        upload_routes,
        "_collect_persisted_upload_references",
        lambda: ({"clean111"}, {"h-111"}),
    )

    handler = MagicMock()
    handler.UPLOAD_DIR = str(up_dir)
    router, _ = setup_upload_routes(handler)

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    resp = client.get("/api/upload/integrity")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["counts"]["disk_files"] == 1
    assert body["counts"]["referenced_present"] == 1
    assert body["counts"]["unreferenced_files"] == 0
