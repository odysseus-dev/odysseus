// src/vector_store.rs  <- src/chroma_client.py (the ChromaDB collection facade)
//! DUAL BACKEND: the default ChromaDB->SQLite swap, PLUS an optional real
//! ChromaDB HTTP backend.
//!
//! The Python's `src/chroma_client.py` is a thin singleton wrapper around the
//! external `chromadb` HTTP service; `rag_vector.py` and `memory_vector.py` then
//! drive a *collection object* whose surface is:
//!
//!   * `client.get_or_create_collection(name=..., metadata={"hnsw:space":"cosine"})`
//!   * `collection.count()`
//!   * `collection.get(ids=[...])` / `collection.get(where=..., include=[...])`
//!   * `collection.add(ids=, embeddings=, documents=, metadatas=)`
//!   * `collection.query(query_embeddings=, n_results=, where=, include=)`
//!   * `collection.delete(ids=...)`
//!   * `client.delete_collection(name)`
//!
//! ## Two backends behind ONE public type
//!
//! `VectorCollection` is the abstraction the whole vector cohort
//! (`rag_vector` / `memory_vector` / `tool_index`) builds on. It is an **enum**
//! with two variants that share the SAME constructor and method set:
//!
//!   * [`VectorCollection::Sqlite`] — the DELIBERATE ChromaDB->SQLite swap (the
//!     DEFAULT). It keeps the entire historical implementation byte-for-byte: a
//!     `collection` discriminator on the shared `vectors` table + brute-force
//!     in-Rust cosine. With NO relevant env set, this is the only path that runs,
//!     so the default behaviour is unchanged.
//!   * [`VectorCollection::ChromaHttp`] — a REAL ChromaDB server reached over the
//!     v2 REST API with `reqwest::blocking`. Selected when the operator opts in
//!     (see [`select_backend`]); it implements the identical collection surface
//!     against the live container.
//!
//! Which variant `VectorCollection::get_or_create` (and the free
//! [`delete_collection`]) construct is decided by [`select_backend`], so EVERY
//! call site — the direct `VectorCollection::get_or_create` callers in
//! `rag_vector`/`memory_vector` AND the `chroma_client` facade — is covered with
//! zero edits upstream.
//!
//! ### The SQLite swap (default) — documented faithful deviations
//!
//!   * The `metadata={"hnsw:space": "cosine"}` collection metadata is ignored:
//!     cosine is the only metric, and (because the `EmbeddingClient` L2-normalizes
//!     every vector) cosine_similarity == dot product, so the ChromaDB distance
//!     `1 - cosine_similarity` is computed here as `1.0 - dot`.
//!   * Embeddings are stored as a little-endian `f32` BLOB (compact; the same
//!     L2-normalized floats ChromaDB stores).
//!   * `PRIMARY KEY(collection, id)` reproduces ChromaDB's per-collection unique
//!     ids; `add()` is therefore `INSERT OR REPLACE` (ChromaDB upsert semantics —
//!     re-adding the same id overwrites, matching ChromaDB `add`).
//!   * Results are returned column-oriented and **nested once** —
//!     `{ids:[[..]], distances:[[..]], documents:[[..]], metadatas:[[..]]}` — so
//!     the Python callers' `results["ids"][0][idx]` indexing ports 1:1. Both
//!     `query` and `get` are modelled this way (ChromaDB's `get` is NOT nested,
//!     but the callers only ever read `results["ids"]` truthiness / iterate the
//!     flat top level on `get`, so we expose `GetResult` flat and `QueryResult`
//!     nested to match each call site exactly — see the two structs below).
//!   * The `where` filter is taken verbatim as the ChromaDB filter dict
//!     (`serde_json::Value`): a `{field: scalar}` clause is metadata equality, a
//!     `{field: {"$contains": substr}}` clause is a metadata substring test.
//!     These are exactly the clauses the two callers build
//!     (`{"owner": ..}`, `{"source": ..}`, `{"directory": ..}`,
//!     `{"source": {"$contains": ..}}`).
//!
//! ### The ChromaHttp backend — v2 REST API
//!
//! Reaches a real Chroma container over `http://{CHROMADB_HOST}:{CHROMADB_PORT}`
//! (same vars as Python's `chroma_client.py`: host default `localhost`, port
//! default `8100`). The Chroma v2 API is tenant/database-scoped under `/api/v2`;
//! all collection ops are keyed by the collection UUID (not the name), so the
//! variant resolves the id once via create-or-get and caches it. `add()` targets
//! `/upsert` so it matches the `INSERT OR REPLACE` semantics `tool_index` relies
//! on. The `$contains`-on-metadata `where` clause is not a canonical Chroma
//! metadata operator, so `get_where` falls back to fetching candidates and
//! substring-filtering in Rust with the SAME `metadata_matches` logic the SQLite
//! backend uses — identical observable result. The query distance is Chroma's
//! cosine distance (`1 - cosine_similarity`, collection created with
//! `hnsw:space=cosine`), identical to the SQLite `1.0 - dot`.
//!
//! Non-2xx responses become `PyError::other("chroma <route> failed: <status>
//! <body>")`, mirroring the SQLite error wrapping; the callers already
//! log+swallow these into degraded/empty results, so a transient Chroma error
//! never panics.
//!
//! Feature gate: `web` (it pulls in the `db` rusqlite layer for the SQLite
//! variant + `reqwest::blocking` for the HTTP variant; declared web so it sits
//! with the embedding HTTP client + the vector RAG/memory cohort).

use once_cell::sync::{Lazy, OnceCell};
use rusqlite::OptionalExtension;
use serde_json::{json, Map, Value};
use std::sync::Mutex;

use crate::core::database::session_local;
use crate::error::{PyError, PyResult};

// ---------------------------------------------------------------------------
// Result shapes
// ---------------------------------------------------------------------------

/// ChromaDB `collection.query(...)` result — column-oriented, **nested once**
/// per the query API (`results["ids"][0][idx]`). Each top-level vector holds one
/// inner list per query embedding; the callers always pass a single query
/// embedding, so there is exactly one inner list (index `[0]`).
#[derive(Debug, Clone, Default)]
pub struct QueryResult {
    pub ids: Vec<Vec<String>>,
    pub distances: Vec<Vec<f64>>,
    pub documents: Vec<Vec<String>>,
    pub metadatas: Vec<Vec<Value>>,
}

impl QueryResult {
    /// An empty single-query result: every column is `[[]]` (one empty inner
    /// list), so `result.ids[0]` is a valid empty `Vec` the callers can iterate
    /// / test for truthiness exactly like ChromaDB's `results["ids"][0]`.
    fn empty_single() -> Self {
        QueryResult {
            ids: vec![Vec::new()],
            distances: vec![Vec::new()],
            documents: vec![Vec::new()],
            metadatas: vec![Vec::new()],
        }
    }
}

/// ChromaDB `collection.get(...)` result — column-oriented, **flat** (`get`
/// returns un-nested lists; the callers read `results["ids"]` / index it
/// positionally with the same `i`). `metadatas`/`documents` are populated even
/// when the Python passed `include=[]` (the callers that pass `include=[]` only
/// read `ids`, so the extra columns are harmless).
#[derive(Debug, Clone, Default)]
pub struct GetResult {
    pub ids: Vec<String>,
    pub documents: Vec<String>,
    pub metadatas: Vec<Value>,
}

// ---------------------------------------------------------------------------
// Embedding BLOB codec (little-endian f32)
// ---------------------------------------------------------------------------

/// Pack an embedding into the little-endian `f32` BLOB stored in `vectors.embedding`.
fn embedding_to_blob(vec: &[f32]) -> Vec<u8> {
    let mut out = Vec::with_capacity(vec.len() * 4);
    for f in vec {
        out.extend_from_slice(&f.to_le_bytes());
    }
    out
}

/// Decode a little-endian `f32` BLOB back into an embedding. A trailing partial
/// chunk (corrupt row) is ignored, mirroring "best effort" decode.
fn blob_to_embedding(blob: &[u8]) -> Vec<f32> {
    blob.chunks_exact(4)
        .map(|c| f32::from_le_bytes([c[0], c[1], c[2], c[3]]))
        .collect()
}

/// Dot product of two equal-length vectors (cosine similarity for L2-normalized
/// inputs). Length mismatch returns `0.0` (defensive; the embedding client
/// produces fixed-dim vectors).
fn dot(a: &[f32], b: &[f32]) -> f64 {
    if a.len() != b.len() {
        return 0.0;
    }
    let mut acc = 0.0_f64;
    for i in 0..a.len() {
        acc += a[i] as f64 * b[i] as f64;
    }
    acc
}

// ---------------------------------------------------------------------------
// `where` filter (the ChromaDB metadata filter dict)
// ---------------------------------------------------------------------------

/// Evaluate one ChromaDB-style `where` clause against a record's parsed metadata.
///
/// Supported clauses (exactly the ones `rag_vector` / `memory_vector` build):
///   * `{field: scalar}`              -> `metadata[field] == scalar`
///   * `{field: {"$contains": s}}`    -> `metadata[field]` is a string containing `s`
///
/// A `None` filter matches every record. A missing metadata key never matches an
/// equality/contains clause (ChromaDB excludes records lacking the field).
fn metadata_matches(meta: &Value, where_filter: Option<&Value>) -> bool {
    let filter = match where_filter {
        Some(Value::Object(m)) if !m.is_empty() => m,
        // None, or `{}`/non-object -> no constraint (match all).
        _ => return true,
    };
    let meta_obj = match meta.as_object() {
        Some(o) => o,
        None => return false,
    };
    for (field, clause) in filter {
        let actual = match meta_obj.get(field) {
            Some(v) => v,
            None => return false, // field absent -> excluded
        };
        match clause {
            // {"$contains": substr} substring test on a string field.
            Value::Object(op) if op.contains_key("$contains") => {
                let needle = op.get("$contains").and_then(|v| v.as_str());
                let haystack = actual.as_str();
                match (haystack, needle) {
                    (Some(h), Some(n)) => {
                        if !h.contains(n) {
                            return false;
                        }
                    }
                    _ => return false,
                }
            }
            // Scalar equality (string / number / bool).
            scalar => {
                if actual != scalar {
                    return false;
                }
            }
        }
    }
    true
}

// ---------------------------------------------------------------------------
// Backend selection (SQLite swap vs real ChromaDB HTTP)
// ---------------------------------------------------------------------------

/// Which storage backend a `VectorCollection` runs against.
///
/// Public so the `web::run` startup wiring can gate the docker auto-launch on
/// `matches!(select_backend(), Backend::ChromaHttp)`. (The variant constructors
/// stay private to this module via the selection funnel.)
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Backend {
    /// The default ChromaDB->SQLite swap (no relevant env set).
    Sqlite,
    /// A real ChromaDB server over the v2 REST API.
    ChromaHttp,
}

/// Cache the resolved backend so a stable choice is shared across calls within a
/// run. [`reset_backend_selection`] clears it so a config change (the embedding
/// routes' reset hook) re-resolves on the next op. Optional by design — clearing
/// it and re-reading env is always safe.
static BACKEND_CACHE: Lazy<Mutex<Option<Backend>>> = Lazy::new(|| Mutex::new(None));

/// Decide the backend, faithful to the no-env default + the task's selection
/// rule. Read on each `get_or_create` / `delete_collection` / `chroma_heartbeat`
/// (via the cache), so a `reset_client()`-driven config change is honored. Also
/// read once at startup by `web::run` to gate the docker auto-launch.
///
/// Logic:
///   * `ODYSSEUS_VECTOR_BACKEND` (lowercased/trimmed) == `chroma` -> ChromaHttp.
///   * `ODYSSEUS_VECTOR_BACKEND` == `sqlite` -> Sqlite (explicit escape hatch,
///     wins even if `CHROMADB_*` happen to be set).
///   * ELSE if `CHROMADB_HOST` OR `CHROMADB_PORT` is EXPLICITLY SET (presence,
///     not value) -> ChromaHttp. (Under docker-compose these are set, matching
///     the Python which always used the HTTP client.)
///   * ELSE -> Sqlite (the DEFAULT swap). With NO env set, behaviour is
///     byte-for-byte identical to today.
pub fn select_backend() -> Backend {
    {
        let cache = BACKEND_CACHE.lock().unwrap_or_else(|p| p.into_inner());
        if let Some(b) = *cache {
            return b;
        }
    }
    let resolved = resolve_backend_from_env();
    let mut cache = BACKEND_CACHE.lock().unwrap_or_else(|p| p.into_inner());
    *cache = Some(resolved);
    resolved
}

/// The pure env -> `Backend` decision (no cache touch), so the unit tests can
/// drive the logic directly with controlled env vars.
fn resolve_backend_from_env() -> Backend {
    if let Ok(raw) = std::env::var("ODYSSEUS_VECTOR_BACKEND") {
        match raw.trim().to_ascii_lowercase().as_str() {
            "chroma" => return Backend::ChromaHttp,
            "sqlite" => return Backend::Sqlite,
            _ => {} // unrecognized value -> fall through to the CHROMADB_* check
        }
    }
    if std::env::var("CHROMADB_HOST").is_ok() || std::env::var("CHROMADB_PORT").is_ok() {
        return Backend::ChromaHttp;
    }
    Backend::Sqlite
}

/// Clear the cached backend selection so the next op re-resolves from the
/// environment. Called by `chroma_client::reset_client()` (the embedding-routes
/// reset hook) after a config change. The per-`ChromaHttpCollection`
/// collection-id cache (an `OnceCell`) is dropped naturally when a fresh
/// `get_or_create` builds a new handle, so the next op re-resolves the id too.
pub fn reset_backend_selection() {
    let mut cache = BACKEND_CACHE.lock().unwrap_or_else(|p| p.into_inner());
    *cache = None;
}

/// `CHROMADB_HOST` (default `localhost`) — read exactly like Python's
/// `chroma_client.py`.
fn chroma_host() -> String {
    std::env::var("CHROMADB_HOST").unwrap_or_else(|_| "localhost".to_string())
}

/// `CHROMADB_PORT` (default `8100`) — read exactly like Python's
/// `chroma_client.py`. A non-numeric value falls back to the default (the Python
/// `int(...)` would raise; we degrade rather than panic).
fn chroma_port() -> u16 {
    std::env::var("CHROMADB_PORT")
        .ok()
        .and_then(|s| s.trim().parse::<u16>().ok())
        .unwrap_or(8100)
}

/// `base_url = http://{host}:{port}` (no `/api/v2` suffix; the routes append it).
fn chroma_base_url() -> String {
    format!("http://{}:{}", chroma_host(), chroma_port())
}

// ---------------------------------------------------------------------------
// Shared blocking HTTP client (ChromaHttp backend)
// ---------------------------------------------------------------------------

/// `CHROMADB_CONNECT_TIMEOUT` (default `2.0` seconds) — the short timeout used
/// for the TCP pre-probe and the HTTP client connect phase. Mirrors Python's
/// `_CONNECT_TIMEOUT = float(os.getenv("CHROMADB_CONNECT_TIMEOUT", "2.0"))`.
fn chroma_connect_timeout() -> std::time::Duration {
    let secs = std::env::var("CHROMADB_CONNECT_TIMEOUT")
        .ok()
        .and_then(|s| s.trim().parse::<f64>().ok())
        .unwrap_or(2.0)
        .max(0.0);
    std::time::Duration::from_secs_f64(secs)
}

/// Attempt a TCP connection to `host:port` within `timeout`. Returns `true` if
/// the connection succeeds, `false` on any OS error (refused, unreachable,
/// timeout). Mirrors Python's `_port_open(host, port, timeout)`.
///
/// `TcpStream::connect_timeout` requires a resolved `SocketAddr`; for hostnames
/// we resolve via `ToSocketAddrs` and probe the first address. A DNS failure is
/// treated as unreachable (returns `false`).
fn port_open(host: &str, port: u16, timeout: std::time::Duration) -> bool {
    use std::net::{TcpStream, ToSocketAddrs};
    let addr_str = format!("{host}:{port}");
    let sock_addr = match addr_str.to_socket_addrs() {
        Ok(mut it) => match it.next() {
            Some(a) => a,
            None => return false,
        },
        Err(_) => return false,
    };
    TcpStream::connect_timeout(&sock_addr, timeout).is_ok()
}

/// One process-wide `reqwest::blocking::Client` for every ChromaHttp op, with a
/// sane total timeout and the connect timeout matching the pre-probe. Built
/// lazily so the SQLite default never constructs it.
///
/// Note: `connect_timeout` here is a belt-and-suspenders guard for the HTTP
/// phase. The primary fast-fail is the explicit TCP pre-probe in
/// `collection_id()`, which calls `chroma_connect_timeout()` fresh on every
/// first-use so it always honors the current env value. The `CHROMA_HTTP`
/// client's connect timeout is frozen at first-use time (Lazy), but the
/// pre-probe fires BEFORE any HTTP request, so the two work together.
static CHROMA_HTTP: Lazy<reqwest::blocking::Client> = Lazy::new(|| {
    reqwest::blocking::Client::builder()
        .connect_timeout(chroma_connect_timeout())
        .timeout(std::time::Duration::from_secs(30))
        .build()
        .unwrap_or_else(|_| reqwest::blocking::Client::new())
});

/// The v2 tenant / database the collection routes are scoped under (defaults that
/// recent `chromadb/chroma:latest` images auto-create).
const CHROMA_TENANT: &str = "default_tenant";
const CHROMA_DATABASE: &str = "default_database";

/// `BASE/api/v2/tenants/{tenant}/databases/{db}/collections` — the scoped
/// collection root (`COLL` in the API map).
fn chroma_collections_url(base_url: &str) -> String {
    format!("{base_url}/api/v2/tenants/{CHROMA_TENANT}/databases/{CHROMA_DATABASE}/collections")
}

// ---------------------------------------------------------------------------
// VectorCollection — the public abstraction over both backends
// ---------------------------------------------------------------------------

/// A handle to one logical ChromaDB collection. The public type the entire
/// vector cohort holds; an enum so the SQLite swap and the real ChromaDB HTTP
/// backend coexist behind one name + one method set. Cheap to clone.
#[derive(Debug, Clone)]
pub enum VectorCollection {
    /// The default ChromaDB->SQLite swap.
    Sqlite(SqliteCollection),
    /// A real ChromaDB server over the v2 REST API.
    ChromaHttp(ChromaHttpCollection),
}

impl VectorCollection {
    /// `client.get_or_create_collection(name=..., metadata={"hnsw:space":"cosine"})`.
    ///
    /// THE SELECTION POINT: reads [`select_backend`] once and constructs the
    /// matching variant. Because `rag_vector`/`memory_vector` call this
    /// constructor DIRECTLY and `tool_index` goes through the `chroma_client`
    /// facade (which calls the same constructor), routing selection here covers
    /// every call site with zero upstream edits.
    pub fn get_or_create(name: &str) -> PyResult<VectorCollection> {
        match select_backend() {
            Backend::Sqlite => Ok(VectorCollection::Sqlite(SqliteCollection::get_or_create(name)?)),
            Backend::ChromaHttp => Ok(VectorCollection::ChromaHttp(
                ChromaHttpCollection::get_or_create(name)?,
            )),
        }
    }

    /// The collection name.
    pub fn name(&self) -> &str {
        match self {
            VectorCollection::Sqlite(c) => c.name(),
            VectorCollection::ChromaHttp(c) => c.name(),
        }
    }

    /// `collection.count()` — number of stored vectors in this collection.
    pub fn count(&self) -> PyResult<i64> {
        match self {
            VectorCollection::Sqlite(c) => c.count(),
            VectorCollection::ChromaHttp(c) => c.count(),
        }
    }

    /// `collection.get(ids=[...])` — fetch specific ids from this collection.
    pub fn get_by_ids(&self, ids: &[String]) -> PyResult<GetResult> {
        match self {
            VectorCollection::Sqlite(c) => c.get_by_ids(ids),
            VectorCollection::ChromaHttp(c) => c.get_by_ids(ids),
        }
    }

    /// `collection.get(where=..., include=[...])` — fetch all records matching
    /// the `where` filter (or every record when `where` is `None`/`{}`).
    pub fn get_where(&self, where_filter: Option<&Value>) -> PyResult<GetResult> {
        match self {
            VectorCollection::Sqlite(c) => c.get_where(where_filter),
            VectorCollection::ChromaHttp(c) => c.get_where(where_filter),
        }
    }

    /// `collection.add(ids=, embeddings=, documents=, metadatas=)` — upsert a
    /// batch of records (ChromaDB upsert / `INSERT OR REPLACE` semantics).
    pub fn add(
        &self,
        ids: &[String],
        embeddings: &[Vec<f32>],
        documents: &[String],
        metadatas: &[Value],
    ) -> PyResult<()> {
        match self {
            VectorCollection::Sqlite(c) => c.add(ids, embeddings, documents, metadatas),
            VectorCollection::ChromaHttp(c) => c.add(ids, embeddings, documents, metadatas),
        }
    }

    /// `collection.query(query_embeddings=[vec], n_results=, where=, include=...)`.
    pub fn query(
        &self,
        query_vec: &[f32],
        n_results: i64,
        where_filter: Option<&Value>,
    ) -> PyResult<QueryResult> {
        match self {
            VectorCollection::Sqlite(c) => c.query(query_vec, n_results, where_filter),
            VectorCollection::ChromaHttp(c) => c.query(query_vec, n_results, where_filter),
        }
    }

    /// `collection.delete(ids=[...])` — remove specific ids; returns the count
    /// removed (best-effort under the HTTP backend — callers use it cosmetically).
    pub fn delete(&self, ids: &[String]) -> PyResult<i64> {
        match self {
            VectorCollection::Sqlite(c) => c.delete(ids),
            VectorCollection::ChromaHttp(c) => c.delete(ids),
        }
    }
}

// ---------------------------------------------------------------------------
// SqliteCollection — the ChromaDB->SQLite swap (DEFAULT, moved verbatim)
// ---------------------------------------------------------------------------

/// A handle to one logical ChromaDB collection (a `collection` discriminator on
/// the shared `vectors` table). Cheap to clone — it holds only the collection
/// name; every operation opens a fresh `session_local()` connection (matching the
/// per-call connection posture of the rest of the DB layer).
#[derive(Debug, Clone)]
pub struct SqliteCollection {
    name: String,
}

impl SqliteCollection {
    /// `client.get_or_create_collection(name=..., metadata={"hnsw:space":"cosine"})`.
    ///
    /// The table is created by `core::database::init_db` / `create_all`, so this
    /// is effectively a no-op handle constructor. We still run the idempotent
    /// `CREATE TABLE IF NOT EXISTS` so a `SqliteCollection` is usable even if the
    /// caller never called `init_db` (matching ChromaDB auto-creating on access).
    /// Raises (returns `Err`) only if the DB can't be opened, mirroring the
    /// Python's `get_chroma_client()` connection failure.
    pub fn get_or_create(name: &str) -> PyResult<SqliteCollection> {
        let conn = session_local()
            .map_err(|e| PyError::other(format!("vector store open failed: {e}")))?;
        conn.execute_batch(
            "CREATE TABLE IF NOT EXISTS vectors (\
                 collection VARCHAR NOT NULL, id VARCHAR NOT NULL, document TEXT, \
                 embedding BLOB, metadata TEXT, PRIMARY KEY (collection, id));\
             CREATE INDEX IF NOT EXISTS ix_vectors_collection ON vectors(collection);",
        )
        .map_err(|e| PyError::other(format!("vector store ensure failed: {e}")))?;
        Ok(SqliteCollection { name: name.to_string() })
    }

    /// The collection name (the `vectors.collection` discriminator).
    pub fn name(&self) -> &str {
        &self.name
    }

    /// `collection.count()` — number of stored vectors in this collection.
    pub fn count(&self) -> PyResult<i64> {
        let conn = session_local()
            .map_err(|e| PyError::other(format!("vector count open failed: {e}")))?;
        conn.query_row(
            "SELECT COUNT(*) FROM vectors WHERE collection = ?1",
            [&self.name],
            |r| r.get::<_, i64>(0),
        )
        .map_err(|e| PyError::other(format!("vector count failed: {e}")))
    }

    /// `collection.get(ids=[...])` — fetch specific ids from this collection.
    ///
    /// The result's `ids` truthiness is what the callers test
    /// (`if existing["ids"]:`), and they read `documents` / `metadatas`
    /// positionally. Order follows the supplied `ids` (only ids that exist are
    /// returned, matching ChromaDB skipping unknown ids).
    pub fn get_by_ids(&self, ids: &[String]) -> PyResult<GetResult> {
        let mut out = GetResult::default();
        if ids.is_empty() {
            return Ok(out);
        }
        let conn = session_local()
            .map_err(|e| PyError::other(format!("vector get open failed: {e}")))?;
        // Preserve the supplied id order: look each up individually (the call
        // sites pass 1 id, so this is not a hot path).
        let mut stmt = conn
            .prepare("SELECT document, metadata FROM vectors WHERE collection = ?1 AND id = ?2")
            .map_err(|e| PyError::other(format!("vector get prepare failed: {e}")))?;
        for id in ids {
            let row: Option<(Option<String>, Option<String>)> = stmt
                .query_row(rusqlite::params![&self.name, id], |r| {
                    Ok((r.get::<_, Option<String>>(0)?, r.get::<_, Option<String>>(1)?))
                })
                .optional()
                .map_err(|e| PyError::other(format!("vector get failed: {e}")))?;
            if let Some((doc, meta)) = row {
                out.ids.push(id.clone());
                out.documents.push(doc.unwrap_or_default());
                out.metadatas.push(parse_meta(meta));
            }
        }
        Ok(out)
    }

    /// `collection.get(where=..., include=[...])` — fetch all records in this
    /// collection that satisfy the `where` filter (or every record when `where`
    /// is `None`/`{}`). Used by `rag_vector._keyword_search_fallback` (no where),
    /// `remove_directory`, and `delete_by_source`.
    ///
    /// Insertion order is preserved via the table rowid (SQLite returns rows in
    /// rowid order absent an `ORDER BY`; we make it explicit for determinism).
    pub fn get_where(&self, where_filter: Option<&Value>) -> PyResult<GetResult> {
        let conn = session_local()
            .map_err(|e| PyError::other(format!("vector get_where open failed: {e}")))?;
        let mut stmt = conn
            .prepare(
                "SELECT id, document, metadata FROM vectors \
                 WHERE collection = ?1 ORDER BY rowid",
            )
            .map_err(|e| PyError::other(format!("vector get_where prepare failed: {e}")))?;
        let rows = stmt
            .query_map([&self.name], |r| {
                Ok((
                    r.get::<_, String>(0)?,
                    r.get::<_, Option<String>>(1)?,
                    r.get::<_, Option<String>>(2)?,
                ))
            })
            .map_err(|e| PyError::other(format!("vector get_where query failed: {e}")))?;

        let mut out = GetResult::default();
        for row in rows {
            let (id, doc, meta_raw) =
                row.map_err(|e| PyError::other(format!("vector get_where row failed: {e}")))?;
            let meta = parse_meta(meta_raw);
            if metadata_matches(&meta, where_filter) {
                out.ids.push(id);
                out.documents.push(doc.unwrap_or_default());
                out.metadatas.push(meta);
            }
        }
        Ok(out)
    }

    /// `collection.add(ids=, embeddings=, documents=, metadatas=)` — upsert a
    /// batch of records (`INSERT OR REPLACE`, matching ChromaDB's per-id
    /// overwrite). The four slices must be the same length (the callers always
    /// build them together); a length mismatch is a `ValueError`.
    pub fn add(
        &self,
        ids: &[String],
        embeddings: &[Vec<f32>],
        documents: &[String],
        metadatas: &[Value],
    ) -> PyResult<()> {
        let n = ids.len();
        if embeddings.len() != n || documents.len() != n || metadatas.len() != n {
            return Err(PyError::value(
                "vector add: ids/embeddings/documents/metadatas length mismatch",
            ));
        }
        if n == 0 {
            return Ok(());
        }
        let mut conn = session_local()
            .map_err(|e| PyError::other(format!("vector add open failed: {e}")))?;
        let tx = conn
            .transaction()
            .map_err(|e| PyError::other(format!("vector add tx failed: {e}")))?;
        {
            let mut stmt = tx
                .prepare(
                    "INSERT OR REPLACE INTO vectors \
                     (collection, id, document, embedding, metadata) \
                     VALUES (?1, ?2, ?3, ?4, ?5)",
                )
                .map_err(|e| PyError::other(format!("vector add prepare failed: {e}")))?;
            for i in 0..n {
                let blob = embedding_to_blob(&embeddings[i]);
                // metadata as compact JSON (None -> SQL NULL when the value is null).
                let meta_json = if metadatas[i].is_null() {
                    None
                } else {
                    Some(metadatas[i].to_string())
                };
                stmt.execute(rusqlite::params![
                    &self.name,
                    &ids[i],
                    &documents[i],
                    blob,
                    meta_json
                ])
                .map_err(|e| PyError::other(format!("vector add insert failed: {e}")))?;
            }
        }
        tx.commit()
            .map_err(|e| PyError::other(format!("vector add commit failed: {e}")))?;
        Ok(())
    }

    /// `collection.query(query_embeddings=[vec], n_results=, where=, include=...)`.
    ///
    /// Brute-force cosine over every record in the collection that passes the
    /// `where` filter: distance = `1.0 - dot(query, row)` (inputs are
    /// L2-normalized, so `dot == cosine_similarity`). Results are sorted ASC by
    /// distance (most similar first, matching ChromaDB) and truncated to
    /// `n_results`. Returns the nested-once shape so `results["ids"][0][idx]`
    /// ports verbatim.
    pub fn query(
        &self,
        query_vec: &[f32],
        n_results: i64,
        where_filter: Option<&Value>,
    ) -> PyResult<QueryResult> {
        if n_results <= 0 {
            return Ok(QueryResult::empty_single());
        }
        let conn = session_local()
            .map_err(|e| PyError::other(format!("vector query open failed: {e}")))?;
        let mut stmt = conn
            .prepare(
                "SELECT id, document, embedding, metadata FROM vectors \
                 WHERE collection = ?1 ORDER BY rowid",
            )
            .map_err(|e| PyError::other(format!("vector query prepare failed: {e}")))?;
        let rows = stmt
            .query_map([&self.name], |r| {
                Ok((
                    r.get::<_, String>(0)?,
                    r.get::<_, Option<String>>(1)?,
                    r.get::<_, Option<Vec<u8>>>(2)?,
                    r.get::<_, Option<String>>(3)?,
                ))
            })
            .map_err(|e| PyError::other(format!("vector query failed: {e}")))?;

        // Candidate tuples: (distance, id, document, metadata).
        let mut scored: Vec<(f64, String, String, Value)> = Vec::new();
        for row in rows {
            let (id, doc, blob, meta_raw) =
                row.map_err(|e| PyError::other(format!("vector query row failed: {e}")))?;
            let meta = parse_meta(meta_raw);
            if !metadata_matches(&meta, where_filter) {
                continue;
            }
            let emb = blob.map(|b| blob_to_embedding(&b)).unwrap_or_default();
            let distance = 1.0 - dot(query_vec, &emb);
            scored.push((distance, id, doc.unwrap_or_default(), meta));
        }

        // Sort ASC by distance. `total_cmp` gives a total order over f64 (NaN-safe)
        // and is stable for equal distances, preserving insertion order on ties.
        scored.sort_by(|a, b| a.0.total_cmp(&b.0));
        scored.truncate(n_results as usize);

        let mut ids = Vec::with_capacity(scored.len());
        let mut distances = Vec::with_capacity(scored.len());
        let mut documents = Vec::with_capacity(scored.len());
        let mut metadatas = Vec::with_capacity(scored.len());
        for (dist, id, doc, meta) in scored {
            ids.push(id);
            distances.push(dist);
            documents.push(doc);
            metadatas.push(meta);
        }
        Ok(QueryResult {
            ids: vec![ids],
            distances: vec![distances],
            documents: vec![documents],
            metadatas: vec![metadatas],
        })
    }

    /// `collection.delete(ids=[...])` — remove specific ids from this collection.
    /// Returns the number of rows removed.
    pub fn delete(&self, ids: &[String]) -> PyResult<i64> {
        if ids.is_empty() {
            return Ok(0);
        }
        let mut conn = session_local()
            .map_err(|e| PyError::other(format!("vector delete open failed: {e}")))?;
        let tx = conn
            .transaction()
            .map_err(|e| PyError::other(format!("vector delete tx failed: {e}")))?;
        let mut removed = 0_i64;
        {
            let mut stmt = tx
                .prepare("DELETE FROM vectors WHERE collection = ?1 AND id = ?2")
                .map_err(|e| PyError::other(format!("vector delete prepare failed: {e}")))?;
            for id in ids {
                removed += stmt
                    .execute(rusqlite::params![&self.name, id])
                    .map_err(|e| PyError::other(format!("vector delete failed: {e}")))?
                    as i64;
            }
        }
        tx.commit()
            .map_err(|e| PyError::other(format!("vector delete commit failed: {e}")))?;
        Ok(removed)
    }

    /// `client.delete_collection(name)` for the SQLite backend — drop every
    /// record belonging to this collection (the table is shared, so a scoped
    /// `DELETE`, not a `DROP TABLE`). Returns the number of rows removed.
    fn delete_collection(name: &str) -> PyResult<i64> {
        let conn = session_local()
            .map_err(|e| PyError::other(format!("delete_collection open failed: {e}")))?;
        let removed = conn
            .execute("DELETE FROM vectors WHERE collection = ?1", [name])
            .map_err(|e| PyError::other(format!("delete_collection failed: {e}")))?;
        Ok(removed as i64)
    }
}

// ---------------------------------------------------------------------------
// ChromaHttpCollection — a real ChromaDB server over the v2 REST API
// ---------------------------------------------------------------------------

/// A handle to one collection on a real ChromaDB server. Holds the base URL +
/// the collection name + the lazily-resolved collection UUID (every v2 op is
/// keyed by the UUID, not the name). Cheap to clone: the `OnceCell<String>` id
/// clones a (possibly-unresolved) cached value.
#[derive(Debug, Clone)]
pub struct ChromaHttpCollection {
    base_url: String,
    name: String,
    /// The collection UUID, resolved once via create-or-get and cached.
    id: OnceCell<String>,
}

impl ChromaHttpCollection {
    /// `client.get_or_create_collection(name=..., metadata={"hnsw:space":"cosine"})`.
    ///
    /// Builds the handle against the configured `base_url`; the UUID is resolved
    /// lazily on first use (so constructing a handle never blocks on the network
    /// — matching the SQLite handle being a cheap constructor). Returns `Err`
    /// only if the base URL cannot be formed (it always can), so this mirrors the
    /// SQLite constructor's "never fails on construction" posture.
    pub fn get_or_create(name: &str) -> PyResult<ChromaHttpCollection> {
        Ok(ChromaHttpCollection {
            base_url: chroma_base_url(),
            name: name.to_string(),
            id: OnceCell::new(),
        })
    }

    /// The collection name.
    pub fn name(&self) -> &str {
        &self.name
    }

    /// Resolve (and cache) the collection UUID via create-or-get.
    ///
    /// Before the first HTTP call, performs a fast TCP pre-probe (mirroring
    /// Python's `_port_open(host, port)` guard in `get_chroma_client()`). The
    /// probe uses `CHROMADB_CONNECT_TIMEOUT` (default 2 s) so an unreachable
    /// host fails in ~2 s with a descriptive error instead of blocking for the
    /// OS timeout (~30–60 s). The error message matches Python's format:
    /// `"ChromaDB is not reachable at {host}:{port}. Start the ChromaDB
    /// service … or set CHROMADB_HOST / CHROMADB_PORT …"`.
    ///
    /// `POST {COLL}` body `{"name":..,"metadata":{"hnsw:space":"cosine"},
    /// "get_or_create":true}` -> `{"id":"<uuid>",..}`. On the off chance the
    /// image does not honor `get_or_create` in the body, falls back to
    /// `GET {COLL}/{name}` to fetch an already-existing collection's id.
    fn collection_id(&self) -> PyResult<&str> {
        if let Some(id) = self.id.get() {
            return Ok(id.as_str());
        }

        // TCP pre-probe: fail fast if the host is unreachable.
        let host = chroma_host();
        let port = chroma_port();
        let timeout = chroma_connect_timeout();
        if !port_open(&host, port, timeout) {
            return Err(PyError::other(format!(
                "ChromaDB is not reachable at {host}:{port}. Start the ChromaDB \
                 service (e.g. `docker compose up chromadb`) or set CHROMADB_HOST / \
                 CHROMADB_PORT to point at a running instance."
            )));
        }

        let coll = chroma_collections_url(&self.base_url);
        let body = json!({
            "name": self.name,
            "metadata": {"hnsw:space": "cosine"},
            "get_or_create": true,
        });
        let resp = CHROMA_HTTP
            .post(&coll)
            .json(&body)
            .send()
            .map_err(|e| PyError::other(format!("chroma create collection failed: {e}")));

        let resolved: PyResult<String> = match resp {
            Ok(r) if r.status().is_success() => {
                parse_collection_id(&read_json(r, "chroma create collection")?)
            }
            Ok(r) => {
                // Fall back to GET by name (older images, or get_or_create not
                // honored in the body).
                let status = r.status();
                let _ = r.text();
                self.fetch_existing_id(&coll).map_err(|e| {
                    PyError::other(format!(
                        "chroma create collection failed: {status}; get-by-name fallback: {e}"
                    ))
                })
            }
            Err(e) => Err(e),
        };

        let id = resolved?;
        // `set` only fails if another thread won the race; either way the cell
        // now holds a valid id, so re-read it.
        let _ = self.id.set(id);
        Ok(self.id.get().expect("id just set").as_str())
    }

    /// `GET {COLL}/{name}` -> the existing collection's UUID (the create
    /// fallback path).
    fn fetch_existing_id(&self, coll: &str) -> PyResult<String> {
        let url = format!("{coll}/{}", self.name);
        let r = CHROMA_HTTP
            .get(&url)
            .send()
            .map_err(|e| PyError::other(format!("chroma get collection failed: {e}")))?;
        if !r.status().is_success() {
            let status = r.status();
            let text = r.text().unwrap_or_default();
            return Err(PyError::other(format!(
                "chroma get collection failed: {status} {text}"
            )));
        }
        parse_collection_id(&read_json(r, "chroma get collection")?)
    }

    /// `collection.count()` -> `GET {COLL}/{id}/count` -> bare integer body.
    pub fn count(&self) -> PyResult<i64> {
        let id = self.collection_id()?;
        let url = format!("{}/{id}/count", chroma_collections_url(&self.base_url));
        let r = CHROMA_HTTP
            .get(&url)
            .send()
            .map_err(|e| PyError::other(format!("chroma count failed: {e}")))?;
        let val = read_json(check_ok(r, "chroma count")?, "chroma count")?;
        val.as_i64()
            .ok_or_else(|| PyError::other(format!("chroma count: non-integer body: {val}")))
    }

    /// `collection.get(ids=[...])` -> `POST {COLL}/{id}/get` body
    /// `{"ids":[...],"include":["documents","metadatas"]}` -> flat `GetResult`.
    /// Preserves the requested-id order; only present ids are returned.
    pub fn get_by_ids(&self, ids: &[String]) -> PyResult<GetResult> {
        if ids.is_empty() {
            return Ok(GetResult::default());
        }
        let body = json!({
            "ids": ids,
            "include": ["documents", "metadatas"],
        });
        let raw = self.post_get(&body)?;
        // Chroma returns matching ids in its own order; re-order to the supplied
        // id order (and drop unknown ids) to match the SQLite backend exactly.
        Ok(reorder_get_result(raw, ids))
    }

    /// `collection.get(where=..., include=[...])` -> `POST {COLL}/{id}/get`.
    ///
    /// `where=None` omits the key (fetch all). For a `{field:{"$contains":s}}`
    /// clause — which is NOT a canonical Chroma metadata `where` operator — this
    /// fetches all candidates and substring-filters in Rust via the SAME
    /// `metadata_matches` logic the SQLite backend uses, so the observable result
    /// is identical. Plain `{field: scalar}` equality clauses are passed verbatim
    /// to Chroma (a valid `where` clause), but we ALSO re-filter in Rust so the
    /// result is correct regardless of which clauses the server honored.
    pub fn get_where(&self, where_filter: Option<&Value>) -> PyResult<GetResult> {
        // Only send Chroma a `where` it can evaluate; any `$contains` clause is
        // dropped from the wire filter and applied in Rust instead.
        let wire_filter = sanitize_where_for_chroma(where_filter);
        let mut body = Map::new();
        if let Some(w) = wire_filter {
            body.insert("where".to_string(), w);
        }
        body.insert("include".to_string(), json!(["documents", "metadatas"]));
        let raw = self.post_get(&Value::Object(body))?;
        // Re-apply the FULL original filter in Rust (covers `$contains` and is a
        // safe no-op for clauses Chroma already honored).
        Ok(filter_get_result(raw, where_filter))
    }

    /// Shared `POST {COLL}/{id}/get` -> flat `GetResult` parse.
    fn post_get(&self, body: &Value) -> PyResult<GetResult> {
        let id = self.collection_id()?;
        let url = format!("{}/{id}/get", chroma_collections_url(&self.base_url));
        let r = CHROMA_HTTP
            .post(&url)
            .json(body)
            .send()
            .map_err(|e| PyError::other(format!("chroma get failed: {e}")))?;
        let val = read_json(check_ok(r, "chroma get")?, "chroma get")?;
        Ok(parse_get_result(&val))
    }

    /// `collection.add(ids=, embeddings=, documents=, metadatas=)`.
    ///
    /// Targets `POST {COLL}/{id}/upsert` so it matches the documented
    /// `VectorCollection::add == INSERT OR REPLACE == ChromaDB upsert` contract
    /// `tool_index` relies on (re-adding an id overwrites). Embeddings are sent as
    /// plain JSON float arrays (the L2-normalized vectors the embedding client
    /// produces). `null` metadatas are sent as `{}` (Chroma rejects null metadata
    /// entries; the SQLite backend stores SQL NULL which `parse_meta` reads back
    /// as `{}`, so `{}` is the faithful equivalent).
    pub fn add(
        &self,
        ids: &[String],
        embeddings: &[Vec<f32>],
        documents: &[String],
        metadatas: &[Value],
    ) -> PyResult<()> {
        let n = ids.len();
        if embeddings.len() != n || documents.len() != n || metadatas.len() != n {
            return Err(PyError::value(
                "vector add: ids/embeddings/documents/metadatas length mismatch",
            ));
        }
        if n == 0 {
            return Ok(());
        }
        let id = self.collection_id()?;
        let metas: Vec<Value> = metadatas
            .iter()
            .map(|m| if m.is_null() { json!({}) } else { m.clone() })
            .collect();
        let body = json!({
            "ids": ids,
            "embeddings": embeddings,
            "documents": documents,
            "metadatas": metas,
        });
        let url = format!("{}/{id}/upsert", chroma_collections_url(&self.base_url));
        let r = CHROMA_HTTP
            .post(&url)
            .json(&body)
            .send()
            .map_err(|e| PyError::other(format!("chroma upsert failed: {e}")))?;
        check_ok(r, "chroma upsert")?;
        Ok(())
    }

    /// `collection.query(query_embeddings=[vec], n_results=, where=, include=...)`.
    ///
    /// `POST {COLL}/{id}/query` -> NESTED-ONCE `{ids:[[..]],distances:[[..]],
    /// documents:[[..]],metadatas:[[..]]}` mapping 1:1 to `QueryResult`. The
    /// returned cosine distance (`1 - cosine_similarity`, `hnsw:space=cosine`) is
    /// identical to the SQLite `1.0 - dot`. As with `get_where`, a `$contains`
    /// clause is dropped from the wire filter; a query result cannot be
    /// post-filtered without re-running the search, but the live callers
    /// (`rag_vector`/`memory_vector`/`tool_index`) only ever pass scalar-equality
    /// `where` (or `None`) to `query`, so that branch is not reached in practice.
    pub fn query(
        &self,
        query_vec: &[f32],
        n_results: i64,
        where_filter: Option<&Value>,
    ) -> PyResult<QueryResult> {
        if n_results <= 0 {
            return Ok(QueryResult::empty_single());
        }
        let id = self.collection_id()?;
        let mut body = Map::new();
        body.insert("query_embeddings".to_string(), json!([query_vec]));
        body.insert("n_results".to_string(), json!(n_results));
        if let Some(w) = sanitize_where_for_chroma(where_filter) {
            body.insert("where".to_string(), w);
        }
        body.insert(
            "include".to_string(),
            json!(["documents", "metadatas", "distances"]),
        );
        let url = format!("{}/{id}/query", chroma_collections_url(&self.base_url));
        let r = CHROMA_HTTP
            .post(&url)
            .json(&Value::Object(body))
            .send()
            .map_err(|e| PyError::other(format!("chroma query failed: {e}")))?;
        let val = read_json(check_ok(r, "chroma query")?, "chroma query")?;
        Ok(parse_query_result(&val))
    }

    /// `collection.delete(ids=[...])` -> `POST {COLL}/{id}/delete` body
    /// `{"ids":[...]}`. Chroma returns null/empty; we return `ids.len()` as a
    /// best-effort count (callers use the count cosmetically / for logging).
    pub fn delete(&self, ids: &[String]) -> PyResult<i64> {
        if ids.is_empty() {
            return Ok(0);
        }
        let id = self.collection_id()?;
        let body = json!({ "ids": ids });
        let url = format!("{}/{id}/delete", chroma_collections_url(&self.base_url));
        let r = CHROMA_HTTP
            .post(&url)
            .json(&body)
            .send()
            .map_err(|e| PyError::other(format!("chroma delete failed: {e}")))?;
        check_ok(r, "chroma delete")?;
        Ok(ids.len() as i64)
    }

    /// `client.delete_collection(name)` for the ChromaHttp backend.
    ///
    /// `DELETE {COLL}/{name}` (v2 deletes by name). Returns an informational
    /// `i64` — the prior count when reachable, else `0`. The callers wrap this in
    /// `let _ =`, so a 404 on a missing collection is swallowed.
    fn delete_collection(name: &str) -> PyResult<i64> {
        let base = chroma_base_url();
        let coll = chroma_collections_url(&base);
        // Best-effort prior count (informational only). Resolve the id via a
        // throwaway handle; ignore failures (the count is cosmetic).
        let prior = {
            let handle = ChromaHttpCollection {
                base_url: base.clone(),
                name: name.to_string(),
                id: OnceCell::new(),
            };
            handle.count().unwrap_or(0)
        };
        let url = format!("{coll}/{name}");
        let r = CHROMA_HTTP
            .delete(&url)
            .send()
            .map_err(|e| PyError::other(format!("chroma delete_collection failed: {e}")))?;
        // A 404 (missing collection) is fine — the callers immediately recreate.
        if !r.status().is_success() && r.status().as_u16() != 404 {
            let status = r.status();
            let text = r.text().unwrap_or_default();
            return Err(PyError::other(format!(
                "chroma delete_collection failed: {status} {text}"
            )));
        }
        Ok(prior)
    }
}

/// `GET {base}/api/v2/heartbeat` -> `Ok(())` on 200. Shared by the free
/// `chroma_heartbeat` helper (the launcher's readiness poll lives in
/// `chroma_launch`).
fn chroma_heartbeat_at(base_url: &str) -> PyResult<()> {
    let url = format!("{base_url}/api/v2/heartbeat");
    let r = CHROMA_HTTP
        .get(&url)
        .send()
        .map_err(|e| PyError::other(format!("chroma heartbeat failed: {e}")))?;
    if r.status().is_success() {
        Ok(())
    } else {
        let status = r.status();
        let text = r.text().unwrap_or_default();
        Err(PyError::other(format!(
            "chroma heartbeat failed: {status} {text}"
        )))
    }
}

/// `get_chroma_client().heartbeat()` — a real best-effort ping when the
/// ChromaHttp backend is selected, a no-op (`Ok(())`) for SQLite. Called by
/// `chroma_client::ChromaClient::heartbeat`.
pub fn chroma_heartbeat() -> PyResult<()> {
    match select_backend() {
        Backend::Sqlite => Ok(()),
        Backend::ChromaHttp => chroma_heartbeat_at(&chroma_base_url()),
    }
}

// ---------------------------------------------------------------------------
// delete_collection — the free fn, selection-aware
// ---------------------------------------------------------------------------

/// `client.delete_collection(name)` — drop a collection. SELECTION-AWARE: it must
/// route to the same backend as `VectorCollection::get_or_create`, because
/// `rag_vector.rebuild_index` (rag_vector.rs:563) + `memory_vector.rebuild`
/// (memory_vector.rs:288) call this free fn DIRECTLY (not via the facade).
///
/// SQLite: a scoped `DELETE` over the shared `vectors` table. ChromaHttp:
/// `DELETE {COLL}/{name}`. Both return an informational row/prior count the
/// callers may ignore (they `let _ =` it).
pub fn delete_collection(name: &str) -> PyResult<i64> {
    match select_backend() {
        Backend::Sqlite => SqliteCollection::delete_collection(name),
        Backend::ChromaHttp => ChromaHttpCollection::delete_collection(name),
    }
}

// ---------------------------------------------------------------------------
// Chroma JSON <-> result-shape helpers (ChromaHttp backend)
// ---------------------------------------------------------------------------

/// Read a `reqwest::blocking::Response` body as JSON, wrapping decode errors.
fn read_json(r: reqwest::blocking::Response, route: &str) -> PyResult<Value> {
    r.json::<Value>()
        .map_err(|e| PyError::other(format!("{route} decode failed: {e}")))
}

/// Return the response if it is a 2xx, else a `PyError` carrying the status +
/// body (mirroring the SQLite backend's error wrapping).
fn check_ok(r: reqwest::blocking::Response, route: &str) -> PyResult<reqwest::blocking::Response> {
    if r.status().is_success() {
        Ok(r)
    } else {
        let status = r.status();
        let text = r.text().unwrap_or_default();
        Err(PyError::other(format!("{route} failed: {status} {text}")))
    }
}

/// Pull the `id` (collection UUID) out of a create/get-collection response body.
fn parse_collection_id(val: &Value) -> PyResult<String> {
    val.get("id")
        .and_then(|v| v.as_str())
        .map(|s| s.to_string())
        .ok_or_else(|| PyError::other(format!("chroma collection response missing id: {val}")))
}

/// Drop any `$contains` clause from a `where` filter before sending it to Chroma
/// (`$contains` is a `where_document` operator, not a metadata `where` operator).
/// The dropped clause is re-applied in Rust by `filter_get_result`. Returns
/// `None` when there is nothing left to send (so the caller omits the `where`
/// key entirely).
fn sanitize_where_for_chroma(where_filter: Option<&Value>) -> Option<Value> {
    let obj = match where_filter {
        Some(Value::Object(m)) if !m.is_empty() => m,
        _ => return None,
    };
    let mut out = Map::new();
    for (k, v) in obj {
        let is_contains = matches!(v, Value::Object(op) if op.contains_key("$contains"));
        if !is_contains {
            out.insert(k.clone(), v.clone());
        }
    }
    if out.is_empty() {
        None
    } else {
        Some(Value::Object(out))
    }
}

/// Parse a FLAT Chroma `get` response (`{"ids":[..],"documents":[..],
/// "metadatas":[..]}`) into a `GetResult`. Missing columns degrade to empty;
/// a `null` metadata entry becomes `{}` (matching the SQLite `parse_meta`).
fn parse_get_result(val: &Value) -> GetResult {
    let ids = string_list(val.get("ids"));
    let documents = string_list_padded(val.get("documents"), ids.len());
    let metadatas = meta_list_padded(val.get("metadatas"), ids.len());
    GetResult {
        ids,
        documents,
        metadatas,
    }
}

/// Re-order a `GetResult` to the supplied id order, dropping unknown ids — so the
/// ChromaHttp `get_by_ids` matches the SQLite backend's "follow supplied order,
/// skip unknown" behaviour exactly.
fn reorder_get_result(raw: GetResult, ids: &[String]) -> GetResult {
    use std::collections::HashMap;
    let mut by_id: HashMap<&str, usize> = HashMap::with_capacity(raw.ids.len());
    for (i, id) in raw.ids.iter().enumerate() {
        by_id.entry(id.as_str()).or_insert(i);
    }
    let mut out = GetResult::default();
    for want in ids {
        if let Some(&i) = by_id.get(want.as_str()) {
            out.ids.push(raw.ids[i].clone());
            out.documents.push(raw.documents.get(i).cloned().unwrap_or_default());
            out.metadatas
                .push(raw.metadatas.get(i).cloned().unwrap_or_else(|| json!({})));
        }
    }
    out
}

/// Re-apply a `where` filter to a `GetResult` in Rust (covers the `$contains`
/// fallback; a no-op for clauses Chroma already honored). Preserves the input
/// order. `None`/`{}` filter -> unchanged.
fn filter_get_result(raw: GetResult, where_filter: Option<&Value>) -> GetResult {
    match where_filter {
        Some(Value::Object(m)) if !m.is_empty() => {}
        _ => return raw,
    }
    let mut out = GetResult::default();
    for i in 0..raw.ids.len() {
        let meta = raw.metadatas.get(i).cloned().unwrap_or_else(|| json!({}));
        if metadata_matches(&meta, where_filter) {
            out.ids.push(raw.ids[i].clone());
            out.documents
                .push(raw.documents.get(i).cloned().unwrap_or_default());
            out.metadatas.push(meta);
        }
    }
    out
}

/// Parse a NESTED-ONCE Chroma `query` response into a `QueryResult`. An empty
/// result (`{"ids":[[]],...}`) maps to `QueryResult::empty_single()`-shaped data.
fn parse_query_result(val: &Value) -> QueryResult {
    let ids = nested_string_list(val.get("ids"));
    let n_groups = ids.len();
    let distances = nested_f64_list(val.get("distances"), &ids);
    let documents = nested_string_list_padded(val.get("documents"), &ids);
    let metadatas = nested_meta_list_padded(val.get("metadatas"), &ids);
    if n_groups == 0 {
        // Chroma always returns one inner list per query embedding; guard the
        // degenerate empty-body case to the empty-single shape the callers expect.
        return QueryResult::empty_single();
    }
    QueryResult {
        ids,
        distances,
        documents,
        metadatas,
    }
}

// --- small JSON-array readers -------------------------------------------------

/// `Value` -> `Vec<String>` (a flat JSON string array; nulls -> empty string).
fn string_list(v: Option<&Value>) -> Vec<String> {
    match v.and_then(|x| x.as_array()) {
        Some(arr) => arr
            .iter()
            .map(|e| e.as_str().map(|s| s.to_string()).unwrap_or_default())
            .collect(),
        None => Vec::new(),
    }
}

/// Like `string_list`, padded with empty strings to `len` (so document/metadata
/// columns line up with the id column even if the server omitted them).
fn string_list_padded(v: Option<&Value>, len: usize) -> Vec<String> {
    let mut out = string_list(v);
    if out.len() < len {
        out.resize(len, String::new());
    }
    out
}

/// `Value` -> `Vec<Value>` of metadata objects (null -> `{}`, padded to `len`).
fn meta_list_padded(v: Option<&Value>, len: usize) -> Vec<Value> {
    let mut out: Vec<Value> = match v.and_then(|x| x.as_array()) {
        Some(arr) => arr
            .iter()
            .map(|e| if e.is_null() { json!({}) } else { e.clone() })
            .collect(),
        None => Vec::new(),
    };
    if out.len() < len {
        out.resize(len, json!({}));
    }
    out
}

/// `Value` -> `Vec<Vec<String>>` (a nested-once string array).
fn nested_string_list(v: Option<&Value>) -> Vec<Vec<String>> {
    match v.and_then(|x| x.as_array()) {
        Some(outer) => outer.iter().map(|inner| string_list(Some(inner))).collect(),
        None => Vec::new(),
    }
}

/// `Value` -> `Vec<Vec<String>>`, each inner list padded to its `ids` group len.
fn nested_string_list_padded(v: Option<&Value>, ids: &[Vec<String>]) -> Vec<Vec<String>> {
    let mut groups = nested_string_list(v);
    groups.resize(ids.len(), Vec::new());
    for (i, g) in groups.iter_mut().enumerate() {
        let want = ids[i].len();
        if g.len() < want {
            g.resize(want, String::new());
        }
    }
    groups
}

/// `Value` -> `Vec<Vec<f64>>` distances, each inner list padded to its `ids`
/// group len (missing distance -> `0.0`).
fn nested_f64_list(v: Option<&Value>, ids: &[Vec<String>]) -> Vec<Vec<f64>> {
    let mut groups: Vec<Vec<f64>> = match v.and_then(|x| x.as_array()) {
        Some(outer) => outer
            .iter()
            .map(|inner| match inner.as_array() {
                Some(arr) => arr.iter().map(|e| e.as_f64().unwrap_or(0.0)).collect(),
                None => Vec::new(),
            })
            .collect(),
        None => Vec::new(),
    };
    groups.resize(ids.len(), Vec::new());
    for (i, g) in groups.iter_mut().enumerate() {
        let want = ids[i].len();
        if g.len() < want {
            g.resize(want, 0.0);
        }
    }
    groups
}

/// `Value` -> `Vec<Vec<Value>>` metadata objects (null -> `{}`), each inner list
/// padded to its `ids` group len.
fn nested_meta_list_padded(v: Option<&Value>, ids: &[Vec<String>]) -> Vec<Vec<Value>> {
    let mut groups: Vec<Vec<Value>> = match v.and_then(|x| x.as_array()) {
        Some(outer) => outer
            .iter()
            .map(|inner| match inner.as_array() {
                Some(arr) => arr
                    .iter()
                    .map(|e| if e.is_null() { json!({}) } else { e.clone() })
                    .collect(),
                None => Vec::new(),
            })
            .collect(),
        None => Vec::new(),
    };
    groups.resize(ids.len(), Vec::new());
    for (i, g) in groups.iter_mut().enumerate() {
        let want = ids[i].len();
        if g.len() < want {
            g.resize(want, json!({}));
        }
    }
    groups
}

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------

/// Parse a stored `vectors.metadata` JSON string into a `Value`. A NULL or
/// unparseable column yields an empty object `{}` (the metadata dict the callers
/// expect: `meta.get("owner")` on a missing/empty dict is just `None`).
fn parse_meta(raw: Option<String>) -> Value {
    match raw {
        Some(s) if !s.is_empty() => {
            serde_json::from_str(&s).unwrap_or_else(|_| Value::Object(Map::new()))
        }
        _ => Value::Object(Map::new()),
    }
}

/// Build a metadata `Value` from key/value string pairs (small convenience for
/// callers/tests; `rag_vector` / `memory_vector` pass `serde_json::Value`
/// objects directly). Kept tiny and inert — present so the JSON helper is in one
/// place. Currently only used by the unit tests below.
#[allow(dead_code)]
fn meta_from_pairs(pairs: &[(&str, &str)]) -> Value {
    let mut m = Map::new();
    for (k, v) in pairs {
        m.insert((*k).to_string(), json!(v));
    }
    Value::Object(m)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    /// ONE process-wide guard for EVERY test that touches the shared env vars
    /// (`DATABASE_URL`, `ODYSSEUS_VECTOR_BACKEND`, `CHROMADB_HOST`,
    /// `CHROMADB_PORT`) or the backend-selection cache. Both `with_temp_db` and
    /// `with_backend_env` lock it, so a backend-selection test never leaks
    /// `CHROMADB_HOST=chromadb` into a concurrently-running DB-backed test (which
    /// would otherwise pick the ChromaHttp backend and try to reach a real
    /// server). Recover from a poisoned lock (a prior test panic) so one failure
    /// doesn't cascade.
    use crate::core::database::DB_TEST_LOCK as ENV_GUARD;

    /// Point every connection at a fresh temp DB so the tests are hermetic and
    /// don't touch the real `data/app.db`. `core::database::db_path()` reads the
    /// process-global `DATABASE_URL`, so the whole body must be serialized via the
    /// shared [`ENV_GUARD`].
    ///
    /// It ALSO clears the backend-selection env vars + the cached selection so the
    /// SQLite default is in force for these DB-backed tests regardless of an
    /// operator's ambient `CHROMADB_*` / `ODYSSEUS_VECTOR_BACKEND`.
    fn with_temp_db<F: FnOnce()>(f: F) {
        // Hold the lock across the entire test body.
        let _held = ENV_GUARD.lock().unwrap_or_else(|p| p.into_inner());

        // Force the SQLite default for the DB-backed roundtrip tests.
        std::env::remove_var("ODYSSEUS_VECTOR_BACKEND");
        std::env::remove_var("CHROMADB_HOST");
        std::env::remove_var("CHROMADB_PORT");
        reset_backend_selection();

        let dir = std::env::temp_dir();
        let unique = format!(
            "odysseus_vecstore_{}_{}.db",
            std::process::id(),
            uuid::Uuid::new_v4().simple()
        );
        let path = dir.join(unique);
        std::env::set_var("DATABASE_URL", format!("sqlite:///{}", path.display()));
        // Ensure a clean slate.
        let _ = std::fs::remove_file(&path);
        f();
        let _ = std::fs::remove_file(&path);
        reset_backend_selection();
    }

    fn unit(x: f32, y: f32) -> Vec<f32> {
        // L2-normalize a 2-d vector (the embedding client normalizes; the store
        // assumes normalized input so cosine == dot).
        let n = (x * x + y * y).sqrt();
        if n == 0.0 {
            vec![0.0, 0.0]
        } else {
            vec![x / n, y / n]
        }
    }

    #[test]
    fn add_count_get_query_delete_roundtrip() {
        with_temp_db(|| {
            let c = VectorCollection::get_or_create("odysseus_rag").unwrap();
            assert!(matches!(c, VectorCollection::Sqlite(_)));
            assert_eq!(c.count().unwrap(), 0);

            let ids = vec!["a".to_string(), "b".to_string(), "c".to_string()];
            let embs = vec![unit(1.0, 0.0), unit(0.0, 1.0), unit(1.0, 1.0)];
            let docs = vec![
                "alpha east".to_string(),
                "beta north".to_string(),
                "gamma diag".to_string(),
            ];
            let metas = vec![
                json!({"owner": "u1", "source": "/docs/a.txt", "directory": "/docs"}),
                json!({"owner": "u2", "source": "/docs/b.txt", "directory": "/docs"}),
                json!({"owner": "u1", "source": "/other/c.txt", "directory": "/other"}),
            ];
            c.add(&ids, &embs, &docs, &metas).unwrap();
            assert_eq!(c.count().unwrap(), 3);

            // get_by_ids preserves supplied order and skips unknown ids.
            let g = c
                .get_by_ids(&["b".to_string(), "zzz".to_string(), "a".to_string()])
                .unwrap();
            assert_eq!(g.ids, vec!["b".to_string(), "a".to_string()]);
            assert_eq!(g.documents[0], "beta north");

            // query: nearest to the east-pointing vector is "a" (distance ~0).
            let q = c.query(&unit(1.0, 0.0), 2, None).unwrap();
            assert_eq!(q.ids.len(), 1); // nested once
            assert_eq!(q.ids[0][0], "a");
            assert!(q.distances[0][0].abs() < 1e-6);

            // delete one id.
            assert_eq!(c.delete(&["a".to_string()]).unwrap(), 1);
            assert_eq!(c.count().unwrap(), 2);
        });
    }

    #[test]
    fn where_filter_eq_and_contains() {
        with_temp_db(|| {
            let c = VectorCollection::get_or_create("odysseus_rag").unwrap();
            let ids = vec!["a".to_string(), "b".to_string(), "c".to_string()];
            let embs = vec![unit(1.0, 0.0), unit(0.0, 1.0), unit(1.0, 1.0)];
            let docs = vec!["a".to_string(), "b".to_string(), "c".to_string()];
            let metas = vec![
                json!({"owner": "u1", "source": "/docs/a.txt", "directory": "/docs"}),
                json!({"owner": "u2", "source": "/docs/b.txt", "directory": "/docs"}),
                json!({"owner": "u1", "source": "/other/c.txt", "directory": "/other"}),
            ];
            c.add(&ids, &embs, &docs, &metas).unwrap();

            // owner eq.
            let owner_filter = json!({"owner": "u1"});
            let q = c.query(&unit(1.0, 1.0), 10, Some(&owner_filter)).unwrap();
            let mut got: Vec<String> = q.ids[0].clone();
            got.sort();
            assert_eq!(got, vec!["a".to_string(), "c".to_string()]);

            // directory eq via get_where.
            let dir_filter = json!({"directory": "/docs"});
            let g = c.get_where(Some(&dir_filter)).unwrap();
            let mut got2 = g.ids.clone();
            got2.sort();
            assert_eq!(got2, vec!["a".to_string(), "b".to_string()]);

            // source $contains substring.
            let contains_filter = json!({"source": {"$contains": "/docs"}});
            let g2 = c.get_where(Some(&contains_filter)).unwrap();
            let mut got3 = g2.ids.clone();
            got3.sort();
            assert_eq!(got3, vec!["a".to_string(), "b".to_string()]);

            // None filter -> all rows.
            let g3 = c.get_where(None).unwrap();
            assert_eq!(g3.ids.len(), 3);
        });
    }

    #[test]
    fn add_is_upsert_and_delete_collection_scopes() {
        with_temp_db(|| {
            let rag = VectorCollection::get_or_create("odysseus_rag").unwrap();
            let mem = VectorCollection::get_or_create("odysseus_memories").unwrap();

            rag.add(
                &["x".to_string()],
                &[unit(1.0, 0.0)],
                &["v1".to_string()],
                &[json!({"source": "memory"})],
            )
            .unwrap();
            // Re-add same id overwrites (upsert).
            rag.add(
                &["x".to_string()],
                &[unit(0.0, 1.0)],
                &["v2".to_string()],
                &[json!({"source": "memory"})],
            )
            .unwrap();
            assert_eq!(rag.count().unwrap(), 1);
            let g = rag.get_by_ids(&["x".to_string()]).unwrap();
            assert_eq!(g.documents[0], "v2");

            mem.add(
                &["m1".to_string()],
                &[unit(1.0, 0.0)],
                &["mem".to_string()],
                &[json!({"source": "memory"})],
            )
            .unwrap();

            // delete_collection is scoped to one collection only.
            let removed = delete_collection("odysseus_rag").unwrap();
            assert_eq!(removed, 1);
            assert_eq!(rag.count().unwrap(), 0);
            assert_eq!(mem.count().unwrap(), 1);
        });
    }

    #[test]
    fn empty_query_returns_nested_empty() {
        with_temp_db(|| {
            let c = VectorCollection::get_or_create("odysseus_rag").unwrap();
            let q = c.query(&unit(1.0, 0.0), 5, None).unwrap();
            // Nested once: ids[0] is an (empty) inner list, so results["ids"][0]
            // is iterable just like ChromaDB.
            assert_eq!(q.ids.len(), 1);
            assert!(q.ids[0].is_empty());
            // n_results <= 0 also yields the empty single-query shape.
            let q0 = c.query(&unit(1.0, 0.0), 0, None).unwrap();
            assert_eq!(q0.ids.len(), 1);
            assert!(q0.ids[0].is_empty());
        });
    }

    #[test]
    fn meta_from_pairs_builds_object() {
        let m = meta_from_pairs(&[("owner", "u1"), ("source", "/a")]);
        assert_eq!(m.get("owner").and_then(|v| v.as_str()), Some("u1"));
        assert_eq!(m.get("source").and_then(|v| v.as_str()), Some("/a"));
    }

    // -- Backend selection logic (pure env; no live server) ------------------

    /// Backend-selection tests mutate process-global env vars, so they must be
    /// serialized against each other AND against `with_temp_db` via the shared
    /// [`ENV_GUARD`]; it also clears the resolved-selection cache so each case
    /// starts clean.
    fn with_backend_env<F: FnOnce()>(f: F) {
        let _held = ENV_GUARD.lock().unwrap_or_else(|p| p.into_inner());
        // Save + clear the relevant vars, restore on the way out.
        let save = |k: &str| std::env::var(k).ok();
        let (b, h, p, ct) = (
            save("ODYSSEUS_VECTOR_BACKEND"),
            save("CHROMADB_HOST"),
            save("CHROMADB_PORT"),
            save("CHROMADB_CONNECT_TIMEOUT"),
        );
        std::env::remove_var("ODYSSEUS_VECTOR_BACKEND");
        std::env::remove_var("CHROMADB_HOST");
        std::env::remove_var("CHROMADB_PORT");
        std::env::remove_var("CHROMADB_CONNECT_TIMEOUT");
        reset_backend_selection();
        f();
        let restore = |k: &str, v: Option<String>| match v {
            Some(val) => std::env::set_var(k, val),
            None => std::env::remove_var(k),
        };
        restore("ODYSSEUS_VECTOR_BACKEND", b);
        restore("CHROMADB_HOST", h);
        restore("CHROMADB_PORT", p);
        restore("CHROMADB_CONNECT_TIMEOUT", ct);
        reset_backend_selection();
    }

    #[test]
    fn select_backend_defaults_to_sqlite_with_no_env() {
        with_backend_env(|| {
            assert_eq!(resolve_backend_from_env(), Backend::Sqlite);
        });
    }

    #[test]
    fn select_backend_chroma_via_vector_backend_env() {
        with_backend_env(|| {
            std::env::set_var("ODYSSEUS_VECTOR_BACKEND", "chroma");
            assert_eq!(resolve_backend_from_env(), Backend::ChromaHttp);
            std::env::set_var("ODYSSEUS_VECTOR_BACKEND", "  CHROMA  ");
            assert_eq!(resolve_backend_from_env(), Backend::ChromaHttp);
        });
    }

    #[test]
    fn select_backend_chroma_via_explicit_host_or_port() {
        with_backend_env(|| {
            std::env::set_var("CHROMADB_HOST", "chromadb");
            assert_eq!(resolve_backend_from_env(), Backend::ChromaHttp);
            std::env::remove_var("CHROMADB_HOST");
            std::env::set_var("CHROMADB_PORT", "8100");
            assert_eq!(resolve_backend_from_env(), Backend::ChromaHttp);
        });
    }

    #[test]
    fn select_backend_sqlite_escape_hatch_wins() {
        with_backend_env(|| {
            // Explicit sqlite forces SQLite even if CHROMADB_* are set.
            std::env::set_var("ODYSSEUS_VECTOR_BACKEND", "sqlite");
            std::env::set_var("CHROMADB_HOST", "chromadb");
            std::env::set_var("CHROMADB_PORT", "8100");
            assert_eq!(resolve_backend_from_env(), Backend::Sqlite);
        });
    }

    #[test]
    fn select_backend_unknown_value_falls_through_to_host_check() {
        with_backend_env(|| {
            // An unrecognized ODYSSEUS_VECTOR_BACKEND value is ignored; selection
            // then depends on CHROMADB_* presence.
            std::env::set_var("ODYSSEUS_VECTOR_BACKEND", "weaviate");
            assert_eq!(resolve_backend_from_env(), Backend::Sqlite);
            std::env::set_var("CHROMADB_HOST", "chromadb");
            assert_eq!(resolve_backend_from_env(), Backend::ChromaHttp);
        });
    }

    #[test]
    fn select_backend_caches_and_reset_reresolves() {
        with_backend_env(|| {
            // No env -> Sqlite, and the choice is cached.
            assert_eq!(select_backend(), Backend::Sqlite);
            // Flip the env; the cached value still wins until reset.
            std::env::set_var("ODYSSEUS_VECTOR_BACKEND", "chroma");
            assert_eq!(select_backend(), Backend::Sqlite);
            // Reset clears the cache -> re-resolves to ChromaHttp.
            reset_backend_selection();
            assert_eq!(select_backend(), Backend::ChromaHttp);
        });
    }

    #[test]
    fn chroma_heartbeat_is_noop_for_sqlite() {
        with_backend_env(|| {
            // SQLite default -> heartbeat is a no-op Ok (never touches the network).
            assert_eq!(select_backend(), Backend::Sqlite);
            assert!(chroma_heartbeat().is_ok());
        });
    }

    #[test]
    fn base_url_reads_python_env_defaults() {
        with_backend_env(|| {
            // Defaults match chroma_client.py: localhost:8100.
            assert_eq!(chroma_base_url(), "http://localhost:8100");
            std::env::set_var("CHROMADB_HOST", "chromadb");
            std::env::set_var("CHROMADB_PORT", "8000");
            assert_eq!(chroma_base_url(), "http://chromadb:8000");
        });
    }

    #[test]
    fn collections_url_is_v2_tenant_scoped() {
        let url = chroma_collections_url("http://localhost:8100");
        assert_eq!(
            url,
            "http://localhost:8100/api/v2/tenants/default_tenant/databases/default_database/collections"
        );
    }

    // -- Chroma JSON <-> result-shape mapping (pure; no live server) ---------

    #[test]
    fn parse_get_result_flat_shape() {
        // The flat un-nested `get` response shape.
        let val = json!({
            "ids": ["a", "b"],
            "documents": ["alpha", "beta"],
            "metadatas": [{"owner": "u1"}, null],
        });
        let g = parse_get_result(&val);
        assert_eq!(g.ids, vec!["a".to_string(), "b".to_string()]);
        assert_eq!(g.documents, vec!["alpha".to_string(), "beta".to_string()]);
        // null metadata -> {}
        assert_eq!(g.metadatas[0], json!({"owner": "u1"}));
        assert_eq!(g.metadatas[1], json!({}));
    }

    #[test]
    fn parse_get_result_pads_missing_columns() {
        // Only ids present -> documents/metadatas padded to match.
        let val = json!({ "ids": ["a", "b", "c"] });
        let g = parse_get_result(&val);
        assert_eq!(g.ids.len(), 3);
        assert_eq!(g.documents, vec!["".to_string(); 3]);
        assert_eq!(g.metadatas, vec![json!({}), json!({}), json!({})]);
    }

    #[test]
    fn reorder_get_result_follows_supplied_order() {
        let raw = GetResult {
            ids: vec!["b".to_string(), "a".to_string()],
            documents: vec!["beta".to_string(), "alpha".to_string()],
            metadatas: vec![json!({"k": "b"}), json!({"k": "a"})],
        };
        // Ask for a, missing zzz, b -> a then b, skipping zzz.
        let out = reorder_get_result(raw, &["a".to_string(), "zzz".to_string(), "b".to_string()]);
        assert_eq!(out.ids, vec!["a".to_string(), "b".to_string()]);
        assert_eq!(out.documents, vec!["alpha".to_string(), "beta".to_string()]);
    }

    #[test]
    fn filter_get_result_applies_contains_in_rust() {
        let raw = GetResult {
            ids: vec!["a".to_string(), "b".to_string(), "c".to_string()],
            documents: vec!["".to_string(); 3],
            metadatas: vec![
                json!({"source": "/docs/a.txt"}),
                json!({"source": "/docs/b.txt"}),
                json!({"source": "/other/c.txt"}),
            ],
        };
        let filter = json!({"source": {"$contains": "/docs"}});
        let out = filter_get_result(raw, Some(&filter));
        assert_eq!(out.ids, vec!["a".to_string(), "b".to_string()]);
    }

    #[test]
    fn sanitize_where_drops_contains_keeps_scalar() {
        // $contains is dropped (applied in Rust); a scalar clause is kept.
        let only_contains = json!({"source": {"$contains": "/docs"}});
        assert_eq!(sanitize_where_for_chroma(Some(&only_contains)), None);

        let mixed = json!({"owner": "u1", "source": {"$contains": "/docs"}});
        let sent = sanitize_where_for_chroma(Some(&mixed)).unwrap();
        assert_eq!(sent, json!({"owner": "u1"}));

        // None / empty -> None (omit the key entirely).
        assert_eq!(sanitize_where_for_chroma(None), None);
        assert_eq!(sanitize_where_for_chroma(Some(&json!({}))), None);
    }

    #[test]
    fn parse_query_result_nested_once_shape() {
        // The nested-once `query` response.
        let val = json!({
            "ids": [["a", "b"]],
            "distances": [[0.0, 0.25]],
            "documents": [["alpha", "beta"]],
            "metadatas": [[{"owner": "u1"}, null]],
        });
        let q = parse_query_result(&val);
        assert_eq!(q.ids.len(), 1); // nested once
        assert_eq!(q.ids[0], vec!["a".to_string(), "b".to_string()]);
        assert_eq!(q.distances[0], vec![0.0, 0.25]);
        assert_eq!(q.documents[0], vec!["alpha".to_string(), "beta".to_string()]);
        assert_eq!(q.metadatas[0][0], json!({"owner": "u1"}));
        assert_eq!(q.metadatas[0][1], json!({})); // null -> {}
    }

    #[test]
    fn parse_query_result_empty_inner_list() {
        // Chroma's empty result: one empty inner list per query embedding.
        let val = json!({
            "ids": [[]],
            "distances": [[]],
            "documents": [[]],
            "metadatas": [[]],
        });
        let q = parse_query_result(&val);
        assert_eq!(q.ids.len(), 1);
        assert!(q.ids[0].is_empty());
        assert!(q.distances[0].is_empty());
    }

    #[test]
    fn parse_collection_id_extracts_uuid() {
        let val = json!({"id": "11111111-2222-3333-4444-555555555555", "name": "odysseus_rag"});
        assert_eq!(
            parse_collection_id(&val).unwrap(),
            "11111111-2222-3333-4444-555555555555"
        );
        // Missing id -> error.
        let bad = json!({"name": "odysseus_rag"});
        assert!(parse_collection_id(&bad).is_err());
    }

    // -- Connect timeout / TCP pre-probe (pure; no live server) ---------------

    #[test]
    fn connect_timeout_default_is_2s() {
        with_backend_env(|| {
            // No env set -> default 2.0 s.
            assert_eq!(
                chroma_connect_timeout(),
                std::time::Duration::from_secs_f64(2.0)
            );
        });
    }

    #[test]
    fn connect_timeout_reads_env_var() {
        with_backend_env(|| {
            std::env::set_var("CHROMADB_CONNECT_TIMEOUT", "0.5");
            assert_eq!(
                chroma_connect_timeout(),
                std::time::Duration::from_secs_f64(0.5)
            );
            // Non-numeric falls back to default.
            std::env::set_var("CHROMADB_CONNECT_TIMEOUT", "bad");
            assert_eq!(
                chroma_connect_timeout(),
                std::time::Duration::from_secs_f64(2.0)
            );
            // Negative clamped to 0.
            std::env::set_var("CHROMADB_CONNECT_TIMEOUT", "-1.0");
            assert_eq!(chroma_connect_timeout(), std::time::Duration::ZERO);
        });
    }

    #[test]
    fn port_open_returns_false_for_closed_port() {
        // Port 1 is almost certainly closed / refused on loopback. Use a very
        // short timeout to keep the test fast. We cannot assert `true` here
        // without a live server, but we CAN assert that a closed port returns
        // false quickly (well under 500 ms even with OS overhead).
        let timeout = std::time::Duration::from_millis(200);
        let t0 = std::time::Instant::now();
        let open = port_open("127.0.0.1", 1, timeout);
        let elapsed = t0.elapsed();
        // Should be false (port 1 is not open in CI).
        assert!(!open, "expected port 1 to be closed");
        // Should complete well within 2× the timeout (not block indefinitely).
        assert!(
            elapsed < std::time::Duration::from_millis(600),
            "port_open took too long: {elapsed:?}"
        );
    }

    #[test]
    fn port_open_returns_false_for_bad_hostname() {
        // An unresolvable hostname should return false, not panic.
        let timeout = std::time::Duration::from_millis(200);
        let open = port_open("this-host-does-not-exist.invalid", 8100, timeout);
        assert!(!open);
    }

    #[test]
    fn chroma_http_collection_id_fails_fast_when_unreachable() {
        // When ChromaDB is not running on localhost:8100, `collection_id()`
        // should return Err with the descriptive message (matching Python's
        // RuntimeError from `get_chroma_client()`), NOT block for 30 s.
        //
        // Skip if something IS actually listening on port 8100 (e.g. a live
        // Chroma instance in CI). We use a 50 ms probe to check this first.
        if port_open("127.0.0.1", 8100, std::time::Duration::from_millis(50)) {
            // A real server is up — the probe would return true, not an error.
            // Skip this assertion so we don't false-fail in a live environment.
            return;
        }
        // Set a very short connect timeout so the test runs fast.
        with_backend_env(|| {
            std::env::set_var("CHROMADB_CONNECT_TIMEOUT", "0.1");
            // Build the handle (constructor is always cheap — no network).
            let handle = ChromaHttpCollection {
                base_url: "http://127.0.0.1:8100".to_string(),
                name: "test_col".to_string(),
                id: OnceCell::new(),
            };
            let t0 = std::time::Instant::now();
            let err = handle.collection_id().unwrap_err();
            let elapsed = t0.elapsed();
            let msg = err.to_string();
            assert!(
                msg.contains("ChromaDB is not reachable"),
                "expected reachability error, got: {msg}"
            );
            assert!(
                msg.contains("CHROMADB_HOST"),
                "expected hint about CHROMADB_HOST in: {msg}"
            );
            // Should fail well within 2 s (our probe timeout is 100 ms).
            assert!(
                elapsed < std::time::Duration::from_secs(2),
                "collection_id took too long: {elapsed:?}"
            );
        });
    }
}
