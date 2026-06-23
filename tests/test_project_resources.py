# tests/test_project_resources.py
import os

from services.project.resources import ProjectResourceStore


def _write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def test_add_txt_resource(tmp_path):
    (tmp_path / "uploads").mkdir()
    src = tmp_path / "src.txt"
    _write(str(src), "Hello world.\n" * 50)

    store = ProjectResourceStore(project_id="prj_r", data_dir=str(tmp_path))
    res = store.add(source_path=str(src), filename="hello.txt", mime="text/plain")

    assert res.id.startswith("rsr_")
    assert res.chunk_count > 0
    assert os.path.exists(os.path.join(str(tmp_path), "uploads", "hello.txt"))


def test_add_then_list_then_remove(tmp_path):
    (tmp_path / "uploads").mkdir()
    src = tmp_path / "src.md"
    _write(str(src), "# Heading\n\nSome content. " * 20)

    store = ProjectResourceStore(project_id="prj_r", data_dir=str(tmp_path))
    res = store.add(source_path=str(src), filename="notes.md", mime="text/markdown")

    listed = store.list()
    assert any(r.id == res.id for r in listed)

    store.remove(res.id)
    assert not os.path.exists(os.path.join(str(tmp_path), "uploads", "notes.md"))
    assert store.list() == []
