# routes/formflow_routes.py
"""FormFlow — /api/formflow/parse (SSE) + /api/formflow/extract-pdf"""

import io
import json
import logging
from typing import Optional

from fastapi import APIRouter, File, Request, UploadFile, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from src.auth_helpers import get_current_user
from src.endpoint_resolver import resolve_endpoint
from src.llm_core import stream_llm

logger = logging.getLogger(__name__)

_PARSE_SYSTEM = """You are a form parser. Given raw text or an image of a form, questionnaire, survey, or application, extract every question and return a JSON array of question objects.

Rules:
- Only set wordLimit or charLimit if the source EXPLICITLY states a limit (e.g. "max 200 words", "250 characters"). Otherwise leave them null.
- Infer the best question type from context. Default to "textarea" for open-ended answers.
- Set required: true unless the source says "optional" or "if applicable".
- For choice/multi, extract each option into the options array.
- For scale questions, set scaleMin and scaleMax (default 1 and 5 if not stated).
- Return ONLY the raw JSON array. No explanation. No markdown code fences. No surrounding text.

Valid question types: text | textarea | choice | multi | yesno | scale | email | number

Required JSON shape per question (all fields must be present):
{"id":"q1","type":"textarea","label":"The question text","required":true,"wordLimit":null,"charLimit":null,"options":[],"scaleMin":null,"scaleMax":null,"placeholder":""}"""


class ParseRequest(BaseModel):
    text: Optional[str] = None
    image_base64: Optional[str] = None
    media_type: Optional[str] = "image/jpeg"


def setup_formflow_routes() -> APIRouter:
    router = APIRouter(tags=["formflow"])

    @router.post("/api/formflow/parse")
    async def parse_form(request: Request, body: ParseRequest):
        owner = get_current_user(request)
        url, model, headers = resolve_endpoint("utility", owner=owner)

        if not url or not model:
            async def _err():
                yield f'event: error\ndata: {json.dumps({"error": "No model configured. Add an endpoint in Settings first."})}\n\n'
            return StreamingResponse(_err(), media_type="text/event-stream")

        if body.image_base64:
            user_content = [
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{body.media_type or 'image/jpeg'};base64,{body.image_base64}"},
                },
                {"type": "text", "text": "Extract all questions from this form image. Return only the JSON array."},
            ]
        else:
            text = (body.text or "").strip()
            if not text:
                async def _err():
                    yield f'event: error\ndata: {json.dumps({"error": "No form content provided."})}\n\n'
                return StreamingResponse(_err(), media_type="text/event-stream")
            user_content = text

        messages = [
            {"role": "system", "content": _PARSE_SYSTEM},
            {"role": "user", "content": user_content},
        ]

        async def generate():
            # Send model name first so the UI can display it during streaming
            yield f'data: {json.dumps({"model": model})}\n\n'
            async for chunk in stream_llm(url, model, messages, headers=headers, max_tokens=4096):
                yield chunk

        return StreamingResponse(generate(), media_type="text/event-stream")

    @router.post("/api/formflow/extract-pdf")
    async def extract_pdf(request: Request, file: UploadFile = File(...)):
        fname = (file.filename or "").lower()
        if not fname.endswith(".pdf"):
            raise HTTPException(400, "PDF file required")

        data = await file.read()
        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(data))
            pages = [page.extract_text() or "" for page in reader.pages]
            text = "\n\n".join(p for p in pages if p.strip())
            if not text.strip():
                raise HTTPException(422, "No extractable text found in PDF. Try uploading an image of the form instead.")
            return {"text": text}
        except ImportError:
            raise HTTPException(501, "pypdf not installed")
        except HTTPException:
            raise
        except Exception:
            logger.exception("PDF extraction error")
            raise HTTPException(500, "PDF extraction failed")

    return router
