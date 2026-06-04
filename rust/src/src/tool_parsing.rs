// src/tool_parsing.rs  <- src/tool_parsing.py
//! tool_parsing.py
//!
//! Regex-based parsing of tool invocations from LLM response text.
//! Supports fenced code blocks, [TOOL_CALL] blocks, and XML-style <invoke> blocks.
//!
//! In Rust there is no module-load cycle: this module owns `_TOOL_NAME_MAP`
//! (the CANONICAL alias map — `tool_schemas` imports it from here, matching the
//! Python `from src.tool_parsing import _TOOL_NAME_MAP`), and the XML/tool-code
//! parsers call `crate::src::tool_schemas::function_call_to_tool_block` directly
//! (the Python's lazy `from src.tool_schemas import function_call_to_tool_block`
//! that worked around the import cycle).

use fancy_regex::Regex as FancyRegex;
use indexmap::IndexMap;
use once_cell::sync::Lazy;
use regex::Regex;

use crate::src::agent_tools::{ToolBlock, TOOL_TAGS};
use crate::src::tool_schemas::function_call_to_tool_block;

// ---------------------------------------------------------------------------
// Regex patterns
// ---------------------------------------------------------------------------

// Pattern 1: ```bash ... ``` fenced code blocks
//
// Python: re.compile(r"```(" + "|".join(TOOL_TAGS) + r")\s*\n([\s\S]*?)```",
//                     re.IGNORECASE)
// `TOOL_TAGS` is a Python set, so the alternation order is unspecified; the
// match result does not depend on it (alternation matches any member). We join
// the same members here. (?i) = re.IGNORECASE; [\s\S] is the DOTALL-free
// "any char" idiom — kept verbatim.
static _TOOL_BLOCK_RE: Lazy<Regex> = Lazy::new(|| {
    let alternation: Vec<&str> = TOOL_TAGS.iter().copied().collect();
    let pat = format!(
        r"(?i)```({})\s*\n([\s\S]*?)```",
        alternation.join("|")
    );
    Regex::new(&pat).unwrap()
});

// Pattern 2: [TOOL_CALL] ... [/TOOL_CALL] blocks (some models use this format)
// Matches: {tool => "shell", args => {--command "ls -la"}} etc.
static _TOOL_CALL_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"(?i)\[TOOL_CALL\]\s*\{([\s\S]*?)\}\s*\[/TOOL_CALL\]").unwrap());

// Pattern 3: XML-style tool calls (minimax, some other models)
// <minimax:tool_call><invoke name="bash"><parameter name="command">...</parameter></invoke></minimax:tool_call>
// Also handles: <tool_call><invoke ...>, <function_call><invoke ...>, plain <invoke ...>
static _XML_TOOL_CALL_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"(?i)<(?:[\w]+:)?(?:tool_call|function_call)>\s*([\s\S]*?)</(?:[\w]+:)?(?:tool_call|function_call)>",
    )
    .unwrap()
});
static _XML_INVOKE_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r#"(?i)<invoke\s+name=["'](\w+)["']>\s*([\s\S]*?)</invoke>"#).unwrap()
});
static _XML_PARAM_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r#"(?i)<parameter\s+name=["'](\w+)["']>([\s\S]*?)</parameter>"#).unwrap()
});

// Pattern 4: <tool_code> blocks (MiniMax-M2.5 style)
// {tool => 'tool_name', args => '<param>value</param>'}
static _TOOL_CODE_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"(?i)<tool_code>\s*\{([\s\S]*?)\}\s*</tool_code>").unwrap());

// Pattern 5: DeepSeek DSML markup leaking into content. When deepseek
// models can't emit structured tool_calls (e.g. we sent no tool schemas
// that round, or the API didn't parse them), they fall back to raw
// markup using fullwidth-pipe delimiters:
//   <｜｜DSML｜｜tool_calls>
//     <｜｜DSML｜｜invoke name="web_search">
//       <｜｜DSML｜｜parameter name="query" string="true">QUERY</｜｜DSML｜｜parameter>
//     </｜｜DSML｜｜invoke>
//   </｜｜DSML｜｜tool_calls>
// We normalize it into the standard <invoke>/<parameter> form so the
// existing XML parser + stripper handle it (parse → execute; strip →
// never show the garbage to the user). The pipe run is tolerant of
// fullwidth (U+FF5C) and ascii '|' in any count.
//
// Python: _DSML_PIPES = r"[｜|]+" — the first char is U+FF5C (FULLWIDTH
// VERTICAL LINE). We keep that literal codepoint, escaped as \u{FF5C} in the
// Rust char class so it is unambiguous in source.
const _DSML_PIPES: &str = r"[\u{FF5C}|]+";

// The six DSML normalization patterns, compiled once. Each mirrors a re.sub
// call in `_normalize_dsml`. The fifth pattern (parameter open) uses a capture
// group whose contents are re-emitted; the rest are plain literal rewrites.
static _DSML_TOOL_CALLS_OPEN_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(&format!(
        r"(?i)<\s*{p}\s*DSML\s*{p}\s*tool_calls\s*>",
        p = _DSML_PIPES
    ))
    .unwrap()
});
static _DSML_TOOL_CALLS_CLOSE_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(&format!(
        r"(?i)<\s*/\s*{p}\s*DSML\s*{p}\s*tool_calls\s*>",
        p = _DSML_PIPES
    ))
    .unwrap()
});
static _DSML_INVOKE_OPEN_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(&format!(
        r"(?i)<\s*{p}\s*DSML\s*{p}\s*invoke\s+name=",
        p = _DSML_PIPES
    ))
    .unwrap()
});
static _DSML_INVOKE_CLOSE_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(&format!(
        r"(?i)<\s*/\s*{p}\s*DSML\s*{p}\s*invoke\s*>",
        p = _DSML_PIPES
    ))
    .unwrap()
});
// parameter open tag — drop any extra attrs (e.g. string="true").
static _DSML_PARAM_OPEN_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(&format!(
        r#"(?i)<\s*{p}\s*DSML\s*{p}\s*parameter\s+name=(["'][^"']+["'])[^>]*>"#,
        p = _DSML_PIPES
    ))
    .unwrap()
});
static _DSML_PARAM_CLOSE_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(&format!(
        r"(?i)<\s*/\s*{p}\s*DSML\s*{p}\s*parameter\s*>",
        p = _DSML_PIPES
    ))
    .unwrap()
});

/// `_normalize_dsml(text)` — rewrite DeepSeek DSML markup into the standard
/// `<invoke>`/`<parameter>` form so the XML parser + stripper handle it.
fn _normalize_dsml(text: &str) -> String {
    // if "DSML" not in text: return text
    if !text.contains("DSML") {
        return text.to_string();
    }
    let mut t = text.to_string();
    t = _DSML_TOOL_CALLS_OPEN_RE.replace_all(&t, "<tool_call>").into_owned();
    t = _DSML_TOOL_CALLS_CLOSE_RE.replace_all(&t, "</tool_call>").into_owned();
    t = _DSML_INVOKE_OPEN_RE.replace_all(&t, "<invoke name=").into_owned();
    t = _DSML_INVOKE_CLOSE_RE.replace_all(&t, "</invoke>").into_owned();
    // r"<parameter name=\1>" — \1 here is a REPLACEMENT backreference (the
    // captured quoted name), which `regex` writes as "${1}".
    t = _DSML_PARAM_OPEN_RE
        .replace_all(&t, "<parameter name=${1}>")
        .into_owned();
    t = _DSML_PARAM_CLOSE_RE.replace_all(&t, "</parameter>").into_owned();
    t
}

// Map model tool names to our tool types.
//
// CANONICAL owner of the alias map: `tool_schemas` imports this (mirroring the
// Python `from src.tool_parsing import _TOOL_NAME_MAP`). An `IndexMap` keeps the
// Python dict insertion order; order is not behaviourally load-bearing for a
// lookup table, but we preserve it to stay faithful.
pub static _TOOL_NAME_MAP: Lazy<IndexMap<&'static str, &'static str>> = Lazy::new(|| {
    IndexMap::from([
        ("shell", "bash"),
        ("bash", "bash"),
        ("terminal", "bash"),
        ("command", "bash"),
        ("execute", "bash"),
        ("run", "bash"),
        ("python", "python"),
        ("code", "python"),
        ("search", "web_search"),
        ("web_search", "web_search"),
        ("websearch", "web_search"),
        ("google_search", "web_search"),
        ("google_search_retrieval", "web_search"),
        ("google_search_grounding", "web_search"),
        ("web_fetch", "web_fetch"),
        ("webfetch", "web_fetch"),
        ("fetch_url", "web_fetch"),
        ("fetch", "web_fetch"),
        ("read", "read_file"),
        ("read_file", "read_file"),
        ("cat", "read_file"),
        ("write", "write_file"),
        ("write_file", "write_file"),
        ("save", "write_file"),
        ("document", "update_document"),
        ("update_document", "update_document"),
        ("create_document", "create_document"),
        ("edit", "edit_document"),
        ("edit_document", "edit_document"),
        ("search_chats", "search_chats"),
        ("search_conversations", "search_chats"),
        ("find_chat", "search_chats"),
        ("chat_with_model", "chat_with_model"),
        ("ask_model", "chat_with_model"),
        ("chat_model", "chat_with_model"),
        ("create_session", "create_session"),
        ("new_session", "create_session"),
        ("list_sessions", "list_sessions"),
        ("send_to_session", "send_to_session"),
        ("message_session", "send_to_session"),
        ("pipeline", "pipeline"),
        ("chain", "pipeline"),
        ("manage_session", "manage_session"),
        ("session_control", "manage_session"),
        ("manage_memory", "manage_memory"),
        ("memory", "manage_memory"),
        ("manage_tasks", "manage_tasks"),
        ("tasks", "manage_tasks"),
        ("schedule", "manage_tasks"),
        ("list_models", "list_models"),
        ("models", "list_models"),
        ("available_models", "list_models"),
        ("ui_control", "ui_control"),
        ("ui", "ui_control"),
        ("control", "ui_control"),
        ("api_call", "api_call"),
        ("api", "api_call"),
        ("integration", "api_call"),
        ("ask_teacher", "ask_teacher"),
        ("teacher", "ask_teacher"),
        ("manage_skills", "manage_skills"),
        ("skills", "manage_skills"),
        ("skill", "manage_skills"),
        ("suggest_document", "suggest_document"),
        ("suggest", "suggest_document"),
        ("review_document", "suggest_document"),
        ("manage_endpoints", "manage_endpoints"),
        ("endpoints", "manage_endpoints"),
        ("manage_mcp", "manage_mcp"),
        ("mcp_servers", "manage_mcp"),
        ("manage_webhooks", "manage_webhooks"),
        ("webhooks", "manage_webhooks"),
        ("manage_tokens", "manage_tokens"),
        ("tokens", "manage_tokens"),
        ("manage_documents", "manage_documents"),
        ("documents", "manage_documents"),
        ("manage_research", "manage_research"),
        ("list_research", "manage_research"),
        ("read_research", "manage_research"),
        ("open_research", "manage_research"),
        ("delete_research", "manage_research"),
        ("manage_settings", "manage_settings"),
        ("settings", "manage_settings"),
        ("preferences", "manage_settings"),
        ("manage_notes", "manage_notes"),
        ("notes", "manage_notes"),
        ("todo", "manage_notes"),
        ("todos", "manage_notes"),
    ])
});

// ---------------------------------------------------------------------------
// Parsing functions
// ---------------------------------------------------------------------------

// Helpers for the ad-hoc per-call regexes inside `_parse_tool_call_block`.
// These mirror the Python `re.search(...)` calls. `re.DOTALL` -> (?s).
static _TC_TOOL_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r#"(?i)tool\s*(?:=>|:|=)\s*["']?(\w+)["']?"#).unwrap());
static _TC_DASH_COMMAND_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r#"(?s)--command\s+["'](.+?)["']"#).unwrap());
static _TC_COMMAND_KV_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r#"(?s)command\s*(?:=>|:|=)\s*["'](.+?)["']"#).unwrap());
static _TC_ARGS_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"(?s)args\s*(?:=>|:|=)\s*\{([\s\S]*)\}").unwrap());
static _TC_ARGS_PREFIX_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"^--?\w+\s+").unwrap());
static _TC_LEADING_PUNCT_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"^[,;]\s*").unwrap());

/// Parse a `[TOOL_CALL]` block into a `ToolBlock`.
///
/// Handles formats like:
///   {tool => "shell", args => {--command "ls -la"}}
///   {tool: "shell", command: "ls -la"}
fn _parse_tool_call_block(raw: &str) -> Option<ToolBlock> {
    // Try to extract tool name
    let tool_match = _TC_TOOL_RE.captures(raw)?;
    let tool_name = tool_match.get(1).unwrap().as_str().to_lowercase();

    // Fall back to the raw name when it's a real tool but not in the alias
    // map, so known tools (e.g. manage_calendar) aren't silently dropped.
    let mapped: Option<&str> = match _TOOL_NAME_MAP.get(tool_name.as_str()) {
        Some(m) => Some(*m),
        None => {
            if TOOL_TAGS.contains(tool_name.as_str()) {
                // `tool_name in TOOL_TAGS` -> use the raw (still-owned) name.
                Some(tool_name.as_str())
            } else {
                None
            }
        }
    };
    let mapped = mapped?;
    // Own the mapped name so it can outlive the `tool_name` borrow above when
    // it was the fall-through-to-raw case.
    let mapped: String = mapped.to_string();

    // Extract the command/content — try several patterns
    let mut content: Option<String> = None;

    // Pattern: --command "value" or --command 'value'
    if let Some(c) = _TC_DASH_COMMAND_RE.captures(raw) {
        content = Some(c.get(1).unwrap().as_str().to_string());
    }

    // Pattern: command => "value" or command: "value"
    if content.is_none() {
        if let Some(c) = _TC_COMMAND_KV_RE.captures(raw) {
            content = Some(c.get(1).unwrap().as_str().to_string());
        }
    }

    // Pattern: args => {content} — extract everything inside the nested braces
    if content.is_none() {
        if let Some(c) = _TC_ARGS_RE.captures(raw) {
            let inner = c.get(1).unwrap().as_str().trim();
            // Strip quotes and key prefixes
            let inner = _TC_ARGS_PREFIX_RE.replace(inner, "");
            // inner.strip('\'"')  — strip a leading/trailing run of ' and "
            let inner = inner.trim_matches(|ch| ch == '\'' || ch == '"');
            if !inner.is_empty() {
                content = Some(inner.to_string());
            }
        }
    }

    // Pattern: query/path/code => "value"
    if content.is_none() {
        for key in ["query", "path", "code", "content", "text", "file"] {
            let re = Regex::new(&format!(
                r#"(?s){key}\s*(?:=>|:|=)\s*["'](.+?)["']"#
            ))
            .unwrap();
            if let Some(m) = re.captures(raw) {
                content = Some(m.get(1).unwrap().as_str().to_string());
                break;
            }
        }
    }

    // Last resort: take everything after the tool declaration
    if content.is_none() {
        let end = tool_match.get(0).unwrap().end();
        let rest = raw[end..].trim();
        let rest = _TC_LEADING_PUNCT_RE.replace(rest, "");
        // rest.strip('{} \t\n\'"')
        let rest = rest.trim_matches(|ch: char| {
            matches!(ch, '{' | '}' | ' ' | '\t' | '\n' | '\'' | '"')
        });
        if !rest.is_empty() {
            content = Some(rest.to_string());
        }
    }

    // if content: return ToolBlock(mapped, content.strip())  /  return None
    content.map(|c| ToolBlock::new(&mapped, c.trim()))
}

/// Parse an `<invoke name="tool"><parameter ...>...</parameter></invoke>` match.
///
/// Delegates content-shaping to `function_call_to_tool_block` — the SAME
/// converter used for native function calls — so the full tool set (every name
/// in `TOOL_TAGS`, plus email + MCP tools) and the correct per-tool content
/// format are handled in ONE place.
///
/// `inv` is a `regex::Captures` of `_XML_INVOKE_RE` (group 1 = tool name,
/// group 2 = body).
fn _parse_xml_invoke(inv: &regex::Captures) -> Option<ToolBlock> {
    // Lowercase the tool name: models often emit capitalized invoke names
    // (e.g. <invoke name="Bash">) and function_call_to_tool_block matches
    // case-sensitively against the lowercase _TOOL_NAME_MAP / TOOL_TAGS.
    let tool_name = inv.get(1).unwrap().as_str().to_lowercase();
    let body = inv.get(2).unwrap().as_str();
    // params preserves insertion order (Python dict / json.dumps order).
    let mut params: serde_json::Map<String, serde_json::Value> = serde_json::Map::new();
    for pm in _XML_PARAM_RE.captures_iter(body) {
        let key = pm.get(1).unwrap().as_str().to_string();
        let val = pm.get(2).unwrap().as_str().trim().to_string();
        params.insert(key, serde_json::Value::String(val));
    }
    let args_json = serde_json::to_string(&serde_json::Value::Object(params)).unwrap();
    function_call_to_tool_block(&tool_name, &args_json)
}

// Helpers for `_parse_tool_code_block`.
static _TCODE_TOOL_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r#"tool\s*=>\s*['"](\S+?)['"]"#).unwrap());
static _TCODE_ARGS_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r#"(?s)args\s*=>\s*['"]?\s*([\s\S]*?)\s*['"]?\s*$"#).unwrap());
// Inner XML params with a backreference (</\1>) — needs fancy-regex.
static _TCODE_XML_PARAM_RE: Lazy<FancyRegex> =
    Lazy::new(|| FancyRegex::new(r"<(\w+)>([\s\S]*?)</\1>").unwrap());

/// Parse a `<tool_code>{tool => 'name', args => '...'}</tool_code>` block
/// (MiniMax style).
fn _parse_tool_code_block(raw: &str) -> Option<ToolBlock> {
    // Extract tool name
    let tool_match = _TCODE_TOOL_RE.captures(raw)?;
    let mut tool_name = tool_match.get(1).unwrap().as_str().to_lowercase().replace('-', "_");
    // Strip MCP prefixes like "mcp__server__" or "cli-mcp-server-"
    for prefix in ["mcp__", "cli_mcp_server_", "desktop_commander_", "mcp_code_executor_"] {
        if tool_name.starts_with(prefix) {
            tool_name = tool_name[prefix.len()..].to_string();
            break;
        }
    }

    let mapped: Option<&str> = _TOOL_NAME_MAP.get(tool_name.as_str()).copied();

    // Extract args content
    let args_body: String = if let Some(c) = _TCODE_ARGS_RE.captures(raw) {
        // .strip().strip("'\"")
        c.get(1)
            .unwrap()
            .as_str()
            .trim()
            .trim_matches(|ch| ch == '\'' || ch == '"')
            .to_string()
    } else {
        String::new()
    };

    // Parse XML params inside args (e.g. <command>ls</command>)
    let mut xml_params: IndexMap<String, String> = IndexMap::new();
    for pm in _TCODE_XML_PARAM_RE.captures_iter(&args_body) {
        let pm = pm.unwrap();
        let key = pm.get(1).unwrap().as_str().to_string();
        let val = pm.get(2).unwrap().as_str().trim().to_string();
        xml_params.insert(key, val);
    }

    // When the model gave structured params, hand them to the canonical
    // converter (same as native calls + <invoke>) so the full tool set and
    // correct per-tool content format apply — not a partial map + k:v blob.
    if !xml_params.is_empty() {
        let mut obj: serde_json::Map<String, serde_json::Value> = serde_json::Map::new();
        for (k, v) in &xml_params {
            obj.insert(k.clone(), serde_json::Value::String(v.clone()));
        }
        let args_json = serde_json::to_string(&serde_json::Value::Object(obj)).unwrap();
        // function_call_to_tool_block(mapped or tool_name, ...)
        let name_for_converter: &str = mapped.unwrap_or(tool_name.as_str());
        if let Some(block) = function_call_to_tool_block(name_for_converter, &args_json) {
            return Some(block);
        }
    }

    // No structured params: args_body is a raw single value (e.g. a bash
    // command). Keep the freeform special-casing for the simple tools.
    if let Some(mapped) = mapped {
        let content: Option<String> = if mapped == "bash" {
            Some(xml_params.get("command").cloned().unwrap_or_else(|| args_body.clone()))
        } else if mapped == "python" {
            Some(xml_params.get("code").cloned().unwrap_or_else(|| args_body.clone()))
        } else if mapped == "web_search" {
            Some(xml_params.get("query").cloned().unwrap_or_else(|| args_body.clone()))
        } else if mapped == "web_fetch" {
            Some(xml_params.get("url").cloned().unwrap_or_else(|| args_body.clone()))
        } else if mapped == "read_file" || mapped == "write_file" {
            // xml_params.get("path", xml_params.get("file_path", args_body))
            Some(
                xml_params
                    .get("path")
                    .cloned()
                    .or_else(|| xml_params.get("file_path").cloned())
                    .unwrap_or_else(|| args_body.clone()),
            )
        } else {
            // "\n".join(f"{k}: {v}" ...) if xml_params else args_body
            if !xml_params.is_empty() {
                Some(
                    xml_params
                        .iter()
                        .map(|(k, v)| format!("{k}: {v}"))
                        .collect::<Vec<_>>()
                        .join("\n"),
                )
            } else {
                Some(args_body.clone())
            }
        };
        // `if content:` — an empty string is falsy in Python.
        if let Some(content) = content {
            if !content.is_empty() {
                return Some(ToolBlock::new(mapped, content.trim()));
            }
        }
    } else if !tool_name.is_empty() && !args_body.is_empty() {
        // Unknown tool — try as MCP tool call
        let content = if !xml_params.is_empty() {
            xml_params
                .iter()
                .map(|(k, v)| format!("{k}: {v}"))
                .collect::<Vec<_>>()
                .join("\n")
        } else {
            args_body.clone()
        };
        return Some(ToolBlock::new(&tool_name, content.trim()));
    }
    None
}

/// Extract executable tool blocks from LLM response text.
///
/// Supports multiple formats:
/// 1. ```bash ... ``` fenced code blocks (standard)
/// 2. [TOOL_CALL] ... [/TOOL_CALL] blocks (some models)
/// 3. XML-style <tool_call>/<invoke> blocks
/// 4. <tool_code> blocks (MiniMax-M2.5 style)
/// 5. DeepSeek DSML markup (normalized to <invoke> first)
pub fn parse_tool_blocks(text: &str) -> Vec<ToolBlock> {
    let mut blocks: Vec<ToolBlock> = Vec::new();

    // Normalize DeepSeek DSML markup into standard <invoke> form so the
    // XML patterns below catch it.
    let text = _normalize_dsml(text);

    // Pattern 1: fenced code blocks
    for m in _TOOL_BLOCK_RE.captures_iter(&text) {
        let tag = m.get(1).unwrap().as_str().to_lowercase();
        let content = m.get(2).unwrap().as_str().trim();
        if content.is_empty() {
            continue;
        }
        // If a code block's content is an <invoke> XML call (some models wrap
        // tool calls in ```python or ```xml fences), parse the invoke instead.
        if content.contains("<invoke") {
            let mut invoked = false;
            for inv in _XML_INVOKE_RE.captures_iter(content) {
                if let Some(block) = _parse_xml_invoke(&inv) {
                    blocks.push(block);
                    invoked = true;
                }
            }
            if invoked {
                continue;
            }
        }
        blocks.push(ToolBlock::new(&tag, content));
    }

    // Pattern 2: [TOOL_CALL] blocks (only if no fenced blocks found)
    if blocks.is_empty() {
        for m in _TOOL_CALL_RE.captures_iter(&text) {
            if let Some(block) = _parse_tool_call_block(m.get(1).unwrap().as_str()) {
                blocks.push(block);
            }
        }
    }

    // Pattern 3: XML-style <tool_call>/<invoke> blocks
    if blocks.is_empty() {
        // Try wrapped: <tool_call><invoke ...>...</invoke></tool_call>
        for m in _XML_TOOL_CALL_RE.captures_iter(&text) {
            let inner = m.get(1).unwrap().as_str();
            for inv in _XML_INVOKE_RE.captures_iter(inner) {
                if let Some(block) = _parse_xml_invoke(&inv) {
                    blocks.push(block);
                }
            }
        }
        // Try bare <invoke> without wrapper
        if blocks.is_empty() {
            for inv in _XML_INVOKE_RE.captures_iter(&text) {
                if let Some(block) = _parse_xml_invoke(&inv) {
                    blocks.push(block);
                }
            }
        }
    }

    // Pattern 4: <tool_code> blocks (MiniMax-M2.5 style)
    if blocks.is_empty() {
        for m in _TOOL_CODE_RE.captures_iter(&text) {
            if let Some(block) = _parse_tool_code_block(m.get(1).unwrap().as_str()) {
                blocks.push(block);
            }
        }
    }

    blocks
}

// Strip-only helpers (regex .sub('', ...) equivalents).
static _STRIP_BARE_INVOKE_RE: Lazy<Regex> = Lazy::new(|| {
    // re.sub(r'<invoke\s+name=["\'].*?</invoke>', '', cleaned,
    //        flags=re.DOTALL | re.IGNORECASE)
    Regex::new(r#"(?is)<invoke\s+name=["'].*?</invoke>"#).unwrap()
});
static _STRIP_BLANKLINES_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"\n{3,}").unwrap());

/// Remove executable tool blocks from text for clean display.
pub fn strip_tool_blocks(text: &str) -> String {
    // Normalize DSML first so its markup gets stripped by the <invoke>
    // / <tool_call> removers below instead of leaking to the user.
    let text = _normalize_dsml(text);
    let cleaned = _TOOL_BLOCK_RE.replace_all(&text, "");
    let cleaned = _TOOL_CALL_RE.replace_all(&cleaned, "");
    let cleaned = _XML_TOOL_CALL_RE.replace_all(&cleaned, "");
    let cleaned = _TOOL_CODE_RE.replace_all(&cleaned, "");
    // Strip bare <invoke> blocks not wrapped in <tool_call>
    let cleaned = _STRIP_BARE_INVOKE_RE.replace_all(&cleaned, "");
    let cleaned = _STRIP_BLANKLINES_RE.replace_all(&cleaned, "\n\n");
    cleaned.trim().to_string()
}

#[cfg(test)]
mod tests {
    // Parity checks against the live Python (`src.tool_parsing` /
    // `src.tool_schemas`). Reference values captured from CPython.
    use super::*;
    use crate::src::tool_schemas::function_call_to_tool_block;

    #[test]
    fn fenced_bash_block() {
        let b = parse_tool_blocks("text\n```bash\nls -la\n```\nmore");
        assert_eq!(b.len(), 1);
        assert_eq!(b[0], ToolBlock::new("bash", "ls -la"));
    }

    #[test]
    fn tool_code_backreference_xml() {
        // The </\1> backreference path (fancy-regex). Python -> [('bash','echo hi')]
        let b = parse_tool_blocks(r#"<tool_code>{tool => "bash", args => "<command>echo hi</command>"}</tool_code>"#);
        assert_eq!(b, vec![ToolBlock::new("bash", "echo hi")]);
    }

    #[test]
    fn dsml_normalization() {
        // Fullwidth U+FF5C pipe DSML -> [('web_search', 'rust')]
        let dsml = "<\u{FF5C}\u{FF5C}DSML\u{FF5C}\u{FF5C}tool_calls><\u{FF5C}\u{FF5C}DSML\u{FF5C}\u{FF5C}invoke name=\"web_search\"><\u{FF5C}\u{FF5C}DSML\u{FF5C}\u{FF5C}parameter name=\"query\" string=\"true\">rust</\u{FF5C}\u{FF5C}DSML\u{FF5C}\u{FF5C}parameter></\u{FF5C}\u{FF5C}DSML\u{FF5C}\u{FF5C}invoke></\u{FF5C}\u{FF5C}DSML\u{FF5C}\u{FF5C}tool_calls>";
        let b = parse_tool_blocks(dsml);
        assert_eq!(b, vec![ToolBlock::new("web_search", "rust")]);
    }

    #[test]
    fn strip_blocks() {
        assert_eq!(strip_tool_blocks("hello\n```bash\nls\n```\nworld"), "hello\n\nworld");
    }

    #[test]
    fn native_create_document() {
        let blk = function_call_to_tool_block(
            "create_document",
            r#"{"title": "T", "language": "python", "content": "print(1)"}"#,
        )
        .unwrap();
        assert_eq!(blk, ToolBlock::new("create_document", "T\npython\nprint(1)"));
    }

    #[test]
    fn native_ui_control_create_theme() {
        let blk = function_call_to_tool_block(
            "ui_control",
            r##"{"action": "create_theme", "name": "neon", "colors": {"bg": "#000", "accentError": "#f00"}}"##,
        )
        .unwrap();
        assert_eq!(
            blk,
            ToolBlock::new(
                "ui_control",
                "create_theme neon #000 #9cdef2 #111111 #355a66 #e06c75 accentError=#f00"
            )
        );
    }

    #[test]
    fn native_email_passthrough() {
        let blk = function_call_to_tool_block(
            "send_email",
            r#"{"to": "a@b.c", "subject": "hi", "body": "x"}"#,
        )
        .unwrap();
        assert_eq!(
            blk,
            ToolBlock::new("mcp__email__send_email", r#"{"to": "a@b.c", "subject": "hi", "body": "x"}"#)
        );
    }

    #[test]
    fn native_manage_session_list_drops_current() {
        let blk = function_call_to_tool_block(
            "manage_session",
            r#"{"action": "list", "session_id": "current"}"#,
        )
        .unwrap();
        assert_eq!(blk, ToolBlock::new("manage_session", "list"));
    }

    #[test]
    fn native_unknown_returns_none() {
        assert!(function_call_to_tool_block("nonsense", "{}").is_none());
    }

    #[test]
    fn native_mcp_passthrough() {
        // json.dumps({"a": 1}) -> '{"a": 1}' (CPython default separators).
        let blk = function_call_to_tool_block("mcp__srv__tool", r#"{"a": 1}"#).unwrap();
        assert_eq!(blk, ToolBlock::new("mcp__srv__tool", r#"{"a": 1}"#));
    }

    #[test]
    fn tool_name_map_new_aliases() {
        // The 7 aliases added for parity with Python's _TOOL_NAME_MAP.
        assert_eq!(_TOOL_NAME_MAP.get("google_search").copied(), Some("web_search"));
        assert_eq!(
            _TOOL_NAME_MAP.get("google_search_retrieval").copied(),
            Some("web_search")
        );
        assert_eq!(
            _TOOL_NAME_MAP.get("google_search_grounding").copied(),
            Some("web_search")
        );
        assert_eq!(_TOOL_NAME_MAP.get("web_fetch").copied(), Some("web_fetch"));
        assert_eq!(_TOOL_NAME_MAP.get("webfetch").copied(), Some("web_fetch"));
        assert_eq!(_TOOL_NAME_MAP.get("fetch_url").copied(), Some("web_fetch"));
        assert_eq!(_TOOL_NAME_MAP.get("fetch").copied(), Some("web_fetch"));
    }

    #[test]
    fn tool_code_web_fetch_freeform() {
        // No structured params: the new `web_fetch` freeform branch returns the
        // raw args_body as the url (xml_params.get("url", args_body)).
        // Python -> [('web_fetch', 'https://example.com')]
        let b = parse_tool_blocks(
            r#"<tool_code>{tool => "web_fetch", args => "https://example.com"}</tool_code>"#,
        );
        assert_eq!(b, vec![ToolBlock::new("web_fetch", "https://example.com")]);
    }

    #[test]
    fn tool_code_web_fetch_structured_url() {
        // With a <url> param, the structured path delegates to
        // function_call_to_tool_block, which has no web_fetch branch and so
        // falls through to the json.dumps catch-all (web_fetch IS in TOOL_TAGS).
        // Python -> [('web_fetch', '{"url": "https://example.com"}')]
        let b = parse_tool_blocks(
            r#"<tool_code>{tool => "web_fetch", args => "<url>https://example.com</url>"}</tool_code>"#,
        );
        assert_eq!(
            b,
            vec![ToolBlock::new("web_fetch", r#"{"url": "https://example.com"}"#)]
        );
    }

    #[test]
    fn tool_code_webfetch_alias_freeform() {
        // The `webfetch` alias also maps to web_fetch; freeform path returns
        // the raw args_body. Python -> [('web_fetch', 'example.com')]
        let b = parse_tool_blocks(
            r#"<tool_code>{tool => "webfetch", args => "example.com"}</tool_code>"#,
        );
        assert_eq!(b, vec![ToolBlock::new("web_fetch", "example.com")]);
    }

    #[test]
    fn tool_code_google_search_alias() {
        // google_search alias maps to web_search; structured query param goes
        // through the converter's web_search branch.
        // Python -> [('web_search', 'rust lang')]
        let b = parse_tool_blocks(
            r#"<tool_code>{tool => "google_search", args => "<query>rust lang</query>"}</tool_code>"#,
        );
        assert_eq!(b, vec![ToolBlock::new("web_search", "rust lang")]);
    }
}
