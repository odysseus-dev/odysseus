// src/database.rs  <- src/database.py
//! Thin re-export shim mirroring the Python `src/database.py`.
//!
//! The Python file is a pure pass-through:
//!
//! ```python
//! # Re-export everything from the canonical core.database module
//! # so that `from src.database import X` continues to work everywhere.
//! from core.database import *  # noqa: F401,F403
//! from core.database import (Base, TimestampMixin, DATABASE_URL, engine,
//!     SessionLocal, Session, ChatMessage, Document, ..., archive_session)
//! ```
//!
//! i.e. it exists only so that `from src.database import X` keeps working after
//! the canonical home for the schema moved to `core.database`. The Rust analogue
//! is a `pub use` of the symbols `crate::core::database` actually provides plus
//! the row models the Rust port keeps in `crate::core::models`.
//!
//! WHAT IS RE-EXPORTED (the live, ported surface):
//!   * Free functions — `session_local` (Python `SessionLocal`), `init_db`,
//!     `bulk_insert_messages`, `cleanup_old_sessions`, `get_session_stats`,
//!     `get_detailed_stats`, `update_session_last_accessed`, `get_session_by_id`,
//!     `archive_session`, `database_url` (Python `DATABASE_URL`), `db_path`,
//!     `create_all`.
//!   * Row structs — `Session` (the `core::database` DB-row struct, matching the
//!     Python `Session` ORM model) and `ChatMessage` (the Rust port keeps this
//!     row struct in `crate::core::models`; `core::database` does not define it,
//!     so it is re-exported from there to preserve `from src.database import
//!     ChatMessage`).
//!
//! NOT PORTED (honest documentation, NOT a stub — these never existed in the
//! Rust translation because the port uses raw `rusqlite` via `session_local()`
//! instead of the SQLAlchemy declarative ORM):
//!   * The SQLAlchemy declarative-ORM model classes beyond `Session`/`ChatMessage`
//!     — `Webhook`, `ScheduledTask`, `TaskRun`, `Document`, `DocumentVersion`,
//!     `GalleryImage`, `ModelEndpoint`, `McpServer`, `Comparison`, `ApiToken`,
//!     `Signature`, `UserTool`, `UserToolData`, `CrewMember`, `Memory`. Their
//!     tables exist in the schema (`core::database::create_all` emits the DDL for
//!     all 24 tables) and consumer modules query them with hand-written raw SQL
//!     over `session_local()`; there are no Rust struct mirrors for them.
//!   * The SQLAlchemy plumbing — `Base`, `TimestampMixin`, `engine`, and the
//!     `get_db` / `get_db_session` dependency-injection generators. rusqlite has
//!     no `Base`/`engine` analogue: `session_local()` opens a fresh `Connection`
//!     per call (the `SessionLocal()` analogue), so the FastAPI `get_db` /
//!     `get_db_session` yield-generators have no place here.
//!
//! Consumers (`event_bus` / `cleanup_service` / `session_actions` /
//! `document_actions` / `webhook_manager` / `caldav_sync`) call
//! `crate::core::database::session_local()` + raw SQL directly (the established
//! convention), NOT through this shim — this module is faithful provenance for
//! the Python `src.database` import surface.
//!
//! Always compiled — the crate has no cargo feature flags — and simply re-exports
//! `crate::core::database` (likewise always compiled in `core/mod.rs`).

pub use crate::core::database::{
    archive_session, bulk_insert_messages, cleanup_old_sessions, create_all, database_url, db_path,
    get_detailed_stats, get_session_by_id, get_session_stats, init_db, session_local,
    update_session_last_accessed, Session,
};

// `ChatMessage` is the Python `core.database.ChatMessage` ORM model; the Rust
// port keeps this row struct in `crate::core::models` (re-exported here to
// preserve `from src.database import ChatMessage`).
pub use crate::core::models::ChatMessage;
