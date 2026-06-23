"""Tests for ProjectService (T8: skeleton — create, get, list, update).

T11/T12/T13 append more tests in subsequent commits."""

import os
import pytest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from services.project.service import ProjectService, ProjectNotFound, ProjectLimitReached, ProjectNameConflict
from services.project.context import ProjectContext


@pytest.fixture
def file_backed_db(tmp_path, monkeypatch):
    """Rebuild SQLAlchemy engine against a real SQLite file so the
    _migrate_add_* migrations can run (they require a file path, not :memory:).
    Both core.database and services.project.service import SessionLocal at
    module load, so both references need to be patched."""
    db_path = tmp_path / "project_test.db"
    db_url = f"sqlite:///{db_path}"
    monkeypatch.setattr("core.database.DATABASE_URL", db_url)
    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    SessionTesting = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr("core.database.SessionLocal", SessionTesting)
    monkeypatch.setattr("core.database.engine", engine)
    monkeypatch.setattr("services.project.service.SessionLocal", SessionTesting)
    from core.database import Base, init_db
    Base.metadata.create_all(bind=engine)
    init_db()
    yield
    try:
        os.remove(str(db_path))
    except FileNotFoundError:
        pass


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


# ────────────────────────────────────── T11 atomic delete ──────────────────────────────────────

def test_delete_drops_chroma_collection_first(file_backed_db, monkeypatch, tmp_path):
    monkeypatch.setenv("ODYSSEUS_DATA_DIR", str(tmp_path))
    svc = ProjectService()
    proj = svc.create(owner="alice", name="X", icon=None, description=None, memory_mode="isolated")

    drop_calls = []
    monkeypatch.setattr(
        "services.project.service._delete_chroma_collection",
        lambda pid: drop_calls.append(pid),
    )
    # Avoid actually wiping the DB file when rmtree runs.
    monkeypatch.setattr("services.project.service.shutil.rmtree", lambda *a, **kw: None)

    svc.delete(proj.id, "alice")
    assert drop_calls == [proj.id]


def test_delete_writes_tombstone_on_filesystem_failure(file_backed_db, monkeypatch, tmp_path):
    monkeypatch.setenv("ODYSSEUS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("services.project.service._delete_chroma_collection", lambda pid: None)

    def boom(*a, **kw):
        raise OSError("disk gone")
    monkeypatch.setattr("services.project.service.shutil.rmtree", boom)

    svc = ProjectService()
    proj = svc.create(owner="alice", name="Y", icon=None, description=None, memory_mode="isolated")

    svc.delete(proj.id, "alice")  # must NOT raise

    # Tombstone row exists.
    from sqlalchemy import select
    from core.database import SessionLocal, DbProject
    with SessionLocal() as db:
        row = db.execute(
            select(DbProject).where(DbProject.id == proj.id)
        ).scalar_one()
    assert row.deleted_at is not None


# ────────────────────────────────────── T12 soft cap + duplicate name ─────────────────────────

def test_soft_cap_rejects_51st_project(file_backed_db, monkeypatch, tmp_path):
    """Creating > 50 projects for the same owner raises ProjectLimitReached."""
    monkeypatch.setenv("ODYSSEUS_DATA_DIR", str(tmp_path))
    svc = ProjectService()
    for i in range(50):
        svc.create(owner="alice", name=f"P{i}", icon=None, description=None, memory_mode="isolated")

    with pytest.raises(ProjectLimitReached) as exc:
        svc.create(owner="alice", name="P51", icon=None, description=None, memory_mode="isolated")
    assert exc.value.maximum == 50


def test_duplicate_name_rejected(file_backed_db, monkeypatch, tmp_path):
    """Same (owner, name) cannot be created twice."""
    monkeypatch.setenv("ODYSSEUS_DATA_DIR", str(tmp_path))
    svc = ProjectService()
    svc.create(owner="alice", name="Same", icon=None, description=None, memory_mode="isolated")
    with pytest.raises(ProjectNameConflict):
        svc.create(owner="alice", name="Same", icon=None, description=None, memory_mode="isolated")


def test_soft_cap_does_not_apply_to_other_owners(file_backed_db, monkeypatch, tmp_path):
    """Bob's projects don't count against Alice's cap."""
    monkeypatch.setenv("ODYSSEUS_DATA_DIR", str(tmp_path))
    svc = ProjectService()
    for i in range(50):
        svc.create(owner="alice", name=f"A{i}", icon=None, description=None, memory_mode="isolated")
    # Bob is unaffected.
    svc.create(owner="bob", name="B0", icon=None, description=None, memory_mode="isolated")


# ────────────────────────────────────── T13 open_context ──────────────────────────────────────

def test_open_context_returns_cached_instance(file_backed_db, monkeypatch, tmp_path):
    monkeypatch.setenv("ODYSSEUS_DATA_DIR", str(tmp_path))
    svc = ProjectService()
    proj = svc.create(owner="alice", name="Ctx", icon=None, description=None, memory_mode="isolated")

    ctx1 = svc.open_context(proj.id, "alice", global_memory_service=None)
    ctx2 = svc.open_context(proj.id, "alice", global_memory_service=None)
    assert ctx1 is ctx2


def test_open_context_isolated_builds_handles(file_backed_db, monkeypatch, tmp_path):
    monkeypatch.setenv("ODYSSEUS_DATA_DIR", str(tmp_path))
    svc = ProjectService()
    proj = svc.create(owner="alice", name="Iso", icon=None, description=None, memory_mode="isolated")
    ctx = svc.open_context(proj.id, "alice", global_memory_service=None)
    assert isinstance(ctx, ProjectContext)
    assert ctx.memory_manager is not None
    assert ctx.memory_vector is not None
    assert ctx.memory_service is not None


def test_open_context_shared_aliases_global(file_backed_db, monkeypatch, tmp_path):
    monkeypatch.setenv("ODYSSEUS_DATA_DIR", str(tmp_path))
    svc = ProjectService()
    proj = svc.create(owner="alice", name="Shared", icon=None, description=None, memory_mode="shared")
    sentinel = object()
    ctx = svc.open_context(proj.id, "alice", global_memory_service=sentinel)
    assert ctx.memory_service is sentinel
