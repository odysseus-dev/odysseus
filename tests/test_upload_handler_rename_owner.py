import json
import os
from pathlib import Path

from src.upload_handler import UploadHandler


def _make_handler(tmp_path: Path) -> UploadHandler:
    base = tmp_path / "base"
    upload = tmp_path / "uploads"
    base.mkdir()
    upload.mkdir()
    return UploadHandler(base_dir=str(base), upload_dir=str(upload))


def _db_path(handler: UploadHandler) -> str:
    return os.path.join(handler.upload_dir, "uploads.json")


def _write_upload_file(handler: UploadHandler, file_id: str, content: bytes = b"content") -> str:
    upload_day = Path(handler.upload_dir) / "2026" / "06" / "09"
    upload_day.mkdir(parents=True, exist_ok=True)
    path = upload_day / file_id
    path.write_bytes(content)
    return str(path)


def _entry(handler: UploadHandler, owner: str, file_hash: str, file_id: str) -> dict:
    path = _write_upload_file(handler, file_id, content=f"{owner}:{file_hash}".encode())
    return {
        "id": file_id,
        "path": path,
        "mime": "text/plain",
        "size": os.path.getsize(path),
        "name": f"{file_id}.txt",
        "hash": file_hash,
        "original_name": f"{file_id}.txt",
        "uploaded_at": "2026-06-09T10:00:00",
        "last_accessed": "2026-06-09T10:00:00",
        "client_ip": "127.0.0.1",
        "owner": owner,
    }


def test_rename_owner_updates_upload_metadata_key_and_resolver(tmp_path):
    handler = _make_handler(tmp_path)
    alice_id = "a" * 32 + ".txt"
    alice_entry = _entry(handler, "Alice", "hash-alice", alice_id)
    bob_entry = _entry(handler, "bob", "hash-bob", "b" * 32 + ".txt")
    handler._atomic_write_json(
        _db_path(handler),
        {
            "Alice:hash-alice": alice_entry,
            "bob:hash-bob": bob_entry,
        },
    )

    renamed = handler.rename_owner("alice", "alice2")

    assert renamed == 1
    updated = json.loads(Path(_db_path(handler)).read_text(encoding="utf-8"))
    assert "Alice:hash-alice" not in updated
    assert "alice2:hash-alice" in updated
    assert updated["alice2:hash-alice"]["owner"] == "alice2"
    assert updated["alice2:hash-alice"]["path"] == alice_entry["path"]
    assert updated["alice2:hash-alice"]["hash"] == alice_entry["hash"]
    assert updated["alice2:hash-alice"]["uploaded_at"] == alice_entry["uploaded_at"]
    assert updated["alice2:hash-alice"]["last_accessed"] == alice_entry["last_accessed"]
    assert updated["bob:hash-bob"]["owner"] == "bob"

    assert handler.resolve_upload(alice_id, owner="alice2")["id"] == alice_id
    assert handler.resolve_upload(alice_id, owner="alice") is None


def test_rename_owner_preserves_rows_when_target_key_collides(tmp_path):
    handler = _make_handler(tmp_path)
    migrated_id = "c" * 32 + ".txt"
    existing_id = "d" * 32 + ".txt"
    migrated = _entry(handler, "alice", "same-hash", migrated_id)
    existing = _entry(handler, "alice2", "same-hash", existing_id)
    unrelated = _entry(handler, "carol", "other-hash", "e" * 32 + ".txt")
    handler._atomic_write_json(
        _db_path(handler),
        {
            "alice:same-hash": migrated,
            "alice2:same-hash": existing,
            "carol:other-hash": unrelated,
        },
    )

    renamed = handler.rename_owner("alice", "alice2")

    assert renamed == 1
    updated = json.loads(Path(_db_path(handler)).read_text(encoding="utf-8"))
    assert len(updated) == 3
    assert updated["alice2:same-hash"]["id"] == existing_id
    migrated_key = f"alice2:same-hash:{migrated_id}"
    assert updated[migrated_key]["id"] == migrated_id
    assert updated[migrated_key]["owner"] == "alice2"
    assert updated[migrated_key]["path"] == migrated["path"]
    assert updated["carol:other-hash"] == unrelated
