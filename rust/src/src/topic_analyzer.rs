// src/topic_analyzer.rs  <- src/topic_analyzer.py
//! Topic analysis for conversations — deduplicated from app.py.
//! Used by /api/conversations/topics and /api/memory/extract fallback.

use once_cell::sync::Lazy;
use regex::Regex;
use serde_json::{json, Map, Value};
use std::collections::HashSet;

/// `TOPIC_KEYWORDS: Dict[str, List[str]]`.
pub static TOPIC_KEYWORDS: Lazy<Vec<(&'static str, Vec<&'static str>)>> = Lazy::new(|| {
    vec![
        ("Technology", vec!["ai", "machine learning", "python", "code", "programming", "computer", "software", "hardware", "algorithm"]),
        ("Science", vec!["science", "physics", "chemistry", "biology", "math", "mathematics", "research", "experiment"]),
        ("Work", vec!["work", "job", "career", "project", "task", "deadline", "meeting", "colleague", "manager"]),
        ("Personal", vec!["personal", "family", "friend", "relationship", "health", "wellness", "exercise", "diet"]),
        ("Learning", vec!["learn", "study", "education", "course", "tutorial", "guide", "how to", "explain"]),
        ("Creativity", vec!["write", "story", "create", "design", "art", "music", "draw", "paint"]),
        ("Planning", vec!["plan", "schedule", "organize", "arrange", "coordinate", "timeline", "calendar"]),
        ("Troubleshooting", vec!["error", "bug", "fix", "problem", "issue", "debug", "troubleshoot"]),
    ]
});

/// `re.split(r'[.!?]', ...)` — precompiled once.
static SENTENCE_SPLIT: Lazy<Regex> = Lazy::new(|| Regex::new(r"[.!?]").unwrap());

/// Word-boundary keyword matchers, mirroring Python's
/// `re.search(rf"\b{re.escape(kw)}\b", content)`. Precompiled once, indexed
/// parallel to [`TOPIC_KEYWORDS`] (`KEYWORD_PATTERNS[ti][ki]` ↔
/// `TOPIC_KEYWORDS[ti].1[ki]`). The Python pattern carries no `IGNORECASE`
/// flag — case-insensitivity comes purely from lowercasing the haystack, so
/// the keywords (all lowercase) match a lowercased `content`/`sentence` here
/// without a `(?i)` flag.
static KEYWORD_PATTERNS: Lazy<Vec<Vec<Regex>>> = Lazy::new(|| {
    TOPIC_KEYWORDS
        .iter()
        .map(|(_topic, keywords)| {
            keywords
                .iter()
                .map(|kw| Regex::new(&format!(r"\b{}\b", regex::escape(kw))).unwrap())
                .collect()
        })
        .collect()
});

/// The `session_manager` argument is duck-typed in the Python (any object that
/// exposes a `.sessions` mapping of `session_id -> session_data`). The faithful
/// translation models that mapping as `serde_json::Map<String, Value>`, matching
/// the dynamic-dict convention used elsewhere in the rewrite.
pub trait SessionManager {
    /// `session_manager.sessions`.
    fn sessions(&self) -> &Map<String, Value>;
}

/// Scan non-archived sessions and return topic frequency data.
/// If owner is set, only include sessions belonging to that user.
///
/// When `owner` is `None` or empty the helper returns an empty result. The
/// unauthenticated-loopback path in `app.py` produces a `None` owner, and
/// silently aggregating topic frequencies in that case is a cross-tenant data
/// leak. Callers that want a system-wide aggregate must pass an explicit
/// `owner` string (e.g. a documented "admin" pseudo-owner) or the route must
/// reject the request with 401.
///
/// Returns dict with "topics" list and "total_topics" count.
pub fn analyze_topics(session_manager: &dyn SessionManager, owner: Option<&str>) -> Value {
    // SECURITY: `if not owner: return {"topics": [], "total_topics": 0}`.
    // A None/empty owner is the unauthenticated-loopback path in app.py;
    // silently aggregating topic frequencies there is a cross-tenant data
    // leak, so bail out before touching any session.
    let owner = match owner.filter(|o| !o.is_empty()) {
        Some(o) => o,
        None => return json!({ "topics": [], "total_topics": 0 }),
    };

    // topic_counts: Dict[str, int] = {t: 0 for t in TOPIC_KEYWORDS}
    let mut topic_counts: Vec<(&'static str, i64)> =
        TOPIC_KEYWORDS.iter().map(|(t, _)| (*t, 0_i64)).collect();
    // topic_matches: Dict[str, list] = {t: [] for t in TOPIC_KEYWORDS}
    let mut topic_matches: Vec<(&'static str, Vec<Value>)> =
        TOPIC_KEYWORDS.iter().map(|(t, _)| (*t, Vec::<Value>::new())).collect();

    for (session_id, session_data) in session_manager.sessions().iter() {
        // if session_data.get("archived", False): continue
        if session_data.get("archived").and_then(|v| v.as_bool()).unwrap_or(false) {
            continue;
        }
        // SECURITY: strict ownership — unconditional now that the empty-owner
        // early return above guarantees a non-empty caller `owner`. Any session
        // whose owner does not match the caller is excluded; ownerless sessions
        // are never included (the early return already prevents an ownerless
        // caller from reaching this loop).
        // sess_owner = session_data.get("owner") or getattr(session_data, "owner", None)
        let sess_owner = session_data
            .get("owner")
            .and_then(|v| v.as_str())
            .filter(|s| !s.is_empty());
        if sess_owner != Some(owner) {
            continue;
        }

        let history = session_data
            .get("history")
            .and_then(|v| v.as_array())
            .cloned()
            .unwrap_or_default();
        for msg in history.iter() {
            // content_raw = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", None)
            let content_raw = msg.get("content");
            // if not content_raw: continue  (Python falsy: None, "", etc.)
            let content_raw = match content_raw {
                Some(c) if !is_falsy(c) => c,
                _ => continue,
            };

            // content = str(content_raw).lower()
            let content_str = value_to_str(content_raw);
            let content = content_str.to_lowercase();
            // role = msg.get("role") if isinstance(msg, dict) else getattr(msg, "role", "")
            let role = msg
                .get("role")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();
            // session_name = session_data.get("name", f"Session {session_id[:6]}")
            let session_name = session_data
                .get("name")
                .and_then(|v| v.as_str())
                .map(|s| s.to_string())
                .unwrap_or_else(|| format!("Session {}", py_slice6(session_id)));

            for (ti, (_topic, keywords)) in TOPIC_KEYWORDS.iter().enumerate() {
                for (ki, kw) in keywords.iter().enumerate() {
                    // if re.search(rf"\b{re.escape(kw)}\b", content):
                    if KEYWORD_PATTERNS[ti][ki].is_match(&content) {
                        topic_counts[ti].1 += 1;
                        // sentences = re.split(r'[.!?]', str(content_raw))
                        let sentences = SENTENCE_SPLIT.split(&content_str);
                        for sentence in sentences {
                            // if re.search(rf"\b{re.escape(kw)}\b", sentence.lower()):
                            if KEYWORD_PATTERNS[ti][ki].is_match(&sentence.to_lowercase()) {
                                topic_matches[ti].1.push(json!({
                                    "session_id": session_id,
                                    "session_name": session_name,
                                    "role": role,
                                    "snippet": sentence.trim(),
                                    "keyword": kw,
                                }));
                                break;
                            }
                        }
                    }
                }
            }
        }
    }

    let mut results: Vec<Value> = Vec::new();
    for (ti, (topic, count)) in topic_counts.iter().enumerate() {
        if *count == 0 {
            continue;
        }
        let matches = &topic_matches[ti].1;
        // unique, seen = [], set()
        let mut unique: Vec<Value> = Vec::new();
        let mut seen: HashSet<String> = HashSet::new();
        for m in matches.iter() {
            let session_id = m.get("session_id").and_then(|v| v.as_str()).unwrap_or("");
            let snippet = m.get("snippet").and_then(|v| v.as_str()).unwrap_or("");
            // key = f"{m['session_id']}-{m['snippet'][:50]}"
            let key = format!("{}-{}", session_id, py_slice(snippet, 50));
            if !seen.contains(&key) {
                seen.insert(key);
                unique.push(m.clone());
            }
        }
        // session_count = len({m["session_id"] for m in unique})
        let session_count = unique
            .iter()
            .filter_map(|m| m.get("session_id").and_then(|v| v.as_str()).map(String::from))
            .collect::<HashSet<String>>()
            .len();
        results.push(json!({
            "topic": topic,
            "frequency": count,
            "examples": unique.iter().take(5).cloned().collect::<Vec<Value>>(),
            "session_count": session_count,
        }));
    }

    // results.sort(key=lambda x: x["frequency"], reverse=True)
    // Python's list.sort is stable; sort descending by frequency.
    results.sort_by(|a, b| {
        let fa = a.get("frequency").and_then(|v| v.as_i64()).unwrap_or(0);
        let fb = b.get("frequency").and_then(|v| v.as_i64()).unwrap_or(0);
        fb.cmp(&fa)
    });
    let total_topics = results.len();
    json!({ "topics": results, "total_topics": total_topics })
}

/// Python truthiness for the `if not content_raw` guard: `None`, empty string,
/// `0`, `false`, and empty containers are falsy.
fn is_falsy(v: &Value) -> bool {
    match v {
        Value::Null => true,
        Value::Bool(b) => !b,
        Value::Number(n) => n.as_f64().map(|f| f == 0.0).unwrap_or(false),
        Value::String(s) => s.is_empty(),
        Value::Array(a) => a.is_empty(),
        Value::Object(o) => o.is_empty(),
    }
}

/// `str(content_raw)` — for a JSON string this is the raw chars; for anything
/// else we fall back to the JSON rendering (closest faithful analogue).
fn value_to_str(v: &Value) -> String {
    match v {
        Value::String(s) => s.clone(),
        other => other.to_string(),
    }
}

/// `s[:6]` over Unicode scalar values (Python slices by code point).
fn py_slice6(s: &str) -> String {
    py_slice(s, 6)
}

/// `s[:n]` over Unicode scalar values.
fn py_slice(s: &str, n: usize) -> String {
    s.chars().take(n).collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    /// Minimal in-memory `SessionManager` for tests: just wraps the
    /// `session_id -> session_data` map the trait exposes.
    struct FakeManager {
        sessions: Map<String, Value>,
    }

    impl SessionManager for FakeManager {
        fn sessions(&self) -> &Map<String, Value> {
            &self.sessions
        }
    }

    fn manager(entries: Vec<(&str, Value)>) -> FakeManager {
        let mut sessions = Map::new();
        for (id, data) in entries {
            sessions.insert(id.to_string(), data);
        }
        FakeManager { sessions }
    }

    fn session(owner: Option<&str>, name: &str, contents: &[(&str, &str)]) -> Value {
        let history: Vec<Value> = contents
            .iter()
            .map(|(role, content)| json!({ "role": role, "content": content }))
            .collect();
        let mut obj = Map::new();
        if let Some(o) = owner {
            obj.insert("owner".to_string(), json!(o));
        }
        obj.insert("name".to_string(), json!(name));
        obj.insert("history".to_string(), json!(history));
        Value::Object(obj)
    }

    fn topics_of(v: &Value) -> Vec<(String, i64)> {
        v.get("topics")
            .and_then(|t| t.as_array())
            .unwrap()
            .iter()
            .map(|t| {
                (
                    t.get("topic").and_then(|x| x.as_str()).unwrap().to_string(),
                    t.get("frequency").and_then(|x| x.as_i64()).unwrap(),
                )
            })
            .collect()
    }

    // ---- Multi-tenant leak ------------------------------------------------

    #[test]
    fn none_owner_returns_empty_without_aggregating() {
        // A session full of keywords that WOULD match, but the unauthenticated
        // (None owner) path must short-circuit to an empty result.
        let mgr = manager(vec![(
            "s1",
            session(Some("alice"), "Chat", &[("user", "python code and algorithm")]),
        )]);
        let out = analyze_topics(&mgr, None);
        assert_eq!(out, json!({ "topics": [], "total_topics": 0 }));
    }

    #[test]
    fn empty_owner_returns_empty_without_aggregating() {
        let mgr = manager(vec![(
            "s1",
            session(Some("alice"), "Chat", &[("user", "python code")]),
        )]);
        let out = analyze_topics(&mgr, Some(""));
        assert_eq!(out, json!({ "topics": [], "total_topics": 0 }));
    }

    #[test]
    fn other_owners_sessions_are_never_aggregated() {
        let mgr = manager(vec![
            (
                "mine",
                session(Some("alice"), "Mine", &[("user", "python code")]),
            ),
            (
                "theirs",
                session(Some("bob"), "Theirs", &[("user", "python code python code")]),
            ),
            // Ownerless session must also be excluded for a non-empty caller.
            (
                "ownerless",
                session(None, "Ownerless", &[("user", "python code")]),
            ),
        ]);
        let out = analyze_topics(&mgr, Some("alice"));
        // Only "mine" contributes: Technology gets python(1) + code(1) = 2.
        let topics = topics_of(&out);
        assert_eq!(topics, vec![("Technology".to_string(), 2)]);
        assert_eq!(out.get("total_topics").and_then(|v| v.as_i64()), Some(1));
        // The example snippets must only ever reference alice's session.
        let examples = out["topics"][0]["examples"].as_array().unwrap();
        for ex in examples {
            assert_eq!(ex.get("session_id").and_then(|v| v.as_str()), Some("mine"));
        }
    }

    #[test]
    fn empty_string_owner_on_session_does_not_match_caller() {
        // `session_data.get("owner") or ...` makes an empty-string owner falsy;
        // such a session is treated as ownerless and excluded.
        let mut data = session(None, "EmptyOwner", &[("user", "python code")]);
        data.as_object_mut()
            .unwrap()
            .insert("owner".to_string(), json!(""));
        let mgr = manager(vec![("s1", data)]);
        let out = analyze_topics(&mgr, Some("alice"));
        assert_eq!(out, json!({ "topics": [], "total_topics": 0 }));
    }

    // ---- Word-boundary matching ------------------------------------------

    #[test]
    fn keyword_requires_word_boundaries_not_substring() {
        // "aist" / "aircraft" contain "ai" as a substring but NOT as a word.
        // "scientific" contains "science"? no — but "scientist" does not match
        // "science". Use words that embed keywords mid-token.
        let mgr = manager(vec![(
            "s1",
            session(
                Some("alice"),
                "Chat",
                &[(
                    "user",
                    // "aircraft" embeds "ai"; "biological" embeds "biology"? no.
                    // "subwork" embeds "work"; "planet" embeds "plan".
                    "the aircraft flew over the planet doing subwork",
                )],
            ),
        )]);
        let out = analyze_topics(&mgr, Some("alice"));
        // None of ai/work/plan should match as a whole word here.
        assert_eq!(out, json!({ "topics": [], "total_topics": 0 }));
    }

    #[test]
    fn keyword_matches_as_whole_word() {
        let mgr = manager(vec![(
            "s1",
            session(
                Some("alice"),
                "Chat",
                &[("user", "I plan to fix the bug at work using ai")],
            ),
        )]);
        let out = analyze_topics(&mgr, Some("alice"));
        let mut topics = topics_of(&out);
        topics.sort();
        // Technology: ai(1). Work: work(1). Planning: plan(1).
        // Troubleshooting: bug(1) + fix(1) = 2.
        assert!(topics.contains(&("Technology".to_string(), 1)));
        assert!(topics.contains(&("Work".to_string(), 1)));
        assert!(topics.contains(&("Planning".to_string(), 1)));
        assert!(topics.contains(&("Troubleshooting".to_string(), 2)));
    }

    #[test]
    fn multiword_keyword_matches_with_boundaries() {
        // "machine learning" (Technology) and "how to" (Learning) are
        // multi-word keywords; word boundaries wrap the whole phrase.
        let mgr = manager(vec![(
            "s1",
            session(
                Some("alice"),
                "Chat",
                &[("user", "machine learning explained: how to start")],
            ),
        )]);
        let out = analyze_topics(&mgr, Some("alice"));
        let topics = topics_of(&out);
        // Technology: machine learning(1). Learning: how to(1) + explain? "explained"
        // embeds "explain" but not as a whole word, so it must NOT count.
        assert!(topics.contains(&("Technology".to_string(), 1)));
        assert!(topics.contains(&("Learning".to_string(), 1)));
    }

    #[test]
    fn explained_does_not_match_explain_as_substring() {
        // Regression guard for the substring->word-boundary switch: the old
        // `.contains("explain")` would have matched "explained".
        let mgr = manager(vec![(
            "s1",
            session(Some("alice"), "Chat", &[("user", "it was explained well")]),
        )]);
        let out = analyze_topics(&mgr, Some("alice"));
        assert_eq!(out, json!({ "topics": [], "total_topics": 0 }));
    }

    #[test]
    fn archived_sessions_are_skipped() {
        let mut data = session(Some("alice"), "Archived", &[("user", "python code")]);
        data.as_object_mut()
            .unwrap()
            .insert("archived".to_string(), json!(true));
        let mgr = manager(vec![("s1", data)]);
        let out = analyze_topics(&mgr, Some("alice"));
        assert_eq!(out, json!({ "topics": [], "total_topics": 0 }));
    }
}
