//! Ported verbatim from the Python `tests/test_endpoint_resolver.py`.

use odysseus::src::endpoint_resolver::{
    _first_chat_model, build_chat_url, build_headers, normalize_base,
};

fn s(v: &[&str]) -> Vec<String> {
    v.iter().map(|x| x.to_string()).collect()
}

// ---- TestNormalizeBase ----

#[test]
fn normalize_base_cases() {
    assert_eq!(
        normalize_base(Some("http://localhost:8000/v1/chat/completions")),
        "http://localhost:8000/v1"
    );
    assert_eq!(normalize_base(Some("http://localhost:8000/v1/models")), "http://localhost:8000/v1");
    assert_eq!(normalize_base(Some("http://localhost:8000/v1/")), "http://localhost:8000/v1");
    assert_eq!(normalize_base(Some("http://localhost:8000/v1")), "http://localhost:8000/v1");
    assert_eq!(normalize_base(Some("")), "");
    assert_eq!(normalize_base(None), ""); // None-safe
}

// ---- TestBuildChatUrl ----

#[test]
fn build_chat_url_cases() {
    assert_eq!(
        build_chat_url("http://localhost:8000/v1"),
        "http://localhost:8000/v1/chat/completions"
    );
    assert_eq!(build_chat_url("https://api.anthropic.com/v1"), "https://api.anthropic.com/v1/messages");
    assert!(build_chat_url("http://localhost:8000/v1").contains("/chat/completions"));
}

// ---- TestBuildHeaders ----

#[test]
fn build_headers_cases() {
    assert!(build_headers(None, "http://localhost:8000/v1").is_empty());

    let openai = build_headers(Some("sk-test123"), "http://localhost:8000/v1");
    assert_eq!(openai.get("Authorization").map(|s| s.as_str()), Some("Bearer sk-test123"));
    assert_eq!(openai.len(), 1);

    let ant = build_headers(Some("sk-ant-test"), "https://api.anthropic.com/v1");
    assert_eq!(ant.get("x-api-key").map(|s| s.as_str()), Some("sk-ant-test"));
    assert_eq!(ant.get("anthropic-version").map(|s| s.as_str()), Some("2023-06-01"));

    let unknown = build_headers(Some("key123"), "http://example.com/v1");
    assert_eq!(unknown.get("Authorization").map(|s| s.as_str()), Some("Bearer key123"));
    assert_eq!(unknown.len(), 1);

    // empty key -> {}
    assert!(build_headers(Some(""), "...").is_empty());
}

// ---- TestFirstChatModel ----

#[test]
fn first_chat_model_cases() {
    assert_eq!(
        _first_chat_model(&s(&["text-embedding-ada-002", "gpt-4", "gpt-3.5-turbo"])),
        Some("gpt-4".to_string())
    );
    // All embeddings — falls back to first.
    assert_eq!(
        _first_chat_model(&s(&["text-embedding-3-small", "text-embedding-3-large"])),
        Some("text-embedding-3-small".to_string())
    );
    assert_eq!(_first_chat_model(&[]), None);
    assert_eq!(_first_chat_model(&s(&["gpt-4"])), Some("gpt-4".to_string()));
}
