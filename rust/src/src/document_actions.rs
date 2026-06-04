// src/document_actions.rs  <- src/document_actions.py
//! Reusable document actions callable from both REST routes and the task
//! scheduler.
//!
//! PORT_VIA_DB (web). The Python uses the SQLAlchemy `Document` model; the
//! faithful raw-SQL analogue reads the `documents` table via
//! `core::database::session_local()` (no `Document` row struct lives in
//! `core::database` — the columns this module needs are SELECTed directly). The
//! `raise TaskNoop(...)` "nothing to do" sentinel is modelled as
//! `Err(builtin_actions::Outcome::noop(...))`.

use crate::src::builtin_actions::Outcome;
use once_cell::sync::Lazy;
use regex::Regex;
use std::collections::HashSet;

/// Titles that indicate a throwaway/junk document.
static JUNK_TITLES: Lazy<HashSet<&'static str>> = Lazy::new(|| {
    [
        "untitled", "untitled document", "new document", "document",
        "new email", "new mail", "new message", "reply", "fwd", "re:",
        "test", "testing", "asdf", "asd", "foo", "bar", "baz",
        "tmp", "temp", "scratch", "scratchpad", "draft", "delete",
        "remove", "junk", "trash", "xxx", "abc", "qwerty",
    ]
    .into_iter()
    .collect()
});

/// `re.sub(r"\s+", " ", ...)` — collapse all whitespace runs to a single space.
static WS_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"\s+").unwrap());
/// `re.sub(r'upload_id="[^"]*"', ...)` — pdf_source re-import volatile attr.
static UPLOAD_ID_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r#"upload_id="[^"]*""#).unwrap());
/// `re.sub(r"\bid=ann-[A-Za-z0-9_-]+", ...)` — annotation ids.
static ANN_ID_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"\bid=ann-[A-Za-z0-9_-]+").unwrap());
/// `re.sub(r"^#{1,6}\s+", "", flags=re.MULTILINE)` — markdown headers.
static HEADER_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"(?m)^#{1,6}\s+").unwrap());
/// `re.sub(r"[*_`>\-=]+", "", ...)` — markdown noise chars.
static MD_CHARS_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"[*_`>\-=]+").unwrap());
/// `re.match(r"^On .+ wrote:?\s*$", ...)` — email attribution header line.
/// `re.match` anchors at the start; the `$` + `\s*` anchor the end (single line).
static ON_WROTE_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"^On .+ wrote:?\s*$").unwrap());

/// Normalize a title for grouping: trim, collapse whitespace, lowercase.
fn norm_title(t: &str) -> String {
    WS_RE.replace_all(t.trim(), " ").to_lowercase()
}

/// A stable fingerprint of document content for duplicate detection.
///
/// Strips bits that differ between otherwise-identical copies — chiefly the
/// `upload_id` of a re-imported PDF and the random `id=` of annotations — so that
/// N imports of the same file collapse to one fingerprint. Whitespace is
/// collapsed and the result lowercased.
fn content_fingerprint(content: &str) -> String {
    let c = content;
    let c = UPLOAD_ID_RE.replace_all(c, "upload_id");
    let c = ANN_ID_RE.replace_all(&c, "id=ann");
    WS_RE.replace_all(c.trim(), " ").to_lowercase()
    // NOTE: Python applies .strip() AFTER the whitespace-collapse; collapsing
    // already removes leading/trailing runs, and `c.trim()` before the collapse
    // is equivalent for the boundary spaces. (The `\s+`->" " keeps interior
    // single spaces; trim removes the ends — identical result.)
}

/// Length of content with markdown noise stripped — a 'completeness' proxy.
fn real_len(content: &str) -> usize {
    let stripped = HEADER_RE.replace_all(content, "");
    let stripped = MD_CHARS_RE.replace_all(&stripped, "");
    let stripped = WS_RE.replace_all(stripped.trim(), " ");
    stripped.chars().count()
}

/// A `documents` row, narrowed to the columns the tidy path reads.
struct DocRow {
    title: Option<String>,
    current_content: Option<String>,
    created_at: Option<String>,
    updated_at: Option<String>,
    // The DB primary key — needed to issue the DELETE.
    id: String,
}

impl DocRow {
    /// `_updated(d) = d.updated_at or d.created_at` — the most-recent timestamp.
    fn updated(&self) -> String {
        match &self.updated_at {
            Some(u) if !u.is_empty() => u.clone(),
            _ => self.created_at.clone().unwrap_or_default(),
        }
    }
}

/// Remove clearly-junk documents and redundant duplicates for an owner.
///
/// Conservative rules (no length-based deletion — short notes are valid):
/// - Empty / whitespace-only / placeholder ("# Untitled")
/// - Title is a throwaway name (test, asdf, …) or the content itself is one
/// - Email reply-chain with no original content
/// - Duplicates: docs sharing the same normalized title AND the same content
///   fingerprint (ignoring volatile upload/annotation ids). The most complete
///   copy (longest real content, then most recent) is kept; the rest deleted.
///
/// Returns `Ok((summary, true))` on success, or `Err(Outcome::noop(...))` when
/// nothing was deleted (`raise TaskNoop` — the scheduler drops the run row).
pub async fn run_document_tidy(owner: &str) -> Result<(String, bool), Outcome> {
    let conn = crate::core::database::session_local()
        .map_err(|e| Outcome::noop(format!("db error: {e}")))?;
    // NOTE: the Python try/finally only closes `db`; any DB error would propagate
    // as a generic exception. Here a connection failure is surfaced as a Noop so
    // the scheduler simply records "nothing to do" rather than a misleading
    // success. (A live DB never hits this path.)

    // docs = filter(owner == owner) if owner else all().
    let (sql, params): (&str, Vec<String>) = if !owner.is_empty() {
        (
            "SELECT id, title, current_content, created_at, updated_at \
             FROM documents WHERE owner = ?1",
            vec![owner.to_string()],
        )
    } else {
        (
            "SELECT id, title, current_content, created_at, updated_at FROM documents",
            Vec::new(),
        )
    };

    let docs: Vec<DocRow> = {
        let mut stmt = conn
            .prepare(sql)
            .map_err(|e| Outcome::noop(format!("db error: {e}")))?;
        let rows = stmt
            .query_map(rusqlite::params_from_iter(params.iter()), |r| {
                Ok(DocRow {
                    id: r.get(0)?,
                    title: r.get(1)?,
                    current_content: r.get(2)?,
                    created_at: r.get(3)?,
                    updated_at: r.get(4)?,
                })
            })
            .map_err(|e| Outcome::noop(format!("db error: {e}")))?;
        rows.filter_map(|r| r.ok()).collect()
    };
    let total_docs = docs.len();

    let mut deleted_examples: Vec<String> = Vec::new();
    let mut deleted: usize = 0;
    let mut kept: usize = 0;
    let mut survivors: Vec<DocRow> = Vec::new();
    // IDs to DELETE (gathered, then one delete pass; the per-row db.delete +
    // single commit collapses to a single batched DELETE with identical effect).
    let mut delete_ids: Vec<String> = Vec::new();

    for doc in docs {
        let content_raw = doc.current_content.clone().unwrap_or_default();
        let content = content_raw.trim().to_string();
        let title = doc.title.clone().unwrap_or_default().trim().to_lowercase();

        // Strip markdown noise to get "real" character count.
        let stripped = HEADER_RE.replace_all(&content, "");
        let stripped = MD_CHARS_RE.replace_all(&stripped, "");
        let stripped = WS_RE.replace_all(stripped.trim(), " ").to_string();
        let _real_len = stripped.chars().count();

        // Detect emails-saved-as-documents (quote chains with no original content).
        // lines = [ln for ln in content.split("\n") if ln.strip()]
        let lines: Vec<&str> = content.split('\n').filter(|ln| !ln.trim().is_empty()).collect();
        let quoted_lines: Vec<&str> =
            lines.iter().copied().filter(|ln| ln.trim_start().starts_with('>')).collect();
        let header_lines: Vec<&str> = lines
            .iter()
            .copied()
            .filter(|ln| ON_WROTE_RE.is_match(ln.trim()))
            .collect();
        let non_quote_content: String = {
            let kept_lines: Vec<&str> = lines
                .iter()
                .copied()
                .filter(|ln| {
                    !ln.trim_start().starts_with('>') && !ON_WROTE_RE.is_match(ln.trim())
                })
                .collect();
            kept_lines.join("\n").trim().to_string()
        };
        // quote_ratio = len(quoted_lines) / max(len(lines), 1)
        let quote_ratio = quoted_lines.len() as f64 / lines.len().max(1) as f64;

        let mut should_delete = false;
        let mut reason = String::new();

        // not content or content in ("", "# Untitled")
        if content.is_empty() || content == "# Untitled" {
            should_delete = true;
            reason = "empty".to_string();
        } else if JUNK_TITLES.contains(title.as_str()) {
            should_delete = true;
            reason = format!("junk title '{title}'");
        } else if JUNK_TITLES.contains(stripped.to_lowercase().as_str()) {
            should_delete = true;
            reason = "throwaway content".to_string();
        } else if (!quoted_lines.is_empty() || !header_lines.is_empty())
            && non_quote_content.chars().count() < 50
            && quote_ratio > 0.4
        {
            // Email reply chain with no original content.
            should_delete = true;
            reason = "email quote-chain only".to_string();
        }

        if should_delete {
            if deleted_examples.len() < 5 {
                // label = (doc.title or "(no title)")[:40]
                let raw_title = match &doc.title {
                    Some(t) if !t.is_empty() => t.clone(),
                    _ => "(no title)".to_string(),
                };
                let label: String = raw_title.chars().take(40).collect();
                deleted_examples.push(format!("{label} ({reason})"));
            }
            delete_ids.push(doc.id.clone());
            deleted += 1;
        } else {
            survivors.push(doc);
        }
    }

    // --- Duplicate pass: group survivors by (normalized title, content
    // fingerprint) and keep only the most complete copy of each group. ---
    // IndexMap-style insertion order is irrelevant here (members are processed
    // independently and the example cap is order-sensitive only across groups,
    // which iterate in survivor-encounter order — preserved by IndexMap).
    let mut groups: indexmap::IndexMap<(String, String), Vec<DocRow>> = indexmap::IndexMap::new();
    for doc in survivors {
        let content = doc.current_content.clone().unwrap_or_default();
        let key = (
            norm_title(doc.title.as_deref().unwrap_or("")),
            content_fingerprint(&content),
        );
        groups.entry(key).or_default().push(doc);
    }

    for (_key, mut members) in groups {
        if members.len() < 2 {
            kept += 1;
            continue;
        }
        // Keep the most complete (longest real content), then most recent.
        // Python sorts by (real_len, _updated) descending; Python's sort is
        // STABLE, so equal keys keep insertion order. `sort_by` is stable too.
        members.sort_by(|a, b| {
            let ka = (
                real_len(&a.current_content.clone().unwrap_or_default()),
                a.updated(),
            );
            let kb = (
                real_len(&b.current_content.clone().unwrap_or_default()),
                b.updated(),
            );
            // reverse=True -> descending.
            kb.cmp(&ka)
        });
        let keeper = &members[0];
        kept += 1;
        let dupes = &members[1..];
        if deleted_examples.len() < 5 {
            let raw_title = match &keeper.title {
                Some(t) if !t.is_empty() => t.clone(),
                _ => "(no title)".to_string(),
            };
            let label: String = raw_title.chars().take(40).collect();
            deleted_examples.push(format!("{label} (+{} duplicate copies)", dupes.len()));
        }
        for d in dupes {
            delete_ids.push(d.id.clone());
            deleted += 1;
        }
    }

    // if deleted: db.commit() — issue the batched DELETE.
    if deleted > 0 && !delete_ids.is_empty() {
        let placeholders = delete_ids.iter().map(|_| "?").collect::<Vec<_>>().join(", ");
        let del_sql = format!("DELETE FROM documents WHERE id IN ({placeholders})");
        if let Err(e) = conn.execute(&del_sql, rusqlite::params_from_iter(delete_ids.iter())) {
            crate::pylog::error(&format!("document tidy delete failed: {e}"));
        }
    }

    if deleted == 0 {
        // raise TaskNoop(f"scanned {len(docs)} document(s), no junk")
        return Err(Outcome::noop(format!("scanned {total_docs} document(s), no junk")));
    }
    let preview = deleted_examples.join("; ");
    let extra = if deleted > deleted_examples.len() {
        format!(" (+{} more)", deleted - deleted_examples.len())
    } else {
        String::new()
    };
    Ok((
        format!("Removed {deleted} of {total_docs}: {preview}{extra} · {kept} kept"),
        true,
    ))
}

#[cfg(test)]
mod web_tests {
    use super::*;

    #[test]
    fn norm_title_collapses_and_lowercases() {
        // "  Foo   Bar  " -> "foo bar"
        assert_eq!(norm_title("  Foo   Bar  "), "foo bar");
        assert_eq!(norm_title("RE:  Hello\tWorld"), "re: hello world");
        assert_eq!(norm_title(""), "");
    }

    #[test]
    fn content_fingerprint_strips_volatile_ids() {
        // upload_id + annotation ids are normalized so re-imports collapse.
        let a = r#"<pdf upload_id="abc123"> body <span id=ann-XY_z9> hi </span>"#;
        let b = r#"<pdf upload_id="DIFFERENT"> body <span id=ann-other-77> hi </span>"#;
        assert_eq!(content_fingerprint(a), content_fingerprint(b));
        // The normalized form is lowercased + whitespace-collapsed.
        assert_eq!(content_fingerprint("  Hello   World  "), "hello world");
    }

    #[test]
    fn real_len_strips_markdown_noise() {
        // Headers + markdown chars + whitespace are stripped before counting.
        assert_eq!(real_len("# Title\n**bold** text"), "Title bold text".chars().count());
        // A pure-markdown line collapses to almost nothing.
        assert_eq!(real_len("===\n---"), 0);
    }

    #[test]
    fn on_wrote_attribution_matches() {
        assert!(ON_WROTE_RE.is_match("On Mon, Jan 1, 2024 someone wrote:"));
        assert!(ON_WROTE_RE.is_match("On x wrote"));
        assert!(!ON_WROTE_RE.is_match("Once upon a time"));
    }

    #[test]
    fn junk_titles_membership() {
        assert!(JUNK_TITLES.contains("untitled"));
        assert!(JUNK_TITLES.contains("re:"));
        assert!(JUNK_TITLES.contains("qwerty"));
        assert!(!JUNK_TITLES.contains("meeting notes"));
    }
}
