"""
factory_routes.py — Project Factory API routes (SQLAlchemy rewrite)

All routes are stateless — they instantiate FactoryService which
manages its own sessions. No raw sqlite3, no shared connections.
"""

import logging

from fastapi import APIRouter, HTTPException, Request

from services.factory_service import FactoryService
from services.factory_orchestrator import plan_project, launch, stop as stop_orchestrator

logger = logging.getLogger(__name__)

svc = FactoryService()  # stateless — no shared conn


def setup_factory_routes() -> APIRouter:
    router = APIRouter(prefix="/api/factory", tags=["factory"])

    # ── Projects ───────────────────────────────────────────────

    @router.post("/projects")
    async def create_project(request: Request):
        """Create a new Factory project and plan tasks via LLM (synchronous)."""
        body = await request.json()
        description = body.get("description")
        if not description:
            raise HTTPException(400, "description is required")
        owner = body.get("owner", "default")
        try:
            project = svc.create_project(description=description, owner=owner)
        except ValueError as e:
            raise HTTPException(400, str(e))
        except Exception as e:
            logger.exception("create_project failed")
            raise HTTPException(500, str(e))

        # Run planning synchronously so tasks exist when the response returns.
        # Typically takes 10-30s. The orchestrator (task execution) launches
        # as a background task inside plan_project.
        pid = project["id"]
        try:
            await plan_project(pid, owner=owner)
        except Exception as e:
            logger.exception(f"Factory: planning failed for project {pid}: {e}")

        # Return the project with tasks populated
        result = svc.get_project(pid)
        return result or project

    @router.get("/projects")
    async def list_projects(owner: str = "default"):
        """List all factory projects."""
        try:
            return svc.get_projects(owner=owner)
        except Exception as e:
            logger.exception("list_projects failed")
            raise HTTPException(500, str(e))

    @router.get("/projects/{project_id}")
    async def get_project(project_id: int):
        """Get a single project by ID."""
        result = svc.get_project(project_id)
        if not result:
            raise HTTPException(404, f"Project {project_id} not found")
        return result

    @router.put("/projects/{project_id}")
    async def update_project(project_id: int, request: Request):
        """Update project metadata (title, description, model)."""
        body = await request.json()
        try:
            result = svc.update_project(
                project_id=project_id,
                title=body.get("title"),
                description=body.get("description"),
                model=body.get("model"),
            )
            if not result:
                raise HTTPException(404, f"Project {project_id} not found")
            return result
        except ValueError as e:
            raise HTTPException(400, str(e))
        except Exception as e:
            logger.exception("update_project failed")
            raise HTTPException(500, str(e))

    @router.delete("/projects/{project_id}")
    async def delete_project(project_id: int):
        """Delete a project and all its nodes, edges, and events."""
        if not svc.delete_project(project_id):
            raise HTTPException(404, f"Project {project_id} not found")
        return {"deleted": True}

    # ── Project lifecycle ───────────────────────────────────────

    @router.post("/projects/{project_id}/start")
    async def start_project(project_id: int):
        """Start a project — transitions to running, marks root tasks ready."""
        try:
            return svc.start_project(project_id)
        except ValueError as e:
            raise HTTPException(400, str(e))
        except Exception as e:
            logger.exception("start_project failed")
            raise HTTPException(500, str(e))

    @router.post("/projects/{project_id}/cancel")
    async def cancel_project(project_id: int):
        """Cancel a project and all its pending/running tasks."""
        try:
            result = svc.cancel_project(project_id)
            if not result:
                raise HTTPException(404, f"Project {project_id} not found")
            return result
        except ValueError as e:
            raise HTTPException(400, str(e))

    @router.post("/projects/{project_id}/reset")
    async def reset_project(project_id: int):
        """Reset a project back to planning state."""
        try:
            result = svc.reset_project(project_id)
            if not result:
                raise HTTPException(404, f"Project {project_id} not found")
            return result
        except Exception as e:
            logger.exception("reset_project failed")
            raise HTTPException(500, str(e))

    @router.post("/projects/{project_id}/status")
    async def set_project_status(project_id: int, request: Request):
        """Manually set project status (with validation)."""
        body = await request.json()
        new_status = body.get("status")
        if not new_status:
            raise HTTPException(400, "status is required")
        try:
            result = svc.set_project_status(project_id, new_status)
            if not result:
                raise HTTPException(404, f"Project {project_id} not found")
            return result
        except ValueError as e:
            raise HTTPException(400, str(e))

    @router.post("/projects/{project_id}/pause")
    async def pause_project(project_id: int):
        """Pause a running project."""
        try:
            result = svc.pause_project(project_id)
            if not result:
                raise HTTPException(404, f"Project {project_id} not found")
            return result
        except ValueError as e:
            raise HTTPException(400, str(e))

    @router.post("/projects/{project_id}/resume")
    async def resume_project(project_id: int, request: Request):
        """Resume a paused project and restart the orchestrator."""
        try:
            body = {}
            try:
                body = await request.json()
            except Exception:
                pass
            owner = body.get("owner", "default")
            result = svc.resume_project(project_id)
            if not result:
                raise HTTPException(404, f"Project {project_id} not found")
            launch(project_id, owner=owner)
            return result
        except ValueError as e:
            raise HTTPException(400, str(e))

    @router.post("/projects/{project_id}/restart")
    async def restart_project(project_id: int, request: Request):
        """Restart a project — resets failed/stuck tasks and relaunches."""
        try:
            body = {}
            try:
                body = await request.json()
            except Exception:
                pass
            mode = body.get("mode", "partial")
            owner = body.get("owner", "default")
            result = svc.restart_project(project_id, mode=mode)
            if not result:
                raise HTTPException(404, f"Project {project_id} not found")
            svc.set_project_status(project_id, "running")
            launch(project_id, owner=owner)
            return result
        except ValueError as e:
            raise HTTPException(400, str(e))
        except Exception as e:
            logger.exception("restart_project failed")
            raise HTTPException(500, str(e))

    # ── Nodes / Tasks ──────────────────────────────────────────

    @router.post("/projects/{project_id}/nodes")
    async def add_node(project_id: int, request: Request):
        """Add a task node to a project."""
        body = await request.json()
        task_type = body.get("task_type")
        if not task_type:
            raise HTTPException(400, "task_type is required")
        try:
            return svc.add_node(
                project_id=project_id,
                task_type=task_type,
                title=body.get("title", ""),
                description=body.get("description", ""),
                assigned_agent=body.get("assigned_agent", ""),
                dependencies=body.get("dependencies", []),
                priority=body.get("priority", 0),
            )
        except ValueError as e:
            raise HTTPException(400, str(e))
        except Exception as e:
            logger.exception("add_node failed")
            raise HTTPException(500, str(e))

    @router.get("/projects/{project_id}/nodes")
    async def list_nodes(project_id: int):
        """List all nodes for a project."""
        try:
            return svc.get_nodes(project_id)
        except Exception as e:
            logger.exception("list_nodes failed")
            raise HTTPException(500, str(e))

    @router.get("/nodes/{node_id}")
    async def get_node(node_id: int):
        """Get a single node by ID."""
        result = svc.get_node(node_id)
        if not result:
            raise HTTPException(404, f"Node {node_id} not found")
        return result

    @router.delete("/nodes/{node_id}")
    async def delete_node(node_id: int):
        """Delete a node and its edges."""
        if not svc.delete_node(node_id):
            raise HTTPException(404, f"Node {node_id} not found")
        return {"deleted": True}

    # ── Task status ────────────────────────────────────────────

    @router.post("/nodes/{node_id}/status")
    async def set_task_status(node_id: int, request: Request):
        """Update a task's status (with validation)."""
        body = await request.json()
        new_status = body.get("status")
        if not new_status:
            raise HTTPException(400, "status is required")
        try:
            result = svc.update_task_status(
                node_id=node_id,
                new_status=new_status,
                result=body.get("result"),
                error=body.get("error"),
            )
            if not result:
                raise HTTPException(404, f"Node {node_id} not found")
            return result
        except ValueError as e:
            raise HTTPException(400, str(e))

    @router.post("/nodes/{node_id}/retry")
    async def retry_task(node_id: int):
        """Retry a failed task."""
        try:
            result = svc.retry_task(node_id)
            if not result:
                raise HTTPException(404, f"Node {node_id} not found")
            return result
        except ValueError as e:
            raise HTTPException(400, str(e))

    @router.post("/tasks/{task_id}/retry")
    async def retry_task_alias(task_id: int):
        """Alias — frontend uses /tasks/{id}/retry."""
        return await retry_task(task_id)

    @router.post("/nodes/{node_id}/complete")
    async def complete_task(node_id: int, request: Request = None):
        """Mark a task as completed with optional result."""
        body = {}
        try:
            if request:
                body = await request.json()
        except Exception:
            pass
        try:
            result = svc.complete_task(
                node_id=node_id,
                result=body.get("result"),
            )
            if not result:
                raise HTTPException(404, f"Node {node_id} not found")
            return result
        except ValueError as e:
            raise HTTPException(400, str(e))
        except Exception as e:
            logger.exception("complete_task failed")
            raise HTTPException(500, str(e))

    @router.post("/nodes/{node_id}/fail")
    async def fail_task(node_id: int, request: Request):
        """Mark a task as failed with an error message."""
        body = await request.json()
        try:
            result = svc.fail_task(
                node_id=node_id,
                error=body.get("error"),
            )
            if not result:
                raise HTTPException(404, f"Node {node_id} not found")
            return result
        except Exception as e:
            logger.exception("fail_task failed")
            raise HTTPException(500, str(e))

    # ── Edges ─────────────────────────────────────────────────

    @router.post("/projects/{project_id}/edges")
    async def add_edge(project_id: int, request: Request):
        """Add a dependency edge between two nodes."""
        body = await request.json()
        from_node_id = body.get("from_node_id")
        to_node_id = body.get("to_node_id")
        if from_node_id is None or to_node_id is None:
            raise HTTPException(400, "from_node_id and to_node_id are required")
        try:
            return svc.add_edge(project_id, from_node_id, to_node_id)
        except ValueError as e:
            raise HTTPException(400, str(e))
        except Exception as e:
            logger.exception("add_edge failed")
            raise HTTPException(500, str(e))

    @router.delete("/edges/{edge_id}")
    async def remove_edge(edge_id: int):
        """Remove a dependency edge."""
        if not svc.remove_edge(edge_id):
            raise HTTPException(404, f"Edge {edge_id} not found")
        return {"deleted": True}

    # ── DAG / Kanban view ──────────────────────────────────────

    @router.get("/projects/{project_id}/dag")
    async def get_dag(project_id: int):
        """Get full DAG view with task stats."""
        return svc.get_dag(project_id)

    @router.get("/projects/{project_id}/tasks/ready")
    async def get_ready_tasks(project_id: int):
        """Get tasks that are ready to execute."""
        try:
            return svc.get_next_ready_tasks(project_id)
        except Exception as e:
            logger.exception("get_ready_tasks failed")
            raise HTTPException(500, str(e))

    # ── Execution Log ───────────────────────────────────────────

    @router.get("/projects/{project_id}/log")
    async def get_execution_log(project_id: int):
        """Get the execution event log for a project."""
        try:
            return svc.get_execution_log(project_id)
        except Exception as e:
            logger.exception("get_execution_log failed")
            raise HTTPException(500, str(e))

    return router
