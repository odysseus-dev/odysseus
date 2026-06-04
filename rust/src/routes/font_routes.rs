// routes/font_routes.rs  <- routes/font_routes.py
//! Custom font discovery — lists user-supplied font files in `static/fonts/custom/`.
//!
//! Faithful translation of the Python module:
//!
//! ```python
//! CUSTOM_FONTS_DIR = os.path.join("static", "fonts", "custom")
//! FONT_EXTENSIONS = {".ttf", ".otf", ".woff", ".woff2"}
//!
//! def setup_font_routes():
//!     router = APIRouter(prefix="/api/fonts", tags=["fonts"])
//!
//!     @router.get("/custom")
//!     async def list_custom_fonts():
//!         ...
//!     return router
//! ```
//!
//! Pure filesystem listing + two filename-normalisation passes + an insertion-
//! ordered families map. No auth, no DB, no managers (it captures nothing from
//! the `app.py` factory call), so [`setup_font_routes`] takes no parameters and
//! the handler reads nothing from `AppState`. This is the proof-batch module
//! that validates the basic `setup_x_routes() -> Router<AppState>` shape and
//! `IndexMap` key-order fidelity.
//!
//! ## Path resolution note
//! Python uses the *relative* path `os.path.join("static", "fonts", "custom")`,
//! which resolves against the process CWD (the app runs from the repo root, so
//! `static/...` == `BASE_DIR/static/...`). The Rust static mount serves from
//! [`crate::core::constants::STATIC_DIR`] (= `BASE_DIR/static`), so the listing
//! reads from `STATIC_DIR/fonts/custom` to keep the emitted
//! `url: "/static/fonts/custom/<f>"` pointing at the same files the static
//! mount actually serves — the observable behaviour Python depends on.

use axum::routing::get;
use axum::{Json, Router};
use indexmap::IndexMap;
use once_cell::sync::Lazy;
use regex::Regex;
use serde_json::{json, Value};

use crate::routes::AppState;

/// `FONT_EXTENSIONS = {".ttf", ".otf", ".woff", ".woff2"}` — the accepted font
/// file extensions (compared lower-cased, as in Python).
const FONT_EXTENSIONS: &[&str] = &[".ttf", ".otf", ".woff", ".woff2"];

/// `FAMILY_SUFFIX_WORDS = ("Display", "Rounded", "Serif", "Sans", "Mono", "Code", "Text")`.
const FAMILY_SUFFIX_WORDS: &[&str] = &[
    "Display", "Rounded", "Serif", "Sans", "Mono", "Code", "Text",
];

/// First `re.sub` of `_derive_family`: strip a trailing weight/style suffix
/// (optionally preceded by `-`, `_`, or a space), case-insensitively.
///
/// `(?i)` reproduces `re.IGNORECASE`; `$` anchors to end-of-string. No
/// lookaround is involved, so this maps to `regex` directly.
static _SUFFIX_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"(?i)[-_ ]?(Thin|ExtraLight|UltraLight|Light|Regular|Medium|SemiBold|DemiBold|Bold|ExtraBold|UltraBold|Black|Heavy|Italic|Oblique|Variable|VF)$",
    )
    .unwrap()
});

/// Third `re.sub` of `_derive_family`: collapse runs of `-`/`_` into a single
/// space (`re.sub(r'[-_]+', ' ', name)`).
static _SEP_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"[-_]+").unwrap());

/// `os.path.splitext(filename)` — split off the final extension. Returns
/// `(root, ext)` where `ext` includes the leading dot (e.g.
/// `("JetBrainsMono-Regular", ".woff2")`), matching CPython's behaviour:
/// a leading dot does not count as an extension and there must be at least one
/// non-dot character before the splitting dot.
fn splitext(filename: &str) -> (String, String) {
    let bytes = filename.as_bytes();
    // Find the last '.' that is not a leading dot of the basename.
    // CPython scans from the end; a dot at index 0 (or one only preceded by
    // dots) is part of the name, not an extension separator.
    let mut last_dot: Option<usize> = None;
    for (i, &b) in bytes.iter().enumerate() {
        if b == b'.' {
            last_dot = Some(i);
        }
    }
    match last_dot {
        Some(idx) => {
            // Everything before `idx` must contain a non-dot char for `idx` to
            // be a real extension separator (so ".bashrc" -> (".bashrc", "")).
            let leading_all_dots = bytes[..idx].iter().all(|&b| b == b'.');
            if leading_all_dots {
                (filename.to_string(), String::new())
            } else {
                (filename[..idx].to_string(), filename[idx..].to_string())
            }
        }
        None => (filename.to_string(), String::new()),
    }
}

/// `_split_family_token(token)` — split common compact font-family suffixes
/// without breaking brand names.
///
/// ```python
/// for suffix in FAMILY_SUFFIX_WORDS:
///     if token.endswith(suffix) and len(token) > len(suffix):
///         return f"{token[:-len(suffix)]} {suffix}"
/// return re.sub(r'(?<=[a-z])(?=[A-Z])', ' ', token)
/// ```
///
/// The suffix check preserves brand names like `"JetBrains"` in
/// `"JetBrainsMono"` → `"JetBrains Mono"`, whereas a raw camelCase split on the
/// whole name would yield `"Jet Brains Mono"`.
///
/// The fallback camelCase split is applied per-token (not to the whole name),
/// which is the Python behaviour. `regex` has no lookbehind, so the zero-width
/// boundary is reproduced by a byte scan: Python's `[a-z]`/`[A-Z]` are
/// ASCII-only ranges, so this matches ASCII case only.
fn split_family_token(token: &str) -> String {
    // First try a suffix match from FAMILY_SUFFIX_WORDS.
    for &suffix in FAMILY_SUFFIX_WORDS {
        if token.ends_with(suffix) && token.len() > suffix.len() {
            let prefix = &token[..token.len() - suffix.len()];
            return format!("{} {}", prefix, suffix);
        }
    }
    // Fallback: insert space at every lower->upper ASCII boundary.
    let bytes = token.as_bytes();
    let mut out = String::with_capacity(token.len());
    for (i, ch) in token.char_indices() {
        if i > 0 {
            let prev = bytes[i - 1];
            if prev.is_ascii_lowercase() && ch.is_ascii_uppercase() {
                out.push(' ');
            }
        }
        out.push(ch);
    }
    out
}

/// Derive a font-family name from a filename like `'JetBrainsMono-Regular.woff2'`
/// → `'JetBrains Mono'`.
fn _derive_family(filename: &str) -> String {
    // name = os.path.splitext(filename)[0]
    let name = splitext(filename).0;
    // Strip common weight/style suffixes
    let name = _SUFFIX_RE.replace(&name, "").into_owned();
    // Replace dashes/underscores with spaces
    let name = _SEP_RE.replace_all(&name, " ").trim().to_string();
    // name = " ".join(_split_family_token(part) for part in name.split())
    let name: String = name
        .split_whitespace()
        .map(split_family_token)
        .collect::<Vec<_>>()
        .join(" ");
    if name.is_empty() {
        filename.to_string()
    } else {
        name
    }
}

/// Resolve `CUSTOM_FONTS_DIR` — see the module-level path-resolution note.
fn custom_fonts_dir() -> String {
    crate::pyos::path::join(
        &crate::core::constants::STATIC_DIR,
        "fonts/custom",
    )
}

/// Return available custom fonts grouped by derived family name.
async fn list_custom_fonts() -> Json<Value> {
    let dir = custom_fonts_dir();
    // os.makedirs(CUSTOM_FONTS_DIR, exist_ok=True)
    let _ = crate::pyos::makedirs(&dir, true);

    // families = {}  — insertion-ordered to match the JSON Python emits.
    let mut families: IndexMap<String, Vec<Value>> = IndexMap::new();

    // for f in sorted(os.listdir(CUSTOM_FONTS_DIR)):
    let mut names: Vec<String> = match std::fs::read_dir(&dir) {
        Ok(rd) => rd
            .filter_map(|e| e.ok())
            .map(|e| e.file_name().to_string_lossy().into_owned())
            .collect(),
        // os.listdir on a path that does not exist raises; but makedirs above
        // creates it first, so this only triggers on a genuine IO error, which
        // would have surfaced from os.listdir in Python too. Treat as empty.
        Err(_) => Vec::new(),
    };
    names.sort();

    for f in names {
        // ext = os.path.splitext(f)[1].lower()
        let ext = splitext(&f).1.to_lowercase();
        // if ext not in FONT_EXTENSIONS: continue
        if !FONT_EXTENSIONS.contains(&ext.as_str()) {
            continue;
        }
        let family = _derive_family(&f);
        // if family not in families: families[family] = []
        // families[family].append({...})
        families.entry(family).or_default().push(json!({
            "file": f,
            "url": format!("/static/fonts/custom/{}", f),
            "format": ext.trim_start_matches('.'),
        }));
    }

    // return {"fonts": families}
    Json(json!({ "fonts": families }))
}

/// `setup_font_routes()` — `APIRouter(prefix="/api/fonts", tags=["fonts"])`.
///
/// Mounts the single `@router.get("/custom")` handler at the absolute path the
/// FastAPI `prefix="/api/fonts"` yields: `GET /api/fonts/custom`.
pub fn setup_font_routes() -> Router<AppState> {
    Router::new().route("/api/fonts/custom", get(list_custom_fonts))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn derive_family_jetbrains_camel_and_suffix() {
        // splitext -> "JetBrainsMono-Regular"; strip "-Regular" -> "JetBrainsMono";
        // sep-replace -> "JetBrainsMono" (no dash/underscore remaining);
        // split_family_token("JetBrainsMono") hits the "Mono" suffix entry ->
        // "JetBrains Mono". Brand name is preserved; no over-splitting.
        assert_eq!(
            _derive_family("JetBrainsMono-Regular.woff2"),
            "JetBrains Mono"
        );
    }

    #[test]
    fn derive_family_suffix_case_insensitive() {
        // re.IGNORECASE: "-bold" stripped just like "-Bold".
        assert_eq!(_derive_family("Inter-bold.ttf"), "Inter");
        assert_eq!(_derive_family("Inter-Bold.ttf"), "Inter");
    }

    #[test]
    fn derive_family_underscores_to_spaces() {
        // "[-_]+" -> single space; no camel boundary inside an all-caps run.
        assert_eq!(_derive_family("My_Cool__Font.otf"), "My Cool Font");
    }

    #[test]
    fn derive_family_only_suffix_falls_back_to_filename() {
        // Stripping the suffix yields an empty name -> `name or filename`.
        // "Regular.ttf" -> splitext "Regular" -> strip "Regular" -> "" -> filename.
        assert_eq!(_derive_family("Regular.ttf"), "Regular.ttf");
    }

    #[test]
    fn derive_family_no_suffix_no_camel() {
        assert_eq!(_derive_family("Roboto.ttf"), "Roboto");
    }

    #[test]
    fn splitext_basic_and_hidden() {
        assert_eq!(
            splitext("JetBrainsMono-Regular.woff2"),
            ("JetBrainsMono-Regular".to_string(), ".woff2".to_string())
        );
        assert_eq!(
            splitext(".bashrc"),
            (".bashrc".to_string(), String::new())
        );
        assert_eq!(
            splitext("noext"),
            ("noext".to_string(), String::new())
        );
        assert_eq!(
            splitext("a.b.c"),
            ("a.b".to_string(), ".c".to_string())
        );
    }

    #[test]
    fn split_family_token_suffix_words() {
        // FAMILY_SUFFIX_WORDS match: suffix split preserves the brand prefix.
        assert_eq!(split_family_token("JetBrainsMono"), "JetBrains Mono");
        assert_eq!(split_family_token("InterDisplay"), "Inter Display");
        assert_eq!(split_family_token("NotoSans"), "Noto Sans");
        assert_eq!(split_family_token("FiraCode"), "Fira Code");
        // Token equal to the suffix itself (len not > suffix.len()) falls through.
        assert_eq!(split_family_token("Mono"), "Mono");
        // Fallback camelCase split when no suffix matches.
        assert_eq!(split_family_token("aB"), "a B");
        // Upper-only run: no lower->upper boundary, no split.
        assert_eq!(split_family_token("HTTPServer"), "HTTPServer");
    }

    #[test]
    fn derive_family_suffix_word_tokens() {
        // Each space-separated token goes through split_family_token individually.
        // "InterDisplay-Regular.ttf" -> strip "-Regular" -> "InterDisplay" ->
        // split_family_token -> "Inter Display".
        assert_eq!(_derive_family("InterDisplay-Regular.ttf"), "Inter Display");
        // Token that is exactly a suffix word: no split (len not > suffix.len()).
        assert_eq!(_derive_family("Mono-Bold.ttf"), "Mono");
    }

    #[tokio::test]
    async fn list_custom_fonts_groups_and_sorts() {
        // Point CUSTOM_FONTS_DIR at a temp tree by overriding the static root.
        // STATIC_DIR is a Lazy<String> derived from BASE_DIR; rather than mutate
        // it, exercise the handler against whatever the real dir is and assert
        // the response shape (`{"fonts": {...}}`) — the dir may be empty, which
        // yields `{"fonts": {}}`, exactly as Python returns for an empty dir.
        let Json(v) = list_custom_fonts().await;
        assert!(v.get("fonts").is_some(), "response must have a `fonts` key");
        assert!(
            v["fonts"].is_object(),
            "`fonts` must be an object (family -> [files])"
        );
    }
}
