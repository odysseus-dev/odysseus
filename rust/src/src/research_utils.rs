// src/research_utils.rs  <- src/research_utils.py
//! Shared utilities for the deep research system.
//!
//! Centralizes text cleaning, quality filtering, and other logic
//! used across deep_research.py, research_handler.py, and visual_report.py.

use once_cell::sync::Lazy;

// ---------------------------------------------------------------------------
// Thinking / reasoning block stripping
// ---------------------------------------------------------------------------

/// Strip thinking / reasoning patterns from LLM output.
///
/// Delegates to `src.text_helpers.strip_think` (single source of truth).
/// Kept as an alias here so existing `from src.research_utils import strip_thinking`
/// callers don't break. Preserves None passthrough — many callers pass an
/// `Optional[str]` LLM result and expect None back when the call failed.
pub fn strip_thinking(text: Option<&str>) -> Option<String> {
    // Preserve None passthrough (the Python `if text is None: return None`).
    let text = text?;
    // `from src.text_helpers import strip_think` (local import).
    use crate::src::text_helpers::strip_think;
    Some(strip_think(text, false, true))
}

// ---------------------------------------------------------------------------
// Source quality filtering
// ---------------------------------------------------------------------------

// Markers indicating extracted content is boilerplate, error text, or empty.
// If any marker is found (case-insensitive), the content is filtered out.
pub static LOW_QUALITY_MARKERS: Lazy<Vec<&'static str>> = Lazy::new(|| {
    vec![
        "insufficient to",
        "content is insufficient",
        "no substantive data",
        "does not contain",
        "not relevant to",
        "no relevant information",
        "unable to extract",
        "completely unrelated",
        "boilerplate",
        "footer text",
        // Phrases (not bare "cookie"/"copyright") so we still catch boilerplate
        // like consent banners and footers without discarding legitimate findings
        // that merely discuss cookies or copyright as their subject.
        "cookie consent",
        "cookie banner",
        "cookie notice",
        "copyright notice",
        "copyright footer",
        "all rights reserved",
    ]
});

/// Check if a finding summary indicates useless or irrelevant content.
pub fn is_low_quality(summary: &str) -> bool {
    // Python wraps this in try/except returning False on any error; in Rust the
    // body is infallible, so the `except` branch is unreachable. The empty-string
    // (Python falsy `not summary`) check is preserved verbatim.
    if summary.is_empty() {
        return true;
    }
    let low = summary.to_lowercase();
    LOW_QUALITY_MARKERS.iter().any(|marker| low.contains(marker))
}

#[cfg(test)]
mod tests {
    use super::*;

    // --- bare "cookie" / "copyright" no longer filter ---

    #[test]
    fn bare_cookie_is_not_low_quality() {
        // A legitimate finding that discusses cookies as its subject should pass.
        assert!(!is_low_quality(
            "This article explains how HTTP cookies work in modern browsers."
        ));
    }

    #[test]
    fn bare_copyright_is_not_low_quality() {
        // A legitimate finding that discusses copyright law should pass.
        assert!(!is_low_quality(
            "The study covers copyright law and fair use doctrine."
        ));
    }

    // --- specific cookie/copyright phrases still filter ---

    #[test]
    fn cookie_consent_is_low_quality() {
        assert!(is_low_quality(
            "Cookie consent banner detected — page content unavailable."
        ));
    }

    #[test]
    fn cookie_banner_is_low_quality() {
        assert!(is_low_quality("cookie banner obscured the main content."));
    }

    #[test]
    fn cookie_notice_is_low_quality() {
        assert!(is_low_quality("This page only returned a cookie notice."));
    }

    #[test]
    fn copyright_notice_is_low_quality() {
        assert!(is_low_quality(
            "Extracted text was a copyright notice in the footer."
        ));
    }

    #[test]
    fn copyright_footer_is_low_quality() {
        assert!(is_low_quality("Content was a copyright footer only."));
    }

    #[test]
    fn all_rights_reserved_is_low_quality() {
        assert!(is_low_quality(
            "All rights reserved. No further content found."
        ));
    }

    // Case-insensitivity check for new markers.
    #[test]
    fn new_markers_are_case_insensitive() {
        assert!(is_low_quality("COOKIE CONSENT wall blocked access."));
        assert!(is_low_quality("ALL RIGHTS RESERVED notice only."));
        assert!(is_low_quality("COPYRIGHT NOTICE in footer."));
    }

    // --- pre-existing marker behaviour unchanged ---

    #[test]
    fn empty_string_is_low_quality() {
        assert!(is_low_quality(""));
    }

    #[test]
    fn boilerplate_is_low_quality() {
        assert!(is_low_quality("This is pure boilerplate text."));
    }

    #[test]
    fn good_summary_passes() {
        assert!(!is_low_quality(
            "The paper presents novel findings on neural architecture search."
        ));
    }
}
