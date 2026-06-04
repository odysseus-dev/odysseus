//! Tests for the `db`-feature translation of core/database.py, run against a
//! real (bundled) SQLite database in a temp dir.
//!
//! All assertions live in ONE test: `database_url()` reads a process-global env
//! var, so a single sequential test avoids the parallel-env race.
//!
//! Run with: `cargo test --features db`

use odysseus::core::database as db;
use rusqlite::Connection;

fn set_db(path: &str) {
    // edition 2021: set_var is safe.
    std::env::set_var("DATABASE_URL", format!("sqlite:///{path}"));
}

/// List the user tables created by init_db (excludes sqlite internal tables).
fn table_names(conn: &Connection) -> Vec<String> {
    let mut stmt = conn
        .prepare("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")
        .unwrap();
    let rows = stmt.query_map([], |r| r.get::<_, String>(0)).unwrap();
    rows.filter_map(|r| r.ok()).collect()
}

fn now_iso() -> String {
    odysseus::pydatetime::utcnow_naive_iso()
}

#[test]
fn database_end_to_end() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("app.db").to_string_lossy().into_owned();
    set_db(&path);

    // --- init_db on a fresh path creates the full schema ---
    db::init_db();
    assert!(std::path::Path::new(&path).exists(), "db file created");

    let conn = Connection::open(&path).unwrap();
    let tables = table_names(&conn);
    // All 24 Python model tables must exist, plus the `vectors` table — a
    // DELIBERATE ChromaDB->sqlite swap with no Python `Base` counterpart (the
    // Python uses an external ChromaDB service; the Rust port backs the vector
    // store with SQLite + in-Rust cosine). So the Rust schema is 24 + 1 = 25.
    let expected = [
        "api_tokens", "calendar_events", "calendars", "chat_messages",
        "comparisons", "crew_members", "document_versions", "documents",
        "editor_drafts", "email_accounts", "gallery_albums", "gallery_images",
        "integrations", "mcp_servers", "memories", "model_endpoints", "notes",
        "scheduled_tasks", "sessions", "signatures", "task_runs", "user_tool_data",
        "user_tools", "webhooks", "vectors",
    ];
    for t in expected {
        assert!(tables.contains(&t.to_string()), "missing table {t}; got {tables:?}");
    }
    assert_eq!(tables.len(), 25, "exactly 25 tables (24 model + vectors swap); got {tables:?}");

    // sessions has every column, including ones added only via migrations
    // (which, on a fresh DB, come from create_all).
    let sess_cols = pragma_cols(&conn, "sessions");
    for c in [
        "id", "name", "endpoint_url", "model", "owner", "rag", "archived",
        "folder", "headers", "last_accessed", "last_message_at", "is_important",
        "message_count", "total_input_tokens", "total_output_tokens", "mode",
        "crew_member_id", "created_at", "updated_at",
    ] {
        assert!(sess_cols.contains(&c.to_string()), "sessions missing col {c}");
    }
    // chat_messages stores the meta_data attr under the column name "metadata".
    assert!(pragma_cols(&conn, "chat_messages").contains(&"metadata".to_string()));

    // --- init_db is idempotent: a second run must not error or duplicate ---
    db::init_db();
    let conn2 = Connection::open(&path).unwrap();
    assert_eq!(table_names(&conn2).len(), 25, "still 25 tables after re-init");

    // --- insert a session + exercise the query helpers ---
    let now = now_iso();
    conn.execute(
        "INSERT INTO sessions (id, name, endpoint_url, model, archived, message_count, created_at, updated_at, last_accessed) \
         VALUES ('s1', 'First', 'http://x/v1', 'gpt-4', 0, 0, ?1, ?1, ?1)",
        [&now],
    )
    .unwrap();
    conn.execute(
        "INSERT INTO sessions (id, name, endpoint_url, model, archived, created_at, updated_at, last_accessed) \
         VALUES ('s2', 'Archived', 'http://x/v1', 'gpt-4', 1, ?1, ?1, ?1)",
        [&now],
    )
    .unwrap();

    // get_session_by_id
    let s = db::get_session_by_id("s1").expect("s1 exists");
    assert_eq!(s.id, "s1");
    assert_eq!(s.name, "First");
    assert!(!s.archived);
    assert!(s.is_active());
    assert!(db::get_session_by_id("nope").is_none());

    // to_dict shape
    let d = s.to_dict();
    assert_eq!(d["id"], serde_json::json!("s1"));
    assert_eq!(d["model"], serde_json::json!("gpt-4"));
    assert_eq!(d["total_input_tokens"], serde_json::json!(0));
    assert!(d.get("name").is_some());
    // Timestamps use Python's isoformat: 'T' separator, no space.
    let ca = d["created_at"].as_str().unwrap();
    assert!(ca.contains('T') && !ca.contains(' '), "isoformat created_at: {ca}");

    // get_session_stats: keys match the Python (total_sessions/active/archived/...).
    let stats = db::get_session_stats();
    assert_eq!(stats["total_sessions"], serde_json::json!(2));
    assert_eq!(stats["archived_sessions"], serde_json::json!(1));
    assert_eq!(stats["active_sessions"], serde_json::json!(1));
    assert_eq!(stats["total_messages"], serde_json::json!(0));
    assert_eq!(stats["total_memories"], serde_json::json!(0));

    // bulk_insert_messages is dead code in the Python and would RAISE if called
    // (chat_messages.id is NOT NULL PRIMARY KEY, no id supplied). The faithful
    // translation propagates that error rather than silently inserting; verified
    // against the real SQLAlchemy DDL. So it must return Err and insert nothing
    // (the transaction rolls back — all-or-nothing).
    let bulk = db::bulk_insert_messages(
        "s1",
        &[("user".into(), "hi".into()), ("assistant".into(), "hello".into())],
    );
    assert!(bulk.is_err(), "bulk_insert_messages mirrors the Python IntegrityError");
    let msg_count: i64 = conn
        .query_row("SELECT COUNT(*) FROM chat_messages WHERE session_id='s1'", [], |r| {
            r.get(0)
        })
        .unwrap();
    assert_eq!(msg_count, 0, "transaction rolled back — nothing inserted");

    // Insert two messages directly (with ids) so the message-count helpers have data.
    conn.execute(
        "INSERT INTO chat_messages (id, session_id, role, content, timestamp) VALUES \
         ('cm1','s1','user','hi',?1),('cm2','s1','assistant','hello',?1)",
        [&now],
    )
    .unwrap();

    // get_detailed_stats = get_session_stats() + database_size_mb (no doc count).
    let ds = db::get_detailed_stats();
    assert_eq!(ds["total_sessions"], serde_json::json!(2));
    assert_eq!(ds["total_messages"], serde_json::json!(2));
    assert!(ds.get("database_size_mb").is_some(), "has database_size_mb");
    assert!(ds.get("documents").is_none(), "no fabricated 'documents' key");

    // archive_session
    assert!(db::archive_session("s1"));
    assert!(db::get_session_by_id("s1").unwrap().archived);
    assert!(!db::archive_session("ghost")); // nonexistent -> false

    // update_session_last_accessed bumps the timestamp + returns true; false for ghost.
    conn.execute("UPDATE sessions SET last_accessed = '2000-01-01 00:00:00.000000' WHERE id='s2'", []).unwrap();
    assert!(db::update_session_last_accessed("s2"));
    assert!(!db::update_session_last_accessed("ghost"));
    let la: String = conn
        .query_row("SELECT last_accessed FROM sessions WHERE id='s2'", [], |r| r.get(0))
        .unwrap();
    assert!(la.as_str() > "2020-01-01", "last_accessed advanced: {la}");

    // cleanup_old_sessions DELETEs archived + old + non-important sessions.
    // Make s2 archived, old, not important -> it should be deleted.
    conn.execute(
        "UPDATE sessions SET archived = 1, is_important = 0, \
         last_accessed = '2000-01-01 00:00:00.000000' WHERE id='s2'",
        [],
    )
    .unwrap();
    let deleted_n = db::cleanup_old_sessions(30);
    assert!(deleted_n >= 1, "cleanup deleted the old archived session, got {deleted_n}");
    assert!(db::get_session_by_id("s2").is_none(), "s2 was deleted");
    // An important old archived session is NOT deleted.
    conn.execute(
        "INSERT INTO sessions (id, name, endpoint_url, model, archived, is_important, created_at, updated_at, last_accessed) \
         VALUES ('s3', 'Keep', 'u', 'm', 1, 1, ?1, ?1, '2000-01-01 00:00:00.000000')",
        [&now],
    )
    .unwrap();
    db::cleanup_old_sessions(30);
    assert!(db::get_session_by_id("s3").is_some(), "important session survives cleanup");
}

fn pragma_cols(conn: &Connection, table: &str) -> Vec<String> {
    let mut stmt = conn.prepare(&format!("PRAGMA table_info({table})")).unwrap();
    let rows = stmt.query_map([], |r| r.get::<_, String>(1)).unwrap();
    rows.filter_map(|r| r.ok()).collect()
}
