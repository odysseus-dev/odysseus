"""Tests for ProjectContext (T9: shared/inherit/isolated memory handles)."""

import os

from services.project.context import ProjectContext
from services.project.paths import project_data_dir


def test_shared_mode_uses_global_memory_service():
    """Shared mode aliases the global MemoryService — no per-project file."""
    sentinel = object()
    ctx = ProjectContext(
        project_id="prj_x",
        owner="alice",
        data_dir="/nonexistent",
        memory_mode="shared",
        global_memory_service=sentinel,
    )
    assert ctx.memory_service is sentinel
    assert ctx.memory_manager is None
    assert ctx.memory_vector is None


def test_isolated_mode_builds_per_project_handles(tmp_path):
    ctx = ProjectContext(
        project_id="prj_y",
        owner="alice",
        data_dir=str(tmp_path / "prj_y"),
        memory_mode="isolated",
        global_memory_service=None,
    )
    assert ctx.memory_service is not None
    assert ctx.memory_manager is not None
    assert ctx.memory_manager.memory_file == os.path.join(str(tmp_path / "prj_y"), "memory.json")
