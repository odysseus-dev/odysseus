"""Whether a turn runs on the compact (clean v3) preview runtime.

The chat route decides this once, from facts known before context
preparation, and uses that one value both to prepare the turn (its typed
context resolution) and to stamp the turn contract's selection mode. The
agent loop dispatches on that stamp. Keeping both sides here, with no other
imports, means preparation and dispatch read one rule and cannot drift.

Runtime selection is not authority: it grants or denies no operation.
"""

COMPACT_PREVIEW_MODE = "clean_compact_v3_preview"


def uses_compact_preview_runtime(
    *,
    clean_route_requested: bool,
    turn_contract_enabled: bool,
    agent_mode: bool,
    agent_permitted: bool,
    image_generation: bool,
) -> bool:
    """The single compact-runtime eligibility rule for one turn.

    ``turn_contract_enabled`` is the route's contract policy for this turn
    (exact approvals, TUI surface and full-schema routes opt out).
    ``agent_permitted`` is false when the user's privileges demote the turn
    to plain chat; image generation sessions run their own execution path.
    """
    return bool(
        clean_route_requested
        and turn_contract_enabled
        and agent_mode
        and agent_permitted
        and not image_generation
    )


def is_compact_preview_contract(turn_contract) -> bool:
    """Whether a turn contract was stamped for the compact runtime."""
    return (
        turn_contract is not None
        and getattr(turn_contract, "selection_mode", None) == COMPACT_PREVIEW_MODE
    )
