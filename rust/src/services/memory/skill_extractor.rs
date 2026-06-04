// services/memory/skill_extractor.rs  <- services/memory/skill_extractor.py
//! Background auto-extraction of skills from complex agent runs.
//! When the agent takes >= 2 rounds or >= 2 tool calls to complete a task,
//! we ask the LLM to distill the approach into a reusable skill.

use serde_json::Value;

use crate::core::models::Session;
use crate::error::PyResult;
use crate::pylog as logger;
use crate::services::memory::skills::{AddSkill, SkillsManager};

/// `SKILL_EXTRACT_PROMPT` — the system prompt, with `{rounds}` / `{tool_count}`
/// placeholders filled by `maybe_extract_skill` (Python uses `str.format`).
const SKILL_EXTRACT_PROMPT: &str = concat!(
    "You are analyzing an AI agent's work session. The agent took {rounds} rounds ",
    "and {tool_count} tool calls to complete the task.\n\n",
    "Extract a reusable 'skill' ONLY IF the session contains a concrete, ",
    "repeatable procedure the agent could follow to solve a similar problem ",
    "ON THE COMPUTER next time (e.g. a sequence of shell commands, code, file ",
    "edits, API calls, or tool usage).\n\n",
    "Return null (the bare word, no JSON) when the session is NOT a reusable ",
    "computer procedure, including:\n",
    "- The real work happened OUTSIDE the computer (the user did something ",
    "physically, in person, on another device, or by hand) and the agent only ",
    "discussed or advised it.\n",
    "- A one-off, personal, or context-specific task that won't recur ",
    "(personal errands, a specific person/place/date, casual conversation).\n",
    "- A pure question/answer or explanation with no transferable method.\n",
    "- The agent failed, gave up, or the approach is not worth repeating.\n\n",
    "When (and only when) a genuine reusable procedure exists, return a JSON ",
    "object with:\n",
    "- \"title\": short name (under 10 words)\n",
    "- \"problem\": what was the challenge (1-2 sentences)\n",
    "- \"solution\": what worked (1-2 sentences)\n",
    "- \"steps\": array of step-by-step instructions (3-7 short steps)\n",
    "- \"tags\": array of relevant keywords (3-5 tags)\n",
    "- \"confidence\": 0.0-1.0 how reliable AND reusable this procedure is\n\n",
    "Be conservative: if in doubt, return null.\n",
    "Return ONLY valid JSON (or the bare word null), no markdown fences."
);

/// Skills the model is unsure about (or that read as one-offs) add clutter —
/// drop anything below this confidence.
const MIN_CONFIDENCE: f64 = 0.6;

/// How many recent messages to include.
const CONTEXT_WINDOW: usize = 12;

/// Coerce a JSON array (the LLM's `steps`/`tags`) into `Vec<String>` to match
/// the canonical `Skill` shape (`Skill.tags`/`procedure` are `Vec<String>`).
/// Python passes the raw list through `list(...)`; the ported `Skill` is typed
/// `Vec<String>`, so string items are taken verbatim and any non-string item is
/// rendered with its JSON text (preserving the element, never silently dropped).
fn _json_to_string_list(value: Option<&Value>) -> Vec<String> {
    match value.and_then(|v| v.as_array()) {
        Some(arr) => arr
            .iter()
            .map(|item| match item.as_str() {
                Some(s) => s.to_string(),
                None => item.to_string(),
            })
            .collect(),
        None => Vec::new(),
    }
}

/// Extract a skill if the agent run was complex enough.
///
/// Mirrors the Python `maybe_extract_skill`. NOTE: the Python signature takes
/// `headers: dict` (required, not optional). `llm_call_async` is called without
/// `temperature`/`max_tokens`, so we pass the `LLMConfig` defaults
/// (`DEFAULT_TEMPERATURE` / `DEFAULT_MAX_TOKENS`) and the explicit `timeout=30`.
///
/// Returns `PyResult<Option<Value>>` for parity with the rest of the port, but
/// — exactly like Python — it NEVER propagates an error: every failure path is
/// logged and collapsed to `Ok(None)`.
#[allow(clippy::too_many_arguments)]
pub async fn maybe_extract_skill(
    session: &Session,
    skills_manager: &SkillsManager,
    endpoint_url: &str,
    model: &str,
    headers: indexmap::IndexMap<String, String>,
    round_count: i64,
    tool_count: i64,
    owner: Option<&str>,
) -> PyResult<Option<Value>> {
    if model.is_empty() {
        logger::debug("[skill-extract] No model provided, skipping");
        return Ok(None);
    }

    // Quiet by default; flip to DEBUG when chasing extractor issues.
    logger::debug(&format!(
        "[skill-extract] start: rounds={round_count} tools={tool_count} model={model} owner={owner:?}",
    ));
    if round_count < 2 && tool_count < 2 {
        logger::debug("[skill-extract] BELOW threshold (need rounds>=2 or tools>=2)");
        return Ok(None);
    }

    // The whole body is wrapped to mirror Python's `try/except` — any error is
    // logged and turned into `None`, never raised.
    match _extract_inner(
        session,
        skills_manager,
        endpoint_url,
        model,
        headers,
        round_count,
        tool_count,
        owner,
    )
    .await
    {
        Ok(result) => Ok(result),
        Err(e) => {
            // Real exceptions stay at WARNING so they don't get lost when users
            // only have the default log level.
            logger::warning(&format!("[skill-extract] FAILED: {e}"));
            Ok(None)
        }
    }
}

#[allow(clippy::too_many_arguments)]
async fn _extract_inner(
    session: &Session,
    skills_manager: &SkillsManager,
    endpoint_url: &str,
    model: &str,
    headers: indexmap::IndexMap<String, String>,
    round_count: i64,
    tool_count: i64,
    owner: Option<&str>,
) -> PyResult<Option<Value>> {
    use crate::src::llm_core::{llm_call_async, LLMConfig};

    // Get recent messages.
    let history = session.get_context_messages();
    let recent: &[Value] = if history.len() > CONTEXT_WINDOW {
        &history[history.len() - CONTEXT_WINDOW..]
    } else {
        &history[..]
    };
    if recent.is_empty() {
        logger::debug("[skill-extract] no recent messages, skipping");
        return Ok(None);
    }

    // Strip media (images/audio) from messages. For each message whose content
    // is a list, keep only the `type == "text"` blocks; a message that had
    // content but is left with NO text blocks (media-only) is dropped. Non-list
    // content (a string, or absent -> "") passes through verbatim. Mirrors
    // Python's `stripped_recent` build exactly, including the `{"role", "content"}`
    // re-shaping where the list content is replaced by the text-only list.
    let mut stripped_recent: Vec<(Value, Value)> = Vec::new();
    for msg in recent {
        // `msg.get("role")` (no default) -> Python `None` when absent; preserved
        // as JSON null so the conv_lines loop applies its own `"?"` default.
        let role = msg.get("role").cloned().unwrap_or(Value::Null);
        // `content = msg.get("content", "")`.
        let content = msg.get("content").cloned().unwrap_or(Value::String(String::new()));
        let content = if let Value::Array(blocks) = &content {
            // text_only = [b for b in content if dict and b["type"] == "text"]
            let text_only: Vec<Value> = blocks
                .iter()
                .filter(|b| {
                    b.is_object() && b.get("type").and_then(|t| t.as_str()) == Some("text")
                })
                .cloned()
                .collect();
            // `if not text_only and content: continue` — media-only, drop it.
            if text_only.is_empty() && !blocks.is_empty() {
                continue;
            }
            Value::Array(text_only)
        } else {
            content
        };
        stripped_recent.push((role, content));
    }

    // `if not stripped_recent: return None` — no text messages remain.
    if stripped_recent.is_empty() {
        return Ok(None);
    }

    // Build conversation summary for extraction.
    let mut conv_lines: Vec<String> = Vec::new();
    for (role, content) in &stripped_recent {
        // `msg.get("role", "?")` — a non-string / null role falls back to "?".
        let role = role.as_str().unwrap_or("?").to_string();
        // content may be a str or a list of content blocks.
        let mut content = match content {
            Value::String(s) => s.clone(),
            Value::Array(blocks) => {
                // " ".join(b["text"] for b in content if dict and type=="text")
                let parts: Vec<String> = blocks
                    .iter()
                    .filter(|b| b.get("type").and_then(|t| t.as_str()) == Some("text"))
                    .map(|b| b.get("text").and_then(|t| t.as_str()).unwrap_or("").to_string())
                    .collect();
                parts.join(" ")
            }
            // Python `msg.get("content", "")` -> "" when absent or non-str/list.
            _ => String::new(),
        };
        // Truncate long messages.
        if content.chars().count() > 500 {
            content = format!("{}...", content.chars().take(500).collect::<String>());
        }
        conv_lines.push(format!("[{role}] {content}"));
    }

    let conversation = conv_lines.join("\n");

    let prompt = SKILL_EXTRACT_PROMPT
        .replace("{rounds}", &round_count.to_string())
        .replace("{tool_count}", &tool_count.to_string());

    let t0 = crate::pytime::monotonic();
    logger::debug(&format!(
        "[skill-extract] calling LLM (endpoint={endpoint_url}, ctx={} msgs, timeout=30s)",
        recent.len(),
    ));
    let response = llm_call_async(
        endpoint_url,
        model,
        vec![
            serde_json::json!({"role": "system", "content": prompt}),
            serde_json::json!({"role": "user", "content": format!("Conversation:\n{conversation}")}),
        ],
        // llm_call_async without temperature/max_tokens -> LLMConfig defaults.
        LLMConfig::DEFAULT_TEMPERATURE,
        LLMConfig::DEFAULT_MAX_TOKENS,
        headers,
        30,
    )
    .await?;
    logger::debug(&format!(
        "[skill-extract] LLM returned in {:.1}s (len={}, head={:?})",
        crate::pytime::monotonic() - t0,
        response.chars().count(),
        response.chars().take(80).collect::<String>(),
    ));

    if response.is_empty() || response.trim().to_lowercase() == "null" {
        logger::debug(
            "[skill-extract] LLM declined (returned null/empty) — \
             session deemed not a reusable procedure",
        );
        return Ok(None);
    }

    // Some models (MiniMax, Qwen-Thinker, DeepSeek-R1) emit chain-of-thought
    // BEFORE the JSON even when asked for raw JSON. `strip_think(prose=True,
    // prompt_echo=True)` removes <think>…</think> tags AND prose-style "Let me
    // analyze this…" preambles. Without it, JSON parsing bombed on character 0.
    // (Python wraps this in try/except and ignores failures — strip_think here
    // is infallible, so no guard is needed.)
    let response = crate::src::text_helpers::strip_think(&response, true, true);

    // Parse JSON.
    let mut text = response.trim().to_string();
    if text.starts_with("```") {
        // text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
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
    // After strip_think, the JSON may still be embedded inside surrounding
    // commentary — slice from the first '{' to the matching last '}'.
    if !text.is_empty() && !text.starts_with('{') {
        let start = text.find('{');
        let end = text.rfind('}');
        if let (Some(start), Some(end)) = (start, end) {
            if start < end {
                text = text[start..=end].to_string();
            }
        }
    }

    // json.JSONDecodeError -> debug + None (Python's dedicated except branch).
    let data: Value = match serde_json::from_str(&text) {
        Ok(v) => v,
        Err(e) => {
            logger::debug(&format!("[skill-extract] non-JSON LLM response, dropping: {e}"));
            return Ok(None);
        }
    };
    // `if not data or not isinstance(data, dict)`: a non-object, or an empty
    // object ({} is falsy in Python), drops.
    let obj = match data.as_object() {
        Some(o) if !o.is_empty() => o,
        _ => {
            logger::debug("[skill-extract] parsed JSON not a dict, dropping");
            return Ok(None);
        }
    };

    let title = obj
        .get("title")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .trim()
        .to_string();
    if title.is_empty() {
        logger::debug("[skill-extract] LLM returned object with no title, dropping");
        return Ok(None);
    }

    // Honour the model's own reliability/reusability estimate. Python:
    // `float(data.get("confidence", 0.7))`, falling back to 0.7 on TypeError /
    // ValueError (a missing key, or a non-numeric value).
    let conf = _coerce_confidence(obj.get("confidence"));
    if conf < MIN_CONFIDENCE {
        logger::debug(&format!(
            "[skill-extract] '{title}' below confidence floor ({conf:.2} < {MIN_CONFIDENCE:.2}) — dropped",
        ));
        return Ok(None);
    }

    // Check for duplicate skills (case-insensitive title match within owner).
    let existing = skills_manager.load(owner);
    for sk in &existing {
        let sk_title = sk.get("title").and_then(|v| v.as_str()).unwrap_or("");
        if sk_title.to_lowercase() == title.to_lowercase() {
            logger::debug(&format!(
                "[skill-extract] '{title}' already exists — dropped as duplicate",
            ));
            return Ok(None);
        }
    }

    // `add_skill(...)` — Python passes the raw `data.get("confidence", 0.7)`
    // (NOT the floored `_conf`) to `add_skill`, which `float()`s it; we mirror
    // that by re-coercing the raw value here.
    let args = AddSkill {
        title: title.clone(),
        problem: obj
            .get("problem")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string(),
        solution: obj
            .get("solution")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string(),
        // Python `data.get("steps", [])` / `data.get("tags", [])` — always a
        // (possibly empty) list, never `None`, so pass `Some(...)`.
        steps: Some(_json_to_string_list(obj.get("steps"))),
        tags: Some(_json_to_string_list(obj.get("tags"))),
        source: "learned".to_string(),
        confidence: _coerce_confidence(obj.get("confidence")),
        // Python `getattr(session, "session_id", None)` — the `Session` dataclass
        // has NO `session_id` attribute (only `id`/`name`), so this is always
        // `None`. Preserved verbatim.
        session_id: None,
        owner: owner.map(|s| s.to_string()),
        ..Default::default()
    };
    // `add_skill` is infallible in the Rust port (returns a `Map`, not a
    // `Result`) — it logs and never raises, so no `?`.
    let entry = skills_manager.add_skill(args);

    // fire_event("skill_added", owner) — Python wraps in try/except and ignores
    // failures; `fire_event` is infallible here.
    crate::src::event_bus::fire_event("skill_added", owner);

    let entry_id = entry.get("id").and_then(|v| v.as_str()).unwrap_or("");
    logger::info(&format!("Auto-extracted skill: {title} (id={entry_id})"));
    Ok(Some(Value::Object(entry)))
}

/// `float(data.get("confidence", 0.7))` with the Python `except (TypeError,
/// ValueError)` fallback to `0.7`. A missing key, a JSON null, or a value that
/// is neither a number nor a numeric string all fall back to `0.7`.
fn _coerce_confidence(value: Option<&Value>) -> f64 {
    match value {
        None | Some(Value::Null) => 0.7,
        Some(Value::Number(n)) => n.as_f64().unwrap_or(0.7),
        Some(Value::String(s)) => s.trim().parse::<f64>().unwrap_or(0.7),
        // bool / array / object -> float() raises TypeError in Python -> 0.7.
        Some(_) => 0.7,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn coerce_confidence_matches_python_float_fallback() {
        assert_eq!(_coerce_confidence(Some(&json!(0.95))), 0.95);
        assert_eq!(_coerce_confidence(Some(&json!("0.42"))), 0.42);
        // missing / null / non-numeric -> 0.7
        assert_eq!(_coerce_confidence(None), 0.7);
        assert_eq!(_coerce_confidence(Some(&json!(null))), 0.7);
        assert_eq!(_coerce_confidence(Some(&json!("high"))), 0.7);
        assert_eq!(_coerce_confidence(Some(&json!(["x"]))), 0.7);
    }

    #[test]
    fn json_to_string_list_coerces_items() {
        assert_eq!(
            _json_to_string_list(Some(&json!(["a", "b"]))),
            vec!["a".to_string(), "b".to_string()]
        );
        // non-string items keep their JSON text; missing -> empty.
        assert_eq!(
            _json_to_string_list(Some(&json!([1, "b"]))),
            vec!["1".to_string(), "b".to_string()]
        );
        assert!(_json_to_string_list(None).is_empty());
        assert!(_json_to_string_list(Some(&json!("notarray"))).is_empty());
    }

    use std::sync::atomic::{AtomicU64, Ordering};
    static COUNTER: AtomicU64 = AtomicU64::new(0);

    fn temp_dir() -> String {
        let n = COUNTER.fetch_add(1, Ordering::SeqCst);
        let base = std::env::temp_dir()
            .join(format!("odysseus-skillext-test-{}-{}", std::process::id(), n));
        base.to_string_lossy().into_owned()
    }

    /// Python `if not model: ... return None` — an empty resolved model short-
    /// circuits before any LLM call or skill is created, even when the round /
    /// tool counts are above the extraction threshold.
    #[tokio::test]
    async fn empty_model_short_circuits() {
        let session = Session::default();
        let dir = temp_dir();
        let mgr = SkillsManager::new(&dir);

        let result = maybe_extract_skill(
            &session,
            &mgr,
            "http://localhost:1234/v1/chat/completions",
            "", // empty model -> early Ok(None)
            indexmap::IndexMap::new(),
            5, // well above the rounds>=2 / tools>=2 threshold
            5,
            None,
        )
        .await
        .expect("maybe_extract_skill never errors");
        assert!(result.is_none(), "empty model must skip extraction");

        let _ = std::fs::remove_dir_all(&dir);
    }
}
