"""GET/PUT /api/model-customizations — admin-only Model Customization."""

from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from core.middleware import require_admin
from src import model_customization


class CustomizationSave(BaseModel):
    endpoint_id: str
    model: str
    settings: Dict[str, Any] = {}


def setup_model_customization_routes():
    router = APIRouter(prefix="/api/model-customizations", tags=["model-customization"])

    @router.get("")
    def list_model_customizations(request: Request):
        require_admin(request)
        return {"endpoints": model_customization.cascade()}

    @router.put("")
    def save_model_customization(request: Request, body: CustomizationSave):
        require_admin(request)
        try:
            return model_customization.save_for_endpoint(body.endpoint_id, body.model, body.settings)
        except ValueError as exc:
            raise HTTPException(400, str(exc))

    return router
