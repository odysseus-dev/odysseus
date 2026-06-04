// core/session_manager.rs  <- core/session_manager.py
//! Session management — all session business logic and DB operations.
//!
//! This is the single place that handles:
//! - Loading/saving sessions to database
//! - Adding messages to sessions
//! - Session lifecycle (create, archive, delete)
//!
//! TRANSLATION NOTES
//! -----------------
//! * Python keeps `self.sessions: Dict[str, Session]` and mutates it through
//!   `self.` on every method. The faithful Rust shape is interior mutability:
//!   `sessions: Mutex<IndexMap<String, Session>>`, so every method takes `&self`
//!   (`IndexMap` preserves Python dict insertion order; `get_sessions_for_user`
//!   returns it in that order). `std::sync::Mutex` is non-reentrant, so methods
//!   that call into each other (`get_session` -> `_load_session_from_db`/
//!   `_touch_session`) only hold the lock in narrow scopes and never across a
//!   re-entrant call.
//! * The Python ORM (`db.query(DbSession)...`, relationships) is translated to
//!   raw SQL over `database::session_local()` connections — `session_manager.py`
//!   owns "all DB operations", and `database.py` owns the schema + row model.
//!   Each `SessionLocal()` + `try/finally: db.close()` becomes a fresh
//!   `Connection` dropped at scope end. `db.commit()`/`db.rollback()` over a unit
//!   of work become an explicit rusqlite transaction (commit on success, dropped
//!   = rolled back on the error path).
//! * `_db_id` write-back: Python's `_persist_message` mutates the *passed*
//!   `message.metadata['_db_id']`. The `SessionPersistence` trait hands us
//!   `&ChatMessage` (shared), so the trait impl cannot write back to the caller's
//!   object — that is the documented ownership deviation. `add_message`/
//!   `replace_messages` (which own their messages) still set `_db_id` on the
//!   stored history, matching the end state of the common path.
//! * `KeyError(f"Session {id} not found")` -> `PyError::key(...)`; `get_session`
//!   and the truncate/replace paths that call it are therefore `PyResult`.

use crate::core::database::{self, session_local};
use crate::core::models::{ChatMessage, Session, SessionPersistence};
use crate::error::{PyError, PyResult};
use crate::pydatetime;
use crate::pylog as logger;
use chrono::Duration;
use indexmap::IndexMap;
use rusqlite::OptionalExtension;
use serde_json::{Map, Value};
use std::sync::Mutex;

/// Manages chat sessions with database persistence.
///
/// Usage:
/// ```ignore
/// let manager = SessionManager::new();
/// let session = manager.create_session(id, name, url, model, false, None)?;
/// manager.add_message(&session.id, ChatMessage::new("user", "hello", None))?;
/// let session = manager.get_session(session_id)?;
/// ```
pub struct SessionManager {
    /// `self.sessions: Dict[str, Session]` — the in-memory cache.
    sessions: Mutex<IndexMap<String, Session>>,
}

impl Default for SessionManager {
    fn default() -> Self {
        Self::new()
    }
}

impl SessionManager {
    /// `__init__(self, sessions_file=None)` — `sessions_file` is kept for backward
    /// compat in Python and not used; the constructor just `load_sessions()`.
    pub fn new() -> Self {
        let manager = SessionManager {
            sessions: Mutex::new(IndexMap::new()),
        };
        manager.load_sessions();
        manager
    }

    // ------------------------------------------------------------------
    // Loading
    // ------------------------------------------------------------------

    /// Load recent session METADATA from the database — messages are hydrated on
    /// demand by `get_session`. (See the Python docstring: avoids walking every
    /// message of every session into RAM at boot.)
    pub fn load_sessions(&self) {
        let conn = match session_local() {
            Ok(c) => c,
            Err(e) => {
                logger::error(&format!("Error loading sessions: {e}"));
                *self.sessions.lock().unwrap() = IndexMap::new();
                return;
            }
        };

        // db.query(DbSession).filter(archived == False, message_count > 0)
        //   .order_by(last_accessed.desc()).limit(100).all()
        let query = format!(
            "SELECT {} FROM sessions \
             WHERE archived = 0 AND message_count > 0 \
             ORDER BY last_accessed DESC LIMIT 100",
            database::Session::SELECT_COLS
        );
        let db_sessions: Vec<database::Session> = match (|| -> rusqlite::Result<_> {
            let mut stmt = conn.prepare(&query)?;
            let rows = stmt
                .query_map([], database::Session::from_row)?
                .filter_map(|r| r.ok())
                .collect::<Vec<_>>();
            Ok(rows)
        })() {
            Ok(rows) => rows,
            Err(e) => {
                logger::error(&format!("Error loading sessions: {e}"));
                *self.sessions.lock().unwrap() = IndexMap::new();
                return;
            }
        };

        let mut loaded_count = 0;
        {
            let mut store = self.sessions.lock().unwrap();
            for db_session in &db_sessions {
                // Python wraps each row in try/except; `_db_to_session_meta` can't
                // raise here (no parse that escapes), but keep the per-row guard
                // shape: a bad row is logged and skipped.
                match Self::_db_to_session_meta(db_session) {
                    Ok(session) => {
                        store.insert(db_session.id.clone(), session);
                        loaded_count += 1;
                    }
                    Err(e) => {
                        logger::error(&format!("Error loading session {}: {e}", db_session.id));
                        continue;
                    }
                }
            }
        }
        logger::info(&format!("Loaded {loaded_count} session(s) (metadata only)"));
    }

    /// Build a Session with empty history. `get_session` will hydrate messages
    /// from the DB on first read.
    ///
    /// Python types this `Optional[Session]` but always returns a `Session`; the
    /// caller's `if meta is None` guard is consequently dead. We return the
    /// `Session` (in `PyResult` only so a future parse can surface, mirroring the
    /// per-row `try/except` in `load_sessions`).
    fn _db_to_session_meta(db_session: &database::Session) -> PyResult<Session> {
        let headers = Self::parse_headers(&db_session.headers);
        let mut session = Session {
            id: db_session.id.clone(),
            name: db_session.name.clone(),
            endpoint_url: db_session.endpoint_url.clone(),
            model: db_session.model.clone(),
            rag: db_session.rag,
            archived: db_session.archived,
            headers,
            history: Vec::new(),
            owner: db_session.owner.clone(),
            // `getattr(db_session, "is_important", False) or False`
            is_important: db_session.is_important,
            ..Session::default()
        };
        // `session.message_count = getattr(db_session, "message_count", 0) or 0`
        session.message_count = db_session.message_count;
        Ok(session)
    }

    /// Convert a database session (+ its messages) to a `Session`.
    ///
    /// Python tries the `db_session.messages` relationship first, then falls back
    /// to an explicit `order_by(timestamp)` query. The relationship has no
    /// `order_by`, so it yields rowid (= insertion) order; the fallback yields
    /// timestamp order. Both insert with monotonically-increasing timestamps, so
    /// the two orders coincide — we issue the single explicit `ORDER BY timestamp`
    /// query (the documented intent) for every row.
    fn _db_to_session(
        conn: &rusqlite::Connection,
        db_session: &database::Session,
    ) -> PyResult<Option<Session>> {
        let mut history: Vec<ChatMessage> = Vec::new();

        let mut stmt = conn
            .prepare(
                "SELECT id, role, content, metadata FROM chat_messages \
                 WHERE session_id = ?1 ORDER BY timestamp",
            )
            .map_err(db_err)?;
        let rows = stmt
            .query_map([&db_session.id], |r| {
                Ok((
                    r.get::<_, String>(0)?,         // id
                    r.get::<_, String>(1)?,         // role
                    r.get::<_, String>(2)?,         // content
                    r.get::<_, Option<String>>(3)?, // metadata (column "metadata")
                ))
            })
            .map_err(db_err)?;

        for row in rows {
            let (db_id, role, content, meta_data) = row.map_err(db_err)?;
            // meta = json.loads(meta_data) if meta_data else {}; if None: {}
            let mut meta = parse_meta(&meta_data)?;
            meta.insert("_db_id".to_string(), Value::String(db_id));
            history.push(ChatMessage::new(role, content, Some(meta)));
        }

        if history.is_empty() {
            return Ok(None);
        }

        let headers = Self::parse_headers(&db_session.headers);
        let mut session = Session {
            id: db_session.id.clone(),
            name: db_session.name.clone(),
            endpoint_url: db_session.endpoint_url.clone(),
            model: db_session.model.clone(),
            rag: db_session.rag,
            archived: db_session.archived,
            headers,
            history,
            owner: db_session.owner.clone(),
            is_important: db_session.is_important,
            ..Session::default()
        };
        // `getattr(db_session, 'message_count', len(history))` — the attribute
        // always exists, so this is just the stored count. (Model-type note: the
        // `i64` field renders a NULL column as 0 where Python's `_db_to_session`
        // would carry `None`; immaterial here since this path only runs for a
        // message-bearing row, whose count is never NULL.)
        session.message_count = db_session.message_count;
        Ok(Some(session))
    }

    /// `headers = db_session.headers; if isinstance(headers, str): json.loads(...)`.
    ///
    /// SQLAlchemy deserializes the `JSON` column to a dict on read; the rusqlite
    /// layer hands us the raw stored text, so we always parse it. A non-object
    /// (or unparseable) value becomes `{}` — matching `json.loads` failure (`{}`)
    /// and `__post_init__`'s `None -> {}`.
    fn parse_headers(stored: &Option<String>) -> IndexMap<String, String> {
        let mut out = IndexMap::new();
        let text = match stored {
            Some(t) => t,
            None => return out, // SQL NULL -> None -> __post_init__ {}
        };
        // SQLAlchemy decodes the `JSON` column once on read; reproduce that decode
        // of the raw stored text.
        let decoded = match serde_json::from_str::<Value>(text) {
            Ok(v) => v,
            Err(_) => return out,
        };
        // `if isinstance(headers, str): headers = json.loads(headers)` — when the
        // decoded value is itself a *string* (a double-encoded object, e.g. a
        // legacy row that stored `json.dumps(dict)` into the JSON column), decode
        // it a second time; `{}` on failure (the `except json.JSONDecodeError`).
        let obj = match decoded {
            Value::Object(map) => map,
            Value::String(s) => match serde_json::from_str::<Value>(&s) {
                Ok(Value::Object(map)) => map,
                _ => return out,
            },
            _ => return out, // null / number / bool / array -> {}
        };
        for (k, v) in obj {
            // DEVIATION: `models::Session.headers` is `IndexMap<String, String>`,
            // matching the Python type hint `Optional[Dict[str, str]]`. `json.loads`
            // would keep a non-string value as-is; here it is coerced to its JSON
            // text. HTTP headers are str->str, so this is contractually safe.
            let val = match v {
                Value::String(s) => s,
                other => other.to_string(),
            };
            out.insert(k, val);
        }
        out
    }

    // ------------------------------------------------------------------
    // Message operations
    // ------------------------------------------------------------------

    /// Add a message to a session and persist to database.
    pub fn add_message(&self, session_id: &str, message: ChatMessage) -> PyResult<()> {
        // session = self.get_session(session_id)  (hydrate + touch; may KeyError)
        self.get_session(session_id)?;

        // session.history.append(message); session.message_count = len(history)
        {
            let mut store = self.sessions.lock().unwrap();
            if let Some(session) = store.get_mut(session_id) {
                session.history.push(message.clone());
                session.message_count = session.history.len() as i64;
            }
        }

        // self._persist_message(session_id, message)
        if let Some(msg_id) = self.persist_message_db(session_id, &message) {
            // Python writes `message.metadata['_db_id'] = msg_id`; `message` here
            // is the just-appended (and stored) object, so tag the stored copy.
            let mut store = self.sessions.lock().unwrap();
            if let Some(session) = store.get_mut(session_id) {
                if let Some(last) = session.history.last_mut() {
                    last.metadata
                        .get_or_insert_with(Map::new)
                        .insert("_db_id".to_string(), Value::String(msg_id));
                }
            }
        }
        Ok(())
    }

    /// Persist a single message to the database, returning the generated row id on
    /// a successful commit (`None` on a DB error — Python logs + rolls back).
    ///
    /// Insert + the session-counter update are one transaction (the Python
    /// `db.add(...)` + counter writes share a single `db.commit()`). The message
    /// row is always inserted; the session counters are only bumped when the
    /// session row exists (`if db_session:`).
    fn persist_message_db(&self, session_id: &str, message: &ChatMessage) -> Option<String> {
        let mut conn = match session_local() {
            Ok(c) => c,
            Err(e) => {
                logger::error(&format!("Error persisting message: {e}"));
                return None;
            }
        };
        // message_count = len(self.sessions[session_id].history) if present else 0
        let count = {
            let store = self.sessions.lock().unwrap();
            store
                .get(session_id)
                .map(|s| s.history.len() as i64)
                .unwrap_or(0)
        };

        let msg_id = uuid::Uuid::new_v4().to_string();
        let meta_json = serialize_meta(&message.metadata);
        let now = pydatetime::utcnow_naive_iso();

        let result = (|| -> rusqlite::Result<()> {
            let tx = conn.transaction()?;
            // `DbChatMessage` omits `timestamp`, relying on its
            // `default=datetime.utcnow` (database.py) — SQLAlchemy fills it at
            // INSERT. The DDL carries no DEFAULT, so we must set it explicitly,
            // or the row's `timestamp` would be NULL and sort BEFORE the
            // real-timestamp rows that `replace_messages` writes (corrupting the
            // `ORDER BY timestamp` history reload).
            tx.execute(
                "INSERT INTO chat_messages (id, session_id, role, content, metadata, timestamp) \
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
                rusqlite::params![msg_id, session_id, message.role, message.content, meta_json, now],
            )?;
            // if db_session: bump message_count + last_accessed + last_message_at
            let exists: bool = tx
                .query_row("SELECT 1 FROM sessions WHERE id = ?1", [session_id], |_| Ok(()))
                .optional()?
                .is_some();
            if exists {
                tx.execute(
                    "UPDATE sessions SET message_count = ?1, last_accessed = ?2, \
                     last_message_at = ?2 WHERE id = ?3",
                    rusqlite::params![count, now, session_id],
                )?;
            }
            tx.commit()
        })();

        match result {
            Ok(()) => {
                logger::debug(&format!("Persisted message to session {session_id}"));
                Some(msg_id)
            }
            Err(e) => {
                logger::error(&format!("Error persisting message: {e}"));
                None
            }
        }
    }

    /// Truncate session history, keeping only the first `keep_count` messages.
    pub fn truncate_messages(&self, session_id: &str, keep_count: i64) -> PyResult<bool> {
        // session = self.get_session(session_id)  (may KeyError)
        self.get_session(session_id)?;

        if keep_count < 0 {
            return Ok(false);
        }

        let mut conn = match session_local() {
            Ok(c) => c,
            Err(e) => {
                logger::error(&format!("Error truncating session: {e}"));
                return Ok(false);
            }
        };

        let result = (|| -> rusqlite::Result<()> {
            let tx = conn.transaction()?;
            // db_messages = query(...).order_by(timestamp).all(); delete [keep_count:]
            let ids: Vec<String> = {
                let mut stmt = tx.prepare(
                    "SELECT id FROM chat_messages WHERE session_id = ?1 ORDER BY timestamp",
                )?;
                let rows = stmt.query_map([session_id], |r| r.get::<_, String>(0))?;
                rows.filter_map(|r| r.ok()).collect()
            };
            for id in ids.iter().skip(keep_count as usize) {
                tx.execute("DELETE FROM chat_messages WHERE id = ?1", [id])?;
            }
            // if db_session: message_count = keep_count; updated_at = now.
            // The `if db_session:` guard collapses into the `WHERE id = ?`-scoped
            // UPDATE (a no-op when the row is absent — which can't happen here,
            // since the preceding `get_session` guarantees the row exists).
            let now = pydatetime::utcnow_naive_iso();
            tx.execute(
                "UPDATE sessions SET message_count = ?1, updated_at = ?2 WHERE id = ?3",
                rusqlite::params![keep_count, now, session_id],
            )?;
            tx.commit()
        })();

        match result {
            Ok(()) => {
                // session.history = session.history[:keep_count]
                {
                    let mut store = self.sessions.lock().unwrap();
                    if let Some(session) = store.get_mut(session_id) {
                        session.history.truncate(keep_count as usize);
                    }
                }
                logger::info(&format!("Truncated session {session_id} to {keep_count} messages"));
                Ok(true)
            }
            Err(e) => {
                logger::error(&format!("Error truncating session: {e}"));
                Ok(false)
            }
        }
    }

    /// Replace a session's persisted and in-memory history atomically.
    pub fn replace_messages(
        &self,
        session_id: &str,
        messages: Vec<ChatMessage>,
    ) -> PyResult<bool> {
        // session = self.get_session(session_id)  (may KeyError)
        self.get_session(session_id)?;

        let mut conn = match session_local() {
            Ok(c) => c,
            Err(e) => {
                logger::error(&format!("Error replacing session history: {e}"));
                return Ok(false);
            }
        };

        // `now = datetime.now(timezone.utc)`; each message gets `now + i µs`.
        let now = pydatetime::utcnow_naive();
        let now_iso = pydatetime::naive_to_iso(now);

        // Mutated copies: each gets its `_db_id` set, then becomes the new history.
        let mut messages = messages;
        let result = (|| -> rusqlite::Result<()> {
            let tx = conn.transaction()?;
            tx.execute("DELETE FROM chat_messages WHERE session_id = ?1", [session_id])?;
            for (i, message) in messages.iter_mut().enumerate() {
                let msg_id = uuid::Uuid::new_v4().to_string();
                let ts = pydatetime::naive_to_iso(now + Duration::microseconds(i as i64));
                let meta_json = serialize_meta(&message.metadata);
                tx.execute(
                    "INSERT INTO chat_messages (id, session_id, role, content, metadata, timestamp) \
                     VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
                    rusqlite::params![
                        msg_id,
                        session_id,
                        message.role,
                        message.content,
                        meta_json,
                        ts
                    ],
                )?;
                // if message.metadata is None: message.metadata = {}; ['_db_id'] = msg_id
                message
                    .metadata
                    .get_or_insert_with(Map::new)
                    .insert("_db_id".to_string(), Value::String(msg_id));
            }
            // if db_session: message_count=len; updated_at=last_accessed=last_message_at=now
            tx.execute(
                "UPDATE sessions SET message_count = ?1, updated_at = ?2, \
                 last_accessed = ?2, last_message_at = ?2 WHERE id = ?3",
                rusqlite::params![messages.len() as i64, now_iso, session_id],
            )?;
            tx.commit()
        })();

        match result {
            Ok(()) => {
                let n = messages.len();
                // session.history = list(messages); session.message_count = len
                {
                    let mut store = self.sessions.lock().unwrap();
                    if let Some(session) = store.get_mut(session_id) {
                        session.history = messages;
                        session.message_count = n as i64;
                    }
                }
                logger::info(&format!(
                    "Replaced session {session_id} history with {n} messages"
                ));
                Ok(true)
            }
            Err(e) => {
                logger::error(&format!("Error replacing session history: {e}"));
                Ok(false)
            }
        }
    }

    // ------------------------------------------------------------------
    // Session CRUD
    // ------------------------------------------------------------------

    /// Get a session by ID, loading from DB if needed. Sessions seeded by
    /// `load_sessions` start with empty history; the first read here hydrates them.
    pub fn get_session(&self, session_id: &str) -> PyResult<Session> {
        // `if id not in self.sessions: load; else: lazy-hydrate if empty & count>0`
        let need_load = {
            let store = self.sessions.lock().unwrap();
            match store.get(session_id) {
                None => true,
                Some(cached) => cached.history.is_empty() && cached.message_count > 0,
            }
        };
        if need_load {
            self._load_session_from_db(session_id)?;
        }

        // Update last_accessed
        self._touch_session(session_id);

        // return self.sessions[session_id]  (present after a successful load)
        let store = self.sessions.lock().unwrap();
        store
            .get(session_id)
            .cloned()
            .ok_or_else(|| PyError::key(format!("Session {session_id} not found")))
    }

    /// Hydrate a single session (with messages) from the database.
    ///
    /// `pub` so the `session_routes` port can reproduce `unarchive_session`'s
    /// `session_manager._load_session_from_db(sid)` reload (the `else` branch that
    /// pulls a freshly-unarchived row back into the live cache). Python wraps that
    /// call in `try/except: pass`, so the route ignores the `PyResult`.
    pub fn _load_session_from_db(&self, session_id: &str) -> PyResult<()> {
        let conn = session_local().map_err(db_err)?;

        // db_session = query(...).first(); if None: raise KeyError("... not found")
        let db_session = database::Session::from_id(&conn, session_id)
            .map_err(|e| {
                logger::error(&format!("Error loading session {session_id}: {e}"));
                db_err(e)
            })?
            .ok_or_else(|| PyError::key(format!("Session {session_id} not found")))?;

        // session = self._db_to_session(db_session, db)
        let session = Self::_db_to_session(&conn, &db_session).map_err(|e| {
            logger::error(&format!("Error loading session {session_id}: {e}"));
            e
        })?;

        let mut store = self.sessions.lock().unwrap();
        match session {
            Some(s) => {
                store.insert(session_id.to_string(), s);
            }
            None => {
                // No messages — fall back to metadata-only entry. (`_db_to_session_meta`
                // never returns None, so the Python's second KeyError is dead.)
                let meta = Self::_db_to_session_meta(&db_session)?;
                store.insert(session_id.to_string(), meta);
            }
        }
        Ok(())
    }

    /// Update `last_accessed` timestamp.
    fn _touch_session(&self, session_id: &str) {
        let conn = match session_local() {
            Ok(c) => c,
            Err(e) => {
                logger::error(&format!("Error updating last_accessed: {e}"));
                return;
            }
        };
        let now = pydatetime::utcnow_naive_iso();
        // if db_session: db_session.last_accessed = now; commit
        if let Err(e) = conn.execute(
            "UPDATE sessions SET last_accessed = ?1 WHERE id = ?2",
            rusqlite::params![now, session_id],
        ) {
            logger::error(&format!("Error updating last_accessed: {e}"));
        }
    }

    /// Create a new session and save to database.
    pub fn create_session(
        &self,
        session_id: &str,
        name: &str,
        endpoint_url: &str,
        model: &str,
        rag: bool,
        owner: Option<&str>,
    ) -> PyResult<Session> {
        let conn = session_local().map_err(|e| {
            logger::error(&format!("Error creating session: {e}"));
            db_err(e)
        })?;

        let now = pydatetime::utcnow_naive_iso();
        // DbSession(id, name, endpoint_url, model, rag, headers={}, owner,
        //           created_at=now, updated_at=now). The flag/count columns take
        // their DDL DEFAULTs (the SQLAlchemy client-side defaults). `last_accessed`
        // is NOT one of those: its default is `func.now()` (a SQL default applied
        // at INSERT), which the DDL doesn't carry — so we set it to SQLite's
        // `CURRENT_TIMESTAMP` (exactly what `func.now()` lowers to), leaving a
        // never-touched session with a non-NULL `last_accessed` as in Python.
        let insert = conn.execute(
            "INSERT INTO sessions (id, name, endpoint_url, model, rag, headers, owner, \
             last_accessed, created_at, updated_at) \
             VALUES (?1, ?2, ?3, ?4, ?5, '{}', ?6, CURRENT_TIMESTAMP, ?7, ?7)",
            rusqlite::params![session_id, name, endpoint_url, model, rag, owner, now],
        );
        if let Err(e) = insert {
            logger::error(&format!("Error creating session: {e}"));
            return Err(db_err(e));
        }

        let session = Session {
            id: session_id.to_string(),
            name: name.to_string(),
            endpoint_url: endpoint_url.to_string(),
            model: model.to_string(),
            rag,
            owner: owner.map(|o| o.to_string()),
            ..Session::default()
        };
        self.sessions
            .lock()
            .unwrap()
            .insert(session_id.to_string(), session.clone());
        Ok(session)
    }

    /// Permanently delete a session and all its messages.
    pub fn delete_session(&self, session_id: &str) -> bool {
        let mut conn = match session_local() {
            Ok(c) => c,
            Err(e) => {
                logger::error(&format!("Error deleting session: {e}"));
                return false;
            }
        };

        // The Python detaches documents and deletes messages *then* only commits
        // when the session row exists; if it doesn't, `db.close()` discards the
        // pending changes. One transaction reproduces that: commit iff present.
        let outcome = (|| -> rusqlite::Result<bool> {
            let tx = conn.transaction()?;
            // Detach documents (session_id -> NULL) so they survive as orphans.
            tx.execute(
                "UPDATE documents SET session_id = NULL WHERE session_id = ?1",
                [session_id],
            )?;
            // Delete messages.
            tx.execute("DELETE FROM chat_messages WHERE session_id = ?1", [session_id])?;
            // Delete session (only commit when it existed).
            let exists: bool = tx
                .query_row("SELECT 1 FROM sessions WHERE id = ?1", [session_id], |_| Ok(()))
                .optional()?
                .is_some();
            if exists {
                tx.execute("DELETE FROM sessions WHERE id = ?1", [session_id])?;
                tx.commit()?;
                Ok(true)
            } else {
                // No commit -> rollback on drop, matching the Python's `return False`.
                Ok(false)
            }
        })();

        match outcome {
            Ok(true) => {
                self.sessions.lock().unwrap().shift_remove(session_id);
                logger::info(&format!("Deleted session {session_id}"));
                true
            }
            Ok(false) => false,
            Err(e) => {
                logger::error(&format!("Error deleting session: {e}"));
                false
            }
        }
    }

    /// Evict ids from the in-memory `sessions` cache ONLY (no DB).
    ///
    /// Mirrors the Python cleanup tail
    /// (`src/cleanup_service.py` `cleanup_old_sessions`):
    /// after the bulk `DELETE FROM sessions`, each deleted id is dropped from the
    /// live `session_manager.sessions` dict
    /// (`for id in session_ids: if id in session_manager.sessions: del session_manager.sessions[id]`).
    /// Without this, `get_session` would keep serving a hydrated-but-deleted row
    /// from the cache until process restart. The DB delete is the caller's
    /// responsibility — this only touches the in-memory map.
    pub fn evict_cached_sessions(&self, session_ids: &[String]) {
        let mut store = self.sessions.lock().unwrap();
        for session_id in session_ids {
            // `if session_id in session_manager.sessions: del ...` — shift_remove is
            // a no-op when absent, matching the `in` guard.
            store.shift_remove(session_id);
        }
    }

    // ------------------------------------------------------------------
    // Session updates
    // ------------------------------------------------------------------

    /// Update session name.
    pub fn update_session_name(&self, session_id: &str, name: &str) -> PyResult<()> {
        // if session_id not in self.sessions: return
        if !self.sessions.lock().unwrap().contains_key(session_id) {
            return Ok(());
        }
        let conn = session_local().map_err(|e| {
            logger::error(&format!("Error updating session name: {e}"));
            db_err(e)
        })?;
        let now = pydatetime::utcnow_naive_iso();
        // if db_session: name=...; updated_at=now; commit; self.sessions[id].name = name
        match conn.execute(
            "UPDATE sessions SET name = ?1, updated_at = ?2 WHERE id = ?3",
            rusqlite::params![name, now, session_id],
        ) {
            Ok(n) if n > 0 => {
                if let Some(s) = self.sessions.lock().unwrap().get_mut(session_id) {
                    s.name = name.to_string();
                }
                Ok(())
            }
            Ok(_) => Ok(()), // db_session was None
            Err(e) => {
                logger::error(&format!("Error updating session name: {e}"));
                Err(db_err(e))
            }
        }
    }

    /// Set a session's HTTP headers (the resolved LLM-endpoint auth) in both the
    /// in-memory cache and the DB `sessions.headers` JSON column.
    ///
    /// The Python resolves endpoint auth per-request (`resolve_session_auth` ->
    /// `sess.headers`); persisting them here lets the streaming chat handler read
    /// them straight off the loaded session. Best-effort on the DB write.
    pub fn update_session_headers(&self, session_id: &str, headers: IndexMap<String, String>) {
        if let Ok(conn) = session_local() {
            let json_text = serde_json::to_string(&headers).unwrap_or_else(|_| "{}".to_string());
            let _ = conn.execute(
                "UPDATE sessions SET headers = ?1 WHERE id = ?2",
                rusqlite::params![json_text, session_id],
            );
        }
        if let Some(s) = self.sessions.lock().unwrap().get_mut(session_id) {
            s.headers = headers;
        }
    }

    /// Archive a session.
    pub fn archive_session(&self, session_id: &str) -> PyResult<()> {
        // if session_id not in self.sessions: return
        if !self.sessions.lock().unwrap().contains_key(session_id) {
            return Ok(());
        }
        let conn = session_local().map_err(|e| {
            logger::error(&format!("Error archiving session: {e}"));
            db_err(e)
        })?;
        let now = pydatetime::utcnow_naive_iso();
        match conn.execute(
            "UPDATE sessions SET archived = 1, updated_at = ?1 WHERE id = ?2",
            rusqlite::params![now, session_id],
        ) {
            Ok(n) if n > 0 => {
                if let Some(s) = self.sessions.lock().unwrap().get_mut(session_id) {
                    s.archived = true;
                }
                Ok(())
            }
            Ok(_) => Ok(()),
            Err(e) => {
                logger::error(&format!("Error archiving session: {e}"));
                Err(db_err(e))
            }
        }
    }

    /// Mark session as important. Unlike name/archive, this does *not* early-return
    /// on a cache miss: a missing DB row raises `KeyError`.
    pub fn mark_important(&self, session_id: &str, important: bool) -> PyResult<()> {
        let conn = session_local().map_err(|e| {
            logger::error(&format!("Error marking session important: {e}"));
            db_err(e)
        })?;
        let now = pydatetime::utcnow_naive_iso();
        // if db_session: set is_important; commit; update cache. else: raise KeyError
        let updated = conn
            .execute(
                "UPDATE sessions SET is_important = ?1, updated_at = ?2 WHERE id = ?3",
                rusqlite::params![important, now, session_id],
            )
            .map_err(|e| {
                logger::error(&format!("Error marking session important: {e}"));
                db_err(e)
            })?;
        if updated > 0 {
            if let Some(s) = self.sessions.lock().unwrap().get_mut(session_id) {
                s.is_important = important;
            }
            Ok(())
        } else {
            Err(PyError::key(format!("Session {session_id} not found")))
        }
    }

    // ------------------------------------------------------------------
    // Queries
    // ------------------------------------------------------------------

    /// Return sessions for a specific user (or all if `username` is `None`).
    pub fn get_sessions_for_user(&self, username: Option<&str>) -> IndexMap<String, Session> {
        let store = self.sessions.lock().unwrap();
        match username {
            None => store.clone(),
            Some(u) => store
                .iter()
                .filter(|(_, s)| s.owner.as_deref() == Some(u))
                .map(|(k, v)| (k.clone(), v.clone()))
                .collect(),
        }
    }

    /// Return a clone of a session from the in-memory cache ONLY — no DB
    /// hydration, no `last_accessed` touch, `None` when absent.
    ///
    /// Maps the route ports' `session_manager.sessions.get(session_id)` (e.g.
    /// `history_routes.fork_session`: `source = session_manager.sessions.get(sid)`
    /// then `if not source: raise HTTPException(404)`). Distinct from
    /// `get_session`, which hydrates from the DB, touches `last_accessed`, and
    /// raises `KeyError` on a miss.
    pub fn peek_session(&self, session_id: &str) -> Option<Session> {
        self.sessions.lock().unwrap().get(session_id).cloned()
    }

    /// Run `f` against a `&mut Session` in the in-memory cache under the lock,
    /// returning `Some(f(...))` when the session is present and `None` when it is
    /// absent (no DB hydration).
    ///
    /// This is the faithful primitive for every route line that mutates the live
    /// `session_manager.sessions[id]` object: `archive`/`unarchive` flipping
    /// `.archived`, `mark_session_important` setting `.is_important`,
    /// `rename_session`'s mid-session `.model`/`.endpoint_url` switch, and the
    /// `history_routes` in-memory `.history` edits (DB-fallback rebuild,
    /// delete/edit/merge, mark-stopped, update-last-meta). The closure runs the
    /// whole read→compute→write under one lock, matching Python's operations on
    /// the live object. The `Option` return reproduces the `if id in
    /// session_manager.sessions:` guard those handlers wrap the mutation in.
    pub fn with_session_mut<F, R>(&self, session_id: &str, f: F) -> Option<R>
    where
        F: FnOnce(&mut Session) -> R,
    {
        self.sessions.lock().unwrap().get_mut(session_id).map(f)
    }

    /// Empty the in-memory session cache — maps `session_manager.sessions.clear()`
    /// in `session_routes.delete_all_sessions` (the admin "delete ALL sessions"
    /// path, after the DB rows are wiped). In-memory only; the route does the
    /// raw-SQL `DELETE` itself.
    pub fn clear_sessions(&self) {
        self.sessions.lock().unwrap().clear();
    }

    /// No-op for DB compatibility.
    pub fn save_sessions(&self) {}

    // ------------------------------------------------------------------
    // Cleanup
    // ------------------------------------------------------------------

    /// Clean up empty and old sessions.
    ///
    /// FAITHFUL REPRODUCTION OF A PYTHON BUG. The Python builds
    /// `cutoff_date = datetime.now(timezone.utc) - timedelta(...)` — timezone
    /// *aware* — and compares it against `db_session.last_accessed`, which
    /// SQLAlchemy's plain `Column(DateTime)` on SQLite reads back *naive*. The
    /// archive `elif`'s short-circuit reaches `last_accessed < cutoff_date` for
    /// ANY non-archived session with a non-null `last_accessed`, where
    /// `naive < aware` raises `TypeError: can't compare offset-naive and
    /// offset-aware datetimes`. That propagates to the `except Exception`, which
    /// `db.rollback()`s (undoing the empty-session DELETEs queued earlier this
    /// loop — but NOT the in-memory `del self.sessions[id]`s) and RE-RAISES.
    /// Consequences, all reproduced here: (a) the archive UPDATE is unreachable,
    /// so `archived_old` is never incremented; (b) the function raises whenever a
    /// normal active session exists; (c) on that raise, the DB empty-deletes roll
    /// back while the cache removals persist. Only when NO non-archived,
    /// non-empty, non-null-`last_accessed` session exists does it complete and
    /// commit the empty-deletes. (Verified against SQLAlchemy 2.0 by the
    /// py<->rs verification workflow; the skill mandates preserving observable
    /// behavior — exceptions included — over silently "fixing" the bug.)
    pub fn cleanup_empty_sessions(&self, auto_archive_days: i64) -> PyResult<Value> {
        let mut conn = session_local().map_err(|e| {
            logger::error(&format!("Cleanup error: {e}"));
            db_err(e)
        })?;

        let mut deleted_empty = 0i64;
        // Python can never reach the archive UPDATE (the comparison above it
        // raises first), so this stays 0 on the non-raising path.
        let archived_old = 0i64;
        let mut total_checked = 0i64;

        // cutoff_date = datetime.now(timezone.utc) - timedelta(days=auto_archive_days).
        // Computed exactly as in Python (so `auto_archive_days` is consumed), but
        // the naive<aware comparison that would use it raises before it is read.
        let _cutoff_date =
            pydatetime::naive_to_iso(pydatetime::utcnow_naive() - Duration::days(auto_archive_days));

        let mut crashed = false;
        let result = (|| -> rusqlite::Result<()> {
            let tx = conn.transaction()?;
            // all_sessions = db.query(DbSession).all()
            let rows: Vec<(String, bool, Option<String>, i64)> = {
                let mut stmt =
                    tx.prepare("SELECT id, archived, last_accessed, message_count FROM sessions")?;
                let it = stmt.query_map([], |r| {
                    Ok((
                        r.get::<_, String>(0)?,
                        r.get::<_, Option<bool>>(1)?.unwrap_or(false),
                        r.get::<_, Option<String>>(2)?,
                        r.get::<_, Option<i64>>(3)?.unwrap_or(0),
                    ))
                })?;
                it.filter_map(|r| r.ok()).collect()
            };

            for (id, archived, last_accessed, message_count) in rows {
                total_checked += 1;
                if message_count == 0 {
                    // Delete empty sessions (+ drop from cache). The cache removal
                    // is a plain map op, NOT undone by a later rollback (as in
                    // Python: `del self.sessions[id]` survives `db.rollback()`).
                    self.sessions.lock().unwrap().shift_remove(&id);
                    tx.execute("DELETE FROM sessions WHERE id = ?1", [&id])?;
                    deleted_empty += 1;
                } else if !archived && last_accessed.is_some() {
                    // Python evaluates `db_session.last_accessed < cutoff_date`
                    // HERE (naive < aware) and raises TypeError. Reproduce it:
                    // abort the transaction (drop `tx` without commit -> rollback)
                    // and surface the error. `is_important` and the `< cutoff`
                    // result are never reached in the Python either.
                    crashed = true;
                    return Ok(());
                }
                // else: archived OR last_accessed is NULL -> skip (no crash, no
                // archive — the only outcomes Python reaches without raising).
            }
            tx.commit() // no crash -> commit the empty-session deletes
        })();

        match result {
            Ok(()) if crashed => {
                // `except Exception as e: logger.error(f"Cleanup error: {e}"); db.rollback(); raise`
                let msg = "can't compare offset-naive and offset-aware datetimes";
                logger::error(&format!("Cleanup error: {msg}"));
                Err(PyError::other(msg))
            }
            Ok(()) => {
                logger::info(&format!(
                    "Cleanup: {deleted_empty} deleted, {archived_old} archived"
                ));
                Ok(serde_json::json!({
                    "deleted_empty": deleted_empty,
                    "archived_old": archived_old,
                    "total_checked": total_checked,
                }))
            }
            Err(e) => {
                logger::error(&format!("Cleanup error: {e}"));
                Err(db_err(e))
            }
        }
    }
}

/// `SessionManager._persist_message` reached through `models`' module global
/// (`models.Session.add_message` -> `_session_manager._persist_message`). The
/// trait gives us `&ChatMessage`, so the `_db_id` write-back to the caller's
/// object is the documented ownership deviation; the DB insert + counter update
/// happen exactly as in Python.
impl SessionPersistence for SessionManager {
    fn _persist_message(&self, session_id: &str, message: &ChatMessage) {
        let _ = self.persist_message_db(session_id, message);
    }

    fn replace_messages(&self, session_id: &str, messages: Vec<ChatMessage>) -> bool {
        // Forward to the inherent method (UFCS to disambiguate from this trait
        // method). Its `Err` (no DB) / `Ok(false)` (unknown session) collapse to
        // `false`, so the compactor falls back to a local-only history update.
        matches!(
            SessionManager::replace_messages(self, session_id, messages),
            Ok(true)
        )
    }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/// `database::Session` lookup by id, exposed for `_load_session_from_db` (kept a
/// free fn so `database.rs`'s `query_by_id` can stay private).
impl database::Session {
    fn from_id(
        conn: &rusqlite::Connection,
        session_id: &str,
    ) -> rusqlite::Result<Option<database::Session>> {
        conn.query_row(
            &format!(
                "SELECT {} FROM sessions WHERE id = ?1",
                database::Session::SELECT_COLS
            ),
            [session_id],
            database::Session::from_row,
        )
        .optional()
    }
}

/// `meta_data=json.dumps(message.metadata) if message.metadata else None` — an
/// empty (or absent) metadata dict is falsy, so it stores `NULL`.
fn serialize_meta(metadata: &Option<Map<String, Value>>) -> Option<String> {
    match metadata {
        Some(m) if !m.is_empty() => Some(Value::Object(m.clone()).to_string()),
        _ => None,
    }
}

/// `meta = json.loads(meta_data) if meta_data else {}; if meta is None: meta = {}`.
fn parse_meta(meta_data: &Option<String>) -> PyResult<Map<String, Value>> {
    let text = match meta_data {
        Some(t) if !t.is_empty() => t,
        _ => return Ok(Map::new()),
    };
    match serde_json::from_str::<Value>(text)? {
        Value::Object(m) => Ok(m),
        // `json.loads("null")` -> None -> `{}`; any non-dict scalar is treated as
        // empty (a list/int can't arise from `json.dumps` of a metadata dict).
        _ => Ok(Map::new()),
    }
}

/// A rusqlite error on a path where the Python would let the exception propagate
/// (`get_db_session` re-raises) — surfaced as a generic `PyError`.
fn db_err(e: rusqlite::Error) -> PyError {
    PyError::other(e.to_string())
}
