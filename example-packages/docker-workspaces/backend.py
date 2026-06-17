# Docker Workspaces — backend entry point
# Called by PackageManager.load_plugin() at startup for installed packages.

def register_routes(app):
    from src.workspace_manager import WorkspaceManager
    from routes.docker_workspace_routes import setup_docker_workspace_routes
    from routes.guard_routes import setup_guard_routes

    workspace_manager = WorkspaceManager()
    app.include_router(setup_docker_workspace_routes(workspace_manager))
    app.include_router(setup_guard_routes())
