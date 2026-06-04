// core/models.rs  <- core/models.py
//! Pure data models — no database logic, no side effects.
//!
//! These are simple datacontainers. All persistence is handled by SessionManager.

use indexmap::IndexMap;
use once_cell::sync::Lazy;
use serde_json::{Map, Value};
use std::sync::{Arc, RwLock};

/// The capabilities `models` needs from the session manager: `_persist_message`
/// (append) and `replace_messages` (whole-history swap, used by the
/// `context_compactor` after summarizing). Python references the concrete
/// `SessionManager`; we depend on this trait instead so the (DB-backed) manager
/// can live in another module without `models` pulling in the database stack.
pub trait SessionPersistence: Send + Sync {
    fn _persist_message(&self, session_id: &str, message: &ChatMessage);

    /// `manager.replace_messages(session_id, messages)` — replace the stored
    /// history wholesale. Returns `true` when the stored copy was updated
    /// (Python returns the same bool); `false` lets the caller fall back to a
    /// local-only update.
    fn replace_messages(&self, session_id: &str, messages: Vec<ChatMessage>) -> bool;
}

// Module-level session manager reference (set at app startup).
//
// Python keeps `_session_manager: Optional[SessionManager]` as a module global.
// The faithful translation is a process-global behind a lock holding a shared
// (`Arc`) handle to the one manager.
static _SESSION_MANAGER: Lazy<RwLock<Option<Arc<dyn SessionPersistence>>>> =
    Lazy::new(|| RwLock::new(None));

/// Set the global session manager reference.
pub fn set_session_manager(manager: Arc<dyn SessionPersistence>) {
    let mut slot = _SESSION_MANAGER.write().unwrap();
    *slot = Some(manager);
}

/// Read the global session manager reference (the `if _session_manager:` check).
pub fn session_manager() -> Option<Arc<dyn SessionPersistence>> {
    _SESSION_MANAGER.read().unwrap().clone()
}

/// A single chat message.
#[derive(Debug, Clone, Default)]
pub struct ChatMessage {
    pub role: String,
    pub content: String,
    pub metadata: Option<Map<String, Value>>,
}

impl ChatMessage {
    /// `ChatMessage(role=..., content=..., metadata=...)`
    pub fn new(
        role: impl Into<String>,
        content: impl Into<String>,
        metadata: Option<Map<String, Value>>,
    ) -> Self {
        ChatMessage {
            role: role.into(),
            content: content.into(),
            metadata,
        }
    }

    /// Convert to dict for API responses.
    pub fn to_dict(&self) -> Map<String, Value> {
        let mut result = Map::new();
        result.insert("role".to_string(), Value::String(self.role.clone()));
        result.insert("content".to_string(), Value::String(self.content.clone()));
        // `if self.metadata:` — truthy only when present *and* non-empty.
        if let Some(md) = &self.metadata {
            if !md.is_empty() {
                result.insert("metadata".to_string(), Value::Object(md.clone()));
            }
        }
        result
    }

    /// Dict-like access for compatibility (`getattr(self, key, default)`).
    pub fn get(&self, key: &str, default: Value) -> Value {
        match key {
            "role" => Value::String(self.role.clone()),
            "content" => Value::String(self.content.clone()),
            "metadata" => match &self.metadata {
                Some(m) => Value::Object(m.clone()),
                None => Value::Null,
            },
            _ => default,
        }
    }
}

/// A chat session — pure data container.
#[derive(Debug, Clone, Default)]
pub struct Session {
    pub id: String,
    pub name: String,
    pub endpoint_url: String,
    pub model: String,
    pub rag: bool,
    pub archived: bool,
    // `__post_init__` turns `None` into `{}` / `[]`, so these always exist.
    // Python `dict` preserves insertion order; `IndexMap` does too (a plain
    // `HashMap` would randomize header iteration / JSON output).
    pub headers: IndexMap<String, String>,
    pub history: Vec<ChatMessage>,
    pub owner: Option<String>,
    pub is_important: bool,
    pub message_count: i64,
}

impl Session {
    /// Add a message to this session.
    ///
    /// Delegates to SessionManager for persistence if available, otherwise just
    /// appends to history.
    ///
    /// Note: unlike Python — where `session` is the very object stored in the
    /// manager — a Rust `Session` is owned, so persistence re-serializes the
    /// manager's own view (matching `_persist_message`, whose `message` argument
    /// the Python also ignores). Use `SessionManager::add_message` to mutate and
    /// persist a stored session.
    pub fn add_message(&mut self, message: ChatMessage) {
        self.history.push(message.clone());
        self.message_count = self.history.len() as i64;

        // Delegate to session manager for persistence
        if let Some(_session_manager) = session_manager() {
            _session_manager._persist_message(&self.id, &message);
        }
    }

    /// Get messages in format for LLM API.
    pub fn get_context_messages(&self) -> Vec<Value> {
        self.history.iter().map(|m| Value::Object(m.to_dict())).collect()
    }

    /// Dict-like access for compatibility (`getattr(self, key, default)`).
    pub fn get(&self, key: &str, default: Value) -> Value {
        match key {
            "id" => Value::String(self.id.clone()),
            "name" => Value::String(self.name.clone()),
            "endpoint_url" => Value::String(self.endpoint_url.clone()),
            "model" => Value::String(self.model.clone()),
            "rag" => Value::Bool(self.rag),
            "archived" => Value::Bool(self.archived),
            "headers" => Value::Object(
                self.headers
                    .iter()
                    .map(|(k, v)| (k.clone(), Value::String(v.clone())))
                    .collect(),
            ),
            "history" => Value::Array(self.history.iter().map(|m| Value::Object(m.to_dict())).collect()),
            "owner" => match &self.owner {
                Some(o) => Value::String(o.clone()),
                None => Value::Null,
            },
            "is_important" => Value::Bool(self.is_important),
            "message_count" => Value::Number(self.message_count.into()),
            _ => default,
        }
    }
}
