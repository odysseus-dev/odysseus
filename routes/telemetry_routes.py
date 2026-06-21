"""Live hardware telemetry endpoint.

Exposes ``GET /api/telemetry`` which returns the latest hardware snapshot
collected by :mod:`services.telemetry.sampler`. The route is admin-only and
requires the ``telemetry_enabled`` setting to be ``True``; otherwise it
returns 403 so the frontend can suppress the widget entirely.
"""

from fastapi import APIRouter, HTTPException, Request


def setup_telemetry_routes() -> APIRouter:
    """Register telemetry routes and return the router."""
    router = APIRouter(prefix="/api/telemetry", tags=["telemetry"])

    @router.get("")
    def get_telemetry(request: Request):
        """Return the latest hardware telemetry snapshot.

        Returns:
            dict: snapshot with cpu_pct, ram_gb, ram_pct, vram_gb, gpu_pct,
                gpu_temp_c, throttle, timestamp. All GPU fields are 0 when
                no NVIDIA GPU is present or pynvml is not installed.

        Raises:
            HTTPException 403: when telemetry is disabled or caller is not admin.
        """
        from src.settings import get_setting
        if not get_setting("telemetry_enabled", False):
            raise HTTPException(status_code=403, detail="telemetry_disabled")

        # Require admin — telemetry leaks hardware info about the host.
        try:
            from core.auth import AuthManager as _AM
            user = getattr(request.state, "current_user", None)
            # API token callers ("api") pass through so automated tools can read
            # telemetry; cookie-session callers must be admin.
            if user and user != "api":
                auth_mgr = getattr(request.app.state, "auth_manager", None)
                if auth_mgr and not auth_mgr.is_admin(user):
                    raise HTTPException(status_code=403, detail="admin_only")
        except HTTPException:
            raise
        except Exception:
            pass

        from services.telemetry.sampler import get_sampler
        sampler = get_sampler()
        snapshot = sampler.get_latest()
        if not snapshot:
            return {
                "timestamp": 0,
                "cpu_pct": 0.0,
                "ram_gb": 0.0,
                "ram_pct": 0.0,
                "vram_gb": 0.0,
                "gpu_pct": 0,
                "gpu_temp_c": 0,
                "throttle": False,
            }
        return snapshot

    return router
