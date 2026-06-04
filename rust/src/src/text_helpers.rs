// src/text_helpers.rs  <- src/text_helpers.py
//! Text-cleanup helpers shared across LLM-output paths.
//!
//! Single source of truth for `<think>`-tag stripping, Qwen-style "Thinking
//! Process" blocks, and the soft "reasoning prose" heuristic that catches
//! chain-of-thought leaks from models that don't tag their reasoning.
//!
//! Before this module, six different files (`email_routes.py`,
//! `chat_helpers.py`, `note_routes.py`, `builtin_actions.py`, `research_utils.py`,
//! `agent_loop.py`) each had their own variant of the same regex. They all
//! broke in slightly different ways on the edges (unclosed `<think>`, nested
//! tags, model emitting `<thinking>` instead of `<think>`).

use fancy_regex::Regex as FancyRegex;
use once_cell::sync::Lazy;
use regex::Regex;

// Closed reasoning blocks. Multi-pass loop in `strip_think` handles nested
// `<think><think>...</think></think>` patterns some models emit.
static THINK_CLOSED_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"(?i)<think(?:ing)?>[\s\S]*?</think(?:ing)?>\s*").unwrap());
// Orphan opening or closing tags that survive after the closed-pass.
static THINK_TAG_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"(?i)</?think(?:ing)?[^>]*>\s*").unwrap());
// Dangling opener anywhere in the response with no closer — strip everything
// from `<think>` to the end of string.
static THINK_OPEN_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"(?i)<think(?:ing)?>[\s\S]*$").unwrap());
// Streaming models occasionally emit `<thinking time="0.42">`-style attributes.
// Normalize to a plain `<think>` so the regexes above catch them.
static THINK_ATTR_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"(?i)<think(?:ing)?\s+[^>]*>").unwrap());
static THINK_ATTR_CLOSE_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"(?i)</think(?:ing)?\s+[^>]*>").unwrap());
// Qwen and a few other models prefix the response with a "Thinking Process:"
// block before the real answer.
static QWEN_THINKING_RE: Lazy<FancyRegex> = Lazy::new(|| {
    FancyRegex::new(r"(?is)^Thinking Process:.*?(?=\n\n#|\n\n\*\*|\z)").unwrap()
});
// Leaked prompt-echo headers (a few models replay the request before answering).
static PROMPT_ECHO_RES: Lazy<Vec<FancyRegex>> = Lazy::new(|| {
    vec![
        FancyRegex::new(r"(?s)^The user asks:.*?(?=\n\n#|\n\n\*\*[A-Z]|\z)").unwrap(),
        FancyRegex::new(r"(?s)^We need to.*?(?=\n\n#|\n\n\*\*[A-Z]|\z)").unwrap(),
    ]
});

// Aggressive heuristic for untagged reasoning prose (models that don't wrap
// CoT in `<think>` tags). Only applied as opt-in (`prose=True`) because it
// false-positives on legit user content like "Looking at the attached file…".
static REASONING_PREFIX_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(concat!(
        r"(?i)^\s*(?:",
        r"the user (?:wants|is|asks|needs|wrote|said|told|messaged|requested)|",
        r"i (?:need|should|have|'ll|will|am going)(?: to)? (?:write|draft|reply|respond|read|check|look|review|consider|think|provide|generate|produce|craft|compose|acknowledge|summarize|answer|give|keep|aim|make|address|focus|use|just|simply|analyze|format|create|build|note|decide)|",
        r"let me (?:think|look|see|check|read|review|consider|draft|write|analyze|format|summarize|create|produce|craft|note|extract|identify|figure)|",
        r"looking at (?:the|this|that)|",
        r"(?:okay|alright|hmm|right|so|well|first|next|now)[,.]?\s+(?:the|i|let|so|now|this|here)|",
        r"based on (?:the|this|what|context)|",
        r"to (?:draft|write|reply|respond|summarize|answer)",
        r")\b",
    ))
    .unwrap()
});

/// Splitter for `re.split(r"\n\s*\n", ...)` used in `_strip_reasoning_prose`.
static PARAGRAPH_SPLIT_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"\n\s*\n").unwrap());

/// `text.strip()` — Python `str.strip()` strips ASCII + Unicode whitespace.
fn py_strip(s: &str) -> &str {
    s.trim()
}

fn _strip_reasoning_prose(text: &str) -> String {
    if text.is_empty() || py_strip(text).is_empty() {
        return text.to_string();
    }
    let stripped = py_strip(text);
    let paragraphs: Vec<&str> = PARAGRAPH_SPLIT_RE.split(stripped).collect();
    if paragraphs.len() <= 1 {
        return text.to_string();
    }
    // Strip only a LEADING contiguous run of reasoning paragraphs. Scanning all
    // paragraphs for the *last* reasoning index destroyed the real answer when a
    // reasoning-style sentence trailed the content block.
    let mut first_keep = 0usize;
    for (i, p) in paragraphs.iter().enumerate() {
        // `re.match` is anchored at the start of the string; the pattern's `^`
        // plus the `(?i)` flag reproduce `_REASONING_PREFIX_RE.match(p)`.
        if REASONING_PREFIX_RE.is_match(p) {
            first_keep = i + 1;
        } else {
            break;
        }
    }
    if first_keep == 0 {
        return text.to_string();
    }
    let keep = &paragraphs[first_keep..];
    if keep.is_empty() {
        return text.to_string();
    }
    py_strip(&keep.join("\n\n")).to_string()
}

/// Strip `<think>` blocks from model output.
///
/// Args:
///   prose: also strip untagged "reasoning prose" paragraphs. Risky on user
///     content (false-positives on phrases like "Looking at the attached
///     file…"); only enable for short LLM-only outputs and only when a
///     `<think>` tag was actually present in the input — callers can use
///     the `had_think` semantics by passing `prose=True` only when they
///     know the input is LLM-only.
///   prompt_echo: also strip Qwen "Thinking Process:" blocks and
///     "The user asks:" / "We need to" leaked prompt echoes.
///
/// Robust to:
///   * closed `<think>...</think>` (any depth, both `<think>` and `<thinking>`)
///   * dangling unclosed `<think>...`
///   * stray opener/closer tags
///   * `<think time="0.42">`-style attributes
pub fn strip_think(text: &str, prose: bool, prompt_echo: bool) -> String {
    if text.is_empty() {
        return String::new();
    }
    // Normalize attributes so the closed/open regexes can catch them.
    let text = THINK_ATTR_RE.replace_all(text, "<think>");
    let text = THINK_ATTR_CLOSE_RE.replace_all(&text, "</think>");
    // Multi-pass for nested blocks.
    let mut prev: Option<String> = None;
    let mut out: String = text.into_owned();
    while prev.as_deref() != Some(out.as_str()) {
        prev = Some(out.clone());
        out = THINK_CLOSED_RE.replace_all(&out, "").into_owned();
    }
    out = THINK_OPEN_RE.replace_all(&out, "").into_owned();
    out = THINK_TAG_RE.replace_all(&out, "").into_owned();
    if prompt_echo {
        out = QWEN_THINKING_RE.replace_all(&out, "").into_owned();
        for re in PROMPT_ECHO_RES.iter() {
            out = re.replace_all(&out, "").into_owned();
        }
    }
    if prose {
        out = _strip_reasoning_prose(&out);
    }
    py_strip(&out).to_string()
}

/// `strip_think` with the Python default keyword arguments
/// (`prose=False`, `prompt_echo=True`).
pub fn strip_think_default(text: &str) -> String {
    strip_think(text, false, true)
}

/// Back-compat alias for the deep-research code path. Keeps existing imports
/// from `src.research_utils` working while delegating to the central impl.
pub fn strip_thinking(text: &str) -> String {
    strip_think(text, false, true)
}

#[cfg(test)]
mod tests {
    use super::*;

    // ── THINK_OPEN_RE: dangling opener anywhere, not just at the start ─────────

    #[test]
    fn dangling_opener_at_start() {
        // Previously handled by the anchored regex; must still work.
        let input = "<think>some unclosed reasoning";
        assert_eq!(strip_think(input, false, false), "");
    }

    #[test]
    fn dangling_opener_mid_string() {
        // Old anchored regex left the opener in place when it wasn't at position 0.
        let input = "Here is my answer. <think>some unclosed reasoning";
        let result = strip_think(input, false, false);
        assert!(!result.contains("<think>"), "dangling mid-string opener should be stripped: {result:?}");
        assert!(result.contains("Here is my answer"), "content before opener should survive: {result:?}");
    }

    #[test]
    fn dangling_opener_after_closed_block() {
        // A closed block followed by a dangling opener — both must be stripped.
        let input = "<think>closed</think> good answer <think>leaked tail";
        let result = strip_think(input, false, false);
        assert_eq!(result, "good answer");
    }

    #[test]
    fn thinking_variant_dangling() {
        // `<thinking>` variant with dangling open.
        let input = "Answer text <thinking>some leaked CoT";
        let result = strip_think(input, false, false);
        assert!(!result.contains("<thinking>"), "dangling <thinking> should be stripped: {result:?}");
        assert!(result.contains("Answer text"), "content before opener should survive: {result:?}");
    }

    #[test]
    fn closed_think_block_unaffected() {
        // Sanity: closed blocks still work after changing THINK_OPEN_RE.
        let input = "<think>internal reasoning</think>The actual answer.";
        assert_eq!(strip_think(input, false, false), "The actual answer.");
    }

    // ── _strip_reasoning_prose: LEADING contiguous run only ───────────────────

    #[test]
    fn prose_strips_leading_run_only() {
        // Two reasoning paragraphs followed by the real answer.
        let input = "Let me think about this.\n\nI need to draft a reply.\n\nHere is your answer with details.";
        let result = strip_think(input, true, false);
        assert_eq!(result, "Here is your answer with details.");
    }

    #[test]
    fn prose_trailing_reasoning_sentence_does_not_destroy_answer() {
        // Old code: last_reasoning_idx scan would find the trailing reasoning
        // sentence and discard everything above it, leaving only the last para.
        // New code: stops at the first non-reasoning paragraph, so the real
        // answer is kept even when a reasoning-style phrase follows it.
        let input = concat!(
            "Let me think about this.\n\n",
            "Here is the full answer with all the details you need.\n\n",
            "Let me check one more time."  // trailing reasoning-style sentence
        );
        let result = strip_think(input, true, false);
        // The answer block must survive; the leading reasoning prefix is stripped.
        assert!(
            result.contains("Here is the full answer"),
            "real answer should be preserved: {result:?}"
        );
    }

    #[test]
    fn prose_no_reasoning_prefix_returns_unchanged() {
        let input = "Here is a direct answer with no reasoning prefix at all.";
        let result = strip_think(input, true, false);
        assert_eq!(result, input);
    }

    #[test]
    fn prose_single_paragraph_returns_unchanged() {
        let input = "Let me think about this single paragraph only.";
        let result = strip_think(input, true, false);
        // Single paragraph: the function returns early, no stripping.
        assert_eq!(result, input);
    }

    #[test]
    fn prose_all_reasoning_paragraphs_returns_original() {
        // When every paragraph matches reasoning, keep is empty → return text.
        let input = "Let me think about this.\n\nI need to write a reply.";
        let result = strip_think(input, true, false);
        // keep slice is empty; must return the original text, not empty string.
        assert!(!result.is_empty(), "should not return empty when all paragraphs are reasoning: {result:?}");
    }

    // ── Combined smoke test ────────────────────────────────────────────────────

    #[test]
    fn think_tag_plus_prose_combined() {
        let input = concat!(
            "<think>deep internal chain-of-thought</think>",
            "Let me draft the reply now.\n\n",
            "Dear user, here is the response."
        );
        let result = strip_think(input, true, false);
        assert_eq!(result, "Dear user, here is the response.");
    }
}
