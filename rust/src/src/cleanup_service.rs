// src/cleanup_service.rs  <- src/cleanup_service.py
//! Session archival + deletion ("cleanup") operations.
//!
//! PORT_VIA_DB (web). The Python uses SQLAlchemy queries over the `Session` /
//! `ChatMessage` models; the faithful raw-SQL analogue runs against the
//! `sessions` + `chat_messages` tables via `core::database::session_local()` (the
//! ORM models in `src/database.rs` are not ported — see that module's header).
//!
//! The Python `cleanup_old_sessions` evicts deleted sessions from the in-memory
//! `session_manager.sessions` dict after the DB delete
//! (`for id in session_ids: if id in session_manager.sessions: del ...`). The
//! Rust `SessionManager` keeps the same persistent in-memory cache
//! (`sessions: Mutex<IndexMap<String, Session>>`) and `get_session` serves a
//! hydrated cached entry without re-checking the DB, so this port threads the
//! `&SessionManager` through `cleanup_old_sessions` / `cleanup_sessions` and,
//! after the bulk `DELETE FROM sessions`, evicts each deleted id from the cache
//! via `SessionManager::evict_cached_sessions` (cache-only, no DB) — preventing
//! a deleted-but-cached session from being returned until process restart. Every
//! other observable effect (which rows are archived/deleted, the freed-space
//! estimate, the returned tuples) is preserved.

use crate::core::session_manager::SessionManager;
use crate::pylog as logger;
use serde_json::{Map, Value};

/// Configuration constants for cleanup operations.
pub struct CleanupConfig;

impl CleanupConfig {
    pub const ARCHIVE_AFTER_DAYS: i64 = 7;
    pub const DELETE_AFTER_DAYS: i64 = 14;
    pub const MIN_MESSAGES_TO_KEEP: i64 = 20;
    pub const PRESERVE_RECENT_COUNT: usize = 10;
    pub const PROTECTED_KEYWORDS: [&'static str; 5] =
        ["important", "remember", "save this", "keep", "bookmark"];
    pub const ESTIMATED_MESSAGE_SIZE_BYTES: i64 = 512;
}

/// A `sessions` row, narrowed to the columns the cleanup paths read.
struct SessionRow {
    id: String,
    name: Option<String>,
    last_accessed: Option<String>,
    message_count: i64,
}

/// `cutoff_date = datetime.utcnow() - timedelta(days=days)` rendered as the
/// stored SQLite datetime string (lexicographically comparable — see pydatetime).
fn cutoff(days: i64) -> String {
    let dt = crate::pydatetime::utcnow_naive() - chrono::Duration::days(days);
    crate::pydatetime::naive_to_iso(dt)
}

/// `_apply_owner_filter` — build the owner `WHERE` fragment + bound params.
///
/// SECURITY: strict — only rows owned by this user. `owner is None` -> no filter.
/// Returns the SQL fragment to AND into the WHERE clause (empty when no owner)
/// and the parameter vector (the owner, or empty).
fn apply_owner_filter(owner: Option<&str>) -> (String, Vec<String>) {
    match owner {
        None => (String::new(), Vec::new()),
        Some(o) => (" AND owner = ?".to_string(), vec![o.to_string()]),
    }
}

/// Archive sessions that haven't been accessed in the configured number of days.
///
/// Returns the number of sessions archived.
pub async fn archive_inactive_sessions(owner: Option<&str>) -> i64 {
    let cutoff_date = cutoff(CleanupConfig::ARCHIVE_AFTER_DAYS);

    let conn = match crate::core::database::session_local() {
        Ok(c) => c,
        Err(e) => {
            logger::error(&format!("Error archiving sessions: {e}"));
            return 0;
        }
    };

    let (owner_sql, owner_params) = apply_owner_filter(owner);
    let result: rusqlite::Result<i64> = (|| {
        // q = filter(last_accessed < cutoff, archived == False) + owner filter
        // -> set archived=True, updated_at=utcnow() for each.
        let now = crate::pydatetime::utcnow_naive_iso();
        // Bind now (?1), cutoff (?2), then owner (?3) if present. The per-row loop
        // + single commit collapses to one UPDATE with identical effect.
        let sql = format!(
            "UPDATE sessions SET archived = 1, updated_at = ?1 \
             WHERE last_accessed < ?2 AND archived = 0{owner_sql}"
        );
        let mut params: Vec<String> = vec![now, cutoff_date.clone()];
        params.extend(owner_params.clone());
        let n = conn.execute(&sql, rusqlite::params_from_iter(params.iter()))?;
        Ok(n as i64)
    })();

    match result {
        Ok(archived_count) => {
            if archived_count > 0 {
                logger::info(&format!("Archived {archived_count} inactive sessions"));
            }
            archived_count
        }
        Err(e) => {
            // except Exception: logger.error + rollback (no commit happened).
            logger::error(&format!("Error archiving sessions: {e}"));
            0
        }
    }
}

/// Load `sessions` rows for a predicate, in optional `created_at DESC` order.
fn query_sessions(
    conn: &rusqlite::Connection,
    where_sql: &str,
    params: &[String],
    order_by_created_desc: bool,
) -> rusqlite::Result<Vec<SessionRow>> {
    let order = if order_by_created_desc {
        " ORDER BY created_at DESC"
    } else {
        ""
    };
    let sql = format!(
        "SELECT id, name, last_accessed, message_count FROM sessions WHERE {where_sql}{order}"
    );
    let mut stmt = conn.prepare(&sql)?;
    let rows = stmt.query_map(rusqlite::params_from_iter(params.iter()), |r| {
        Ok(SessionRow {
            id: r.get(0)?,
            name: r.get(1)?,
            last_accessed: r.get(2)?,
            message_count: r.get::<_, Option<i64>>(3)?.unwrap_or(0),
        })
    })?;
    rows.collect()
}

/// Delete old sessions based on specific criteria.
///
/// Returns `(number of sessions deleted, space freed in MB)`.
pub async fn cleanup_old_sessions(
    session_manager: &SessionManager,
    owner: Option<&str>,
) -> (i64, f64) {
    let cutoff_date = cutoff(CleanupConfig::DELETE_AFTER_DAYS);
    let mut deleted_count: i64 = 0;
    let mut space_freed: i64 = 0;
    // Ids deleted by the committed DELETE, evicted from the in-memory cache after
    // the transaction succeeds (the Python `del session_manager.sessions[id]` loop).
    let mut deleted_ids: Vec<String> = Vec::new();

    let conn = match crate::core::database::session_local() {
        Ok(c) => c,
        Err(e) => {
            logger::error(&format!("Error cleaning up old sessions: {e}"));
            return (0, 0.0);
        }
    };

    let (owner_sql, owner_params) = apply_owner_filter(owner);

    let result: rusqlite::Result<()> = (|| {
        // recent_session_ids = {first 10 by created_at DESC}
        let recent_where = format!("1=1{owner_sql}");
        let all_sessions = query_sessions(&conn, &recent_where, &owner_params, true)?;
        let recent_session_ids: std::collections::HashSet<String> = all_sessions
            .iter()
            .take(CleanupConfig::PRESERVE_RECENT_COUNT)
            .map(|s| s.id.clone())
            .collect();

        // base_query: archived==True, last_accessed<cutoff, is_important==False,
        // message_count < MIN_MESSAGES_TO_KEEP + owner filter.
        let base_where = format!(
            "archived = 1 AND last_accessed < ? AND is_important = 0 AND message_count < ?{owner_sql}"
        );
        let mut base_params: Vec<String> = vec![
            cutoff_date.clone(),
            CleanupConfig::MIN_MESSAGES_TO_KEEP.to_string(),
        ];
        base_params.extend(owner_params.clone());
        let candidate_sessions = query_sessions(&conn, &base_where, &base_params, false)?;

        let mut sessions_to_delete: Vec<SessionRow> = Vec::new();
        for session in candidate_sessions {
            if recent_session_ids.contains(&session.id) {
                continue;
            }
            if session.message_count >= CleanupConfig::MIN_MESSAGES_TO_KEEP {
                continue;
            }
            let session_name_lower = session.name.as_deref().unwrap_or("").to_lowercase();
            if CleanupConfig::PROTECTED_KEYWORDS
                .iter()
                .any(|kw| session_name_lower.contains(kw))
            {
                continue;
            }
            sessions_to_delete.push(session);
        }

        // space_freed += COUNT(chat_messages WHERE session_id=?) * 512, per session.
        for session in &sessions_to_delete {
            let message_count: i64 = conn.query_row(
                "SELECT COUNT(*) FROM chat_messages WHERE session_id = ?1",
                [&session.id],
                |r| r.get::<_, i64>(0),
            )?;
            space_freed += message_count * CleanupConfig::ESTIMATED_MESSAGE_SIZE_BYTES;
        }

        let session_ids: Vec<String> = sessions_to_delete.iter().map(|s| s.id.clone()).collect();
        if !session_ids.is_empty() {
            // DELETE FROM sessions WHERE id IN (...) — one DELETE, then commit.
            let placeholders =
                session_ids.iter().map(|_| "?").collect::<Vec<_>>().join(", ");
            let sql = format!("DELETE FROM sessions WHERE id IN ({placeholders})");
            conn.execute(&sql, rusqlite::params_from_iter(session_ids.iter()))?;
            deleted_count = session_ids.len() as i64;
            // Python then evicts each id from session_manager.sessions AFTER the
            // commit. Hand the ids back so the caller can do that once the
            // transaction has succeeded (mirrors `for id in session_ids: ...`).
            deleted_ids = session_ids;
        }
        Ok(())
    })();

    if let Err(e) = result {
        // except Exception: logger.error + rollback. The DELETE never committed
        // (error path), so the in-memory cache is left untouched — matching
        // Python, where the cache eviction loop runs only after `db.commit()`.
        logger::error(&format!("Error cleaning up old sessions: {e}"));
        return (deleted_count, 0.0);
    }

    // for session_id in session_ids:
    //     if session_id in session_manager.sessions: del session_manager.sessions[session_id]
    if !deleted_ids.is_empty() {
        session_manager.evict_cached_sessions(&deleted_ids);
    }

    if deleted_count > 0 {
        let space_freed_mb = space_freed as f64 / (1024.0 * 1024.0);
        logger::info(&format!(
            "Deleted {deleted_count} old sessions, freeing approximately {space_freed_mb:.2} MB"
        ));
        return (deleted_count, space_freed_mb);
    }

    (deleted_count, 0.0)
}

/// `round(x, 2)` — Python's banker's-free round-half-to-even? No: Python's
/// built-in `round` is round-half-to-even, but the values here (KB / MB) are
/// floats far from .xx5 ties in practice. We use the half-away-from-zero
/// arithmetic round used elsewhere in the port for the JSON-preview numbers.
fn round2(x: f64) -> f64 {
    (x * 100.0).round() / 100.0
}

/// `session.last_accessed.isoformat() if session.last_accessed else "Unknown"`.
fn last_accessed_iso(s: &Option<String>) -> String {
    match s {
        Some(v) => crate::pydatetime::to_isoformat(v),
        None => "Unknown".to_string(),
    }
}

/// Get a preview of what would be cleaned up without making changes.
///
/// Returns a dict (JSON object, insertion-order preserved) describing the
/// archive / delete candidates + preserved sessions + estimated freed space.
pub async fn get_cleanup_preview(owner: Option<&str>) -> Value {
    let cutoff_archive = cutoff(CleanupConfig::ARCHIVE_AFTER_DAYS);
    let cutoff_delete = cutoff(CleanupConfig::DELETE_AFTER_DAYS);

    let mut sessions_to_archive: Vec<Value> = Vec::new();
    let mut sessions_to_delete: Vec<Value> = Vec::new();
    let mut estimated_space_freed: i64 = 0;
    let mut preserved_sessions: Vec<Value> = Vec::new();

    let (owner_sql, owner_params) = apply_owner_filter(owner);

    if let Ok(conn) = crate::core::database::session_local() {
        let result: rusqlite::Result<()> = (|| {
            // archive_q: last_accessed < cutoff_archive AND archived == False.
            let archive_where = format!("last_accessed < ? AND archived = 0{owner_sql}");
            let mut archive_params: Vec<String> = vec![cutoff_archive.clone()];
            archive_params.extend(owner_params.clone());
            let archive_candidates =
                query_sessions(&conn, &archive_where, &archive_params, false)?;

            for session in &archive_candidates {
                let mut m = Map::new();
                m.insert("id".to_string(), Value::String(session.id.clone()));
                m.insert("name".to_string(), json_name(&session.name));
                m.insert(
                    "last_accessed".to_string(),
                    Value::String(last_accessed_iso(&session.last_accessed)),
                );
                m.insert("message_count".to_string(), Value::from(session.message_count));
                sessions_to_archive.push(Value::Object(m));
            }

            // recent_session_ids = {first 10 by created_at DESC}
            let recent_where = format!("1=1{owner_sql}");
            let all_sessions = query_sessions(&conn, &recent_where, &owner_params, true)?;
            let recent_session_ids: std::collections::HashSet<String> = all_sessions
                .iter()
                .take(CleanupConfig::PRESERVE_RECENT_COUNT)
                .map(|s| s.id.clone())
                .collect();

            // base_query: archived==True, last_accessed<cutoff_delete,
            // is_important==False, message_count < MIN + owner filter.
            let base_where = format!(
                "archived = 1 AND last_accessed < ? AND is_important = 0 AND message_count < ?{owner_sql}"
            );
            let mut base_params: Vec<String> = vec![
                cutoff_delete.clone(),
                CleanupConfig::MIN_MESSAGES_TO_KEEP.to_string(),
            ];
            base_params.extend(owner_params.clone());
            let candidate_sessions = query_sessions(&conn, &base_where, &base_params, false)?;

            for session in &candidate_sessions {
                if recent_session_ids.contains(&session.id) {
                    let mut m = Map::new();
                    m.insert("id".to_string(), Value::String(session.id.clone()));
                    m.insert("name".to_string(), json_name(&session.name));
                    m.insert(
                        "reason".to_string(),
                        Value::String(format!(
                            "part of last {} sessions",
                            CleanupConfig::PRESERVE_RECENT_COUNT
                        )),
                    );
                    m.insert(
                        "last_accessed".to_string(),
                        Value::String(last_accessed_iso(&session.last_accessed)),
                    );
                    m.insert("message_count".to_string(), Value::from(session.message_count));
                    preserved_sessions.push(Value::Object(m));
                    continue;
                }

                if session.message_count >= CleanupConfig::MIN_MESSAGES_TO_KEEP {
                    let mut m = Map::new();
                    m.insert("id".to_string(), Value::String(session.id.clone()));
                    m.insert("name".to_string(), json_name(&session.name));
                    m.insert(
                        "reason".to_string(),
                        Value::String(format!(
                            "has {}+ messages",
                            CleanupConfig::MIN_MESSAGES_TO_KEEP
                        )),
                    );
                    m.insert(
                        "last_accessed".to_string(),
                        Value::String(last_accessed_iso(&session.last_accessed)),
                    );
                    m.insert("message_count".to_string(), Value::from(session.message_count));
                    preserved_sessions.push(Value::Object(m));
                    continue;
                }

                let session_name_lower = session.name.as_deref().unwrap_or("").to_lowercase();
                let matching_keywords: Vec<&&str> = CleanupConfig::PROTECTED_KEYWORDS
                    .iter()
                    .filter(|kw| session_name_lower.contains(**kw))
                    .collect();
                if let Some(first_kw) = matching_keywords.first() {
                    let mut m = Map::new();
                    m.insert("id".to_string(), Value::String(session.id.clone()));
                    m.insert("name".to_string(), json_name(&session.name));
                    m.insert(
                        "reason".to_string(),
                        Value::String(format!("contains keyword: {}", **first_kw)),
                    );
                    m.insert(
                        "last_accessed".to_string(),
                        Value::String(last_accessed_iso(&session.last_accessed)),
                    );
                    m.insert("message_count".to_string(), Value::from(session.message_count));
                    preserved_sessions.push(Value::Object(m));
                    continue;
                }

                let session_space =
                    session.message_count * CleanupConfig::ESTIMATED_MESSAGE_SIZE_BYTES;
                estimated_space_freed += session_space;

                let mut m = Map::new();
                m.insert("id".to_string(), Value::String(session.id.clone()));
                m.insert("name".to_string(), json_name(&session.name));
                m.insert(
                    "last_accessed".to_string(),
                    Value::String(last_accessed_iso(&session.last_accessed)),
                );
                m.insert("message_count".to_string(), Value::from(session.message_count));
                m.insert(
                    "estimated_size_kb".to_string(),
                    Value::from(round2(session_space as f64 / 1024.0)),
                );
                sessions_to_delete.push(Value::Object(m));
            }
            Ok(())
        })();

        if let Err(e) = result {
            logger::error(&format!("Error generating cleanup preview: {e}"));
        }
    }

    let mut out = Map::new();
    out.insert("sessions_to_archive".to_string(), Value::Array(sessions_to_archive));
    out.insert("sessions_to_delete".to_string(), Value::Array(sessions_to_delete));
    out.insert("preserved_sessions".to_string(), Value::Array(preserved_sessions));
    out.insert(
        "estimated_space_freed_mb".to_string(),
        Value::from(round2(estimated_space_freed as f64 / (1024.0 * 1024.0))),
    );
    Value::Object(out)
}

/// `session.name` -> JSON value (`None` -> null; SQLAlchemy keeps the raw value).
fn json_name(name: &Option<String>) -> Value {
    match name {
        Some(n) => Value::String(n.clone()),
        None => Value::Null,
    }
}

/// Perform complete cleanup operations with error recovery.
///
/// Returns `(archived_count, deleted_count, space_freed_mb)`.
pub async fn cleanup_sessions(
    session_manager: &SessionManager,
    owner: Option<&str>,
) -> (i64, i64, f64) {
    // try: archived_count = await archive_inactive_sessions(...). The helper
    // already swallows its own errors (logger.error), so this never panics; the
    // outer try/except is preserved structurally (defaults stay on failure).
    let archived_count = archive_inactive_sessions(owner).await;

    // try: deleted_count, space_freed_mb = await cleanup_old_sessions(session_manager, ...).
    let (deleted_count, space_freed_mb) = cleanup_old_sessions(session_manager, owner).await;

    (archived_count, deleted_count, space_freed_mb)
}

#[cfg(test)]
mod web_tests {
    use super::*;

    #[test]
    fn config_constants_match_python() {
        assert_eq!(CleanupConfig::ARCHIVE_AFTER_DAYS, 7);
        assert_eq!(CleanupConfig::DELETE_AFTER_DAYS, 14);
        assert_eq!(CleanupConfig::MIN_MESSAGES_TO_KEEP, 20);
        assert_eq!(CleanupConfig::PRESERVE_RECENT_COUNT, 10);
        assert_eq!(CleanupConfig::ESTIMATED_MESSAGE_SIZE_BYTES, 512);
        assert_eq!(
            CleanupConfig::PROTECTED_KEYWORDS,
            ["important", "remember", "save this", "keep", "bookmark"]
        );
    }

    #[test]
    fn owner_filter_strict() {
        // owner=None -> no filter; owner=Some -> strict equality.
        let (sql, params) = apply_owner_filter(None);
        assert_eq!(sql, "");
        assert!(params.is_empty());
        let (sql, params) = apply_owner_filter(Some("alice"));
        assert_eq!(sql, " AND owner = ?");
        assert_eq!(params, vec!["alice".to_string()]);
    }

    #[test]
    fn round2_two_dp() {
        assert_eq!(round2(1.0 / 3.0), 0.33);
        assert_eq!(round2(512.0 / 1024.0), 0.5);
    }

    #[test]
    fn last_accessed_unknown_when_missing() {
        assert_eq!(last_accessed_iso(&None), "Unknown");
        // A stored datetime is rendered as Python isoformat (T separator).
        assert_eq!(
            last_accessed_iso(&Some("2024-01-02 03:04:05.000000".to_string())),
            "2024-01-02T03:04:05"
        );
    }

    #[tokio::test]
    async fn preview_shape_for_unknown_owner() {
        // A non-existent owner yields empty candidate lists + 0.0 freed MB.
        let v = get_cleanup_preview(Some("__no_such_owner_cleanup__")).await;
        assert_eq!(v["sessions_to_archive"].as_array().unwrap().len(), 0);
        assert_eq!(v["sessions_to_delete"].as_array().unwrap().len(), 0);
        assert_eq!(v["preserved_sessions"].as_array().unwrap().len(), 0);
        assert_eq!(v["estimated_space_freed_mb"], serde_json::json!(0.0));
        // Insertion order preserved (serde_json preserve_order).
        let keys: Vec<&str> = v.as_object().unwrap().keys().map(|s| s.as_str()).collect();
        assert_eq!(
            keys,
            vec![
                "sessions_to_archive",
                "sessions_to_delete",
                "preserved_sessions",
                "estimated_space_freed_mb"
            ]
        );
    }
}
