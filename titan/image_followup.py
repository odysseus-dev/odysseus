"""Image follow-up checks — non-default params need user confirmation via the agent.

Intent (batch count, same seed, language) is decided by the chat LLM using
SESSION_IMAGE_CONTEXT and conversation — not backend regex heuristics.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple


def load_last_session_image_meta(
    session_id: Optional[str],
    owner: Optional[str] = None,
) -> Optional[Tuple[int, str]]:
    """Return (gen_seed, gallery_id) for the newest image in this chat session."""
    sid = (session_id or "").strip()
    if not sid:
        return None
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
                return None
            seed = getattr(row, "gen_seed", None)
            if seed is None:
                return None
            return int(seed), str(row.id)
        finally:
            db.close()
    except Exception:
        return None


def apply_image_defaults(args: Dict[str, Any], raw_args: Dict[str, Any]) -> Dict[str, Any]:
    """Defaults only — never override explicit tool-call values."""
    out = dict(args or {})
    if "n" not in raw_args or raw_args.get("n") is None:
        out["n"] = 1
    else:
        try:
            out["n"] = max(1, min(4, int(out.get("n") or 1)))
        except (TypeError, ValueError):
            out["n"] = 1
    return out


def check_non_default_params(
    args: Dict[str, Any],
    raw_args: Dict[str, Any],
    *,
    confirm: bool = False,
) -> Optional[str]:
    """Return NEEDS_USER_INPUT when the agent set non-default params without user approval."""
    if confirm:
        return None

    explicit_n = "n" in raw_args and raw_args.get("n") is not None
    if explicit_n:
        try:
            raw_n = max(1, min(4, int(args.get("n") or 1)))
        except (TypeError, ValueError):
            raw_n = 1
        if raw_n > 1:
            return (
                f"NEEDS_USER_INPUT: You set n={raw_n} (batch). The default is 1 image. "
                f"Ask the user whether they want {raw_n} images or just one. "
                f"Then call generate_image again with confirm=true and the n they chose "
                f"(omit n or use n=1 for a single image)."
            )

    return None


# Backwards-compatible alias used by image_gen_server.
check_image_followup_conflicts = check_non_default_params
