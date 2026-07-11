"""
factory_service.py — Project Factory Service (SQLAlchemy rewrite)

Pure logic layer (no FastAPI imports) implementing the Factory Service
for the Odysseus project. Provides project CRUD, DAG management,
execution orchestration, and state machine validation.

Uses SQLAlchemy models from factory_models and get_db_session() from
core.database — no raw sqlite3.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from core.database import get_db_session
from services.factory_models import (
    FactoryProject,
    FactoryNode,
    FactoryEdge,
    FactoryEvent,
)

logger = logging.getLogger(__name__)


# ── Helpers ──────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _project_to_dict(p: FactoryProject) -> Dict[str, Any]:
    return {
        "id": p.id,
        "title": p.title,
        "description": p.description or "",
        "status": p.status,
        "model": p.model or "",
        "owner": p.owner or "default",
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
    }


def _node_to_dict(n: FactoryNode) -> Dict[str, Any]:
    return {
        "id": n.id,
        "project_id": n.project_id,
        "task_type": n.task_type or "",
        "title": n.title or "",
        "description": n.description or "",
        "status": n.status or "pending",
        "assigned_agent": n.assigned_agent or "",
        "agent": n.assigned_agent or "",
        "filename": getattr(n, "filename", None) or "",
        "result": n.result,
        "error": n.error,
        "dependencies": n.dependencies,
        "priority": n.priority or 0,
        "retries": n.retries or 0,
        "created_at": n.created_at.isoformat() if n.created_at else None,
        "updated_at": n.updated_at.isoformat() if n.updated_at else None,
    }


def _edge_to_dict(e: FactoryEdge) -> Dict[str, Any]:
    return {
        "id": e.id,
        "from_node_id": e.from_node_id,
        "to_node_id": e.to_node_id,
        "project_id": e.project_id,
    }


def _event_to_dict(ev: FactoryEvent) -> Dict[str, Any]:
    return {
        "id": ev.id,
        "project_id": ev.project_id,
        "node_id": ev.node_id,
        "event_type": ev.event_type,
        "message": ev.message or "",
        "metadata": ev.metadata_,
        "created_at": ev.created_at.isoformat() if ev.created_at else None,
    }


# ── Valid status transitions ─────────────────────────────────

_VALID_PROJECT_TRANSITIONS = {
    "planning":     {"planning", "queued", "cancelled", "failed"},
    "queued":       {"planning", "running", "cancelled", "failed"},
    "running":      {"paused", "completed", "failed", "cancelled"},
    "paused":       {"running", "completed", "failed", "cancelled"},
    "completed":    {"running"},  # allow re-opening for iteration
    "failed":       {"queued"},
    "cancelled":    set(),
}

_VALID_NODE_TRANSITIONS = {
    "pending":            {"pending", "ready", "running", "skipped", "cancelled"},
    "ready":              {"ready", "running", "cancelled"},
    "running":            {"completed", "failed", "human_intervention", "cancelled"},
    "completed":          set(),
    "failed":             {"ready", "running", "cancelled"},
    "human_intervention": {"ready", "running", "cancelled"},
    "skipped":            set(),
    "cancelled":          set(),
}


# ── FactoryService ───────────────────────────────────────────

class FactoryService:
    """
    Stateless service — every method opens its own DB session via
    get_db_session().  No shared connection, no __init__ with conn.
    """

    # ── Project CRUD ──────────────────────────────────────────

    def create_project(
        self,
        description: str = "",
        title: Optional[str] = None,
        model: str = "",
        owner: str = "default",
    ) -> Dict[str, Any]:
        with get_db_session() as db:
            p = FactoryProject(
                title=title or (description[:80].strip() if description else "Untitled Project"),
                description=description,
                status="planning",
                model=model,
                owner=owner,
                created_at=_now(),
                updated_at=_now(),
            )
            db.add(p)
            db.flush()  # get id
            self._log_event(db, p.id, event_type="project_created",
                            message=f"Project '{p.title}' created")
            return _project_to_dict(p)

    def get_project(self, project_id: int) -> Optional[Dict[str, Any]]:
        with get_db_session() as db:
            p = db.query(FactoryProject).filter(FactoryProject.id == project_id).first()
            if not p:
                return None
            data = _project_to_dict(p)
            nodes = db.query(FactoryNode).filter(
                FactoryNode.project_id == project_id
            ).order_by(FactoryNode.id).all()
            data["tasks"] = [_node_to_dict(n) for n in nodes]
            return data

    def get_projects(self, owner: str = "default") -> List[Dict[str, Any]]:
        with get_db_session() as db:
            rows = db.query(FactoryProject).filter(
                FactoryProject.owner == owner
            ).order_by(FactoryProject.id.desc()).all()
            result = []
            for p in rows:
                data = _project_to_dict(p)
                nodes = db.query(FactoryNode).filter(
                    FactoryNode.project_id == p.id
                ).order_by(FactoryNode.id).all()
                data["tasks"] = [_node_to_dict(n) for n in nodes]
                result.append(data)
            return result

    def update_project(
        self,
        project_id: int,
        title: Optional[str] = None,
        description: Optional[str] = None,
        model: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        with get_db_session() as db:
            p = db.query(FactoryProject).filter(FactoryProject.id == project_id).first()
            if not p:
                return None
            if title is not None:
                p.title = title
            if description is not None:
                p.description = description
            if model is not None:
                p.model = model
            p.updated_at = _now()
            self._log_event(db, project_id, event_type="project_updated",
                            message="Project metadata updated")
            return _project_to_dict(p)

    def delete_project(self, project_id: int) -> bool:
        with get_db_session() as db:
            p = db.query(FactoryProject).filter(FactoryProject.id == project_id).first()
            if not p:
                return False
            # Cascade should handle edges/events, but delete nodes explicitly
            db.query(FactoryEdge).filter(FactoryEdge.project_id == project_id).delete()
            db.query(FactoryEvent).filter(FactoryEvent.project_id == project_id).delete()
            db.query(FactoryNode).filter(FactoryNode.project_id == project_id).delete()
            db.delete(p)
            return True

    # ── Project status transitions ────────────────────────────

    def set_project_status(
        self, project_id: int, new_status: str
    ) -> Optional[Dict[str, Any]]:
        with get_db_session() as db:
            p = db.query(FactoryProject).filter(FactoryProject.id == project_id).first()
            if not p:
                return None
            allowed = _VALID_PROJECT_TRANSITIONS.get(p.status, set())
            if new_status not in allowed:
                raise ValueError(
                    f"Invalid transition: {p.status} -> {new_status}. "
                    f"Allowed: {sorted(allowed)}"
                )
            old = p.status
            p.status = new_status
            p.updated_at = _now()
            self._log_event(
                db, project_id,
                event_type="status_changed",
                message=f"Project {old} -> {new_status}",
            )
            return _project_to_dict(p)

    # ── Node CRUD ──────────────────────────────────────────────

    def add_node(
        self,
        project_id: int,
        task_type: str,
        title: str = "",
        description: str = "",
        assigned_agent: str = "",
        dependencies: Optional[List[int]] = None,
        priority: int = 0,
        filename: Optional[str] = None,
    ) -> Dict[str, Any]:
        with get_db_session() as db:
            p = db.query(FactoryProject).filter(FactoryProject.id == project_id).first()
            if not p:
                raise ValueError(f"Project {project_id} not found")
            if p.status not in ("planning", "queued", "running"):
                raise ValueError(
                    f"Cannot add nodes to project in '{p.status}' status"
                )

            n = FactoryNode(
                project_id=project_id,
                task_type=task_type,
                title=title,
                description=description,
                status="pending",
                assigned_agent=assigned_agent,
                filename=filename,
                dependencies=dependencies or [],
                priority=priority,
                retries=0,
                created_at=_now(),
                updated_at=_now(),
            )
            db.add(n)
            db.flush()  # get id

            # Create edges for each dependency
            for dep_id in (dependencies or []):
                dep = db.query(FactoryNode).filter(
                    FactoryNode.id == dep_id,
                    FactoryNode.project_id == project_id,
                ).first()
                if dep:
                    edge = FactoryEdge(
                        project_id=project_id,
                        from_node_id=dep_id,
                        to_node_id=n.id,
                    )
                    db.add(edge)
                else:
                    logger.warning("Dependency node %d not found, skipping edge", dep_id)

            self._log_event(
                db, project_id,
                event_type="node_added",
                node_id=n.id,
                message=f"Node '{title}' ({task_type}) added",
            )
            return _node_to_dict(n)

    def get_node(self, node_id: int) -> Optional[Dict[str, Any]]:
        with get_db_session() as db:
            n = db.query(FactoryNode).filter(FactoryNode.id == node_id).first()
            return _node_to_dict(n) if n else None

    def get_nodes(self, project_id: int) -> List[Dict[str, Any]]:
        with get_db_session() as db:
            rows = db.query(FactoryNode).filter(
                FactoryNode.project_id == project_id
            ).order_by(FactoryNode.id).all()
            return [_node_to_dict(r) for r in rows]

    def delete_node(self, node_id: int) -> bool:
        with get_db_session() as db:
            n = db.query(FactoryNode).filter(FactoryNode.id == node_id).first()
            if not n:
                return False
            project_id = n.project_id
            # Remove edges referencing this node
            db.query(FactoryEdge).filter(
                (FactoryEdge.from_node_id == node_id) |
                (FactoryEdge.to_node_id == node_id)
            ).delete()
            db.delete(n)
            self._log_event(db, project_id, event_type="node_deleted",
                            node_id=node_id, message="Node deleted")
            return True

    # ── Edge management ────────────────────────────────────────

    def add_edge(
        self, project_id: int, from_node_id: int, to_node_id: int
    ) -> Dict[str, Any]:
        with get_db_session() as db:
            # Validate nodes exist and belong to project
            from_node = db.query(FactoryNode).filter(
                FactoryNode.id == from_node_id,
                FactoryNode.project_id == project_id,
            ).first()
            to_node = db.query(FactoryNode).filter(
                FactoryNode.id == to_node_id,
                FactoryNode.project_id == project_id,
            ).first()
            if not from_node:
                raise ValueError(f"From node {from_node_id} not found")
            if not to_node:
                raise ValueError(f"To node {to_node_id} not found")

            # Check for duplicates
            existing = db.query(FactoryEdge).filter(
                FactoryEdge.project_id == project_id,
                FactoryEdge.from_node_id == from_node_id,
                FactoryEdge.to_node_id == to_node_id,
            ).first()
            if existing:
                return _edge_to_dict(existing)

            # Check for cycles (simple DFS)
            if self._would_create_cycle(db, project_id, from_node_id, to_node_id):
                raise ValueError("Adding this edge would create a cycle")

            edge = FactoryEdge(
                project_id=project_id,
                from_node_id=from_node_id,
                to_node_id=to_node_id,
            )
            db.add(edge)
            db.flush()

            # Update dependencies list on to_node
            if to_node_id not in (to_node.dependencies or []):
                to_node.dependencies = list(to_node.dependencies or []) + [from_node_id]
                to_node.updated_at = _now()

            return _edge_to_dict(edge)

    def remove_edge(self, edge_id: int) -> bool:
        with get_db_session() as db:
            edge = db.query(FactoryEdge).filter(FactoryEdge.id == edge_id).first()
            if not edge:
                return False
            # Update dependencies on to_node
            to_node = db.query(FactoryNode).filter(
                FactoryNode.id == edge.to_node_id
            ).first()
            if to_node and edge.from_node_id in (to_node.dependencies or []):
                deps = list(to_node.dependencies)
                deps.remove(edge.from_node_id)
                to_node.dependencies = deps
                to_node.updated_at = _now()
            db.delete(edge)
            return True

    def _would_create_cycle(
        self, db: Session, project_id: int, from_node_id: int, to_node_id: int
    ) -> bool:
        """DFS from to_node_id — if we can reach from_node_id, it's a cycle."""
        visited = set()
        stack = [to_node_id]
        while stack:
            current = stack.pop()
            if current == from_node_id:
                return True
            if current in visited:
                continue
            visited.add(current)
            # Find nodes that have edges FROM current (current -> next)
            next_ids = [
                e.to_node_id for e in
                db.query(FactoryEdge).filter(FactoryEdge.from_node_id == current).all()
            ]
            stack.extend(next_ids)
        return False

    # ── DAG view ──────────────────────────────────────────────

    def get_dag(self, project_id: int) -> Dict[str, Any]:
        with get_db_session() as db:
            p = db.query(FactoryProject).filter(FactoryProject.id == project_id).first()
            if not p:
                return {"error": f"Project {project_id} not found"}

            nodes = db.query(FactoryNode).filter(
                FactoryNode.project_id == project_id
            ).order_by(FactoryNode.id).all()

            edges = db.query(FactoryEdge).filter(
                FactoryEdge.project_id == project_id
            ).all()

            # Build adjacency info
            tasks = []
            for n in nodes:
                incoming = [e.from_node_id for e in edges if e.to_node_id == n.id]
                outgoing = [e.to_node_id for e in edges if e.from_node_id == n.id]
                tasks.append({
                    **_node_to_dict(n),
                    "dependencies": incoming,
                    "dependents": outgoing,
                })

            return {
                "project": _project_to_dict(p),
                "tasks": tasks,
                "edges": [_edge_to_dict(e) for e in edges],
                "total_tasks": len(tasks),
                "completed_tasks": sum(1 for t in tasks if t["status"] == "completed"),
                "failed_tasks": sum(1 for t in tasks if t["status"] == "failed"),
                "running_tasks": sum(1 for t in tasks if t["status"] == "running"),
                "pending_tasks": sum(1 for t in tasks if t["status"] in ("pending", "ready")),
            }

    # ── Task status / execution ───────────────────────────────

    def update_task_status(
        self,
        node_id: int,
        new_status: str,
        result: Optional[Any] = None,
        error: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        with get_db_session() as db:
            n = db.query(FactoryNode).filter(FactoryNode.id == node_id).first()
            if not n:
                return None

            allowed = _VALID_NODE_TRANSITIONS.get(n.status, set())
            if new_status not in allowed:
                raise ValueError(
                    f"Invalid node transition: {n.status} -> {new_status}. "
                    f"Allowed: {sorted(allowed)}"
                )

            old = n.status
            n.status = new_status
            if result is not None:
                n.result = result
            if error is not None:
                n.error = error
            n.updated_at = _now()

            self._log_event(
                db, n.project_id,
                event_type="task_status_changed",
                node_id=node_id,
                message=f"Task '{n.title}' ({n.task_type}): {old} -> {new_status}",
            )
            return _node_to_dict(n)

    def retry_task(self, node_id: int) -> Optional[Dict[str, Any]]:
        with get_db_session() as db:
            n = db.query(FactoryNode).filter(FactoryNode.id == node_id).first()
            if not n:
                return None
            if n.status not in ("failed", "human_intervention"):
                raise ValueError(f"Can only retry failed or blocked tasks (current: {n.status})")
            n.status = "ready"
            n.error = None
            n.retries = (n.retries or 0) + 1
            n.updated_at = _now()
            self._log_event(db, n.project_id, event_type="task_retried",
                            node_id=node_id, message=f"Task retried (attempt {n.retries})")
            return _node_to_dict(n)

    def set_task_error(self, node_id: int, error: str) -> None:
        """Set the error message on a node without changing its status."""
        with get_db_session() as db:
            n = db.query(FactoryNode).filter(FactoryNode.id == node_id).first()
            if n:
                n.error = error
                n.updated_at = _now()

    def set_task_progress(self, node_id: int, phase: str, attempt: int = 0,
                          max_attempts: int = 0, detail: str = "") -> None:
        """Update the live progress on a running task so the frontend can show it."""
        with get_db_session() as db:
            n = db.query(FactoryNode).filter(FactoryNode.id == node_id).first()
            if n:
                parts = [phase]
                if attempt and max_attempts:
                    parts.append(f"attempt {attempt}/{max_attempts}")
                if detail:
                    parts.append(detail)
                n.error = " — ".join(parts)
                n.updated_at = _now()

    def requeue_stale_running(self, project_id: int, max_age_seconds: int) -> int:
        """Re-queue tasks stuck in 'running' longer than max_age_seconds.

        Used by the orchestrator at startup to recover tasks that were left
        'running' because a previous orchestrator task was cancelled/killed
        mid-produce. Bypasses the normal transition table (running->ready is
        normally invalid) since this is a recovery/admin operation.
        Returns the number of tasks re-queued.
        """
        threshold = _now() - timedelta(seconds=max_age_seconds)
        count = 0
        with get_db_session() as db:
            stale = db.query(FactoryNode).filter(
                FactoryNode.project_id == project_id,
                FactoryNode.status == "running",
                FactoryNode.updated_at < threshold,
            ).all()
            for n in stale:
                n.status = "ready"
                n.error = "re-queued (previous run was stale)"
                n.updated_at = _now()
                count += 1
                self._log_event(
                    db, project_id, event_type="task_requeued_stale",
                    node_id=n.id,
                    message=f"Task '{n.title}' re-queued after stale running state",
                )
        return count

    def mark_ready_tasks(self, project_id: int) -> int:
        """Mark root pending tasks (no incoming edges) as ready. Returns count."""
        with get_db_session() as db:
            edges = db.query(FactoryEdge).filter(
                FactoryEdge.project_id == project_id
            ).all()
            has_incoming = {e.to_node_id for e in edges}
            nodes = db.query(FactoryNode).filter(
                FactoryNode.project_id == project_id,
                FactoryNode.status == "pending",
            ).all()
            count = 0
            for n in nodes:
                if n.id not in has_incoming:
                    n.status = "ready"
                    n.updated_at = _now()
                    count += 1
            return count

    def start_project(self, project_id: int) -> Dict[str, Any]:
        """Transition project to running and mark root tasks as ready."""
        with get_db_session() as db:
            p = db.query(FactoryProject).filter(FactoryProject.id == project_id).first()
            if not p:
                raise ValueError(f"Project {project_id} not found")
            if p.status not in ("planning", "queued", "failed"):
                raise ValueError(
                    f"Cannot start project in '{p.status}' status"
                )

            # Validate: must have at least one node
            nodes = db.query(FactoryNode).filter(
                FactoryNode.project_id == project_id
            ).all()
            if not nodes:
                raise ValueError("Cannot start project with no tasks")

            p.status = "running"
            p.updated_at = _now()

            # Find root nodes (no incoming edges) and set to "ready"
            edges = db.query(FactoryEdge).filter(
                FactoryEdge.project_id == project_id
            ).all()
            has_incoming = {e.to_node_id for e in edges}

            roots_ready = 0
            for n in nodes:
                if n.id not in has_incoming and n.status in ("pending", "ready"):
                    n.status = "ready"
                    n.updated_at = _now()
                    roots_ready += 1

            self._log_event(
                db, project_id,
                event_type="project_started",
                message=f"Project started — {roots_ready} root tasks ready",
            )
            return _project_to_dict(p)

    def get_next_ready_tasks(self, project_id: int) -> List[Dict[str, Any]]:
        with get_db_session() as db:
            ready = db.query(FactoryNode).filter(
                FactoryNode.project_id == project_id,
                FactoryNode.status == "ready",
            ).order_by(FactoryNode.priority.desc(), FactoryNode.id).all()
            return [_node_to_dict(r) for r in ready]

    def complete_task(
        self, node_id: int, result: Optional[Any] = None
    ) -> Optional[Dict[str, Any]]:
        with get_db_session() as db:
            n = db.query(FactoryNode).filter(FactoryNode.id == node_id).first()
            if not n:
                return None
            if n.status not in ("running", "ready", "human_intervention", "failed"):
                raise ValueError(
                    f"Cannot complete task in '{n.status}' status"
                )

            n.status = "completed"
            n.result = result
            n.error = None
            n.updated_at = _now()
            self._log_event(
                db, n.project_id, event_type="task_completed",
                node_id=node_id, message=f"Task '{n.title}' completed",
            )

            # Check if dependents should become ready
            edges = db.query(FactoryEdge).filter(
                FactoryEdge.from_node_id == node_id
            ).all()

            for edge in edges:
                dep_node = db.query(FactoryNode).filter(
                    FactoryNode.id == edge.to_node_id
                ).first()
                if not dep_node or dep_node.status != "pending":
                    continue

                # Check if ALL dependencies of dep_node are completed
                all_deps = db.query(FactoryEdge).filter(
                    FactoryEdge.to_node_id == dep_node.id
                ).all()

                all_done = all(
                    dep.status == "completed"
                    for dep in db.query(FactoryNode).filter(
                        FactoryNode.id.in_([e.from_node_id for e in all_deps])
                    ).all()
                )

                if all_done:
                    dep_node.status = "ready"
                    dep_node.updated_at = _now()
                    self._log_event(
                        db, n.project_id, event_type="task_ready",
                        node_id=dep_node.id,
                        message=f"Task '{dep_node.title}' is now ready",
                    )

            # Check if ALL project tasks are done
            self._check_project_completion(db, n.project_id)

            return _node_to_dict(n)

    def fail_task(
        self, node_id: int, error: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        with get_db_session() as db:
            n = db.query(FactoryNode).filter(FactoryNode.id == node_id).first()
            if not n:
                return None
            n.status = "failed"
            n.error = error
            n.updated_at = _now()
            self._log_event(
                db, n.project_id, event_type="task_failed",
                node_id=node_id, message=f"Task '{n.title}' failed: {error}",
            )
            return _node_to_dict(n)

    def cancel_project(self, project_id: int) -> Optional[Dict[str, Any]]:
        with get_db_session() as db:
            p = db.query(FactoryProject).filter(FactoryProject.id == project_id).first()
            if not p:
                return None
            if p.status in ("completed", "cancelled"):
                raise ValueError(
                    f"Cannot cancel project in '{p.status}' status"
                )
            p.status = "cancelled"
            p.updated_at = _now()

            # Cancel all non-completed tasks
            db.query(FactoryNode).filter(
                FactoryNode.project_id == project_id,
                FactoryNode.status != "completed",
            ).update({"status": "cancelled", "updated_at": _now()})

            self._log_event(db, project_id, event_type="project_cancelled",
                            message="Project cancelled")
            return _project_to_dict(p)

    def reset_project(self, project_id: int) -> Optional[Dict[str, Any]]:
        with get_db_session() as db:
            p = db.query(FactoryProject).filter(FactoryProject.id == project_id).first()
            if not p:
                return None
            p.status = "planning"
            p.updated_at = _now()

            # Reset all nodes
            db.query(FactoryNode).filter(
                FactoryNode.project_id == project_id
            ).update({
                "status": "pending",
                "result": None,
                "error": None,
                "retries": 0,
                "updated_at": _now(),
            })

            self._log_event(db, project_id, event_type="project_reset",
                            message="Project reset to planning")
            return _project_to_dict(p)

    def pause_project(self, project_id: int) -> Optional[Dict[str, Any]]:
        with get_db_session() as db:
            p = db.query(FactoryProject).filter(FactoryProject.id == project_id).first()
            if not p:
                return None
            if p.status != "running":
                raise ValueError(f"Cannot pause project in '{p.status}' status")
            p.status = "paused"
            p.updated_at = _now()
            self._log_event(db, project_id, event_type="project_paused",
                            message="Project paused")
            return _project_to_dict(p)

    def resume_project(self, project_id: int) -> Optional[Dict[str, Any]]:
        with get_db_session() as db:
            p = db.query(FactoryProject).filter(FactoryProject.id == project_id).first()
            if not p:
                return None
            if p.status != "paused":
                raise ValueError(f"Cannot resume project in '{p.status}' status")
            p.status = "running"
            p.updated_at = _now()
            self._log_event(db, project_id, event_type="project_resumed",
                            message="Project resumed")
            return _project_to_dict(p)

    def restart_project(self, project_id: int, mode: str = "partial") -> Optional[Dict[str, Any]]:
        """Reset tasks and restart. mode='full' resets everything to pending;
        'partial' only resets failed/human_intervention tasks."""
        with get_db_session() as db:
            p = db.query(FactoryProject).filter(FactoryProject.id == project_id).first()
            if not p:
                return None

            if mode == "full":
                db.query(FactoryNode).filter(
                    FactoryNode.project_id == project_id
                ).update({
                    "status": "pending", "result": None, "error": None,
                    "retries": 0, "updated_at": _now(),
                })
            else:
                db.query(FactoryNode).filter(
                    FactoryNode.project_id == project_id,
                    FactoryNode.status.in_(["failed", "human_intervention", "cancelled"]),
                ).update({
                    "status": "pending", "result": None, "error": None,
                    "retries": 0, "updated_at": _now(),
                })

            p.status = "queued"
            p.updated_at = _now()

            # Re-mark root tasks as ready
            edges = db.query(FactoryEdge).filter(
                FactoryEdge.project_id == project_id
            ).all()
            has_incoming = {e.to_node_id for e in edges}
            nodes = db.query(FactoryNode).filter(
                FactoryNode.project_id == project_id
            ).all()
            for n in nodes:
                if n.id not in has_incoming and n.status == "pending":
                    n.status = "ready"
                    n.updated_at = _now()

            self._log_event(db, project_id, event_type="project_restarted",
                            message=f"Project restarted ({mode})")
            return _project_to_dict(p)

    # ── Internal helpers ──────────────────────────────────────

    def _log_event(
        self,
        db: Session,
        project_id: int,
        event_type: str,
        node_id: Optional[int] = None,
        message: str = "",
        metadata: Optional[Dict] = None,
    ) -> None:
        ev = FactoryEvent(
            project_id=project_id,
            node_id=node_id,
            event_type=event_type,
            message=message,
            metadata_=metadata,
            created_at=_now(),
        )
        db.add(ev)

    def _log_event_safe(self, project_id: int, node_id: Optional[int],
                        message: str, event_type: str = "task_update") -> None:
        """Log an event using its own session (for callers without a db handle)."""
        with get_db_session() as db:
            self._log_event(db, project_id, event_type=event_type,
                            node_id=node_id, message=message)

    def get_execution_log(self, project_id: int) -> List[Dict[str, Any]]:
        with get_db_session() as db:
            events = db.query(FactoryEvent).filter(
                FactoryEvent.project_id == project_id
            ).order_by(FactoryEvent.created_at).all()
            return [_event_to_dict(e) for e in events]

    def _check_project_completion(self, db: Session, project_id: int) -> None:
        """Auto-complete or auto-fail the project when all tasks are done."""
        p = db.query(FactoryProject).filter(
            FactoryProject.id == project_id,
            FactoryProject.status == "running",
        ).first()
        if not p:
            return

        nodes = db.query(FactoryNode).filter(
            FactoryNode.project_id == project_id
        ).all()

        statuses = {n.status for n in nodes}
        if not statuses:
            return

        # All completed
        if statuses == {"completed"}:
            p.status = "completed"
            p.updated_at = _now()
            self._log_event(db, project_id, event_type="project_completed",
                            message="All tasks completed — project done")
        # No running/ready/pending — some failed
        elif not statuses & {"running", "ready", "pending"}:
            p.status = "failed"
            p.updated_at = _now()
            self._log_event(db, project_id, event_type="project_failed",
                            message="No tasks remain actionable — project failed")

    # ── Convenience for routes ────────────────────────────────

    def _get_dag_tasks(self, project_id: int) -> List[Dict]:
        """Lightweight DAG tasks list (for Kanban-style views)."""
        dag = self.get_dag(project_id)
        return dag.get("tasks", [])
