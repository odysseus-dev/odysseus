"""Regression: _active_cookbook_endpoint_ids must not crash on a non-dict
cookbook state file.

The reader parses COOKBOOK_STATE_FILE with json.loads inside a try, but the
state.get("tasks") that follows is OUTSIDE that try. The file is managed
lifecycle JSON written by several places (cookbook sync, lifecycle ticker,
codex routes) and is externally editable, so a top-level list or scalar is
possible — which made state.get(...) raise AttributeError. The guard returns
an empty set (same as a parse failure) for any non-dict shape.
"""
import sys
import types
from unittest.mock import MagicMock

from tests.helpers.import_state import (
    clear_fake_endpoint_resolver_modules,
    preserve_import_state,
)

with preserve_import_state("core.database", "src.database",
                           "core.session_manager", "routes.model_routes"):
    clear_fake_endpoint_resolver_modules()
    if "core.database" not in sys.modules:
        _core_db = types.ModuleType("core.database")
        for _name in [
            "SessionLocal", "ModelEndpoint", "Session", "ChatMessage", "Document",
            "DocumentVersion", "GalleryImage", "GalleryAlbum", "Note",
            "CalendarCal", "CalendarEvent", "ScheduledTask", "TaskRun",
            "McpServer", "ProviderAuthSession", "Base",
        ]:
            setattr(_core_db, _name, MagicMock())
        _core_db.utcnow_naive = MagicMock()
        sys.modules["core.database"] = _core_db

    import routes.model_routes as model_routes


def _write_state(tmp_path, text):
    p = tmp_path / "cookbook_state.json"
    p.write_text(text, encoding="utf-8")
    return str(p)


def test_top_level_list_does_not_crash(tmp_path, monkeypatch):
    monkeypatch.setattr(model_routes, "COOKBOOK_STATE_FILE",
                        _write_state(tmp_path, '[{"type": "serve"}]'))
    # Before the guard: AttributeError: 'list' object has no attribute 'get'.
    assert model_routes._active_cookbook_endpoint_ids() == set()


def test_top_level_scalar_does_not_crash(tmp_path, monkeypatch):
    monkeypatch.setattr(model_routes, "COOKBOOK_STATE_FILE",
                        _write_state(tmp_path, '42'))
    assert model_routes._active_cookbook_endpoint_ids() == set()


def test_missing_file_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(model_routes, "COOKBOOK_STATE_FILE",
                        str(tmp_path / "nope.json"))
    assert model_routes._active_cookbook_endpoint_ids() == set()


def test_valid_dict_state_still_extracts_active_serve_ids(tmp_path, monkeypatch):
    state = (
        '{"tasks": ['
        '{"type": "serve", "status": "running", "_endpointId": "local-abc"},'
        '{"type": "serve", "status": "stopped", "_endpointId": "local-dead"},'
        '{"type": "download", "status": "running", "_endpointId": "local-x"}'
        ']}'
    )
    monkeypatch.setattr(model_routes, "COOKBOOK_STATE_FILE",
                        _write_state(tmp_path, state))
    ids = model_routes._active_cookbook_endpoint_ids()
    # Only the active (running) serve task's endpoint id is returned.
    assert "local-abc" in ids
    assert "local-dead" not in ids
    assert "local-x" not in ids
