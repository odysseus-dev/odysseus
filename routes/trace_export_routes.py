import sys
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import List, Optional
from pathlib import Path

# Ensure the repo root is on sys.path when executing this file directly.
repo_root = Path(__file__).resolve().parents[1]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from core.database import Session, get_db
from src.auth_helpers import get_current_user
from services.trace_export import build_trace_records


router = APIRouter()

class TraceExportRequest(BaseModel):
    session_id: str
    message_ids: List[str]
    label: str
    note: Optional[str] = None

@router.post("/trace/export")
async def export_trace(
    payload: TraceExportRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    try:
        data = build_trace_records(
            db=db,
            current_user=current_user,  # <--- Change this back! Remove the .id
            session_id=payload.session_id,
            message_ids=payload.message_ids,
            label=payload.label,
            note=payload.note
        )

        return {
            "status": "success",
            "data": data
        }

    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=str(e)
        )