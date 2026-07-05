import os
from pathlib import Path
from types import SimpleNamespace

from routes import personal_routes


def test_personal_upload_paths_are_owner_scoped_and_unique(tmp_path, monkeypatch):
    monkeypatch.setattr(personal_routes, "UPLOADS_DIR", str(tmp_path))

    alice_dir = personal_routes._personal_upload_dir_for_owner("alice")
    bob_dir = personal_routes._personal_upload_dir_for_owner("bob")

    assert Path(alice_dir).parent == tmp_path
    assert Path(bob_dir).parent == tmp_path
    assert alice_dir != bob_dir

    first_path, first_stored, first_display = personal_routes._unique_personal_upload_path(
        alice_dir,
        "notes.txt",
    )
    second_path, second_stored, second_display = personal_routes._unique_personal_upload_path(
        alice_dir,
        "notes.txt",
    )

    assert first_display == second_display == "notes.txt"
    assert first_stored != second_stored
    assert first_path != second_path
    assert Path(first_path).parent == Path(alice_dir)
    assert Path(second_path).parent == Path(alice_dir)


def test_personal_upload_paths_stay_under_upload_root(tmp_path, monkeypatch):
    monkeypatch.setattr(personal_routes, "UPLOADS_DIR", str(tmp_path))

    upload_dir = personal_routes._personal_upload_dir_for_owner("../alice")
    file_path, stored_name, display_name = personal_routes._unique_personal_upload_path(
        upload_dir,
        "../../.env",
    )

    assert os.path.commonpath([file_path, upload_dir]) == upload_dir
    assert Path(file_path).name == stored_name
    assert display_name == "env"


def test_rename_personal_upload_owner_moves_files_and_rewrites_rag(tmp_path, monkeypatch):
    monkeypatch.setattr(personal_routes, "UPLOADS_DIR", str(tmp_path))

    old_dir = Path(personal_routes._personal_upload_dir_for_owner("alice"))
    old_file = old_dir / "note.txt"
    old_file.write_text("alice private RAG note", encoding="utf-8")

    manager_calls = []
    rag_calls = []
    manager = SimpleNamespace(
        rename_directory=lambda old, new, path_map=None: manager_calls.append((old, new, dict(path_map or {}))),
    )
    rag = SimpleNamespace(
        rename_owner=lambda old, new, path_map=None, path_prefixes=None: rag_calls.append(
            (old, new, dict(path_map or {}), list(path_prefixes or []))
        ) or {"success": True, "updated_count": 1},
    )

    result = personal_routes.rename_personal_upload_owner(
        "alice",
        "alice2",
        personal_docs_manager=manager,
        rag_manager=rag,
    )

    new_dir = Path(personal_routes._personal_upload_dir_for_owner("alice2"))
    new_file = new_dir / "note.txt"
    assert old_file.exists() is False
    assert new_file.read_text(encoding="utf-8") == "alice private RAG note"
    assert result["moved_files"] == 1
    assert manager_calls == [(str(old_dir), str(new_dir), {str(old_file): str(new_file)})]
    assert rag_calls == [
        (
            "alice",
            "alice2",
            {str(old_file): str(new_file)},
            [(str(old_dir), str(new_dir))],
        )
    ]
