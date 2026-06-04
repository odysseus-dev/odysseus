//! Tests for the translated pure-logic parts of `src::context_compactor`:
//! `_sanitize_tool_messages` and `trim_for_context`.

use odysseus::src::context_compactor::{
    _sanitize_tool_messages, trim_for_context, trim_for_context_default,
};
use odysseus::src::model_context::estimate_tokens;
use serde_json::{json, Value};

#[test]
fn sanitize_drops_orphan_tool_messages() {
    // A `tool` message with no preceding assistant tool_calls is an orphan.
    let msgs = vec![
        json!({"role": "user", "content": "hi"}),
        json!({"role": "tool", "content": "orphan result"}),
        json!({"role": "assistant", "content": "hello"}),
    ];
    let out = _sanitize_tool_messages(&msgs);
    assert_eq!(out.len(), 2);
    assert!(out.iter().all(|m| m["role"] != json!("tool")));
}

#[test]
fn sanitize_keeps_valid_tool_batch() {
    let msgs = vec![
        json!({"role": "user", "content": "q"}),
        json!({"role": "assistant", "content": "", "tool_calls": [{"id": "c1"}]}),
        json!({"role": "tool", "content": "result", "tool_call_id": "c1"}),
        json!({"role": "assistant", "content": "final"}),
    ];
    let out = _sanitize_tool_messages(&msgs);
    // All four are valid: the tool message follows an assistant with tool_calls.
    assert_eq!(out.len(), 4);
}

#[test]
fn sanitize_strips_dangling_tool_calls() {
    // assistant has tool_calls but no following tool message, and no content:
    // it gets dropped entirely.
    let msgs = vec![
        json!({"role": "user", "content": "q"}),
        json!({"role": "assistant", "content": "", "tool_calls": [{"id": "c1"}]}),
    ];
    let out = _sanitize_tool_messages(&msgs);
    assert_eq!(out.len(), 1);
    assert_eq!(out[0]["role"], json!("user"));

    // ... but if it has text content, keep it minus the tool_calls key.
    let msgs2 = vec![
        json!({"role": "user", "content": "q"}),
        json!({"role": "assistant", "content": "thinking out loud", "tool_calls": [{"id": "c1"}]}),
    ];
    let out2 = _sanitize_tool_messages(&msgs2);
    assert_eq!(out2.len(), 2);
    assert!(out2[1].as_object().unwrap().get("tool_calls").is_none());
    assert_eq!(out2[1]["content"], json!("thinking out loud"));
}

#[test]
fn trim_returns_unchanged_when_fits() {
    let msgs = vec![
        json!({"role": "system", "content": "be nice"}),
        json!({"role": "user", "content": "hi"}),
    ];
    // Huge context -> nothing to trim.
    assert_eq!(trim_for_context(&msgs, 100000, 512), msgs);
    assert_eq!(trim_for_context_default(&msgs, 100000), msgs);
}

#[test]
fn trim_drops_extra_system_messages_first() {
    // Two system messages; a big convo. With a tight budget, the extra system
    // (RAG/memory) message is dropped before conversation turns.
    let preset = json!({"role": "system", "content": "PRESET"});
    let rag = json!({"role": "system", "content": "a".repeat(4000)}); // ~1200 tokens
    let mut msgs = vec![preset.clone(), rag.clone()];
    for i in 0..6 {
        msgs.push(json!({"role": "user", "content": format!("msg {i}")}));
    }
    // Budget big enough for preset + convo but not the giant RAG block.
    let convo_only: Vec<Value> = msgs.iter().filter(|m| m["content"] != rag["content"]).cloned().collect();
    let budget = estimate_tokens(&convo_only) + 50;
    let out = trim_for_context(&msgs, budget + 512, 512);
    // The preset survives; the RAG block is gone; conversation is intact.
    assert!(out.iter().any(|m| m["content"] == json!("PRESET")));
    assert!(!out.iter().any(|m| m["content"] == rag["content"]));
    assert!(out.iter().any(|m| m["content"] == json!("msg 5")));
}

#[test]
fn trim_protects_recent_messages() {
    // 30 conversation turns, tiny budget -> older turns dropped, recent kept.
    let mut msgs = vec![json!({"role": "system", "content": "sys"})];
    for i in 0..30 {
        msgs.push(json!({"role": "user", "content": format!("turn-{i:02} {}", "x".repeat(100))}));
    }
    let out = trim_for_context(&msgs, 600, 100);
    // The most recent turn must survive.
    assert!(out.iter().any(|m| m["content"].as_str().unwrap_or("").starts_with("turn-29")));
    // An early turn should have been dropped.
    assert!(!out.iter().any(|m| m["content"].as_str().unwrap_or("").starts_with("turn-00")));
}
