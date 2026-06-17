"""
WorkspaceManager: Docker container lifecycle management for AI workspaces.
Each workspace is a stateful Docker container backed by a persistent Named Volume.
Chat history deletion NEVER affects workspace state — they are decoupled layers.
"""
import logging
import uuid

logger = logging.getLogger(__name__)

_docker_available = False
try:
    import docker
    # Guard against a non-SDK `docker` shadowing the real docker-py package.
    # The repo ships a top-level `docker/` directory (deployment configs); when
    # the SDK isn't installed, `import docker` silently resolves to that as a
    # namespace package and succeeds without exposing `from_env`. Verifying the
    # attribute keeps the "SDK not installed" path honest instead of failing
    # later with a confusing "cannot connect to daemon" error.
    if hasattr(docker, "from_env"):
        _docker_available = True
    else:
        logger.warning(
            "The importable 'docker' module is not the docker-py SDK "
            "(no from_env); Docker workspaces disabled. Run: pip install docker"
        )
except ImportError:
    logger.warning("docker SDK not installed; Docker workspaces disabled. Run: pip install docker")


def _get_client():
    if not _docker_available:
        raise RuntimeError("Docker SDK not available. Install with: pip install docker")
    import docker as _docker
    if not hasattr(_docker, "from_env"):
        raise RuntimeError(
            "The importable 'docker' module is not the docker-py SDK "
            "(no from_env). Install it with: pip install docker"
        )
    try:
        return _docker.from_env()
    except Exception as e:
        raise RuntimeError(f"Cannot connect to Docker daemon: {e}")


class WorkspaceManager:
    """Manages the full lifecycle of AI workspace containers and their volumes."""

    def __init__(self):
        self._client = None

    def _c(self):
        if self._client is None:
            self._client = _get_client()
        return self._client

    def is_available(self) -> bool:
        """Check if Docker is reachable."""
        try:
            self._c().ping()
            return True
        except Exception:
            return False

    def create_workspace(
        self,
        name: str,
        template_image: str = "python:3.11-alpine",
        resource_limits: dict | None = None,
        owner: str | None = None,
        description: str = "",
        bound_agent_id: str | None = None,
    ) -> dict:
        """
        Create a Docker container + persistent Named Volume for a workspace.
        The volume survives chat deletion, app restarts, and container recreation.
        """
        ws_id = uuid.uuid4().hex[:12]
        volume_name = f"ws-{ws_id}-vol"
        container_name = f"odysseus-ws-{ws_id}"
        limits = resource_limits or {"memory": "512m", "cpu": 0.5}

        client = self._c()

        client.volumes.create(volume_name, labels={
            "odysseus.workspace.id": ws_id,
            "odysseus.owner": owner or "system",
            "odysseus.managed": "true",
        })

        labels = {
            "odysseus.workspace.id": ws_id,
            "odysseus.workspace.name": name,
            "odysseus.owner": owner or "system",
            "odysseus.protected": "true",
            "odysseus.managed": "true",
        }
        if bound_agent_id:
            labels["odysseus.agent"] = bound_agent_id

        nano_cpus = int(float(limits.get("cpu", 0.5)) * 1_000_000_000)
        mem_limit = limits.get("memory", "512m")

        container = client.containers.create(
            image=template_image,
            name=container_name,
            volumes={volume_name: {"bind": "/workspace", "mode": "rw"}},
            working_dir="/workspace",
            command="tail -f /dev/null",
            labels=labels,
            nano_cpus=nano_cpus,
            mem_limit=mem_limit,
            detach=True,
            network_mode="bridge",
            environment={"WORKSPACE_ID": ws_id, "WORKSPACE_NAME": name},
        )
        container.start()

        from core.database import get_db_session, DockerWorkspace
        with get_db_session() as db:
            ws = DockerWorkspace(
                id=ws_id,
                name=name,
                description=description,
                template_image=template_image,
                container_id=container.id,
                volume_name=volume_name,
                status="running",
                resource_limits=limits,
                bound_agent_id=bound_agent_id,
                labels=labels,
                owner=owner,
            )
            db.add(ws)

        logger.info(f"Workspace created: {ws_id} '{name}' owner={owner} image={template_image}")
        return self.get_workspace(ws_id) or {}

    def stop_workspace(self, ws_id: str, owner: str | None = None) -> bool:
        """Stop container (data is preserved on the volume)."""
        from core.database import get_db_session, DockerWorkspace
        with get_db_session() as db:
            ws = db.query(DockerWorkspace).filter(DockerWorkspace.id == ws_id).first()
            if not ws:
                raise ValueError(f"Workspace {ws_id} not found")
            if owner and ws.owner != owner:
                raise PermissionError(f"Not authorized to stop workspace {ws_id}")
            if ws.status == "destroyed":
                raise ValueError("Workspace has been destroyed")

            try:
                container = self._c().containers.get(ws.container_id)
                container.stop(timeout=10)
            except Exception as e:
                logger.warning(f"Container stop warning: {e}")
            ws.status = "stopped"

        return True

    def start_workspace(self, ws_id: str, owner: str | None = None) -> bool:
        """Start a stopped workspace container."""
        from core.database import get_db_session, DockerWorkspace
        with get_db_session() as db:
            ws = db.query(DockerWorkspace).filter(DockerWorkspace.id == ws_id).first()
            if not ws:
                raise ValueError(f"Workspace {ws_id} not found")
            if owner and ws.owner != owner:
                raise PermissionError(f"Not authorized to start workspace {ws_id}")
            if ws.status == "destroyed":
                raise ValueError("Workspace has been destroyed and cannot be restarted")

            try:
                container = self._c().containers.get(ws.container_id)
                container.start()
                ws.status = "running"
                return True
            except Exception as e:
                logger.error(f"Container start failed: {e}")
                ws.status = "error"
                return False

    def destroy_workspace(self, ws_id: str, owner: str | None = None) -> bool:
        """
        DESTRUCTIVE: Stop + remove container and permanently delete the Named Volume.
        This action CANNOT be undone. All workspace data is lost.
        """
        from core.database import get_db_session, DockerWorkspace
        with get_db_session() as db:
            ws = db.query(DockerWorkspace).filter(DockerWorkspace.id == ws_id).first()
            if not ws:
                raise ValueError(f"Workspace {ws_id} not found")
            if owner and ws.owner != owner:
                raise PermissionError(f"Not authorized to destroy workspace {ws_id}")
            if ws.status == "destroyed":
                return True

            try:
                container = self._c().containers.get(ws.container_id)
                container.stop(timeout=5)
                container.remove(force=True)
            except Exception as e:
                logger.warning(f"Container removal warning: {e}")

            try:
                volume = self._c().volumes.get(ws.volume_name)
                volume.remove(force=True)
            except Exception as e:
                logger.warning(f"Volume removal warning: {e}")

            ws.status = "destroyed"

        logger.info(f"Workspace destroyed: {ws_id}")
        return True

    def execute_in_workspace(self, ws_id: str, command: str, owner: str | None = None) -> dict:
        """Run a shell command inside the workspace container."""
        from src.execution_guard import classify_command
        check = classify_command(command)
        if check["blocked"]:
            raise PermissionError(f"Command blocked by security policy: {check['reason']}")

        from core.database import get_db_session, DockerWorkspace
        with get_db_session() as db:
            ws = db.query(DockerWorkspace).filter(DockerWorkspace.id == ws_id).first()
            if not ws:
                raise ValueError(f"Workspace {ws_id} not found")
            if owner and ws.owner != owner:
                raise PermissionError("Not authorized to execute in this workspace")
            if ws.status != "running":
                raise RuntimeError(f"Workspace is not running (status: {ws.status})")

            try:
                container = self._c().containers.get(ws.container_id)
                result = container.exec_run(
                    ["/bin/sh", "-c", command],
                    workdir="/workspace",
                    environment={"HOME": "/workspace"},
                    demux=False,
                )
                return {
                    "exit_code": result.exit_code,
                    "output": result.output.decode(errors="replace") if result.output else "",
                    "risk_level": check["risk"],
                }
            except Exception as e:
                return {"exit_code": -1, "output": str(e), "risk_level": check["risk"]}

    def get_workspace_logs(self, ws_id: str, tail: int = 100) -> str:
        """Retrieve recent container logs."""
        from core.database import get_db_session, DockerWorkspace
        with get_db_session() as db:
            ws = db.query(DockerWorkspace).filter(DockerWorkspace.id == ws_id).first()
            if not ws or ws.status == "destroyed":
                return ""
            try:
                container = self._c().containers.get(ws.container_id)
                return container.logs(tail=tail).decode(errors="replace")
            except Exception as e:
                return f"Error retrieving logs: {e}"

    def list_workspaces(self, owner: str | None = None, include_destroyed: bool = False) -> list:
        from core.database import get_db_session, DockerWorkspace
        with get_db_session() as db:
            q = db.query(DockerWorkspace)
            if owner:
                q = q.filter(DockerWorkspace.owner == owner)
            if not include_destroyed:
                q = q.filter(DockerWorkspace.status != "destroyed")
            return [self._to_dict(ws) for ws in q.order_by(DockerWorkspace.created_at.desc()).all()]

    def get_workspace(self, ws_id: str) -> dict | None:
        from core.database import get_db_session, DockerWorkspace
        with get_db_session() as db:
            ws = db.query(DockerWorkspace).filter(DockerWorkspace.id == ws_id).first()
            return self._to_dict(ws) if ws else None

    def sync_status(self, ws_id: str) -> str:
        """Sync DB workspace status with actual Docker container state."""
        from core.database import get_db_session, DockerWorkspace
        with get_db_session() as db:
            ws = db.query(DockerWorkspace).filter(DockerWorkspace.id == ws_id).first()
            if not ws or ws.status == "destroyed":
                return ws.status if ws else "not_found"

            try:
                container = self._c().containers.get(ws.container_id)
                actual = "running" if container.status == "running" else "stopped"
                if ws.status != actual:
                    ws.status = actual
            except Exception:
                pass

            return ws.status

    def bind_agent(self, ws_id: str, agent_id: str, owner: str | None = None) -> bool:
        """Associate an AI agent session with a workspace."""
        from core.database import get_db_session, DockerWorkspace
        with get_db_session() as db:
            ws = db.query(DockerWorkspace).filter(DockerWorkspace.id == ws_id).first()
            if not ws:
                return False
            if owner and ws.owner != owner:
                raise PermissionError("Not authorized")
            ws.bound_agent_id = agent_id
        return True

    def _to_dict(self, ws) -> dict:
        return {
            "id": ws.id,
            "name": ws.name,
            "description": ws.description,
            "template_image": ws.template_image,
            "container_id": ws.container_id,
            "volume_name": ws.volume_name,
            "status": ws.status,
            "resource_limits": ws.resource_limits or {},
            "bound_agent_id": ws.bound_agent_id,
            "labels": ws.labels or {},
            "owner": ws.owner,
            "created_at": ws.created_at.isoformat() if ws.created_at else None,
            "updated_at": ws.updated_at.isoformat() if ws.updated_at else None,
        }
