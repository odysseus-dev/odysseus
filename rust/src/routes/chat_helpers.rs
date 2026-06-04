// routes/chat_helpers.rs  <- routes/chat_helpers.py
//! Shared helpers for chat routes — context building, post-response tasks,
//! auth resolution.
//!
//! This is the HARD PREREQUISITE for `chat_routes` (the wave-5 CHAT port): a
//! pure-helpers + dataclasses module with no router. It reproduces
//! `build_chat_context` / `save_assistant_response` / `run_post_response_tasks`
//! / `_enforce_chat_privileges` / `_normalize_thinking` and the `PresetInfo` /
//! `ChatContext` / `PreprocessedMessage` dataclasses, binding to the already-
//! ported chat managers and shared helpers.
//!
//! ## Module gate (web + db)
//! `_enforce_chat_privileges` and `accumulate_token_usage` read the database
//! (the daily-message cap count over `chat_messages JOIN sessions`; the
//! per-session token totals on `sessions`), and `build_chat_context` reaches the
//! `ChatHandler`/`ChatProcessor` that live on the `web`-gated `AppState`. So,
//! like `session_routes`/`history_routes`, the whole module compiles on the
//! **web + db** profile. The pure regex helpers (`_normalize_thinking`,
//! `_extract_thinking_meta`, `clean_thinking_for_save`) are exercised by tests
//! under the same gate.
//!
//! ## `build_chat_context` — fully ported
//! Every step is translated faithfully, including the two formerly-deferred calls:
//!
//! * `normalize_model_id(sess.endpoint_url, sess.model)` — REAL via the NETWORK
//!   port `crate::src::llm_core::normalize_model_id_network` (probes the
//!   endpoint's `/models` with no auth headers + 30s timeout, anthropic
//!   short-circuit, exact-then-`basename` match), then `if norm: sess.model =
//!   norm`. (The pure-string `llm_core::normalize_model_id`/`list_model_ids`
//!   helpers kept those names; the network form is distinctly named.)
//! * `maybe_compact(...)` — REAL via `context_compactor::maybe_compact`
//!   (LLM-summarizes the older half once usage exceeds 85% of the model's known
//!   context window), then `trim_for_context`. Below threshold it returns the
//!   messages unchanged, so normal conversations are untouched.

use std::sync::Arc;

use serde_json::{json, Map, Value};

use crate::core::models::{ChatMessage, Session};
use crate::routes::prefs_store;

use crate::routes::HttpException;

// ── Data containers ──────────────────────────────────────────────────────── #

/// `@dataclass PresetInfo` — extracted preset parameters.
///
/// The ported `ChatHandler::validate_and_extract_preset` returns
/// `(f64, i64, Option<String>, String)` (temperature/max_tokens are non-optional
/// with defaults, character_name is a `String`). Python's dataclass types them
/// `Optional[float]` / `Optional[int]` / `Optional[str]`; we keep `Option`s here
/// to mirror the dataclass field types exactly and let the caller normalize the
/// always-present temperature/max_tokens into `Some`, and the empty
/// `character_name` into `None` (Python sets `char_name=""` -> the dataclass
/// stores `""`; an empty character name is falsy at every use site).
#[derive(Debug, Clone, Default, PartialEq)]
pub struct PresetInfo {
    pub temperature: Option<f64>,
    pub max_tokens: Option<i64>,
    pub system_prompt: Option<String>,
    pub character_name: Option<String>,
}

/// `@dataclass PreprocessedMessage` — result of `chat_handler.preprocess_message`.
///
/// `user_content` is the Python `Any` (str or multimodal list); the ported
/// `ChatHandler::preprocess_message` returns it as a `serde_json::Value` (a
/// JSON string for the text case, a JSON array for the multimodal case), so it
/// is carried as a `Value` here.
#[derive(Debug, Clone, PartialEq)]
pub struct PreprocessedMessage {
    pub enhanced_message: String,
    pub user_content: Value,
    pub text_for_context: String,
    pub youtube_transcripts: Vec<String>,
    pub attachment_meta: Vec<Value>,
}

/// `@dataclass ChatContext` — everything needed to call the LLM after
/// context-building.
///
/// `auto_opened_docs` defaults to an empty list (Python
/// `field(default_factory=list)`).
#[derive(Debug, Clone)]
pub struct ChatContext {
    pub preface: Vec<Value>,
    pub rag_sources: Vec<Value>,
    pub web_sources: Vec<Value>,
    pub used_memories: Vec<Value>,
    pub messages: Vec<Value>,
    pub context_length: i64,
    pub was_compacted: bool,
    pub user: Option<String>,
    pub uprefs: Value,
    pub preset: PresetInfo,
    pub preprocessed: PreprocessedMessage,
    pub auto_opened_docs: Vec<Value>,
}

// ── Helpers ──────────────────────────────────────────────────────────────── #

/// `needs_auto_name(name)` — check if a session still has its default/placeholder
/// name.
///
/// `not name` (empty) -> True; `"Chat:"`-prefix or exactly `"Chat"` -> True;
/// the default frontend name `"modelname HH:MM:SS AM/PM"` -> True.
pub fn needs_auto_name(name: &str) -> bool {
    use once_cell::sync::Lazy;
    use regex::Regex;
    // `re.match(r'^.+ \d{1,2}:\d{2}:\d{2}(\s*(AM|PM))?$', name, re.IGNORECASE)` —
    // `re.match` anchors at the start; the trailing `$` anchors the end. The
    // `(?i)` flag makes the AM/PM match case-insensitive, and the AM/PM suffix is
    // now OPTIONAL (24-hour default names like "llama3 22:42:07" also match).
    static DEFAULT_NAME_RE: Lazy<Regex> =
        Lazy::new(|| Regex::new(r"(?i)^.+ \d{1,2}:\d{2}:\d{2}(\s*(AM|PM))?$").unwrap());

    // `if not name: return True`
    if name.is_empty() {
        return true;
    }
    // `if name.startswith("Chat:") or name == "Chat": return True`
    if name.starts_with("Chat:") || name == "Chat" {
        return true;
    }
    // `if re.match(...): return True`
    DEFAULT_NAME_RE.is_match(name)
}

/// `extract_preset(chat_handler, preset_id)` — extract preset parameters via
/// `chat_handler`.
///
/// `validate_and_extract_preset` raises `HTTPException(400, "Invalid
/// preset_id…")` for an unknown id, so this returns a `Result` (the Python lets
/// the exception propagate out of the route).
pub fn extract_preset(
    chat_handler: &crate::src::chat_handler::ChatHandler<crate::src::upload_handler::UploadHandler>,
    preset_id: Option<&str>,
) -> Result<PresetInfo, HttpException> {
    let (temperature, max_tokens, system_prompt, char_name) =
        chat_handler.validate_and_extract_preset(preset_id)?;
    Ok(PresetInfo {
        temperature: Some(temperature),
        max_tokens: Some(max_tokens),
        system_prompt,
        // Python stores `char_name` verbatim (incl. `""`). Empty -> `None` so the
        // value is falsy at use sites (matches `if preset.character_name:`).
        character_name: if char_name.is_empty() {
            None
        } else {
            Some(char_name)
        },
    })
}

/// `add_user_message(sess, chat_handler, preprocessed, incognito)` — add the user
/// message to session history and update the session name.
///
/// PARITY NOTE — multimodal content: Python's `ChatMessage("user",
/// preprocessed.user_content, ...)` accepts `user_content` as either a string
/// **or** a multimodal list. The ported `ChatMessage.content` is a `String`, so
/// a list `user_content` is serialized to its JSON-array string form (the same
/// way `Session::get_context_messages` already renders content). The text case
/// is byte-identical; the multimodal case is the documented ported-model
/// constraint (the model layer stores content as text).
pub fn add_user_message(
    sess: &mut Session,
    chat_handler: &crate::src::chat_handler::ChatHandler<crate::src::upload_handler::UploadHandler>,
    preprocessed: &PreprocessedMessage,
    incognito: bool,
) {
    // `user_meta = {"attachments": ...} if preprocessed.attachment_meta else None`
    let user_meta: Option<Map<String, Value>> = if !preprocessed.attachment_meta.is_empty() {
        let mut m = Map::new();
        m.insert(
            "attachments".to_string(),
            Value::Array(preprocessed.attachment_meta.clone()),
        );
        Some(m)
    } else {
        None
    };
    // `sess.add_message(ChatMessage("user", preprocessed.user_content, metadata=user_meta))`
    let content = match &preprocessed.user_content {
        Value::String(s) => s.clone(),
        other => other.to_string(),
    };
    sess.add_message(ChatMessage::new("user", content, user_meta));
    // `if not incognito: chat_handler.update_session_name_if_needed(sess, text_for_context)`
    if !incognito {
        chat_handler.update_session_name_if_needed(sess, &preprocessed.text_for_context);
    }
}

/// `_enforce_chat_privileges(request, sess)` — apply the per-user privilege gates
/// (`allowed_models` + `max_messages_per_day`) that both `/api/chat` and
/// `/api/chat_stream` must enforce BEFORE any LLM work.
///
/// Raises `HTTPException(403)` if the session's model is not in the user's
/// allowlist, or `HTTPException(429)` if the user has hit their daily message
/// cap. No-op for unauthenticated callers (`user is None`). Admins receive
/// `ADMIN_PRIVILEGES` (empty `allowed_models` / zero cap), so it is a no-op for
/// them.
///
/// The `request.app.state.auth_manager` lookup becomes the `&AppState.auth`
/// handle; `get_current_user(request)` is the already-resolved `user` (the auth
/// gate's `CurrentUser`, threaded in by the caller). Single-user mode passes
/// `user = None` and short-circuits, exactly like the Python.
pub fn enforce_chat_privileges(
    auth: &crate::core::auth::AuthManager,
    user: Option<&str>,
    sess_model: &str,
) -> Result<(), HttpException> {
    // `try: user = get_current_user(request) except Exception: user = None`
    // `if not user: return`  (also covers an empty-string user)
    let user = match user {
        Some(u) if !u.is_empty() => u,
        _ => return Ok(()),
    };

    // `privs = auth_manager.get_privileges(user) or {}`
    let privs = auth.get_privileges(user);

    // `allowed = privs.get("allowed_models") or []`
    let allowed: Vec<&str> = privs
        .get("allowed_models")
        .and_then(|v| v.as_array())
        .map(|a| a.iter().filter_map(|v| v.as_str()).collect())
        .unwrap_or_default();
    // `if allowed and sess.model and sess.model not in allowed:`
    if !allowed.is_empty() && !sess_model.is_empty() && !allowed.contains(&sess_model) {
        return Err(HttpException::new(
            403,
            format!("Your account is not allowed to use model '{sess_model}'."),
        ));
    }

    // `cap = int(privs.get("max_messages_per_day") or 0)`
    let cap = privs
        .get("max_messages_per_day")
        .map(json_to_i64)
        .unwrap_or(0);
    // `if cap <= 0: return`
    if cap <= 0 {
        return Ok(());
    }

    // db.query(ChatMessage).join(Session, ...).filter(Session.owner == user,
    //   ChatMessage.role == "user",
    //   ChatMessage.timestamp >= utcnow() - timedelta(days=1)).count()
    let count = daily_user_message_count(user);
    // `if count >= cap: raise HTTPException(429, ...)`
    if count >= cap {
        return Err(HttpException::new(
            429,
            format!("Daily message limit reached ({cap}). Try again in 24 hours."),
        ));
    }
    Ok(())
}

/// Count the caller's `role == "user"` messages in the last 24h, scoped to the
/// sessions they own. Mirrors the SQLAlchemy `db.query(ChatMessage).join(
/// Session, ChatMessage.session_id == Session.id).filter(...).count()`.
///
/// DB errors degrade to `0` (the Python opens a fresh `SessionLocal()` and would
/// raise on a broken DB; here a count failure is treated as "no messages", which
/// can only ever loosen the cap, never tighten it — and the live DB is always
/// present on this profile).
fn daily_user_message_count(user: &str) -> i64 {
    let conn = match crate::core::database::session_local() {
        Ok(c) => c,
        Err(_) => return 0,
    };
    // `datetime.utcnow() - timedelta(days=1)` as the same naive-UTC ISO string
    // the schema stores `timestamp` in (DATETIME), so the `>=` string compare
    // matches SQLAlchemy's datetime comparison on this SQLite column.
    let cutoff = {
        use chrono::Duration;
        let dt = crate::pydatetime::utcnow_naive() - Duration::days(1);
        crate::pydatetime::naive_to_iso(dt)
    };
    conn.query_row(
        "SELECT COUNT(*) FROM chat_messages \
         JOIN sessions ON chat_messages.session_id = sessions.id \
         WHERE sessions.owner = ?1 AND chat_messages.role = 'user' \
           AND chat_messages.timestamp >= ?2",
        rusqlite::params![user, cutoff],
        |r| r.get::<_, i64>(0),
    )
    .unwrap_or(0)
}

/// `int(x or 0)` for a JSON privilege value — numbers truncate toward zero, a
/// truthy non-number falls back to `0` (the Python `int(...)` would raise on a
/// non-numeric, but privileges are always ints; this defends without crashing).
fn json_to_i64(v: &Value) -> i64 {
    if let Some(i) = v.as_i64() {
        i
    } else if let Some(f) = v.as_f64() {
        f as i64
    } else if let Some(s) = v.as_str() {
        s.trim().parse::<i64>().unwrap_or(0)
    } else {
        0
    }
}

/// `accumulate_token_usage(session_id, metrics)` — add input/output token counts
/// to the session's running totals.
///
/// `in_t = metrics.get("input_tokens", 0)`, `out_t = metrics.get("output_tokens",
/// 0)`; `if not (in_t or out_t): return`. The UPDATE adds onto the existing
/// `total_input_tokens` / `total_output_tokens` (coalescing `NULL -> 0`, matching
/// `(db_s.total_input_tokens or 0) + in_t`). Errors are swallowed (Python wraps
/// the body in `try/except: db.rollback()`).
pub fn accumulate_token_usage(session_id: &str, metrics: &Value) {
    let in_t = metrics
        .get("input_tokens")
        .map(json_to_i64)
        .unwrap_or(0);
    let out_t = metrics
        .get("output_tokens")
        .map(json_to_i64)
        .unwrap_or(0);
    // `if not (in_t or out_t): return`
    if in_t == 0 && out_t == 0 {
        return;
    }
    let conn = match crate::core::database::session_local() {
        Ok(c) => c,
        Err(_) => return,
    };
    // `(db_s.total_input_tokens or 0) + in_t` / same for output. COALESCE handles
    // the legacy NULL rows; the DEFAULT 0 covers fresh rows.
    let _ = conn.execute(
        "UPDATE sessions \
         SET total_input_tokens = COALESCE(total_input_tokens, 0) + ?1, \
             total_output_tokens = COALESCE(total_output_tokens, 0) + ?2 \
         WHERE id = ?3",
        rusqlite::params![in_t, out_t, session_id],
    );
}

// ── Thinking normalization (regex helpers) ───────────────────────────────── #

/// `_normalize_thinking(text)` — wrap inline thinking patterns in `<think>` tags
/// so they persist on reload.
///
/// Handles `"Thinking Process:"` (Qwen3.5), Gemma-style inline reasoning
/// (`"The user said/asked…"`, `"I should/need to…"`), and garbled `<think>` tags
/// (reasoning before the tag, unclosed tags).
///
/// The Python compiles `re` patterns with `[\s\S]` (DOTALL-style) and one
/// `(?=…)` lookahead; the `regex` crate supports `[\s\S]` directly, and the one
/// pattern with a lookahead is built with `fancy_regex`. Every `re.match` is
/// `^`-anchored, `re.search` is unanchored, mirroring Python exactly.
pub fn normalize_thinking(text: &str) -> String {
    use fancy_regex::Regex as FancyRegex;
    use once_cell::sync::Lazy;
    use regex::Regex;

    // `reasoning_prefix_re = re.compile(r'^\s*(?:thinking…|the user |…)', re.IGNORECASE)`
    static REASONING_PREFIX_RE: Lazy<Regex> = Lazy::new(|| {
        Regex::new(
            r"(?i)^\s*(?:thinking(?:\s+process)?\s*:|the user |i need |i should |i will |they are |the question |i can )",
        )
        .unwrap()
    });
    // `thinking_prefix_re = re.compile(r'^thinking(?:\s+process)?\s*:\s*', re.IGNORECASE)`
    static THINKING_PREFIX_RE: Lazy<Regex> =
        Lazy::new(|| Regex::new(r"(?i)^thinking(?:\s+process)?\s*:\s*").unwrap());
    // garbled: `re.match(r'^([\s\S]+?)\n*<think(?:ing)?>\s*([\s\S]*?)(?:</think(?:ing)?>)?\s*$', text, re.IGNORECASE)`
    static GARBLED_RE: Lazy<FancyRegex> = Lazy::new(|| {
        FancyRegex::new(
            r"(?is)^([\s\S]+?)\n*<think(?:ing)?>\s*([\s\S]*?)(?:</think(?:ing)?>)?\s*$",
        )
        .unwrap()
    });
    // Qwen clean-boundary: `re.match(r'^(Thinking…)(\n\n(?=[A-Z]|Hey|…))', text, re.IGNORECASE | re.MULTILINE)`
    static QWEN_BOUNDARY_RE: Lazy<FancyRegex> = Lazy::new(|| {
        FancyRegex::new(
            r"(?im)^(Thinking(?:\s+Process)?:[\s\S]*?)(\n\n(?=[A-Z]|Hey|Yo|Hi|Sure|I |What|Here|Let|The |This |OK|Ok|Yes|No |So |Well |Thank|Alright|Of course|Absolutely|Great|Hello|As ))",
        )
        .unwrap()
    });
    // `re.findall(r'["“]([^"”]{10,})["”]', text)`
    static LAST_QUOTE_RE: Lazy<Regex> =
        Lazy::new(|| Regex::new("[\"\u{201c}]([^\"\u{201d}]{10,})[\"\u{201d}]").unwrap());
    // Fallback reply-line guard: `re.match(r'^[\d*\-\s(]', line)`
    static REPLY_LINE_GUARD_RE: Lazy<Regex> =
        Lazy::new(|| Regex::new(r"^[\d*\-\s(]").unwrap());

    // `if not text: return text`
    if text.is_empty() {
        return text.to_string();
    }

    let reasoning_starts: [&str; 9] = [
        "The user ",
        "I need ",
        "I should ",
        "I will ",
        "They are ",
        "The question ",
        "I can ",
        "Thinking Process",
        "Thinking:",
    ];

    // Handle garbled <think> tags.
    if let Ok(Some(garbled)) = GARBLED_RE.captures(text) {
        let before = garbled.get(1).map(|m| m.as_str()).unwrap_or("").trim();
        let after = garbled.get(2).map(|m| m.as_str()).unwrap_or("").trim();
        // `stripped_before = before.lstrip()`
        let stripped_before = before.trim_start();
        // `if any(stripped_before.startswith(p) for p in reasoning_starts) or
        //     reasoning_prefix_re.match(stripped_before):`
        let looks_reasoning = reasoning_starts.iter().any(|p| stripped_before.starts_with(p))
            || REASONING_PREFIX_RE.is_match(stripped_before);
        if looks_reasoning {
            // `stripped_before = thinking_prefix_re.sub('', stripped_before)`
            let cleaned = THINKING_PREFIX_RE.replace(stripped_before, "");
            // `return '<think>' + stripped_before + '</think>\n' + after`
            return format!("<think>{cleaned}</think>\n{after}");
        }
    }

    // `if '<think' in text.lower(): return text`
    if text.to_lowercase().contains("<think") {
        return text.to_string();
    }

    // Qwen3.5: "Thinking Process:" / "Thinking:" prefix.
    if THINKING_PREFIX_RE.is_match(text.trim_start()) {
        // Try clean boundary first.
        if let Ok(Some(m)) = QWEN_BOUNDARY_RE.captures(text) {
            let g1 = m.get(1).map(|x| x.as_str()).unwrap_or("");
            // `think = thinking_prefix_re.sub('', m.group(1)).strip()`
            let think = THINKING_PREFIX_RE.replace(g1, "");
            let think = think.trim();
            // `return '<think>' + think + '</think>' + text[m.end()-2:]`
            let m_end = m.get(0).map(|x| x.end()).unwrap_or(0);
            let tail = byte_slice_from(text, m_end.saturating_sub(2));
            return format!("<think>{think}</think>{tail}");
        }
        // Fallback: find last non-indented paragraph as reply.
        let parts: Vec<&str> = text.split("\n\n").collect();
        for i in (1..parts.len()).rev() {
            let line = parts[i].trim();
            // `if line and not re.match(r'^[\d*\-\s(]', line) and len(line) > 5:`
            if !line.is_empty()
                && !REPLY_LINE_GUARD_RE.is_match(line)
                && line.chars().count() > 5
            {
                let joined_before = parts[..i].join("\n\n");
                let think = THINKING_PREFIX_RE.replace(&joined_before, "");
                let think = think.trim();
                let reply = parts[i..].join("\n\n");
                return format!("<think>{think}</think>\n\n{reply}");
            }
        }
        // Last resort: look for a quoted final response inside the thinking.
        let quotes: Vec<&str> = LAST_QUOTE_RE
            .captures_iter(text)
            .filter_map(|c| c.get(1).map(|m| m.as_str()))
            .collect();
        if let Some(last) = quotes.last() {
            let reply = last.trim();
            let think = THINKING_PREFIX_RE.replace(text, "");
            let think = think.trim();
            return format!("<think>{think}</think>\n\n{reply}");
        }
        // Truly no reply found.
        let think = THINKING_PREFIX_RE.replace(text, "");
        let think = think.trim();
        return format!("<think>{think}</think>");
    }

    // Gemma-style: starts with reasoning.
    let stripped_text = text.trim_start();
    // `first_line = stripped_text.split('\n')[0].strip()`
    let first_line = stripped_text.split('\n').next().unwrap_or("").trim();
    let reasoning_starts_gemma: [&str; 7] = [
        "The user ",
        "I need ",
        "I should ",
        "I will ",
        "They are ",
        "The question ",
        "I can ",
    ];
    // The Python `reply_starts` tuple — exactly these 20 entries (a `&[&str]`
    // slice so the count is not hand-maintained).
    let reply_starts: &[&str] = &[
        "Hey", "Hi ", "Hi!", "Hello", "Sure", "Yes", "No ", "No,", "Yo", "OK", "Here",
        "Absolutely", "Of course", "Great", "Alright", "Thanks", "Welcome", "Good ",
        "I'm happy", "I'd be",
    ];
    if reasoning_starts_gemma
        .iter()
        .any(|p| first_line.starts_with(p))
    {
        let lines: Vec<&str> = stripped_text.split('\n').collect();
        // Try line-by-line split first.
        for (i, line) in lines.iter().enumerate() {
            let stripped = line.trim();
            // `if not stripped: continue`
            if stripped.is_empty() {
                continue;
            }
            // `if i > 0 and any(stripped.startswith(p) for p in reply_starts):`
            if i > 0 && reply_starts.iter().any(|p| stripped.starts_with(p)) {
                let think = lines[..i].join("\n");
                let reply = lines[i..].join("\n");
                return format!("<think>{think}</think>\n{reply}");
            }
        }

        // Try within-line split — model mashed thinking + reply on one line.
        for p in reply_starts.iter() {
            // `pattern = r'([.!?])\s*(' + re.escape(p) + r')'`
            let pattern = format!(r"([.!?])\s*({})", regex::escape(p));
            let re = Regex::new(&pattern).unwrap();
            // `m = re.search(pattern, stripped_text)`
            if let Some(m) = re.find(stripped_text) {
                // `if m and m.start() > 20:` — at least 20 chars of reasoning before.
                // Python's `m.start()` is a CODEPOINT index; `re.find().start()` is a
                // BYTE offset, so a multibyte reasoning prefix would mis-trigger the
                // `> 20` gate. Convert the byte offset to a codepoint count before the
                // numeric compare. (The slices below stay on byte offsets — slicing at
                // the regex's own byte position is correct, and the `+ 1` re-includes
                // the matched `[.!?]` punctuation char, which is always ASCII.)
                let start_cp = stripped_text[..m.start()].chars().count();
                if start_cp > 20 {
                    // `think = stripped_text[:m.start() + 1]` — include the period.
                    let think = byte_slice_to(stripped_text, m.start() + 1);
                    // `reply = stripped_text[m.start() + 1:].lstrip()`
                    let reply = byte_slice_from(stripped_text, m.start() + 1).trim_start();
                    return format!("<think>{think}</think>\n{reply}");
                }
            }
        }

        // Last resort: find last non-reasoning line.
        for i in (1..lines.len()).rev() {
            let stripped = lines[i].trim();
            // `if stripped and not any(stripped.startswith(p) for p in reasoning_starts)
            //     and not stripped.startswith('*') and len(stripped) > 3:`
            if !stripped.is_empty()
                && !reasoning_starts_gemma
                    .iter()
                    .any(|p| stripped.starts_with(p))
                && !stripped.starts_with('*')
                && stripped.chars().count() > 3
            {
                let think = lines[..i].join("\n");
                let reply = lines[i..].join("\n");
                return format!("<think>{think}</think>\n{reply}");
            }
        }
    }

    text.to_string()
}

/// `_extract_thinking_meta(text)` — extract thinking content into metadata,
/// return `{thinking, reply, time}` or `None`.
pub fn extract_thinking_meta(text: &str) -> Option<Value> {
    use once_cell::sync::Lazy;
    use regex::Regex;

    // `re.search(r'<think(?:ing)?\s+time="([\d.]+)"', text)`
    static TIME_RE: Lazy<Regex> =
        Lazy::new(|| Regex::new(r#"<think(?:ing)?\s+time="([\d.]+)""#).unwrap());
    // `re.sub(r'<think(?:ing)?\s+time="[\d.]+"', '<think', text)`
    static TIME_SUB_RE: Lazy<Regex> =
        Lazy::new(|| Regex::new(r#"<think(?:ing)?\s+time="[\d.]+""#).unwrap());
    // `re.match(r'^[\s]*<think(?:ing)?>([\s\S]*?)</think(?:ing)?>\s*([\s\S]*)', clean, re.IGNORECASE)`
    static THINK_MATCH_RE: Lazy<Regex> = Lazy::new(|| {
        Regex::new(
            r"(?is)^[\s]*<think(?:ing)?>([\s\S]*?)</think(?:ing)?>\s*([\s\S]*)",
        )
        .unwrap()
    });

    // `if not text: return None`
    if text.is_empty() {
        return None;
    }

    // `think_time = time_match.group(1) if time_match else None`
    let think_time: Option<String> = TIME_RE
        .captures(text)
        .and_then(|c| c.get(1).map(|m| m.as_str().to_string()));
    // `clean = re.sub(r'<think…time="…"', '<think', text)`
    let clean = TIME_SUB_RE.replace_all(text, "<think");

    if let Some(c) = THINK_MATCH_RE.captures(&clean) {
        let thinking = c.get(1).map(|m| m.as_str()).unwrap_or("").trim();
        let reply = c.get(2).map(|m| m.as_str()).unwrap_or("").trim();
        // `if thinking and reply:` — only split when there's an actual reply.
        if !thinking.is_empty() && !reply.is_empty() {
            return Some(thinking_meta_value(thinking, reply, think_time.as_deref()));
        }
    }

    // Detect Thinking Process: or Gemma-style reasoning.
    let normalized = normalize_thinking(text);
    if normalized.contains("<think>") {
        if let Some(c) = THINK_MATCH_RE.captures(&normalized) {
            let thinking = c.get(1).map(|m| m.as_str()).unwrap_or("").trim();
            let reply = c.get(2).map(|m| m.as_str()).unwrap_or("").trim();
            if !thinking.is_empty() && !reply.is_empty() {
                return Some(thinking_meta_value(thinking, reply, think_time.as_deref()));
            }
        }
    }

    None
}

/// Build the `{thinking, reply, time}` dict `_extract_thinking_meta` returns.
/// `time` is `None` when no `time="…"` attr was present (Python stores the key
/// with a `None` value via `info.get("time")`; here we always include the key,
/// using JSON `null` for the absent case so callers reading `info["time"]` /
/// `info.get("time")` behave identically).
fn thinking_meta_value(thinking: &str, reply: &str, time: Option<&str>) -> Value {
    json!({
        "thinking": thinking,
        "reply": reply,
        "time": time,
    })
}

/// `clean_thinking_for_save(content, metadata)` — extract thinking from content
/// into metadata. Used by save paths that bypass `save_assistant_response`.
///
/// Returns `(reply, md)`. `md` is a copy of the supplied metadata (Python
/// `dict(metadata) if metadata else {}`) with `thinking` / `thinking_time` keys
/// added when a thinking block was found; `thinking_time` is only set when the
/// extracted info carried a non-null `time` (Python `if info.get("time"):`).
pub fn clean_thinking_for_save(
    content: &str,
    metadata: Option<&Map<String, Value>>,
) -> (String, Map<String, Value>) {
    let mut md = metadata.cloned().unwrap_or_default();
    if let Some(info) = extract_thinking_meta(content) {
        if let Some(t) = info.get("thinking") {
            md.insert("thinking".to_string(), t.clone());
        }
        // `if info.get("time"): md["thinking_time"] = info["time"]` — truthy only
        // (a `None`/`null` or empty string is skipped).
        if let Some(time) = info.get("time").and_then(|v| v.as_str()) {
            if !time.is_empty() {
                md.insert("thinking_time".to_string(), json!(time));
            }
        }
        let reply = info
            .get("reply")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        return (reply, md);
    }
    (content.to_string(), md)
}

/// `save_assistant_response(sess, session_manager, session_id, full_response,
/// last_metrics, **kwargs)` — add the assistant response to session history.
///
/// In incognito mode it keeps the in-memory context but skips DB persistence
/// (and returns `None` rather than a DB id). The keyword args map onto the
/// [`SaveAssistantOpts`] struct so the long Python signature stays readable.
///
/// Returns the persisted message's DB id when one is available (so the stream can
/// wire it onto the freshly-rendered bubble), else `None`. Incognito always
/// returns `None`.
///
/// PARITY NOTE — `_db_id`: Python reads `sess.history[-1].metadata.get("_db_id")`,
/// which the persistence layer stamps onto the stored message. The ported
/// `add_message` does not stamp a `_db_id` back onto the in-memory `ChatMessage`,
/// so the lookup mirrors the Python's own `except (IndexError, AttributeError):
/// pass -> return None` fallthrough when the key is absent. The DB-id wiring is
/// the documented follow-up the persistence layer will provide; until then this
/// returns the value if a future `_db_id` ever lands in metadata, else `None`.
pub fn save_assistant_response(
    sess: &mut Session,
    session_manager: &crate::core::session_manager::SessionManager,
    session_id: &str,
    full_response: &str,
    last_metrics: Option<&Value>,
    opts: SaveAssistantOpts<'_>,
) -> Option<String> {
    // `md = dict(last_metrics) if last_metrics else {}`
    let mut md: Map<String, Value> = match last_metrics.and_then(|v| v.as_object()) {
        Some(m) => m.clone(),
        None => Map::new(),
    };
    // `md["model"] = sess.model`
    md.insert("model".to_string(), json!(sess.model));
    if let Some(cn) = opts.character_name {
        if !cn.is_empty() {
            md.insert("character_name".to_string(), json!(cn));
        }
    }
    if let Some(ws) = opts.web_sources {
        if !ws.is_empty() {
            md.insert("web_sources".to_string(), Value::Array(ws.to_vec()));
        }
    }
    if let Some(rs) = opts.rag_sources {
        if !rs.is_empty() {
            md.insert("rag_sources".to_string(), Value::Array(rs.to_vec()));
        }
    }
    if let Some(rs) = opts.research_sources {
        if !rs.is_empty() {
            md.insert("research_sources".to_string(), Value::Array(rs.to_vec()));
        }
    }
    if let Some(um) = opts.used_memories {
        if !um.is_empty() {
            md.insert("memories_used".to_string(), Value::Array(um.to_vec()));
        }
    }
    // `if do_research and not research_sources: md["research_clarification"] = True`
    let no_research_sources = opts.research_sources.map(|r| r.is_empty()).unwrap_or(true);
    if opts.do_research && no_research_sources {
        md.insert("research_clarification".to_string(), json!(true));
    }
    if let Some(te) = opts.tool_events {
        if !te.is_empty() {
            md.insert("tool_events".to_string(), Value::Array(te.to_vec()));
        }
    }

    // Extract thinking into metadata (don't pollute message content with tags).
    let content: String = if let Some(info) = extract_thinking_meta(full_response) {
        if let Some(t) = info.get("thinking") {
            md.insert("thinking".to_string(), t.clone());
        }
        // `md["thinking_time"] = _think_info.get("time")` — Python stores the key
        // even when the value is `None`. We mirror that (JSON `null` for absent).
        md.insert(
            "thinking_time".to_string(),
            info.get("time").cloned().unwrap_or(Value::Null),
        );
        info.get("reply")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string()
    } else {
        full_response.to_string()
    };

    // `sess.add_message(ChatMessage("assistant", _content, metadata=md))`
    let md_opt = if md.is_empty() { None } else { Some(md) };
    sess.add_message(ChatMessage::new("assistant", content, md_opt));

    // `if not incognito: update_session_last_accessed(session_id); save_sessions()`
    if !opts.incognito {
        crate::core::database::update_session_last_accessed(session_id);
        session_manager.save_sessions();
    }

    // `if incognito: return None`
    if opts.incognito {
        return None;
    }
    // `_last = sess.history[-1]; _meta = getattr(_last, "metadata", None);
    //  if isinstance(_meta, dict): return _meta.get("_db_id")`
    sess.history
        .last()
        .and_then(|m| m.metadata.as_ref())
        .and_then(|meta| meta.get("_db_id"))
        .and_then(|v| v.as_str())
        .map(str::to_string)
}

/// Keyword args for [`save_assistant_response`] (the Python `*,` keyword-only
/// block). `Default` gives the Python defaults (all `None`/`false`).
#[derive(Default)]
pub struct SaveAssistantOpts<'a> {
    pub character_name: Option<&'a str>,
    pub web_sources: Option<&'a [Value]>,
    pub rag_sources: Option<&'a [Value]>,
    pub research_sources: Option<&'a [Value]>,
    pub used_memories: Option<&'a [Value]>,
    pub do_research: bool,
    pub tool_events: Option<&'a [Value]>,
    pub incognito: bool,
}

// ── Async context build ──────────────────────────────────────────────────── #

/// `preprocess(chat_handler, message, att_ids, sess, auto_opened_docs)` — run
/// `chat_handler.preprocess_message` and wrap the result.
pub async fn preprocess(
    chat_handler: &crate::src::chat_handler::ChatHandler<crate::src::upload_handler::UploadHandler>,
    message: &str,
    att_ids: &[String],
    sess: &Session,
    auto_opened_docs: Option<&mut Vec<Value>>,
) -> PreprocessedMessage {
    let (enhanced, user_content, text_ctx, yt_transcripts, att_meta) = chat_handler
        .preprocess_message(message, att_ids, sess, auto_opened_docs)
        .await;
    PreprocessedMessage {
        enhanced_message: enhanced,
        user_content,
        text_for_context: text_ctx,
        youtube_transcripts: yt_transcripts,
        attachment_meta: att_meta,
    }
}

/// `build_chat_context(...)` — build the full context (preface + messages) for an
/// LLM call. The shared logic between `/chat` and `/chat_stream` (preset
/// extraction, message preprocessing, memory/RAG/web injection, compaction,
/// normalization).
///
/// The Python `request` (for `get_current_user` + the message webhook/event) is
/// replaced by the resolved `user` and the `webhook_manager` handle the caller
/// threads in. `fire_message_event` is invoked from here exactly as Python does
/// inside `build_chat_context` (gated on `not incognito`).
#[allow(clippy::too_many_arguments)]
pub async fn build_chat_context(
    sess: &mut Session,
    user: Option<&str>,
    chat_handler: &crate::src::chat_handler::ChatHandler<crate::src::upload_handler::UploadHandler>,
    chat_processor: &crate::src::chat_processor::ChatProcessor,
    message: &str,
    session_id: &str,
    opts: BuildContextOpts<'_>,
) -> Result<ChatContext, HttpException> {
    // Preset
    let preset = extract_preset(chat_handler, opts.preset_id)?;

    // Preprocess message — the auto_opened_docs collector captures server-side
    // docs created during preprocess.
    let mut auto_opened_docs: Vec<Value> = Vec::new();
    let att_ids: Vec<String> = opts.att_ids.unwrap_or_default().to_vec();
    let preprocessed = preprocess(
        chat_handler,
        message,
        &att_ids,
        sess,
        Some(&mut auto_opened_docs),
    )
    .await;

    // Add user message to history.
    add_user_message(sess, chat_handler, &preprocessed, opts.incognito);

    // Fire events.
    if !opts.incognito {
        fire_message_event(
            opts.webhook_manager,
            user,
            session_id,
            &sess.model,
            message,
            opts.compare_mode,
        );
    }

    // Resolve user prefs.
    let uprefs = prefs_store::load_for_user(user);

    // Memory enabled?
    // `mem_enabled = not incognito and not no_memory and uprefs.get("memory_enabled", True)`
    let mem_enabled = !opts.incognito
        && !opts.no_memory
        && pref_bool(&uprefs, "memory_enabled", true);
    // `skills_enabled = not incognito and uprefs.get("skills_enabled", True)`
    let skills_enabled = !opts.incognito && pref_bool(&uprefs, "skills_enabled", true);

    // Use RAG?
    // `use_rag_val = (str(use_rag).lower() != "false") if use_rag is not None else True`
    // then `if incognito: use_rag_val = False`.
    let use_rag_val = match opts.use_rag {
        Some(v) => v.to_lowercase() != "false",
        None => true,
    };
    let use_rag_val = if opts.incognito { false } else { use_rag_val };

    // If pre-fetched search context was provided (compare mode), skip live web.
    let skip_web = opts.search_context.map(|s| !s.is_empty()).unwrap_or(false);

    // Build context preface. The stream path uses enhanced_message; the sync path
    // uses text_for_context.
    let ctx_msg = if opts.use_enhanced_message {
        &preprocessed.enhanced_message
    } else {
        &preprocessed.text_for_context
    };
    // `use_web=use_web and not skip_web` — Python `use_web` may be None (falsy).
    let use_web = opts.use_web.unwrap_or(false) && !skip_web;
    let time_filter = opts.time_filter;
    // `if use_rag is not None: _preface_kwargs["use_rag"] = use_rag_val` — when
    // `use_rag` is None the kwarg is omitted, so `build_context_preface` uses its
    // own default (the ported signature's `use_rag` default is `true`).
    let use_rag_arg = if opts.use_rag.is_some() {
        use_rag_val
    } else {
        true
    };
    let (mut preface, rag_sources, web_sources) = chat_processor.build_context_preface(
        ctx_msg,
        use_web,
        use_rag_arg,
        mem_enabled,
        time_filter,
        preset.system_prompt.as_deref(),
        user,
        preset.character_name.as_deref(),
        opts.agent_mode,
        opts.incognito,
        skills_enabled,
    );

    // Capture used memories immediately.
    let used_memories = chat_processor.last_used_memories();

    // Inject pre-fetched search context (compare mode).
    if let Some(sc) = opts.search_context {
        if !sc.is_empty() {
            preface.push(crate::src::prompt_security::untrusted_context_message(
                "prefetched search context",
                Some(sc),
            ));
        }
    }

    // YouTube transcripts.
    for transcript in &preprocessed.youtube_transcripts {
        preface.push(crate::src::prompt_security::untrusted_context_message(
            "youtube transcript",
            Some(transcript),
        ));
    }

    // Normalize model ID. Prefer cached endpoint models so group chat does not
    // re-hit slow local /models endpoints on every participant turn.
    //
    // `norm = _normalize_model_id_from_cache(sess) or normalize_model_id(sess.endpoint_url, sess.model)`
    // then `if norm: sess.model = norm`.
    //
    // The cache path reads the stored `cached_models` of the enabled endpoint
    // whose `base_url` normalizes to the session's base, and canonicalizes the
    // requested model id (exact then `basename` match) — no network. Only when the
    // cache yields nothing does this fall back to the NETWORK form, which probes
    // the endpoint's `/models` (no auth headers, exactly like Python) and matches
    // the same way. `None` from both leaves the model unchanged.
    let norm = match normalize_model_id_from_cache(sess) {
        Some(m) => Some(m),
        None => {
            crate::src::llm_core::normalize_model_id_network(&sess.endpoint_url, &sess.model).await
        }
    };
    if let Some(norm) = norm {
        sess.model = norm;
    }

    // Build messages.
    // `messages = preface + sess.get_context_messages()`
    let mut messages: Vec<Value> = preface.clone();
    messages.extend(sess.get_context_messages());

    // Auto-compact, then trim (chat_helpers.py:442-445):
    //   messages, context_length, was_compacted = await maybe_compact(
    //       sess, sess.endpoint_url, sess.model, messages, sess.headers)
    //   messages = trim_for_context(messages, context_length)
    //
    // `maybe_compact` summarizes the older half once usage exceeds 85% of the
    // model's known context window; below threshold it returns the messages
    // unchanged (so normal conversations are untouched). `context_length` is the
    // pure `get_context_length` known-windows lookup (the network probe is the one
    // deferred half), so this adds no extra hot-path network call.
    let endpoint_url = sess.endpoint_url.clone();
    let model = sess.model.clone();
    let sess_headers = sess.headers.clone();
    let (compacted, context_length, was_compacted) = crate::src::context_compactor::maybe_compact(
        sess,
        &endpoint_url,
        &model,
        &messages,
        &sess_headers,
    )
    .await;
    let messages = crate::src::context_compactor::trim_for_context_default(&compacted, context_length);

    Ok(ChatContext {
        preface,
        rag_sources,
        web_sources,
        used_memories,
        messages,
        context_length,
        was_compacted,
        user: user.map(str::to_string),
        uprefs,
        preset,
        preprocessed,
        auto_opened_docs,
    })
}

/// Keyword args for [`build_chat_context`] (the long Python keyword block).
/// `Default` gives the Python defaults.
#[derive(Default)]
pub struct BuildContextOpts<'a> {
    pub preset_id: Option<&'a str>,
    pub att_ids: Option<&'a [String]>,
    pub use_web: Option<bool>,
    pub use_rag: Option<&'a str>,
    pub use_research: Option<bool>,
    pub time_filter: Option<&'a str>,
    pub incognito: bool,
    pub no_memory: bool,
    pub search_context: Option<&'a str>,
    pub compare_mode: bool,
    pub webhook_manager: Option<&'a Arc<crate::src::webhook_manager::WebhookManager>>,
    pub use_enhanced_message: bool,
    pub agent_mode: bool,
}

/// `uprefs.get(key, default)` for a bool pref — truthy/falsy follows Python:
/// a JSON `false`/`null`/missing is the default-or-false, a present `true` wins.
fn pref_bool(uprefs: &Value, key: &str, default: bool) -> bool {
    match uprefs.get(key) {
        Some(Value::Bool(b)) => *b,
        // Present-but-not-bool: Python would treat the raw value's truthiness.
        // Numbers/strings: 0/""/null falsy, else truthy.
        Some(Value::Number(n)) => n.as_f64().map(|f| f != 0.0).unwrap_or(false),
        Some(Value::String(s)) => !s.is_empty(),
        Some(Value::Null) | None => default,
        Some(_) => default,
    }
}

// ── Background / fire-and-forget side effects ────────────────────────────── #

/// `fire_message_event(request, webhook_manager, session_id, sess, message,
/// compare_mode)` — fire webhook and event_bus events for a new user message.
///
/// `asyncio.create_task(webhook_manager.fire(...))` -> the ported
/// `WebhookManager::fire_and_forget` (which spawns the same network fire).
/// `fire_event("message_sent", user)` is synchronous in both.
pub fn fire_message_event(
    webhook_manager: Option<&Arc<crate::src::webhook_manager::WebhookManager>>,
    user: Option<&str>,
    session_id: &str,
    model: &str,
    message: &str,
    compare_mode: bool,
) {
    // `if webhook_manager and not compare_mode: asyncio.create_task(...fire("chat.message"...))`
    if let Some(wm) = webhook_manager {
        if !compare_mode {
            // `message[:2000]` — truncate by characters (Python slices the str).
            let truncated: String = message.chars().take(2000).collect();
            wm.fire_and_forget(
                "chat.message",
                json!({
                    "session_id": session_id,
                    "model": model,
                    "message": truncated,
                }),
            );
        }
    }
    // `fire_event("message_sent", user)`
    crate::src::event_bus::fire_event("message_sent", user);
}

/// `run_post_response_tasks(...)` — fire background tasks after a completed
/// response: memory extraction, webhooks, auto-name, skill extraction.
///
/// Python calls `asyncio.create_task(...)` for the extractors / webhook /
/// auto-name; the faithful Rust analogue spawns the same work via `tokio::spawn`
/// from cloned owned handles (`Arc<...>` managers + an owned `Session` snapshot),
/// so each future is `'static` — fire-and-forget, exactly like
/// `create_task`. The pure GATING logic (the `should_extract` / skill-extract
/// thresholds, the token accumulation, the auto-name predicate) runs inline and
/// is unit-tested directly.
#[allow(clippy::too_many_arguments)]
pub fn run_post_response_tasks(
    sess: &Session,
    session_manager: Arc<crate::core::session_manager::SessionManager>,
    session_id: &str,
    message: &str,
    full_response: &str,
    last_metrics: Option<&Value>,
    uprefs: &Value,
    memory_manager: Arc<crate::src::memory::MemoryManager>,
    memory_vector: Option<Arc<crate::src::memory_vector::MemoryVectorStore>>,
    webhook_manager: Option<Arc<crate::src::webhook_manager::WebhookManager>>,
    opts: PostResponseOpts<'_>,
) {
    // Memory extraction — only every 4th message pair to avoid excess LLM calls.
    let msg_count = sess.history.len() as i64;
    let should_extract = msg_count >= 4 && msg_count % 4 == 0;
    if !opts.incognito
        && !opts.compare_mode
        && should_extract
        && pref_bool(uprefs, "auto_memory", true)
    {
        let (t_url, t_model, t_headers) = crate::src::task_endpoint::resolve_task_endpoint(
            Some(sess.endpoint_url.clone()),
            Some(sess.model.clone()),
            Some(map_from_headers(&sess.headers)),
            sess.owner.as_deref(),
        );
        let sess_owned = sess.clone();
        let mm = memory_manager.clone();
        let mv = memory_vector.clone();
        tokio::spawn(async move {
            // `extract_and_store(sess, memory_manager, memory_vector, t_url, t_model, t_headers)`
            // The extractor wants `&mut Option<MemoryVectorStore>`; reconstruct an
            // owned Option from the Arc handle for the spawned task (Python passes
            // the live store; cloning the in-memory index is the faithful analogue).
            let mut mv_owned: Option<crate::src::memory_vector::MemoryVectorStore> =
                mv.map(|a| (*a).clone());
            let headers = t_headers.map(headers_from_map);
            crate::services::memory::memory_extractor::extract_and_store(
                &sess_owned,
                &mm,
                &mut mv_owned,
                t_url.as_deref().unwrap_or(""),
                t_model.as_deref().unwrap_or(""),
                headers,
            )
            .await;
        });
    }

    // Skill extraction from complex agent runs. Only when the user actually chose
    // agent mode, never in incognito/compare.
    let auto_skills_enabled = pref_bool(uprefs, "auto_skills", true);
    if opts.extract_skills
        && auto_skills_enabled
        && !opts.incognito
        && !opts.compare_mode
        && (opts.agent_rounds >= 2 || opts.agent_tool_calls >= 2)
    {
        if let Some(sm) = opts.skills_manager.cloned() {
            let (s_url, s_model, s_headers) = crate::src::task_endpoint::resolve_task_endpoint(
                Some(sess.endpoint_url.clone()),
                Some(sess.model.clone()),
                Some(map_from_headers(&sess.headers)),
                sess.owner.as_deref(),
            );
            let sess_owned = sess.clone();
            let owner = opts.owner.map(str::to_string);
            let rounds = opts.agent_rounds;
            let tools = opts.agent_tool_calls;
            tokio::spawn(async move {
                let headers = s_headers.map(headers_from_map).unwrap_or_default();
                let _ = crate::services::memory::skill_extractor::maybe_extract_skill(
                    &sess_owned,
                    &sm,
                    s_url.as_deref().unwrap_or(""),
                    s_model.as_deref().unwrap_or(""),
                    headers,
                    rounds,
                    tools,
                    owner.as_deref(),
                )
                .await;
            });
        } else {
            crate::pylog::warning(
                "[skill-extract] gate PASSED but skills_manager is None — extraction skipped.",
            );
        }
    }

    // Token accumulation.
    if let Some(metrics) = last_metrics {
        accumulate_token_usage(session_id, metrics);
    }

    // Webhook.
    if let Some(wm) = &webhook_manager {
        if !opts.compare_mode {
            let resp_trunc: String = full_response.chars().take(2000).collect();
            wm.fire_and_forget(
                "chat.completed",
                json!({
                    "session_id": session_id,
                    "model": sess.model,
                    "user_message": message,
                    "response": resp_trunc,
                }),
            );
        }
    }

    // Auto-name.
    if needs_auto_name(&sess.name) {
        let sess_owned = sess.clone();
        let sm = session_manager.clone();
        tokio::spawn(async move {
            auto_name_session(&sm, &sess_owned).await;
        });
    }
}

/// Keyword args for [`run_post_response_tasks`] (the Python `*,` keyword block).
pub struct PostResponseOpts<'a> {
    pub incognito: bool,
    pub compare_mode: bool,
    pub character_name: Option<&'a str>,
    pub agent_rounds: i64,
    pub agent_tool_calls: i64,
    pub skills_manager: Option<&'a Arc<crate::services::memory::skills::SkillsManager>>,
    pub owner: Option<&'a str>,
    pub extract_skills: bool,
}

impl<'a> Default for PostResponseOpts<'a> {
    fn default() -> Self {
        // Python defaults: incognito=False, compare_mode=False, character_name=None,
        // agent_rounds=0, agent_tool_calls=0, skills_manager=None, owner=None,
        // extract_skills=True.
        PostResponseOpts {
            incognito: false,
            compare_mode: false,
            character_name: None,
            agent_rounds: 0,
            agent_tool_calls: 0,
            skills_manager: None,
            owner: None,
            extract_skills: true,
        }
    }
}

/// `auto_name_session(session_manager, sess)` — generate a short title for a
/// session from its first user message.
///
/// The whole body is wrapped in the Python's `try/except`: any failure is logged
/// and swallowed (no rename). `llm_call_async` / `resolve_task_endpoint` /
/// `strip_think` are all ported.
pub async fn auto_name_session(
    session_manager: &crate::core::session_manager::SessionManager,
    sess: &Session,
) {
    // Find first user message.
    let mut first_msg = String::new();
    for msg in &sess.history {
        if msg.role == "user" {
            // Python: if content is a list, pick the first text item; else str(content).
            // The ported ChatMessage.content is always a String (the model layer
            // stores content as text), so this is `str(content)[:500]`.
            let content = &msg.content;
            first_msg = content.chars().take(500).collect();
            break;
        }
    }
    // `if not first_msg: return`
    if first_msg.is_empty() {
        return;
    }

    let (t_url, t_model, t_headers) = crate::src::task_endpoint::resolve_task_endpoint(
        Some(sess.endpoint_url.clone()),
        Some(sess.model.clone()),
        Some(map_from_headers(&sess.headers)),
        sess.owner.as_deref(),
    );
    let url = match t_url {
        Some(u) if !u.is_empty() => u,
        _ => return,
    };
    let model = t_model.unwrap_or_default();
    let headers = t_headers.map(headers_from_map).unwrap_or_default();

    let messages = vec![
        json!({"role": "system", "content": "Generate a short title (3-6 words, no quotes) for a conversation that starts with this message. Reply with ONLY the title, nothing else. Do NOT include any thinking, reasoning, or explanation — just the title."}),
        json!({"role": "user", "content": first_msg}),
    ];

    // `title = await llm_call_async(t_url, t_model, [...], temperature=0.3, max_tokens=4096, headers=t_headers, timeout=60)`
    let title = match crate::src::llm_core::llm_call_async(
        &url, &model, messages, 0.3, 4096, headers, 60,
    )
    .await
    {
        Ok(t) => t,
        Err(e) => {
            crate::pylog::error(&format!("Auto-name failed for {}: {e}", sess.id));
            return;
        }
    };

    // `title = title.strip().strip('"\'').strip()`
    let title = title.trim();
    let title = title.trim_matches(|c| c == '"' || c == '\'');
    let title = title.trim();
    // `title = strip_think(title, prose=False, prompt_echo=False)`
    let title = crate::src::text_helpers::strip_think(title, false, false);
    // `if title and len(title) < 80:`
    if !title.is_empty() && title.chars().count() < 80 {
        let _ = session_manager.update_session_name(&sess.id, &title);
        crate::pylog::info(&format!("Auto-named session {}: {title}", sess.id));
    }
}

// ── Session auth resolution ──────────────────────────────────────────────── #

/// `os.path.basename(p.rstrip("/"))` — the trailing path segment after the last
/// `/`, with any trailing slashes stripped first. (`"a/b/"` -> `"b"`, `"a/b"` ->
/// `"b"`, `"model"` -> `"model"`.)
fn path_basename(p: &str) -> &str {
    let trimmed = p.trim_end_matches('/');
    match trimmed.rfind('/') {
        Some(i) => &trimmed[i + 1..],
        None => trimmed,
    }
}

/// `_match_cached_model_id(requested, models)` — pick the cached model id that
/// matches `requested`, preferring an exact id, then a `basename` match.
///
/// `model_ids = [str(m) for m in models if m]` — drop falsy entries. Returns the
/// matched id (the cached spelling, which may differ from `requested`), or `None`.
fn match_cached_model_id(requested: &str, models: &[String]) -> Option<String> {
    // `if not requested or not models: return None`
    if requested.is_empty() || models.is_empty() {
        return None;
    }
    let model_ids: Vec<&str> = models
        .iter()
        .filter(|m| !m.is_empty())
        .map(|m| m.as_str())
        .collect();
    // `if requested in model_ids: return requested`
    if model_ids.contains(&requested) {
        return Some(requested.to_string());
    }
    // `req_base = os.path.basename(requested.rstrip("/"))`
    let req_base = path_basename(requested);
    for model_id in &model_ids {
        // `if os.path.basename(model_id.rstrip("/")) == req_base: return model_id`
        if path_basename(model_id) == req_base {
            return Some((*model_id).to_string());
        }
    }
    None
}

/// `_normalize_model_id_from_cache(sess)` — use stored endpoint model ids before
/// falling back to a live `/models` probe.
///
/// Looks up the enabled `model_endpoints` row whose `base_url` normalizes to the
/// session's endpoint base, parses its `cached_models` (a JSON list), and returns
/// the canonical model id via [`match_cached_model_id`]. `None` when there is no
/// endpoint/model, no matching enabled endpoint, no cached models, or no match.
///
/// PARITY NOTE — owner: the Python `_normalize_model_id_from_cache` queries
/// `db.query(ModelEndpoint).filter(ModelEndpoint.is_enabled == True).all()` with
/// NO owner filter (it is a pure model-id canonicalization, not an auth/secret
/// read), so this scans ALL enabled endpoints to match. Owner scoping lives in
/// [`resolve_session_auth`], which guards the api-key read.
fn normalize_model_id_from_cache(sess: &Session) -> Option<String> {
    // `endpoint_url = sess.endpoint_url or ""`, `requested = sess.model or ""`
    let endpoint_url = sess.endpoint_url.as_str();
    let requested = sess.model.as_str();
    // `if not endpoint_url or not requested: return None`
    if endpoint_url.is_empty() || requested.is_empty() {
        return None;
    }

    // `session_base = normalize_base(endpoint_url)` (the Python `except` falls back
    // to `endpoint_url.rstrip("/")`; our `normalize_base` never raises, so the
    // happy path is exact). `if not session_base: return None`.
    let session_base = crate::src::endpoint_resolver::normalize_base(Some(endpoint_url));
    if session_base.is_empty() {
        return None;
    }

    // `db.query(ModelEndpoint).filter(is_enabled == True).all()` — NO owner filter
    // (see PARITY NOTE). Confine the rusqlite Connection to this synchronous scope
    // and bind the collected rows to a Vec so no DB handle crosses an await.
    let endpoints: Vec<(String, Option<String>)> = {
        let conn = match crate::core::database::session_local() {
            Ok(c) => c,
            Err(_) => return None,
        };
        let mut stmt = match conn
            .prepare("SELECT base_url, cached_models FROM model_endpoints WHERE is_enabled = 1")
        {
            Ok(s) => s,
            Err(_) => return None,
        };
        let rows = stmt.query_map([], |r| {
            Ok((r.get::<_, String>(0)?, r.get::<_, Option<String>>(1)?))
        });
        match rows {
            Ok(it) => it.filter_map(Result::ok).collect(),
            Err(_) => return None,
        }
    };

    for (base_url, raw_models) in &endpoints {
        // `if normalize_base(ep.base_url) != session_base: continue`
        if crate::src::endpoint_resolver::normalize_base(Some(base_url)) != session_base {
            continue;
        }
        // `raw_models = ep.cached_models; if not raw_models: continue`
        let raw = match raw_models {
            Some(s) if !s.is_empty() => s,
            _ => continue,
        };
        // `models = json.loads(raw_models) if isinstance(raw_models, str) else raw_models`
        // — the column is TEXT, so always a JSON string; a parse failure is skipped
        // (Python `try/except: continue`).
        let models: Vec<String> = match serde_json::from_str::<Vec<String>>(raw) {
            Ok(m) => m,
            Err(_) => continue,
        };
        if let Some(matched) = match_cached_model_id(requested, &models) {
            return Some(matched);
        }
    }

    None
}

/// `_session_url_matches_endpoint(session_url, endpoint_base)` — does the session's
/// stored `endpoint_url` resolve to this endpoint's base, by EXACT URL?
///
/// `if not session_url or not endpoint_base: return False`. Otherwise the session
/// url (trailing-slash-stripped) must EXACTLY equal one of: the normalized base,
/// `base + "/chat/completions"`, or `build_chat_url(base)` (trailing-slash-
/// stripped). This is a tight equality set — NOT a loose `base in session_url`
/// substring — so two endpoints sharing a host prefix can't borrow each other's
/// api key.
fn session_url_matches_endpoint(session_url: &str, endpoint_base: &str) -> bool {
    if session_url.is_empty() || endpoint_base.is_empty() {
        return false;
    }
    // `sess_url = session_url.rstrip("/")`
    let sess_url = session_url.trim_end_matches('/');
    // `base = normalize_base(endpoint_base).rstrip("/")`
    let base = crate::src::endpoint_resolver::normalize_base(Some(endpoint_base));
    let base = base.trim_end_matches('/');
    // `return sess_url in {base, base + "/chat/completions", build_chat_url(base).rstrip("/")}`
    let chat_url = crate::src::endpoint_resolver::build_chat_url(base);
    let chat_url = chat_url.trim_end_matches('/');
    sess_url == base || sess_url == format!("{base}/chat/completions") || sess_url == chat_url
}

/// `resolve_session_auth(sess, session_id, owner=None)` — ensure the session has
/// auth headers, resolving them from the endpoint DB when missing.
///
/// `has_auth = sess.headers and any(k.lower() in ('authorization','x-api-key'))`;
/// when present, no-op. Otherwise look up the enabled `model_endpoints` row whose
/// `base_url` matches the session's `endpoint_url` by EXACT url
/// ([`session_url_matches_endpoint`]), require `is_enabled`, build headers from its
/// api_key, and persist them back onto the session row.
///
/// When `owner` is `Some`, the endpoint lookup AND the session UPDATE are scoped to
/// that owner (`owner = ?owner OR owner IS NULL` for endpoints; `owner = ?owner`
/// for the session row), so two users with similar endpoint URLs can't borrow each
/// other's api key. `None` owner scans/updates without that filter (single-user /
/// admin path).
///
/// Errors are swallowed (Python wraps the body in `try/except` and only logs a
/// warning).
pub fn resolve_session_auth(sess: &mut Session, session_id: &str, owner: Option<&str>) {
    // `has_auth = sess.headers and isinstance(dict) and any(k.lower() in (...))`
    let has_auth = sess.headers.keys().any(|k| {
        let lk = k.to_lowercase();
        lk == "authorization" || lk == "x-api-key"
    });
    if has_auth {
        return;
    }

    // `target_url = getattr(sess, "endpoint_url", "") or ""; if not target_url: return`
    let target_url = sess.endpoint_url.clone();
    if target_url.is_empty() {
        return;
    }

    let conn = match crate::core::database::session_local() {
        Ok(c) => c,
        Err(e) => {
            crate::pylog::warning(&format!("Failed to resolve session headers: {e}"));
            return;
        }
    };

    // `q = db.query(ModelEndpoint).filter(is_enabled == True)`
    // `if owner: q = owner_filter(q, ModelEndpoint, owner)` — owner's own rows +
    // null-owner (legacy/shared), exactly the `owner = ?1 OR owner IS NULL` fragment
    // SQLAlchemy's `owner_filter` emits.
    let row_map = |r: &rusqlite::Row| -> rusqlite::Result<(String, Option<String>, String)> {
        Ok((
            r.get::<_, String>(0)?,
            r.get::<_, Option<String>>(1)?,
            r.get::<_, String>(2)?,
        ))
    };
    let endpoints: Vec<(String, Option<String>, String)> = {
        let (sql, params): (&str, Vec<&dyn rusqlite::ToSql>) = match owner.as_ref() {
            Some(o) => (
                "SELECT base_url, api_key, name FROM model_endpoints \
                 WHERE is_enabled = 1 AND (owner = ?1 OR owner IS NULL)",
                vec![o],
            ),
            None => (
                "SELECT base_url, api_key, name FROM model_endpoints WHERE is_enabled = 1",
                vec![],
            ),
        };
        let mut stmt = match conn.prepare(sql) {
            Ok(s) => s,
            Err(e) => {
                crate::pylog::warning(&format!("Failed to resolve session headers: {e}"));
                return;
            }
        };
        let rows = stmt.query_map(params.as_slice(), row_map);
        match rows {
            Ok(it) => it.filter_map(Result::ok).collect(),
            Err(e) => {
                crate::pylog::warning(&format!("Failed to resolve session headers: {e}"));
                return;
            }
        }
    };

    for (base_url, api_key, name) in &endpoints {
        // `if not _session_url_matches_endpoint(target_url, ep.base_url or ""): continue`
        if !session_url_matches_endpoint(&target_url, base_url) {
            continue;
        }
        // `if not ep.api_key: return` — a matched-but-keyless endpoint stops the
        // search (Python returns, it does NOT fall through to a later endpoint).
        let key = match api_key {
            Some(k) if !k.is_empty() => k,
            _ => return,
        };
        // `base = normalize_base(ep.base_url or ""); sess.headers = build_headers(ep.api_key, base)`
        let base = crate::src::endpoint_resolver::normalize_base(Some(base_url));
        let new_headers = crate::src::endpoint_resolver::build_headers(Some(key), &base);
        sess.headers = new_headers.clone();

        // `update_q = db.query(Session).filter(id == session_id)`
        // `if owner: update_q = update_q.filter(Session.owner == owner)`
        // `update_q.update({"headers": sess.headers}); db.commit()`
        let headers_json = {
            let map: Map<String, Value> = new_headers
                .iter()
                .map(|(k, v)| (k.clone(), Value::String(v.clone())))
                .collect();
            serde_json::to_string(&Value::Object(map)).unwrap_or_else(|_| "{}".to_string())
        };
        let _ = match owner {
            Some(o) => conn.execute(
                "UPDATE sessions SET headers = ?1 WHERE id = ?2 AND owner = ?3",
                rusqlite::params![headers_json, session_id, o],
            ),
            None => conn.execute(
                "UPDATE sessions SET headers = ?1 WHERE id = ?2",
                rusqlite::params![headers_json, session_id],
            ),
        };
        crate::pylog::info(&format!(
            "Resolved and persisted auth headers for session {session_id} from endpoint {name}"
        ));
        return;
    }
}

// ── Small adapters ───────────────────────────────────────────────────────── #

/// `IndexMap<String,String>` (Session.headers) -> the `serde_json::Map` shape
/// `resolve_task_endpoint` accepts as its `fallback_headers`.
fn map_from_headers(
    headers: &indexmap::IndexMap<String, String>,
) -> Map<String, Value> {
    let mut m = Map::new();
    for (k, v) in headers {
        m.insert(k.clone(), Value::String(v.clone()));
    }
    m
}

/// `serde_json::Map` (the headers `resolve_task_endpoint` returns) ->
/// `IndexMap<String,String>` (what the extractors / `llm_call_async` accept).
/// Non-string values are stringified (headers are always strings in practice).
fn headers_from_map(map: Map<String, Value>) -> indexmap::IndexMap<String, String> {
    let mut out: indexmap::IndexMap<String, String> = indexmap::IndexMap::new();
    for (k, v) in map {
        let s = match v {
            Value::String(s) => s,
            other => other.to_string(),
        };
        out.insert(k, s);
    }
    out
}

// ── byte-offset slicing helpers ──────────────────────────────────────────── #

/// `text[n:]` — slice from byte offset `n`, clamped to a char boundary.
/// The regex offsets are always at char boundaries (the patterns match ASCII
/// punctuation), so the clamp is a defensive no-op for the normal case.
fn byte_slice_from(text: &str, n: usize) -> &str {
    if n >= text.len() {
        return "";
    }
    let mut idx = n;
    while idx < text.len() && !text.is_char_boundary(idx) {
        idx += 1;
    }
    &text[idx..]
}

/// `text[:n]` — slice up to byte offset `n`, clamped to a char boundary.
fn byte_slice_to(text: &str, n: usize) -> &str {
    if n >= text.len() {
        return text;
    }
    let mut idx = n;
    while idx > 0 && !text.is_char_boundary(idx) {
        idx -= 1;
    }
    &text[..idx]
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    // ── needs_auto_name ──────────────────────────────────────────────────
    #[test]
    fn needs_auto_name_empty_is_true() {
        assert!(needs_auto_name(""));
    }

    #[test]
    fn needs_auto_name_chat_prefixes() {
        assert!(needs_auto_name("Chat"));
        assert!(needs_auto_name("Chat: hello world"));
        assert!(!needs_auto_name("My great chat"));
    }

    #[test]
    fn needs_auto_name_default_frontend_name() {
        // "modelname HH:MM:SS AM/PM"
        assert!(needs_auto_name("llama3 10:42:07 PM"));
        assert!(needs_auto_name("gpt-4o 9:05:59 AM"));
        // The AM/PM suffix is now OPTIONAL (upstream parity: the regex made the
        // `(\s*(AM|PM))?` group optional), so a 24-hour default name with no
        // AM/PM is also recognized as a placeholder.
        assert!(needs_auto_name("llama3 10:42:07"));
        assert!(needs_auto_name("gpt-4o 22:05:59"));
        // The AM/PM match is now case-insensitive (`(?i)`).
        assert!(needs_auto_name("llama3 10:42:07 pm"));
        assert!(needs_auto_name("gpt-4o 9:05:59 am"));
        // A real user-given title without a HH:MM:SS clock is still kept.
        assert!(!needs_auto_name("My great chat"));
        assert!(!needs_auto_name("llama3 chat about rust"));
    }

    // ── _normalize_thinking ─────────────────────────────────────────────
    #[test]
    fn normalize_thinking_empty() {
        assert_eq!(normalize_thinking(""), "");
    }

    #[test]
    fn normalize_thinking_already_tagged_passthrough() {
        let t = "<think>reasoning</think>\nHello!";
        assert_eq!(normalize_thinking(t), t);
    }

    #[test]
    fn normalize_thinking_qwen_clean_boundary() {
        let t = "Thinking Process: I should greet warmly.\n\nHello there!";
        let out = normalize_thinking(t);
        assert!(out.starts_with("<think>"), "got: {out}");
        assert!(out.contains("</think>"), "got: {out}");
        assert!(out.contains("Hello there!"), "got: {out}");
        // The "Thinking Process:" prefix is stripped from the thinking content.
        assert!(!out.contains("<think>Thinking Process:"), "got: {out}");
    }

    #[test]
    fn normalize_thinking_gemma_line_split() {
        let t = "The user wants a greeting.\nHey! How can I help?";
        let out = normalize_thinking(t);
        assert_eq!(out, "<think>The user wants a greeting.</think>\nHey! How can I help?");
    }

    #[test]
    fn normalize_thinking_garbled_tag() {
        let t = "The user said hi.\n<think>Hey! What's up?";
        let out = normalize_thinking(t);
        assert!(out.starts_with("<think>The user said hi.</think>"), "got: {out}");
        assert!(out.contains("Hey! What's up?"), "got: {out}");
    }

    #[test]
    fn normalize_thinking_plain_reply_unchanged() {
        let t = "Sure, here is the answer.";
        assert_eq!(normalize_thinking(t), t);
    }

    // ── _extract_thinking_meta / clean_thinking_for_save ─────────────────
    #[test]
    fn extract_thinking_meta_native_tags() {
        let info = extract_thinking_meta("<think>pondering</think>\nThe answer is 42.").unwrap();
        assert_eq!(info["thinking"], json!("pondering"));
        assert_eq!(info["reply"], json!("The answer is 42."));
        assert_eq!(info["time"], json!(null));
    }

    #[test]
    fn extract_thinking_meta_with_time_attr() {
        let info =
            extract_thinking_meta("<think time=\"1.5\">pondering</think>\nDone.").unwrap();
        assert_eq!(info["thinking"], json!("pondering"));
        assert_eq!(info["reply"], json!("Done."));
        assert_eq!(info["time"], json!("1.5"));
    }

    #[test]
    fn extract_thinking_meta_empty_reply_returns_none() {
        // thinking-only turn (no reply after </think>) -> keep raw, return None.
        assert!(extract_thinking_meta("<think>just reasoning, no reply</think>").is_none());
    }

    #[test]
    fn extract_thinking_meta_no_thinking_returns_none() {
        assert!(extract_thinking_meta("Just a plain reply.").is_none());
    }

    #[test]
    fn clean_thinking_for_save_extracts_into_metadata() {
        let (reply, md) =
            clean_thinking_for_save("<think time=\"0.4\">hmm</think>\nHi there.", None);
        assert_eq!(reply, "Hi there.");
        assert_eq!(md.get("thinking"), Some(&json!("hmm")));
        assert_eq!(md.get("thinking_time"), Some(&json!("0.4")));
    }

    #[test]
    fn clean_thinking_for_save_no_thinking_passthrough() {
        let mut base = Map::new();
        base.insert("k".to_string(), json!("v"));
        let (reply, md) = clean_thinking_for_save("plain content", Some(&base));
        assert_eq!(reply, "plain content");
        // Original metadata preserved, no thinking keys added.
        assert_eq!(md.get("k"), Some(&json!("v")));
        assert!(md.get("thinking").is_none());
    }

    // ── json_to_i64 / pref_bool ──────────────────────────────────────────
    #[test]
    fn json_to_i64_variants() {
        assert_eq!(json_to_i64(&json!(5)), 5);
        assert_eq!(json_to_i64(&json!(2.9)), 2);
        assert_eq!(json_to_i64(&json!("7")), 7);
        assert_eq!(json_to_i64(&json!("nope")), 0);
        assert_eq!(json_to_i64(&json!(null)), 0);
    }

    #[test]
    fn pref_bool_defaults_and_overrides() {
        let p = json!({"memory_enabled": false, "skills_enabled": true});
        assert!(!pref_bool(&p, "memory_enabled", true));
        assert!(pref_bool(&p, "skills_enabled", true));
        // Missing -> default.
        assert!(pref_bool(&p, "auto_memory", true));
        assert!(!pref_bool(&p, "auto_memory", false));
    }

    // ── PresetInfo defaults ──────────────────────────────────────────────
    #[test]
    fn preset_info_default() {
        let p = PresetInfo::default();
        assert_eq!(p, PresetInfo {
            temperature: None,
            max_tokens: None,
            system_prompt: None,
            character_name: None,
        });
    }

    // ── path_basename / match_cached_model_id ────────────────────────────
    #[test]
    fn path_basename_variants() {
        assert_eq!(path_basename("a/b/c"), "c");
        assert_eq!(path_basename("a/b/c/"), "c");
        assert_eq!(path_basename("model"), "model");
        assert_eq!(path_basename("hf.co/org/model"), "model");
        assert_eq!(path_basename(""), "");
    }

    #[test]
    fn match_cached_model_id_exact_then_basename() {
        let models = vec![
            "hf.co/org/llama3-8b".to_string(),
            "gpt-4o".to_string(),
        ];
        // Exact match wins, returns the requested spelling.
        assert_eq!(
            match_cached_model_id("gpt-4o", &models).as_deref(),
            Some("gpt-4o")
        );
        // basename match: requested bare name canonicalizes to the cached path id.
        assert_eq!(
            match_cached_model_id("llama3-8b", &models).as_deref(),
            Some("hf.co/org/llama3-8b")
        );
        // No match.
        assert_eq!(match_cached_model_id("mistral", &models), None);
        // Empty inputs.
        assert_eq!(match_cached_model_id("", &models), None);
        assert_eq!(match_cached_model_id("gpt-4o", &[]), None);
    }

    // ── session_url_matches_endpoint (EXACT, not loose substring) ─────────
    #[test]
    fn session_url_matches_endpoint_exact_set() {
        let base = "https://api.example.com/v1";
        // The three accepted forms.
        assert!(session_url_matches_endpoint(
            "https://api.example.com/v1/chat/completions",
            base
        ));
        assert!(session_url_matches_endpoint("https://api.example.com/v1", base));
        assert!(session_url_matches_endpoint("https://api.example.com/v1/", base));
        // A loose substring/host-prefix is REJECTED (the old loose match would
        // have accepted this); a different host that merely contains the base host
        // as a substring must not match.
        assert!(!session_url_matches_endpoint(
            "https://api.example.com.evil.test/v1/chat/completions",
            base
        ));
        // Empty inputs -> False.
        assert!(!session_url_matches_endpoint("", base));
        assert!(!session_url_matches_endpoint("https://api.example.com/v1", ""));
    }

    // ── DB-backed: normalize_model_id_from_cache + resolve_session_auth ───
    //
    // These touch the sqlite DB. They each create a UNIQUE temp DB file, point
    // `DATABASE_URL` at it, build the schema, and seed rows. A process-local mutex
    // serializes them so the shared `DATABASE_URL` env var is never read mid-swap
    // by a sibling DB test running on another thread. Never the live data dir.
    // Shared process-global lock (NOT a per-module one) so these DB tests
    // serialize against the sibling `chat_routes` DB tests too — both swap the
    // process-wide `DATABASE_URL`.
    use crate::core::database::DB_TEST_LOCK;

    struct TmpDb(std::path::PathBuf);
    impl Drop for TmpDb {
        fn drop(&mut self) {
            let _ = std::fs::remove_file(&self.0);
        }
    }

    fn fresh_temp_db(tag: &str) -> TmpDb {
        let dir = std::env::temp_dir();
        let unique = format!(
            "odysseus_chat_helpers_{tag}_{}_{}.db",
            std::process::id(),
            uuid::Uuid::new_v4().simple()
        );
        let path = dir.join(unique);
        let _ = std::fs::remove_file(&path);
        std::env::set_var("DATABASE_URL", format!("sqlite:///{}", path.display()));
        crate::core::database::create_all().unwrap();
        TmpDb(path)
    }

    fn now_iso() -> String {
        crate::pydatetime::utcnow_naive_iso()
    }

    #[allow(clippy::too_many_arguments)]
    fn seed_endpoint(
        conn: &rusqlite::Connection,
        id: &str,
        name: &str,
        base_url: &str,
        api_key: Option<&str>,
        enabled: bool,
        owner: Option<&str>,
        cached_models: Option<&str>,
    ) {
        let ts = now_iso();
        conn.execute(
            "INSERT INTO model_endpoints \
             (id, name, base_url, api_key, is_enabled, model_type, owner, cached_models, created_at, updated_at) \
             VALUES (?1, ?2, ?3, ?4, ?5, 'llm', ?6, ?7, ?8, ?8)",
            rusqlite::params![id, name, base_url, api_key, enabled as i64, owner, cached_models, ts],
        )
        .unwrap();
    }

    fn seed_session(conn: &rusqlite::Connection, id: &str, owner: Option<&str>) {
        let ts = now_iso();
        conn.execute(
            "INSERT INTO sessions (id, name, endpoint_url, model, owner, headers, created_at, updated_at) \
             VALUES (?1, 'sess', 'https://api.example.com/v1/chat/completions', 'gpt-4o', ?2, '{}', ?3, ?3)",
            rusqlite::params![id, owner, ts],
        )
        .unwrap();
    }

    #[test]
    fn normalize_model_id_from_cache_basename_match() {
        let _g = DB_TEST_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let _db = fresh_temp_db("normcache");
        {
            let conn = crate::core::database::session_local().unwrap();
            // cached_models holds the canonical path-prefixed id; requested is bare.
            seed_endpoint(
                &conn,
                "ep1",
                "Example",
                "https://api.example.com/v1",
                Some("sk-x"),
                true,
                None,
                Some(r#"["hf.co/org/llama3-8b", "gpt-4o"]"#),
            );
        }
        let sess = Session {
            endpoint_url: "https://api.example.com/v1/chat/completions".to_string(),
            model: "llama3-8b".to_string(),
            ..Default::default()
        };
        // Cache-first lookup canonicalizes to the cached spelling — no network.
        assert_eq!(
            normalize_model_id_from_cache(&sess).as_deref(),
            Some("hf.co/org/llama3-8b")
        );
    }

    #[test]
    fn normalize_model_id_from_cache_skips_disabled_and_unmatched() {
        let _g = DB_TEST_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let _db = fresh_temp_db("normcache_skip");
        {
            let conn = crate::core::database::session_local().unwrap();
            // DISABLED endpoint with the matching model — must be skipped.
            seed_endpoint(
                &conn,
                "ep_off",
                "Off",
                "https://api.example.com/v1",
                Some("sk-x"),
                false,
                None,
                Some(r#"["gpt-4o"]"#),
            );
            // Enabled endpoint but DIFFERENT base — must not match the session base.
            seed_endpoint(
                &conn,
                "ep_other",
                "Other",
                "https://api.other.com/v1",
                Some("sk-y"),
                true,
                None,
                Some(r#"["gpt-4o"]"#),
            );
        }
        let sess = Session {
            endpoint_url: "https://api.example.com/v1/chat/completions".to_string(),
            model: "gpt-4o".to_string(),
            ..Default::default()
        };
        // Disabled row ignored, other-base row ignored -> no cache hit.
        assert_eq!(normalize_model_id_from_cache(&sess), None);
    }

    #[test]
    fn resolve_session_auth_owner_scoped_exact_match_persists() {
        let _g = DB_TEST_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let _db = fresh_temp_db("auth_owner");
        {
            let conn = crate::core::database::session_local().unwrap();
            seed_session(&conn, "s1", Some("alice"));
            // Alice's enabled endpoint with the EXACT base (matches by exact url).
            seed_endpoint(
                &conn,
                "ep_alice",
                "Alice EP",
                "https://api.example.com/v1",
                Some("sk-alice"),
                true,
                Some("alice"),
                None,
            );
            // Bob's endpoint at the SAME base — must NOT be used for alice.
            seed_endpoint(
                &conn,
                "ep_bob",
                "Bob EP",
                "https://api.example.com/v1",
                Some("sk-bob"),
                true,
                Some("bob"),
                None,
            );
        }
        let mut sess = Session {
            id: "s1".to_string(),
            endpoint_url: "https://api.example.com/v1/chat/completions".to_string(),
            model: "gpt-4o".to_string(),
            owner: Some("alice".to_string()),
            ..Default::default()
        };
        resolve_session_auth(&mut sess, "s1", Some("alice"));
        // Alice's key was used, NOT bob's.
        assert_eq!(
            sess.headers.get("Authorization").map(String::as_str),
            Some("Bearer sk-alice")
        );
        // Headers were persisted back onto the session row.
        let conn = crate::core::database::session_local().unwrap();
        let stored: String = conn
            .query_row(
                "SELECT headers FROM sessions WHERE id = ?1",
                rusqlite::params!["s1"],
                |r| r.get::<_, String>(0),
            )
            .unwrap();
        assert!(stored.contains("Bearer sk-alice"), "got: {stored}");
        assert!(!stored.contains("sk-bob"), "got: {stored}");
    }

    #[test]
    fn resolve_session_auth_skips_disabled_and_loose_url() {
        let _g = DB_TEST_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let _db = fresh_temp_db("auth_skip");
        {
            let conn = crate::core::database::session_local().unwrap();
            seed_session(&conn, "s1", None);
            // DISABLED endpoint at the exact base — must be skipped (is_enabled).
            seed_endpoint(
                &conn,
                "ep_off",
                "Off",
                "https://api.example.com/v1",
                Some("sk-off"),
                false,
                None,
                None,
            );
            // Enabled endpoint whose base only LOOSELY contains the session host —
            // the EXACT-url match must reject it.
            seed_endpoint(
                &conn,
                "ep_loose",
                "Loose",
                "https://api.example.com.evil.test/v1",
                Some("sk-loose"),
                true,
                None,
                None,
            );
        }
        let mut sess = Session {
            id: "s1".to_string(),
            endpoint_url: "https://api.example.com/v1/chat/completions".to_string(),
            model: "gpt-4o".to_string(),
            ..Default::default()
        };
        resolve_session_auth(&mut sess, "s1", None);
        // No auth header was added — disabled and loose-url rows both rejected.
        assert!(sess.headers.get("Authorization").is_none());
        assert!(sess.headers.get("x-api-key").is_none());
    }

    #[test]
    fn resolve_session_auth_noop_when_already_authed() {
        let _g = DB_TEST_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let _db = fresh_temp_db("auth_noop");
        {
            let conn = crate::core::database::session_local().unwrap();
            seed_endpoint(
                &conn,
                "ep1",
                "EP",
                "https://api.example.com/v1",
                Some("sk-fresh"),
                true,
                None,
                None,
            );
        }
        let mut headers = indexmap::IndexMap::new();
        headers.insert("Authorization".to_string(), "Bearer sk-existing".to_string());
        let mut sess = Session {
            id: "s1".to_string(),
            endpoint_url: "https://api.example.com/v1/chat/completions".to_string(),
            model: "gpt-4o".to_string(),
            headers,
            ..Default::default()
        };
        resolve_session_auth(&mut sess, "s1", None);
        // Already authed -> untouched (no re-resolve from the DB).
        assert_eq!(
            sess.headers.get("Authorization").map(String::as_str),
            Some("Bearer sk-existing")
        );
    }
}
