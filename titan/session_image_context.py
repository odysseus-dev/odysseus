"""Structured session image facts for the chat LLM (not intent parsing)."""

from __future__ import annotations

from typing import Any, Dict, Optional

SEED_MAX = 2**31 - 1


def load_session_image_context(
    session_id: Optional[str],
    owner: Optional[str] = None,
) -> Dict[str, Any]:
    """Read-only facts about the newest gallery image in this chat session."""
    empty: Dict[str, Any] = {
        "has_prior_image": False,
        "last_gallery_id": None,
        "last_seed": None,
        "last_prompt": None,
        "last_style": None,
        "last_size": None,
        "last_quality": None,
    }
    sid = (session_id or "").strip()
    if not sid:
        return empty
    try:
        from core.database import GalleryImage, SessionLocal
        from src.auth_helpers import owner_filter

        db = SessionLocal()
        try:
            q = db.query(GalleryImage).filter(GalleryImage.session_id == sid)
            if owner:
                q = owner_filter(q, GalleryImage, owner)
            row = q.order_by(GalleryImage.created_at.desc()).first()
            if not row:
                return empty
            prompt = (row.prompt or "").strip()
            if len(prompt) > 200:
                prompt = prompt[:197] + "..."
            style = getattr(row, "gen_style", None) or ""
            if not style and row.model and "titan-sd:" in str(row.model):
                style = str(row.model).split("titan-sd:", 1)[-1].split()[0]
            seed = getattr(row, "gen_seed", None)
            return {
                "has_prior_image": True,
                "last_gallery_id": str(row.id),
                "last_seed": int(seed) if seed is not None else None,
                "last_prompt": prompt or None,
                "last_style": style or None,
                "last_size": row.size or None,
                "last_quality": row.quality or None,
            }
        finally:
            db.close()
    except Exception:
        return empty


def format_context_for_llm(ctx: Dict[str, Any]) -> str:
    """Human-readable block injected into chat context and wizard messages."""
    if not ctx.get("has_prior_image"):
        return (
            "SESSION_IMAGE_CONTEXT:\n"
            "- No prior generated image is saved for this chat session.\n"
            "- Default: omit `seed` (random). Omit `n` or use n=1 for a single image.\n"
            "- You interpret the user's language for same-seed or batch requests; "
            "use ask_user when unclear."
        )

    lines = [
        "SESSION_IMAGE_CONTEXT (database facts — not user text):",
        f"- last_gallery_id: {ctx.get('last_gallery_id')}",
    ]
    if ctx.get("last_seed") is not None:
        lines.append(f"- last_seed: {ctx['last_seed']}")
    else:
        lines.append("- last_seed: (not recorded)")
    if ctx.get("last_style"):
        lines.append(f"- last_style: {ctx['last_style']}")
    if ctx.get("last_size"):
        lines.append(f"- last_size: {ctx['last_size']}")
    if ctx.get("last_prompt"):
        lines.append(f"- last_prompt: {ctx['last_prompt']}")
    lines.extend([
        "",
        "Your job (chat model): interpret the user's message in any language.",
        "- Same seed / reuse seed: pass seed=last_seed exactly in generate_image (never guess).",
        "- New random seed: omit `seed`.",
        "- Multiple images: set n=1..4 to match what the user asked.",
        "- Regenerate/edit prior image: op=regenerate, source_image_id=last_gallery_id.",
        "- If intent is unclear: ask_user first, then call generate_image with explicit seed/n.",
    ])
    return "\n".join(lines)


def validate_seed_value(seed: Optional[int]) -> Optional[str]:
    """Return an error message if seed is structurally invalid."""
    if seed is None:
        return None
    if seed < 0 or seed > SEED_MAX:
        return (
            f"seed={seed} is out of allowed range (0..{SEED_MAX}). "
            "Do not invent seeds — use last_seed from SESSION_IMAGE_CONTEXT, "
            "omit seed for random, or ask the user."
        )
    return None
