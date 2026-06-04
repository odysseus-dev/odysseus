// src/request_models.rs  <- src/request_models.py
//! Pydantic request/response models for API endpoints.
//!
//! Each Python `class X(BaseModel)` becomes a `#[derive(Deserialize, Serialize)]`
//! struct. Pydantic semantics map to serde:
//!   * a `Field(...)` with no default is **required** (no `#[serde(default)]`),
//!     so deserialization errors when the key is absent — pydantic's required-
//!     field validation;
//!   * a `Field(default=...)` is optional — `#[serde(default)]` (or a
//!     `default = "fn"` for non-zero defaults) supplies the same value.
//!
//! `@field_validator`s that *coerce* a value (strip the message, snap an invalid
//! `time_filter`/`category` to a fallback) are reproduced via
//! `#[serde(deserialize_with = ...)]`; the one that uses a `pattern=` (which
//! pydantic enforces by *raising*) errors during deserialization.
//!
//! DEVIATION: pydantic `Field` length/range constraints (`min_length`,
//! `max_length`, `ge`, `le`) are NOT enforced by serde derive. They are
//! documented here and left to a later validation pass; the coercing validators,
//! which change the returned value, ARE reproduced because they affect behaviour.

use chrono::{DateTime, Utc};
use serde::de::Error as _;
use serde::{Deserialize, Deserializer, Serialize};
use serde_json::Value;

// ---- defaults (pydantic Field(default=...) with non-zero values) ----
fn default_true() -> bool {
    true
}
fn default_one() -> f64 {
    1.0
}
fn default_fact() -> String {
    "fact".to_string()
}
fn default_user() -> String {
    "user".to_string()
}

// ---- @field_validator reproductions ----

/// `clean_message`: `return v.strip()`.
fn de_strip<'de, D: Deserializer<'de>>(d: D) -> Result<String, D::Error> {
    let s = String::deserialize(d)?;
    Ok(s.trim().to_string())
}

/// `validate_time_filter`: keep only day/week/month/year, else None.
fn de_time_filter<'de, D: Deserializer<'de>>(d: D) -> Result<Option<String>, D::Error> {
    let v = Option::<String>::deserialize(d)?;
    Ok(match v {
        Some(s) if matches!(s.as_str(), "day" | "week" | "month" | "year") => Some(s),
        _ => None,
    })
}

const MEMORY_CATEGORIES: [&str; 7] =
    ["fact", "contact", "task", "preference", "identity", "project", "goal"];

/// `validate_category`: snap an invalid category to "fact".
fn de_category_or_fact<'de, D: Deserializer<'de>>(d: D) -> Result<String, D::Error> {
    let s = String::deserialize(d)?;
    Ok(if MEMORY_CATEGORIES.contains(&s.as_str()) {
        s
    } else {
        "fact".to_string()
    })
}

/// `category: Optional[str] = Field(default=None, pattern="^(fact|...|goal)$")`.
/// pydantic raises on a non-matching value, so this errors during deserialize.
fn de_category_pattern<'de, D: Deserializer<'de>>(d: D) -> Result<Option<String>, D::Error> {
    let v = Option::<String>::deserialize(d)?;
    match v {
        None => Ok(None),
        Some(s) if MEMORY_CATEGORIES.contains(&s.as_str()) => Ok(Some(s)),
        Some(s) => Err(D::Error::custom(format!(
            "string does not match regex \"^(fact|contact|task|preference|identity|project|goal)$\": {s}"
        ))),
    }
}

// ---- Request Models ----

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct ChatRequest {
    /// message: str = Field(..., min_length=1, max_length=50000); cleaned via strip.
    #[serde(deserialize_with = "de_strip")]
    pub message: String,
    /// session: str = Field(...) — required Session ID.
    pub session: String,
    /// attachments: Optional[List[str]] = Field(default=[]).
    #[serde(default)]
    pub attachments: Vec<String>,
    /// use_web: Optional[bool] = Field(default=False).
    #[serde(default)]
    pub use_web: bool,
    /// use_research: Optional[bool] = Field(default=False).
    #[serde(default)]
    pub use_research: bool,
    /// time_filter: Optional[str] = Field(default=None) — snapped to a valid value or None.
    #[serde(default, deserialize_with = "de_time_filter")]
    pub time_filter: Option<String>,
    /// preset_id: Optional[str] = Field(default=None).
    #[serde(default)]
    pub preset_id: Option<String>,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct SessionCreateRequest {
    /// name: Optional[str] = Field(default="", max_length=200).
    #[serde(default)]
    pub name: String,
    /// endpoint_url: str = Field(...) — required.
    pub endpoint_url: String,
    /// model: Optional[str] = Field(default="").
    #[serde(default)]
    pub model: String,
    /// rag: Optional[bool] = Field(default=False).
    #[serde(default)]
    pub rag: bool,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct MemoryAddRequest {
    /// text: str = Field(..., min_length=1, max_length=5000) — required.
    pub text: String,
    /// category: str = Field(default="fact") — invalid snapped to "fact".
    #[serde(default = "default_fact", deserialize_with = "de_category_or_fact")]
    pub category: String,
    /// source: str = Field(default="user").
    #[serde(default = "default_user")]
    pub source: String,
    /// session_id: Optional[str] = Field(default=None).
    #[serde(default)]
    pub session_id: Option<String>,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct MemoryUpdateRequest {
    /// text: str = Field(..., min_length=1, max_length=5000) — required.
    pub text: String,
    /// category: Optional[str] = Field(default=None, pattern="^(fact|...|goal)$").
    #[serde(default, deserialize_with = "de_category_pattern")]
    pub category: Option<String>,
}

/// Request model for updating custom preset configuration.
#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct PresetUpdateRequest {
    /// Character display name (shown next to model name). Field("", max_length=50).
    #[serde(default)]
    pub name: String,
    /// Whether this character is active. Field(True).
    #[serde(default = "default_true")]
    pub enabled: bool,
    /// Temperature parameter (0.0-2.0). Field(1.0, ge=0.0, le=2.0).
    #[serde(default = "default_one")]
    pub temperature: f64,
    /// Maximum tokens to generate (0 = no limit). Field(0, ge=0, le=8192).
    #[serde(default)]
    pub max_tokens: i64,
    /// System prompt to guide assistant behavior. Field("", max_length=10000).
    #[serde(default)]
    pub system_prompt: String,
    /// Text to prepend to each outgoing user message. Field("", max_length=5000).
    #[serde(default)]
    pub inject_prefix: String,
    /// Text to append to each outgoing user message. Field("", max_length=5000).
    #[serde(default)]
    pub inject_suffix: String,
}

/// Request model for directory operations.
#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct DirectoryRequest {
    /// directory: str = Field(..., min_length=1, max_length=500) — required.
    pub directory: String,
}

// ---- Response Models ----

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct ErrorResponse {
    pub error: String,
    pub message: String,
    #[serde(default)]
    pub details: Option<Value>,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct UploadResponse {
    pub id: String,
    pub name: String,
    pub mime: String,
    pub size: i64,
    pub hash: String,
    pub uploaded_at: DateTime<Utc>,
    #[serde(default)]
    pub is_duplicate: bool,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct SessionResponse {
    pub id: String,
    pub name: String,
    pub model: String,
    #[serde(default)]
    pub rag: bool,
    #[serde(default)]
    pub archived: bool,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct MemoryResponse {
    pub id: String,
    pub text: String,
    pub category: String,
    pub source: String,
    pub timestamp: i64,
    #[serde(default)]
    pub session_id: Option<String>,
}
