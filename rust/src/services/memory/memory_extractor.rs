// services/memory/memory_extractor.rs  <- services/memory/memory_extractor.py
//! Background auto-extraction of facts from chat conversations.
//! After each LLM response, this module sends the last few messages to the LLM
//! asking it to extract memorable facts, then stores them in both memory.json
//! and the vector index.
//!
//! Periodically audits all memories via LLM to consolidate duplicates,
//! rewrite vague entries, and remove junk.

use std::sync::atomic::{AtomicI64, Ordering};

use once_cell::sync::Lazy;
use regex::Regex;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

use crate::core::models::Session;
use crate::pylog as logger;
use crate::pyos as os;
use crate::services::memory::MemoryManager;
use crate::services::memory::MemoryVectorStore;

// ===========================================================================
// Tidy-state sidecar (audit short-circuit)
// ===========================================================================

/// Sidecar JSON next to memory.json that remembers the fingerprint of the last
/// successfully-audited state per owner. Lets the audit short-circuit when
/// nothing has changed since the previous tidy — running the LLM again on an
/// already-clean list was wasting 30-120s per call and occasionally timing out
/// on the second pass.
fn _tidy_state_path(memory_manager: &MemoryManager) -> String {
    os::path::join(
        &os::path::dirname(&memory_manager.memory_file),
        "memory_tidy_state.json",
    )
}

/// Stable hash of an owner's memories — order-independent, depends only on
/// id+text+category. Any add/edit/delete invalidates it.
fn _fingerprint_entries(entries: &[Value]) -> String {
    // items = sorted((str(id), text, category) for e in entries)
    let mut items: Vec<(String, String, String)> = entries
        .iter()
        .map(|e| {
            // str(e.get("id", "")) — Python stringifies whatever id is.
            let id = match e.get("id") {
                Some(Value::String(s)) => s.clone(),
                Some(Value::Null) | None => String::new(),
                Some(other) => other.to_string(),
            };
            let text = e.get("text").and_then(|v| v.as_str()).unwrap_or("").to_string();
            let category = e.get("category").and_then(|v| v.as_str()).unwrap_or("").to_string();
            (id, text, category)
        })
        .collect();
    items.sort();

    let mut h = Sha256::new();
    for (id, text, category) in &items {
        // "\x1f".join(triple) + "\x1e"
        let triple = format!("{id}\u{1f}{text}\u{1f}{category}\u{1e}");
        h.update(triple.as_bytes());
    }
    hex::encode(h.finalize())
}

fn _load_tidy_state(memory_manager: &MemoryManager) -> serde_json::Map<String, Value> {
    let path = _tidy_state_path(memory_manager);
    match std::fs::read_to_string(&path) {
        Ok(raw) => match serde_json::from_str::<Value>(&raw) {
            // return data if isinstance(data, dict) else {}
            Ok(Value::Object(map)) => map,
            _ => serde_json::Map::new(),
        },
        // FileNotFoundError / JSONDecodeError -> {}
        Err(_) => serde_json::Map::new(),
    }
}

fn _save_tidy_state(memory_manager: &MemoryManager, owner: Option<&str>, fingerprint: &str) {
    let path = _tidy_state_path(memory_manager);
    let mut state = _load_tidy_state(memory_manager);
    // state[owner or ""] = {"fingerprint": fingerprint}
    state.insert(
        owner.unwrap_or("").to_string(),
        json!({"fingerprint": fingerprint}),
    );
    // json.dump(state, f, indent=2)
    match serde_json::to_string_pretty(&Value::Object(state)) {
        Ok(serialized) => {
            if let Err(e) = std::fs::write(&path, serialized) {
                logger::warning(&format!("Could not persist tidy fingerprint: {e}"));
            }
        }
        Err(e) => logger::warning(&format!("Could not persist tidy fingerprint: {e}")),
    }
}

// ===========================================================================
// Prompts / constants
// ===========================================================================

const EXTRACT_SYSTEM_PROMPT: &str = concat!(
    "You are a memory extraction assistant. Analyze the conversation and extract ONLY ",
    "durable personal facts about the user that would be useful across many future conversations.\n\n",
    "Good examples: name, job title, city, family members, long-term projects, strong preferences.\n",
    "Bad examples: what they asked about today, temporary moods, generic statements, ",
    "things the assistant said, one-off tasks, opinions on the current topic.\n\n",
    "Rules:\n",
    "- MAX 2 facts per conversation — only the most important\n",
    "- Only extract facts the USER stated or clearly implied\n",
    "- Each fact must be a single short sentence (under 15 words)\n",
    "- If a fact is similar to something likely already known, skip it\n",
    "- If nothing durable was revealed, return []\n\n",
    "Return a JSON array of objects with 'text' and 'category' fields.\n",
    "Categories: 'identity', 'preference', 'fact', 'contact', 'project', 'goal'\n\n",
    "Return ONLY valid JSON, no markdown fences."
);

/// How many recent messages to include for extraction.
const CONTEXT_WINDOW: usize = 6;

const AUDIT_SYSTEM_PROMPT: &str = concat!(
    "You are a memory database curator. Be CONSERVATIVE: remove only TRUE ",
    "duplicates and clearly useless entries. Every distinct fact must survive. ",
    "When in doubt, KEEP the entry. Return the cleaned list.\n\n",
    "Rules:\n",
    "1. MERGE only entries that state the SAME fact in different words. If you ",
    "are not sure two entries are the same fact, KEEP BOTH.\n",
    "   Merge: 'User's name is Sam' + 'The user is called Sam' -> one.\n",
    "   Do NOT merge related-but-distinct facts: 'Likes Python' and 'Uses ",
    "Python at work' are DIFFERENT — keep both.\n",
    "2. REMOVE only entries that are genuinely worthless: about what the AI did ",
    "(not the user), empty, or meaningless. Do NOT drop a real fact just ",
    "because it seems minor or niche.\n",
    "3. Keep the original wording. Only lightly trim obvious redundancy — do ",
    "NOT aggressively rewrite or shorten.\n",
    "4. Preserve the 'id' of the entry you keep when merging.\n",
    "5. Never invent facts. When unsure, KEEP.\n\n",
    "Return a JSON array of objects with fields: id, text, category.\n",
    "Return ONLY valid JSON, no markdown fences."
);

/// Audit every N new memories added.
const AUDIT_INTERVAL: i64 = 5;

/// `_extractions_since_audit = 0` — module-level mutable counter. Python keeps a
/// single global int; the faithful Rust analogue is a process-global atomic.
static EXTRACTIONS_SINCE_AUDIT: Lazy<AtomicI64> = Lazy::new(|| AtomicI64::new(0));

// ===========================================================================
// Regexes for the fallback candidate extractor (compiled once)
// ===========================================================================

static WS_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"\s+").unwrap());
// ^(?:the|a|an)\s+  (case-insensitive)
static LEADING_ARTICLE_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"(?i)^(?:the|a|an)\s+").unwrap());
// https?://|@|[{}<>]
static URL_OR_SYMBOL_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"https?://|@|[{}<>]").unwrap());

static NAME_IS_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"(?i)\bmy name is\s+([A-Za-z][A-Za-z0-9 .'\-]{1,50})\b").unwrap());
static CALL_ME_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"(?i)\bcall me\s+([A-Za-z][A-Za-z0-9 .'\-]{1,50})\b").unwrap());
static LIVE_IN_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"(?i)\bi (?:live in|am from|'m from)\s+([^.!?\n]{2,80})").unwrap());
static PREFER_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"(?i)\bi (?:prefer|like|love|hate|do not like|don't like)\s+([^.!?\n]{4,100})")
        .unwrap()
});
static VISIT_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"(?i)\bi (?:(?:want|would like|plan|hope) to|wanna) (?:go|travel|move|visit) to\s+([^.!?\n]{2,80})",
    )
    .unwrap()
});

// ===========================================================================
// Pure helpers
// ===========================================================================

/// `_message_text` — extract the text from a message dict (str or list of
/// content blocks). Mirrors the Python `getattr`/`dict.get` duck typing: the
/// Rust messages are always `serde_json::Value` objects.
fn _message_text(message: &Value) -> String {
    match message.get("content") {
        Some(Value::String(s)) => s.trim().to_string(),
        Some(Value::Array(items)) => {
            let mut parts: Vec<String> = Vec::new();
            for item in items {
                if item.as_object().is_some() {
                    // str(item.get("text") or item.get("content") or "")
                    let text = item.get("text").and_then(|v| v.as_str());
                    let content = item.get("content").and_then(|v| v.as_str());
                    let chosen = text.filter(|s| !s.is_empty()).or(content).unwrap_or("");
                    parts.push(chosen.to_string());
                } else {
                    // str(item)
                    parts.push(match item {
                        Value::String(s) => s.clone(),
                        other => other.to_string(),
                    });
                }
            }
            // " ".join(p for p in parts if p).strip()
            parts
                .into_iter()
                .filter(|p| !p.is_empty())
                .collect::<Vec<_>>()
                .join(" ")
                .trim()
                .to_string()
        }
        _ => String::new(),
    }
}

/// `_message_role` — lower-cased role, or "" when absent.
fn _message_role(message: &Value) -> String {
    message
        .get("role")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_lowercase()
}

/// `_clean_memory_value` — normalize whitespace, strip wrapping punctuation,
/// drop a leading article, and reject empty / too-long / URL-or-symbol values.
fn _clean_memory_value(value: &str, max_len: usize) -> String {
    // re.sub(r"\s+", " ", value or "").strip(" .,!?:;\"'`“”‘’")
    let collapsed = WS_RE.replace_all(value, " ");
    let stripped: &str = collapsed.trim_matches(|c: char| {
        matches!(
            c,
            ' ' | '.' | ',' | '!' | '?' | ':' | ';' | '"' | '\'' | '`' | '“' | '”' | '‘' | '’'
        )
    });
    // re.sub(r"^(?:the|a|an)\s+", "", value, flags=re.I)
    let no_article = LEADING_ARTICLE_RE.replace(stripped, "");
    let cleaned = no_article.as_ref();
    // if not value or len(value) > max_len: return ""
    if cleaned.is_empty() || cleaned.chars().count() > max_len {
        return String::new();
    }
    // if re.search(r"https?://|@|[{}<>]", value): return ""
    if URL_OR_SYMBOL_RE.is_match(cleaned) {
        return String::new();
    }
    cleaned.to_string()
}

/// Extract obvious durable facts without relying on the LLM.
///
/// Deliberately narrow: the LLM remains the main extractor, but simple
/// identity/preference/goal statements should not silently vanish just because
/// the background model judged them too conversational.
fn _fallback_memory_candidates(messages: &[Value]) -> Vec<Value> {
    let mut candidates: Vec<Value> = Vec::new();
    let mut seen: std::collections::HashSet<String> = std::collections::HashSet::new();

    // Inner closure `add(text, category)`.
    let mut add = |text: &str, category: &str| {
        let text = _clean_memory_value(text, 120);
        if text.is_empty() {
            return;
        }
        let key = text.to_lowercase();
        if seen.contains(&key) {
            return;
        }
        seen.insert(key);
        candidates.push(json!({"text": text, "category": category}));
    };

    for msg in messages {
        if _message_role(msg) != "user" {
            continue;
        }
        let text = _message_text(msg);
        if text.is_empty() {
            continue;
        }

        if let Some(caps) = NAME_IS_RE.captures(&text) {
            let name = _clean_memory_value(&caps[1], 50);
            if !name.is_empty() {
                add(&format!("User's name is {name}."), "identity");
            }
        }

        if let Some(caps) = CALL_ME_RE.captures(&text) {
            let name = _clean_memory_value(&caps[1], 50);
            if !name.is_empty() {
                add(&format!("User wants to be called {name}."), "identity");
            }
        }

        if let Some(caps) = LIVE_IN_RE.captures(&text) {
            let place = _clean_memory_value(&caps[1], 80);
            if !place.is_empty() {
                add(&format!("User lives in {place}."), "identity");
            }
        }

        if let Some(caps) = PREFER_RE.captures(&text) {
            let preference = _clean_memory_value(&caps[1], 100);
            if !preference.is_empty() {
                add(&format!("User prefers {preference}."), "preference");
            }
        }

        if let Some(caps) = VISIT_RE.captures(&text) {
            let destination = _clean_memory_value(&caps[1], 80);
            if !destination.is_empty() {
                add(&format!("User wants to visit {destination}."), "goal");
            }
        }
    }

    // return candidates[:2]
    candidates.truncate(2);
    candidates
}

/// Check if `new_text` is too similar to any existing memory (Jaccard
/// similarity over whitespace-split lowercase tokens).
fn _is_text_duplicate(new_text: &str, existing: &[Value], threshold: f64) -> bool {
    let new_tokens: std::collections::HashSet<String> =
        new_text.to_lowercase().split_whitespace().map(str::to_string).collect();
    if new_tokens.is_empty() {
        return false;
    }
    for entry in existing {
        let old_text = entry.get("text").and_then(|v| v.as_str()).unwrap_or("");
        let old_tokens: std::collections::HashSet<String> =
            old_text.to_lowercase().split_whitespace().map(str::to_string).collect();
        if old_tokens.is_empty() {
            continue;
        }
        let intersection = new_tokens.intersection(&old_tokens).count();
        let union = new_tokens.union(&old_tokens).count();
        if union > 0 && (intersection as f64) / (union as f64) >= threshold {
            return true;
        }
    }
    false
}

/// Strip non-text content blocks from messages, mirroring the inline
/// `stripped_recent` build in the Python `extract_and_store`:
///
/// - String / scalar content is passed through unchanged.
/// - List content keeps only `{"type": "text", ...}` blocks. A message whose
///   list content had blocks but none of them text is DROPPED entirely
///   (`if not text_only and content: continue`).
fn _strip_media(recent: &[Value]) -> Vec<Value> {
    let mut stripped: Vec<Value> = Vec::new();
    for msg in recent {
        let role = msg.get("role").cloned().unwrap_or(Value::Null);
        // content = msg.get("content", "")
        let content = msg.get("content").cloned().unwrap_or_else(|| json!(""));
        let content = match content {
            Value::Array(blocks) => {
                // Filter out multimodal blocks that aren't text.
                let text_only: Vec<Value> = blocks
                    .iter()
                    .filter(|b| {
                        b.as_object()
                            .and_then(|o| o.get("type"))
                            .and_then(|t| t.as_str())
                            == Some("text")
                    })
                    .cloned()
                    .collect();
                // if not text_only and content: continue
                if text_only.is_empty() && !blocks.is_empty() {
                    continue;
                }
                Value::Array(text_only)
            }
            other => other,
        };
        stripped.push(json!({"role": role, "content": content}));
    }
    stripped
}

// ===========================================================================
// extract_and_store
// ===========================================================================

/// Extract facts from recent conversation and store them.
///
/// Designed to run as a background task. Errors are logged, never raised
/// (mirrors the Python top-level `try/except`).
///
/// `memory_vector` is `&mut Option<MemoryVectorStore>` rather than the Python
/// positional: the canonical `MemoryVectorStore::add`/`find_similar` are async,
/// and the audit it triggers calls `rebuild(&mut self)`. Taking `&mut` keeps the
/// store mutable through the whole call chain without an interior-mutability
/// shim. `headers: Option<...>` mirrors `Optional[dict] = None`.
pub async fn extract_and_store(
    session: &Session,
    memory_manager: &MemoryManager,
    memory_vector: &mut Option<MemoryVectorStore>,
    endpoint_url: &str,
    model: &str,
    headers: Option<indexmap::IndexMap<String, String>>,
) {
    if let Err(e) = _extract_and_store_inner(
        session,
        memory_manager,
        memory_vector,
        endpoint_url,
        model,
        headers,
    )
    .await
    {
        logger::error(&format!("Memory extraction failed: {e}"));
    }
}

async fn _extract_and_store_inner(
    session: &Session,
    memory_manager: &MemoryManager,
    memory_vector: &mut Option<MemoryVectorStore>,
    endpoint_url: &str,
    model: &str,
    headers: Option<indexmap::IndexMap<String, String>>,
) -> crate::error::PyResult<()> {
    use crate::src::llm_core::llm_call_async;

    if endpoint_url.is_empty() || model.is_empty() {
        logger::debug("[memory-extract] No model or URL provided, skipping");
        return Ok(());
    }

    // Get last N messages from session.
    let messages = session.get_context_messages();
    let recent: Vec<Value> = if messages.len() > CONTEXT_WINDOW {
        messages[messages.len() - CONTEXT_WINDOW..].to_vec()
    } else {
        messages
    };

    if recent.len() < 2 {
        // Need at least a user message and assistant response.
        return Ok(());
    }

    // Strip media (images/audio) from messages — background memory extraction
    // only needs the text. The VL-generated descriptions are already in the
    // text content of the messages. This avoids sending image tokens to
    // non-vision models and prevents accidental "vision grounding" triggers.
    let stripped_recent = _strip_media(&recent);

    if stripped_recent.is_empty() {
        return Ok(());
    }

    let fallback_facts = _fallback_memory_candidates(&stripped_recent);

    // extraction_messages = [system] + stripped_recent
    let mut extraction_messages: Vec<Value> =
        vec![json!({"role": "system", "content": EXTRACT_SYSTEM_PROMPT})];
    extraction_messages.extend(stripped_recent.iter().cloned());

    let mut facts: Vec<Value> = Vec::new();
    // Inner try/except: LLM failure logs a warning but does NOT abort — the
    // fallback candidates can still be stored.
    match llm_call_async(
        endpoint_url,
        model,
        extraction_messages,
        0.1,
        500,
        headers.clone().unwrap_or_default(),
        // Python `extract_and_store` passes no `timeout=` to `llm_call_async`, so
        // it uses that function's default `LLMConfig.STREAM_TIMEOUT` (=300), NOT
        // `DEFAULT_TIMEOUT`. The Rust constant for that is `DEFAULT_STREAM_TIMEOUT`.
        crate::src::llm_core::LLMConfig::DEFAULT_STREAM_TIMEOUT as u64,
    )
    .await
    {
        Ok(raw) => {
            // Parse JSON (handle markdown fences if model wraps them).
            let mut text = raw.trim().to_string();
            if text.starts_with("```") {
                let after_first_nl = match text.split_once('\n') {
                    Some((_, rest)) => rest,
                    None => text.as_str(),
                };
                let before_last_fence = match after_first_nl.rsplit_once("```") {
                    Some((head, _)) => head,
                    None => after_first_nl,
                };
                text = before_last_fence.trim().to_string();
            }
            match serde_json::from_str::<Value>(&text) {
                // `if not isinstance(facts, list): facts = []` — only accept an
                // array; a non-array parse leaves `facts` empty.
                Ok(Value::Array(arr)) => facts = arr,
                Ok(_) => {}
                Err(_) => logger::debug("Memory extraction returned non-JSON"),
            }
        }
        Err(e) => logger::warning(&format!(
            "LLM memory extraction failed; using fallback candidates if available: {e}"
        )),
    }

    // facts = list(facts) + fallback_facts
    if !fallback_facts.is_empty() {
        facts.extend(fallback_facts);
    }

    if facts.is_empty() {
        logger::info("Auto memory extraction ran: 0 candidates");
        return Ok(());
    }

    // Get owner from session.
    let owner: Option<String> = session.owner.clone();

    let mut existing = memory_manager.load_all();
    let mut added: i64 = 0;

    for fact in &facts {
        let (fact_text, category): (String, String) = match fact {
            Value::String(s) => (s.clone(), "fact".to_string()),
            Value::Object(obj) => {
                let t = obj.get("text").and_then(|v| v.as_str()).unwrap_or("").trim().to_string();
                // fact.get("category", "fact") — a missing/non-string key falls
                // back to "fact".
                let c = obj
                    .get("category")
                    .and_then(|v| v.as_str())
                    .unwrap_or("fact")
                    .to_string();
                (t, c)
            }
            _ => continue,
        };

        if fact_text.is_empty() || fact_text.chars().count() < 5 {
            continue;
        }

        // Dedup: vector similarity first (fast), then exact text match.
        // A runtime embedding/vector failure must not abort the whole batch —
        // log and fall through to the text/fuzzy dedup below instead of losing
        // every validated fact extracted this session.
        if let Some(mv) = memory_vector.as_ref() {
            if mv.healthy() {
                let existing_id = match mv.find_similar(&fact_text, 0.72).await {
                    Ok(id) => id,
                    Err(e) => {
                        logger::warning(&format!(
                            "Memory dedup (vector) unavailable, using text fallback: {e}"
                        ));
                        None
                    }
                };
                if let Some(existing_id) = existing_id {
                    logger::debug(&format!(
                        "Memory dedup (vector): '{}' matches {existing_id}",
                        fact_text.chars().take(50).collect::<String>(),
                    ));
                    continue;
                }
            }
        }

        // Text dedup fallback: exact match + fuzzy similarity, scoped to the
        // owner's entries (+ legacy un-owned) when an owner is set.
        let user_existing: Vec<Value> = match &owner {
            Some(o) => existing
                .iter()
                .filter(|e| {
                    let eo = e.get("owner");
                    eo.and_then(|v| v.as_str()) == Some(o.as_str())
                        || matches!(eo, None | Some(Value::Null))
                })
                .cloned()
                .collect(),
            None => existing.clone(),
        };
        if !memory_manager.find_duplicates(&fact_text, Some(&user_existing)).is_empty() {
            continue;
        }
        // Fuzzy text similarity (catches rephrased duplicates when the vector
        // index is unavailable).
        if _is_text_duplicate(&fact_text, &user_existing, 0.6) {
            logger::debug(&format!(
                "Memory dedup (fuzzy): '{}' too similar to existing",
                fact_text.chars().take(50).collect::<String>(),
            ));
            continue;
        }

        let mut entry =
            memory_manager.add_entry(&fact_text, "auto", &category, owner.as_deref())?;
        // Auto-pin identity facts (name, job, location) — core context.
        if category == "identity" {
            if let Some(obj) = entry.as_object_mut() {
                obj.insert("pinned".to_string(), json!(true));
            }
        }
        // Python: `if hasattr(session, "session_id"): ... elif hasattr(session,
        // "name"): entry["session_id"] = session.name`. The `Session` dataclass
        // has NO `session_id` attribute but DOES have `name`, so the `elif`
        // branch always fires — stamp `session.name`. Preserved verbatim.
        if let Some(obj) = entry.as_object_mut() {
            obj.insert("session_id".to_string(), json!(session.name));
        }

        existing.push(entry.clone());

        // Add to vector index. The JSON store (saved below) is the source of
        // truth and the keyword path can still retrieve this entry, so a vector
        // write failure must not drop the fact or abort the remaining batch.
        if let Some(mv) = memory_vector.as_ref() {
            if mv.healthy() {
                let entry_id = entry.get("id").and_then(|v| v.as_str()).unwrap_or("");
                if let Err(e) = mv.add(entry_id, &fact_text).await {
                    logger::warning(&format!("Memory vector add failed for {entry_id}: {e}"));
                }
            }
        }

        added += 1;
    }

    if added > 0 {
        memory_manager.save(&mut existing)?;
        // fire_event("memory_added", _owner) per added (try/except ignored).
        for _ in 0..added {
            crate::src::event_bus::fire_event("memory_added", owner.as_deref());
        }
        logger::info(&format!("Auto-extracted {added} memories from session"));

        // global _extractions_since_audit; += added; audit at threshold.
        let total = EXTRACTIONS_SINCE_AUDIT.fetch_add(added, Ordering::SeqCst) + added;
        if total >= AUDIT_INTERVAL {
            EXTRACTIONS_SINCE_AUDIT.store(0, Ordering::SeqCst);
            logger::info("Audit threshold reached, running memory audit");
            audit_memories(
                memory_manager,
                memory_vector,
                endpoint_url,
                model,
                headers,
                owner.as_deref(),
            )
            .await;
        }
    } else {
        logger::info("Auto memory extraction ran: 0 added");
    }

    Ok(())
}

// ===========================================================================
// audit_memories
// ===========================================================================

/// Send all memories to the LLM for deduplication and consolidation.
///
/// - Merges near-duplicate entries
/// - Rewrites vague entries to be concise
/// - Removes junk / non-personal entries
/// - Rebuilds the vector index afterwards
///
/// Safe to call manually or from the automatic trigger in `extract_and_store`.
/// Errors are logged, never raised; returns the `{before, after, ...}` status
/// dict as a `serde_json::Value` (preserve_order), matching the Python.
pub async fn audit_memories(
    memory_manager: &MemoryManager,
    memory_vector: &mut Option<MemoryVectorStore>,
    endpoint_url: &str,
    model: &str,
    headers: Option<indexmap::IndexMap<String, String>>,
    owner: Option<&str>,
) -> Value {
    match _audit_memories_inner(memory_manager, memory_vector, endpoint_url, model, headers, owner)
        .await
    {
        Ok(result) => result,
        Err(e) => {
            logger::error(&format!("Memory audit failed: {e}"));
            json!({"error": e.to_string()})
        }
    }
}

async fn _audit_memories_inner(
    memory_manager: &MemoryManager,
    memory_vector: &mut Option<MemoryVectorStore>,
    endpoint_url: &str,
    model: &str,
    headers: Option<indexmap::IndexMap<String, String>>,
    owner: Option<&str>,
) -> crate::error::PyResult<Value> {
    use crate::src::llm_core::llm_call_async;

    let existing = memory_manager.load(owner);
    if existing.is_empty() {
        logger::info("Memory audit: nothing to audit");
        return Ok(json!({"before": 0, "after": 0}));
    }

    let before_count = existing.len() as i64;

    // Short-circuit when this exact set of memories was already audited.
    let current_fp = _fingerprint_entries(&existing);
    let tidy_state = _load_tidy_state(memory_manager);
    let last_fp = tidy_state
        .get(owner.unwrap_or(""))
        .and_then(|v| v.as_object())
        .and_then(|m| m.get("fingerprint"))
        .and_then(|v| v.as_str());
    if last_fp == Some(current_fp.as_str()) {
        logger::info("Memory audit: state unchanged since last tidy — skipping LLM");
        return Ok(json!({
            "before": before_count,
            "after": before_count,
            "already_tidy": true,
        }));
    }

    // Build payload: list of {id, text, category} for the LLM.
    let memory_payload: Vec<Value> = existing
        .iter()
        .map(|m| {
            json!({
                "id": m.get("id").cloned().unwrap_or(Value::Null),
                "text": m.get("text").cloned().unwrap_or(Value::Null),
                "category": m.get("category").and_then(|v| v.as_str()).unwrap_or("fact"),
            })
        })
        .collect();

    // json.dumps(memory_payload, ensure_ascii=False)
    let payload_json = serde_json::to_string(&Value::Array(memory_payload))
        .map_err(|e| crate::error::PyError::other(format!("serialize payload: {e}")))?;

    let audit_messages = vec![
        json!({"role": "system", "content": AUDIT_SYSTEM_PROMPT}),
        json!({"role": "user", "content": payload_json}),
    ];

    let raw = llm_call_async(
        endpoint_url,
        model,
        audit_messages,
        0.1,
        16384,
        headers.unwrap_or_default(),
        120,
    )
    .await?;

    // Parse the JSON list, tolerating reasoning-model noise: <think> blocks,
    // markdown fences, leading prose, and trailing commas.
    let mut text = raw.trim().to_string();
    text = THINK_BLOCK_RE.replace_all(&text, "").trim().to_string();

    let mut cleaned = _loads_list(&text);
    if cleaned.is_none() {
        if let Some(caps) = FENCE_RE.captures(&text) {
            cleaned = _loads_list(caps[1].trim());
        }
    }
    if cleaned.is_none() {
        // _a, _b = text.find('['), text.rfind(']'); slice if _a >= 0 and _b > _a
        if let (Some(a), Some(b)) = (text.find('['), text.rfind(']')) {
            if b > a {
                cleaned = _loads_list(&text[a..=b]);
            }
        }
    }
    let cleaned = match cleaned {
        Some(c) => c,
        None => {
            logger::error(&format!(
                "Memory audit returned non-JSON: {}",
                text.chars().take(300).collect::<String>(),
            ));
            return Ok(json!({"before": before_count, "after": before_count, "error": "bad_json"}));
        }
    };

    // Build lookup of original entries by id so we can preserve metadata.
    let mut originals: std::collections::HashMap<String, Value> = std::collections::HashMap::new();
    for m in &existing {
        if let Some(id) = m.get("id").and_then(|v| v.as_str()) {
            originals.insert(id.to_string(), m.clone());
        }
    }

    let mut final_entries: Vec<Value> = Vec::new();
    for item in &cleaned {
        let obj = match item.as_object() {
            Some(o) => o,
            None => continue,
        };
        let mid = obj.get("id").and_then(|v| v.as_str()).unwrap_or("");
        let new_text = obj.get("text").and_then(|v| v.as_str()).unwrap_or("").trim().to_string();
        if new_text.is_empty() {
            continue;
        }

        if let Some(original) = originals.get(mid) {
            // Preserve original metadata, update text + category.
            let mut entry = original.clone();
            if let Some(eobj) = entry.as_object_mut() {
                eobj.insert("text".to_string(), json!(new_text));
                // if item.get("category"): entry["category"] = item["category"]
                // (Python truthy: a non-empty value).
                if let Some(cat) = obj.get("category") {
                    if _truthy(cat) {
                        eobj.insert("category".to_string(), cat.clone());
                    }
                }
            }
            final_entries.push(entry);
        } else {
            // ID not found — skip to avoid inventing entries.
            logger::debug(&format!("Audit returned unknown id {mid}, skipping"));
            continue;
        }
    }

    let after_count = final_entries.len() as i64;

    // Safety net against catastrophic over-deletion. A conservative tidy should
    // never wipe out half the store in one pass.
    if before_count >= 8 && (after_count as f64) < (before_count as f64) * 0.5 {
        logger::warning(&format!(
            "Memory audit would cut {before_count} -> {after_count} \
             (>50% removed) — refusing as unsafe, keeping originals"
        ));
        return Ok(json!({
            "before": before_count,
            "after": before_count,
            "error": "unsafe_removal",
        }));
    }

    // Merge audited entries back with other users' entries.
    let saved_entries: Vec<Value> = if let Some(owner_str) = owner {
        let all_entries = memory_manager.load_all();
        let audited_ids: std::collections::HashSet<String> = final_entries
            .iter()
            .filter_map(|e| e.get("id").and_then(|v| v.as_str()).map(str::to_string))
            .collect();
        // other_entries: owner != this owner AND owner is not None.
        let mut other_entries: Vec<Value> = all_entries
            .iter()
            .filter(|e| {
                let eo = e.get("owner");
                eo.and_then(|v| v.as_str()) != Some(owner_str)
                    && !matches!(eo, None | Some(Value::Null))
            })
            .cloned()
            .collect();
        let other_ids: std::collections::HashSet<String> = other_entries
            .iter()
            .filter_map(|e| e.get("id").and_then(|v| v.as_str()).map(str::to_string))
            .collect();
        // Also keep legacy entries (owner is None) not part of this audit.
        for e in &all_entries {
            let is_legacy = matches!(e.get("owner"), None | Some(Value::Null));
            let id = e.get("id").and_then(|v| v.as_str()).unwrap_or("");
            if is_legacy && !audited_ids.contains(id) && !other_ids.contains(id) {
                other_entries.push(e.clone());
            }
        }
        // saved_entries = final_entries + other_entries
        let mut merged: Vec<Value> = final_entries.clone();
        merged.extend(other_entries);
        merged
    } else {
        // saved_entries = final_entries
        final_entries.clone()
    };
    let mut to_save = saved_entries.clone();
    memory_manager.save(&mut to_save)?;
    logger::info(&format!(
        "Memory audit complete: {before_count} -> {after_count} entries \
         ({} removed/merged)",
        before_count - after_count,
    ));

    // Rebuild vector index from the full saved set, not just this owner's
    // slice — otherwise the shared collection is wiped of every other owner's
    // entries until they happen to run their own audit.
    if let Some(mv) = memory_vector.as_mut() {
        if mv.healthy() {
            mv.rebuild(&saved_entries).await?;
        }
    }

    // Persist the post-tidy fingerprint so the next call short-circuits.
    _save_tidy_state(memory_manager, owner, &_fingerprint_entries(&final_entries));

    Ok(json!({"before": before_count, "after": after_count}))
}

// ===========================================================================
// JSON-list parse helpers (audit noise tolerance)
// ===========================================================================

// <think(?:ing)?>[\s\S]*?</think(?:ing)?>  (case-insensitive)
static THINK_BLOCK_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"(?is)<think(?:ing)?>.*?</think(?:ing)?>").unwrap());
// ```(?:json)?\s*\n?([\s\S]*?)```
static FENCE_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"(?s)```(?:json)?\s*\n?(.*?)```").unwrap());
// ,(\s*[}\]])  — trailing-comma repair
static TRAILING_COMMA_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r",(\s*[}\]])").unwrap());

/// `_loads_list(s)` — try parsing `s` as a JSON list, then retry after stripping
/// trailing commas. Returns `Some(list)` only when the result is an array.
fn _loads_list(s: &str) -> Option<Vec<Value>> {
    if s.is_empty() {
        return None;
    }
    let repaired = TRAILING_COMMA_RE.replace_all(s, "$1");
    for candidate in [s, repaired.as_ref()] {
        if let Ok(Value::Array(arr)) = serde_json::from_str::<Value>(candidate) {
            return Some(arr);
        }
    }
    None
}

/// Python truthiness for a JSON value (used for `if item.get("category")`).
fn _truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64().map(|f| f != 0.0).unwrap_or(true),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn fingerprint_is_order_independent() {
        let a = vec![
            json!({"id": "1", "text": "User likes Python", "category": "preference"}),
            json!({"id": "2", "text": "User is named Sam", "category": "identity"}),
        ];
        let b = vec![
            json!({"id": "2", "text": "User is named Sam", "category": "identity"}),
            json!({"id": "1", "text": "User likes Python", "category": "preference"}),
        ];
        assert_eq!(_fingerprint_entries(&a), _fingerprint_entries(&b));
        // A text edit invalidates it.
        let c = vec![
            json!({"id": "1", "text": "User loves Python", "category": "preference"}),
            json!({"id": "2", "text": "User is named Sam", "category": "identity"}),
        ];
        assert_ne!(_fingerprint_entries(&a), _fingerprint_entries(&c));
    }

    #[test]
    fn fingerprint_stable_hex() {
        // Single entry, known seps: sha256("1\x1ftext\x1fcat\x1e").
        let entries = vec![json!({"id": "1", "text": "text", "category": "cat"})];
        let fp = _fingerprint_entries(&entries);
        let mut h = Sha256::new();
        h.update("1\u{1f}text\u{1f}cat\u{1e}".as_bytes());
        assert_eq!(fp, hex::encode(h.finalize()));
    }

    #[test]
    fn clean_memory_value_strips_and_rejects() {
        // whitespace collapse + leading article + wrapping punctuation.
        assert_eq!(_clean_memory_value("  the   Sam.  ", 50), "Sam");
        // URL/symbol rejected.
        assert_eq!(_clean_memory_value("see https://x.com", 50), "");
        // too long rejected.
        assert_eq!(_clean_memory_value("abcdef", 3), "");
    }

    #[test]
    fn fallback_candidates_extract_identity_and_cap_two() {
        let msgs = vec![
            json!({"role": "user", "content": "Hi, my name is Sam and I live in Berlin."}),
            json!({"role": "user", "content": "I prefer dark mode for everything."}),
            json!({"role": "user", "content": "I want to visit Tokyo someday."}),
            json!({"role": "assistant", "content": "my name is Bot (should be ignored)"}),
        ];
        let cands = _fallback_memory_candidates(&msgs);
        // Capped to the first 2 candidates. NOTE: the `my name is` regex char
        // class includes spaces, so it greedily captures up to the sentence
        // boundary — verified byte-identical to the Python `_fallback_memory_
        // candidates` output (no trailing period: `_clean_memory_value` strips it).
        assert_eq!(cands.len(), 2);
        assert_eq!(cands[0]["text"], json!("User's name is Sam and I live in Berlin"));
        assert_eq!(cands[0]["category"], json!("identity"));
        assert_eq!(cands[1]["text"], json!("User lives in Berlin"));
    }

    #[test]
    fn is_text_duplicate_jaccard() {
        let existing = vec![json!({"text": "User likes Python a lot"})];
        // identical-ish -> dup at 0.6
        assert!(_is_text_duplicate("User likes Python a lot", &existing, 0.6));
        // disjoint -> not dup
        assert!(!_is_text_duplicate("completely different sentence here", &existing, 0.6));
    }

    #[test]
    fn loads_list_tolerates_trailing_comma() {
        assert_eq!(_loads_list("[1, 2, 3,]"), Some(vec![json!(1), json!(2), json!(3)]));
        // an object is not a list -> None
        assert_eq!(_loads_list("{\"a\": 1}"), None);
        assert_eq!(_loads_list(""), None);
    }

    #[test]
    fn strip_media_passes_strings_and_keeps_text_blocks() {
        let recent = vec![
            // Plain string content is passed through unchanged.
            json!({"role": "user", "content": "hello there"}),
            // Mixed list: keep only the text blocks, drop image/audio.
            json!({"role": "assistant", "content": [
                {"type": "text", "text": "a description"},
                {"type": "image_url", "image_url": {"url": "data:..."}},
                {"type": "text", "text": "more text"},
            ]}),
        ];
        let stripped = _strip_media(&recent);
        assert_eq!(stripped.len(), 2);
        assert_eq!(stripped[0], json!({"role": "user", "content": "hello there"}));
        assert_eq!(
            stripped[1],
            json!({"role": "assistant", "content": [
                {"type": "text", "text": "a description"},
                {"type": "text", "text": "more text"},
            ]})
        );
    }

    #[test]
    fn strip_media_drops_message_with_only_media_blocks() {
        let recent = vec![
            json!({"role": "user", "content": "keep me"}),
            // List content with NO text blocks -> message dropped entirely.
            json!({"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": "x"}},
            ]}),
            // Empty list content (`content` is falsy) -> kept with empty list.
            json!({"role": "user", "content": []}),
        ];
        let stripped = _strip_media(&recent);
        assert_eq!(stripped.len(), 2);
        assert_eq!(stripped[0], json!({"role": "user", "content": "keep me"}));
        // The empty-list message survives with an empty text-only list, because
        // `text_only` is empty BUT `content` is also empty/falsy.
        assert_eq!(stripped[1], json!({"role": "user", "content": []}));
    }

    #[test]
    fn message_text_handles_str_and_blocks() {
        assert_eq!(_message_text(&json!({"content": "  hi  "})), "hi");
        let blocks = json!({"content": [
            {"type": "text", "text": "alpha"},
            {"type": "image"},
            {"type": "text", "text": "beta"},
        ]});
        // Empty parts are filtered before the join, so it's a single space.
        assert_eq!(_message_text(&blocks), "alpha beta");
    }
}
