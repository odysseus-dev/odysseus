"""Projects: per-owner isolated workspaces (chats, files, memory)."""
from .service import ProjectService

_default_service: ProjectService | None = None


def get_project_service() -> ProjectService:
    """Return the process-wide ProjectService singleton."""
    global _default_service
    if _default_service is None:
        _default_service = ProjectService()
    return _default_service