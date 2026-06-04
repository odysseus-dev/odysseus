// src/session_actions.rs  <- src/session_actions.py
//! Reusable session actions that can be called from both REST routes and the
//! task scheduler / builtin actions system.
//!
//! The Python uses the SQLAlchemy `Session` / `ChatMessage` models; the faithful
//! raw-SQL analogue runs against `sessions` + `chat_messages` via
//! `core::database::session_local()`. Phase 2 resolves the background-task
//! endpoint via `task_endpoint::resolve_task_endpoint` and calls
//! `llm_core::llm_call_async`, then applies the AI's folder grouping.

use once_cell::sync::Lazy;
use regex::Regex;
use rusqlite::OptionalExtension;
use std::collections::HashSet;

/// Names that indicate a throwaway/test session.
static THROWAWAY_NAMES: Lazy<HashSet<&'static str>> = Lazy::new(|| {
    [
        "test", "testing", "asdf", "asd", "hello", "hi", "hey",
        "yo", "sup", "hola", "hii", "hiii", "heyo",
        "foo", "bar", "baz", "tmp", "temp", "scratch", "untitled",
        "new chat", "delete", "remove", "junk", "trash", "xxx",
        "abc", "qwerty", "blah", "stuff", "whatever", "idk",
        "ok", "lol", "bruh", "hmm", "hm", "meh",
    ]
    .into_iter()
    .collect()
});
const THROWAWAY_MAX_MESSAGES: i64 = 4;

/// `re.search(r'```(?:json)?\s*\n?([\s\S]*?)```', text)` — fenced JSON extractor.
static FENCE_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"```(?:json)?\s*\n?([\s\S]*?)```").unwrap());

/// A `sessions` row narrowed to what Phase 1/2 read.
struct SessRow {
    id: String,
    name: Option<String>,
    folder: Option<String>,
    is_important: bool,
}

/// Run session cleanup + (optional) AI folder sort for the given owner.
///
/// `skip_llm`: when True, do only Phase 1 (delete empty/throwaway sessions);
/// skip Phase 2 (AI folder assignment). Used by the built-in daily background
/// sweep so it never burns LLM tokens.
///
/// Returns a human-readable summary of what was done.
pub async fn run_auto_sort(owner: &str, skip_llm: bool) -> String {
    // db = SessionLocal(); try/finally db.close() (dropped at scope end). The DB
    // work + commits run synchronously up front; the LLM call (Phase 2) happens
    // after, and the connection is reopened for the folder apply so it is not
    // held across the `await` (rusqlite::Connection is not Send).

    // ── Phase 1 + the session-list build run on one connection. ──
    let phase1 = run_phase1(owner);
    let (deleted_empty, deleted_throwaway, session_list) = match phase1 {
        Ok(v) => v,
        Err(e) => {
            // The Python has no try/except around the body (only try/finally), so a
            // DB error would propagate; here we surface it as the summary string to
            // avoid a panic in the background sweep.
            crate::pylog::error(&format!("auto-sort phase 1 failed: {e}"));
            return format!("Auto-sort failed: {e}");
        }
    };

    if session_list.len() < 2 {
        return format!(
            "Cleaned {} sessions. Too few remaining to sort.",
            deleted_empty + deleted_throwaway
        );
    }

    // Background built-in sweep skips folder-sort to stay pure infra.
    if skip_llm {
        return format!(
            "Cleaned {} sessions (folder sort skipped).",
            deleted_empty + deleted_throwaway
        );
    }

    // ── Phase 2: AI folder assignment. `resolve_task_endpoint()` is bare in the
    // Python (session_actions.py) — owner defaults to None.
    let (url, model, headers) =
        crate::src::task_endpoint::resolve_task_endpoint(None, None, None, None);
    let (url, model) = match (url, model) {
        (Some(u), Some(m)) => (u, m),
        (Some(u), None) => (u, String::new()),
        _ => {
            return format!(
                "Cleaned {} sessions. No model endpoint available for sorting.",
                deleted_empty + deleted_throwaway
            );
        }
    };

    // names_text = "\n".join(f'  "{id[:8]}": "{name}"' ...)
    let names_text = session_list
        .iter()
        .map(|s| {
            let prefix: String = s.id.chars().take(8).collect();
            format!("  \"{prefix}\": \"{}\"", s.name)
        })
        .collect::<Vec<_>>()
        .join("\n");
    let prompt = format!(
        "You are a session organizer. Group these chat sessions into folders by topic.\n\n\
         Rules:\n\
         - Be aggressive about grouping — put EVERY session in a folder\n\
         - Use short folder names (2-4 words max)\n\
         - Use the 8-char ID prefixes exactly as given\n\
         - Output ONLY raw JSON, no markdown fences, no explanation\n\n\
         Required JSON format:\n\
         {{\"folders\": {{\"Folder Name\": [\"id_prefix1\", \"id_prefix2\"], \"Other Folder\": [\"id_prefix3\"]}}}}\n\n\
         Sessions (id_prefix: name):\n{{\n{names_text}\n}}"
    );

    // headers Map<String,Value> -> IndexMap<String,String> for llm_call_async.
    let mut hdr_map: indexmap::IndexMap<String, String> = indexmap::IndexMap::new();
    if let Some(h) = headers {
        for (k, v) in h {
            // header values are strings; .as_str() unwraps the JSON String.
            let val = v.as_str().map(|s| s.to_string()).unwrap_or_else(|| v.to_string());
            hdr_map.insert(k, val);
        }
    }

    // raw = await llm_call_async(url, model, [...], 0.3, 16384, headers, 120)
    let messages = vec![serde_json::json!({"role": "user", "content": prompt})];
    let raw = match crate::src::llm_core::llm_call_async(
        &url, &model, messages, 0.3, 16384, hdr_map, 120,
    )
    .await
    {
        Ok(r) => r,
        Err(e) => {
            crate::pylog::warning(&format!("Auto-sort LLM call failed: {e}"));
            return format!(
                "Cleaned {} sessions. Folder sort skipped (model unreachable).",
                deleted_empty + deleted_throwaway
            );
        }
    };

    // Parse JSON from response (3-tier).
    let text = raw.trim();
    let mut result: Option<serde_json::Value> = serde_json::from_str(text).ok();
    if result.is_none() {
        if let Some(cap) = FENCE_RE.captures(text) {
            if let Some(g1) = cap.get(1) {
                result = serde_json::from_str(g1.as_str().trim()).ok();
            }
        }
    }
    if result.is_none() {
        // brace_start = text.find('{'); brace_end = text.rfind('}')
        let brace_start = text.find('{');
        let brace_end = text.rfind('}');
        if let (Some(bs), Some(be)) = (brace_start, brace_end) {
            if be > bs {
                // text[brace_start:brace_end+1] — byte slice on the '{'/'}' bytes
                // (both ASCII, so the byte indices are char boundaries).
                result = serde_json::from_str(&text[bs..=be]).ok();
            }
        }
    }
    let result = match result {
        Some(r) => r,
        None => {
            return format!(
                "Cleaned {} sessions. AI returned unparseable response.",
                deleted_empty + deleted_throwaway
            );
        }
    };

    // folders = result.get("folders", {})
    let folders = result.get("folders").and_then(|f| f.as_object());
    let folders = match folders {
        Some(f) if !f.is_empty() => f,
        _ => {
            return format!(
                "Cleaned {} sessions. No folder groupings found.",
                deleted_empty + deleted_throwaway
            );
        }
    };

    // id_prefix_map = {id[:8]: id for s in session_list}
    // IndexMap preserves session_list order (Python dict comprehension order),
    // which the `for p, fid in id_prefix_map.items()` fuzzy loop relies on.
    let mut id_prefix_map: indexmap::IndexMap<String, String> = indexmap::IndexMap::new();
    for s in &session_list {
        let prefix: String = s.id.chars().take(8).collect();
        id_prefix_map.insert(prefix, s.id.clone());
    }

    // Apply assignments — reopen a connection (not held across the await).
    let conn = match crate::core::database::session_local() {
        Ok(c) => c,
        Err(e) => {
            crate::pylog::error(&format!("auto-sort apply failed: {e}"));
            return format!("Auto-sort failed: {e}");
        }
    };

    let mut updated = 0i64;
    // for folder_name, ids in folders.items() (serde_json::Map preserves order).
    for (folder_name, ids_val) in folders {
        let ids = match ids_val.as_array() {
            Some(a) => a,
            None => continue,
        };
        for sid_val in ids {
            let sid_or_prefix = match sid_val.as_str() {
                Some(s) => s,
                None => continue,
            };
            let mut full_id: Option<String> = None;
            // if sid_or_prefix in id_prefix_map.values()
            if id_prefix_map.values().any(|v| v == sid_or_prefix) {
                full_id = Some(sid_or_prefix.to_string());
            } else {
                // prefix = sid_or_prefix.rstrip(".").rstrip(" ")
                let prefix = sid_or_prefix.trim_end_matches('.').trim_end_matches(' ');
                if let Some(fid) = id_prefix_map.get(prefix) {
                    full_id = Some(fid.clone());
                } else {
                    // fuzzy: first (p, fid) where fid.startswith(prefix) or
                    // prefix.startswith(p).
                    for (p, fid) in &id_prefix_map {
                        if fid.starts_with(prefix) || prefix.starts_with(p.as_str()) {
                            full_id = Some(fid.clone());
                            break;
                        }
                    }
                }
            }
            if let Some(fid) = full_id {
                // db_sess = filter(id==full_id).first(); if db_sess: set folder.
                let now = crate::pydatetime::utcnow_naive_iso();
                let n = conn
                    .execute(
                        "UPDATE sessions SET folder = ?1, updated_at = ?2 WHERE id = ?3",
                        rusqlite::params![folder_name, now, fid],
                    )
                    .unwrap_or(0);
                if n > 0 {
                    updated += 1;
                }
            }
        }
    }

    // folder_summary = ", ".join(f"{k} ({len(v)})" ...)
    let folder_summary = folders
        .iter()
        .map(|(k, v)| {
            let len = v.as_array().map(|a| a.len()).unwrap_or(0);
            format!("{k} ({len})")
        })
        .collect::<Vec<_>>()
        .join(", ");
    format!(
        "Deleted {deleted_empty} empty + {deleted_throwaway} throwaway. \
         Sorted {updated} sessions into: {folder_summary}"
    )
}

/// Phase 1 (delete empty/throwaway) + the Phase-2 candidate list, on one
/// connection (no `.await` inside — the connection is dropped before Phase 2's
/// LLM call). Returns `(deleted_empty, deleted_throwaway, session_list)`.
fn run_phase1(
    owner: &str,
) -> rusqlite::Result<(i64, i64, Vec<SessionEntry>)> {
    let conn = crate::core::database::session_local()?;

    let mut deleted_empty = 0i64;
    let mut deleted_throwaway = 0i64;
    let mut delete_ids: Vec<String> = Vec::new();

    // rows = filter(archived==False, [owner==owner if owner]).all()
    let (sql, params): (&str, Vec<String>) = if !owner.is_empty() {
        (
            "SELECT id, name, folder, is_important FROM sessions \
             WHERE archived = 0 AND owner = ?1",
            vec![owner.to_string()],
        )
    } else {
        (
            "SELECT id, name, folder, is_important FROM sessions WHERE archived = 0",
            Vec::new(),
        )
    };
    let rows: Vec<SessRow> = {
        let mut stmt = conn.prepare(sql)?;
        let mapped = stmt.query_map(rusqlite::params_from_iter(params.iter()), |r| {
            Ok(SessRow {
                id: r.get(0)?,
                name: r.get(1)?,
                folder: r.get(2)?,
                is_important: r.get::<_, Option<bool>>(3)?.unwrap_or(false),
            })
        })?;
        mapped.collect::<rusqlite::Result<Vec<_>>>()?
    };

    for row in &rows {
        // if getattr(row, 'is_important', False): continue
        if row.is_important {
            continue;
        }
        // if (row.name or "").strip() == "Incognito": delete as throwaway.
        let name_stripped = row.name.as_deref().unwrap_or("").trim().to_string();
        if name_stripped == "Incognito" {
            deleted_throwaway += 1;
            delete_ids.push(row.id.clone());
            continue;
        }

        // msg_count = query(id).filter(session_id==row.id).limit(MAX+1).count()
        let msg_count: i64 = conn.query_row(
            "SELECT COUNT(*) FROM (SELECT id FROM chat_messages WHERE session_id = ?1 LIMIT ?2)",
            rusqlite::params![row.id, THROWAWAY_MAX_MESSAGES + 1],
            |r| r.get::<_, i64>(0),
        )?;
        let mut should_delete = false;

        if msg_count == 0 {
            should_delete = true;
            deleted_empty += 1;
        } else if msg_count <= THROWAWAY_MAX_MESSAGES {
            let name = row.name.as_deref().unwrap_or("").trim().to_lowercase();
            // first_msg = first user content by timestamp.
            let first_text: String = conn
                .query_row(
                    "SELECT content FROM chat_messages \
                     WHERE session_id = ?1 AND role = 'user' ORDER BY timestamp LIMIT 1",
                    [&row.id],
                    |r| r.get::<_, Option<String>>(0),
                )
                .optional()?
                .flatten()
                .unwrap_or_default()
                .trim()
                .to_lowercase();
            // assistant_count = query(id).filter(role=='assistant').limit(1).count()
            let assistant_count: i64 = conn.query_row(
                "SELECT COUNT(*) FROM (SELECT id FROM chat_messages \
                 WHERE session_id = ?1 AND role = 'assistant' LIMIT 1)",
                [&row.id],
                |r| r.get::<_, i64>(0),
            )?;

            // Mirrors the Python if/elif at session_actions.py:79-88 1:1: three
            // distinct throwaway conditions share the same body (mark for delete +
            // bump the throwaway counter) — the duplicate branches are deliberate.
            #[allow(clippy::if_same_then_else)]
            if THROWAWAY_NAMES.contains(name.as_str())
                || name.starts_with("chat:")
                || THROWAWAY_NAMES.contains(first_text.as_str())
            {
                should_delete = true;
                deleted_throwaway += 1;
            } else if msg_count == 1 && assistant_count == 0 {
                should_delete = true;
                deleted_throwaway += 1;
            } else if msg_count <= 4
                && !first_text.is_empty()
                && first_text.split_whitespace().count() <= 8
                && first_text.chars().count() <= 80
            {
                // Short trivial chats — e.g. "write hi to a friend" -> "Hi!"
                should_delete = true;
                deleted_throwaway += 1;
            } else {
                // Aggressive: total message text under 250 chars combined = trivial.
                let total_chars: i64 = {
                    let mut stmt = conn.prepare(
                        "SELECT content FROM chat_messages WHERE session_id = ?1",
                    )?;
                    let contents = stmt.query_map([&row.id], |r| {
                        r.get::<_, Option<String>>(0)
                    })?;
                    let mut total = 0i64;
                    for c in contents {
                        let s = c?.unwrap_or_default();
                        total += s.chars().count() as i64;
                    }
                    total
                };
                if total_chars <= 250 {
                    should_delete = true;
                    deleted_throwaway += 1;
                }
            }
        }

        if should_delete {
            delete_ids.push(row.id.clone());
        }
    }

    // if deleted_empty or deleted_throwaway: db.commit() + log.
    if !delete_ids.is_empty() {
        let placeholders = delete_ids.iter().map(|_| "?").collect::<Vec<_>>().join(", ");
        // Delete child messages first (FK enforcement is OFF, matching pysqlite, so
        // the ORM-level cascade is reproduced explicitly). `chat_messages.session_id`
        // is `ondelete="CASCADE"` with `cascade="all, delete-orphan"` on the
        // relationship, so the children are deleted outright.
        let del_msgs = format!("DELETE FROM chat_messages WHERE session_id IN ({placeholders})");
        let _ = conn.execute(&del_msgs, rusqlite::params_from_iter(delete_ids.iter()));
        // Nullify the `ondelete="SET NULL"` dependents. Python deletes each Session
        // through the ORM (`db.delete(row)`), and SQLAlchemy disassociates every
        // child relationship whose FK is SET NULL (cascade is `save-update, merge`,
        // not delete) by issuing `UPDATE <table> SET session_id = NULL WHERE
        // session_id = <deleted>` on flush: documents, memories, scheduled_tasks,
        // gallery_images AND crew_members all declare such a relationship to
        // Session (core/database.py:175/198, 617/623, 515/532, 243/262, 483/489).
        // Since `PRAGMA foreign_keys = OFF` (core::database) means the declarative
        // ON DELETE SET NULL never fires, reproduce it explicitly so those rows do
        // not keep a dangling session_id pointing at a now-deleted session.
        for table in ["documents", "memories", "scheduled_tasks", "gallery_images", "crew_members"] {
            let nullify =
                format!("UPDATE {table} SET session_id = NULL WHERE session_id IN ({placeholders})");
            let _ = conn.execute(&nullify, rusqlite::params_from_iter(delete_ids.iter()));
        }
        // `user_tools` has `backref(..., cascade="all, delete-orphan")` on Session
        // (core/database.py:443), so SQLAlchemy DELETES the session-scoped tool
        // rows outright (not SET NULL) when the session is deleted. Reproduce that.
        let del_tools = format!("DELETE FROM user_tools WHERE session_id IN ({placeholders})");
        let _ = conn.execute(&del_tools, rusqlite::params_from_iter(delete_ids.iter()));
        let del_sess = format!("DELETE FROM sessions WHERE id IN ({placeholders})");
        let _ = conn.execute(&del_sess, rusqlite::params_from_iter(delete_ids.iter()));
    }
    if deleted_empty != 0 || deleted_throwaway != 0 {
        crate::pylog::info(&format!(
            "Auto-sort: deleted {deleted_empty} empty + {deleted_throwaway} throwaway sessions"
        ));
    }

    // ── Phase 2 candidate list: remaining = filter(archived==False, owner). ──
    let deleted_set: HashSet<&String> = delete_ids.iter().collect();
    let mut session_list: Vec<SessionEntry> = Vec::new();
    for row in &rows {
        // The Python re-queries; here we reuse `rows` minus the deleted ids
        // (identical membership — same predicate, the deleted rows are gone).
        if deleted_set.contains(&row.id) {
            continue;
        }
        // if row.name == "Incognito": continue (note: Python compares the RAW name).
        if row.name.as_deref() == Some("Incognito") {
            continue;
        }
        session_list.push(SessionEntry {
            id: row.id.clone(),
            // name = row.name or "(unnamed)"
            name: match &row.name {
                Some(n) if !n.is_empty() => n.clone(),
                _ => "(unnamed)".to_string(),
            },
            _current_folder: row.folder.clone(),
        });
    }

    Ok((deleted_empty, deleted_throwaway, session_list))
}

/// A Phase-2 candidate session (`{id, name, current_folder}`).
struct SessionEntry {
    id: String,
    name: String,
    /// Carried for parity with the Python dict; not read by the apply phase.
    _current_folder: Option<String>,
}

#[cfg(test)]
mod web_tests {
    use super::*;

    #[test]
    fn throwaway_names_and_constant() {
        assert_eq!(THROWAWAY_MAX_MESSAGES, 4);
        assert!(THROWAWAY_NAMES.contains("test"));
        assert!(THROWAWAY_NAMES.contains("new chat"));
        assert!(THROWAWAY_NAMES.contains("qwerty"));
        assert!(!THROWAWAY_NAMES.contains("important"));
        // The exact set size guards against accidental edits to the literal.
        assert_eq!(THROWAWAY_NAMES.len(), 38);
    }

    #[test]
    fn fence_regex_extracts_inner_json() {
        let text = "blah\n```json\n{\"folders\": {}}\n```\ntail";
        let cap = FENCE_RE.captures(text).unwrap();
        assert_eq!(cap.get(1).unwrap().as_str().trim(), "{\"folders\": {}}");
    }

    #[test]
    fn fence_regex_plain_fence() {
        // The (?:json)? group is optional — a bare ``` fence still extracts.
        let text = "```\n{\"a\": 1}\n```";
        let cap = FENCE_RE.captures(text).unwrap();
        assert_eq!(cap.get(1).unwrap().as_str().trim(), "{\"a\": 1}");
    }
}
