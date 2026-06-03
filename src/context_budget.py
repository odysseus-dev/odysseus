"""Adaptive input-token budget for the agent loop (#1170).

The agent soft-trims its input context to ``agent_input_token_budget`` (default
6000). The old computation was ``min(context_length or budget, budget)``, which
made the 6000 default a hard ceiling for *every* model — so a 128K or 1M context
model was silently capped at 6000 input tokens even though it can hold far more.

This derives the effective budget from the model's discovered context window when
the user has NOT set an explicit budget, while still honouring an explicit setting
exactly (clamped to the window). Pure and side-effect free so it is unit-testable.
"""

# Generous ceiling so long-context models are unblocked without sending a
# pathologically large prompt every agent turn. Tunable; chosen to fully cover
# 128K models and give 1M models a large but bounded budget.
DEFAULT_HARD_MAX = 200_000
DEFAULT_BUDGET = 6000
DEFAULT_HEADROOM = 0.85


def compute_input_token_budget(
    configured: int,
    context_length: int,
    explicit: bool,
    *,
    default: int = DEFAULT_BUDGET,
    headroom: float = DEFAULT_HEADROOM,
    hard_max: int = DEFAULT_HARD_MAX,
) -> int:
    """Return the effective soft input-token budget.

    Args:
        configured: the value read from settings (may be the default).
        context_length: the model's discovered context window (0/unknown if none).
        explicit: True if the user explicitly set ``agent_input_token_budget``.

    Rules:
        - Explicit user budget is honoured exactly, only clamped to the model's
          window when that window is known (never send more than the model holds).
        - Otherwise (default), scale to ``headroom`` of the context window, capped
          at ``hard_max`` — so long-context models use their capacity.
        - When the window is unknown, fall back to the configured/default value
          (preserving the previous behaviour).
    """
    configured = int(configured or 0)
    context_length = int(context_length or 0)

    if explicit and configured > 0:
        return min(configured, context_length) if context_length > 0 else configured

    if context_length > 0:
        scaled = int(context_length * headroom)
        return max(1, min(scaled, hard_max))

    return configured if configured > 0 else default
