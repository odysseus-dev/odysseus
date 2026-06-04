//! Tests for the translated `src::model_context`, ported verbatim from the
//! Python `tests/test_model_context.py` (the ground truth for this module).

use odysseus::src::model_context::{_is_local_endpoint, _lookup_known, estimate_tokens};
use serde_json::{json, Value};

// ---- TestIsLocalEndpoint ----

#[test]
fn is_local_endpoint_cases() {
    assert!(_is_local_endpoint("http://localhost:5000/v1/chat/completions"));
    assert!(_is_local_endpoint("http://127.0.0.1:8080/v1/chat/completions"));
    assert!(_is_local_endpoint("http://192.168.1.1:11434/v1/chat/completions"));
    assert!(_is_local_endpoint("http://10.0.0.5:8000/v1/chat/completions"));
    // 100.64.0.0/10 is the CGNAT range Tailscale uses.
    assert!(_is_local_endpoint("http://100.64.0.1:5000/v1/chat/completions"));
    // Remote providers.
    assert!(!_is_local_endpoint("https://api.openai.com/v1/chat/completions"));
    assert!(!_is_local_endpoint("https://api.anthropic.com/v1/messages"));
    // Degenerate inputs.
    assert!(!_is_local_endpoint(""));
    assert!(!_is_local_endpoint("not-a-url"));
}

// ---- TestEstimateTokens ----

#[test]
fn estimate_tokens_empty_list() {
    let empty: Vec<Value> = vec![];
    assert_eq!(estimate_tokens(&empty), 0);
}

#[test]
fn estimate_tokens_single_short_message() {
    let messages = vec![json!({"role": "user", "content": "Hello"})];
    // 4 overhead + int(5 * 0.3) = 4 + 1 = 5
    assert_eq!(estimate_tokens(&messages), 5);
}

#[test]
fn estimate_tokens_multiple_messages() {
    let messages = vec![
        json!({"role": "system", "content": "You are helpful."}), // 16 chars
        json!({"role": "user", "content": "Hi there"}),           // 8 chars
    ];
    // 4 + int(16*0.3) + 4 + int(8*0.3) = 4 + 4 + 4 + 2 = 14
    let expected = 4 + (16.0_f64 * 0.3) as i64 + 4 + (8.0_f64 * 0.3) as i64;
    assert_eq!(estimate_tokens(&messages), expected);
    assert_eq!(estimate_tokens(&messages), 14);
}

#[test]
fn estimate_tokens_multimodal_content_list() {
    let messages = vec![json!({
        "role": "user",
        "content": [
            {"type": "text", "text": "Describe this image"}, // 19 chars
            {"type": "image_url", "image_url": {"url": "data:..."}},
        ],
    })];
    // 4 overhead + int(19 * 0.3); image_url ignored
    assert_eq!(estimate_tokens(&messages), 4 + (19.0_f64 * 0.3) as i64);
    assert_eq!(estimate_tokens(&messages), 9);
}

#[test]
fn estimate_tokens_missing_content_key() {
    let messages = vec![json!({"role": "assistant"})];
    // 4 overhead + 0 content
    assert_eq!(estimate_tokens(&messages), 4);
}

#[test]
fn estimate_tokens_scales_with_length() {
    let short = estimate_tokens(&[json!({"role": "user", "content": "short"})]);
    let long = estimate_tokens(&[json!({"role": "user", "content": "a".repeat(10000)})]);
    assert!(long > short * 10);
}

// ---- TestLookupKnown ----

#[test]
fn lookup_known_cases() {
    assert_eq!(_lookup_known("claude-sonnet-4-5"), Some(200000));
    assert_eq!(_lookup_known("gpt-4o"), Some(128000));
    assert_eq!(_lookup_known("deepseek-r1"), Some(64000));
    assert_eq!(_lookup_known("gemini-2.5-pro"), Some(1048576));
    assert_eq!(_lookup_known("totally-unknown-model-xyz"), None);
    // Provider-prefixed models still match.
    assert_eq!(_lookup_known("openrouter/deepseek-r1"), Some(64000));
    // :free / :extended suffixes still match.
    assert_eq!(_lookup_known("deepseek-r1:free"), Some(64000));
}
