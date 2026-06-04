"""File-conversion routes — backs the Settings → Convert tab.

A small convert.io-style endpoint: upload a file, pick a target format, get the
converted file back as a download. Conversions run in-memory (see
:mod:`src.file_convert`); nothing is persisted.
"""

import logging

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response

from src.auth_helpers import get_current_user
from src.file_convert import (
    ConversionError,
    convert_file,
    supported_targets,
)
from src.upload_limits import read_upload_limited

logger = logging.getLogger(__name__)

# Hard cap — conversions buffer the whole file in memory.
MAX_CONVERT_BYTES = 50 * 1024 * 1024  # 50 MB


def setup_convert_routes() -> APIRouter:
    router = APIRouter(prefix="/api/convert", tags=["convert"])

    @router.get("/targets")
    async def get_targets(filename: str):
        """List the formats a given filename can convert to (drives the UI)."""
        targets = supported_targets(filename)
        return {"filename": filename, "targets": targets}

    @router.post("")
    async def convert(
        request: Request,
        file: UploadFile = File(...),
        target: str = Form(...),
    ):
        """Convert an uploaded file to ``target`` and stream it back as a download."""
        # Touch the session so anonymous/auth modes behave like other routes.
        get_current_user(request)

        data = await read_upload_limited(file, MAX_CONVERT_BYTES, label="File")
        if not data:
            raise HTTPException(status_code=400, detail="Empty file.")

        try:
            out_bytes, out_name, media_type = convert_file(
                data, file.filename or "file", target
            )
        except ConversionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Conversion failed for %s -> %s", file.filename, target)
            raise HTTPException(status_code=500, detail="Conversion failed.") from exc

        return Response(
            content=out_bytes,
            media_type=media_type,
            headers={
                "Content-Disposition": f'attachment; filename="{out_name}"',
                "X-Output-Filename": out_name,
            },
        )

    return router
