import json

from src.personal_docs import PersonalDocsManager


def test_manager_ignores_invalid_persisted_state_shapes(tmp_path):
    (tmp_path / "indexed_directories.json").write_text(json.dumps({"bad": "shape"}))
    (tmp_path / "excluded_files.json").write_text(json.dumps({"bad": "shape"}))

    manager = PersonalDocsManager(str(tmp_path))

    assert manager.indexed_directories == []
    assert manager.excluded_files == set()


def test_manager_filters_invalid_persisted_state_rows(tmp_path):
    (tmp_path / "indexed_directories.json").write_text(json.dumps(["/tmp/docs", 123]))
    (tmp_path / "excluded_files.json").write_text(json.dumps(["/tmp/docs/a.txt", None]))

    manager = PersonalDocsManager(str(tmp_path))

    assert manager.indexed_directories == ["/tmp/docs"]
    assert manager.excluded_files == {"/tmp/docs/a.txt"}
