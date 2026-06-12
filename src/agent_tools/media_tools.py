"""Media-related agent tools registered through TOOL_HANDLERS."""

import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)


async def list_media_models(content: str, session_id: Optional[str] = None, owner: Optional[str] = None) -> Dict:
    """List configured + enabled media-generation models from the registry.

    Content = optional kind filter ("image" | "video"); defaults to "image".
    """
    from src import media_registry

    kind = (content or "").strip().lower()
    if kind not in ("image", "video"):
        kind = "image"

    try:
        enabled = media_registry.list_enabled_models(kind=kind, owner=owner or "")
        if not enabled:
            if kind == "image":
                _model, degraded = media_registry.default_image_model_or_degraded(owner=owner or "")
            else:
                degraded = media_registry.degraded_state(
                    "no_models",
                    kind=kind,
                    message=f"No {kind} generation models are currently configured.",
                    next_steps=[
                        "Configure a media provider and register a model.",
                        "Run the provider probe again.",
                    ],
                )
            return {
                "results": media_registry.format_degraded_message(degraded),
                "models": [],
                "status": degraded.get("status"),
                "available": False,
            }

        default = media_registry.resolve_default_model(kind=kind, owner=owner or "")
        default_id = default["id"] if default else None

        public = []
        for m in enabled:
            entry = media_registry.to_public_dict(m)
            entry["isDefault"] = bool(default_id and entry.get("id") == default_id)
            public.append(entry)

        lines = [f"Configured {kind} models ({len(public)}):"]
        for p in public:
            caps = ", ".join(p.get("capabilities") or []) or "—"
            tag = " [default]" if p.get("isDefault") else ""
            lines.append(
                f"- {p.get('label')} (`{p.get('id')}`, {p.get('provider')}){tag} — {caps}"
            )
        if not default_id:
            lines.append("")
            lines.append("No default is set; pass an explicit model id to generate_image.")

        return {
            "results": "\n".join(lines),
            "models": public,
            "default_model_id": default_id,
            "available": True,
        }
    except Exception as e:
        logger.error("list_media_models failed: %s", e)
        return {"error": str(e)}


class ListMediaModelsTool:
    async def execute(self, content: str, ctx: dict) -> Dict:
        return await list_media_models(content, ctx.get("session_id"), owner=ctx.get("owner"))
