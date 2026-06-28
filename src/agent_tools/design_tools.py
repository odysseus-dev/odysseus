"""design_tools.py — chat-driven entry points for Odysseus Design.

create_design now spins up a DesignProject (opens in the dedicated Design Maker
surface via a design_open SSE event); edit_design still re-prompts the active
design Document. The heavy lifting (model resolution, generation, storage)
lives in design_service.
"""

import json
import logging

from src.design_service import (
    generate_design_html,
    save_new_design,
    save_design_version,
    create_design_project,
)

logger = logging.getLogger(__name__)


def _parse(content: str) -> dict:
    c = (content or "").strip()
    if c.startswith("{"):
        try:
            return json.loads(c)
        except Exception:
            pass
    return {"prompt": c}


class CreateDesignTool:
    async def execute(self, content: str, ctx: dict) -> dict:
        a = _parse(content)
        prompt = (a.get("prompt") or a.get("instruction") or "").strip()
        title = (a.get("title") or "").strip() or None
        try:
            return await create_design_project(prompt, title, a.get("model"), ctx)
        except Exception as e:
            logger.warning("create_design generation failed: %s", e)
            return {"error": f"Design generation failed: {e}"}


class EditDesignTool:
    async def execute(self, content: str, ctx: dict) -> dict:
        a = _parse(content)
        instruction = (a.get("instruction") or a.get("prompt") or "").strip()

        # Pull the current design HTML so the edit is a scoped re-prompt.
        import src.agent_tools.document_tools as _dt
        from src.database import SessionLocal, Document

        owner = ctx.get("owner")
        target = ctx.get("doc_id") or _dt._active_document_id
        cur = None
        if target:
            db = SessionLocal()
            try:
                d = db.query(Document).filter(Document.id == target).first()
                # Owner-scope read (IDOR) — enforced only when authenticated.
                if d and owner is not None and d.owner != owner:
                    d = None
                cur = d.current_content if d else None
            finally:
                db.close()

        # Pin the resolved target so save_design_version writes the SAME doc
        # rather than whatever the process-global active id is after the await.
        if target:
            ctx = {**ctx, "doc_id": target}

        try:
            html = await generate_design_html(instruction, cur, a.get("model"), ctx.get("owner"))
        except Exception as e:
            logger.warning("edit_design generation failed: %s", e)
            return {"error": f"Design edit failed: {e}"}
        return await save_design_version(html, ctx)
