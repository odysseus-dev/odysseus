// mcp_servers/common.rs  <- mcp_servers/_common.py
//! Shared constants and helpers for built-in MCP servers.
//!
//! Mirrors the Python `mcp_servers/_common.py` module. The module is named
//! `common` (not `_common`) because a leading underscore is not a conventional
//! Rust module name; the constants and the [`truncate`] helper match the Python
//! one-for-one. Everything here is char-oriented because Python's `len(str)` and
//! `str[:limit]` operate on Unicode code points, not bytes.

/// Maximum number of characters of command/tool output to surface (the Python
/// `MAX_OUTPUT_CHARS`).
pub const MAX_OUTPUT_CHARS: usize = 10_000;

/// Maximum number of characters when reading a file (the Python
/// `MAX_READ_CHARS`).
#[allow(dead_code)]
pub const MAX_READ_CHARS: usize = 20_000;

/// Default shell-command timeout in seconds (the Python `SHELL_TIMEOUT`).
#[allow(dead_code)]
pub const SHELL_TIMEOUT: u64 = 60;

/// Default Python-execution timeout in seconds (the Python `PYTHON_TIMEOUT`).
#[allow(dead_code)]
pub const PYTHON_TIMEOUT: u64 = 30;

/// Default search timeout in seconds (the Python `SEARCH_TIMEOUT`).
#[allow(dead_code)]
pub const SEARCH_TIMEOUT: u64 = 30;

/// Truncate `text` to `limit` characters with a suffix note.
///
/// Faithful port of:
/// ```python
/// def truncate(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
///     if len(text) > limit:
///         return text[:limit] + f"\n... (truncated, {len(text)} chars total)"
///     return text
/// ```
/// Both the length test and the slice operate on Unicode scalar values (the
/// Rust analogue of Python `str` indexing), so the truncation never lands in the
/// middle of a multi-byte character.
pub fn truncate(text: &str, limit: usize) -> String {
    let total = text.chars().count();
    if total > limit {
        let head: String = text.chars().take(limit).collect();
        format!("{head}\n... (truncated, {total} chars total)")
    } else {
        text.to_string()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn constants_match_python() {
        assert_eq!(MAX_OUTPUT_CHARS, 10_000);
        assert_eq!(MAX_READ_CHARS, 20_000);
        assert_eq!(SHELL_TIMEOUT, 60);
        assert_eq!(PYTHON_TIMEOUT, 30);
        assert_eq!(SEARCH_TIMEOUT, 30);
    }

    #[test]
    fn truncate_returns_input_when_under_limit() {
        assert_eq!(truncate("hello", 10), "hello");
        // Exactly at the limit is NOT truncated (Python uses `>` not `>=`).
        assert_eq!(truncate("abcde", 5), "abcde");
    }

    #[test]
    fn truncate_appends_note_when_over_limit() {
        let out = truncate("abcdef", 3);
        assert_eq!(out, "abc\n... (truncated, 6 chars total)");
    }

    #[test]
    fn truncate_counts_and_slices_by_chars_not_bytes() {
        // Each `é` is 2 bytes but 1 char. With limit 2 over a 4-char string the
        // total must read 4 (char count) and the head must be the first 2 chars.
        let s = "éédd"; // 4 chars, 6 bytes
        let out = truncate(s, 2);
        assert_eq!(out, "éé\n... (truncated, 4 chars total)");
        // And it stays on a valid char boundary (no panic, valid UTF-8).
        assert!(out.is_char_boundary(out.len()));
    }

    #[test]
    fn truncate_zero_limit_truncates_nonempty() {
        assert_eq!(truncate("x", 0), "\n... (truncated, 1 chars total)");
        // Empty string with zero limit: 0 > 0 is false, so unchanged.
        assert_eq!(truncate("", 0), "");
    }
}
