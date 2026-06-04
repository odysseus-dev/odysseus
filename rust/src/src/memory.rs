// src/memory.rs  <- src/memory.py
//! JSON-file `MemoryManager` plus the `tokenize` / `get_text_similarity`
//! free functions.
//!
//! Faithful port of `src/memory.py`. The Python stores memory entries as a flat
//! JSON list of heterogeneous dicts; the Rust port mirrors that with
//! `serde_json::Value` objects (the crate is built with `preserve_order`, so
//! key insertion order is retained the way Python's `dict` does). Writes are
//! atomic the same way (`memory.json.tmp` then `os.replace`).
//!
//! PORT_NOW — fully self-contained, default build. No external services.
//!
//! One behaviour is preserved *exactly* as the Python, including its quirk:
//!
//! * The default-`source` drift between `_validate_entries` ("unknown") and
//!   `save` ("user"): an entry loaded from disk missing `source` is stamped
//!   "unknown", but the same missing field on a `save()` is stamped "user".
//!
//! The bullet-regex bug in `extract_memory_from_chat` (where a `-`/`*`/`•` line
//! used to crash Python on `None.strip()`) has been fixed upstream: the inner
//! regex now captures the trailing text for both the bullet and numbered
//! branches, so bullet lines extract their inner text instead of raising. The
//! method still returns `PyResult` (it can no longer error), keeping the
//! signature stable for callers.

use crate::error::{PyError, PyResult};
use crate::pyos as os;
use crate::pytime as time;
use once_cell::sync::Lazy;
use regex::Regex;
use serde_json::{json, Map, Value};
use std::collections::HashSet;

/// Simple tokenizer that splits on whitespace and removes punctuation.
///
/// `[cleaned for word in text.split() if (cleaned := word.strip('.,!?";'))]` —
/// `str.split()` with no argument splits on arbitrary runs of whitespace and
/// discards empty leading/trailing fields; `str.strip(chars)` removes the
/// listed characters from both ends. Tokens that strip down to empty are
/// filtered out (a word made entirely of punctuation yields no token).
pub fn tokenize(text: &str) -> Vec<String> {
    const STRIP_CHARS: &[char] = &['.', ',', '!', '?', '"', ';'];
    text.split_whitespace()
        .map(|word| word.trim_matches(STRIP_CHARS))
        .filter(|word| !word.is_empty())
        .map(|word| word.to_string())
        .collect()
}

/// Calculate Jaccard similarity between two texts.
pub fn get_text_similarity(text1: &str, text2: &str) -> f64 {
    // if not text1 or not text2: return 0.0
    if text1.is_empty() || text2.is_empty() {
        return 0.0;
    }

    let tokens1: HashSet<String> = tokenize(&text1.to_lowercase()).into_iter().collect();
    let tokens2: HashSet<String> = tokenize(&text2.to_lowercase()).into_iter().collect();

    if tokens1.is_empty() && tokens2.is_empty() {
        return 1.0;
    }
    if tokens1.is_empty() || tokens2.is_empty() {
        return 0.0;
    }

    let intersection = tokens1.intersection(&tokens2).count();
    let union = tokens1.union(&tokens2).count();

    intersection as f64 / union as f64
}

// `re.match` anchors at the *start* of the string; the `regex` crate's
// `is_match`/`find` are unanchored, so the patterns below carry an explicit `^`
// (already present in the Python source where it relies on `re.match`).

/// `re.match(r'^[-*•]|\d+\.', line)` — bullet or numbered-list marker at start.
static BULLET_OR_NUMBER_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"^(?:[-*•]|\d+\.)").unwrap());
/// `re.match(r'^(?:[-*•]|\d+\.)\s*(.*)', line)` — the bullet/number marker is a
/// single non-capturing alternation, and capture group 1 captures the trailing
/// inner text for BOTH branches (bullet and numbered). The previous pattern put
/// the group on the numbered branch only, so a bullet line matched with
/// group(1)=None and crashed Python on `.strip()`; this is the corrected form.
static BULLET_INNER_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"^(?:[-*•]|\d+\.)\s*(.*)").unwrap());
/// `re.search(r'memory|fact|note|remember', line, re.I)`.
static MEMORY_HEADING_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"(?i)memory|fact|note|remember").unwrap());
/// `re.match(r'^={3,}|-{3,}|_{3,}', line)`.
static SEPARATOR_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"^(?:={3,}|-{3,}|_{3,})").unwrap());
/// `re.match(pattern, message.strip(), re.IGNORECASE)` for inline commands.
static INLINE_COMMAND_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"(?i)^(?:remember|memorize|save|note|store)[:\-]?\s+(.+)$").unwrap());
/// `re.search(r'\b[A-Z][a-z]+ [A-Z][a-z]+\b', memory["text"])` — full-name shape.
static FULL_NAME_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"\b[A-Z][a-z]+ [A-Z][a-z]+\b").unwrap());

/// `uuid.uuid4()` rendered with dashes (matches Python's `str(uuid.uuid4())`).
fn uuid4() -> String {
    uuid::Uuid::new_v4().to_string()
}

/// `int(time.time())` — current Unix time truncated to whole seconds.
fn now_int() -> i64 {
    time::time() as i64
}

/// JSON-file-backed memory store.
pub struct MemoryManager {
    /// `self.memory_file = os.path.join(data_dir, "memory.json")`.
    pub memory_file: String,
}

impl MemoryManager {
    /// `MemoryManager(data_dir)` — builds the file path and ensures the file
    /// exists (mirroring `__init__`).
    pub fn new(data_dir: &str) -> PyResult<Self> {
        let memory_file = os::path::join(data_dir, "memory.json");
        let mgr = MemoryManager { memory_file };
        mgr.ensure_file_exists()?;
        Ok(mgr)
    }

    /// Extract memory entries from chat history as a fallback when LLM fails.
    ///
    /// `chat_history` is a list of chat messages with `role`/`content` keys.
    /// `session_id` is optional and associated with each extracted memory.
    ///
    /// Returns memory entries with text, timestamp, and optional session_id.
    ///
    /// Bullet (`-`/`*`/`•`) and numbered (`1.`) lines both extract their inner
    /// text via capture group 1 of the corrected `BULLET_INNER_RE`. (Previously
    /// a bullet line crashed Python on `None.strip()`; that bug is fixed.) The
    /// method keeps its `PyResult` return type for caller-signature stability,
    /// but no longer produces an error.
    // if_same_then_else: mirrors Python memory.py:73-77 — two `elif ... : pass`
    // branches (heading / separator) with deliberately identical empty bodies.
    #[allow(clippy::if_same_then_else)]
    pub fn extract_memory_from_chat(
        &self,
        chat_history: &[Value],
        session_id: Option<&str>,
    ) -> PyResult<Vec<Value>> {
        let mut memories: Vec<Value> = Vec::new();

        for msg in chat_history {
            // if msg.get("role") == "assistant":
            if msg.get("role").and_then(|v| v.as_str()) == Some("assistant") {
                // content = str(msg.get("content", ""))
                let content = str_of(msg.get("content"));
                // lines = content.split('\n')
                for line in content.split('\n') {
                    // line = line.strip()
                    let line = line.trim();

                    if BULLET_OR_NUMBER_RE.is_match(line) {
                        // text_match = re.match(r'^(?:[-*•]|\d+\.)\s*(.*)', line)
                        if let Some(caps) = BULLET_INNER_RE.captures(line) {
                            // text = text_match.group(1).strip()
                            // group(1) now captures the inner text for both the
                            // bullet and numbered branches (corrected regex), so
                            // it always participates when the pattern matched.
                            let text = caps.get(1).map(|m| m.as_str()).unwrap_or("").trim();
                            if !text.is_empty() {
                                // timestamp = int(datetime.now().timestamp()) —
                                // Unix seconds, identical to int(time.time()).
                                memories.push(json!({
                                    "text": text,
                                    "timestamp": now_int(),
                                    "session_id": session_id,
                                }));
                            }
                        }
                    } else if MEMORY_HEADING_RE.is_match(line) {
                        // pass
                    } else if SEPARATOR_RE.is_match(line) {
                        // pass
                    }
                }
            }
        }

        Ok(memories)
    }

    /// Check if a message is an inline memory command (e.g. "remember: X").
    ///
    /// Returns `(is_command, extracted_text)` where `is_command` is true if the
    /// message matches the memory command pattern.
    pub fn process_inline_memory_command(&self, message: &str) -> (bool, String) {
        match INLINE_COMMAND_RE.captures(message.trim()) {
            Some(caps) => (
                true,
                caps.get(1).map(|m| m.as_str().trim().to_string()).unwrap_or_default(),
            ),
            None => (false, String::new()),
        }
    }

    /// Create memory file if it doesn't exist.
    pub fn ensure_file_exists(&self) -> PyResult<()> {
        if !os::path::exists(&self.memory_file) {
            // os.makedirs(os.path.dirname(self.memory_file), exist_ok=True)
            // create_dir_all is the exist_ok=True analogue; a path with no
            // parent (dirname == "") is a no-op, matching os.makedirs("").
            let parent = os::path::dirname(&self.memory_file);
            if !parent.is_empty() {
                std::fs::create_dir_all(&parent)?;
            }
            // json.dump([], f, ensure_ascii=False, indent=2)
            std::fs::write(&self.memory_file, "[]")?;
        }
        Ok(())
    }

    /// Load all memory entries from JSON file (unfiltered).
    pub fn load_all(&self) -> Vec<Value> {
        // if not os.path.exists(self.memory_file): return []
        if !os::path::exists(&self.memory_file) {
            return Vec::new();
        }

        match std::fs::read_to_string(&self.memory_file) {
            Ok(raw) => match serde_json::from_str::<Value>(&raw) {
                Ok(Value::Array(data)) => self.validate_entries(data),
                // isinstance(data, list) is False -> falls through to `return []`.
                Ok(_) => Vec::new(),
                // json.JSONDecodeError -> migrate from legacy.
                Err(e) => {
                    crate::pylog::error(&format!("Error loading memory.json: {e}"));
                    self.migrate_from_legacy()
                }
            },
            // PermissionError (and other read failures) -> migrate from legacy.
            Err(e) => {
                crate::pylog::error(&format!("Error loading memory.json: {e}"));
                self.migrate_from_legacy()
            }
        }
    }

    /// Load memory entries, optionally filtered by owner.
    pub fn load(&self, owner: Option<&str>) -> Vec<Value> {
        let entries = self.load_all();
        match owner {
            None => entries,
            Some(owner) => entries
                .into_iter()
                .filter(|e| e.get("owner").and_then(|v| v.as_str()) == Some(owner))
                .collect(),
        }
    }

    /// Ensure all entries have required fields.
    ///
    /// NOTE the source-default drift vs `save`: a missing `source` here defaults
    /// to "unknown" (preserved deliberately).
    fn validate_entries(&self, entries: Vec<Value>) -> Vec<Value> {
        let mut validated: Vec<Value> = Vec::new();
        for mut entry in entries {
            // Only objects can carry these fields; Python would crash on a
            // non-dict element, but the data is always a list of dicts.
            if let Some(obj) = entry.as_object_mut() {
                if !obj.contains_key("id") {
                    obj.insert("id".to_string(), json!(uuid4()));
                }
                if !obj.contains_key("timestamp") {
                    obj.insert("timestamp".to_string(), json!(now_int()));
                }
                if !obj.contains_key("source") {
                    obj.insert("source".to_string(), json!("unknown"));
                }
                if !obj.contains_key("category") {
                    obj.insert("category".to_string(), json!("fact"));
                }
                if !obj.contains_key("uses") {
                    obj.insert("uses".to_string(), json!(0));
                }
            }
            validated.push(entry);
        }
        validated
    }

    /// Migrate from old text format to JSON if needed.
    fn migrate_from_legacy(&self) -> Vec<Value> {
        // legacy_path = os.path.join(os.path.dirname(self.memory_file), "memory.txt")
        let legacy_path = os::path::join(&os::path::dirname(&self.memory_file), "memory.txt");
        if !os::path::exists(&legacy_path) {
            return Vec::new();
        }

        crate::pylog::info("Converting legacy memory.txt to new JSON format");
        // try: ... except Exception: return []
        let raw = match std::fs::read_to_string(&legacy_path) {
            Ok(raw) => raw,
            Err(e) => {
                crate::pylog::error(&format!("Failed to convert legacy memory: {e}"));
                return Vec::new();
            }
        };

        // lines = [ln.strip() for ln in f.readlines() if ln.strip()]
        let mut entries: Vec<Value> = Vec::new();
        for line in raw.lines() {
            let line = line.trim();
            if line.is_empty() {
                continue;
            }
            entries.push(json!({
                "id": uuid4(),
                "text": line,
                "timestamp": now_int(),
                "source": "user",
                "category": "fact",
            }));
        }

        if let Err(e) = self.save(&mut entries) {
            crate::pylog::error(&format!("Failed to convert legacy memory: {e}"));
            return Vec::new();
        }
        entries
    }

    /// Save memory entries to JSON file.
    ///
    /// NOTE the source-default drift vs `_validate_entries`: a missing `source`
    /// here defaults to "user" (preserved deliberately). The Python mutates the
    /// passed-in list in place; the Rust port takes `&mut` so the same fields
    /// are stamped onto the caller's entries.
    pub fn save(&self, entries: &mut [Value]) -> PyResult<()> {
        // Validate entries before saving.
        for entry in entries.iter_mut() {
            if let Some(obj) = entry.as_object_mut() {
                if !obj.contains_key("id") {
                    obj.insert("id".to_string(), json!(uuid4()));
                }
                if !obj.contains_key("timestamp") {
                    obj.insert("timestamp".to_string(), json!(now_int()));
                }
                if !obj.contains_key("source") {
                    obj.insert("source".to_string(), json!("user"));
                }
                if !obj.contains_key("category") {
                    obj.insert("category".to_string(), json!("fact"));
                }
            }
        }

        // Use atomic write: tmp file then os.replace.
        let tmp_file = format!("{}.tmp", self.memory_file);
        // json.dump(entries, f, ensure_ascii=False, indent=2)
        // serde_json pretty = 2-space indent; preserve_order keeps key order;
        // non-ASCII is emitted verbatim (matches ensure_ascii=False).
        let serialized = serde_json::to_string_pretty(&Value::Array(entries.to_vec()))?;
        std::fs::write(&tmp_file, serialized)?;
        os::replace(&tmp_file, &self.memory_file)?;
        Ok(())
    }

    /// Add a new memory entry.
    ///
    /// Raises `ValueError` on empty text (after stripping).
    pub fn add_entry(
        &self,
        text: &str,
        source: &str,
        category: &str,
        owner: Option<&str>,
    ) -> PyResult<Value> {
        if text.trim().is_empty() {
            return Err(PyError::value("Memory text cannot be empty"));
        }

        let mut entry = Map::new();
        entry.insert("id".to_string(), json!(uuid4()));
        entry.insert("text".to_string(), json!(text.trim()));
        entry.insert("timestamp".to_string(), json!(now_int()));
        entry.insert("source".to_string(), json!(source));
        entry.insert("category".to_string(), json!(category));
        entry.insert("uses".to_string(), json!(0));
        // if owner: entry["owner"] = owner  (Python falsy: None and "" skip it)
        if let Some(owner) = owner.filter(|o| !o.is_empty()) {
            entry.insert("owner".to_string(), json!(owner));
        }
        Ok(Value::Object(entry))
    }

    /// Bump the uses counter for each memory id. Called after a memory has
    /// actually been injected into a chat's context (not just retrieved).
    pub fn increment_uses(&self, ids: &[String]) -> PyResult<()> {
        if ids.is_empty() {
            return Ok(());
        }
        let id_set: HashSet<&str> = ids.iter().map(|s| s.as_str()).collect();
        let mut entries = self.load_all();
        let mut changed = false;
        for e in entries.iter_mut() {
            let matches = e
                .get("id")
                .and_then(|v| v.as_str())
                .map(|id| id_set.contains(id))
                .unwrap_or(false);
            if matches {
                // e["uses"] = int(e.get("uses", 0) or 0) + 1
                // `... or 0` coerces a falsy (None/0/"") value to 0; int() on a
                // present truthy value parses it.
                let current = e
                    .get("uses")
                    .map(py_int_or_zero)
                    .unwrap_or(0);
                if let Some(obj) = e.as_object_mut() {
                    obj.insert("uses".to_string(), json!(current + 1));
                }
                changed = true;
            }
        }
        if changed {
            self.save(&mut entries)?;
        }
        Ok(())
    }

    /// Find duplicate memory entries based on text content.
    ///
    /// When `entries` is `None`, loads via `self.load()` (owner-unfiltered).
    pub fn find_duplicates(&self, text: &str, entries: Option<&[Value]>) -> Vec<Value> {
        let loaded;
        let entries: &[Value] = match entries {
            Some(e) => e,
            None => {
                loaded = self.load(None);
                &loaded
            }
        };

        let text_lower = text.trim().to_lowercase();
        entries
            .iter()
            // entry["text"].lower() == text_lower  (KeyError if "text" absent,
            // but every stored entry has a "text" field)
            .filter(|entry| {
                entry
                    .get("text")
                    .and_then(|v| v.as_str())
                    .map(|t| t.to_lowercase() == text_lower)
                    .unwrap_or(false)
            })
            .cloned()
            .collect()
    }

    /// Categorize memories by type and relevance.
    ///
    /// Returns a dict with `contacts`/`preferences`/`facts`/`tasks` lists.
    pub fn categorize_memory_by_relevance(&self, message: &str, memories: &[Value]) -> Value {
        let mut contacts: Vec<Value> = Vec::new();
        let mut preferences: Vec<Value> = Vec::new();
        let mut facts: Vec<Value> = Vec::new();
        let mut tasks: Vec<Value> = Vec::new();

        let msg_lower = message.to_lowercase();

        for mem in memories {
            let text_lower = mem
                .get("text")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_lowercase();

            // Contact info
            if any_contains(&text_lower, &["phone", "email", "address", "lives", "works"]) {
                if any_contains(&msg_lower, &["contact", "phone", "address", "email"]) {
                    contacts.push(mem.clone());
                }
            }
            // Personal preferences
            else if any_contains(&text_lower, &["likes", "dislikes", "prefers", "favorite"]) {
                if any_contains(&msg_lower, &["like", "prefer", "favorite", "want"]) {
                    preferences.push(mem.clone());
                }
            }
            // Tasks and todos
            else if any_contains(&text_lower, &["todo", "task", "remind", "meeting"]) {
                if any_contains(&msg_lower, &["todo", "task", "schedule", "remind"]) {
                    tasks.push(mem.clone());
                }
            }
            // General facts - only if very relevant
            else {
                let mem_text = mem.get("text").and_then(|v| v.as_str()).unwrap_or("");
                if get_text_similarity(message, mem_text) > 0.4 {
                    facts.push(mem.clone());
                }
            }
        }

        json!({
            "contacts": contacts,
            "preferences": preferences,
            "facts": facts,
            "tasks": tasks,
        })
    }

    /// Get memories that are relevant to the query based on text similarity and
    /// semantic keyword matching.
    pub fn get_relevant_memories(
        &self,
        query: &str,
        memories: &[Value],
        threshold: f64,
        max_items: usize,
    ) -> Vec<Value> {
        // if not memories or not query.strip(): return []
        if memories.is_empty() || query.trim().is_empty() {
            return Vec::new();
        }

        // Define keyword categories for semantic matching.
        let identity_words = ["name", "who", "i", "am", "called", "identity", "myself", "me", "my"];
        let contact_words =
            ["phone", "email", "address", "contact", "number", "where", "located", "reach"];
        let preference_words = [
            "like", "prefer", "favorite", "want", "love", "hate", "dislike", "enjoy", "interested",
        ];
        let task_words = ["todo", "task", "remind", "meeting", "appointment", "schedule", "deadline"];
        let fact_words = [
            "what", "when", "where", "how", "why", "explain", "describe", "information", "know",
        ];

        let query_lower = query.to_lowercase();

        // Determine query type based on keywords (first match wins).
        let query_type: Option<&str> = if any_contains(&query_lower, &identity_words) {
            Some("identity")
        } else if any_contains(&query_lower, &contact_words) {
            Some("contact")
        } else if any_contains(&query_lower, &preference_words) {
            Some("preference")
        } else if any_contains(&query_lower, &task_words) {
            Some("task")
        } else if any_contains(&query_lower, &fact_words) {
            Some("fact")
        } else {
            None
        };

        // (score, memory) tuples; built in the same order as Python so the
        // stable sort below reproduces Python's `list.sort` ordering.
        let mut relevant: Vec<(f64, Value)> = Vec::new();
        let mut identity_memories: Vec<&Value> = Vec::new();
        let mut other_memories: Vec<&Value> = Vec::new();

        // Separate identity memories from others.
        for memory in memories {
            let raw_text = memory.get("text").and_then(|v| v.as_str()).unwrap_or("");
            let memory_text = raw_text.to_lowercase();
            // is_identity = any([ re.search(full-name), any(phrase in text) ])
            let is_identity = FULL_NAME_RE.is_match(raw_text)
                || any_contains(
                    &memory_text,
                    &["name is", "i'm", "i am", "called", "my name", "named", "call me"],
                );
            if is_identity {
                identity_memories.push(memory);
            } else {
                other_memories.push(memory);
            }
        }

        // For identity queries, include all identity memories regardless of
        // similarity (high fixed score so they sort first).
        if query_type == Some("identity") && !identity_memories.is_empty() {
            for memory in &identity_memories {
                relevant.push((0.9, (*memory).clone()));
            }
        }

        // Process other memories with similarity scoring.
        for memory in &other_memories {
            let raw_text = memory.get("text").and_then(|v| v.as_str()).unwrap_or("");
            let memory_text = raw_text.to_lowercase();
            let memory_tokens: HashSet<String> = tokenize(&memory_text).into_iter().collect();
            let query_tokens: HashSet<String> = tokenize(&query_lower).into_iter().collect();

            // if not query_tokens or not memory_tokens: continue
            if query_tokens.is_empty() || memory_tokens.is_empty() {
                continue;
            }

            let base_similarity = query_tokens.intersection(&memory_tokens).count() as f64
                / query_tokens.union(&memory_tokens).count() as f64;
            let mut final_score = base_similarity;

            // Apply boosts based on semantic matching.
            match query_type {
                Some("contact") => {
                    let has_contact_info = any_contains(
                        &memory_text,
                        &[
                            "@gmail.com", "@", ".com", "phone", "number", "address", "http", "www",
                            "tel:",
                        ],
                    );
                    if has_contact_info {
                        final_score *= 1.4; // 40% boost for contact-related memories
                    }
                }
                Some("preference") => {
                    let has_preference = any_contains(
                        &memory_text,
                        &[
                            "like", "love", "hate", "dislike", "prefer", "favorite", "enjoy",
                            "interested",
                        ],
                    );
                    if has_preference {
                        final_score *= 1.3; // 30% boost for preference-related memories
                    }
                }
                Some("task") => {
                    let has_task = any_contains(
                        &memory_text,
                        &[
                            "todo", "task", "remind", "meeting", "appointment", "schedule",
                            "deadline", "need to",
                        ],
                    );
                    if has_task {
                        final_score *= 1.3; // 30% boost for task-related memories
                    }
                }
                _ => {}
            }

            // Always consider exact phrase matches as highly relevant.
            // if query.lower() in memory["text"].lower():
            if raw_text.to_lowercase().contains(&query.to_lowercase()) {
                // final_score = max(final_score, 0.8)
                final_score = final_score.max(0.8);
            }

            // Include memory if it meets threshold after boosts.
            if final_score >= threshold {
                relevant.push((final_score, (*memory).clone()));
            }
        }

        // Sort by final score (descending). Python's `list.sort` is stable;
        // `sort_by` is stable, so ties keep insertion order exactly.
        relevant.sort_by(|a, b| b.0.partial_cmp(&a.0).unwrap_or(std::cmp::Ordering::Equal));
        relevant
            .into_iter()
            .take(max_items)
            .map(|(_, mem)| mem)
            .collect()
    }
}

/// `str(value)` for the message-content field: a JSON string yields its chars,
/// `None`/absent yields `""` (the `msg.get("content", "")` default), and any
/// other JSON type falls back to its rendering (closest faithful analogue).
fn str_of(v: Option<&Value>) -> String {
    match v {
        None | Some(Value::Null) => String::new(),
        Some(Value::String(s)) => s.clone(),
        Some(other) => other.to_string(),
    }
}

/// `any(word in haystack for word in needles)`.
fn any_contains(haystack: &str, needles: &[&str]) -> bool {
    needles.iter().any(|n| haystack.contains(n))
}

/// `int(value or 0)` for a stored `uses` field: falsy (null/0/false/empty
/// string) → 0; a number → its integer truncation; a numeric string → parsed.
fn py_int_or_zero(v: &Value) -> i64 {
    match v {
        Value::Null | Value::Bool(false) => 0,
        Value::Bool(true) => 1,
        Value::Number(n) => n.as_i64().unwrap_or_else(|| n.as_f64().unwrap_or(0.0) as i64),
        Value::String(s) => {
            if s.is_empty() {
                0
            } else {
                s.trim().parse::<i64>().unwrap_or(0)
            }
        }
        _ => 0,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tokenize_strips_listed_punctuation() {
        assert_eq!(tokenize("Hello, world!"), vec!["Hello", "world"]);
        // Quotes and semicolons are stripped from both ends; internal kept.
        assert_eq!(tokenize("\"a.b\";"), vec!["a.b"]);
    }

    #[test]
    fn text_similarity_edges() {
        assert_eq!(get_text_similarity("", "x"), 0.0);
        assert_eq!(get_text_similarity("x", ""), 0.0);
        // Identical token sets -> 1.0.
        assert_eq!(get_text_similarity("cat dog", "dog cat"), 1.0);
        // Half overlap: {a,b} vs {b,c} -> 1/3.
        let s = get_text_similarity("a b", "b c");
        assert!((s - 1.0 / 3.0).abs() < 1e-9);
    }

    #[test]
    fn inline_command_matches() {
        let mgr = MemoryManager { memory_file: String::new() };
        let (is_cmd, text) = mgr.process_inline_memory_command("Remember: buy milk");
        assert!(is_cmd);
        assert_eq!(text, "buy milk");
        let (is_cmd, text) = mgr.process_inline_memory_command("just chatting");
        assert!(!is_cmd);
        assert_eq!(text, "");
    }

    #[test]
    fn relevant_identity_query_includes_all_identity_memories() {
        let mgr = MemoryManager { memory_file: String::new() };
        let memories = vec![
            json!({"text": "My name is John Smith"}),
            json!({"text": "Likes coffee"}),
        ];
        let out = mgr.get_relevant_memories("who am i", &memories, 0.05, 8);
        // Identity memory scored 0.9 and sorts first.
        assert_eq!(out[0].get("text").and_then(|v| v.as_str()), Some("My name is John Smith"));
    }

    #[test]
    fn relevant_exact_phrase_floor() {
        let mgr = MemoryManager { memory_file: String::new() };
        // Use a query type that is NOT identity (so it scores via the other path).
        let memories = vec![json!({"text": "the deadline is friday afternoon"})];
        // "deadline" -> task query type; exact-substring floor lifts to >=0.8.
        let out = mgr.get_relevant_memories("deadline", &memories, 0.05, 8);
        assert_eq!(out.len(), 1);
    }

    #[test]
    fn add_entry_rejects_empty() {
        let mgr = MemoryManager { memory_file: String::new() };
        assert!(mgr.add_entry("   ", "user", "fact", None).is_err());
        let e = mgr.add_entry("hi", "user", "fact", Some("alice")).unwrap();
        assert_eq!(e.get("owner").and_then(|v| v.as_str()), Some("alice"));
        assert_eq!(e.get("uses").and_then(|v| v.as_i64()), Some(0));
    }

    #[test]
    fn extract_bullet_line_captures_inner_text() {
        // CHANGED: previously this test asserted the OLD broken behavior where a
        // bullet line raised (Python `None.strip()` AttributeError). The upstream
        // fix makes capture group 1 participate for the bullet branch too, so a
        // bullet line now extracts its inner text instead of raising. The test is
        // updated to assert the NEW corrected behavior.
        let mgr = MemoryManager { memory_file: String::new() };
        let history = vec![json!({
            "role": "assistant",
            "content": "- foo\n* bar\n1. baz\n• qux",
        })];
        let out = mgr.extract_memory_from_chat(&history, None).unwrap();
        assert_eq!(out.len(), 4);
        assert_eq!(out[0].get("text").and_then(|v| v.as_str()), Some("foo"));
        assert_eq!(out[1].get("text").and_then(|v| v.as_str()), Some("bar"));
        assert_eq!(out[2].get("text").and_then(|v| v.as_str()), Some("baz"));
        assert_eq!(out[3].get("text").and_then(|v| v.as_str()), Some("qux"));
    }

    #[test]
    fn extract_numbered_line_captures_text() {
        let mgr = MemoryManager { memory_file: String::new() };
        let history = vec![json!({"role": "assistant", "content": "1. first fact\n2. second"})];
        let out = mgr.extract_memory_from_chat(&history, Some("s1")).unwrap();
        assert_eq!(out.len(), 2);
        assert_eq!(out[0].get("text").and_then(|v| v.as_str()), Some("first fact"));
        assert_eq!(out[0].get("session_id").and_then(|v| v.as_str()), Some("s1"));
    }

    #[test]
    fn tokenize_filters_empty_tokens() {
        // A word made entirely of stripped punctuation yields no token.
        assert_eq!(tokenize("hello ... world"), vec!["hello", "world"]);
        assert!(tokenize(";;; ,,, ???").is_empty());
        // Whitespace-only / empty input -> no tokens.
        assert!(tokenize("   ").is_empty());
    }

    #[test]
    fn ensure_file_exists_creates_parent_dir() {
        // Use a fresh, non-existent nested temp path so the parent must be made.
        let base = std::env::temp_dir().join(format!("odysseus_mem_test_{}", uuid4()));
        let nested = base.join("a").join("b");
        let mgr = MemoryManager::new(&nested.to_string_lossy()).unwrap();
        // The parent dir was created and the file was written with "[]".
        assert!(nested.exists());
        assert!(std::path::Path::new(&mgr.memory_file).exists());
        assert_eq!(std::fs::read_to_string(&mgr.memory_file).unwrap(), "[]");
        // load_all on the fresh file yields an empty list.
        assert!(mgr.load_all().is_empty());
        // Cleanup.
        let _ = std::fs::remove_dir_all(&base);
    }
}
