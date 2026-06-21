"""Serving profile API routes.

Exposes:
- ``GET /api/profiles``      — list all profiles (MAX, DAILY, CUSTOM)
- ``GET /api/profiles/{key}``  — single profile detail
- ``PUT /api/profiles/custom``  — persist user edits to the CUSTOM profile

Profiles are read-only built-ins (MAX, DAILY) plus a user-editable CUSTOM
stored in ``data/profiles.json``. No authentication gate — profiles contain
no sensitive data and are shared across all users of an Odysseus instance.
"""

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse


def setup_profile_routes() -> APIRouter:
    """Register profile routes and return the router."""
    router = APIRouter(prefix="/api/profiles", tags=["profiles"])

    @router.get("")
    def list_profiles():
        """Return all serving profiles in display order.

        Returns:
            list: Profile objects with key, label, description,
                ttft_estimate, ctx_size, gpu_layers, flash_attn,
                features, is_builtin.
        """
        from services.profiles.profiles import list_profiles as _list
        return _list()

    @router.get("/{key}")
    def get_profile(key: str):
        """Return a single serving profile by key.

        Args:
            key: Profile identifier — ``max``, ``daily``, or ``custom``.

        Returns:
            Profile dict.

        Raises:
            HTTPException 404: when the key is not recognised.
        """
        from services.profiles.profiles import get_profile as _get
        profile = _get(key)
        if profile is None:
            raise HTTPException(status_code=404, detail=f"profile '{key}' not found")
        return profile

    @router.put("/custom")
    def save_custom_profile(body: dict):
        """Persist user-supplied CUSTOM profile fields to disk.

        Args:
            body: Partial or full override dict. ``key`` and ``is_builtin``
                are always overwritten server-side.

        Returns:
            The full merged CUSTOM profile after saving.
        """
        from services.profiles.profiles import save_custom as _save
        # Strip read-only fields the client must not override.
        body.pop("is_builtin", None)
        body.pop("key", None)
        return _save(body)

    return router
