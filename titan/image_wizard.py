"""Image wizard helpers: defaults, structural validation, context-enriched messages.

Intent (same seed, batch count, language) is handled by the chat LLM using
SESSION_IMAGE_CONTEXT — not by backend regex.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from titan.session_image_context import (
    format_context_for_llm,
    load_session_image_context,
    validate_seed_value,
)


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


def validate_tool_params(
    *,
    seed: Optional[int],
    n: int,
    raw_args: Dict[str, Any],
) -> Optional[str]:
    """Structural validation only. Returns NEEDS_USER_INPUT body or None."""
    seed_err = validate_seed_value(seed)
    if seed_err:
        return f"NEEDS_USER_INPUT: {seed_err}"

    if "n" in raw_args and raw_args.get("n") is not None:
        try:
            raw_n = int(raw_args["n"])
        except (TypeError, ValueError):
            return (
                "NEEDS_USER_INPUT: `n` must be an integer 1..4. "
                "Ask the user how many images they want, then call generate_image again."
            )
        if raw_n < 1 or raw_n > 4:
            return (
                f"NEEDS_USER_INPUT: n={raw_n} is outside 1..4. "
                "Ask the user to pick a count between 1 and 4."
            )
    return None


def wizard_message(
    body: str,
    *,
    session_id: Optional[str] = None,
    owner: Optional[str] = None,
    ctx: Optional[Dict[str, Any]] = None,
) -> str:
    """Append SESSION_IMAGE_CONTEXT facts so the chat LLM can decide seed/n."""
    if ctx is None:
        ctx = load_session_image_context(session_id, owner)
    block = format_context_for_llm(ctx)
    text = (body or "").rstrip()
    if not text.startswith("NEEDS_USER_INPUT:"):
        text = f"NEEDS_USER_INPUT: {text}"
    return f"{text}\n\n{block}"
