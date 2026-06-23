"""Tests for ProjectService (T8: skeleton — create, get, list, update).

T11/T12/T13 append more tests in subsequent commits."""

import pytest

from services.project.service import ProjectService, ProjectNotFound


def test_create_isolated_project_returns_db_row(tmp_path, monkeypatch):
    """Bare create of an `isolated` project — exercises the file tree and
    the SQLite row. Other modes are tested separately."""
    monkeypatch.setenv("ODYSSEUS_DATA_DIR", str(tmp_path))
    svc = ProjectService()
    proj = svc.create(
        owner="alice",
        name="My Notes",
        icon="📒",
        description="Course notes for fall semester",
        memory_mode="isolated",
    )
    assert proj.owner == "alice"
    assert proj.name == "My Notes"
    assert proj.memory_mode == "isolated"
    assert proj.id.startswith("prj_")
