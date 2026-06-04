// src/context_compactor.rs  <- src/context_compactor.py
//! context_compactor.py
//!
//! Auto-compacts conversation history when approaching context window limits.
//! Summarizes older messages via the same LLM, preserving key context.
//!
//! SCOPE: the full pipeline is translated here — `_sanitize_tool_messages` and
//! `trim_for_context` (pure logic + the module constants and self-summary
//! prompt), PLUS the async/LLM-bound `maybe_compact(...)` (summarizes the older
//! half of the conversation via `llm_call_async` + `resolve_endpoint("utility")`)
//! and the session-manager-bound `_update_session_history(...)` (swaps the live
//! session's history for `[summary] + recent`, via the `SessionPersistence`
//! manager when present, else a local update). `get_context_length` is the pure
//! known-windows lookup, so `maybe_compact` adds no hot-path network call.

use crate::core::models::{session_manager, ChatMessage, Session};
use crate::src::endpoint_resolver::resolve_endpoint;
use crate::src::llm_core::llm_call_async;
use crate::src::model_context::{estimate_tokens, get_context_length};
use crate::pylog as logger;
use indexmap::IndexMap;
use serde_json::{json, Map, Value};

pub const COMPACT_THRESHOLD: f64 = 0.85; // Trigger compaction at 85% of context window
pub const SUMMARY_MAX_TOKENS: i64 = 1024;
pub const SMALL_CONTEXT_LIMIT: i64 = 8192; // Models with context <= this get aggressive trimming

/// Cursor-style self-summarization prompt — produces structured, dense summaries.
pub const SELF_SUMMARY_SYSTEM_PROMPT: &str = r#"You are summarizing a conversation to preserve context after compaction. Produce a structured summary that lets the conversation continue seamlessly.

Use this format:

## Conversation Summary
**Turns summarized:** {count}  |  **Compactions so far:** {n}

### User Goal
One sentence describing what the user is trying to accomplish.

### What Was Done
- Bullet points of completed actions, decisions made, and key outputs
- Include specific file paths, function names, variable names, URLs, and config values
- Note any errors encountered and how they were resolved

### Current State
What is the system/code/task state right now? What was the last thing discussed?

### Pending / Next Steps
- What remains to be done
- Any open questions or blockers

### Key Context
- Important constraints, preferences, or decisions that must not be forgotten
- Specific values: model names, ports, paths, credentials references, versions

Keep the summary under 1000 tokens. Be dense — every token should carry information. Do not include pleasantries or meta-commentary."#;

/// `m.get(key)` Python truthiness: present AND non-empty/non-zero/non-false.
fn truthy(m: &Value, key: &str) -> bool {
    match m.get(key) {
        None | Some(Value::Null) => false,
        Some(Value::Bool(b)) => *b,
        Some(Value::Number(n)) => n.as_f64().map(|f| f != 0.0).unwrap_or(true),
        Some(Value::String(s)) => !s.is_empty(),
        Some(Value::Array(a)) => !a.is_empty(),
        Some(Value::Object(o)) => !o.is_empty(),
    }
}

/// `m.get("role")` as `&str` ("" when missing/non-string — only used for `==`).
fn role(m: &Value) -> &str {
    m.get("role").and_then(|v| v.as_str()).unwrap_or("")
}

/// `(m.get("content") or "")` as a string slice (non-string content -> "").
fn content_str(m: &Value) -> &str {
    m.get("content").and_then(|v| v.as_str()).unwrap_or("")
}

/// Drop orphaned `tool` messages and dangling assistant `tool_calls`.
///
/// OpenAI's API requires every `role:"tool"` message to immediately follow an
/// assistant message that carries `tool_calls` (or another tool message in the
/// same batch). Front-trimming the history can cut the assistant `tool_calls`
/// parent while keeping its tool responses; this pass repairs that.
pub fn _sanitize_tool_messages(msgs: &[Value]) -> Vec<Value> {
    // Pass 1: drop orphan tool messages.
    let mut cleaned: Vec<Value> = Vec::new();
    let mut in_batch = false; // right after an assistant tool_calls (or mid-batch)?
    for m in msgs {
        let r = role(m);
        if r == "tool" {
            if in_batch {
                cleaned.push(m.clone());
            }
            // else: orphan — drop
            continue;
        }
        in_batch = r == "assistant" && truthy(m, "tool_calls");
        cleaned.push(m.clone());
    }

    // Pass 2: drop assistant tool_calls messages that have NO following tool
    // response (dangling).
    let mut out: Vec<Value> = Vec::new();
    for i in 0..cleaned.len() {
        let mut m = cleaned[i].clone();
        if role(&m) == "assistant" && truthy(&m, "tool_calls") {
            let nxt = cleaned.get(i + 1);
            let followed_by_tool = nxt.map(|n| role(n) == "tool").unwrap_or(false);
            if !followed_by_tool {
                // Strip tool_calls so it's a plain assistant turn (preserving any
                // text content the model produced alongside the calls).
                if let Some(obj) = m.as_object_mut() {
                    obj.remove("tool_calls");
                }
                // if not (m.get("content") or "").strip(): continue
                if content_str(&m).trim().is_empty() {
                    continue; // nothing left worth keeping
                }
            }
        }
        out.push(m);
    }
    out
}

/// `estimate_tokens` over an owned message vec (the Python passes lists around).
fn est(msgs: &[Value]) -> i64 {
    estimate_tokens(msgs)
}

/// Trim system messages to fit within `context_length`.
///
/// For small-context models, progressively strips RAG/memory system messages
/// (keeping the preset system prompt) and older conversation turns. Reserves
/// space for the response.
pub fn trim_for_context(messages: &[Value], context_length: i64, reserve_tokens: i64) -> Vec<Value> {
    let mut budget = context_length - reserve_tokens;
    let used = est(messages);
    if used <= budget {
        return messages.to_vec();
    }

    logger::info(&format!(
        "Trimming messages: {used} tokens > {budget} budget (ctx={context_length})"
    ));

    // Separate system / protected / conversation. `_protected` messages count
    // toward budget but are never dropped.
    let mut system_msgs: Vec<Value> = Vec::new();
    let mut protected_msgs: Vec<Value> = Vec::new();
    let mut convo_msgs: Vec<Value> = Vec::new();
    for msg in messages {
        if truthy(msg, "_protected") {
            protected_msgs.push(msg.clone());
        } else if role(msg) == "system" {
            system_msgs.push(msg.clone());
        } else {
            convo_msgs.push(msg.clone());
        }
    }

    let protected_tokens = est(&protected_msgs);
    budget -= protected_tokens;

    // Keep first system msg (preset prompt), drop others (memory, RAG, memo).
    let mut essential_system: Vec<Value> = system_msgs.iter().take(1).cloned().collect();
    let extra_system: Vec<Value> = system_msgs.iter().skip(1).cloned().collect();

    // Try dropping extra system messages, then add back what fits.
    let trimmed = concat(&[&essential_system, &convo_msgs]);
    if est(&trimmed) <= budget {
        let mut result = essential_system.clone();
        for msg in &extra_system {
            let candidate = concat(&[&result, std::slice::from_ref(msg), &convo_msgs]);
            if est(&candidate) <= budget {
                result.push(msg.clone());
            } else {
                break;
            }
        }
        return _sanitize_tool_messages(&concat(&[&result, &protected_msgs, &convo_msgs]));
    }

    // Still too big — truncate the first system message (keep > 500 chars).
    if !essential_system.is_empty() {
        let sys_text = content_str(&essential_system[0]).to_string();
        if sys_text.chars().count() > 2000 {
            let head: String = sys_text.chars().take(2000).collect();
            essential_system[0] = json!({
                "role": "system",
                "content": format!("{head}\n[System prompt truncated for context limits]"),
            });
            let trimmed = concat(&[&essential_system, &convo_msgs]);
            if est(&trimmed) <= budget {
                return _sanitize_tool_messages(&concat(&[
                    &essential_system,
                    &protected_msgs,
                    &convo_msgs,
                ]));
            }
        }
    }

    // Still too big — drop older turns BUT protect the last 10.
    const PROTECT_RECENT: usize = 10;
    if convo_msgs.len() > PROTECT_RECENT {
        let split = convo_msgs.len() - PROTECT_RECENT;
        let mut old_msgs: Vec<Value> = convo_msgs[..split].to_vec();
        let recent_msgs: Vec<Value> = convo_msgs[split..].to_vec();
        while !old_msgs.is_empty()
            && est(&concat(&[&essential_system, &old_msgs, &recent_msgs])) > budget
        {
            old_msgs.remove(0); // pop(0)
        }
        convo_msgs = concat(&[&old_msgs, &recent_msgs]);
    } else {
        while !convo_msgs.is_empty() && est(&concat(&[&essential_system, &convo_msgs])) > budget {
            convo_msgs.remove(0); // pop(0)
        }
    }

    let result = _sanitize_tool_messages(&concat(&[&essential_system, &protected_msgs, &convo_msgs]));
    logger::info(&format!(
        "Trimmed to {} tokens ({} messages)",
        est(&result),
        result.len()
    ));
    result
}

/// `trim_for_context(messages, context_length)` with the Python default
/// `reserve_tokens=512`.
pub fn trim_for_context_default(messages: &[Value], context_length: i64) -> Vec<Value> {
    trim_for_context(messages, context_length, 512)
}

/// Concatenate message slices (the Python `a + b + c` list joins).
fn concat(parts: &[&[Value]]) -> Vec<Value> {
    let mut out = Vec::new();
    for p in parts {
        out.extend(p.iter().cloned());
    }
    out
}

/// `maybe_compact(session, endpoint_url, model, messages, headers)` — check
/// context usage and compact (LLM-summarize the older half) if above
/// `COMPACT_THRESHOLD`. Returns `(messages, context_length, was_compacted)`.
///
/// Faithful port of `src/context_compactor.py:175-273`. Below threshold (the
/// common case) returns the messages UNCHANGED, so wiring this into the chat
/// path never regresses normal conversations — only very long ones (relative to
/// the model's known context window) get summarized, matching Python.
pub async fn maybe_compact(
    session: &mut Session,
    endpoint_url: &str,
    model: &str,
    messages: &[Value],
    headers: &IndexMap<String, String>,
) -> (Vec<Value>, i64, bool) {
    let context_length = get_context_length(endpoint_url, model);
    let used = estimate_tokens(messages);
    // pct = (used / context_length) * 100 if context_length else 0
    let pct = if context_length != 0 {
        (used as f64 / context_length as f64) * 100.0
    } else {
        0.0
    };

    if pct < COMPACT_THRESHOLD * 100.0 {
        return (messages.to_vec(), context_length, false);
    }

    logger::info(&format!(
        "Context at {pct:.1}% ({used}/{context_length} tokens) — compacting"
    ));

    // Split into system preface and conversation (order-preserving).
    let system_msgs: Vec<Value> = messages.iter().filter(|m| role(m) == "system").cloned().collect();
    let convo_msgs: Vec<Value> = messages.iter().filter(|m| role(m) != "system").cloned().collect();

    if convo_msgs.len() < 4 {
        return (messages.to_vec(), context_length, false);
    }

    // Summarize the older half, keep the recent half.
    let split_point = convo_msgs.len() / 2;
    let older = &convo_msgs[..split_point];
    let recent = &convo_msgs[split_point..];

    // convo_text = "\n".join(f"{role.upper()}: {content[:2000]}" for m in older)
    let convo_text = older
        .iter()
        .map(|m| {
            let r = role(m).to_uppercase();
            let c: String = content_str(m).chars().take(2000).collect();
            format!("{r}: {c}")
        })
        .collect::<Vec<_>>()
        .join("\n");

    // Count prior compactions from existing summary system messages.
    let compaction_count = system_msgs
        .iter()
        .filter(|m| content_str(m).contains("[Conversation summary"))
        .count();

    // Use the utility model if configured, otherwise fall back to the session
    // model/endpoint. `resolve_endpoint("utility")` => (url?, model?, headers?).
    let (util_url, util_model, util_headers) = resolve_endpoint("utility", session.owner.as_deref());
    let util_url_truthy = util_url.as_deref().map(|u| !u.is_empty()).unwrap_or(false);
    // compact_url = util_url or endpoint_url ; compact_model = util_model or model
    let compact_url = match &util_url {
        Some(u) if !u.is_empty() => u.clone(),
        _ => endpoint_url.to_string(),
    };
    let compact_model = match &util_model {
        Some(m) if !m.is_empty() => m.clone(),
        _ => model.to_string(),
    };
    // compact_headers = util_headers if util_url else headers
    let compact_headers: IndexMap<String, String> = if util_url_truthy {
        util_headers.unwrap_or_default()
    } else {
        headers.clone()
    };

    // prompt = SELF_SUMMARY_SYSTEM_PROMPT.replace("{count}", len(older)).replace("{n}", count+1)
    let prompt = SELF_SUMMARY_SYSTEM_PROMPT
        .replace("{count}", &older.len().to_string())
        .replace("{n}", &(compaction_count + 1).to_string());
    let summary_messages = vec![
        json!({"role": "system", "content": prompt}),
        json!({"role": "user", "content": convo_text}),
    ];

    let summary = match llm_call_async(
        &compact_url,
        &compact_model,
        summary_messages,
        0.2,
        SUMMARY_MAX_TOKENS,
        compact_headers,
        30,
    )
    .await
    {
        Ok(s) => s,
        Err(e) => {
            // except Exception: log + return system_msgs + recent (drop older), no compaction flag.
            logger::error(&format!("Compaction summary failed: {e}"));
            return (concat(&[&system_msgs, recent]), context_length, false);
        }
    };

    let summary_msg = json!({
        "role": "system",
        "content": format!("[Conversation summary — earlier messages were compacted]\n{summary}"),
    });
    let compacted = concat(&[&system_msgs, &[summary_msg], recent]);

    // Persist the compaction to the live session history.
    _update_session_history(session, split_point, &summary);

    let new_used = estimate_tokens(&compacted);
    logger::info(&format!(
        "Compacted: {used} -> {new_used} tokens ({} messages summarized, {} kept)",
        older.len(),
        recent.len()
    ));

    (compacted, context_length, true)
}

/// `_update_session_history(session, split_point, summary)` — swap the in-memory
/// session history for `[summary] + recent`, persisting via the session manager
/// when available (else a local update). Faithful port of
/// `src/context_compactor.py:275-300`.
fn _update_session_history(session: &mut Session, split_point: usize, summary: &str) {
    // if split_point >= len(session.history): return
    if split_point >= session.history.len() {
        return;
    }
    // recent_history = session.history[split_point:]
    let recent_history: Vec<ChatMessage> = session.history[split_point..].to_vec();
    let mut metadata = Map::new();
    metadata.insert("compacted".to_string(), Value::Bool(true));
    metadata.insert(
        "summarized_count".to_string(),
        Value::Number((split_point as i64).into()),
    );
    let summary_msg = ChatMessage::new(
        "system",
        format!("[Conversation summary]\n{summary}"),
        Some(metadata),
    );
    let mut new_history = vec![summary_msg];
    new_history.extend(recent_history);

    // if manager and session.id: if manager.replace_messages(...): return
    if !session.id.is_empty() {
        if let Some(manager) = session_manager() {
            if manager.replace_messages(&session.id, new_history.clone()) {
                // The manager updated its stored copy; mirror it onto the live
                // `&mut Session` (in Python they are the same object).
                session.history = new_history;
                return;
            }
        }
    }
    // session.history = new_history
    session.history = new_history;
}

#[cfg(test)]
mod compaction_tests {
    use super::*;

    fn session_with_history(id: &str, history: Vec<ChatMessage>) -> Session {
        let n = history.len() as i64;
        Session {
            id: id.to_string(),
            name: "t".to_string(),
            endpoint_url: "http://127.0.0.1:1/v1/chat/completions".to_string(),
            model: "test-model".to_string(),
            rag: false,
            archived: false,
            headers: IndexMap::new(),
            history,
            owner: None,
            is_important: false,
            message_count: n,
        }
    }

    // The common case: a few short messages stay far below 85% of the context
    // window, so `maybe_compact` returns them UNCHANGED with `was_compacted=false`
    // and never reaches `resolve_endpoint` / `llm_call_async` (pure, no I/O).
    #[tokio::test]
    async fn maybe_compact_below_threshold_is_noop() {
        let mut sess = session_with_history("s1", vec![]);
        let url = "http://127.0.0.1:1/v1/chat/completions".to_string();
        let messages = vec![
            json!({"role": "system", "content": "be brief"}),
            json!({"role": "user", "content": "hi"}),
            json!({"role": "assistant", "content": "hello"}),
        ];
        let (out, ctx, was) =
            maybe_compact(&mut sess, &url, "test-model", &messages, &IndexMap::new()).await;
        assert!(!was, "below threshold => not compacted");
        assert_eq!(out, messages, "messages pass through unchanged");
        assert!(ctx > 0);
    }

    // `_update_session_history` swaps history for `[summary] + recent` with the
    // Python metadata (`compacted`, `summarized_count`). Empty id => the (absent)
    // manager is skipped and the local update is applied.
    #[test]
    fn update_session_history_swaps_to_summary_plus_recent() {
        let history = vec![
            ChatMessage::new("user", "m0", None),
            ChatMessage::new("assistant", "m1", None),
            ChatMessage::new("user", "m2", None),
            ChatMessage::new("assistant", "m3", None),
        ];
        let mut sess = session_with_history("", history);
        _update_session_history(&mut sess, 2, "THE SUMMARY");
        assert_eq!(sess.history.len(), 3); // [summary] + 2 recent
        assert_eq!(sess.history[0].role, "system");
        assert_eq!(sess.history[0].content, "[Conversation summary]\nTHE SUMMARY");
        let md = sess.history[0].metadata.as_ref().unwrap();
        assert_eq!(md.get("compacted"), Some(&Value::Bool(true)));
        assert_eq!(md.get("summarized_count"), Some(&Value::Number(2.into())));
        assert_eq!(sess.history[1].content, "m2");
        assert_eq!(sess.history[2].content, "m3");
    }

    #[test]
    fn update_session_history_noop_when_split_past_end() {
        let mut sess = session_with_history("", vec![ChatMessage::new("user", "only", None)]);
        _update_session_history(&mut sess, 5, "X");
        assert_eq!(sess.history.len(), 1);
        assert_eq!(sess.history[0].content, "only");
    }
}
