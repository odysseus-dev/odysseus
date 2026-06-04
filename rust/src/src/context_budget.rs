// src/context_budget.rs  <- src/context_budget.py
//! Adaptive input-token budget for the agent loop (#1170).
//!
//! The agent soft-trims its input context to `agent_input_token_budget` (default
//! 6000). The old computation `min(context_length or budget, budget)` made 6000 a
//! hard ceiling for EVERY model, so a 128K/1M-context model was silently capped at
//! 6000 input tokens. This derives the effective budget from the model's
//! discovered context window when the user has NOT set an explicit budget, while
//! honouring an explicit setting exactly (clamped to the window). Pure + testable.

/// Generous ceiling so long-context models are unblocked without a pathologically
/// large prompt each turn. Covers 128K fully and bounds 1M models.
pub const DEFAULT_HARD_MAX: i64 = 200_000;
pub const DEFAULT_BUDGET: i64 = 6000;
pub const DEFAULT_HEADROOM: f64 = 0.85;

/// `compute_input_token_budget(configured, context_length, explicit)` with the
/// Python defaults.
pub fn compute_input_token_budget(configured: i64, context_length: i64, explicit: bool) -> i64 {
    compute_input_token_budget_with(
        configured,
        context_length,
        explicit,
        DEFAULT_BUDGET,
        DEFAULT_HEADROOM,
        DEFAULT_HARD_MAX,
    )
}

/// Full form (the Python keyword args `default`/`headroom`/`hard_max`).
///
/// Rules: an explicit user budget is honoured exactly, clamped to the window only
/// when known; otherwise scale to `headroom` of the window capped at `hard_max`;
/// when the window is unknown fall back to the configured/default value.
pub fn compute_input_token_budget_with(
    configured: i64,
    context_length: i64,
    explicit: bool,
    default: i64,
    headroom: f64,
    hard_max: i64,
) -> i64 {
    // Python: `int(configured or 0)` / `int(context_length or 0)`.
    let configured = configured.max(0);
    let context_length = context_length.max(0);

    if explicit && configured > 0 {
        return if context_length > 0 {
            configured.min(context_length)
        } else {
            configured
        };
    }
    if context_length > 0 {
        let scaled = (context_length as f64 * headroom) as i64;
        return scaled.min(hard_max).max(1);
    }
    if configured > 0 {
        configured
    } else {
        default
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn explicit_clamped_to_window() {
        // explicit budget honoured, clamped to window when known
        assert_eq!(compute_input_token_budget(8000, 4096, true), 4096);
        assert_eq!(compute_input_token_budget(8000, 128000, true), 8000);
        // unknown window -> exact explicit value
        assert_eq!(compute_input_token_budget(8000, 0, true), 8000);
    }

    #[test]
    fn default_scales_to_window() {
        // long-context model is no longer capped at 6000
        assert_eq!(compute_input_token_budget(6000, 128000, false), 108800); // 0.85*128000
        // capped at hard_max for huge windows
        assert_eq!(compute_input_token_budget(6000, 1_000_000, false), 200_000);
        // unknown window -> configured/default fallback
        assert_eq!(compute_input_token_budget(6000, 0, false), 6000);
        assert_eq!(compute_input_token_budget(0, 0, false), 6000);
    }
}
