// src/tool_implementations/search_chats.rs  <- src/tool_implementations.py (do_search_chats)
//! `search_chats` tool group — search past chat messages.
//!
//! Faithful port of `tool_implementations.py::do_search_chats` (the only member
//! of this group). The Python builds a SQLAlchemy ORM query joining
//! `ChatMessage` and `Session`; we issue the equivalent raw SQL over a fresh
//! `core::database::session_local()` connection (the documented DB access
//! pattern — no ORM models exist beyond `Session`).
//!
//! All character slicing below mirrors Python's code-point semantics (`str.find`
//! / `content[start:end]` operate on code points, not bytes), so the snippet
//! math is done over a `Vec<char>` rather than Rust byte indices.

use indexmap::IndexMap;
use serde_json::{Map, Value};

use crate::pylog as logger;

/// One grouped-by-session match, mirroring the per-session dict the Python
/// builds in `seen_sessions[session_id] = {...}`. Insertion order into the
/// owning `IndexMap` reproduces Python dict insertion order (load-bearing: the
/// output `lines` iterate `seen_sessions.items()`).
struct SessionMatch {
    name: String,
    snippet: String,
    role: String,
    /// `msg.timestamp.isoformat()` or `None`. Carried so the struct is a faithful
    /// 1:1 of the Python dict; not rendered into `lines` but kept for parity.
    #[allow(dead_code)]
    timestamp: Option<String>,
}

/// Search past chat messages for the calling user's sessions only.
///
/// Without an owner filter this used to leak EVERY user's chat history
/// into the agent's `search_chats` results (v2 review HIGH-11). The
/// caller in `tool_execution.execute_tool_block` now plumbs the owner
/// through; legacy callers without owner pass through as before but
/// will only see legacy/null-owner rows.
pub async fn do_search_chats(query: &str, limit: i64, owner: Option<&str>) -> Map<String, Value> {
    match search_chats_inner(query, limit, owner) {
        Ok(result) => result,
        Err(e) => {
            // except Exception as e: logger.error(...); return {error, exit_code}
            logger::error(&format!("search_chats failed: {e}"));
            let mut err = Map::new();
            err.insert("error".to_string(), Value::String(e.to_string()));
            err.insert("exit_code".to_string(), Value::Number(1.into()));
            err
        }
    }
}

/// The body of the Python `try:` block, factored out so any `rusqlite` error
/// surfaces as the `except Exception` path above (catch-and-return-error-Map —
/// never propagated as a `PyError`/`?` past the public boundary).
fn search_chats_inner(
    query: &str,
    limit: i64,
    owner: Option<&str>,
) -> rusqlite::Result<Map<String, Value>> {
    use crate::core::database::session_local;

    // Escape LIKE wildcards in the user-supplied query so a stray % or _
    // doesn't widen the match (and to keep the response deterministic).
    // Order matters: backslash first, then % and _.
    let safe_q = query
        .replace('\\', "\\\\")
        .replace('%', "\\%")
        .replace('_', "\\_");
    let like_pattern = format!("%{safe_q}%");

    let conn = session_local()?;

    // db.query(ChatMessage, Session.id, Session.name)
    //   .join(Session, ChatMessage.session_id == Session.id)
    //   .filter(Session.archived == False,
    //           ChatMessage.content.ilike(f"%{safe_q}%", escape="\\"),
    //           ChatMessage.role.in_(["user", "assistant"]))
    //   [.filter((Session.owner == owner) | (Session.owner.is_(None)))]
    //   .order_by(ChatMessage.timestamp.desc()).limit(limit).all()
    //
    // SQLAlchemy `.ilike()` compiles to `lower(content) LIKE lower(?)`; emit that
    // exact SQL so behavior matches the Python on the same engine. (On default
    // SQLite both sides' `lower()` and `LIKE` are ASCII-only; an ICU build would
    // make this Unicode-aware uniformly — same as the Python would get.) `ESCAPE
    // '\'` honors the wildcard escaping above.
    let base_sql = "\
        SELECT m.content, m.role, m.timestamp, s.id, s.name \
        FROM chat_messages m \
        JOIN sessions s ON m.session_id = s.id \
        WHERE s.archived = 0 \
          AND lower(m.content) LIKE lower(?1) ESCAPE '\\' \
          AND m.role IN ('user', 'assistant')";

    // Collect rows in result order (timestamp DESC), then group by session in
    // first-seen order — exactly the Python two-step (query then dedupe loop).
    type Row = (String, String, Option<String>, String, String);
    let rows: Vec<Row> = if let Some(owner) = owner {
        // Restrict to this user's sessions plus legacy null-owner rows (so
        // single-user upgrades keep seeing their own data).
        let sql = format!(
            "{base_sql} AND (s.owner = ?2 OR s.owner IS NULL) \
             ORDER BY m.timestamp DESC LIMIT ?3"
        );
        let mut stmt = conn.prepare(&sql)?;
        let mapped = stmt.query_map(
            rusqlite::params![like_pattern, owner, limit],
            |r| {
                Ok((
                    r.get::<_, String>(0)?,         // m.content
                    r.get::<_, String>(1)?,         // m.role
                    r.get::<_, Option<String>>(2)?, // m.timestamp
                    r.get::<_, String>(3)?,         // s.id
                    r.get::<_, Option<String>>(4)?  // s.name
                        .unwrap_or_default(),
                ))
            },
        )?;
        mapped.collect::<rusqlite::Result<Vec<_>>>()?
    } else {
        let sql = format!("{base_sql} ORDER BY m.timestamp DESC LIMIT ?2");
        let mut stmt = conn.prepare(&sql)?;
        let mapped = stmt.query_map(rusqlite::params![like_pattern, limit], |r| {
            Ok((
                r.get::<_, String>(0)?,
                r.get::<_, String>(1)?,
                r.get::<_, Option<String>>(2)?,
                r.get::<_, String>(3)?,
                r.get::<_, Option<String>>(4)?.unwrap_or_default(),
            ))
        })?;
        mapped.collect::<rusqlite::Result<Vec<_>>>()?
    };

    // if not rows: return {"results": f'No chats found matching "{query}".'}
    if rows.is_empty() {
        let mut result = Map::new();
        result.insert(
            "results".to_string(),
            Value::String(format!("No chats found matching \"{query}\".")),
        );
        return Ok(result);
    }

    // Group by session to avoid duplicate links. `IndexMap` preserves first-seen
    // (insertion) order == Python dict order, which `lines` below depends on.
    let mut seen_sessions: IndexMap<String, SessionMatch> = IndexMap::new();
    // query.lower() computed once (Python recomputes per row, but it's invariant).
    let query_lower = query.to_lowercase();

    for (content, role, timestamp, session_id, session_name) in rows {
        if seen_sessions.contains_key(&session_id) {
            continue;
        }
        // content = msg.content or ""  (NOT NULL column, but mirror the guard)
        let snippet = build_snippet(&content, query, &query_lower);

        seen_sessions.insert(
            session_id,
            SessionMatch {
                // session_name or "Untitled"
                name: if session_name.is_empty() {
                    "Untitled".to_string()
                } else {
                    session_name
                },
                snippet,
                role,
                // msg.timestamp.isoformat() if msg.timestamp else None
                timestamp: timestamp.as_deref().map(crate::pydatetime::to_isoformat),
            },
        );
    }

    // lines = [f'Found {len} session(s) matching "{query}":\n']
    let mut lines: Vec<String> = Vec::new();
    lines.push(format!(
        "Found {} session(s) matching \"{query}\":\n",
        seen_sessions.len()
    ));
    for (sid, info) in &seen_sessions {
        // role is included in the dict for parity but, like Python, is not
        // rendered into the output lines.
        let _ = &info.role;
        lines.push(format!("- **{}** (#{sid})", info.name));
        lines.push(format!("  Link: [Open chat](#{sid})"));
        lines.push(format!("  > {}", info.snippet));
        lines.push(String::new());
    }

    let mut result = Map::new();
    result.insert("results".to_string(), Value::String(lines.join("\n")));
    Ok(result)
}

/// Build the highlight snippet for the first match in `content`.
///
/// Mirrors:
/// ```python
/// lower_content = content.lower()
/// idx = lower_content.find(query.lower())
/// if idx == -1:
///     snippet = content[:150]
/// else:
///     start = max(0, idx - 60)
///     end = min(len(content), idx + len(query) + 60)
///     snippet = ("..." if start > 0 else "") + content[start:end] + ("..." if end < len(content) else "")
/// ```
/// Every index here is a Python **code-point** index, so we slice over a
/// `Vec<char>` (Rust's `str::find`/slicing are byte-based and would diverge on
/// multibyte text).
fn build_snippet(content: &str, query: &str, query_lower: &str) -> String {
    let chars: Vec<char> = content.chars().collect();
    let len = chars.len();

    // lower_content.find(query.lower()) — code-point index of the first match,
    // or None (Python's -1).
    let idx = char_find(&content.to_lowercase(), query_lower);

    match idx {
        None => {
            // content[:150]
            chars.iter().take(150).collect()
        }
        Some(idx) => {
            let query_len = query.chars().count();
            // start = max(0, idx - 60)  (idx, query_len are code-point counts)
            let start = idx.saturating_sub(60);
            // end = min(len(content), idx + len(query) + 60)
            let end = (idx + query_len + 60).min(len);

            let mut snippet = String::new();
            if start > 0 {
                snippet.push_str("...");
            }
            snippet.extend(chars[start..end].iter());
            if end < len {
                snippet.push_str("...");
            }
            snippet
        }
    }
}

/// `str.find` over **code points**: index (in chars) of the first occurrence of
/// `needle` in `haystack`, or `None`. Rust's `str::find` returns a byte offset;
/// this converts to the code-point offset Python's slicing then uses.
fn char_find(haystack: &str, needle: &str) -> Option<usize> {
    haystack
        .find(needle)
        .map(|byte_idx| haystack[..byte_idx].chars().count())
}
