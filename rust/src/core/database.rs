// core/database.rs  <- core/database.py
//! Database layer — schema, connection, migrations, and query helpers.
//!
//! The Python uses SQLAlchemy's declarative ORM (24 `Base` models) plus ~28
//! raw-`sqlite3` migration functions and `Base.metadata.create_all`. This is a
//! rusqlite translation:
//!
//!   * `init_db()` runs the migrations in order, then creates any missing tables
//!     (the `create_all` analogue) — the DDL below reproduces every model's
//!     columns, types, nullability, primary/foreign keys, and indexes.
//!   * Each `_migrate_*` is translated 1:1 (raw `PRAGMA table_info` + `ALTER
//!     TABLE`, same guards, same SQL).
//!   * `SessionLocal()` (a per-call ORM session bound to one engine) becomes
//!     `session_local()` — a fresh `Connection` to the same on-disk path.
//!
//! FAITHFUL DEVIATIONS (documented):
//!   * SQLAlchemy `default=<literal>` is applied *client-side* at INSERT, so
//!     `create_all` emits no SQL `DEFAULT`. We emit `DEFAULT <literal>` in the
//!     DDL instead: the stored values are identical (the ORM would have written
//!     them anyway) and raw helpers/tests inserting partial rows stay correct.
//!   * pysqlite leaves `PRAGMA foreign_keys` OFF, so the `ON DELETE` clauses are
//!     declarative-only in Python and the ORM-level cascades do the real work.
//!     rusqlite's *bundled* SQLite is built with `SQLITE_DEFAULT_FOREIGN_KEYS=1`
//!     (FK ON by default — the opposite), so `open_db` explicitly runs
//!     `PRAGMA foreign_keys = OFF` on every connection to match pysqlite. The
//!     clauses stay in the DDL but enforcement is off (see `open_db`).
//!   * Row structs exist only for the tables this file's own helpers query
//!     (`Session`, `ChatMessage`, `Document`, `Memory`); the other 20 tables are
//!     DDL-only until their consumers (route modules) are translated.

use crate::pylog as logger;
use crate::pyos as os;
use rusqlite::{Connection, OptionalExtension};

/// `DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/app.db")`.
pub fn database_url() -> String {
    os::getenv("DATABASE_URL", "sqlite:///./data/app.db")
}

/// `DATABASE_URL.replace("sqlite:///", "")` — the on-disk path the migrations use.
pub fn db_path() -> String {
    // In tests, a thread that set up a PRIVATE test DB via `test_db()` targets
    // that file instead of the process-global `DATABASE_URL` — so concurrent
    // tests never clobber each other's target. Production has no such override.
    #[cfg(test)]
    if let Some(p) = current_thread_test_db() {
        return p;
    }
    database_url().replace("sqlite:///", "")
}

/// Open a connection with the same foreign-key posture as the Python's driver.
///
/// Match the Python's foreign-key posture. Upstream now installs a SQLAlchemy
/// `@event.listens_for(Engine, "connect")` listener that runs `PRAGMA
/// foreign_keys=ON` on EVERY connection (previously pysqlite left it OFF). To stay
/// faithful to source-of-truth we enable FK enforcement per connection too —
/// `rusqlite` bundles SQLite compiled with `SQLITE_DEFAULT_FOREIGN_KEYS=1` so this
/// is also its native default. The port's delete/detach paths already order child
/// deletes before parents, so they remain valid under enforcement.
fn open_db(path: &str) -> rusqlite::Result<Connection> {
    let conn = Connection::open(path)?;
    // pysqlite (and thus SQLAlchemy's default SQLite dialect) opens connections
    // with `sqlite3.connect(..., timeout=5.0)` — a 5s busy timeout. rusqlite
    // instead defaults to 0, failing immediately when the database file is locked
    // by another connection. The app opens a fresh `Connection` per request/op
    // (see `session_local`), so concurrent operations on the same file are normal;
    // match Python and wait for the lock instead of erroring out.
    conn.busy_timeout(std::time::Duration::from_secs(5))?;
    conn.execute_batch("PRAGMA foreign_keys = ON;")?;
    Ok(conn)
}

/// `SessionLocal()` — a fresh connection to the database file.
///
/// The Python's `sessionmaker(bind=engine)` hands out ORM sessions sharing one
/// engine; the faithful raw-SQL analogue is a new `Connection` per call (closed
/// when dropped, matching `db.close()` in every `finally:`).
pub fn session_local() -> rusqlite::Result<Connection> {
    open_db(&db_path())
}

/// `with engine.connect() as conn:` — the migration helpers that use the engine
/// (not raw `sqlite3`) auto-create the DB file; this mirrors that by opening the
/// path (creating it if absent).
fn engine_connect() -> rusqlite::Result<Connection> {
    open_db(&db_path())
}

/// `sqlite3.connect(db_path)` guarded by `if not os.path.exists(db_path): return`.
/// Returns `None` (skip the migration) when the file doesn't exist yet.
fn connect_existing() -> Option<Connection> {
    let path = db_path();
    if !os::path::exists(&path) {
        return None;
    }
    open_db(&path).ok()
}

/// `[row[1] for row in conn.execute("PRAGMA table_info(<table>)")]` — the column
/// names of a table.
fn table_columns(conn: &Connection, table: &str) -> Vec<String> {
    let sql = format!("PRAGMA table_info({table})");
    let mut stmt = match conn.prepare(&sql) {
        Ok(s) => s,
        Err(_) => return Vec::new(),
    };
    let rows = stmt.query_map([], |r| r.get::<_, String>(1));
    match rows {
        Ok(it) => it.filter_map(|r| r.ok()).collect(),
        Err(_) => Vec::new(),
    }
}

// ===========================================================================
// Schema (the `Base.metadata.create_all` analogue)
// ===========================================================================
//
// One `CREATE TABLE IF NOT EXISTS` per model, then the explicit `__table_args__`
// composite indexes plus single-column `index=True` indexes. SQLAlchemy emits
// `VARCHAR`/`TEXT`/`BOOLEAN`/`INTEGER`/`DATETIME`/`JSON`; `EncryptedText` lowers
// to `TEXT`. `created_at`/`updated_at` come from `TimestampMixin` (NOT NULL).

const CREATE_TABLES: &str = r#"
CREATE TABLE IF NOT EXISTS sessions (
    id VARCHAR NOT NULL PRIMARY KEY,
    name VARCHAR NOT NULL,
    endpoint_url VARCHAR NOT NULL,
    model VARCHAR NOT NULL,
    owner VARCHAR,
    rag BOOLEAN DEFAULT 0,
    archived BOOLEAN DEFAULT 0,
    folder VARCHAR DEFAULT NULL,
    headers JSON,
    last_accessed DATETIME,
    last_message_at DATETIME DEFAULT NULL,
    is_important BOOLEAN DEFAULT 0,
    message_count INTEGER DEFAULT 0,
    total_input_tokens INTEGER DEFAULT 0,
    total_output_tokens INTEGER DEFAULT 0,
    mode VARCHAR,
    crew_member_id VARCHAR,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_sessions_id ON sessions(id);
CREATE INDEX IF NOT EXISTS ix_sessions_owner ON sessions(owner);
CREATE INDEX IF NOT EXISTS ix_sessions_active ON sessions(archived, last_accessed);
CREATE INDEX IF NOT EXISTS ix_sessions_search ON sessions(name, archived);

CREATE TABLE IF NOT EXISTS chat_messages (
    id VARCHAR NOT NULL PRIMARY KEY,
    session_id VARCHAR NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    role VARCHAR NOT NULL,
    content TEXT NOT NULL,
    metadata TEXT,
    timestamp DATETIME
);
CREATE INDEX IF NOT EXISTS ix_chat_messages_id ON chat_messages(id);
CREATE INDEX IF NOT EXISTS ix_chat_messages_session_id ON chat_messages(session_id);
CREATE INDEX IF NOT EXISTS ix_messages_session_time ON chat_messages(session_id, timestamp);

CREATE TABLE IF NOT EXISTS documents (
    id VARCHAR NOT NULL PRIMARY KEY,
    session_id VARCHAR REFERENCES sessions(id) ON DELETE SET NULL,
    title VARCHAR NOT NULL DEFAULT 'Untitled',
    language VARCHAR,
    current_content TEXT NOT NULL DEFAULT '',
    version_count INTEGER DEFAULT 1,
    is_active BOOLEAN DEFAULT 1,
    archived BOOLEAN DEFAULT 0,
    owner VARCHAR,
    tidy_verdict VARCHAR,
    source_email_uid VARCHAR,
    source_email_folder VARCHAR,
    source_email_account_id VARCHAR,
    source_email_message_id VARCHAR,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_documents_id ON documents(id);
CREATE INDEX IF NOT EXISTS ix_documents_session_id ON documents(session_id);
CREATE INDEX IF NOT EXISTS ix_documents_owner ON documents(owner);
CREATE INDEX IF NOT EXISTS ix_documents_source_email_message_id ON documents(source_email_message_id);

CREATE TABLE IF NOT EXISTS document_versions (
    id VARCHAR NOT NULL PRIMARY KEY,
    document_id VARCHAR NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    version_number INTEGER NOT NULL,
    content TEXT NOT NULL,
    summary VARCHAR,
    source VARCHAR DEFAULT 'ai',
    created_at DATETIME
);
CREATE INDEX IF NOT EXISTS ix_document_versions_id ON document_versions(id);
CREATE INDEX IF NOT EXISTS ix_document_versions_document_id ON document_versions(document_id);

CREATE TABLE IF NOT EXISTS gallery_albums (
    id VARCHAR NOT NULL PRIMARY KEY,
    name VARCHAR NOT NULL,
    description TEXT DEFAULT '',
    cover_id VARCHAR,
    owner VARCHAR,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_gallery_albums_id ON gallery_albums(id);
CREATE INDEX IF NOT EXISTS ix_gallery_albums_owner ON gallery_albums(owner);

CREATE TABLE IF NOT EXISTS gallery_images (
    id VARCHAR NOT NULL PRIMARY KEY,
    filename VARCHAR NOT NULL UNIQUE,
    prompt TEXT NOT NULL DEFAULT '',
    model VARCHAR,
    size VARCHAR,
    quality VARCHAR,
    tags VARCHAR DEFAULT '',
    ai_tags TEXT DEFAULT '',
    session_id VARCHAR REFERENCES sessions(id) ON DELETE SET NULL,
    album_id VARCHAR REFERENCES gallery_albums(id) ON DELETE SET NULL,
    owner VARCHAR,
    is_active BOOLEAN DEFAULT 1,
    favorite BOOLEAN DEFAULT 0,
    file_hash VARCHAR(64),
    taken_at DATETIME,
    camera_make VARCHAR,
    camera_model VARCHAR,
    gps_lat VARCHAR,
    gps_lng VARCHAR,
    width INTEGER,
    height INTEGER,
    file_size INTEGER,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_gallery_images_id ON gallery_images(id);
CREATE INDEX IF NOT EXISTS ix_gallery_images_session_id ON gallery_images(session_id);
CREATE INDEX IF NOT EXISTS ix_gallery_images_album_id ON gallery_images(album_id);
CREATE INDEX IF NOT EXISTS ix_gallery_images_owner ON gallery_images(owner);
CREATE INDEX IF NOT EXISTS ix_gallery_images_file_hash ON gallery_images(file_hash);
CREATE INDEX IF NOT EXISTS ix_gallery_images_taken_at ON gallery_images(taken_at);
CREATE INDEX IF NOT EXISTS ix_gallery_images_tags ON gallery_images(tags);
CREATE INDEX IF NOT EXISTS ix_gallery_images_model ON gallery_images(model);
CREATE INDEX IF NOT EXISTS ix_gallery_images_active ON gallery_images(is_active, created_at);

CREATE TABLE IF NOT EXISTS email_accounts (
    id VARCHAR NOT NULL PRIMARY KEY,
    owner VARCHAR,
    name VARCHAR NOT NULL,
    is_default BOOLEAN NOT NULL DEFAULT 0,
    enabled BOOLEAN NOT NULL DEFAULT 1,
    imap_host VARCHAR DEFAULT '',
    imap_port INTEGER DEFAULT 993,
    imap_user VARCHAR DEFAULT '',
    imap_password VARCHAR DEFAULT '',
    imap_starttls BOOLEAN DEFAULT 1,
    smtp_host VARCHAR DEFAULT '',
    smtp_port INTEGER DEFAULT 465,
    smtp_user VARCHAR DEFAULT '',
    smtp_password VARCHAR DEFAULT '',
    smtp_security VARCHAR DEFAULT 'ssl',
    from_address VARCHAR DEFAULT '',
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_email_accounts_id ON email_accounts(id);
CREATE INDEX IF NOT EXISTS ix_email_accounts_owner ON email_accounts(owner);
CREATE INDEX IF NOT EXISTS ix_email_accounts_owner_default ON email_accounts(owner, is_default);

CREATE TABLE IF NOT EXISTS model_endpoints (
    id VARCHAR NOT NULL PRIMARY KEY,
    name VARCHAR NOT NULL,
    base_url VARCHAR NOT NULL,
    api_key TEXT,
    is_enabled BOOLEAN DEFAULT 1,
    hidden_models TEXT,
    cached_models TEXT,
    model_type VARCHAR DEFAULT 'llm',
    endpoint_kind VARCHAR DEFAULT 'auto',
    model_refresh_mode VARCHAR DEFAULT 'auto',
    model_refresh_interval INTEGER,
    model_refresh_timeout INTEGER,
    supports_tools BOOLEAN DEFAULT NULL,
    owner VARCHAR,
    pinned_models TEXT,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_model_endpoints_id ON model_endpoints(id);
CREATE INDEX IF NOT EXISTS ix_model_endpoints_owner ON model_endpoints(owner);

CREATE TABLE IF NOT EXISTS mcp_servers (
    id VARCHAR NOT NULL PRIMARY KEY,
    name VARCHAR NOT NULL,
    transport VARCHAR NOT NULL DEFAULT 'stdio',
    command VARCHAR,
    args TEXT,
    env TEXT,
    url VARCHAR,
    is_enabled BOOLEAN DEFAULT 1,
    oauth_config TEXT,
    disabled_tools TEXT,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_mcp_servers_id ON mcp_servers(id);

CREATE TABLE IF NOT EXISTS comparisons (
    id VARCHAR NOT NULL PRIMARY KEY,
    session_id VARCHAR,
    owner VARCHAR,
    prompt TEXT NOT NULL,
    model_a VARCHAR NOT NULL,
    model_b VARCHAR NOT NULL,
    endpoint_a VARCHAR NOT NULL,
    endpoint_b VARCHAR NOT NULL,
    response_a TEXT,
    response_b TEXT,
    metrics_a TEXT,
    metrics_b TEXT,
    winner VARCHAR,
    is_blind BOOLEAN DEFAULT 1,
    blind_mapping TEXT,
    voted_at DATETIME,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_comparisons_id ON comparisons(id);
CREATE INDEX IF NOT EXISTS ix_comparisons_owner ON comparisons(owner);
CREATE INDEX IF NOT EXISTS ix_comparisons_voted_at ON comparisons(voted_at);

CREATE TABLE IF NOT EXISTS signatures (
    id VARCHAR NOT NULL PRIMARY KEY,
    owner VARCHAR,
    name VARCHAR NOT NULL DEFAULT 'Signature',
    data_png TEXT NOT NULL,
    width INTEGER,
    height INTEGER,
    svg TEXT,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_signatures_id ON signatures(id);
CREATE INDEX IF NOT EXISTS ix_signatures_owner ON signatures(owner);

CREATE TABLE IF NOT EXISTS api_tokens (
    id VARCHAR NOT NULL PRIMARY KEY,
    owner VARCHAR,
    name VARCHAR NOT NULL,
    token_hash VARCHAR NOT NULL,
    token_prefix VARCHAR NOT NULL,
    scopes VARCHAR NOT NULL DEFAULT 'chat',
    is_active BOOLEAN DEFAULT 1,
    last_used_at DATETIME,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_api_tokens_id ON api_tokens(id);
CREATE INDEX IF NOT EXISTS ix_api_tokens_owner ON api_tokens(owner);

CREATE TABLE IF NOT EXISTS webhooks (
    id VARCHAR NOT NULL PRIMARY KEY,
    name VARCHAR NOT NULL,
    url VARCHAR NOT NULL,
    secret VARCHAR,
    events VARCHAR NOT NULL,
    is_active BOOLEAN DEFAULT 1,
    last_triggered_at DATETIME,
    last_status_code INTEGER,
    last_error VARCHAR,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_webhooks_id ON webhooks(id);

CREATE TABLE IF NOT EXISTS user_tools (
    id VARCHAR NOT NULL PRIMARY KEY,
    name VARCHAR NOT NULL,
    description TEXT,
    icon VARCHAR DEFAULT '',
    html_content TEXT NOT NULL,
    scope VARCHAR NOT NULL DEFAULT 'global',
    session_id VARCHAR REFERENCES sessions(id) ON DELETE SET NULL,
    owner VARCHAR,
    is_pinned BOOLEAN DEFAULT 0,
    is_active BOOLEAN DEFAULT 1,
    version INTEGER DEFAULT 1,
    author VARCHAR DEFAULT 'ai',
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_user_tools_id ON user_tools(id);
CREATE INDEX IF NOT EXISTS ix_user_tools_owner ON user_tools(owner);
CREATE INDEX IF NOT EXISTS ix_user_tools_scope ON user_tools(scope);
CREATE INDEX IF NOT EXISTS ix_user_tools_active ON user_tools(is_active);

CREATE TABLE IF NOT EXISTS user_tool_data (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tool_id VARCHAR NOT NULL REFERENCES user_tools(id) ON DELETE CASCADE,
    key VARCHAR NOT NULL,
    value TEXT,
    created_at DATETIME,
    updated_at DATETIME
);
CREATE UNIQUE INDEX IF NOT EXISTS ix_user_tool_data_tool_key ON user_tool_data(tool_id, key);

CREATE TABLE IF NOT EXISTS crew_members (
    id VARCHAR NOT NULL PRIMARY KEY,
    owner VARCHAR,
    name VARCHAR NOT NULL,
    avatar VARCHAR,
    user_name VARCHAR,
    personality TEXT,
    model VARCHAR,
    endpoint_url VARCHAR,
    greeting TEXT,
    enabled_tools TEXT,
    session_id VARCHAR REFERENCES sessions(id) ON DELETE SET NULL,
    is_active BOOLEAN DEFAULT 1,
    sort_order INTEGER DEFAULT 0,
    is_default_assistant BOOLEAN DEFAULT 0,
    timezone VARCHAR,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_crew_members_id ON crew_members(id);
CREATE INDEX IF NOT EXISTS ix_crew_members_owner ON crew_members(owner);

CREATE TABLE IF NOT EXISTS scheduled_tasks (
    id VARCHAR NOT NULL PRIMARY KEY,
    owner VARCHAR,
    name VARCHAR NOT NULL DEFAULT 'Untitled Task',
    prompt TEXT,
    task_type VARCHAR DEFAULT 'llm',
    action VARCHAR,
    schedule VARCHAR,
    scheduled_time VARCHAR,
    scheduled_day INTEGER,
    scheduled_date DATETIME,
    trigger_type VARCHAR DEFAULT 'schedule',
    trigger_event VARCHAR,
    trigger_count INTEGER,
    trigger_counter INTEGER DEFAULT 0,
    next_run DATETIME,
    last_run DATETIME,
    status VARCHAR DEFAULT 'active',
    output_target VARCHAR DEFAULT 'session',
    session_id VARCHAR REFERENCES sessions(id) ON DELETE SET NULL,
    model VARCHAR,
    endpoint_url VARCHAR,
    run_count INTEGER DEFAULT 0,
    cron_expression VARCHAR,
    then_task_id VARCHAR REFERENCES scheduled_tasks(id) ON DELETE SET NULL,
    webhook_token VARCHAR UNIQUE,
    crew_member_id VARCHAR,
    character_id VARCHAR,
    max_steps INTEGER,
    email_results BOOLEAN DEFAULT 1,
    notifications_enabled BOOLEAN DEFAULT 1,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_scheduled_tasks_id ON scheduled_tasks(id);
CREATE INDEX IF NOT EXISTS ix_scheduled_tasks_owner ON scheduled_tasks(owner);
CREATE INDEX IF NOT EXISTS ix_scheduled_tasks_next_run ON scheduled_tasks(next_run);
CREATE INDEX IF NOT EXISTS ix_scheduled_tasks_due ON scheduled_tasks(status, next_run);
CREATE INDEX IF NOT EXISTS ix_scheduled_tasks_event ON scheduled_tasks(trigger_type, trigger_event, status);

CREATE TABLE IF NOT EXISTS editor_drafts (
    id VARCHAR NOT NULL PRIMARY KEY,
    owner VARCHAR,
    name VARCHAR NOT NULL DEFAULT 'Untitled',
    source_image_id VARCHAR,
    width INTEGER,
    height INTEGER,
    payload TEXT NOT NULL DEFAULT '',
    thumbnail TEXT,
    is_active BOOLEAN DEFAULT 1,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_editor_drafts_id ON editor_drafts(id);
CREATE INDEX IF NOT EXISTS ix_editor_drafts_owner ON editor_drafts(owner);
CREATE INDEX IF NOT EXISTS ix_editor_drafts_source_image_id ON editor_drafts(source_image_id);
CREATE INDEX IF NOT EXISTS ix_editor_drafts_owner_updated ON editor_drafts(owner, is_active, updated_at);

CREATE TABLE IF NOT EXISTS task_runs (
    id VARCHAR NOT NULL PRIMARY KEY,
    task_id VARCHAR NOT NULL REFERENCES scheduled_tasks(id) ON DELETE CASCADE,
    started_at DATETIME NOT NULL,
    finished_at DATETIME,
    status VARCHAR DEFAULT 'running',
    result TEXT,
    error TEXT,
    tokens_used INTEGER,
    steps TEXT,
    model VARCHAR
);
CREATE INDEX IF NOT EXISTS ix_task_runs_id ON task_runs(id);
CREATE INDEX IF NOT EXISTS ix_task_runs_task ON task_runs(task_id, started_at);

CREATE TABLE IF NOT EXISTS memories (
    id VARCHAR NOT NULL PRIMARY KEY,
    text TEXT NOT NULL,
    category VARCHAR DEFAULT 'fact',
    source VARCHAR DEFAULT 'user',
    owner VARCHAR,
    session_id VARCHAR REFERENCES sessions(id) ON DELETE SET NULL,
    timestamp INTEGER
);
CREATE INDEX IF NOT EXISTS ix_memories_id ON memories(id);
CREATE INDEX IF NOT EXISTS ix_memories_owner ON memories(owner);
CREATE INDEX IF NOT EXISTS ix_memories_session_id ON memories(session_id);
CREATE INDEX IF NOT EXISTS ix_memories_lookup ON memories(category, timestamp);
CREATE INDEX IF NOT EXISTS ix_memories_session ON memories(session_id, timestamp);

CREATE TABLE IF NOT EXISTS notes (
    id VARCHAR NOT NULL PRIMARY KEY,
    owner VARCHAR,
    title VARCHAR DEFAULT '',
    content TEXT,
    items TEXT,
    note_type VARCHAR DEFAULT 'note',
    color VARCHAR,
    label VARCHAR,
    pinned BOOLEAN DEFAULT 0,
    archived BOOLEAN DEFAULT 0,
    due_date VARCHAR,
    source VARCHAR DEFAULT 'user',
    session_id VARCHAR,
    sort_order INTEGER DEFAULT 0,
    image_url VARCHAR,
    repeat VARCHAR DEFAULT 'none',
    ai_classification TEXT,
    ai_content_hash VARCHAR,
    agent_session_id VARCHAR,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_notes_id ON notes(id);
CREATE INDEX IF NOT EXISTS ix_notes_owner ON notes(owner);

CREATE TABLE IF NOT EXISTS calendars (
    id VARCHAR NOT NULL PRIMARY KEY,
    owner VARCHAR,
    name VARCHAR NOT NULL,
    color VARCHAR DEFAULT '#5b8abf',
    source VARCHAR DEFAULT 'local',
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_calendars_id ON calendars(id);
CREATE INDEX IF NOT EXISTS ix_calendars_owner ON calendars(owner);

CREATE TABLE IF NOT EXISTS calendar_events (
    uid VARCHAR NOT NULL PRIMARY KEY,
    calendar_id VARCHAR NOT NULL REFERENCES calendars(id),
    summary VARCHAR NOT NULL DEFAULT '',
    description TEXT DEFAULT '',
    location VARCHAR DEFAULT '',
    dtstart DATETIME NOT NULL,
    dtend DATETIME NOT NULL,
    all_day BOOLEAN DEFAULT 0,
    is_utc BOOLEAN NOT NULL DEFAULT 0,
    rrule VARCHAR DEFAULT '',
    color VARCHAR,
    status VARCHAR DEFAULT 'confirmed',
    importance VARCHAR DEFAULT 'normal',
    event_type VARCHAR,
    last_pinged DATETIME,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_calendar_events_uid ON calendar_events(uid);
CREATE INDEX IF NOT EXISTS ix_calendar_events_calendar_id ON calendar_events(calendar_id);
CREATE INDEX IF NOT EXISTS ix_calendar_events_dtstart ON calendar_events(dtstart);

CREATE TABLE IF NOT EXISTS integrations (
    id VARCHAR NOT NULL PRIMARY KEY,
    owner VARCHAR,
    name VARCHAR NOT NULL,
    type VARCHAR NOT NULL,
    config JSON,
    enabled BOOLEAN DEFAULT 1,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_integrations_id ON integrations(id);
CREATE INDEX IF NOT EXISTS ix_integrations_owner ON integrations(owner);

-- DELIBERATE BACKEND SWAP (no Python `Base` model — ChromaDB is an *external*
-- service in the Python). The Rust port replaces ChromaDB with this single
-- SQLite-backed `vectors` table + in-Rust brute-force cosine similarity (the
-- same spirit as the SQLAlchemy->rusqlite choice). `vector_store.rs` exposes a
-- Chroma-style facade over it; `rag_vector.rs` / `memory_vector.rs` use the
-- `collection` discriminator ('odysseus_rag' / 'odysseus_memories').
--
--   * `embedding` is a little-endian f32 BLOB (compact; ChromaDB stores the same
--     L2-normalized float vectors). Cosine distance = 1 - dot, computed in Rust.
--   * `metadata` is the JSON metadata dict ChromaDB carries per record.
--   * PRIMARY KEY(collection, id) == ChromaDB's per-collection unique ids, and
--     makes `add()` an `INSERT OR REPLACE` (ChromaDB `upsert`/`add` semantics).
CREATE TABLE IF NOT EXISTS vectors (
    collection VARCHAR NOT NULL,
    id VARCHAR NOT NULL,
    document TEXT,
    embedding BLOB,
    metadata TEXT,
    PRIMARY KEY (collection, id)
);
CREATE INDEX IF NOT EXISTS ix_vectors_collection ON vectors(collection);
"#;

/// `Base.metadata.create_all(bind=engine)` — create any tables that don't exist.
pub fn create_all() -> rusqlite::Result<()> {
    let conn = engine_connect()?;
    conn.execute_batch(CREATE_TABLES)
}

// ===========================================================================
// Migrations (each a 1:1 translation of a `_migrate_*` in database.py)
// ===========================================================================
//
// The raw-`sqlite3` migrations guard on file existence (`connect_existing`); the
// `engine.connect()` ones run unconditionally (the file is auto-created). Each
// preserves the original SQL and the `if <col> not in columns:` guard.

fn _migrate_add_last_message_at_column() {
    let conn = match connect_existing() {
        Some(c) => c,
        None => return,
    };
    let result = (|| -> rusqlite::Result<()> {
        let columns = table_columns(&conn, "sessions");
        if !columns.contains(&"last_message_at".to_string()) {
            conn.execute("ALTER TABLE sessions ADD COLUMN last_message_at DATETIME", [])?;
        }
        conn.execute_batch(
            r#"
            UPDATE sessions
               SET last_message_at = COALESCE(
                   (SELECT MAX(timestamp) FROM chat_messages
                     WHERE chat_messages.session_id = sessions.id),
                   last_accessed,
                   created_at
               )
             WHERE last_message_at IS NULL;
            CREATE INDEX IF NOT EXISTS ix_sessions_last_message_at
              ON sessions(archived, last_message_at);
            "#,
        )?;
        Ok(())
    })();
    match result {
        Ok(()) => logger::info("Migrated: added + backfilled 'last_message_at' on sessions"),
        Err(e) => logger::warning(&format!("last_message_at migration failed: {e}")),
    }
}

fn _migrate_add_document_archived_column() {
    let conn = match connect_existing() {
        Some(c) => c,
        None => return,
    };
    let columns = table_columns(&conn, "documents");
    if !columns.contains(&"archived".to_string()) {
        match conn.execute("ALTER TABLE documents ADD COLUMN archived BOOLEAN DEFAULT 0", []) {
            Ok(_) => logger::info("Migrated: added 'archived' to documents"),
            Err(e) => logger::warning(&format!("documents.archived migration failed: {e}")),
        }
    }
}

fn _migrate_add_owner_column() {
    let conn = match connect_existing() {
        Some(c) => c,
        None => return,
    };
    let columns = table_columns(&conn, "sessions");
    if !columns.contains(&"owner".to_string()) {
        let r = conn.execute_batch(
            "ALTER TABLE sessions ADD COLUMN owner TEXT;\
             CREATE INDEX IF NOT EXISTS ix_sessions_owner ON sessions(owner);",
        );
        match r {
            Ok(()) => logger::info("Migrated: added 'owner' column to sessions"),
            Err(e) => logger::warning(&format!("Migration check failed: {e}")),
        }
    }
}

fn _migrate_model_endpoints() {
    let conn = match connect_existing() {
        Some(c) => c,
        None => return,
    };
    let columns = table_columns(&conn, "model_endpoints");
    if !columns.is_empty() && !columns.contains(&"base_url".to_string()) {
        match conn.execute("DROP TABLE IF EXISTS model_endpoints", []) {
            Ok(_) => logger::info("Migrated: dropped old model_endpoints table (schema change)"),
            Err(e) => logger::warning(&format!("model_endpoints migration check failed: {e}")),
        }
    }
}

/// Generic `_migrate_add_<col>` over `model_endpoints` (covers hidden_models,
/// cached_models, model_type, supports_tools — same shape, only col/type/log
/// differ). The Python has one function each; folding them keeps the SQL and the
/// `if columns and <col> not in columns` guard identical.
fn migrate_add_endpoint_column(col: &str, coltype: &str, log: Option<&str>) {
    let conn = match connect_existing() {
        Some(c) => c,
        None => return,
    };
    let columns = table_columns(&conn, "model_endpoints");
    if !columns.is_empty() && !columns.contains(&col.to_string()) {
        let sql = format!("ALTER TABLE model_endpoints ADD COLUMN {col} {coltype}");
        match conn.execute(&sql, []) {
            Ok(_) => {
                if let Some(msg) = log {
                    logger::info(msg);
                }
            }
            Err(e) => logger::warning(&format!("{col} migration failed: {e}")),
        }
    }
}

fn _migrate_add_hidden_models_column() {
    migrate_add_endpoint_column(
        "hidden_models",
        "TEXT",
        Some("Migrated: added 'hidden_models' column to model_endpoints"),
    );
}

/// `_migrate_add_email_smtp_security()` — add `smtp_security` to email_accounts
/// and backfill from the SMTP port (587 -> starttls, else ssl).
fn _migrate_add_email_smtp_security() {
    let conn = match connect_existing() {
        Some(c) => c,
        None => return,
    };
    let columns = table_columns(&conn, "email_accounts");
    if columns.is_empty() || columns.contains(&"smtp_security".to_string()) {
        return;
    }
    if let Err(e) = conn.execute(
        "ALTER TABLE email_accounts ADD COLUMN smtp_security TEXT DEFAULT 'ssl'",
        [],
    ) {
        logger::warning(&format!("smtp_security migration skipped: {e}"));
        return;
    }
    let _ = conn.execute(
        "UPDATE email_accounts SET smtp_security = CASE \
WHEN smtp_port = 587 THEN 'starttls' ELSE 'ssl' END \
WHERE smtp_security IS NULL OR smtp_security = ''",
        [],
    );
    logger::info("Migrated: added smtp_security column to email_accounts");
}

fn _migrate_add_model_endpoint_owner_column() {
    // Adds the column AND an index, so it's spelled out rather than via the helper.
    let conn = match connect_existing() {
        Some(c) => c,
        None => return,
    };
    let columns = table_columns(&conn, "model_endpoints");
    if !columns.is_empty() && !columns.contains(&"owner".to_string()) {
        let r = conn.execute_batch(
            "ALTER TABLE model_endpoints ADD COLUMN owner VARCHAR;\
             CREATE INDEX IF NOT EXISTS ix_model_endpoints_owner ON model_endpoints(owner);",
        );
        match r {
            Ok(()) => logger::info("Migrated: added 'owner' column + index to model_endpoints"),
            Err(e) => logger::warning(&format!("model_endpoints.owner migration failed: {e}")),
        }
    }
}

fn _migrate_add_model_type_column() {
    migrate_add_endpoint_column(
        "model_type",
        "TEXT DEFAULT 'llm'",
        Some("Migrated: added 'model_type' column to model_endpoints"),
    );
}

/// `_migrate_add_model_endpoint_refresh_columns` — add endpoint classification /
/// refresh policy columns if missing (endpoint_kind, model_refresh_mode,
/// model_refresh_interval, model_refresh_timeout).
fn _migrate_add_model_endpoint_refresh_columns() {
    migrate_add_endpoint_column(
        "endpoint_kind",
        "TEXT DEFAULT 'auto'",
        None,
    );
    migrate_add_endpoint_column(
        "model_refresh_mode",
        "TEXT DEFAULT 'auto'",
        None,
    );
    migrate_add_endpoint_column(
        "model_refresh_interval",
        "INTEGER",
        None,
    );
    migrate_add_endpoint_column(
        "model_refresh_timeout",
        "INTEGER",
        None,
    );
}

fn _migrate_add_task_run_model_column() {
    let conn = match connect_existing() {
        Some(c) => c,
        None => return,
    };
    let columns = table_columns(&conn, "task_runs");
    if !columns.is_empty() && !columns.contains(&"model".to_string()) {
        match conn.execute("ALTER TABLE task_runs ADD COLUMN model TEXT", []) {
            Ok(_) => logger::info("Migrated: added 'model' column to task_runs"),
            Err(e) => logger::warning(&format!("task_runs model migration failed: {e}")),
        }
    }
}

fn _migrate_add_supports_tools_column() {
    migrate_add_endpoint_column(
        "supports_tools",
        "BOOLEAN",
        Some("Migrated: added 'supports_tools' column to model_endpoints"),
    );
}

fn _migrate_add_cached_models_column() {
    // Python logs nothing on success for this one.
    migrate_add_endpoint_column("cached_models", "TEXT", None);
}

fn _migrate_add_notes_sort_order() {
    let conn = match connect_existing() {
        Some(c) => c,
        None => return,
    };
    let result = (|| -> rusqlite::Result<()> {
        let columns = table_columns(&conn, "notes");
        let has = |c: &str| columns.contains(&c.to_string());
        if !columns.is_empty() && !has("sort_order") {
            conn.execute("ALTER TABLE notes ADD COLUMN sort_order INTEGER DEFAULT 0", [])?;
        }
        if !columns.is_empty() && !has("image_url") {
            conn.execute("ALTER TABLE notes ADD COLUMN image_url TEXT", [])?;
        }
        if !columns.is_empty() && !has("repeat") {
            conn.execute("ALTER TABLE notes ADD COLUMN repeat TEXT DEFAULT 'none'", [])?;
        }
        if !columns.is_empty() && !has("ai_classification") {
            conn.execute("ALTER TABLE notes ADD COLUMN ai_classification TEXT", [])?;
        }
        if !columns.is_empty() && !has("ai_content_hash") {
            conn.execute("ALTER TABLE notes ADD COLUMN ai_content_hash TEXT", [])?;
        }
        if !columns.is_empty() && !has("agent_session_id") {
            conn.execute("ALTER TABLE notes ADD COLUMN agent_session_id TEXT", [])?;
        }
        Ok(())
    })();
    if let Err(e) = result {
        logger::warning(&format!("notes migration failed: {e}"));
    }
}

/// Generic `_migrate_add_<col>` over `sessions` (mode / folder). Mirrors the
/// `if <col> not in columns` guard; logs on success.
fn migrate_add_sessions_column(col: &str, coltype: &str, log: &str, warn: &str) {
    let conn = match connect_existing() {
        Some(c) => c,
        None => return,
    };
    let columns = table_columns(&conn, "sessions");
    if !columns.contains(&col.to_string()) {
        match conn.execute(&format!("ALTER TABLE sessions ADD COLUMN {col} {coltype}"), []) {
            Ok(_) => logger::info(log),
            Err(e) => logger::warning(&format!("{warn}: {e}")),
        }
    }
}

fn _migrate_add_mode_column() {
    migrate_add_sessions_column(
        "mode",
        "TEXT",
        "Migrated: added 'mode' column to sessions",
        "Migration check for mode failed",
    );
}

fn _migrate_add_folder_column() {
    migrate_add_sessions_column(
        "folder",
        "TEXT",
        "Migrated: added 'folder' column to sessions",
        "Migration check for folder failed",
    );
}

fn _migrate_add_token_columns() {
    let conn = match connect_existing() {
        Some(c) => c,
        None => return,
    };
    let columns = table_columns(&conn, "sessions");
    if !columns.contains(&"total_input_tokens".to_string()) {
        let r = conn.execute_batch(
            "ALTER TABLE sessions ADD COLUMN total_input_tokens INTEGER DEFAULT 0;\
             ALTER TABLE sessions ADD COLUMN total_output_tokens INTEGER DEFAULT 0;",
        );
        match r {
            Ok(()) => logger::info("Migrated: added token tracking columns to sessions"),
            Err(e) => logger::warning(&format!("Migration check for token columns failed: {e}")),
        }
    }
}

/// Generic helper: add owner TEXT column + index to a table if missing.
fn _migrate_add_owner_to_table(table_name: &str, index_name: &str) {
    let conn = match connect_existing() {
        Some(c) => c,
        None => return,
    };
    let columns = table_columns(&conn, table_name);
    if !columns.contains(&"owner".to_string()) {
        let r = conn.execute_batch(&format!(
            "ALTER TABLE {table_name} ADD COLUMN owner TEXT;\
             CREATE INDEX IF NOT EXISTS {index_name} ON {table_name}(owner);"
        ));
        match r {
            Ok(()) => logger::info(&format!("Migrated: added 'owner' column to {table_name}")),
            Err(e) => {
                logger::warning(&format!("Migration owner column for {table_name} failed: {e}"))
            }
        }
    }
}

fn _migrate_add_multiuser_owner_columns() {
    _migrate_add_owner_to_table("memories", "ix_memories_owner");
    _migrate_add_owner_to_table("gallery_images", "ix_gallery_images_owner");
    _migrate_add_owner_to_table("user_tools", "ix_user_tools_owner");
    _migrate_add_owner_to_table("comparisons", "ix_comparisons_owner");
    _migrate_add_owner_to_table("api_tokens", "ix_api_tokens_owner");
    _migrate_add_owner_to_table("documents", "ix_documents_owner");
}

fn _migrate_add_api_token_scopes_column() {
    let conn = match connect_existing() {
        Some(c) => c,
        None => return,
    };
    let columns = table_columns(&conn, "api_tokens");
    if !columns.is_empty() && !columns.contains(&"scopes".to_string()) {
        let r = conn.execute_batch(
            "ALTER TABLE api_tokens ADD COLUMN scopes TEXT NOT NULL DEFAULT 'chat';\
             UPDATE api_tokens SET scopes = 'chat' WHERE scopes IS NULL OR scopes = '';",
        );
        match r {
            Ok(()) => logger::info("Migrated: added scopes column to api_tokens"),
            Err(e) => logger::warning(&format!("api_tokens.scopes migration failed: {e}")),
        }
    }
}

/// `_migrate_backfill_document_owner_from_session` — uses `engine.connect()`
/// (unconditional), so no file-existence guard.
fn _migrate_backfill_document_owner_from_session() {
    let conn = match engine_connect() {
        Ok(c) => c,
        Err(_) => return,
    };
    let result = (|| -> rusqlite::Result<usize> {
        let cols = table_columns(&conn, "documents");
        if !cols.contains(&"owner".to_string()) {
            return Ok(0);
        }
        conn.execute(
            "UPDATE documents SET owner = (\
               SELECT s.owner FROM sessions s WHERE s.id = documents.session_id\
             ) WHERE owner IS NULL AND session_id IS NOT NULL \
             AND EXISTS (SELECT 1 FROM sessions s WHERE s.id = documents.session_id \
                         AND s.owner IS NOT NULL)",
            [],
        )
    })();
    match result {
        Ok(n) if n > 0 => {
            logger::info(&format!("Backfilled owner on {n} session-linked documents"))
        }
        Ok(_) => {}
        Err(e) => logger::warning(&format!("document owner backfill: {e}")),
    }
}

fn _migrate_add_tidy_verdict() {
    let conn = match engine_connect() {
        Ok(c) => c,
        Err(_) => return,
    };
    let cols = table_columns(&conn, "documents");
    if !cols.contains(&"tidy_verdict".to_string()) {
        match conn.execute("ALTER TABLE documents ADD COLUMN tidy_verdict VARCHAR", []) {
            Ok(_) => logger::info("Added tidy_verdict column to documents"),
            Err(e) => logger::warning(&format!("tidy_verdict migration: {e}")),
        }
    }
}

fn _migrate_add_doc_source_email_cols() {
    let conn = match engine_connect() {
        Ok(c) => c,
        Err(_) => return,
    };
    let result = (|| -> rusqlite::Result<()> {
        let existing = table_columns(&conn, "documents");
        let cols_to_add = [
            ("source_email_uid", "VARCHAR"),
            ("source_email_folder", "VARCHAR"),
            ("source_email_account_id", "VARCHAR"),
            ("source_email_message_id", "VARCHAR"),
        ];
        for (col, spec) in cols_to_add {
            if !existing.contains(&col.to_string()) {
                conn.execute(&format!("ALTER TABLE documents ADD COLUMN {col} {spec}"), [])?;
                logger::info(&format!("Added {col} column to documents"));
            }
        }
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_documents_source_email_message_id \
             ON documents (source_email_message_id)",
            [],
        )?;
        Ok(())
    })();
    if let Err(e) = result {
        logger::warning(&format!("doc source-email migration: {e}"));
    }
}

fn _migrate_add_task_automation_columns() {
    let conn = match engine_connect() {
        Ok(c) => c,
        Err(_) => return,
    };
    let result = (|| -> rusqlite::Result<()> {
        // (col, def) preserving insertion order.
        let new_cols = [
            ("task_type", "VARCHAR DEFAULT 'llm'"),
            ("action", "VARCHAR"),
            ("trigger_type", "VARCHAR DEFAULT 'schedule'"),
            ("trigger_event", "VARCHAR"),
            ("trigger_count", "INTEGER"),
            ("trigger_counter", "INTEGER DEFAULT 0"),
        ];
        // cols_info: (name, notnull) per row of PRAGMA table_info.
        let mut stmt = conn.prepare("PRAGMA table_info(scheduled_tasks)")?;
        let cols_info: Vec<(String, i64)> = stmt
            .query_map([], |r| Ok((r.get::<_, String>(1)?, r.get::<_, i64>(3)?)))?
            .filter_map(|r| r.ok())
            .collect();
        drop(stmt);
        let col_names: Vec<String> = cols_info.iter().map(|(n, _)| n.clone()).collect();
        for (col_name, col_def) in new_cols {
            if !col_names.contains(&col_name.to_string()) {
                conn.execute(
                    &format!("ALTER TABLE scheduled_tasks ADD COLUMN {col_name} {col_def}"),
                    [],
                )?;
            }
        }
        // notnull_map: rebuild needed if prompt/schedule/scheduled_time are NOT NULL.
        let notnull = |name: &str| {
            cols_info
                .iter()
                .find(|(n, _)| n == name)
                .map(|(_, nn)| *nn)
                .unwrap_or(0)
        };
        let needs_rebuild =
            notnull("prompt") == 1 || notnull("schedule") == 1 || notnull("scheduled_time") == 1;
        if needs_rebuild {
            logger::info(
                "Rebuilding scheduled_tasks to make prompt/schedule/scheduled_time nullable",
            );
            conn.execute_batch(
                r#"
                ALTER TABLE scheduled_tasks RENAME TO _old_scheduled_tasks;
                CREATE TABLE scheduled_tasks (
                    id VARCHAR PRIMARY KEY,
                    owner VARCHAR,
                    name VARCHAR NOT NULL,
                    prompt TEXT,
                    schedule VARCHAR,
                    scheduled_time VARCHAR,
                    scheduled_day INTEGER,
                    scheduled_date DATETIME,
                    next_run DATETIME,
                    last_run DATETIME,
                    status VARCHAR,
                    output_target VARCHAR,
                    session_id VARCHAR,
                    model VARCHAR,
                    endpoint_url VARCHAR,
                    run_count INTEGER,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL,
                    task_type VARCHAR DEFAULT 'llm',
                    action VARCHAR,
                    trigger_type VARCHAR DEFAULT 'schedule',
                    trigger_event VARCHAR,
                    trigger_count INTEGER,
                    trigger_counter INTEGER DEFAULT 0
                );
                INSERT INTO scheduled_tasks
                SELECT id, owner, name, prompt, schedule, scheduled_time,
                       scheduled_day, scheduled_date, next_run, last_run,
                       status, output_target, session_id, model, endpoint_url,
                       run_count, created_at, updated_at,
                       task_type, action, trigger_type, trigger_event,
                       trigger_count, trigger_counter
                FROM _old_scheduled_tasks;
                DROP TABLE _old_scheduled_tasks;
                "#,
            )?;
        }
        logger::info("Task automation columns migration complete");
        Ok(())
    })();
    if let Err(e) = result {
        logger::warning(&format!("task automation migration: {e}"));
    }
}

/// Generic `_migrate_add_<col>` over `mcp_servers` (oauth_config / disabled_tools).
fn migrate_add_mcp_column(col: &str, log: &str, warn: &str) {
    let conn = match engine_connect() {
        Ok(c) => c,
        Err(_) => return,
    };
    let cols = table_columns(&conn, "mcp_servers");
    if !cols.contains(&col.to_string()) {
        match conn.execute(&format!("ALTER TABLE mcp_servers ADD COLUMN {col} TEXT"), []) {
            Ok(_) => logger::info(log),
            Err(e) => logger::warning(&format!("{warn}: {e}")),
        }
    }
}

fn _migrate_add_oauth_config() {
    migrate_add_mcp_column(
        "oauth_config",
        "Added oauth_config column to mcp_servers",
        "oauth_config migration",
    );
}

fn _migrate_add_disabled_tools() {
    migrate_add_mcp_column(
        "disabled_tools",
        "Added disabled_tools column to mcp_servers",
        "disabled_tools migration",
    );
}

fn _migrate_add_task_v2_columns() {
    let conn = match engine_connect() {
        Ok(c) => c,
        Err(_) => return,
    };
    let result = (|| -> rusqlite::Result<()> {
        let cols = table_columns(&conn, "scheduled_tasks");
        let new_cols = [
            ("cron_expression", "VARCHAR"),
            ("then_task_id", "VARCHAR"),
            ("webhook_token", "VARCHAR"),
        ];
        for (col_name, col_def) in new_cols {
            if !cols.contains(&col_name.to_string()) {
                conn.execute(
                    &format!("ALTER TABLE scheduled_tasks ADD COLUMN {col_name} {col_def}"),
                    [],
                )?;
            }
        }
        if !cols.contains(&"webhook_token".to_string()) {
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS ix_scheduled_tasks_webhook \
                 ON scheduled_tasks(webhook_token)",
                [],
            )?;
        }
        logger::info("Task v2 columns migration complete");
        Ok(())
    })();
    if let Err(e) = result {
        logger::warning(&format!("task v2 migration: {e}"));
    }
}

fn _migrate_drop_ping_notes_tasks() {
    let conn = match engine_connect() {
        Ok(c) => c,
        Err(_) => return,
    };
    let result = (|| -> rusqlite::Result<()> {
        for action in ["ping_notes", "ping_events"] {
            conn.execute(
                "DELETE FROM task_runs WHERE task_id IN \
                 (SELECT id FROM scheduled_tasks WHERE action=?1)",
                [action],
            )?;
            let rows = conn.execute("DELETE FROM scheduled_tasks WHERE action=?1", [action])?;
            if rows > 0 {
                logger::info(&format!("Dropped {rows} {action} task row(s)"));
            }
        }
        Ok(())
    })();
    if let Err(e) = result {
        logger::debug(&format!("drop_ping_notes_tasks: {e}"));
    }
}

fn _migrate_add_notifications_enabled() {
    let conn = match engine_connect() {
        Ok(c) => c,
        Err(_) => return,
    };
    let cols = table_columns(&conn, "scheduled_tasks");
    if !cols.contains(&"notifications_enabled".to_string()) {
        match conn.execute(
            "ALTER TABLE scheduled_tasks ADD COLUMN notifications_enabled BOOLEAN DEFAULT 1",
            [],
        ) {
            Ok(_) => logger::info("Added notifications_enabled column to scheduled_tasks"),
            Err(e) => logger::warning(&format!("notifications_enabled migration: {e}")),
        }
    }
}

fn _migrate_add_crew_member_id() {
    let conn = match engine_connect() {
        Ok(c) => c,
        Err(_) => return,
    };
    let result = (|| -> rusqlite::Result<()> {
        let cols = table_columns(&conn, "sessions");
        if !cols.contains(&"crew_member_id".to_string()) {
            conn.execute("ALTER TABLE sessions ADD COLUMN crew_member_id TEXT", [])?;
            logger::info("Added crew_member_id column to sessions");
        }
        let cols2 = table_columns(&conn, "scheduled_tasks");
        if !cols2.contains(&"crew_member_id".to_string()) {
            conn.execute("ALTER TABLE scheduled_tasks ADD COLUMN crew_member_id TEXT", [])?;
            logger::info("Added crew_member_id column to scheduled_tasks");
        }
        Ok(())
    })();
    if let Err(e) = result {
        logger::warning(&format!("crew_member_id migration: {e}"));
    }
}

fn _migrate_add_assistant_columns() {
    let conn = match engine_connect() {
        Ok(c) => c,
        Err(_) => return,
    };
    let result = (|| -> rusqlite::Result<()> {
        let cols = table_columns(&conn, "crew_members");
        if !cols.contains(&"is_default_assistant".to_string()) {
            conn.execute(
                "ALTER TABLE crew_members ADD COLUMN is_default_assistant BOOLEAN DEFAULT 0",
                [],
            )?;
            logger::info("Added is_default_assistant column to crew_members");
        }
        if !cols.contains(&"timezone".to_string()) {
            conn.execute("ALTER TABLE crew_members ADD COLUMN timezone TEXT", [])?;
            logger::info("Added timezone column to crew_members");
        }
        Ok(())
    })();
    if let Err(e) = result {
        logger::warning(&format!("assistant columns migration: {e}"));
    }
}

/// `_migrate_assign_legacy_owner` — assign all null-owner data to the first
/// (admin) user. Reads the admin username from `auth.json`, then `UPDATE`s every
/// owner-bearing table, and finally stamps the admin owner onto any ownerless
/// `data/memory.json` entries (matching the Python tail at
/// `core/database.py:1048-1064`, which is security-relevant: a legacy ownerless
/// entry would otherwise be invisible to the per-owner `MemoryManager::load`
/// filter for every user, including admin).
///
/// It also ports the `user_prefs.json` tail (`core/database.py:1066-1079`): a flat
/// (legacy, no `_users` key) prefs dict is nested under `_users.<admin>`. The Rust
/// prefs loader already tolerates the flat shape, so this only normalizes the
/// on-disk format to match Python — reads were already observationally equivalent.
/// Public entrypoint for [`_migrate_assign_legacy_owner`] — the null-owner sweep
/// (`web::startup_event`'s hourly loop, the analogue of app.py's `_null_owner_sweep_loop`
/// importing `from core.database import _migrate_assign_legacy_owner`). A thin pub
/// wrapper so the private migration name stays internal to this module.
pub fn migrate_assign_legacy_owner() {
    _migrate_assign_legacy_owner();
}

fn _migrate_assign_legacy_owner() {
    // Find admin user from auth.json (is_admin: True, else first user).
    let mut auth_path = os::path::join(&os::path::dirname(&db_path()), "auth.json");
    if !std::path::Path::new(&auth_path).is_absolute() {
        auth_path = os::path::join("data", "auth.json");
    }
    let admin_user: Option<String> = (|| {
        let text = std::fs::read_to_string(&auth_path).ok()?;
        let data: serde_json::Value = serde_json::from_str(&text).ok()?;
        let users = data.get("users")?.as_object()?;
        if users.is_empty() {
            return None;
        }
        for (uname, udata) in users {
            if udata.get("is_admin").and_then(|v| v.as_bool()) == Some(true) {
                return Some(uname.clone());
            }
        }
        users.keys().next().cloned()
    })();
    let admin_user = match admin_user {
        Some(u) => u,
        None => return,
    };

    let conn = match connect_existing() {
        Some(c) => c,
        None => return,
    };
    let tables = [
        "sessions", "memories", "gallery_images", "user_tools", "comparisons",
        "documents", "signatures", "notes", "calendars", "calendar_events",
        "integrations", "scheduled_tasks", "task_runs", "crew_members",
        "gallery_albums", "gallery_people", "user_tool_data", "api_tokens",
        "webhooks",
    ];
    for table in tables {
        let columns = table_columns(&conn, table);
        if columns.contains(&"owner".to_string()) {
            match conn.execute(
                &format!("UPDATE {table} SET owner = ?1 WHERE owner IS NULL"),
                [&admin_user],
            ) {
                Ok(n) if n > 0 => {
                    logger::info(&format!("Assigned {n} legacy rows in {table} to '{admin_user}'"))
                }
                Ok(_) => {}
                Err(e) => {
                    logger::warning(&format!("Legacy owner assignment for {table} failed: {e}"))
                }
            }
        }
    }
    // Python commits/closes the sqlite3 connection here; `conn` is dropped at the
    // end of this block, which closes it the same way.
    drop(conn);

    // Also migrate memory.json — stamp the admin owner onto every entry whose
    // `owner` is falsy (absent / null / empty string), then rewrite the file with
    // `json.dump(..., ensure_ascii=False, indent=2)`. The whole tail is wrapped in
    // a try/except in Python, so any IO/parse failure is logged and swallowed.
    let mem_path = os::path::join("data", "memory.json");
    let result = (|| -> serde_json::Result<()> {
        if !os::path::exists(&mem_path) {
            return Ok(());
        }
        let raw = match std::fs::read_to_string(&mem_path) {
            Ok(raw) => raw,
            // Treat read failures like the Python `except Exception` (logged below).
            Err(e) => return Err(serde_json::Error::io(e)),
        };
        let mut memories: serde_json::Value = serde_json::from_str(&raw)?;
        // `for m in memories` iterates the top-level list; non-list data would
        // raise in Python and be swallowed by the surrounding except.
        let array = match memories.as_array_mut() {
            Some(a) => a,
            None => return Ok(()),
        };
        let total = array.len();
        let mut changed = false;
        for m in array.iter_mut() {
            // `if not m.get("owner"):` — falsy means absent, null, or "".
            let owner_falsy = match m.get("owner") {
                None | Some(serde_json::Value::Null) => true,
                Some(serde_json::Value::String(s)) => s.is_empty(),
                _ => false,
            };
            if owner_falsy {
                if let Some(obj) = m.as_object_mut() {
                    obj.insert("owner".to_string(), serde_json::Value::String(admin_user.clone()));
                    changed = true;
                }
            }
        }
        if changed {
            // `json.dump(memories, f, ensure_ascii=False, indent=2)` — raw UTF-8
            // (no `\uXXXX` escaping) at 2-space indent; preserve_order keeps key
            // order. The Python write here is plain (non-atomic), but a `.tmp` +
            // rename is strictly safer and byte-identical on disk.
            let serialized = serde_json::to_string_pretty(&memories)?;
            let tmp = format!("{mem_path}.tmp");
            std::fs::write(&tmp, serialized).map_err(serde_json::Error::io)?;
            os::replace(&tmp, &mem_path).map_err(serde_json::Error::io)?;
            // Python logs the *total* count (`sum(1 for _ in memories)`), not the
            // changed count.
            logger::info(&format!(
                "Assigned {total} legacy memories in memory.json to '{admin_user}'"
            ));
        }
        Ok(())
    })();
    if let Err(e) = result {
        logger::warning(&format!("memory.json legacy migration failed: {e}"));
    }

    // Also migrate `user_prefs.json` to the per-user (`_users`) format
    // (`core/database.py:1066-1079`): a non-empty FLAT dict (no `_users` key) is
    // nested under `_users.<admin>`. Wrapped like the Python try/except — any
    // IO/parse failure is logged and swallowed. (The Rust prefs loader already reads
    // both shapes; this only normalizes the on-disk file to match Python.)
    let prefs_path = os::path::join("data", "user_prefs.json");
    let prefs_result = (|| -> serde_json::Result<()> {
        if !os::path::exists(&prefs_path) {
            return Ok(());
        }
        let raw = match std::fs::read_to_string(&prefs_path) {
            Ok(raw) => raw,
            Err(e) => return Err(serde_json::Error::io(e)),
        };
        let prefs: serde_json::Value = serde_json::from_str(&raw)?;
        // `if "_users" not in prefs and prefs:` — only a non-empty, flat dict migrates.
        let obj = match prefs.as_object() {
            Some(o) => o,
            None => return Ok(()),
        };
        if obj.is_empty() || obj.contains_key("_users") {
            return Ok(());
        }
        // Flat format -> nest the whole dict under the admin user.
        let mut users = serde_json::Map::new();
        users.insert(admin_user.clone(), prefs.clone());
        let mut wrapper = serde_json::Map::new();
        wrapper.insert("_users".to_string(), serde_json::Value::Object(users));
        let serialized = serde_json::to_string_pretty(&serde_json::Value::Object(wrapper))?;
        let tmp = format!("{prefs_path}.tmp");
        std::fs::write(&tmp, serialized).map_err(serde_json::Error::io)?;
        os::replace(&tmp, &prefs_path).map_err(serde_json::Error::io)?;
        logger::info(&format!(
            "Migrated user_prefs.json to per-user format under '{admin_user}'"
        ));
        Ok(())
    })();
    if let Err(e) = prefs_result {
        logger::warning(&format!("user_prefs.json migration failed: {e}"));
    }
}

/// `_migrate_add_calendar_metadata` — add importance/event_type/last_pinged.
fn _migrate_add_calendar_metadata() {
    let conn = match connect_existing() {
        Some(c) => c,
        None => return,
    };
    let result = (|| -> rusqlite::Result<()> {
        let columns = table_columns(&conn, "calendar_events");
        let has = |c: &str| columns.contains(&c.to_string());
        if !columns.is_empty() && !has("importance") {
            conn.execute(
                "ALTER TABLE calendar_events ADD COLUMN importance TEXT DEFAULT 'normal'",
                [],
            )?;
        }
        if !columns.is_empty() && !has("event_type") {
            conn.execute("ALTER TABLE calendar_events ADD COLUMN event_type TEXT", [])?;
        }
        if !columns.is_empty() && !has("last_pinged") {
            conn.execute("ALTER TABLE calendar_events ADD COLUMN last_pinged DATETIME", [])?;
        }
        Ok(())
    })();
    if let Err(e) = result {
        logger::warning(&format!("calendar_events migration failed: {e}"));
    }
}

/// `_migrate_add_calendar_is_utc` — add is_utc (Z-suffix preservation flag).
fn _migrate_add_calendar_is_utc() {
    let conn = match connect_existing() {
        Some(c) => c,
        None => return,
    };
    let columns = table_columns(&conn, "calendar_events");
    if !columns.is_empty() && !columns.contains(&"is_utc".to_string()) {
        match conn.execute(
            "ALTER TABLE calendar_events ADD COLUMN is_utc BOOLEAN DEFAULT 0 NOT NULL",
            [],
        ) {
            Ok(_) => logger::info("Migrated: added 'is_utc' column to calendar_events"),
            Err(e) => logger::warning(&format!("is_utc migration failed: {e}")),
        }
    }
}

/// `_migrate_seed_email_account` — if email_accounts is empty and settings.json
/// has legacy flat imap/smtp keys, create a single "Default" account from them.
fn _migrate_seed_email_account() {
    let result = (|| -> rusqlite::Result<()> {
        let conn = engine_connect()?;
        // Short-circuit unless the table exists AND is empty.
        let table_exists: bool = conn
            .query_row(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='email_accounts'",
                [],
                |_| Ok(()),
            )
            .optional()?
            .is_some();
        if !table_exists {
            return Ok(());
        }
        let existing: i64 =
            conn.query_row("SELECT COUNT(*) FROM email_accounts", [], |r| r.get(0))?;
        if existing > 0 {
            return Ok(());
        }
        drop(conn);

        // Read data/settings.json (skip silently if absent / unparseable).
        let settings_file = "data/settings.json";
        if !os::path::exists(settings_file) {
            return Ok(());
        }
        let s: serde_json::Value = match std::fs::read_to_string(settings_file)
            .ok()
            .and_then(|t| serde_json::from_str(&t).ok())
        {
            Some(v) => v,
            None => return Ok(()),
        };
        let gets = |k: &str| s.get(k).and_then(|v| v.as_str()).unwrap_or("");
        let imap_host = gets("imap_host").trim().to_string();
        let smtp_host = gets("smtp_host").trim().to_string();
        if imap_host.is_empty() && smtp_host.is_empty() {
            return Ok(()); // nothing to migrate
        }

        // int(s.get("imap_port") or 993) — falsy/missing -> default.
        let geti = |k: &str, dflt: i64| -> i64 {
            match s.get(k) {
                Some(serde_json::Value::Number(n)) => n.as_i64().unwrap_or(dflt),
                Some(serde_json::Value::String(t)) if !t.is_empty() => {
                    t.parse().unwrap_or(dflt)
                }
                _ => dflt,
            }
        };
        let getb = |k: &str, dflt: bool| -> bool {
            s.get(k).and_then(|v| v.as_bool()).unwrap_or(dflt)
        };
        let now = crate::pydatetime::utcnow_naive_iso();
        let conn = engine_connect()?;
        conn.execute(
            "INSERT INTO email_accounts \
               (id, owner, name, is_default, enabled, \
                imap_host, imap_port, imap_user, imap_password, imap_starttls, \
                smtp_host, smtp_port, smtp_user, smtp_password, \
                from_address, created_at, updated_at) \
             VALUES (?1, NULL, 'Default', 1, 1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?12)",
            rusqlite::params![
                uuid::Uuid::new_v4().simple().to_string(),
                imap_host,
                geti("imap_port", 993),
                gets("imap_user"),
                gets("imap_password"),
                getb("imap_starttls", true),
                smtp_host,
                geti("smtp_port", 465),
                gets("smtp_user"),
                gets("smtp_password"),
                gets("email_from"),
                now,
            ],
        )?;
        logger::info("Seeded email_accounts 'Default' from settings.json");
        Ok(())
    })();
    if let Err(e) = result {
        logger::warning(&format!("seed email account migration: {e}"));
    }
}

/// Shared body for the three "encrypt plaintext column(s)" migrations. For each
/// row, encrypt every named column that is non-empty and not already `enc:`.
/// Raw SQL so the EncryptedText decorator isn't applied twice.
///
/// `log_template` is the full success message with a `{n}` placeholder, so each
/// caller reproduces its Python log line verbatim (they differ in wording).
///
/// DEVIATION: Python wraps `from src.secret_storage import ...` in a per-call
/// try/except and, on `ImportError`, logs "secret_storage import failed;
/// skipping ... migration" and returns. In Rust `secret_storage` is statically
/// linked, so that runtime-import failure mode cannot occur and has no faithful
/// analogue — the distinct skip-log is intentionally not reproduced.
fn migrate_encrypt_columns(table: &str, id_col: &str, cols: &[&str], log_template: &str, warn: &str) {
    use crate::src::secret_storage::{encrypt, is_encrypted};
    let result = (|| -> rusqlite::Result<i64> {
        let conn = engine_connect()?;
        let select = format!("SELECT {id_col}, {} FROM {table}", cols.join(", "));
        let mut stmt = conn.prepare(&select)?;
        let ncols = cols.len();
        // Collect (id, [values]) so we can re-borrow conn for UPDATEs.
        let rows: Vec<(String, Vec<Option<String>>)> = stmt
            .query_map([], |r| {
                let id: String = r.get(0)?;
                let mut vals = Vec::with_capacity(ncols);
                for i in 0..ncols {
                    vals.push(r.get::<_, Option<String>>(i + 1)?);
                }
                Ok((id, vals))
            })?
            .filter_map(|r| r.ok())
            .collect();
        drop(stmt);

        let mut migrated = 0;
        for (rid, vals) in rows {
            // updates: which columns need encrypting (non-empty, not enc:).
            let mut sets: Vec<String> = Vec::new();
            let mut params: Vec<String> = Vec::new();
            for (i, col) in cols.iter().enumerate() {
                if let Some(v) = &vals[i] {
                    if !v.is_empty() && !is_encrypted(v) {
                        params.push(encrypt(v));
                        sets.push(format!("{col} = ?{}", params.len()));
                    }
                }
            }
            if !sets.is_empty() {
                let sql = format!(
                    "UPDATE {table} SET {} WHERE {id_col} = ?{}",
                    sets.join(", "),
                    params.len() + 1
                );
                params.push(rid);
                let p: Vec<&dyn rusqlite::ToSql> =
                    params.iter().map(|s| s as &dyn rusqlite::ToSql).collect();
                conn.execute(&sql, p.as_slice())?;
                migrated += 1;
            }
        }
        Ok(migrated)
    })();
    match result {
        Ok(n) if n > 0 => logger::info(&log_template.replace("{n}", &n.to_string())),
        Ok(_) => {}
        Err(e) => logger::warning(&format!("{warn}: {e}")),
    }
}

fn _migrate_encrypt_endpoint_keys() {
    migrate_encrypt_columns(
        "model_endpoints",
        "id",
        &["api_key"],
        "Encrypted plaintext API key on {n} endpoint row(s)",
        "Endpoint-key encryption migration skipped",
    );
}

fn _migrate_encrypt_signatures() {
    migrate_encrypt_columns(
        "signatures",
        "id",
        &["data_png", "svg"],
        "Encrypted plaintext signature(s) on {n} row(s)",
        "Signature encryption migration skipped",
    );
}

fn _migrate_encrypt_email_passwords() {
    migrate_encrypt_columns(
        "email_accounts",
        "id",
        &["imap_password", "smtp_password"],
        "Encrypted plaintext passwords on {n} email account row(s)",
        "Password migration failed (will retry next start)",
    );
}

/// `_migrate_add_vectors_table` — the ChromaDB->sqlite swap migration (no Python
/// counterpart). `create_all` already emits the `vectors` DDL for fresh
/// databases; this idempotent migration ensures it also exists on databases
/// created before the swap landed. `CREATE TABLE/INDEX IF NOT EXISTS` makes it a
/// no-op when the table is already present. Uses `engine.connect()` (auto-creates
/// the file), so it runs unconditionally like the other create_all-era migrations.
fn _migrate_add_vectors_table() {
    let conn = match engine_connect() {
        Ok(c) => c,
        Err(_) => return,
    };
    let result = conn.execute_batch(
        r#"
        CREATE TABLE IF NOT EXISTS vectors (
            collection VARCHAR NOT NULL,
            id VARCHAR NOT NULL,
            document TEXT,
            embedding BLOB,
            metadata TEXT,
            PRIMARY KEY (collection, id)
        );
        CREATE INDEX IF NOT EXISTS ix_vectors_collection ON vectors(collection);
        "#,
    );
    if let Err(e) = result {
        logger::warning(&format!("vectors table migration failed: {e}"));
    }
}

/// `init_db()` — exact order of the Python: model_endpoints migration, then
/// `create_all`, then the remaining migrations in their original sequence.
pub fn init_db() {
    _migrate_model_endpoints();
    if let Err(e) = create_all() {
        logger::warning(&format!("create_all failed: {e}"));
    }
    _migrate_add_hidden_models_column();
    _migrate_add_cached_models_column();
    _migrate_add_notes_sort_order();
    _migrate_add_model_type_column();
    _migrate_add_model_endpoint_refresh_columns();
    _migrate_add_model_endpoint_owner_column();
    _migrate_add_supports_tools_column();
    migrate_add_endpoint_column(
        "pinned_models",
        "TEXT",
        Some("Migrated: added 'pinned_models' column to model_endpoints"),
    );
    _migrate_add_email_smtp_security();
    _migrate_add_task_run_model_column();
    _migrate_add_owner_column();
    _migrate_add_document_archived_column();
    _migrate_add_last_message_at_column();
    _migrate_add_folder_column();
    _migrate_add_token_columns();
    _migrate_add_mode_column();
    _migrate_add_multiuser_owner_columns();
    _migrate_add_api_token_scopes_column();
    _migrate_backfill_document_owner_from_session();
    _migrate_assign_legacy_owner();
    _migrate_add_tidy_verdict();
    _migrate_add_doc_source_email_cols();
    _migrate_add_oauth_config();
    _migrate_add_task_automation_columns();
    _migrate_add_disabled_tools();
    _migrate_add_task_v2_columns();
    _migrate_add_notifications_enabled();
    _migrate_drop_ping_notes_tasks();
    _migrate_add_crew_member_id();
    _migrate_add_assistant_columns();
    _migrate_seed_email_account();
    _migrate_add_calendar_metadata();
    _migrate_add_calendar_is_utc();
    _migrate_encrypt_email_passwords();
    _migrate_encrypt_signatures();
    _migrate_encrypt_endpoint_keys();
    // ChromaDB->sqlite swap (no Python counterpart): ensure the `vectors` table
    // exists on databases created before the swap. No-op when already present.
    _migrate_add_vectors_table();
}

// ===========================================================================
// Query helpers
// ===========================================================================

/// Efficiently insert multiple messages.
///
/// `messages` is a list of `(role, content)`. The Python `bulk_insert_mappings`
/// sets only `session_id`, `role`, `content`, and `timestamp=utcnow()` — `id`
/// and `metadata` are left unset.
///
/// FAITHFUL NOTE: this is **dead code** in the Python (no callers anywhere) and,
/// if called, would *raise* — `chat_messages.id` is `NOT NULL PRIMARY KEY`, so an
/// insert with no `id` fails with an IntegrityError, which `get_db_session`
/// rolls back and **re-raises** (verified against the real SQLAlchemy DDL). The
/// translation preserves that: a single transaction (all-or-nothing, mirroring
/// the context manager's commit/rollback) whose error is propagated, not
/// swallowed. So `bulk_insert_messages(...)` returns `Err(...)` here exactly as
/// the Python raises.
pub fn bulk_insert_messages(
    session_id: &str,
    messages: &[(String, String)],
) -> rusqlite::Result<()> {
    let mut conn = session_local()?;
    let now = crate::pydatetime::utcnow_naive_iso();
    let tx = conn.transaction()?; // get_db_session: commit on success, rollback on error
    for (role, content) in messages {
        tx.execute(
            "INSERT INTO chat_messages (session_id, role, content, timestamp) \
             VALUES (?1, ?2, ?3, ?4)",
            rusqlite::params![session_id, role, content, now],
        )?;
    }
    tx.commit()?;
    Ok(())
}

/// Remove sessions older than `days` days.
///
/// `db.query(Session).filter(archived == True, last_accessed < cutoff,
/// is_important == False).delete()` — DELETEs archived, old, non-important
/// sessions and returns the deleted count.
pub fn cleanup_old_sessions(days: i64) -> i64 {
    let conn = match session_local() {
        Ok(c) => c,
        Err(e) => {
            logger::error(&format!("Error cleaning up old sessions: {e}"));
            return 0;
        }
    };
    // cutoff_date = datetime.utcnow() - timedelta(days=days)
    let cutoff = crate::pytime::time() - (days as f64 * 24.0 * 60.0 * 60.0);
    let cutoff_dt = crate::pydatetime::from_timestamp_naive_iso(cutoff);
    match conn.execute(
        "DELETE FROM sessions \
         WHERE archived = 1 AND last_accessed < ?1 AND is_important = 0",
        [&cutoff_dt],
    ) {
        Ok(n) => n as i64,
        Err(e) => {
            logger::error(&format!("Error cleaning up old sessions: {e}"));
            0
        }
    }
}

/// Get database statistics: session/message/memory counts.
pub fn get_session_stats() -> serde_json::Value {
    let empty = || {
        serde_json::json!({
            "total_sessions": 0, "active_sessions": 0, "archived_sessions": 0,
            "total_messages": 0, "total_memories": 0,
        })
    };
    let conn = match session_local() {
        Ok(c) => c,
        Err(_) => return empty(),
    };
    // NOTE: `conn.query_row(sql, [], |r| r.get(0))` must annotate the column
    // type. With the value buried inside a `json!{...}` field the compiler infers
    // `r.get(0)` as a *unit-typed* aggregate accessor and SQLite reports "no
    // rows" — so each count is a `let` with an explicit `i64` binding instead.
    fn count1(conn: &Connection, sql: &str) -> rusqlite::Result<i64> {
        conn.query_row(sql, [], |r| r.get::<_, i64>(0))
    }
    let result = (|| -> rusqlite::Result<serde_json::Value> {
        let total_sessions = count1(&conn, "SELECT COUNT(*) FROM sessions")?;
        let active_sessions = count1(&conn, "SELECT COUNT(*) FROM sessions WHERE archived = 0")?;
        let archived_sessions = count1(&conn, "SELECT COUNT(*) FROM sessions WHERE archived = 1")?;
        let total_messages = count1(&conn, "SELECT COUNT(*) FROM chat_messages")?;
        let total_memories = count1(&conn, "SELECT COUNT(*) FROM memories")?;
        Ok(serde_json::json!({
            "total_sessions": total_sessions,
            "active_sessions": active_sessions,
            "archived_sessions": archived_sessions,
            "total_messages": total_messages,
            "total_memories": total_memories,
        }))
    })();
    match result {
        Ok(v) => v,
        Err(e) => {
            logger::error(&format!("Error getting session stats: {e}"));
            empty()
        }
    }
}

/// Comprehensive statistics: `get_session_stats()` plus the SQLite file size in MB.
pub fn get_detailed_stats() -> serde_json::Value {
    let mut stats = get_session_stats();

    // db_size_mb = round(size / (1024*1024), 2) when the sqlite file exists.
    let mut db_size_mb = 0.0_f64;
    let url = database_url();
    if url.contains("sqlite") {
        let mut path = url.replace("sqlite:///", "");
        if !std::path::Path::new(&path).is_absolute() {
            path = crate::src::app_helpers::abs_join(".", &path);
        }
        if let Ok(meta) = std::fs::metadata(&path) {
            let mb = meta.len() as f64 / (1024.0 * 1024.0);
            db_size_mb = (mb * 100.0).round() / 100.0; // round(.., 2)
        }
    }
    if let Some(obj) = stats.as_object_mut() {
        obj.insert("database_size_mb".to_string(), serde_json::json!(db_size_mb));
    }
    stats
}

/// Update the `last_accessed` timestamp for a session. Returns `true` if the
/// session existed (and was updated), else `false`.
pub fn update_session_last_accessed(session_id: &str) -> bool {
    let conn = match session_local() {
        Ok(c) => c,
        Err(_) => return false,
    };
    let now = crate::pydatetime::utcnow_naive_iso();
    match conn.execute(
        "UPDATE sessions SET last_accessed = ?1 WHERE id = ?2",
        [&now, session_id],
    ) {
        Ok(n) => n > 0, // db_session existed -> True
        Err(_) => false,
    }
}

/// Get a session by ID (the row's `id`), or `None`.
pub fn get_session_by_id(session_id: &str) -> Option<Session> {
    let conn = session_local().ok()?;
    Session::query_by_id(&conn, session_id).ok().flatten()
}

/// `get_session_mode(session_id)` — the persisted `sessions.mode`, or `None` when
/// unset/unknown. Best-effort: never errors (`None` on any DB failure).
pub fn get_session_mode(session_id: &str) -> Option<String> {
    let conn = connect_existing()?;
    conn.query_row(
        "SELECT mode FROM sessions WHERE id = ?1",
        [session_id],
        |row| row.get::<_, Option<String>>(0),
    )
    .ok()
    .flatten()
}

/// `set_session_mode(session_id, mode)` — persist `sessions.mode`; `true` iff a row
/// matched. Best-effort (false on any failure).
pub fn set_session_mode(session_id: &str, mode: &str) -> bool {
    let conn = match connect_existing() {
        Some(c) => c,
        None => return false,
    };
    matches!(
        conn.execute("UPDATE sessions SET mode = ?1 WHERE id = ?2", [mode, session_id]),
        Ok(n) if n > 0
    )
}

/// `get_upcoming_events(owner, horizon_days=60, limit=40)` — upcoming non-cancelled
/// events as `{uid, title, start}`, soonest first. `owner=None` => NO owner
/// scoping (single-user/legacy); multi-user callers MUST pass the owner.
pub fn get_upcoming_events(
    owner: Option<&str>,
    horizon_days: i64,
    limit: i64,
) -> Vec<serde_json::Value> {
    use serde_json::json;
    let conn = match connect_existing() {
        Some(c) => c,
        None => return Vec::new(),
    };
    let now = chrono::Utc::now().naive_utc();
    let horizon = now + chrono::Duration::days(horizon_days);
    let now_s = now.format("%Y-%m-%d %H:%M:%S%.6f").to_string();
    let hor_s = horizon.format("%Y-%m-%d %H:%M:%S%.6f").to_string();

    let base = "SELECT e.uid, e.summary, e.dtstart \
                FROM calendar_events e JOIN calendars c ON e.calendar_id = c.id \
                WHERE e.dtstart >= ?1 AND e.dtstart <= ?2 AND e.status != 'cancelled'";
    let map_row = |row: &rusqlite::Row| -> rusqlite::Result<serde_json::Value> {
        let uid: String = row.get::<_, Option<String>>(0)?.unwrap_or_default();
        let summary: String = row.get::<_, Option<String>>(1)?.unwrap_or_default();
        let dtstart: String = row.get::<_, Option<String>>(2)?.unwrap_or_default();
        // dt.isoformat() — parse the stored "YYYY-MM-DD HH:MM:SS[.ffffff]" and emit
        // a T-separated form; fall back to the raw value on a parse failure.
        let start = chrono::NaiveDateTime::parse_from_str(&dtstart, "%Y-%m-%d %H:%M:%S%.f")
            .or_else(|_| chrono::NaiveDateTime::parse_from_str(&dtstart, "%Y-%m-%d %H:%M:%S"))
            .map(|d| d.format("%Y-%m-%dT%H:%M:%S").to_string())
            .unwrap_or(dtstart);
        Ok(json!({ "uid": uid, "title": summary, "start": start }))
    };

    let result: rusqlite::Result<Vec<serde_json::Value>> = if let Some(o) = owner {
        let sql = format!("{base} AND c.owner = ?3 ORDER BY e.dtstart LIMIT ?4");
        let mut stmt = match conn.prepare(&sql) {
            Ok(s) => s,
            Err(_) => return Vec::new(),
        };
        stmt.query_map(rusqlite::params![now_s, hor_s, o, limit], map_row)
            .map(|rows| rows.filter_map(|r| r.ok()).collect())
    } else {
        let sql = format!("{base} ORDER BY e.dtstart LIMIT ?3");
        let mut stmt = match conn.prepare(&sql) {
            Ok(s) => s,
            Err(_) => return Vec::new(),
        };
        stmt.query_map(rusqlite::params![now_s, hor_s, limit], map_row)
            .map(|rows| rows.filter_map(|r| r.ok()).collect())
    };
    result.unwrap_or_default()
}

/// Archive a session; `true` if it existed and was archived.
pub fn archive_session(session_id: &str) -> bool {
    let conn = match session_local() {
        Ok(c) => c,
        Err(_) => return false,
    };
    // The Python loads the row, sets archived, commits, returns True only when
    // the row existed (the UPDATE's rowcount is the same signal).
    match conn.execute("UPDATE sessions SET archived = 1 WHERE id = ?1", [session_id]) {
        Ok(n) => n > 0,
        Err(_) => false,
    }
}

// ===========================================================================
// Row structs (only for tables this file's helpers read; see header note)
// ===========================================================================

/// A `sessions` row (the SQLAlchemy `Session` model).
#[derive(Debug, Clone, Default)]
pub struct Session {
    pub id: String,
    pub name: String,
    pub endpoint_url: String,
    pub model: String,
    pub owner: Option<String>,
    pub rag: bool,
    pub archived: bool,
    pub folder: Option<String>,
    pub headers: Option<String>,
    pub last_accessed: Option<String>,
    pub last_message_at: Option<String>,
    pub is_important: bool,
    pub message_count: i64,
    pub total_input_tokens: i64,
    pub total_output_tokens: i64,
    pub mode: Option<String>,
    pub crew_member_id: Option<String>,
    pub created_at: Option<String>,
    pub updated_at: Option<String>,
}

impl Session {
    /// `@property is_active` — not archived.
    pub fn is_active(&self) -> bool {
        !self.archived
    }

    /// The `sessions` columns, in the exact order `from_row` indexes them. Shared
    /// by `query_by_id` and the `session_manager` queries so the column order can
    /// only ever be defined once.
    pub(crate) const SELECT_COLS: &'static str =
        "id, name, endpoint_url, model, owner, rag, archived, folder, headers, \
         last_accessed, last_message_at, is_important, message_count, \
         total_input_tokens, total_output_tokens, mode, crew_member_id, \
         created_at, updated_at";

    /// `db.query(Session).filter(Session.id == session_id).first()`.
    fn query_by_id(conn: &Connection, session_id: &str) -> rusqlite::Result<Option<Session>> {
        conn.query_row(
            &format!("SELECT {} FROM sessions WHERE id = ?1", Self::SELECT_COLS),
            [session_id],
            Session::from_row,
        )
        .optional()
    }

    pub(crate) fn from_row(r: &rusqlite::Row) -> rusqlite::Result<Session> {
        Ok(Session {
            id: r.get(0)?,
            name: r.get(1)?,
            endpoint_url: r.get(2)?,
            model: r.get(3)?,
            owner: r.get(4)?,
            rag: r.get::<_, Option<bool>>(5)?.unwrap_or(false),
            archived: r.get::<_, Option<bool>>(6)?.unwrap_or(false),
            folder: r.get(7)?,
            headers: r.get(8)?,
            last_accessed: r.get(9)?,
            last_message_at: r.get(10)?,
            is_important: r.get::<_, Option<bool>>(11)?.unwrap_or(false),
            message_count: r.get::<_, Option<i64>>(12)?.unwrap_or(0),
            total_input_tokens: r.get::<_, Option<i64>>(13)?.unwrap_or(0),
            total_output_tokens: r.get::<_, Option<i64>>(14)?.unwrap_or(0),
            mode: r.get(15)?,
            crew_member_id: r.get(16)?,
            created_at: r.get(17)?,
            updated_at: r.get(18)?,
        })
    }

    /// `to_dict()` — the JSON serialization the Python defines on this model.
    pub fn to_dict(&self) -> serde_json::Value {
        // `self.<dt>.isoformat() if self.<dt> else None` — None -> null,
        // otherwise the stored datetime rendered exactly as Python's isoformat.
        let iso = |s: &Option<String>| -> serde_json::Value {
            match s {
                Some(v) => serde_json::json!(crate::pydatetime::to_isoformat(v)),
                None => serde_json::Value::Null,
            }
        };
        serde_json::json!({
            "id": self.id,
            "name": self.name,
            "model": self.model,
            "endpoint_url": self.endpoint_url,
            "rag": self.rag,
            "archived": self.archived,
            "created_at": iso(&self.created_at),
            "updated_at": iso(&self.updated_at),
            "last_accessed": iso(&self.last_accessed),
            "last_message_at": iso(&self.last_message_at),
            "message_count": self.message_count,
            "is_important": self.is_important,
            "folder": self.folder,
            // `self.total_input_tokens or 0` — NULL/0 both -> 0 (already i64 here).
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "crew_member_id": self.crew_member_id,
        })
    }
}

/// Process-global lock serializing unit tests that swap the shared `DATABASE_URL`
/// env var. The connection target is process-wide, so two tests that each point
/// `DATABASE_URL` at their own temp DB and run concurrently would clobber each
/// other's target. Tests across modules (e.g. `chat_helpers`, `chat_routes`)
/// must lock THIS single mutex (not a per-module one) so they truly serialize.
#[cfg(test)]
pub(crate) static DB_TEST_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());

#[cfg(test)]
thread_local! {
    /// When `Some`, the on-disk path of a PRIVATE, thread-local test database the
    /// current thread set up via [`test_db`]. `db_path()` returns this for the
    /// current thread, so a test can point the DB at its own file WITHOUT mutating
    /// the process-global `DATABASE_URL` — concurrent tests therefore never clobber
    /// each other's target nor race on a sibling's file lifecycle. Best-effort DB
    /// reads in production helpers also consult this to mirror Python's
    /// `"core.database" not in sys.modules` guard: a unit test that did NOT set up
    /// a database skips the DB and returns the empty result.
    static TEST_DB_PATH: std::cell::RefCell<Option<String>> = const { std::cell::RefCell::new(None) };
}

/// The current thread's private test-DB path, if it set one up via [`test_db`].
#[cfg(test)]
pub(crate) fn current_thread_test_db() -> Option<String> {
    TEST_DB_PATH.with(|c| c.borrow().clone())
}

/// RAII handle to a private, thread-local test database (see [`TEST_DB_PATH`]).
/// On drop it restores the previous thread-local value and removes the temp file.
#[cfg(test)]
pub(crate) struct TestDb {
    path: std::path::PathBuf,
    prev: Option<String>,
}

#[cfg(test)]
impl Drop for TestDb {
    fn drop(&mut self) {
        let prev = self.prev.take();
        TEST_DB_PATH.with(|c| *c.borrow_mut() = prev);
        let _ = std::fs::remove_file(&self.path);
    }
}

/// Create a fresh, private temp database for the CURRENT THREAD and run
/// [`create_all`] on it. Unlike swapping the process-global `DATABASE_URL`, this
/// is thread-scoped, so it never races sibling tests. Any DB access made *on this
/// thread* (including the synchronous best-effort helpers) targets this file.
/// The returned guard must be held for the duration of the test.
#[cfg(test)]
pub(crate) fn test_db(tag: &str) -> TestDb {
    let path = std::env::temp_dir().join(format!(
        "odysseus_testdb_{tag}_{}_{}.db",
        std::process::id(),
        uuid::Uuid::new_v4().simple()
    ));
    let _ = std::fs::remove_file(&path);
    let prev = TEST_DB_PATH.with(|c| c.borrow_mut().replace(path.to_string_lossy().into_owned()));
    create_all().expect("test_db: create_all");
    TestDb { path, prev }
}
