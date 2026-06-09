import os
from pathlib import Path

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
