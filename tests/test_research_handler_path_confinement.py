import json

import pytest

from src import research_handler
from src.research_handler import ResearchHandler


def _handler():
    handler = ResearchHandler.__new__(ResearchHandler)
    handler._active_tasks = {}
    return handler


def test_research_json_path_allows_safe_ids(tmp_path, monkeypatch):
    data_dir = tmp_path / "deep_research"
    monkeypatch.setattr(research_handler, "RESEARCH_DATA_DIR", data_dir)

    path = research_handler._research_json_path("rp-abc123")

    assert path == (data_dir / "rp-abc123.json").resolve()


@pytest.mark.parametrize("session_id", ["../escape", "..", "rp/test", "rp_test", "", None])
def test_research_json_path_rejects_invalid_ids(tmp_path, monkeypatch, session_id):
    monkeypatch.setattr(research_handler, "RESEARCH_DATA_DIR", tmp_path / "deep_research")

    assert research_handler._research_json_path(session_id) is None


def test_research_json_path_rejects_symlink_escape(tmp_path, monkeypatch):
    data_dir = tmp_path / "deep_research"
    outside = tmp_path / "outside"
    data_dir.mkdir()
    outside.mkdir()
    monkeypatch.setattr(research_handler, "RESEARCH_DATA_DIR", data_dir)
    link = data_dir / "rp-abc123.json"
    target = outside / "rp-abc123.json"
    target.write_text("{}", encoding="utf-8")
    try:
        link.symlink_to(target)
    except (AttributeError, NotImplementedError, OSError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    assert research_handler._research_json_path("rp-abc123") is None


def test_handler_disk_read_methods_reject_invalid_ids(tmp_path, monkeypatch):
    outside = tmp_path / "escape.json"
    outside.write_text(json.dumps({"result": "secret"}), encoding="utf-8")
    monkeypatch.setattr(research_handler, "RESEARCH_DATA_DIR", tmp_path / "deep_research")
    handler = _handler()

    assert handler.get_status("../escape") is None
    assert handler.get_result("../escape") is None
    assert handler.get_sources("../escape") is None
    assert handler.get_raw_findings("../escape") is None
    assert handler._get_session_json("../escape") is None
    assert handler.get_report_html("../escape") is None


def test_handler_mutations_reject_invalid_ids_without_touching_outside_files(tmp_path, monkeypatch):
    outside = tmp_path / "escape.json"
    outside.write_text(json.dumps({"result": "secret", "hidden_images": ["x"]}), encoding="utf-8")
    monkeypatch.setattr(research_handler, "RESEARCH_DATA_DIR", tmp_path / "deep_research")
    handler = _handler()

    assert handler.hide_image("../escape", "https://example.com/image.png") is False
    assert handler.unhide_all_images("../escape") is False
    handler.clear_result("../escape")
    handler._save_result("../escape", {"query": "q", "status": "done", "result": "r", "started_at": 1})

    assert json.loads(outside.read_text(encoding="utf-8")) == {
        "result": "secret",
        "hidden_images": ["x"],
    }


def test_start_research_rejects_invalid_session_id():
    handler = _handler()

    with pytest.raises(ValueError):
        handler.start_research("../escape", "q", "http://localhost", "model")
