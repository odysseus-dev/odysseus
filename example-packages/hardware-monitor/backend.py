# Hardware Monitor — backend entry point
# Called by PackageManager.load_plugin() at startup for installed packages.

def register_routes(app):
    from routes.hardware_routes import setup_hardware_routes
    app.include_router(setup_hardware_routes())
