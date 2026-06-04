// src/chat_helpers.rs  <- src/chat_helpers.py
//! URL extraction, message/upload validation, request parsing.
//!
//! Only the small *portable* slice of the Python module is ported here:
//! [`extract_urls`], the pure URL-from-text scanner consumed by
//! `chat_processor`/`chat_handler`. The Python file also defines
//! `validate_message`, `validate_file_upload`, and `coerce_message_and_session`,
//! which all hang off FastAPI's `HTTPException` / `UploadFile` and the
//! `session_manager` surface; those are request-layer concerns that land with
//! the route translation, not this pure-helpers pass.
//!
//! The YouTube URL predicate / id-extractor that `chat_handler` also reaches for
//! live in the sibling [`crate::src::youtube_min`] module (kept separate so the
//! Python origin — `src/youtube_handler.py` — stays obvious).

use once_cell::sync::Lazy;
use regex::Regex;

// `url_pattern = r'https?://[^\s<>"{}|\\^`\[\]]+'`
//
// Compiled once (Python recompiles per call via `re.findall`, but the pattern is
// a constant, so a `Lazy` is observationally identical and avoids the re-parse).
static URL_PATTERN: Lazy<Regex> =
    Lazy::new(|| Regex::new(r#"https?://[^\s<>"{}|\\^`\[\]]+"#).unwrap());

// `re.sub(r'[.,;:!?\)]+$', '', url)` — strip a run of trailing punctuation.
static TRAILING_PUNCT: Lazy<Regex> = Lazy::new(|| Regex::new(r"[.,;:!?\)]+$").unwrap());

/// Extract URLs from text using regex pattern.
///
/// Mirrors `chat_helpers.extract_urls`: find every `https?://…` run, then strip a
/// trailing run of `. , ; : ! ? )` from each match. `re.findall` returns
/// non-overlapping matches left-to-right, which `Regex::find_iter` reproduces.
pub fn extract_urls(text: &str) -> Vec<String> {
    let mut cleaned_urls: Vec<String> = Vec::new();
    for m in URL_PATTERN.find_iter(text) {
        let url = m.as_str();
        // `url = re.sub(r'[.,;:!?\)]+$', '', url)` — only the trailing run is
        // affected; `replace` with an empty string matches `re.sub`'s single
        // anchored match.
        let stripped = TRAILING_PUNCT.replace(url, "");
        cleaned_urls.push(stripped.into_owned());
    }
    cleaned_urls
}

#[cfg(test)]
mod tests {
    // Parity checks against the live Python (`src.chat_helpers.extract_urls`).
    use super::*;

    #[test]
    fn plain_url() {
        assert_eq!(
            extract_urls("see https://example.com/page"),
            vec!["https://example.com/page".to_string()]
        );
    }

    #[test]
    fn strips_trailing_punctuation() {
        // Python: re.sub(r'[.,;:!?\)]+$', '', 'https://x.com/a).') -> 'https://x.com/a'
        assert_eq!(
            extract_urls("visit https://x.com/a)."),
            vec!["https://x.com/a".to_string()]
        );
    }

    #[test]
    fn multiple_urls_in_order() {
        let urls = extract_urls("a http://one.test/x and https://two.test/y, end");
        assert_eq!(
            urls,
            vec![
                "http://one.test/x".to_string(),
                "https://two.test/y".to_string(),
            ]
        );
    }

    #[test]
    fn stops_at_disallowed_chars() {
        // The char class excludes whitespace, <>, quotes, braces, etc.
        let urls = extract_urls(r#"<a href="https://h.test/p">click</a>"#);
        assert_eq!(urls, vec!["https://h.test/p".to_string()]);
    }

    #[test]
    fn no_urls() {
        assert!(extract_urls("nothing to see here").is_empty());
    }

    #[test]
    fn query_string_preserved() {
        let urls = extract_urls("https://www.youtube.com/watch?v=abc123 cool");
        assert_eq!(urls, vec!["https://www.youtube.com/watch?v=abc123".to_string()]);
    }
}
