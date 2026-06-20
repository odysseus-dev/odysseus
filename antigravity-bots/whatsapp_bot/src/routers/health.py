from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/health", tags=["health"])

class HealthResponse(BaseModel):
    status: str

@router.get("/live", response_model=HealthResponse)
async def liveness_probe() -> HealthResponse:
    return HealthResponse(status="ok")

@router.get("/ready", response_model=HealthResponse)
async def readiness_probe() -> HealthResponse:
    # Additional readiness checks (like DB connection) go here
    return HealthResponse(status="ready")
