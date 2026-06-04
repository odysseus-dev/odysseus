//! DB-backed fidelity tests for the `web`+`db` translation of
//! `routes/compare_routes.py` (proof-batch P8).
//!
//! The five handlers in `src/routes/compare_routes.rs` are thin wrappers over the
//! `comparisons` table: `INSERT` on start/record, owner-scoped `SELECT` +
//! conditional `UPDATE` on vote, owner-scoped `SELECT`/`DELETE` on delete, and an
//! owner-filtered `SELECT ... ORDER BY created_at DESC LIMIT 50` on history. The
//! handler bodies need a full `AppState` (18 manager Arcs) to call directly, so —
//! exactly as `tests/session_manager.rs` pins the manager's documented behavior
//! against a real bundled SQLite DB — this test pins the SQL the handlers run
//! against the same schema, asserting the load-bearing owner-scope (strict 404),
//! blind-mapping persistence, and ordering/limit semantics the Python relies on.
//!
//! Everything lives in ONE sequential test because the DB URL is a process-global
//! env var (matching the other db-feature integration tests, which avoid the
//! parallel-env race).
//!
//! Run with: `cargo test --features "web db"`

use odysseus::core::database as db;
use rusqlite::{params, Connection, OptionalExtension};

fn set_db(path: &str) {
    std::env::set_var("DATABASE_URL", format!("sqlite:///{path}"));
}

/// Insert a comparison row exactly the way `start_comparison` / `record_comparison`
/// do (the columns + the TimestampMixin `created_at`/`updated_at`).
fn insert_comparison(
    conn: &Connection,
    id: &str,
    owner: Option<&str>,
    prompt: &str,
    blind_mapping: Option<&str>,
    winner: Option<&str>,
    created_at: &str,
) {
    conn.execute(
        "INSERT INTO comparisons \
           (id, prompt, model_a, model_b, endpoint_a, endpoint_b, winner, is_blind, \
            blind_mapping, owner, created_at, updated_at) \
         VALUES (?1, ?2, 'ma', 'mb', 'ea', 'eb', ?3, 1, ?4, ?5, ?6, ?6)",
        params![id, prompt, winner, blind_mapping, owner, created_at],
    )
    .unwrap();
}

#[test]
// The vote SELECT is read into a faithful 5-column row tuple mirroring the
// comparisons schema 1:1; a type alias would obscure the column mapping.
#[allow(clippy::type_complexity)]
fn compare_routes_db_behavior() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("app.db").to_string_lossy().into_owned();
    set_db(&path);
    db::init_db();
    let conn = Connection::open(&path).unwrap();
    conn.execute_batch("PRAGMA foreign_keys = OFF;").unwrap();

    // --- start/record INSERT shape: owner stored, blind_mapping persisted --------
    insert_comparison(
        &conn,
        "c1",
        Some("alice"),
        "hello",
        Some(r#"{"left": "b", "right": "a"}"#),
        None,
        "2026-06-01 10:00:00",
    );
    let (owner, mapping, winner): (Option<String>, Option<String>, Option<String>) = conn
        .query_row(
            "SELECT owner, blind_mapping, winner FROM comparisons WHERE id='c1'",
            [],
            |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)),
        )
        .unwrap();
    assert_eq!(owner.as_deref(), Some("alice"));
    assert_eq!(mapping.as_deref(), Some(r#"{"left": "b", "right": "a"}"#));
    assert!(winner.is_none(), "freshly-started comparison has no winner");

    // --- vote: the SELECT the handler runs, then the conditional UPDATE ----------
    // The handler reads (owner, winner, blind_mapping, model_a, model_b) by id.
    let row: Option<(Option<String>, Option<String>, Option<String>, String, String)> = conn
        .query_row(
            "SELECT owner, winner, blind_mapping, model_a, model_b FROM comparisons WHERE id='c1'",
            [],
            |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?, r.get(4)?)),
        )
        .optional()
        .unwrap();
    let (owner, existing_winner, bm, _ma, _mb) = row.expect("row present");
    assert_eq!(owner.as_deref(), Some("alice"));
    assert!(existing_winner.is_none());
    // Blind mapping says left->b: a "left" vote records the physical "b".
    let mapping: serde_json::Value = serde_json::from_str(&bm.unwrap()).unwrap();
    assert_eq!(mapping["left"], serde_json::json!("b"));
    conn.execute(
        "UPDATE comparisons SET winner=?1, voted_at=?2, updated_at=?2 WHERE id='c1'",
        params!["b", "2026-06-01 10:05:00"],
    )
    .unwrap();
    let (w, voted): (Option<String>, Option<String>) = conn
        .query_row(
            "SELECT winner, voted_at FROM comparisons WHERE id='c1'",
            [],
            |r| Ok((r.get(0)?, r.get(1)?)),
        )
        .unwrap();
    assert_eq!(w.as_deref(), Some("b"));
    assert_eq!(voted.as_deref(), Some("2026-06-01 10:05:00"));

    // --- owner scope: a row owned by someone else is invisible to alice ----------
    insert_comparison(&conn, "c2", Some("bob"), "bobs", None, Some("tie"), "2026-06-01 09:00:00");
    // A null-owner (legacy) row is STILL owner-scoped-out for a named user under the
    // history WHERE owner = ? filter (strict, mirroring the Python query).
    insert_comparison(&conn, "c3", None, "legacy", None, Some("a"), "2026-06-01 08:00:00");

    // history for alice: WHERE owner='alice' ORDER BY created_at DESC LIMIT 50.
    let mut stmt = conn
        .prepare(
            "SELECT id FROM comparisons WHERE owner = ?1 ORDER BY created_at DESC LIMIT 50",
        )
        .unwrap();
    let alice_ids: Vec<String> = stmt
        .query_map(params!["alice"], |r| r.get::<_, String>(0))
        .unwrap()
        .map(Result::unwrap)
        .collect();
    assert_eq!(alice_ids, vec!["c1".to_string()], "alice only sees her own row");

    // history with no user (anonymous): no WHERE filter, newest-first.
    let mut stmt_all = conn
        .prepare("SELECT id FROM comparisons ORDER BY created_at DESC LIMIT 50")
        .unwrap();
    let all_ids: Vec<String> = stmt_all
        .query_map([], |r| r.get::<_, String>(0))
        .unwrap()
        .map(Result::unwrap)
        .collect();
    // created_at desc: c1 (10:00) > c2 (09:00) > c3 (08:00).
    assert_eq!(all_ids, vec!["c1".to_string(), "c2".to_string(), "c3".to_string()]);

    // --- delete owner scope: the handler 404s if user set and owner mismatches ---
    // Reproduce the handler's decision: load owner, compare to the acting user.
    let owner_of_c2: Option<Option<String>> = conn
        .query_row("SELECT owner FROM comparisons WHERE id='c2'", [], |r| {
            r.get::<_, Option<String>>(0)
        })
        .optional()
        .unwrap();
    let owner_of_c2 = owner_of_c2.expect("c2 exists").as_deref().map(str::to_string);
    // alice acting on bob's row -> 404 path (no DELETE issued).
    let alice = Some("alice");
    let blocked = matches!(alice, Some(u) if owner_of_c2.as_deref() != Some(u));
    assert!(blocked, "alice must not be able to delete bob's comparison");

    // bob acting on his own row -> DELETE proceeds.
    let bob = Some("bob");
    let allowed = !matches!(bob, Some(u) if owner_of_c2.as_deref() != Some(u));
    assert!(allowed);
    conn.execute("DELETE FROM comparisons WHERE id='c2'", []).unwrap();
    let gone: Option<i64> = conn
        .query_row("SELECT 1 FROM comparisons WHERE id='c2'", [], |r| r.get(0))
        .optional()
        .unwrap();
    assert!(gone.is_none(), "c2 deleted");

    // A missing id -> the handler's `if not comp: 404` (no row to load).
    let missing: Option<Option<String>> = conn
        .query_row("SELECT owner FROM comparisons WHERE id='nope'", [], |r| {
            r.get::<_, Option<String>>(0)
        })
        .optional()
        .unwrap();
    assert!(missing.is_none(), "missing id surfaces the 404 branch");
}
