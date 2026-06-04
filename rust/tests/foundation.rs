//! Tests for the translated pure-logic foundation (core/ + supporting shims).
//! These mirror the behaviours the Python relies on.

use odysseus::core::atomic_io::{atomic_write_json, atomic_write_text};
use odysseus::core::auth::AuthManager;
use odysseus::core::exceptions::{LLMServiceError, SessionNotFoundError};
use odysseus::core::models::{ChatMessage, Session};
use odysseus::pyotp;
use odysseus::src::{app_helpers, secret_storage};
use serde_json::{json, Map, Value};

fn tmp_path(name: &str) -> (tempfile::TempDir, String) {
    let dir = tempfile::tempdir().unwrap();
    let p = dir.path().join(name).to_string_lossy().into_owned();
    (dir, p)
}

#[test]
fn atomic_write_json_roundtrip() {
    let (dir, path) = tmp_path("cfg.json");
    let data = json!({"a": 1, "b": [1, 2, 3], "c": "hi"});
    atomic_write_json(&path, &data, None).unwrap();

    let read = std::fs::read_to_string(&path).unwrap();
    let parsed: Value = serde_json::from_str(&read).unwrap();
    assert_eq!(parsed, data);
    // CPython json.dump default separators: ", " and ": ".
    assert!(read.contains("\"a\": 1"), "got: {read}");
    assert!(read.contains(", "), "got: {read}");

    // No temp file left behind.
    let leftovers: Vec<_> = std::fs::read_dir(dir.path())
        .unwrap()
        .filter_map(|e| e.ok())
        .filter(|e| e.file_name().to_string_lossy().contains(".tmp."))
        .collect();
    assert!(leftovers.is_empty());
}

#[test]
fn atomic_write_text_and_indent() {
    let (_dir, path) = tmp_path("note.txt");
    atomic_write_text(&path, "hello\nworld").unwrap();
    assert_eq!(std::fs::read_to_string(&path).unwrap(), "hello\nworld");

    let (_d2, p2) = tmp_path("indented.json");
    atomic_write_json(&p2, &json!({"x": 1}), Some(2)).unwrap();
    let s = std::fs::read_to_string(&p2).unwrap();
    assert!(s.contains("\n  \"x\": 1"), "indented form: {s}");
}

#[test]
fn auth_full_flow() {
    let (_dir, auth_path) = tmp_path("auth.json");
    let am = AuthManager::with_path(&auth_path);
    assert!(!am.is_configured());
    assert!(am.setup("Admin", "pw123")); // username is lowercased
    assert!(am.is_configured());
    assert!(!am.setup("other", "x")); // already configured
    assert!(am.is_admin("admin"));
    assert!(am.verify_password("admin", "pw123"));
    assert!(!am.verify_password("admin", "wrong"));

    // non-admin user + privilege defaults
    assert!(am.create_user("bob", "bobpw", false));
    assert!(!am.create_user("bob", "again", false)); // duplicate
    assert!(!am.is_admin("bob"));
    assert_eq!(am.get_privileges("bob").get("can_use_bash"), Some(&json!(false)));
    assert_eq!(am.get_privileges("admin").get("can_use_bash"), Some(&json!(true)));

    // session token lifecycle
    let tok = am.create_session("admin", "pw123").unwrap();
    assert!(am.validate_token(Some(&tok)));
    assert_eq!(am.get_username_for_token(Some(&tok)).as_deref(), Some("admin"));
    am.revoke_token(&tok);
    assert!(!am.validate_token(Some(&tok)));
    assert!(!am.validate_token(None));

    // change password
    assert!(am.change_password("admin", "pw123", "newpw"));
    assert!(am.verify_password("admin", "newpw"));

    // deleting a user revokes their live sessions
    let tok2 = am.create_session("bob", "bobpw").unwrap();
    assert!(am.validate_token(Some(&tok2)));
    assert!(am.delete_user("bob", "admin"));
    assert!(!am.validate_token(Some(&tok2)));

    // persisted to disk: a fresh manager reads it back
    let am2 = AuthManager::with_path(&auth_path);
    assert!(am2.is_admin("admin"));
    assert!(am2.verify_password("admin", "newpw"));
}

#[test]
fn auth_totp_flow() {
    let (_dir, auth_path) = tmp_path("auth.json");
    let am = AuthManager::with_path(&auth_path);
    assert!(am.setup("admin", "pw"));
    assert!(!am.totp_enabled("admin"));

    let secret = am.totp_generate_secret("admin").unwrap();
    let uri = am.totp_get_provisioning_uri("admin", &secret);
    assert!(uri.starts_with("otpauth://totp/Odysseus:admin?secret="));
    assert!(uri.contains(&format!("secret={secret}")));
    assert!(uri.contains("issuer=Odysseus"));

    // 2FA not yet enabled -> verify passes through.
    assert!(am.totp_verify("admin", "irrelevant"));

    let code = pyotp::TOTP::new(&secret).at(odysseus::pytime::time() as i64);
    assert!(am.totp_confirm_enable("admin", &code));
    assert!(am.totp_enabled("admin"));

    let code2 = pyotp::TOTP::new(&secret).at(odysseus::pytime::time() as i64);
    assert!(am.totp_verify("admin", &code2));

    assert!(!am.totp_disable("admin", "wrong"));
    assert!(am.totp_disable("admin", "pw"));
    assert!(!am.totp_enabled("admin"));
}

#[test]
fn totp_rfc6238_vectors() {
    // base32("12345678901234567890")
    let secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ";
    assert_eq!(pyotp::TOTP::new(secret).at(59), "287082");
    assert_eq!(pyotp::TOTP::new(secret).at(1111111109), "081804");
    assert_eq!(pyotp::TOTP::new(secret).at(1234567890), "005924");
}

#[test]
fn secret_storage_roundtrip() {
    let enc = secret_storage::encrypt("hunter2");
    assert!(enc.starts_with("enc:"));
    assert!(secret_storage::is_encrypted(&enc));
    assert_eq!(secret_storage::decrypt(&enc), "hunter2");
    // re-encrypting an already-encrypted value is a no-op
    assert_eq!(secret_storage::encrypt(&enc), enc);
    // legacy plaintext passes through decrypt unchanged
    assert_eq!(secret_storage::decrypt("plain"), "plain");
    // empty in / empty out
    assert_eq!(secret_storage::encrypt(""), "");
    assert_eq!(secret_storage::decrypt(""), "");
    assert!(!secret_storage::is_encrypted("plain"));
}

#[test]
fn search_ranking_prefers_news_over_sports_and_social() {
    // Port of tests/test_search_ranking.py against the translated ranking.rs.
    use odysseus::src::search::ranking::rank_search_results;
    let results = vec![
        json!({
            "title": "Chicago Stars fire GM Richard Feuz",
            "url": "https://www.reuters.com/sports/soccer/chicago-stars-fire-gm-richard-feuz--flm-2026-05-27/",
            "snippet": "The Chicago Stars fired their general manager.",
        }),
        json!({
            "title": "United States Eliminates Canada In Quarterfinals",
            "url": "https://sports.yahoo.com/articles/united-states-vs-canada-live-updates-170747222.html",
            "snippet": "United States eliminated Canada in hockey.",
        }),
        json!({
            "title": "Canada - AP News",
            "url": "https://apnews.com/hub/canada",
            "snippet": "Stay up to date on the latest Canada news coverage from AP News.",
        }),
        json!({
            "title": "CBC News - Canada",
            "url": "https://www.cbc.ca/news/canada",
            "snippet": "Your source for Canadian news in English.",
        }),
        json!({
            "title": "CTV News - Canada",
            "url": "https://www.ctvnews.ca/canada",
            "snippet": "Latest news, travel, politics, money, jobs and more.",
        }),
    ];
    let ranked = rank_search_results("Canada news today", results);
    let top3: Vec<&str> = ranked.iter().take(3).map(|r| r["url"].as_str().unwrap()).collect();
    assert!(top3.contains(&"https://apnews.com/hub/canada"), "top3={top3:?}");
    assert!(top3.contains(&"https://www.cbc.ca/news/canada"), "top3={top3:?}");
    assert!(top3.contains(&"https://www.ctvnews.ca/canada"), "top3={top3:?}");
    assert!(ranked
        .last()
        .unwrap()["url"]
        .as_str()
        .unwrap()
        .starts_with("https://sports.yahoo.com/"));
}

#[test]
fn search_query_enhancement() {
    use odysseus::src::search::query::{build_enhanced_query, enhance_query};
    // site: filter is extracted and re-appended.
    let (q, site) = enhance_query("best laptops site:example.com");
    assert_eq!(site.as_deref(), Some("example.com"));
    assert!(q.contains("site:example.com"), "q={q}");
    // time filter maps day->d and is appended.
    let q2 = build_enhanced_query("breaking news", Some("day"));
    assert!(q2.contains("after:d"), "q2={q2}");
}

#[test]
fn search_cache_key_is_sha256_hex() {
    use odysseus::src::search::cache::generate_cache_key;
    // sha256("hello") hex digest — matches Python hashlib.sha256(...).hexdigest().
    assert_eq!(
        generate_cache_key("hello"),
        "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
    );
}

#[test]
fn agent_tools_facade() {
    use odysseus::src::agent_tools::{
        get_mcp_manager, set_mcp_manager, ToolBlock, MAX_AGENT_ROUNDS, MAX_OUTPUT_CHARS, TOOL_TAGS,
        _truncate,
    };

    // Constants match the Python facade.
    assert_eq!(MAX_AGENT_ROUNDS, 20);
    assert_eq!(MAX_OUTPUT_CHARS, 10_000);

    // TOOL_TAGS membership (a few representatives + the cookbook additions).
    assert!(TOOL_TAGS.contains("bash"));
    assert!(TOOL_TAGS.contains("web_search"));
    assert!(TOOL_TAGS.contains("app_api"));
    assert!(TOOL_TAGS.contains("serve_model"));
    assert!(!TOOL_TAGS.contains("not_a_tool"));

    // ToolBlock namedtuple(tool_type, content).
    let b = ToolBlock::new("bash", "ls -la");
    assert_eq!(b.tool_type, "bash");
    assert_eq!(b.content, "ls -la");

    // _truncate: under the limit untouched; over the limit gets the suffix.
    assert_eq!(_truncate("short", MAX_OUTPUT_CHARS), "short");
    let big = "x".repeat(MAX_OUTPUT_CHARS as usize + 5);
    let t = _truncate(&big, MAX_OUTPUT_CHARS);
    assert!(t.starts_with(&"x".repeat(MAX_OUTPUT_CHARS as usize)));
    assert!(t.contains(&format!("... (truncated, {} chars total)", big.len())));

    // mcp manager global: None until set, then returns the same shared Arc.
    // The slot now holds the live `Arc<McpManager>` (not the old inert
    // `serde_json::Value`); `McpManager` is neither `PartialEq` nor `Debug`, so
    // identity is checked with `Arc::ptr_eq` against the instance we set.
    use odysseus::src::mcp_manager::McpManager;
    use std::sync::Arc;
    assert!(get_mcp_manager().is_none());
    let mgr = Arc::new(McpManager::new());
    set_mcp_manager(mgr.clone());
    let got = get_mcp_manager().expect("manager set");
    assert!(Arc::ptr_eq(&got, &mgr));
}

#[test]
fn request_models_pydantic_defaults_and_validators() {
    use odysseus::src::request_models::{ChatRequest, MemoryAddRequest, MemoryUpdateRequest};

    // message+session required; defaults fill the rest (use_web/use_research
    // False, attachments []). clean_message strips the message.
    let c: ChatRequest =
        serde_json::from_str(r#"{"message": "  hi  ", "session": "s1"}"#).unwrap();
    assert_eq!(c.message, "hi"); // stripped by clean_message validator
    assert_eq!(c.session, "s1");
    assert!(!c.use_web);
    assert!(!c.use_research);
    assert!(c.attachments.is_empty());
    assert_eq!(c.time_filter, None);

    // time_filter validator: valid kept, invalid snapped to None.
    let good: ChatRequest =
        serde_json::from_str(r#"{"message": "x", "session": "s", "time_filter": "week"}"#).unwrap();
    assert_eq!(good.time_filter.as_deref(), Some("week"));
    let bad: ChatRequest =
        serde_json::from_str(r#"{"message": "x", "session": "s", "time_filter": "decade"}"#).unwrap();
    assert_eq!(bad.time_filter, None);

    // Missing required field is a validation error (like pydantic).
    assert!(serde_json::from_str::<ChatRequest>(r#"{"message": "x"}"#).is_err());

    // MemoryAddRequest: category validator snaps invalid -> "fact"; default "fact".
    let m: MemoryAddRequest =
        serde_json::from_str(r#"{"text": "t", "category": "nonsense"}"#).unwrap();
    assert_eq!(m.category, "fact");
    assert_eq!(m.source, "user");
    let m2: MemoryAddRequest = serde_json::from_str(r#"{"text": "t"}"#).unwrap();
    assert_eq!(m2.category, "fact");
    let m3: MemoryAddRequest =
        serde_json::from_str(r#"{"text": "t", "category": "goal"}"#).unwrap();
    assert_eq!(m3.category, "goal");

    // MemoryUpdateRequest: pattern category rejects a non-matching value.
    assert!(serde_json::from_str::<MemoryUpdateRequest>(r#"{"text": "t", "category": "bad"}"#).is_err());
    let u: MemoryUpdateRequest =
        serde_json::from_str(r#"{"text": "t", "category": "task"}"#).unwrap();
    assert_eq!(u.category.as_deref(), Some("task"));
}

#[test]
fn atomic_write_json_ensure_ascii() {
    // CPython json.dump defaults to ensure_ascii=True -> \uXXXX escapes.
    let (_dir, path) = tmp_path("uni.json");
    atomic_write_json(&path, &json!({"name": "café ☕", "emoji": "😀"}), None).unwrap();
    let raw = std::fs::read_to_string(&path).unwrap();
    // ensure_ascii=True (byte-identical to CPython json.dumps). The on-disk file
    // holds the backslash-escape TEXT, not the characters. Build each needle from
    // char codes so the source contains no Unicode/escape ambiguity at all.
    let bs = '\u{5c}'; // a single backslash character
    let bmp = format!("caf{bs}u00e9 {bs}u2615"); // "café ☕" escaped
    let astral = format!("{bs}ud83d{bs}ude00"); // "😀" -> UTF-16 surrogate pair
    assert!(raw.is_ascii(), "file must be pure ASCII: {raw}");
    assert!(raw.contains(&bmp), "BMP escapes ({bmp}); got: {raw}");
    assert!(raw.contains(&astral), "astral surrogate pair ({astral}); got: {raw}");
    // Still valid JSON that round-trips back to the original characters.
    let parsed: Value = serde_json::from_str(&raw).unwrap();
    assert_eq!(parsed["name"], json!("café ☕"));
    assert_eq!(parsed["emoji"], json!("😀"));
}

#[test]
fn session_not_found_error_message() {
    // Python: super().__init__(f"Session '{session_id}' not found")
    let e = SessionNotFoundError::new("abc123");
    assert_eq!(e.to_string(), "Session 'abc123' not found");
    assert_eq!(e.session_id, "abc123");
    // LLMServiceError carries an optional status_code (core/exceptions.py).
    assert_eq!(LLMServiceError::new("boom").to_string(), "boom");
    assert_eq!(LLMServiceError::with_status_code("boom", 503).status_code, Some(503));
}

#[test]
fn read_if_exists_normalizes_newlines() {
    let (_dir, path) = tmp_path("crlf.txt");
    std::fs::write(&path, "line1\r\nline2\r\n").unwrap();
    // Python text mode -> "line1\nline2" after universal-newline + strip.
    assert_eq!(app_helpers::read_if_exists(&path), "line1\nline2");
    // Missing file -> "".
    assert_eq!(app_helpers::read_if_exists("/no/such/file/xyz"), "");
}

#[test]
fn inside_base_dir_symlinked_prefix_and_nonexistent_leaf() {
    // On macOS the tempdir lives under /var/folders, where /var is a symlink to
    // /private/var. Before the realpath fix, the not-yet-created leaf resolved
    // against the unresolved /var root while the base resolved to /private/var,
    // so a legitimately-inside path was wrongly rejected.
    let dir = tempfile::tempdir().unwrap();
    let base = dir.path().to_string_lossy().into_owned();
    let inner = format!("{base}/sub/leaf.txt"); // does not exist yet
    assert!(app_helpers::inside_base_dir(&base, &inner));
    // A path that escapes the base is rejected.
    let outside = format!("{base}/../evil.txt");
    assert!(!app_helpers::inside_base_dir(&base, &outside));
}

#[test]
fn pybuiltins_int_matches_python() {
    use odysseus::pybuiltins::int;
    assert_eq!(int("24"), 24);
    assert_eq!(int("  24 "), 24); // whitespace stripped
    assert_eq!(int("\t5\n"), 5);
    assert_eq!(int("1_000"), 1000); // underscore separators
    assert_eq!(int("-7"), -7);
}

#[test]
fn session_headers_preserve_insertion_order() {
    let mut s = Session::default();
    s.headers.insert("Z-First".into(), "1".into());
    s.headers.insert("A-Second".into(), "2".into());
    s.headers.insert("M-Third".into(), "3".into());
    let keys: Vec<&str> = s.headers.keys().map(|k| k.as_str()).collect();
    assert_eq!(keys, vec!["Z-First", "A-Second", "M-Third"]);
    // get("headers") emits a JSON object in the same insertion order.
    let v = s.get("headers", Value::Null);
    let emitted: Vec<&str> = v.as_object().unwrap().keys().map(|k| k.as_str()).collect();
    assert_eq!(emitted, vec!["Z-First", "A-Second", "M-Third"]);
}

#[test]
fn models_chatmessage_and_session() {
    let m = ChatMessage::new("user", "hi", None);
    let d = m.to_dict();
    assert_eq!(d.get("role"), Some(&json!("user")));
    assert_eq!(d.get("content"), Some(&json!("hi")));
    assert!(!d.contains_key("metadata"));

    let mut md = Map::new();
    md.insert("k".into(), json!(1));
    let m2 = ChatMessage::new("assistant", "yo", Some(md));
    assert!(m2.to_dict().contains_key("metadata"));

    // empty metadata is falsy in Python -> omitted
    let m3 = ChatMessage::new("user", "x", Some(Map::new()));
    assert!(!m3.to_dict().contains_key("metadata"));

    let mut s = Session {
        id: "s1".into(),
        name: "n".into(),
        endpoint_url: "u".into(),
        model: "m".into(),
        ..Default::default()
    };
    s.history.push(m.clone());
    assert_eq!(s.get_context_messages().len(), 1);
    assert_eq!(s.get("id", Value::Null), json!("s1"));
    assert_eq!(s.get("message_count", Value::Null), json!(0));
    assert_eq!(s.get("nope", json!("def")), json!("def"));
}
