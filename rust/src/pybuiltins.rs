//! `pybuiltins` — Python builtin functions the translation relies on, where
//! Rust's nearest equivalent has different edge-case behaviour.

/// `int(s)` for base-10 string conversion.
///
/// Mirrors CPython more closely than `str::parse::<i64>()`: it strips
/// surrounding (Unicode) whitespace and permits `_` digit separators
/// (`int("1_000") == 1000`, `int(" 24 ") == 24`). Like Python's `int()`, it
/// raises — here, panics — on genuinely non-numeric input (the Python program
/// would also abort at the call site with `ValueError`).
///
/// Deviation: CPython `int` has unbounded precision; this returns `i64`, so a
/// value exceeding `i64::MAX` panics where Python would not. Such values do not
/// occur for the config integers this is used on.
pub fn int(s: &str) -> i64 {
    let t = s.trim();
    // Python permits '_' as a digit separator; strip them (lenient on
    // placement — Python rejects leading/trailing/doubled underscores).
    let cleaned: String = t.chars().filter(|c| *c != '_').collect();
    cleaned
        .parse::<i64>()
        .unwrap_or_else(|_| panic!("invalid literal for int() with base 10: {s:?}"))
}
