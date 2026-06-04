//! Behavioural tests for the `db`-feature translation of
//! `core/session_manager.py`, run against a real (bundled) SQLite database.
//!
//! As in `tests/database.rs`, everything lives in ONE sequential test because
//! `database_url()` reads a process-global env var (avoids the parallel-env
//! race). These assertions are the fidelity check for the manager — there is no
//! Python unit test for `session_manager.py`, so the behaviour is pinned here
//! against the documented Python semantics.
//!
//! Run with: `cargo test --features db`

use odysseus::core::database as db;
use odysseus::core::models::{self, ChatMessage};
use odysseus::core::session_manager::SessionManager;
use odysseus::error::PyError;
use rusqlite::Connection;
use std::sync::Arc;

fn set_db(path: &str) {
    std::env::set_var("DATABASE_URL", format!("sqlite:///{path}"));
}

fn count(conn: &Connection, sql: &str) -> i64 {
    conn.query_row(sql, [], |r| r.get::<_, i64>(0)).unwrap()
}

#[test]
fn session_manager_end_to_end() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("app.db").to_string_lossy().into_owned();
    set_db(&path);
    db::init_db();
    // Mirror the pysqlite / `open_db` posture (FK enforcement OFF) so this
    // test's direct setup inserts behave like the manager's own connections.
    let conn = Connection::open(&path).unwrap();
    conn.execute_batch("PRAGMA foreign_keys = OFF;").unwrap();

    let manager = Arc::new(SessionManager::new());

    // --- create_session: returns a Session + writes the row (headers '{}') ---
    let s = manager
        .create_session("s1", "First", "http://x/v1", "gpt-4", false, Some("alice"))
        .unwrap();
    assert_eq!(s.id, "s1");
    assert_eq!(s.name, "First");
    assert_eq!(s.owner.as_deref(), Some("alice"));
    assert!(s.history.is_empty() && s.message_count == 0);
    let (owner, headers): (Option<String>, Option<String>) = conn
        .query_row("SELECT owner, headers FROM sessions WHERE id='s1'", [], |r| {
            Ok((r.get(0)?, r.get(1)?))
        })
        .unwrap();
    assert_eq!(owner.as_deref(), Some("alice"));
    assert_eq!(headers.as_deref(), Some("{}"));
    // last_accessed is populated at INSERT (Python's `default=func.now()`).
    let la_new: Option<String> = conn
        .query_row("SELECT last_accessed FROM sessions WHERE id='s1'", [], |r| r.get(0))
        .unwrap();
    assert!(la_new.is_some(), "create_session populates last_accessed via CURRENT_TIMESTAMP");

    // --- add_message: persists, bumps count + last_message_at, tags `_db_id` ---
    manager
        .add_message("s1", ChatMessage::new("user", "hi", None))
        .unwrap();
    manager
        .add_message("s1", ChatMessage::new("assistant", "hello", None))
        .unwrap();
    assert_eq!(count(&conn, "SELECT COUNT(*) FROM chat_messages WHERE session_id='s1'"), 2);
    let (mc, lma): (i64, Option<String>) = conn
        .query_row(
            "SELECT message_count, last_message_at FROM sessions WHERE id='s1'",
            [],
            |r| Ok((r.get(0)?, r.get(1)?)),
        )
        .unwrap();
    assert_eq!(mc, 2, "message_count tracks history length");
    assert!(lma.is_some(), "last_message_at set on persist");
    // Each persisted row carries a non-null timestamp (Python's
    // `timestamp = Column(DateTime, default=datetime.utcnow)`); without it the
    // ORDER BY timestamp history reload would be corrupted vs replace_messages.
    assert_eq!(
        count(&conn, "SELECT COUNT(*) FROM chat_messages WHERE session_id='s1' AND timestamp IS NOT NULL"),
        2,
        "add_message sets timestamp"
    );

    // The in-memory message carries its DB id (manager-owned add_message path).
    let s1 = manager.get_session("s1").unwrap();
    assert_eq!(s1.history.len(), 2);
    assert_eq!(s1.history[0].content, "hi");
    assert_eq!(s1.history[1].content, "hello");
    let db_id = s1.history[1]
        .metadata
        .as_ref()
        .and_then(|m| m.get("_db_id"))
        .and_then(|v| v.as_str());
    assert!(db_id.is_some_and(|v| !v.is_empty()), "_db_id tagged on the stored message");

    // --- hydration: a FRESH manager loads metadata-only, then hydrates on read ---
    let manager2 = SessionManager::new();
    // load_sessions seeded s1 as metadata-only: count known, history empty.
    let meta = manager2.get_sessions_for_user(None);
    assert!(meta.contains_key("s1"));
    assert_eq!(meta["s1"].message_count, 2);
    assert!(meta["s1"].history.is_empty(), "metadata-only before first read");
    // get_session hydrates messages from the DB, in timestamp order, with _db_id.
    let hyd = manager2.get_session("s1").unwrap();
    assert_eq!(hyd.history.len(), 2);
    assert_eq!(hyd.history[0].content, "hi");
    assert_eq!(hyd.history[1].content, "hello");
    assert!(hyd.history[0].metadata.as_ref().unwrap().contains_key("_db_id"));

    // --- headers round-trip: a JSON object column hydrates into the model ---
    conn.execute(
        "INSERT INTO sessions (id, name, endpoint_url, model, headers, message_count, archived, created_at, updated_at) \
         VALUES ('sh', 'H', 'u', 'm', '{\"X-Test\":\"1\"}', 1, 0, '2025-01-01 00:00:00.000000', '2025-01-01 00:00:00.000000')",
        [],
    ).unwrap();
    conn.execute(
        "INSERT INTO chat_messages (id, session_id, role, content, timestamp) \
         VALUES ('hm1','sh','user','x','2025-01-01 00:00:00.000000')",
        [],
    ).unwrap();
    let sh = SessionManager::new().get_session("sh").unwrap();
    assert_eq!(sh.headers.get("X-Test").map(String::as_str), Some("1"));

    // A double-encoded headers value (a JSON *string* whose content is an object,
    // i.e. a legacy `json.dumps(dict)` stored into the JSON column) is decoded
    // twice — matching Python's `isinstance(str) -> json.loads` branch.
    let double_encoded = r#""{\"X-Enc\":\"2\"}""#;
    conn.execute(
        "INSERT INTO sessions (id, name, endpoint_url, model, headers, message_count, archived, created_at, updated_at) \
         VALUES ('sh2', 'H2', 'u', 'm', ?1, 1, 0, '2025-01-01 00:00:00.000000', '2025-01-01 00:00:00.000000')",
        [double_encoded],
    ).unwrap();
    conn.execute(
        "INSERT INTO chat_messages (id, session_id, role, content, timestamp) \
         VALUES ('hm2','sh2','user','x','2025-01-01 00:00:00.000000')",
        [],
    ).unwrap();
    let sh2 = SessionManager::new().get_session("sh2").unwrap();
    assert_eq!(sh2.headers.get("X-Enc").map(String::as_str), Some("2"), "double-encoded headers");

    // --- get_session on a missing id raises KeyError ---
    match manager.get_session("ghost") {
        Err(PyError::Key(m)) => assert!(m.contains("ghost"), "KeyError mentions the id: {m}"),
        other => panic!("expected KeyError, got {other:?}"),
    }

    // --- truncate_messages: keep only the first N (DB + memory) ---
    assert!(manager.truncate_messages("s1", 1).unwrap());
    assert_eq!(count(&conn, "SELECT COUNT(*) FROM chat_messages WHERE session_id='s1'"), 1);
    assert_eq!(manager.get_session("s1").unwrap().history.len(), 1);
    assert!(!manager.truncate_messages("s1", -1).unwrap(), "negative keep_count -> false");

    // --- replace_messages: atomic swap; monotonic timestamps; _db_id set ---
    let replacement = vec![
        ChatMessage::new("user", "a", None),
        ChatMessage::new("assistant", "b", None),
        ChatMessage::new("user", "c", None),
    ];
    assert!(manager.replace_messages("s1", replacement).unwrap());
    assert_eq!(count(&conn, "SELECT COUNT(*) FROM chat_messages WHERE session_id='s1'"), 3);
    assert_eq!(count(&conn, "SELECT message_count FROM sessions WHERE id='s1'"), 3);
    // Timestamps strictly increase (now + i µs) so reads come back a,b,c.
    let ordered: Vec<String> = {
        let mut stmt = conn
            .prepare("SELECT content FROM chat_messages WHERE session_id='s1' ORDER BY timestamp")
            .unwrap();
        stmt.query_map([], |r| r.get::<_, String>(0))
            .unwrap()
            .filter_map(|r| r.ok())
            .collect()
    };
    assert_eq!(ordered, vec!["a", "b", "c"]);
    let after = manager.get_session("s1").unwrap();
    assert_eq!(after.history.len(), 3);
    assert!(after.history[2].metadata.as_ref().unwrap().contains_key("_db_id"));

    // --- update_session_name / archive_session (cache-gated early return) ---
    manager.update_session_name("s1", "Renamed").unwrap();
    assert_eq!(
        conn.query_row("SELECT name FROM sessions WHERE id='s1'", [], |r| r.get::<_, String>(0))
            .unwrap(),
        "Renamed"
    );
    assert_eq!(manager.get_session("s1").unwrap().name, "Renamed");
    // Not in cache -> no-op (no error), DB untouched.
    manager.update_session_name("not-cached", "X").unwrap();

    manager.archive_session("s1").unwrap();
    assert_eq!(count(&conn, "SELECT archived FROM sessions WHERE id='s1'"), 1);

    // --- mark_important: updates existing; raises KeyError when the row is gone ---
    manager.mark_important("s1", true).unwrap();
    assert_eq!(count(&conn, "SELECT is_important FROM sessions WHERE id='s1'"), 1);
    match manager.mark_important("ghost", true) {
        Err(PyError::Key(m)) => assert!(m.contains("ghost")),
        other => panic!("expected KeyError, got {other:?}"),
    }

    // --- get_sessions_for_user: owner filter over the in-memory cache ---
    manager
        .create_session("s2", "Bob's", "u", "m", false, Some("bob"))
        .unwrap();
    let alices = manager.get_sessions_for_user(Some("alice"));
    assert!(alices.contains_key("s1") && !alices.contains_key("s2"));
    let all = manager.get_sessions_for_user(None);
    assert!(all.contains_key("s1") && all.contains_key("s2"));

    // --- delete_session: detaches documents, deletes messages + row; cache drop ---
    conn.execute(
        "INSERT INTO documents (id, session_id, title, current_content, created_at, updated_at) \
         VALUES ('d1','s2','Doc','body','2025-01-01 00:00:00.000000','2025-01-01 00:00:00.000000')",
        [],
    ).unwrap();
    assert!(manager.delete_session("s2"));
    assert!(db::get_session_by_id("s2").is_none(), "session row gone");
    let doc_sid: Option<String> = conn
        .query_row("SELECT session_id FROM documents WHERE id='d1'", [], |r| r.get(0))
        .unwrap();
    assert_eq!(doc_sid, None, "document detached, not deleted");
    assert!(!manager.get_sessions_for_user(None).contains_key("s2"), "dropped from cache");
    assert!(!manager.delete_session("s2"), "second delete -> false");

    // --- delete_session transaction: a missing session row commits NOTHING ---
    // (Python returns False without commit; the detach + message-delete roll back.)
    conn.execute(
        "INSERT INTO chat_messages (id, session_id, role, content, timestamp) \
         VALUES ('om1','orphan','user','o','2025-01-01 00:00:00.000000')",
        [],
    ).unwrap();
    conn.execute(
        "INSERT INTO documents (id, session_id, title, current_content, created_at, updated_at) \
         VALUES ('od1','orphan','D','b','2025-01-01 00:00:00.000000','2025-01-01 00:00:00.000000')",
        [],
    ).unwrap();
    assert!(!manager.delete_session("orphan"), "no sessions row -> false");
    assert_eq!(
        count(&conn, "SELECT COUNT(*) FROM chat_messages WHERE session_id='orphan'"),
        1,
        "message-delete rolled back (no commit when session absent)"
    );
    let od_sid: Option<String> = conn
        .query_row("SELECT session_id FROM documents WHERE id='od1'", [], |r| r.get(0))
        .unwrap();
    assert_eq!(od_sid.as_deref(), Some("orphan"), "document-detach rolled back");

    // --- models global path: Session.add_message -> _persist_message persists ---
    manager
        .create_session("g1", "Global", "u", "m", false, None)
        .unwrap();
    models::set_session_manager(manager.clone());
    let before = count(&conn, "SELECT COUNT(*) FROM chat_messages WHERE session_id='g1'");
    let mut detached = models::Session {
        id: "g1".to_string(),
        ..Default::default()
    };
    detached.add_message(ChatMessage::new("user", "via-global", None));
    let inserted = count(&conn, "SELECT COUNT(*) FROM chat_messages WHERE session_id='g1'") - before;
    assert_eq!(inserted, 1, "the module-global _persist_message wrote the row");
    assert_eq!(
        conn.query_row("SELECT content FROM chat_messages WHERE session_id='g1'", [], |r| r
            .get::<_, String>(0))
            .unwrap(),
        "via-global"
    );

    // --- save_sessions is a no-op (DB compatibility) ---
    manager.save_sessions();

    // --- fork/peek cache-sync invariant (regression: "fork copies 0 messages
    // after a streamed turn"). The chat route does get_session() -> CLONE, then
    // Session::add_message on the clone (persists DB rows but does NOT touch the
    // in-memory cache). peek_session / fork_session read the cache with NO DB
    // hydration, so the route must mirror the clone's history back via
    // with_session_mut. This pins both halves. (models::set_session_manager was
    // installed above, so the clone's add_message reaches _persist_message.) ---
    manager
        .create_session("fk", "Fork src", "u", "m", false, None)
        .unwrap();
    let mut clone = manager.get_session("fk").unwrap();
    clone.add_message(ChatMessage::new("user", "q", None));
    clone.add_message(ChatMessage::new("assistant", "a", None));
    // The clone diverged: its mutations did NOT reach the cache on their own.
    assert!(
        manager.peek_session("fk").unwrap().history.is_empty(),
        "clone-only mutations must NOT appear in the cache (the bug this guards)"
    );
    // The route's mirror-back.
    manager.with_session_mut("fk", |c| {
        c.history = clone.history.clone();
        c.message_count = clone.message_count;
    });
    let peeked = manager.peek_session("fk").unwrap();
    assert_eq!(
        peeked.history.len(),
        2,
        "after mirror-back, peek_session/fork see the streamed turn"
    );
    assert_eq!(peeked.history[0].content, "q");
    assert_eq!(peeked.history[1].content, "a");

    // --- cleanup_empty_sessions: faithfully reproduces the Python naive-vs-aware
    // TypeError. Run on a FRESH isolated DB so the scenario is deterministic
    // (the manager opens session_local() per call, which reads DATABASE_URL, so
    // every path1 op above must already be done before we switch). ---
    let dir2 = tempfile::tempdir().unwrap();
    let path2 = dir2.path().join("app.db").to_string_lossy().into_owned();
    set_db(&path2);
    db::init_db();
    let conn2 = Connection::open(&path2).unwrap();
    conn2.execute_batch("PRAGMA foreign_keys = OFF;").unwrap();
    let mgr2 = SessionManager::new();
    let old = "2000-01-01 00:00:00.000000";

    // (A) No-crash path: the only non-empty session is ARCHIVED (so the
    // naive<aware comparison is never reached). cleanup deletes the empty row and
    // commits; archived_old stays 0 (Python never reaches the archive UPDATE).
    conn2.execute(
        "INSERT INTO sessions (id, name, endpoint_url, model, message_count, archived, is_important, last_accessed, created_at, updated_at) \
         VALUES ('e1','E','u','m',0,0,0,?1,?1,?1)",
        [old],
    ).unwrap();
    conn2.execute(
        "INSERT INTO sessions (id, name, endpoint_url, model, message_count, archived, is_important, last_accessed, created_at, updated_at) \
         VALUES ('arch','A','u','m',5,1,0,?1,?1,?1)",
        [old],
    ).unwrap();
    let ok = mgr2.cleanup_empty_sessions(30).unwrap();
    assert_eq!(ok["deleted_empty"].as_i64().unwrap(), 1, "empty committed-deleted");
    assert_eq!(ok["archived_old"].as_i64().unwrap(), 0, "archive UPDATE is unreachable in Python");
    assert_eq!(count(&conn2, "SELECT COUNT(*) FROM sessions WHERE id='e1'"), 0);

    // (B) Crash path: a non-archived, non-empty session with a non-null
    // last_accessed makes Python compare naive<aware -> TypeError -> rollback +
    // re-raise. The empty 'e2' DELETE rolls back; 'live' is never archived.
    conn2.execute(
        "INSERT INTO sessions (id, name, endpoint_url, model, message_count, archived, is_important, last_accessed, created_at, updated_at) \
         VALUES ('e2','E2','u','m',0,0,0,?1,?1,?1)",
        [old],
    ).unwrap();
    conn2.execute(
        "INSERT INTO sessions (id, name, endpoint_url, model, message_count, archived, is_important, last_accessed, created_at, updated_at) \
         VALUES ('live','L','u','m',5,0,0,?1,?1,?1)",
        [old],
    ).unwrap();
    match mgr2.cleanup_empty_sessions(30) {
        Err(PyError::Other(m)) => assert!(m.contains("offset-naive"), "TypeError message: {m}"),
        other => panic!("expected the naive/aware TypeError reproduction, got {other:?}"),
    }
    assert_eq!(
        count(&conn2, "SELECT COUNT(*) FROM sessions WHERE id='e2'"),
        1,
        "empty-session DELETE rolled back on the crash (no commit)"
    );
    assert_eq!(count(&conn2, "SELECT archived FROM sessions WHERE id='live'"), 0, "live never archived");
}
