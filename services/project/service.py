"""ProjectService — CRUD, ownership, soft cap, atomic delete.

One process-wide singleton is expected; the caller wires it up at app
init time. It owns:

  * The SQLite row in the `projects` table.
  * The filesystem tree under ``DATA_DIR/projects/<owner_slug>/<pid>/``.
  * The per-project ChromaDB collection (via ``ProjectContext``).
  * An in-memory cache of open ``ProjectContext`` handles keyed by pid.

The service does NOT own chat history. Sessions are project-scoped via
``Session.project_id``; CRUD for sessions is reused from the existing
``SessionManager`` plus an ownership check.
"""
from __future__ import annotations

import logging
import os
import shutil
import time
import uuid
from dataclasses import dataclass
from typing import Dict, List, Optional

from sqlalchemy import select, update, delete, text

from fastapi import HTTPException  # for the 503 case

from core.atomic_io import atomic_write_json
from core.database import DbProject, Session as DbSession, SessionLocal
from services.project.paths import project_data_dir, slugify_owner

logger = logging.getLogger(__name__)

PROJECT_SOFT_CAP = 50
PROJECT_ID_PREFIX = "prj_"


def _new_project_id() -> str:
    return f"{PROJECT_ID_PREFIX}{uuid.uuid4().hex[:12]}"


class ProjectNotFound(Exception):
    """Raised when a project lookup fails (used by route layer to return 404)."""


class ProjectNameConflict(Exception):
    """Raised on duplicate (owner, name) — maps to 409 duplicate_name."""


class ProjectLimitReached(Exception):
    """Raised when the soft cap is exceeded — maps to 409 limit_reached."""

    def __init__(self, current: int, maximum: int):
        self.current = current
        self.maximum = maximum
        super().__init__(f"limit reached: {current}/{maximum}")


def _chroma_reachable_or_raise() -> None:
    from src.chroma_client import get_chroma_client
    try:
        get_chroma_client().heartbeat()
    except Exception as e:
        raise HTTPException(status_code=503, detail={"error": "vector_store_unavailable"})


def _delete_chroma_collection(project_id: str) -> None:
    """Idempotent: drop the project's resource collection if present."""
    from src.chroma_client import get_chroma_client
    try:
        client = get_chroma_client()
        client.delete_collection(f"project_resources_{project_id}")
    except Exception:
        pass  # Already-missing is success.


@dataclass
class Project:
    """A read-model for the route layer."""
    id: str
    owner: str
    name: str
    icon: Optional[str]
    description: Optional[str]
    memory_mode: str
    snapshot_meta: Optional[str]
    custom_prompt: Optional[str]
    custom_instructions: Optional[str]
    prompt_override_mode: str
    instructions_override_mode: str
    created_at: int
    updated_at: int

    @classmethod
    def from_row(cls, row: DbProject) -> "Project":
        return cls(
            id=row.id,
            owner=row.owner,
            name=row.name,
            icon=row.icon,
            description=row.description,
            memory_mode=row.memory_mode,
            snapshot_meta=row.snapshot_meta,
            custom_prompt=row.custom_prompt,
            custom_instructions=row.custom_instructions,
            prompt_override_mode=row.prompt_override_mode,
            instructions_override_mode=row.instructions_override_mode,
            created_at=int(row.created_at.timestamp()) if row.created_at else 0,
            updated_at=int(row.updated_at.timestamp()) if row.updated_at else 0,
        )


class ProjectService:
    """Process-wide Projects facade."""

    def __init__(self) -> None:
        # Lazy-loaded ProjectContext handles (populated by open_context()).
        self._contexts: Dict[str, "object"] = {}  # pid -> ProjectContext

    # ───────────────────────────────────────── CRUD ──────────────────────────────────────────

    def create(
        self,
        owner: str,
        name: str,
        icon: Optional[str],
        description: Optional[str],
        memory_mode: str,
    ) -> Project:
        """Create a new project. Enforces soft cap + uniqueness, allocates
        the on-disk tree, runs the Inherit snapshot if requested.

        Raises ProjectLimitReached / ProjectNameConflict on validation
        failures; on any post-rollback failure inside Inherit, the entire
        create rolls back (no orphan directory or row).
        """
        if memory_mode not in ("shared", "inherit", "isolated"):
            raise ValueError(f"invalid memory_mode: {memory_mode}")
        self._check_soft_cap(owner)
        self._check_name_unique(owner, name)

        project_id = _new_project_id()

        with SessionLocal() as db:
            row = DbProject(
                id=project_id,
                owner=owner,
                name=name,
                icon=icon,
                description=description,
                memory_mode=memory_mode,
                prompt_override_mode="append",
                instructions_override_mode="append",
            )
            db.add(row)
            db.commit()
            db.refresh(row)

        # Allocate the on-disk tree AFTER the row exists so the
        # rollback path is "delete row + rmtree directory".
        data_dir = project_data_dir(owner, project_id)
        try:
            self._create_tree(data_dir)
            if memory_mode == "inherit":
                from services.project.snapshot import run_inherit_snapshot
                snap = run_inherit_snapshot(data_dir)
                with SessionLocal() as db:
                    db.execute(
                        update(DbProject)
                        .where(DbProject.id == project_id)
                        .values(snapshot_meta=snap.to_json())
                    )
                    db.commit()
            elif memory_mode == "isolated":
                # Empty memory.json so first read is consistent.
                atomic_write_json(os.path.join(data_dir, "memory.json"), [])
            # shared: no memory files — uses the global brain.
        except Exception:
            # Rollback: delete the row, then remove the partial tree.
            with SessionLocal() as db:
                db.execute(delete(DbProject).where(DbProject.id == project_id))
                db.commit()
            shutil.rmtree(data_dir, ignore_errors=True)
            raise

        return self.get(project_id, owner)

    def get(self, project_id: str, owner: str) -> Project:
        with SessionLocal() as db:
            row = db.execute(
                select(DbProject).where(
                    DbProject.id == project_id,
                    DbProject.owner == owner,
                    DbProject.deleted_at.is_(None),
                )
            ).scalar_one_or_none()
        if row is None:
            raise ProjectNotFound(project_id)
        return Project.from_row(row)

    def list_for_owner(self, owner: str) -> List[Project]:
        with SessionLocal() as db:
            rows = db.execute(
                select(DbProject)
                .where(DbProject.owner == owner, DbProject.deleted_at.is_(None))
                .order_by(DbProject.created_at.desc())
            ).scalars().all()
        return [Project.from_row(r) for r in rows]

    def update_settings(
        self,
        project_id: str,
        owner: str,
        *,
        custom_prompt: Optional[str] = None,
        custom_instructions: Optional[str] = None,
        prompt_override_mode: Optional[str] = None,
        instructions_override_mode: Optional[str] = None,
        name: Optional[str] = None,
        icon: Optional[str] = None,
        description: Optional[str] = None,
    ) -> Project:
        fields = {}
        if custom_prompt is not None:
            fields["custom_prompt"] = custom_prompt
        if custom_instructions is not None:
            fields["custom_instructions"] = custom_instructions
        if prompt_override_mode is not None:
            if prompt_override_mode not in ("append", "override"):
                raise ValueError("prompt_override_mode must be 'append' or 'override'")
            fields["prompt_override_mode"] = prompt_override_mode
        if instructions_override_mode is not None:
            if instructions_override_mode not in ("append", "override"):
                raise ValueError("instructions_override_mode must be 'append' or 'override'")
            fields["instructions_override_mode"] = instructions_override_mode
        if name is not None:
            fields["name"] = name
        if icon is not None:
            fields["icon"] = icon
        if description is not None:
            fields["description"] = description
        if not fields:
            return self.get(project_id, owner)

        with SessionLocal() as db:
            res = db.execute(
                update(DbProject)
                .where(DbProject.id == project_id, DbProject.owner == owner,
                       DbProject.deleted_at.is_(None))
                .values(**fields)
            )
            db.commit()
            if res.rowcount == 0:
                raise ProjectNotFound(project_id)
        return self.get(project_id, owner)

    def delete(self, project_id: str, owner: str) -> None:
        """Atomicity order per spec §1d:

        1. Pre-flight: ChromaDB reachable.
        2. ChromaDB: drop `project_resources_<pid>` collection.
        3. SQLite: delete sessions + project row in one transaction.
        4. FS: ``shutil.rmtree(data_dir)``.
        5. On FS failure: insert tombstone row so a sweeper retries.
        """
        # Step 1 — ChromaDB reachability check.
        _chroma_reachable_or_raise()

        # Step 2 — drop resource collection (idempotent on missing).
        _delete_chroma_collection(project_id)

        # Step 3 — SQLite.
        with SessionLocal() as db:
            # `project_id` on `sessions` is a migration-managed column not
            # declared on the SQLAlchemy model, so delete via raw SQL.
            db.execute(
                text("DELETE FROM sessions WHERE project_id = :pid"),
                {"pid": project_id},
            )
            res = db.execute(
                delete(DbProject).where(
                    DbProject.id == project_id, DbProject.owner == owner
                )
            )
            db.commit()
            if res.rowcount == 0:
                raise ProjectNotFound(project_id)

        # Step 4 — FS wipe. On failure, write a tombstone.
        data_dir = project_data_dir(owner, project_id)
        try:
            shutil.rmtree(data_dir, ignore_errors=False)
        except OSError:
            with SessionLocal() as db:
                db.add(DbProject(
                    id=project_id,
                    owner=owner,
                    name="__deleted__",  # placeholder; never appears in list_for_owner
                    memory_mode="isolated",
                    prompt_override_mode="append",
                    instructions_override_mode="append",
                    deleted_at=int(time.time()),
                ))
                db.commit()
            logger.warning("FS wipe failed for %s; tombstone inserted", project_id)

    # ────────────────────────────────────── internals ─────────────────────────────────────────

    def _check_soft_cap(self, owner: str) -> None:
        with SessionLocal() as db:
            count = db.execute(
                select(DbProject.id).where(
                    DbProject.owner == owner, DbProject.deleted_at.is_(None)
                )
            ).all()
        if len(count) >= PROJECT_SOFT_CAP:
            raise ProjectLimitReached(len(count), PROJECT_SOFT_CAP)

    def _check_name_unique(self, owner: str, name: str) -> None:
        with SessionLocal() as db:
            existing = db.execute(
                select(DbProject.id).where(
                    DbProject.owner == owner,
                    DbProject.name == name,
                    DbProject.deleted_at.is_(None),
                )
            ).first()
        if existing is not None:
            raise ProjectNameConflict(name)

    def _create_tree(self, data_dir: str) -> None:
        """Allocate the project directory + subdirectories + initial empty
        rag_index.json. Atomic write of rag_index.json ensures a
        half-created project directory never has a missing index."""
        os.makedirs(os.path.join(data_dir, "uploads"), exist_ok=True)
        os.makedirs(os.path.join(data_dir, "memory_vectors"), exist_ok=True)
        atomic_write_json(
            os.path.join(data_dir, "rag_index.json"),
            {"version": 1, "resources": []},
        )
