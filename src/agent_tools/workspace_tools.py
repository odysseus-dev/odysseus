import json
import logging

logger = logging.getLogger(__name__)


def _get_workspace_manager():
    try:
        from src.workspace_manager import WorkspaceManager
        return WorkspaceManager()
    except Exception as e:
        logger.warning("WorkspaceManager unavailable: %s", e)
        return None


class CreateWorkspaceTool:
    async def execute(self, content: str, ctx: dict) -> dict:
        try:
            args = json.loads(content) if content.strip().startswith("{") else {}
        except (json.JSONDecodeError, TypeError):
            args = {}

        name = (args.get("name") or "").strip()
        if not name:
            return {"error": "create_workspace: name is required", "exit_code": 1}

        image = args.get("image") or "python:3.11-alpine"
        description = args.get("description") or ""
        memory = args.get("memory") or "512m"
        cpu = float(args.get("cpu") or 0.5)
        owner = ctx.get("owner") or "agent"

        wm = _get_workspace_manager()
        if wm is None:
            return {"error": "Docker workspaces are not available on this host", "exit_code": 1}

        try:
            ws = wm.create_workspace(
                name=name,
                template_image=image,
                description=description,
                resource_limits={"memory": memory, "cpu": cpu},
                owner=owner,
            )
            return {
                "workspace_id": ws["id"],
                "name": ws["name"],
                "status": ws["status"],
                "image": ws["template_image"],
                "message": f"Workspace '{name}' created and started (id: {ws['id']})",
            }
        except Exception as e:
            return {"error": str(e), "exit_code": 1}


class ListWorkspacesTool:
    async def execute(self, content: str, ctx: dict) -> dict:
        owner = ctx.get("owner") or "agent"
        wm = _get_workspace_manager()
        if wm is None:
            return {"error": "Docker workspaces are not available on this host", "exit_code": 1}

        try:
            workspaces = wm.list_workspaces(owner=owner)
            return {
                "workspaces": [
                    {
                        "id": w["id"],
                        "name": w["name"],
                        "status": w["status"],
                        "image": w.get("template_image", ""),
                        "description": w.get("description", ""),
                    }
                    for w in workspaces
                ],
                "count": len(workspaces),
            }
        except Exception as e:
            return {"error": str(e), "exit_code": 1}


class ExecuteInWorkspaceTool:
    async def execute(self, content: str, ctx: dict) -> dict:
        try:
            args = json.loads(content) if content.strip().startswith("{") else {}
        except (json.JSONDecodeError, TypeError):
            args = {}

        ws_id = (args.get("workspace_id") or "").strip()
        command = (args.get("command") or "").strip()
        if not ws_id:
            return {"error": "execute_in_workspace: workspace_id is required", "exit_code": 1}
        if not command:
            return {"error": "execute_in_workspace: command is required", "exit_code": 1}

        owner = ctx.get("owner") or "agent"
        wm = _get_workspace_manager()
        if wm is None:
            return {"error": "Docker workspaces are not available on this host", "exit_code": 1}

        try:
            result = wm.execute_in_workspace(ws_id=ws_id, command=command, owner=owner)
            return {
                "output": result.get("output", ""),
                "exit_code": result.get("exit_code", 0),
                "workspace_id": ws_id,
            }
        except PermissionError as e:
            return {"error": str(e), "exit_code": 1}
        except Exception as e:
            return {"error": str(e), "exit_code": 1}


class StopWorkspaceTool:
    async def execute(self, content: str, ctx: dict) -> dict:
        try:
            args = json.loads(content) if content.strip().startswith("{") else {}
        except (json.JSONDecodeError, TypeError):
            args = {}

        ws_id = (args.get("workspace_id") or "").strip()
        if not ws_id:
            return {"error": "stop_workspace: workspace_id is required", "exit_code": 1}

        owner = ctx.get("owner") or "agent"
        wm = _get_workspace_manager()
        if wm is None:
            return {"error": "Docker workspaces are not available on this host", "exit_code": 1}

        try:
            wm.stop_workspace(ws_id=ws_id, owner=owner)
            return {"message": f"Workspace {ws_id} stopped", "workspace_id": ws_id}
        except Exception as e:
            return {"error": str(e), "exit_code": 1}
