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


def _int_or_zero(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


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
        context_length: the model's discovered context window. Pass 0 when the
            window is unknown / only a bare fallback — auto-scaling then stays
            conservative instead of trusting an unproven window (review on #4122).
        explicit: True if the user set a NON-default budget. The default value is
            the "auto" sentinel (scale to the window); any other value is an
            explicit cap. (A deliberately-chosen default can't be distinguished
            from a materialized default by value, so the default reads as auto.)

    Rules:
        - Explicit user budget is honoured exactly, only clamped to the model's
          window when that window is known (the user's deliberate choice wins;
          ``hard_max`` is an auto-budget ceiling only — see #1230).
        - Otherwise (auto), scale to ``headroom`` of the context window, capped at
          ``hard_max`` — so long-context models use their capacity.
        - When the window is unknown (context_length <= 0), use the conservative
          ``default`` budget and do NOT scale off the fallback.
    """
    configured = _int_or_zero(configured)
    context_length = _int_or_zero(context_length)

    if explicit and configured > 0:
        return min(configured, context_length) if context_length > 0 else configured

    if context_length > 0:
        scaled = int(context_length * headroom)
        return max(1, min(scaled, hard_max))

    return configured if configured > 0 else default


def budget_is_explicit(configured: int, *, default: int = DEFAULT_BUDGET) -> bool:
    """Whether a configured agent_input_token_budget is a deliberate explicit cap.

    The default value is the "auto" sentinel (scale to the model's window), so only
    a NON-default positive value counts as explicit. This keys off the VALUE, not
    settings *presence* — the settings-save path materializes every default into
    settings.json, so a persisted default must still read as auto (the regression
    #4121 / #1230 are about). Centralised here so the materialized-default contract
    is unit-testable and can't silently regress to a presence check.
    """
    configured = int(configured or 0)
    return configured > 0 and configured != default


def context_budget_report(
    system_tokens: int,
    schema_tokens: int,
    history_tokens: int,
    context_length: int,
) -> dict:
    """Return a structured breakdown of context usage for the UI.

    Emitted as part of the ``agent_prep`` SSE event so the frontend can
    display context budget information.  All values are approximate
    (based on the same chars·0.3 estimator used elsewhere).

    Keys:
        system_prompt_tokens:  tokens consumed by the system prompt(s)
        tool_schema_tokens:    tokens consumed by JSON tool schemas
        history_tokens:        tokens consumed by conversation history
        total_tokens:          sum of the above
        context_length:        the model's context window
        percent_used:          total / context_length as a percentage
        tokens_available:      remaining tokens before hitting the window
        warning:               True when total > 85% of context_length
        tier:                  'micro' | 'small' | 'medium' | 'large'
    """
    total = system_tokens + schema_tokens + history_tokens
    pct = round((total / context_length) * 100, 1) if context_length > 0 else 0

    if context_length <= 8192:
        tier = "micro"
    elif context_length <= 16384:
        tier = "small"
    elif context_length <= 32768:
        tier = "medium"
    else:
        tier = "large"

    return {
        "system_prompt_tokens": system_tokens,
        "tool_schema_tokens": schema_tokens,
        "history_tokens": history_tokens,
        "total_tokens": total,
        "context_length": context_length,
        "percent_used": pct,
        "tokens_available": max(0, context_length - total),
        "warning": total > context_length * DEFAULT_HEADROOM if context_length > 0 else False,
        "tier": tier,
    }
