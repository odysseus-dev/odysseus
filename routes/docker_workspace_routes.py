"""Docker Workspace API routes — lifecycle management for AI workspaces."""
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from src.auth_helpers import get_current_user


class CreateWorkspaceBody(BaseModel):
    name: str
    template_image: str = "python:3.11-alpine"
    description: str = ""
    resource_limits: dict = {}
    bound_agent_id: Optional[str] = None


class ExecuteBody(BaseModel):
    command: str
    guard_token: Optional[str] = None


class BindAgentBody(BaseModel):
    agent_id: str


def setup_docker_workspace_routes(workspace_manager):
    router = APIRouter(prefix="/api/docker-workspaces", tags=["docker-workspaces"])

    def _owner(request: Request) -> str:
        owner = get_current_user(request)
        if not owner:
            raise HTTPException(status_code=401, detail="Authentication required")
        return owner

    @router.get("/available")
    def check_available(request: Request):
        """Check if Docker is available on this host."""
        _owner(request)
        return {"available": workspace_manager.is_available()}

    @router.get("")
    def list_workspaces(request: Request, include_destroyed: bool = False):
        """List all workspaces owned by the current user."""
        owner = _owner(request)
        workspaces = workspace_manager.list_workspaces(owner=owner, include_destroyed=include_destroyed)
        return {"workspaces": workspaces, "count": len(workspaces)}

    @router.get("/{ws_id}")
    def get_workspace(ws_id: str, request: Request):
        """Get workspace details."""
        owner = _owner(request)
        ws = workspace_manager.get_workspace(ws_id)
        if not ws:
            raise HTTPException(status_code=404, detail=f"Workspace '{ws_id}' not found")
        if ws["owner"] != owner:
            raise HTTPException(status_code=403, detail="Access denied")
        return ws

    @router.get("/{ws_id}/status")
    def sync_workspace_status(ws_id: str, request: Request):
        """Sync and return the current Docker container status."""
        owner = _owner(request)
        ws = workspace_manager.get_workspace(ws_id)
        if not ws:
            raise HTTPException(status_code=404, detail="Workspace not found")
        if ws["owner"] != owner:
            raise HTTPException(status_code=403, detail="Access denied")

        if not workspace_manager.is_available():
            return {"status": ws["status"], "synced": False}

        status = workspace_manager.sync_status(ws_id)
        return {"status": status, "synced": True}

    @router.post("")
    def create_workspace(body: CreateWorkspaceBody, request: Request):
        """
        Create a new Docker workspace container with a persistent Named Volume.
        The workspace lifecycle is completely independent of chat history.
        """
        owner = _owner(request)

        if not workspace_manager.is_available():
            raise HTTPException(
                status_code=503,
                detail="Docker is not available on this host. Install Docker and ensure the daemon is running.",
            )

        if not body.name.strip():
            raise HTTPException(status_code=400, detail="Workspace name is required")

        try:
            ws = workspace_manager.create_workspace(
                name=body.name.strip(),
                template_image=body.template_image,
                resource_limits=body.resource_limits or None,
                owner=owner,
                description=body.description,
                bound_agent_id=body.bound_agent_id,
            )
            return ws
        except RuntimeError as e:
            raise HTTPException(status_code=503, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to create workspace: {e}")

    @router.post("/{ws_id}/start")
    def start_workspace(ws_id: str, request: Request):
        """Start a stopped workspace container."""
        owner = _owner(request)
        try:
            ok = workspace_manager.start_workspace(ws_id, owner=owner)
            return {"success": ok, "ws_id": ws_id}
        except (ValueError, PermissionError) as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/{ws_id}/stop")
    def stop_workspace(ws_id: str, request: Request):
        """Stop a workspace container (data is preserved)."""
        owner = _owner(request)
        try:
            ok = workspace_manager.stop_workspace(ws_id, owner=owner)
            return {"success": ok, "ws_id": ws_id}
        except (ValueError, PermissionError) as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.delete("/{ws_id}")
    def destroy_workspace(ws_id: str, request: Request):
        """
        DESTRUCTIVE: Permanently destroy a workspace and all its data.
        This removes the Docker container AND the Named Volume. Cannot be undone.
        """
        owner = _owner(request)
        try:
            ok = workspace_manager.destroy_workspace(ws_id, owner=owner)
            return {"success": ok, "ws_id": ws_id, "warning": "All workspace data has been permanently deleted"}
        except (ValueError, PermissionError) as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/{ws_id}/exec")
    def execute_in_workspace(ws_id: str, body: ExecuteBody, request: Request):
        """
        Execute a command inside the workspace container.
        HIGH-risk commands are blocked. MEDIUM-risk commands return a pending_approval
        response with a guard_token; the client must call POST /api/guard/decide/{token}
        with decision="approve" then re-submit with guard_token in the body.
        """
        from src.execution_guard import classify_command
        from src.guard_approvals import create_approval, consume

        owner = _owner(request)

        if body.guard_token:
            record = consume(body.guard_token, owner)
            if record is None:
                raise HTTPException(status_code=403, detail="Guard token invalid, expired, or not approved")
            command = record["command"]
        else:
            command = body.command
            risk = classify_command(command)
            if risk["blocked"]:
                raise HTTPException(status_code=403, detail=f"Command blocked: {risk['reason']}")
            if risk["risk"] == "MEDIUM":
                token = create_approval(command, owner, ws_id)
                return {
                    "status": "pending_approval",
                    "token": token,
                    "command": command,
                    "reason": risk["reason"],
                }

        try:
            result = workspace_manager.execute_in_workspace(ws_id, command, owner=owner)
            return result
        except PermissionError as e:
            raise HTTPException(status_code=403, detail=str(e))
        except (ValueError, RuntimeError) as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.get("/{ws_id}/logs")
    def get_workspace_logs(ws_id: str, request: Request, tail: int = 100):
        """Get recent container logs from a workspace."""
        owner = _owner(request)
        ws = workspace_manager.get_workspace(ws_id)
        if not ws:
            raise HTTPException(status_code=404, detail="Workspace not found")
        if ws["owner"] != owner:
            raise HTTPException(status_code=403, detail="Access denied")
        logs = workspace_manager.get_workspace_logs(ws_id, tail=min(tail, 500))
        return {"logs": logs, "ws_id": ws_id}

    @router.post("/{ws_id}/bind-agent")
    def bind_agent_to_workspace(ws_id: str, body: BindAgentBody, request: Request):
        """Associate an AI agent session with a workspace for context routing."""
        owner = _owner(request)
        try:
            ok = workspace_manager.bind_agent(ws_id, body.agent_id, owner=owner)
            return {"success": ok, "ws_id": ws_id, "agent_id": body.agent_id}
        except PermissionError as e:
            raise HTTPException(status_code=403, detail=str(e))

    return router
