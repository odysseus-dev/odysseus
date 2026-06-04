// Included by agent_loop.rs — the formerly web-gated surface (now always
// compiled, as the crate has no cargo feature flags): stream_agent_loop + the
// system-prompt assembly + the verifier subagent + _load_mcp_disabled_map.

// ---------------------------------------------------------------------------
// _load_mcp_disabled_map  (db; only reached when an MCP manager is present,
// which it never is in the Rust port — kept faithful for completeness)
// ---------------------------------------------------------------------------

/// `_load_mcp_disabled_map()` — load per-server disabled tool sets from the DB.
///
/// Raw SQL over `mcp_servers` (`SELECT id, disabled_tools`), mirroring the Python
/// `db.query(McpServer).all()` loop: parse the JSON `disabled_tools` column into a
/// set, keyed by server id, skipping rows with no / empty / unparseable list.
fn _load_mcp_disabled_map() -> std::collections::HashMap<String, HashSet<String>> {
    let mut disabled_map: std::collections::HashMap<String, HashSet<String>> =
        std::collections::HashMap::new();
    let conn = match crate::core::database::session_local() {
        Ok(c) => c,
        Err(_) => return disabled_map,
    };
    let mut stmt = match conn.prepare("SELECT id, disabled_tools FROM mcp_servers") {
        Ok(s) => s,
        Err(_) => return disabled_map,
    };
    let rows = stmt.query_map([], |row| {
        let id: String = row.get(0)?;
        let dt: Option<String> = row.get(1)?;
        Ok((id, dt))
    });
    if let Ok(rows) = rows {
        for r in rows.flatten() {
            let (id, dt) = r;
            if let Some(dt) = dt {
                if dt.is_empty() {
                    continue;
                }
                if let Ok(Value::Array(names)) = serde_json::from_str::<Value>(&dt) {
                    let set: HashSet<String> = names
                        .iter()
                        .filter_map(|v| v.as_str().map(|s| s.to_string()))
                        .collect();
                    if !set.is_empty() {
                        disabled_map.insert(id, set);
                    }
                }
            }
        }
    }
    disabled_map
}

// ---------------------------------------------------------------------------
// _build_base_prompt / _build_system_prompt
// ---------------------------------------------------------------------------
//
// Prompt cache globals (Python module-level `_cached_base_prompt` /
// `_cached_base_prompt_key`). Keyed by the same signature tuple the Python uses,
// serialized to a String key for the Mutex map.

static CACHED_BASE_PROMPT: Lazy<Mutex<Option<(String, String)>>> = Lazy::new(|| Mutex::new(None));

/// `_build_base_prompt(disabled_tools, mcp_mgr, needs_admin, relevant_tools, ...)`.
///
/// `relevant_tools = Some(set)` -> RAG mode (always taken in the Rust port, since
/// the keyword fallback always populates `relevant_tools`). The legacy
/// full-prompt fallback branch (RAG unavailable -> `relevant_tools is None`) is
/// preserved for completeness.
///
/// The skill-index injection (Python L952-984, `services::memory::skills`
/// `index_for`, owner=None) is returned SEPARATELY (NOT appended to the agent
/// prompt) — skill `name`/`description` are user-editable, so the index block
/// must never live in the trusted system role. The caller wraps it in
/// `untrusted_context_message` and ships it as a user-role message (same
/// treatment as the matched-skills block). The integration-descriptions
/// injection (Python L986-990, `integrations::get_integrations_prompt`) and the
/// MCP-description injection (Python L992-998: when an `Arc<McpManager>` is
/// present, `get_tool_descriptions_for_prompt` is appended; it returns `""` when
/// no MCP tools are connected) ARE part of the cached base prompt, exactly as in
/// Python. Returns `(agent_prompt, skill_index_block)`.
fn _build_base_prompt(
    disabled_tools: Option<&HashSet<String>>,
    mcp_mgr: &Option<std::sync::Arc<crate::src::mcp_manager::McpManager>>,
    needs_admin: bool,
    relevant_tools: Option<&HashSet<String>>,
    mcp_disabled_map: Option<&std::collections::HashMap<String, HashSet<String>>>,
    compact: bool,
) -> (String, String) {
    let mut disabled: HashSet<String> = disabled_tools.cloned().unwrap_or_default();
    // if not get_setting("image_gen_enabled", True): disabled.add("generate_image")
    if !setting_image_gen_enabled() {
        disabled.insert("generate_image".to_string());
    }

    let mut prompt = if let Some(relevant) = relevant_tools {
        // RAG mode: include always-available + retrieved + admin (if needed)
        let mut tool_names: HashSet<String> =
            ALWAYS_AVAILABLE.iter().map(|s| s.to_string()).collect();
        tool_names.extend(relevant.iter().cloned());
        if needs_admin {
            tool_names.extend(_ADMIN_TOOLS.iter().map(|s| s.to_string()));
        }
        _assemble_prompt(&tool_names, Some(&disabled), compact)
    } else {
        // Fallback: full prompt (RAG unavailable). Mirror the Python branch.
        let all_known: HashSet<String> = TOOL_SECTIONS.keys().map(|k| k.to_string()).collect();
        if !needs_admin {
            // At least strip the management section.
            let always: HashSet<String> = ALWAYS_AVAILABLE.iter().map(|s| s.to_string()).collect();
            let keep_extra: HashSet<String> = [
                "generate_image", "suggest_document",
                "chat_with_model", "ask_teacher", "list_models",
            ]
            .into_iter()
            .map(|s| s.to_string())
            .collect();
            // mgmt_tools = all_known - always - keep_extra
            let mgmt_tools: HashSet<String> = all_known
                .difference(&always)
                .cloned()
                .collect::<HashSet<String>>()
                .difference(&keep_extra)
                .cloned()
                .collect();
            let included: HashSet<String> = all_known.difference(&mgmt_tools).cloned().collect();
            _assemble_prompt(&included, Some(&disabled), compact)
        } else if compact {
            _assemble_prompt(&all_known, Some(&disabled), true)
        } else {
            // AGENT_SYSTEM_PROMPT == _assemble_prompt(all tools) with no disabled
            // applied; Python passes the precomputed legacy constant. We rebuild
            // it with the disabled set applied to match the effective behavior.
            _assemble_prompt(&all_known, Some(&disabled), false)
        }
    };

    // Inject the Level-0 skill index — one line per skill (Python L952-984).
    // owner=None; gating mirrors `index_for` (platform/toolsets). A soft
    // enhancement — any failure is non-fatal (here the calls don't throw).
    //
    // SECURITY: skill `name` and `description` are user-editable, so the index
    // block is returned SEPARATELY (not appended to agent_prompt). The caller
    // wraps it in untrusted_context_message and ships it as a user-role message
    // — same treatment as the matched-skills block.
    let mut skill_index_block = String::new();
    {
        let sm = crate::services::memory::skills::SkillsManager::new(
            &crate::src::constants::DATA_DIR,
        );
        // active_tools = list(set(TOOL_SECTIONS.keys()) - set(disabled or []))
        let active_tools: Vec<String> = TOOL_SECTIONS
            .keys()
            .filter(|k| !disabled.contains(**k))
            .map(|k| k.to_string())
            .collect();
        let skill_idx = sm.index_for(None, Some(&active_tools), None);
        if !skill_idx.is_empty() {
            let mut lines: Vec<String> = vec![
                "## Available skills".to_string(),
                "Procedures the assistant should consult before doing domain work. \
Fetch the full procedure with `manage_skills` action=view name=<name> \
when one looks relevant. Entries tagged `(draft)` were written by the \
teacher-escalation loop after a prior failure — treat them as authoritative \
guidance; if you follow one and it works, that's a good signal the procedure \
is correct."
                    .to_string(),
            ];
            // by_cat[category] -> [skills]; categories rendered sorted.
            let mut by_cat: std::collections::BTreeMap<String, Vec<&Map<String, Value>>> =
                std::collections::BTreeMap::new();
            for s in &skill_idx {
                let cat = s
                    .get("category")
                    .and_then(|v| v.as_str())
                    .unwrap_or("general")
                    .to_string();
                by_cat.entry(cat).or_default().push(s);
            }
            for (cat, items) in &by_cat {
                lines.push(format!("\n**{cat}**"));
                for s in items {
                    let name = s.get("name").and_then(|v| v.as_str()).unwrap_or("?");
                    let desc = s.get("description").and_then(|v| v.as_str()).unwrap_or("");
                    let badge = if s.get("status").and_then(|v| v.as_str()) == Some("draft") {
                        " *(draft)*"
                    } else {
                        ""
                    };
                    lines.push(format!("- `{name}` — {desc}{badge}"));
                }
            }
            // skill_index_block = "\n\n" + "\n".join(lines)
            skill_index_block = format!("\n\n{}", lines.join("\n"));
        }
    }

    // Inject integration descriptions (Python L986-990).
    let integ_prompt = crate::src::integrations::get_integrations_prompt();
    if !integ_prompt.is_empty() {
        prompt.push_str("\n\n");
        prompt.push_str(&integ_prompt);
    }

    // Inject MCP tool descriptions (Python L992-998).
    if let Some(mgr) = mcp_mgr {
        let mcp_desc = mgr.get_tool_descriptions_for_prompt(mcp_disabled_map);
        if !mcp_desc.is_empty() {
            prompt.push_str(&mcp_desc);
        }
    }

    (prompt, skill_index_block)
}

/// `_build_system_prompt(...)` — build agent system prompt, inject document
/// context, merge consecutive system msgs. Returns `(messages, mcp_schemas)`.
///
/// `mcp_schemas` is populated from the live `Arc<McpManager>` via
/// `get_all_openai_schemas(mcp_disabled_map)` when a manager is present (Python
/// L571-573); it is empty when no manager is wired (or MCP was suppressed for a
/// public/non-admin owner). The full Python enrichment chain is ported: the
/// document-context block (+ FORM MODE for form-backed PDFs + suggestion-mode
/// detection), the email writing-style + email-document-format injections, and
/// the prefs-gated relevant-skills injection (`owner` scopes the prefs + skills).
fn _build_system_prompt(
    messages: Vec<Value>,
    model: &str,
    active_document: Option<&ActiveDocument>,
    mcp_mgr: &Option<std::sync::Arc<crate::src::mcp_manager::McpManager>>,
    disabled_tools: Option<&HashSet<String>>,
    needs_admin: bool,
    relevant_tools: Option<&HashSet<String>>,
    mcp_disabled_map: Option<&std::collections::HashMap<String, HashSet<String>>>,
    compact: bool,
    owner: Option<&str>,
) -> (Vec<Value>, Vec<Value>) {
    // cache_key = (frozenset(disabled), bool(mcp_mgr), needs_admin, rt_key,
    //              compact, ov_sig). ov_sig is a deterministic signature of the
    //              builtin_tool_overrides so an edit in the Skills UI busts the
    //              cached base prompt without a restart (Python hashes it sha256;
    //              any stable signature works — we sort the keys + serialize).
    let rt_key = relevant_tools.map(|s| {
        let mut v: Vec<&String> = s.iter().collect();
        v.sort();
        v.iter().map(|x| x.as_str()).collect::<Vec<_>>().join(",")
    });
    let disabled_key = {
        let mut v: Vec<&String> = disabled_tools.map(|d| d.iter().collect()).unwrap_or_default();
        v.sort();
        v.iter().map(|x| x.as_str()).collect::<Vec<_>>().join(",")
    };
    let ov_sig = {
        // sort_keys=True equivalent: BTreeMap -> deterministic serialization.
        let ov = get_builtin_overrides();
        let sorted: std::collections::BTreeMap<String, Value> = ov.into_iter().collect();
        serde_json::to_string(&sorted).unwrap_or_default()
    };
    let cache_key = format!(
        "{disabled_key}|{}|{needs_admin}|{}|{compact}|{ov_sig}",
        mcp_mgr.is_some(),
        rt_key.unwrap_or_default()
    );

    let cached_prompt = {
        let cache = CACHED_BASE_PROMPT.lock().unwrap();
        match cache.as_ref() {
            Some((k, p)) if *k == cache_key && active_document.is_none() => Some(p.clone()),
            _ => None,
        }
    };
    // The skill INDEX block is user-editable (name + description), so it must
    // never live in the trusted system role and is NOT cached. Always recompute
    // it (on a cache hit, via a fresh _build_base_prompt that yields the index).
    let (agent_prompt, skill_index_block) = match cached_prompt {
        Some(p) => {
            let (_, idx) = _build_base_prompt(
                disabled_tools,
                mcp_mgr,
                needs_admin,
                relevant_tools,
                mcp_disabled_map,
                compact,
            );
            (p, idx)
        }
        None => {
            let (p, idx) = _build_base_prompt(
                disabled_tools,
                mcp_mgr,
                needs_admin,
                relevant_tools,
                mcp_disabled_map,
                compact,
            );
            if active_document.is_none() {
                *CACHED_BASE_PROMPT.lock().unwrap() = Some((cache_key, p.clone()));
            }
            (p, idx)
        }
    };

    // mcp_schemas — dynamic per request (Python L571-573). Empty when no manager
    // is wired or MCP was suppressed for a public/non-admin owner.
    let mcp_schemas: Vec<Value> = match mcp_mgr {
        Some(mgr) => mgr.get_all_openai_schemas(mcp_disabled_map),
        None => Vec::new(),
    };

    set_active_model(Some(model));

    // Current date/time — prepended every request (system TZ-local). Mutable from
    // here: the email-style / email-format / relevant-skills injections below are
    // dynamic per-request (NOT part of the cached base prompt).
    let mut agent_prompt = prepend_datetime_block(agent_prompt);

    // Document context as a SEPARATE protected message.
    let mut doc_message: Option<Value> = None;
    if let Some(doc) = active_document {
        set_active_document(Some(&doc.id));
        let doc_raw = doc.current_content.clone().unwrap_or_default();
        let doc_title_l = doc.title.clone().unwrap_or_default().trim().to_lowercase();
        let head400: String = doc_raw.chars().take(400).collect();
        let is_email_doc = doc.language.as_deref() == Some("email")
            || matches!(doc_title_l.as_str(), "new email" | "new mail" | "new message")
            || (head400.contains("To:") && head400.contains("Subject:") && doc_raw.contains("\n---\n"));

        let doc_ctx = if is_email_doc {
            build_email_doc_ctx(doc, &doc_raw)
        } else {
            // Form-backed PDF docs (recognized by the `pdf_form_source` front-matter
            // pointer) get a focused FORM MODE prompt; everything else gets the
            // regular generic-document context (Python L630-697).
            let is_form_backed = crate::src::pdf_form_doc::find_source_upload_id(
                doc.current_content.as_deref().unwrap_or(""),
            )
            .is_some();
            if is_form_backed {
                build_pdf_form_doc_ctx(doc)
            } else {
                build_generic_doc_ctx(doc, &doc_raw)
            }
        };
        let mut msg = untrusted_context_message("active editor document", Some(&doc_ctx));
        if let Some(obj) = msg.as_object_mut() {
            obj.insert("_protected".to_string(), Value::Bool(true));
        }

        // Auto-detect suggestion mode from the last user message.
        let mut last_user_msg = String::new();
        for m in messages.iter().rev() {
            if m.get("role").and_then(|r| r.as_str()) == Some("user") {
                last_user_msg =
                    message_content_text(m.get("content").unwrap_or(&Value::Null)).to_lowercase();
                break;
            }
        }
        let suggest_keywords = [
            "suggest", "review", "improve", "feedback", "critique", "proofread",
            "check my", "look over",
        ];
        if suggest_keywords.iter().any(|kw| last_user_msg.contains(kw)) {
            if let Some(obj) = msg.as_object_mut() {
                if let Some(Value::String(c)) = obj.get_mut("content") {
                    c.push_str(
                        "\n\nTrusted instruction for this turn: the user appears to want \
suggestions for the active editor document. Use suggest_document \
with <<<FIND>>>...<<<SUGGEST>>>...<<<REASON>>>...<<<END>>> blocks.",
                    );
                }
            }
        }
        doc_message = Some(msg);
    } else {
        set_active_document(None);
    }

    // The last user message text (lowercased), reused by the email-style gate and
    // the relevant-skills retrieval below.
    let last_user_text = {
        let mut t = String::new();
        for m in messages.iter().rev() {
            if m.get("role").and_then(|r| r.as_str()) == Some("user") {
                t = message_content_text(m.get("content").unwrap_or(&Value::Null)).to_lowercase();
                break;
            }
        }
        t
    };

    // ── Email writing style + identity (Python L720-765) ──
    // Broader than read/list: models may compose via send/reply/ui_control after
    // the first round, so any email-ish tool in `relevant_tools` (plus an
    // email-ish word in the user's message) arms the injection.
    const EMAIL_TOOL_HINTS: &[&str] = &[
        "list_email_accounts", "send_email", "reply_to_email", "list_emails", "read_email",
        "bulk_email", "archive_email", "delete_email", "mark_email_read",
        "resolve_contact", "ui_control",
        "mcp__email__list_email_accounts",
        "mcp__email__send_email", "mcp__email__reply_to_email",
        "mcp__email__list_emails", "mcp__email__read_email",
        "mcp__email__bulk_email", "mcp__email__archive_email",
        "mcp__email__delete_email", "mcp__email__mark_email_read",
    ];
    let relevant_has_email_tool = relevant_tools
        .map(|rt| rt.iter().any(|t| EMAIL_TOOL_HINTS.contains(&t.as_str())))
        .unwrap_or(false);
    let inject_style = if active_document.map(|d| d.language.as_deref() == Some("email")).unwrap_or(false)
    {
        true
    } else if relevant_has_email_tool {
        ["email", "mail", "reply", "send", "inbox"]
            .iter()
            .any(|tok| last_user_text.contains(tok))
    } else {
        false
    };
    if inject_style {
        let style = crate::src::settings::load_settings()
            .get("email_writing_style")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .trim()
            .to_string();
        if !style.is_empty() {
            agent_prompt.push_str(&format!(
                "\n\n📧 EMAIL WRITING STYLE AND IDENTITY — FOLLOW FOR ANY EMAIL DRAFT OR SEND:\n\
{style}\n\n\
Hard identity rule: write as the user/mailbox owner only. Do not sign as, speak as, \
or imply you are the recipient, original sender, quoted sender, spouse, assistant, \
company, or any other third party. If a signature is needed, use only the name/signature \
from the saved writing style. Never copy a name from the quoted thread into the sign-off.\n\
Mechanical style rules: never use em dash/en dash; use --. Never use curly apostrophes. \
For English emails, default to Hi [Name] or Hiya from the saved style rather than Hey. \
If the saved style specifies Best/newline/name, use that sign-off when a sign-off is natural."
            ));
        }
    }

    // ── Email document format hint (Python L767-780) ──
    if relevant_has_email_tool {
        agent_prompt.push_str(
            "\n\n📧 EMAIL DOCUMENT FORMAT: When drafting email replies, use create_document with language=\"email\". \
The content format is:\n\
To: recipient@example.com\n\
Subject: Re: Original subject\n\
In-Reply-To: <original-message-id>\n\
References: <original-message-id>\n\
---\n\
Body text here...\n\n\
The user can then edit and click Send or Draft in the editor. For an already-open email draft, \
edit the current document instead of creating another one.",
        );
    }

    // ── Relevant-skills injection (Python L782-866), prefs-gated ──
    // Uses the RAW last-user message (`_extract_last_user_message`), NOT the
    // lowercased gate text — `get_relevant_skills` does its own tokenization.
    //
    // SECURITY: skill content (name/description/when_to_use/procedure/pitfalls)
    // is user-editable via `manage_skills`. It must NOT be concatenated into the
    // trusted system role — a malicious description could be treated as a system
    // instruction by the LLM. Build a SEPARATE user-role message wrapped in
    // untrusted_context_message (metadata.trusted=False), combining the matched
    // skills with the equally-user-editable skill INDEX block, and insert it next
    // to the user's request — same treatment as `doc_message`.
    let skills_message = build_skills_message(&messages, owner, &skill_index_block);

    // Insert the agent system message after any leading system messages.
    let mut insert_idx = 0usize;
    for (i, m) in messages.iter().enumerate() {
        if m.get("role").and_then(|r| r.as_str()) == Some("system") {
            insert_idx = i + 1;
        } else {
            break;
        }
    }
    let agent_msg = json!({"role": "system", "content": agent_prompt});
    let mut combined: Vec<Value> = Vec::with_capacity(messages.len() + 2);
    combined.extend_from_slice(&messages[..insert_idx]);
    combined.push(agent_msg);
    combined.extend_from_slice(&messages[insert_idx..]);

    // Merge consecutive system messages — but skip _protected doc messages.
    let mut merged: Vec<Value> = Vec::new();
    for msg in combined.into_iter() {
        let is_sys = msg.get("role").and_then(|r| r.as_str()) == Some("system");
        let is_protected = msg.get("_protected").and_then(|p| p.as_bool()).unwrap_or(false);
        let prev_mergeable = merged
            .last()
            .map(|m| {
                m.get("role").and_then(|r| r.as_str()) == Some("system")
                    && !m.get("_protected").and_then(|p| p.as_bool()).unwrap_or(false)
            })
            .unwrap_or(false);
        if is_sys && !is_protected && prev_mergeable {
            let prev_content = merged
                .last()
                .and_then(|m| m.get("content"))
                .and_then(|c| c.as_str())
                .unwrap_or("")
                .to_string();
            let this_content = msg.get("content").and_then(|c| c.as_str()).unwrap_or("");
            let last = merged.last_mut().unwrap();
            *last = json!({
                "role": "system",
                "content": format!("{prev_content}\n\n{this_content}"),
            });
        } else {
            merged.push(msg);
        }
    }

    // Insert the document message + skills message right before the last user
    // message so they're close to the user's request and survive context
    // trimming independently. Same treatment for the matched-skills block —
    // user-editable skill content must never be in the system role.
    let mut last_user_idx = merged.len().saturating_sub(1);
    for i in (0..merged.len()).rev() {
        if merged[i].get("role").and_then(|r| r.as_str()) == Some("user") {
            last_user_idx = i;
            break;
        }
    }
    if let Some(dm) = doc_message {
        merged.insert(last_user_idx, dm);
        last_user_idx += 1; // the document message is now at last_user_idx
    }
    if let Some(sm) = skills_message {
        merged.insert(last_user_idx, sm);
    }

    (merged, mcp_schemas)
}

/// Prepend the `## Current date and time` block to the agent prompt. Uses the
/// system-local timezone (`datetime.now().astimezone()`).
fn prepend_datetime_block(agent_prompt: String) -> String {
    use chrono::{Datelike, Local, Timelike, Utc};
    let now = Local::now();
    let utc = Utc::now();

    // %A, %B %-d, %Y  (no leading-zero day)
    let weekday = now.format("%A").to_string();
    let month = now.format("%B").to_string();
    let day = now.day();
    let year = now.year();
    let ymd = now.format("%Y-%m-%d").to_string();
    // %-I:%M %p  (12-hour, no leading zero)
    let hour12 = {
        let h = now.hour() % 12;
        if h == 0 { 12 } else { h }
    };
    let ampm = if now.hour() < 12 { "AM" } else { "PM" };
    let local_min = now.format("%M").to_string();
    let tz_abbr = now.format("%Z").to_string();
    // %z -> +0900 ; format as +09:00
    let off = now.format("%z").to_string();
    let off_fmt = if off.len() >= 5 {
        format!("{}:{}", &off[..3], &off[3..5])
    } else {
        "+00:00".to_string()
    };
    let local_hm = now.format("%H:%M").to_string();
    let utc_hm = utc.format("%H:%M").to_string();

    let block = format!(
        "## Current date and time\n\
Today is {weekday}, {month} {day}, {year} ({ymd}). \
Local time is {hour12}:{local_min} {ampm} ({tz_abbr}, UTC{off_fmt}); \
current UTC time is {utc_hm}. \
Use this for any 'today'/'tomorrow'/'this week' reasoning — do NOT \
infer the date from training data or from event timestamps.\n\
When scheduling a task (manage_tasks), scheduled_time is in UTC: \
subtract the offset above from the user's local time \
(local {local_hm} = {utc_hm} UTC right now).\n\n"
    );
    block + &agent_prompt
}

/// The ACTIVE EMAIL DRAFT document-context block.
fn build_email_doc_ctx(doc: &ActiveDocument, doc_raw: &str) -> String {
    let title = doc.title.clone().unwrap_or_default();
    format!(
        "ACTIVE EMAIL DRAFT (open in editor — the user is looking at this right now)\n\
Title: \"{title}\"\n\
```\n{doc_raw}\n```\n\n\
When the user asks you to write, reply to, or improve this email:\n\
1. Use `update_document` to replace the ENTIRE content — keep all the header lines (To, Subject, In-Reply-To, References, X-Source-UID, X-Source-Folder, X-Attachments) and the `---` separator EXACTLY as they are.\n\
2. Replace ONLY the body text (the part after `---`). If there is a quoted original email (lines starting with `>`), keep that quoted block unchanged BELOW your new reply.\n\
3. Write the reply body above the quoted original. Use the saved email writing style when present.\n\
4. Identity is critical: write as the logged-in user / mailbox owner only. NEVER sign as the recipient, original sender, quoted sender, spouse, assistant, company, or any third party. If adding a signature, use only the name/signature implied by the saved email writing style.\n\
5. Mechanical style is critical: never use em dash/en dash; use --. Never use curly apostrophes. For English emails, use Hi/Hiya from the saved style rather than Hey unless the user explicitly asks for Hey.\n\
6. Do NOT use create_document — the email is already open, you must update it.\n\n\
Do NOT ask the user to paste or share the email — you already have it above."
    )
}

/// The generic ACTIVE DOCUMENT context block (line-numbered).
fn build_generic_doc_ctx(doc: &ActiveDocument, doc_raw: &str) -> String {
    let title = doc.title.clone().unwrap_or_default();
    let language = match doc.language.clone() {
        Some(l) if !l.is_empty() => l,
        _ => "text".to_string(),
    };
    // "\n".join(f"{i}\t{ln}" for i, ln in enumerate(raw.split("\n"), 1))
    let doc_numbered = doc_raw
        .split('\n')
        .enumerate()
        .map(|(i, ln)| format!("{}\t{ln}", i + 1))
        .collect::<Vec<_>>()
        .join("\n");
    format!(
        "ACTIVE DOCUMENT (open in the editor — the user is looking at it right now)\n\
Title: \"{title}\" | Language: {language}\n\
Below is the full text. Each line is prefixed with its line number and a TAB, \
purely so you can locate references like \"[Doc edit: L25]\" — the number and tab \
are NOT part of the document.\n\
```\n{doc_numbered}\n```\n\
You ALREADY HAVE this document — it is right above. Do NOT ask the user to paste \
it, and do NOT use read_file, bash, cat, or any tool to fetch it: it lives in the \
editor, NOT on disk, so those attempts will fail. Every request is about THIS \
document unless the user clearly says otherwise.\n\
A \"[Doc edit: L25]\" prefix means the user is pointing at that line — use the \
numbers above to find the text they mean.\n\
To edit: use edit_document with <<<FIND>>>...<<<REPLACE>>>...<<<END>>>. The FIND \
text must match the document EXACTLY and must NOT include the leading line-number \
or tab (those are reference-only). To rewrite entirely: update_document."
    )
}

/// The focused FORM MODE context for a form-backed PDF document (Python L641-674).
/// The whole form is in the markdown; the model edits bullet lines via
/// `edit_document`, anchored on the trailing `<!-- field=NAME type=TYPE -->`.
fn build_pdf_form_doc_ctx(doc: &ActiveDocument) -> String {
    let title = doc.title.clone().unwrap_or_default();
    let content = doc.current_content.clone().unwrap_or_default();
    format!(
        "ACTIVE PDF FORM (open in editor — the user is looking at this right now)\n\
Title: \"{title}\"\n\
```\n{content}\n```\n\n\
The ENTIRE form is in the markdown above. Every field, on every page, is a bullet line you can see now.\n\n\
DO NOT try to \"read the file\", \"open the PDF\", or call filesystem / read_file / mcp__filesystem__read_file / any file-reading tool. The form IS the document above. Just edit it.\n\n\
DO NOT ask the user to upload, share, or re-attach. The form is already loaded.\n\n\
TO EDIT: call `edit_document` with FIND/REPLACE matching whole bullet lines. The trailing HTML comment `<!-- field=NAME type=TYPE -->` is the ground truth anchor — match it to pick the correct bullet.\n\n\
RULES:\n\
1. FIND the WHOLE bullet line including the trailing comment. REPLACE keeps the bullet structure and the comment exactly; only the value text after the label changes.\n\
2. Text bullets — `- **label:** value <!--field=NAME-->` — replace `value`.\n\
3. Choice bullets — `- **label** [opt1 / opt2 / opt3]: value <!--field=NAME-->` — replace `value` with one of the listed options verbatim.\n\
4. Checkbox bullets — `- [ ] **label** <!--field=NAME-->` — toggle `[ ]` ↔ `[x]`.\n\
5. NEVER invent values. If the user gives no value, ASK. Never write fake names, addresses, emails, or \"NaN\"/\"N/A\"/\"TBD\".\n\
6. NEVER edit the front-matter `<!-- pdf_form_source ... -->` or the `## Page N` section headers.\n\
7. NEVER touch signature fields (type=signature) — the user signs those by clicking on the rendered PDF.\n\
8. Bulk requests are scoped by field type. \"All included\" means every choice field with that option. Do NOT touch text fields.\n\
9. The user has an Export button — do NOT try to export."
    )
}

/// Build the matched-"## Relevant skills for this request" block + the skill
/// INDEX block as a SEPARATE untrusted user-role message (Python L782-866).
///
/// SECURITY: skill content (name/description/when_to_use/procedure/pitfalls) and
/// the index block's name+description are user-editable via `manage_skills`, so
/// they must NEVER be concatenated into the trusted system role. Both are wrapped
/// in `untrusted_context_message("skills", ...)` (metadata.trusted=False) and the
/// caller inserts the result next to the user's request, like `doc_message`.
///
/// Prefs-gated: respects the per-user `skills_enabled` toggle, the
/// `auto_approve_skills` + `skill_min_confidence` confidence gate, and the
/// `skill_max_injected` cap (clamped 0..=12). Bumps each surfaced skill's `uses`
/// counter via `record_use` (owner-scoped). Returns `None` when there are neither
/// matched skills nor an index block (or skills are disabled). A soft enhancement
/// — never fatal.
fn build_skills_message(
    messages: &[Value],
    owner: Option<&str>,
    skill_index_block: &str,
) -> Option<Value> {
    let last_user = _extract_last_user_message(messages);
    if last_user.is_empty() {
        return None;
    }
    // Respect the user's skills-enabled toggle (mirrors memory_enabled).
    let prefs = crate::routes::prefs_store::load_for_user(owner);
    let skills_on = prefs
        .get("skills_enabled")
        .and_then(|v| v.as_bool())
        .unwrap_or(true);
    if !skills_on {
        return None;
    }

    let sm = crate::services::memory::skills::SkillsManager::new(&crate::src::constants::DATA_DIR);

    // Approve OFF -> published-only (min_conf 2.0 clears no draft). Approve ON ->
    // prefs.skill_min_confidence, else get_setting("skill_autosave_min_confidence", 0.85).
    let auto_approve = prefs
        .get("auto_approve_skills")
        .and_then(|v| v.as_bool())
        .unwrap_or(true);
    let skill_min_conf = if !auto_approve {
        2.0
    } else {
        prefs
            .get("skill_min_confidence")
            .and_then(|v| v.as_f64())
            .unwrap_or_else(|| {
                crate::src::settings::get_setting(
                    "skill_autosave_min_confidence",
                    serde_json::json!(0.85),
                )
                .as_f64()
                .unwrap_or(0.85)
            })
    };
    // skill_max_injected = prefs OR get_setting("skill_max_injected", 3), clamp 0..=12.
    let skill_max_injected = prefs
        .get("skill_max_injected")
        .and_then(|v| v.as_i64())
        .unwrap_or_else(|| {
            crate::src::settings::get_setting("skill_max_injected", serde_json::json!(3))
                .as_i64()
                .unwrap_or(3)
        });
    let skill_max_injected = skill_max_injected.clamp(0, 12) as usize;

    let relevant_skills = if skill_max_injected > 0 {
        sm.get_relevant_skills(
            &last_user,
            Some(sm.load(owner)),
            0.25,
            skill_max_injected,
            skill_min_conf,
        )
    } else {
        Vec::new()
    };

    // Python: `lines = [""]` is built up front; the matched-skills heading +
    // bodies are appended only when relevant_skills is non-empty.
    let mut lines: Vec<String> = vec![String::new()];
    if !relevant_skills.is_empty() {
        // Bump the "uses" counter on every skill we actually surface (owner-scoped).
        for sk in &relevant_skills {
            if let Some(name) = sk.get("name").and_then(|v| v.as_str()) {
                sm.record_use(name, owner);
            }
        }
        lines.push("## Relevant skills for this request".to_string());
        lines.push(
            "These skills are matched to your current request. Each is a \
procedure proven to work. Follow them step by step. To see the full SKILL.md \
(more detail, pitfalls, verification steps), call `manage_skills` with \
action='view' and the skill name."
                .to_string(),
        );
        for sk in &relevant_skills {
            let mut src_tag = String::new();
            if sk.get("source").and_then(|v| v.as_str()) == Some("teacher-escalation") {
                let tm = sk
                    .get("teacher_model")
                    .and_then(|v| v.as_str())
                    .filter(|s| !s.is_empty())
                    .unwrap_or("teacher");
                src_tag = format!(" _(learned from {tm})_");
            }
            let name = sk.get("name").and_then(|v| v.as_str()).unwrap_or("?");
            lines.push(format!("\n### {name}{src_tag}"));
            if let Some(desc) = sk.get("description").and_then(|v| v.as_str()) {
                if !desc.is_empty() {
                    lines.push(desc.to_string());
                }
            }
            if let Some(wtu) = sk.get("when_to_use").and_then(|v| v.as_str()) {
                if !wtu.is_empty() {
                    lines.push(format!("_When to use:_ {wtu}"));
                }
            }
            if let Some(proc) = sk.get("procedure").and_then(|v| v.as_array()) {
                if !proc.is_empty() {
                    lines.push("Procedure:".to_string());
                    for (i, step) in proc.iter().enumerate() {
                        let s = step.as_str().unwrap_or("");
                        lines.push(format!("  {}. {s}", i + 1));
                    }
                }
            }
            if let Some(pf) = sk.get("pitfalls").and_then(|v| v.as_array()) {
                if !pf.is_empty() {
                    let joined = pf
                        .iter()
                        .filter_map(|p| p.as_str())
                        .collect::<Vec<_>>()
                        .join("; ");
                    lines.push(format!("Pitfalls: {joined}"));
                }
            }
        }
    }

    // Build the message only when there's matched-skills content OR an index
    // block (Python `if relevant_skills or _skill_index_block:`).
    if relevant_skills.is_empty() && skill_index_block.is_empty() {
        return None;
    }
    let mut skills_text = lines.join("\n");
    if !skill_index_block.is_empty() {
        skills_text = format!("{skill_index_block}\n\n{skills_text}");
    }
    Some(untrusted_context_message("skills", Some(&skills_text)))
}

// ---------------------------------------------------------------------------
// _run_verifier_subagent
// ---------------------------------------------------------------------------

/// `_THINK_RE` — `<think>...</think>` with DOTALL + IGNORECASE.
static THINK_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"(?is)<think>.*?</think>").unwrap());

/// `_run_verifier_subagent(instruction, actions_snapshot, *, endpoint_url, model,
/// headers)` — fresh-context completion verifier. Returns a list of failure
/// reasons (empty = pass, or silently empty on any error).
async fn _run_verifier_subagent(
    instruction: &str,
    actions_snapshot: &str,
    endpoint_url: &str,
    model: &str,
    headers: &indexmap::IndexMap<String, String>,
) -> Vec<String> {
    let instr: String = instruction.chars().take(4000).collect();
    let snap: String = actions_snapshot.chars().take(8000).collect();
    let prompt = format!(
        "You are an independent verifier. Another assistant just claimed the \
following task is complete. Using ONLY the request and the record of \
what it actually did, decide whether that claim is correct. Be strict: \
only say SUCCESS if the work genuinely satisfies the request.\n\n\
<user_request>\n{instr}\n</user_request>\n\n\
<actions_taken>\n{snap}\n</actions_taken>\n\n\
<checklist>\n\
1. Every concrete deliverable the request asked for was actually produced\n\
2. Outputs/edits match what was asked — nothing missing, no extra or unrequested changes\n\
3. Tool results show success, not errors or empty output that got ignored\n\
4. Anything the request said to leave alone was left unchanged\n\
</checklist>\n\n\
Reason briefly (2-3 sentences max). Then output EXACTLY one of:\n\
  VERIFICATION: SUCCESS\n\
  VERIFICATION: FAIL: <one short sentence per issue, semicolon-separated>\n\
Output nothing after the VERIFICATION line."
    );

    let raw = match crate::src::llm_core::llm_call_async(
        endpoint_url,
        model,
        vec![json!({"role": "user", "content": prompt})],
        0.0,
        600,
        headers.clone(),
        60,
    )
    .await
    {
        Ok(r) => r,
        Err(e) => {
            logger::warning(&format!("[agent] verifier subagent failed: {e}"));
            return Vec::new();
        }
    };
    let raw = THINK_RE.replace_all(&raw, "").to_string();
    let mut last_v: Option<String> = None;
    for line in raw.lines() {
        if line.contains("VERIFICATION:") {
            last_v = Some(line.trim().to_string());
        }
    }
    let last_v = match last_v {
        Some(v) if v.contains("VERIFICATION: FAIL:") => v,
        _ => return Vec::new(),
    };
    let reasons = last_v
        .split_once("VERIFICATION: FAIL:")
        .map(|x| x.1)
        .unwrap_or("")
        .trim()
        .to_string();
    reasons
        .split(';')
        .map(|r| r.trim().to_string())
        .filter(|r| !r.is_empty())
        .collect()
}

// ---------------------------------------------------------------------------
// _empty_response_fallback — defined as `pub fn` in agent_loop.rs. This file is
// `include!`d into that same module, so the function is already in scope here;
// the duplicate local copy added during the parity port was removed in
// convergence to resolve the E0428 name clash.
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// stream_agent_loop
// ---------------------------------------------------------------------------

/// `stream_agent_loop(args)` — the streaming multi-round agent loop.
///
/// A PLAIN fn returning `impl Stream<Item = String>` (each item one framed SSE
/// string). See the module docstring for the full event vocabulary.
pub fn stream_agent_loop(args: AgentLoopArgs) -> impl futures_util::Stream<Item = String> + Send {
    use crate::src::agent_tools::get_mcp_manager;
    use crate::src::context_compactor::trim_for_context;
    use crate::src::model_context::estimate_tokens;
    use crate::src::tool_execution::{execute_tool_block, format_tool_result, ProgressCb};
    use crate::src::tool_parsing::strip_tool_blocks;
    use crate::src::tool_schemas::FUNCTION_TOOL_SCHEMAS;
    use crate::src::tool_security::blocked_tools_for_owner;
    use futures_util::StreamExt;
    use std::time::Instant;

    async_stream::stream! {
        let AgentLoopArgs {
            endpoint_url,
            model,
            mut messages,
            headers,
            temperature,
            max_tokens,
            prompt_type,
            max_rounds,
            max_tool_calls,
            context_length,
            active_document,
            session_id,
            disabled_tools,
            owner,
            relevant_tools,
            fallbacks,
            is_teacher_run,
        } = args;

        let session_id_ref = session_id.as_deref();
        let owner_ref = owner.as_deref();

        let mut mcp_mgr = get_mcp_manager();
        let mut prep_timings: indexmap::IndexMap<String, f64> = indexmap::IndexMap::new();
        let mut disabled_tools: HashSet<String> = disabled_tools.unwrap_or_default();
        let public_blocked = blocked_tools_for_owner(owner_ref);
        if !public_blocked.is_empty() {
            disabled_tools.extend(public_blocked);
            // Hide all MCP schemas for public/non-admin users.
            mcp_mgr = None;
        }

        let t0 = Instant::now();
        let needs_admin = _detect_admin_intent(&messages);
        let last_user = _extract_last_user_message(&messages);
        let retrieval_query = {
            let q = _recent_context_for_retrieval(&messages, 3, 600);
            if q.is_empty() { last_user.clone() } else { q }
        };
        let mcp_disabled_map = if mcp_mgr.is_some() {
            _load_mcp_disabled_map()
        } else {
            std::collections::HashMap::new()
        };
        prep_timings.insert("request_setup".to_string(), t0.elapsed().as_secs_f64());

        // ── Tool selection ──
        // The RAG index (src.tool_index.get_tool_index) is unported -> always
        // None, so we go straight to the keyword fallback (Python L1308).
        let t1 = Instant::now();
        let mut rt: Option<HashSet<String>> = relevant_tools;
        if let Some(ref rtt) = rt {
            logger::info(&format!(
                "[tool-rag] Using caller-provided relevant_tools ({} tools)",
                rtt.len()
            ));
        }
        // Keyword-based tool selection.
        if rt.is_none() && !retrieval_query.is_empty() {
            let mut selected: HashSet<String> =
                ALWAYS_AVAILABLE.iter().map(|s| s.to_string()).collect();
            let ql = retrieval_query.to_lowercase();
            for (keywords, tools) in _KEYWORD_HINTS.iter() {
                if keywords.iter().any(|kw| ql.contains(kw)) {
                    selected.extend(tools.iter().map(|s| s.to_string()));
                }
            }
            // Always include core document/memory tools.
            selected.insert("create_document".to_string());
            selected.insert("manage_memory".to_string());
            selected.insert("manage_notes".to_string());
            let extra: Vec<String> = {
                let always: HashSet<String> =
                    ALWAYS_AVAILABLE.iter().map(|s| s.to_string()).collect();
                let mut v: Vec<String> = selected.difference(&always).cloned().collect();
                v.sort();
                v
            };
            logger::info(&format!(
                "[tool-rag] Keyword fallback selected: {:?}",
                extra
            ));
            rt = Some(selected);
        }
        // If a document is open the model needs the editing tools available.
        if let Some(ref mut rtt) = rt {
            if active_document.is_some() {
                rtt.insert("edit_document".to_string());
                rtt.insert("update_document".to_string());
                rtt.insert("suggest_document".to_string());
            }
        }
        prep_timings.insert("tool_selection".to_string(), t1.elapsed().as_secs_f64());
        let _ = _TOOL_SELECTION_TIMEOUT_SECONDS;

        // ── API-model detection ──
        let t2 = Instant::now();
        let model_lc = model.to_lowercase();
        // Per-endpoint supports_tools override (raw SQL on model_endpoints).
        let endpoint_supports = lookup_endpoint_supports_tools(&endpoint_url);
        let model_supports_tools = [
            "gpt-4", "gpt-5", "gpt-o", "claude", "gemini", "gemma",
            "qwen3", "qwen2.5", "mixtral", "mistral", "llama-3.1", "llama-3.2",
            "llama-3.3", "llama-4",
            // Local-served models that follow OpenAI-style function calling
            // via vLLM's `--enable-auto-tool-choice`. Belt-and-suspenders
            // with the per-endpoint flag above.
            "minimax", "kimi", "yi-", "phi-3", "phi-4", "command-r",
            "glm-4", "internlm", "hermes",
            // deepseek-v2/v3/chat support tools via the cloud API; deepseek-r1
            // (reasoning model) does not — handled by the blocklist below.
            "deepseek-v", "deepseek-chat",
        ]
        .iter()
        .any(|kw| model_lc.contains(kw));
        // Models known to reject tool schemas at the Ollama/local level even when
        // the endpoint URL would otherwise enable native function calling. The
        // per-endpoint supports_tools flag (True/False) always takes priority and
        // can override this list for users who know their setup.
        let model_no_tools = ["deepseek-r1"].iter().any(|kw| model_lc.contains(kw));
        // Native Ollama endpoints (/api/chat) handle tool schemas differently from
        // the OpenAI-compat path. Models like gemma4, qwen3.5, ministral respond to
        // tool schemas by emitting a single native tool_call token then stopping,
        // rather than writing a fenced block — the agent loop sees 1 token and no
        // recognised tool, so the round terminates immediately (issue #1567).
        // Unless the endpoint is explicitly marked supports_tools=True by the user
        // (via the endpoint settings toggle), treat Ollama-native as text-only so
        // the fenced-block path is used instead of native function calling.
        let is_ollama_native = crate::src::llm_core::_is_ollama_native_url(&endpoint_url);
        let is_api_model = if endpoint_supports == Some(true) {
            true
        } else if endpoint_supports == Some(false) || model_no_tools || is_ollama_native {
            false
        } else {
            _API_HOSTS.iter().any(|h| endpoint_url.contains(h)) || model_supports_tools
        };

        let (built_messages, mcp_schemas) = _build_system_prompt(
            messages,
            &model,
            active_document.as_ref(),
            &mcp_mgr,
            Some(&disabled_tools),
            needs_admin,
            rt.as_ref(),
            Some(&mcp_disabled_map),
            is_api_model,
            owner_ref,
        );
        messages = built_messages;
        prep_timings.insert("prompt_build".to_string(), t2.elapsed().as_secs_f64());

        // ── Soft context trim ──
        let t3 = Instant::now();
        let soft_budget = setting_agent_input_token_budget();
        if soft_budget > 0 {
            let before = estimate_tokens(&messages);
            // Python: `reserve = max(min(max_tokens or 1024, 2048), 512)`. The
            // `or 1024` floor applies ONLY when max_tokens is falsy (0), NOT as a
            // blanket minimum — so 1..=1023 keep their value (clamped to >=512).
            let base = if max_tokens == 0 { 1024 } else { max_tokens };
            let reserve = base.clamp(512, 2048);
            // Honour the configurable ceiling for the auto-derived budget path.
            // No-op when the user has an explicit `agent_input_token_budget`
            // (that branch ignores hard_max). Falls back to DEFAULT_HARD_MAX on
            // missing/malformed/<=0 values so misconfig can't zero the budget.
            let hard_max = {
                let v = crate::src::settings::get_setting(
                    "agent_input_token_hard_max",
                    json!(crate::src::context_budget::DEFAULT_HARD_MAX),
                );
                let hm = if json_truthy(&v) {
                    json_int_trunc(&v)
                } else {
                    crate::src::context_budget::DEFAULT_HARD_MAX
                };
                if hm <= 0 {
                    crate::src::context_budget::DEFAULT_HARD_MAX
                } else {
                    hm
                }
            };
            // Scale the default budget to the model's context window so long-
            // context models aren't silently capped at 6000; an explicit user
            // setting is still honoured (clamped to the window). (#1170)
            let effective_budget = crate::src::context_budget::compute_input_token_budget_with(
                soft_budget,
                context_length,
                crate::src::settings::is_setting_overridden("agent_input_token_budget"),
                crate::src::context_budget::DEFAULT_BUDGET,
                crate::src::context_budget::DEFAULT_HEADROOM,
                hard_max,
            );
            let trimmed = trim_for_context(&messages, effective_budget, reserve);
            let after = estimate_tokens(&trimmed);
            if after < before {
                logger::info(&format!(
                    "[agent] soft-trimmed context: {before} -> {after} tokens (budget={effective_budget}, reserve={reserve})"
                ));
                messages = trimmed;
            }
        }
        prep_timings.insert("context_trim".to_string(), t3.elapsed().as_secs_f64());

        // Strip internal metadata keys (_protected) before sending to the API.
        for m in messages.iter_mut() {
            if let Some(obj) = m.as_object_mut() {
                obj.remove("_protected");
            }
        }

        // emit agent_prep breadcrumb
        {
            let mut rounded = Map::new();
            for (k, v) in prep_timings.iter() {
                rounded.insert(k.clone(), json!(round_to(*v, 3)));
            }
            yield format!("data: {}\n\n", json!({"type": "agent_prep", "data": rounded}));
        }

        let mut full_response = String::new();
        let total_start = Instant::now();
        let mut time_to_first_token: Option<f64> = None;
        let mut first_token_received = false;
        let mut tool_events: Vec<Value> = Vec::new();
        let mut round_texts: Vec<String> = Vec::new();
        let mut effectful_used = false;
        let mut verifier_rounds: i64 = 0;
        let verifier_instruction = _extract_last_user_message(&messages);
        let mut real_input_tokens: i64 = 0;
        let mut real_output_tokens: i64 = 0;
        let mut last_round_input_tokens: i64 = 0;
        let mut has_real_usage = false;
        // Backend-reported speeds (llama.cpp timings): true decode + prefill.
        let mut backend_gen_tps: f64 = 0.0;
        let mut backend_prefill_tps: f64 = 0.0;
        let mut total_tool_calls: i64 = 0;

        // Loop-breaker state.
        let mut recent_call_sigs: VecDeque<String> = VecDeque::with_capacity(6);
        let mut stuck_rounds = 0i64;
        let mut tool_type_counts: std::collections::HashMap<String, i64> =
            std::collections::HashMap::new();
        let mut force_answer = false;
        // The most recent round's reasoning_content, kept outside the loop so the
        // end-of-loop empty-response guard can fall back to it (Python references
        // `round_reasoning` after the loop, which is the last round's value).
        let mut last_round_reasoning = String::new();

        let mut round_num: i64 = 1;
        while round_num <= max_rounds {
            let mut round_response = String::new();
            let mut round_reasoning = String::new();
            let mut native_tool_calls: Vec<Value> = Vec::new();
            // Doc streaming state (per round).
            let mut doc_acc = String::new();
            let mut doc_opened = false;
            let mut doc_last_len: i64 = 0;
            let mut doc_fence_offset: usize = 0;
            let mut doc_scan_from: usize = 0;

            // Build the tool schemas for this round.
            let all_tool_schemas: Vec<Value> = if force_answer {
                Vec::new()
            } else if is_api_model {
                let mut base: Vec<Value> = if let Some(ref rtt) = rt {
                    FUNCTION_TOOL_SCHEMAS
                        .iter()
                        .filter(|s| {
                            s.get("function")
                                .and_then(|f| f.get("name"))
                                .and_then(|n| n.as_str())
                                .map(|n| rtt.contains(n))
                                .unwrap_or(false)
                        })
                        .cloned()
                        .collect()
                } else if needs_admin {
                    FUNCTION_TOOL_SCHEMAS.clone()
                } else {
                    FUNCTION_TOOL_SCHEMAS
                        .iter()
                        .filter(|s| {
                            s.get("function")
                                .and_then(|f| f.get("name"))
                                .and_then(|n| n.as_str())
                                .map(|n| !_ADMIN_SCHEMA_NAMES.contains(n))
                                .unwrap_or(true)
                        })
                        .cloned()
                        .collect()
                };
                // Append the live MCP schemas (empty when no manager is wired).
                base.extend(mcp_schemas.iter().cloned());
                if !disabled_tools.is_empty() {
                    base.retain(|t| {
                        let fname = t
                            .get("function")
                            .and_then(|f| f.get("name"))
                            .and_then(|n| n.as_str());
                        let tname = t.get("name").and_then(|n| n.as_str());
                        let fblocked = fname.map(|n| disabled_tools.contains(n)).unwrap_or(false);
                        let tblocked = tname.map(|n| disabled_tools.contains(n)).unwrap_or(false);
                        !fblocked && !tblocked
                    });
                }
                base
            } else {
                // Local: only MCP schemas when the message suggests MCP usage
                // (empty when no manager is wired or no tools are connected).
                let last_content = last_user.to_lowercase();
                let wants_mcp = _MCP_KEYWORDS.iter().any(|kw| last_content.contains(kw));
                if wants_mcp && !mcp_schemas.is_empty() {
                    mcp_schemas.clone()
                } else {
                    Vec::new()
                }
            };
            let agent_stream_timeout = setting_agent_stream_timeout_seconds().max(0) as u64;
            let agent_stream_timeout = if agent_stream_timeout == 0 { 300 } else { agent_stream_timeout };

            let tool_names_sent: Vec<&str> = all_tool_schemas
                .iter()
                .filter_map(|t| t.get("function").and_then(|f| f.get("name")).and_then(|n| n.as_str()))
                .collect();
            logger::info(&format!(
                "[agent-debug] round={round_num} model={model} is_api_model={is_api_model} tools_sent={} tool_names={:?}",
                tool_names_sent.len(),
                &tool_names_sent[..tool_names_sent.len().min(15)]
            ));

            // Candidate chain: primary + fallbacks.
            let mut candidates: Vec<(String, String, indexmap::IndexMap<String, String>)> =
                vec![(endpoint_url.clone(), model.clone(), headers.clone())];
            if let Some(ref fbs) = fallbacks {
                candidates.extend(fbs.iter().cloned());
            }
            // Wall-clock deadline for runaway trickle streams.
            let round_deadline =
                Instant::now() + std::time::Duration::from_secs((agent_stream_timeout * 4).max(1200));

            let tools_arg = if all_tool_schemas.is_empty() {
                None
            } else {
                Some(all_tool_schemas.clone())
            };
            let pt_arg = if round_num == 1 { prompt_type.clone() } else { None };

            let inner = crate::src::llm_core::stream_llm_with_fallback(
                candidates,
                messages.clone(),
                temperature,
                max_tokens,
                pt_arg,
                tools_arg,
                agent_stream_timeout,
            );
            futures_util::pin_mut!(inner);
            while let Some(chunk) = inner.next().await {
                if Instant::now() > round_deadline {
                    logger::warning(&format!(
                        "[agent] round {round_num} stream exceeded wall-clock deadline; cutting off"
                    ));
                    break;
                }
                if chunk.starts_with("event: error") {
                    yield chunk;
                    continue;
                }
                if chunk.starts_with("data: ") && !chunk.starts_with("data: [DONE]") {
                    let payload = &chunk[6..];
                    let data: Value = match serde_json::from_str(payload.trim()) {
                        Ok(v) => v,
                        Err(_) => {
                            if round_num == 1 {
                                yield chunk;
                            }
                            continue;
                        }
                    };
                    let dtype = data.get("type").and_then(|t| t.as_str());
                    if dtype == Some("tool_call_delta") {
                        let arg_delta = data.get("arg_delta").and_then(|a| a.as_str()).unwrap_or("");
                        doc_acc.push_str(arg_delta);
                        if !doc_opened {
                            if let Some(tm) = DOC_TITLE_RE.captures(&doc_acc) {
                                doc_opened = true;
                                let title = decode_json_string_fragment(tm.get(1).map(|m| m.as_str()).unwrap_or(""));
                                let lang = DOC_LANG_RE
                                    .captures(&doc_acc)
                                    .map(|lm| decode_json_string_fragment(lm.get(1).map(|m| m.as_str()).unwrap_or("")))
                                    .unwrap_or_default();
                                logger::info(&format!("Doc streaming: open title={title:?} lang={lang:?}"));
                                yield format!("data: {}\n\n", json!({"type": "doc_stream_open", "title": title, "language": lang}));
                            }
                        }
                        if doc_opened {
                            if let Some(cm) = DOC_CONTENT_RE.find(&doc_acc) {
                                let raw = &doc_acc[cm.end()..];
                                let raw = DOC_CONTENT_TAIL_RE.replace(raw, "").to_string();
                                let decoded = decode_doc_content(&raw);
                                let dlen = decoded.chars().count() as i64;
                                if dlen > doc_last_len {
                                    doc_last_len = dlen;
                                    yield format!("data: {}\n\n", json!({"type": "doc_stream_delta", "content": decoded}));
                                }
                            }
                        }
                    } else if dtype == Some("tool_calls") {
                        native_tool_calls = data
                            .get("calls")
                            .and_then(|c| c.as_array())
                            .cloned()
                            .unwrap_or_default();
                        logger::info(&format!(
                            "Agent round {round_num}: received {} native tool call(s)",
                            native_tool_calls.len()
                        ));
                    } else if dtype == Some("usage") {
                        let u = data.get("data").cloned().unwrap_or(Value::Null);
                        let round_input = u.get("input_tokens").and_then(|x| x.as_i64()).unwrap_or(0);
                        real_input_tokens += round_input;
                        real_output_tokens += u.get("output_tokens").and_then(|x| x.as_i64()).unwrap_or(0);
                        last_round_input_tokens = round_input;
                        has_real_usage = true;
                        // Backend-reported TRUE generation speed (llama.cpp
                        // timings.predicted_per_second) — pure decode, excludes
                        // prefill/network. Preferred over tokens/wall-clock,
                        // which reads low. Keep the last round's value.
                        if let Some(g) = u.get("gen_tps").and_then(|x| x.as_f64()) {
                            if g != 0.0 {
                                backend_gen_tps = g;
                            }
                        }
                        if let Some(p) = u.get("prefill_tps").and_then(|x| x.as_f64()) {
                            if p != 0.0 {
                                backend_prefill_tps = p;
                            }
                        }
                    } else if dtype == Some("fallback") {
                        // The selected model failed and another answered; surface
                        // the notice so a misconfigured provider isn't masked.
                        logger::warning(&format!(
                            "[agent] round {round_num} fell back: {} -> {}",
                            data.get("selected_model").and_then(|v| v.as_str()).unwrap_or(""),
                            data.get("answered_by").and_then(|v| v.as_str()).unwrap_or("")
                        ));
                        yield chunk.clone();
                    } else if data.get("delta").is_some() {
                        if !first_token_received {
                            time_to_first_token = Some(total_start.elapsed().as_secs_f64());
                            first_token_received = true;
                        }
                        let delta_text = data.get("delta").and_then(|d| d.as_str()).unwrap_or("");
                        if data.get("thinking").and_then(|t| t.as_bool()).unwrap_or(false) {
                            round_reasoning.push_str(delta_text);
                        } else {
                            round_response.push_str(delta_text);
                            full_response.push_str(delta_text);
                        }
                        yield chunk.clone();
                        // Text-fence doc streaming for rounds 2+ (round 1 fence
                        // detection is handled by the frontend + the fenced-block
                        // path below).
                        if round_num > 1 && doc_acc.is_empty() {
                            for ev in collect_text_fence_events(
                                &round_response,
                                &mut doc_opened,
                                &mut doc_fence_offset,
                                &mut doc_scan_from,
                                &mut doc_last_len,
                            ) {
                                yield ev;
                            }
                        }
                    } else if data.get("error").is_some() {
                        let err_msg = data.get("error").and_then(|e| e.as_str()).unwrap_or("unknown");
                        logger::error(&format!("Agent round {round_num}: stream error: {err_msg}"));
                        yield format!(
                            "data: {}\n\n",
                            json!({"delta": format!("\n\n*[Stream error: {err_msg}]*")})
                        );
                    }
                } else if chunk.starts_with("event: ") {
                    yield chunk;
                }
                // intercept [DONE] — don't forward until all rounds finish.
            }
            // Remember this round's reasoning for the end-of-loop fallback.
            last_round_reasoning = round_reasoning.clone();

            let (mut tool_blocks, used_native) =
                _resolve_tool_blocks(&round_response, &native_tool_calls, round_num);

            // ── Force-answer round ──
            if force_answer {
                if !tool_blocks.is_empty() {
                    logger::info(&format!(
                        "[agent] force-answer round {round_num}: discarding {} ignored tool call(s)",
                        tool_blocks.len()
                    ));
                }
                tool_blocks = Vec::new();
                let stripped = strip_tool_blocks(&round_response);
                let no_think = THINK_RE.replace_all(&stripped, "").trim().to_string();
                if no_think.is_empty() {
                    // Grace synthesis: one blunt non-streaming call.
                    let mut synth = String::new();
                    let mut synth_messages = messages.clone();
                    synth_messages.push(json!({
                        "role": "user",
                        "content": "Using ONLY the information already gathered above, write \
the final answer for the user now. Do NOT call any tools, \
do NOT explain your reasoning — output the finished response \
directly. If some data couldn't be fetched, just work with \
what you have and note what's missing in one short line."
                    }));
                    match crate::src::llm_core::llm_call_async(
                        &endpoint_url, &model, synth_messages, 0.3, max_tokens, headers.clone(), 60,
                    ).await {
                        Ok(raw) => {
                            let s = strip_tool_blocks(&raw);
                            synth = THINK_RE.replace_all(&s, "").trim().to_string();
                        }
                        Err(e) => {
                            logger::warning(&format!("[agent] grace synthesis failed: {e}"));
                        }
                    }
                    if !synth.is_empty() {
                        yield format!("data: {}\n\n", json!({"delta": synth}));
                        full_response.push_str(&synth);
                    } else {
                        let fb = "I gathered some search results but couldn't pull a clean \
answer together. Want me to try a more specific question, \
or summarize what I did find?";
                        yield format!("data: {}\n\n", json!({"delta": fb}));
                        full_response.push_str(fb);
                    }
                }
            }

            // ── Auto-create document fallback ──
            let has_doc_tool = tool_blocks.iter().any(|b| {
                matches!(b.tool_type.as_str(), "create_document" | "update_document")
            }) || native_tool_calls.iter().any(|tc| {
                matches!(tc.get("name").and_then(|n| n.as_str()), Some("create_document") | Some("update_document"))
            });
            if !has_doc_tool && session_id_ref.is_some() && !disabled_tools.contains("create_document") {
                if let Some((lang_tag, code_body)) = find_autodoc_code_block(&round_response) {
                    let lang_map = |t: &str| -> String {
                        match t {
                            "py" => "python".to_string(),
                            "js" => "javascript".to_string(),
                            "ts" => "typescript".to_string(),
                            "" => "text".to_string(),
                            other => other.to_string(),
                        }
                    };
                    let doc_lang = lang_map(&lang_tag);
                    let doc_title = format!("Code ({doc_lang})");
                    let tb = ToolBlock::new(
                        "create_document",
                        &format!("{doc_title}\n{doc_lang}\n{code_body}"),
                    );
                    tool_blocks.push(tb);
                    yield format!("data: {}\n\n", json!({"type": "doc_stream_open", "title": doc_title, "language": doc_lang}));
                    yield format!("data: {}\n\n", json!({"type": "doc_stream_delta", "content": code_body}));
                    logger::info(&format!(
                        "Auto-created document from {lang_tag} code block ({} lines)",
                        code_body.matches('\n').count() + 1
                    ));
                }
            }

            // Save cleaned round text.
            let cleaned_round = strip_tool_blocks(&round_response).trim().to_string();
            round_texts.push(cleaned_round.clone());

            if tool_blocks.is_empty() {
                // ── Completion verifier ──
                let claimed_done = !THINK_RE.replace_all(&cleaned_round, "").trim().is_empty();
                if effectful_used
                    && !force_answer
                    && claimed_done
                    && verifier_rounds < _VERIFIER_MAX_ROUNDS
                    && setting_agent_verifier_subagent()
                {
                    yield format!("data: {}\n\n", json!({"type": "agent_step", "round": round_num}));
                    let vfail = _run_verifier_subagent(
                        &verifier_instruction,
                        &_build_actions_snapshot(&tool_events, 8000),
                        &endpoint_url, &model, &headers,
                    ).await;
                    if !vfail.is_empty() {
                        verifier_rounds += 1;
                        logger::info(&format!(
                            "[agent] verifier flagged {} issue(s) on round {round_num}: {vfail:?}",
                            vfail.len()
                        ));
                        let note = "\n\n_Double-checked the work and found something to fix._\n\n";
                        yield format!("data: {}\n\n", json!({"delta": note}));
                        full_response.push_str(note);
                        messages.push(json!({
                            "role": "system",
                            "content": format!(
                                "An independent verifier reviewed your work against the \
original request and found issues that must be fixed before \
this is actually done:\n- {}\n\nFix these now using tools, then finish.",
                                vfail.join("\n- ")
                            ),
                        }));
                        effectful_used = false;
                        round_num += 1;
                        continue;
                    }
                }
                break; // no tools — done
            }

            // ── Loop-breaker ──
            let mut sig_parts: Vec<String> = tool_blocks
                .iter()
                .map(|b| {
                    let head: String = b.content.trim().chars().take(120).collect();
                    format!("{}:{}", b.tool_type, head)
                })
                .collect();
            sig_parts.sort();
            let sig = sig_parts.join("|");
            let is_repeat = recent_call_sigs.contains(&sig);
            if recent_call_sigs.len() == 6 {
                recent_call_sigs.pop_front();
            }
            recent_call_sigs.push_back(sig.clone());
            for b in &tool_blocks {
                *tool_type_counts.entry(b.tool_type.clone()).or_insert(0) += 1;
            }
            let real_text = THINK_RE.replace_all(&cleaned_round, "").trim().to_string();
            if is_repeat && real_text.is_empty() {
                stuck_rounds += 1;
            } else {
                stuck_rounds = 0;
            }
            let runaway: Option<String> = tool_type_counts
                .iter()
                .find(|(_, n)| **n >= 15)
                .map(|(t, _)| t.clone());
            if stuck_rounds >= 4 || runaway.is_some() {
                let reason = match &runaway {
                    Some(t) => format!("calling {t} over and over"),
                    None => "repeating the same tool calls without new progress".to_string(),
                };
                let sig_head: String = sig.chars().take(80).collect();
                logger::warning(&format!(
                    "[agent] loop-breaker tripped on round {round_num} ({reason}); sig={sig_head:?}"
                ));
                let off: Vec<&str> = ["web_search", "bash"]
                    .into_iter()
                    .filter(|t| disabled_tools.contains(*t))
                    .collect();
                let off_note = if off.is_empty() {
                    String::new()
                } else {
                    format!(
                        " ({} is currently disabled — say so if you needed it.)",
                        off.join(", ")
                    )
                };
                force_answer = true;
                messages.push(json!({
                    "role": "system",
                    "content": format!(
                        "You're repeating tool calls without converging. STOP calling \
tools and end the turn one of two ways: (a) write your best \
final answer NOW from the information already gathered, or \
(b) if you're genuinely blocked, say plainly what's blocking \
you in a sentence or two.{off_note}"
                    ),
                }));
                full_response.push_str("\n\n");
                yield format!("data: {}\n\n", json!({"type": "agent_step", "round": round_num + 1}));
                round_num += 1;
                continue;
            }

            // ── Pre-stream document content for fenced blocks (non-native) ──
            if !doc_opened
                && round_num == 1
                && tool_blocks.iter().any(|b| b.tool_type == "create_document")
            {
                doc_opened = true;
            }
            if !doc_opened {
                for block in &tool_blocks {
                    if block.tool_type == "create_document" {
                        let content_trimmed = block.content.trim();
                        let lines: Vec<&str> = content_trimmed.split('\n').collect();
                        let title = lines.first().map(|s| s.trim()).unwrap_or("Untitled");
                        let mut lang = "";
                        let mut content_start = 1;
                        if lines.len() > 1 {
                            let l1 = lines[1].trim();
                            if l1.chars().count() < 20 && !l1.is_empty() && l1.chars().all(|c| c.is_alphabetic()) {
                                lang = l1;
                                content_start = 2;
                            }
                        }
                        let content = if lines.len() > content_start {
                            lines[content_start..].join("\n")
                        } else {
                            String::new()
                        };
                        yield format!("data: {}\n\n", json!({"type": "doc_stream_open", "title": title, "language": lang}));
                        if !content.is_empty() {
                            yield format!("data: {}\n\n", json!({"type": "doc_stream_delta", "content": content}));
                        }
                        break;
                    } else if block.tool_type == "update_document" {
                        let content = block.content.trim().to_string();
                        yield format!("data: {}\n\n", json!({"type": "doc_stream_open", "title": "", "language": ""}));
                        yield format!("data: {}\n\n", json!({"type": "doc_stream_delta", "content": content}));
                        break;
                    }
                }
            }

            // ── Execute each tool block ──
            let mut tool_results: Vec<String> = Vec::new();
            let mut tool_result_texts: Vec<String> = Vec::new();
            let mut budget_hit = false;
            for block in tool_blocks.iter() {
                if max_tool_calls > 0 && total_tool_calls >= max_tool_calls {
                    yield format!(
                        "data: {}\n\n",
                        json!({"type": "budget_exceeded", "limit": max_tool_calls, "used": total_tool_calls})
                    );
                    budget_hit = true;
                    break;
                }
                total_tool_calls += 1;

                let is_doc_tool = matches!(
                    block.tool_type.as_str(),
                    "create_document" | "update_document" | "edit_document" | "suggest_document"
                );
                let cmd_display: String = if is_doc_tool {
                    block.content.split('\n').next().unwrap_or("").trim().chars().take(80).collect()
                } else {
                    block.content.trim().to_string()
                };

                yield format!(
                    "data: {}\n\n",
                    json!({"type": "tool_start", "tool": block.tool_type, "command": cmd_display, "round": round_num})
                );

                // Progress channel: forward {elapsed_s, tail} payloads as
                // tool_progress events. The progress_cb pushes into an mpsc
                // channel; we drain it as the tool runs.
                let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel::<Value>();
                let progress_cb: ProgressCb = {
                    let tx = tx.clone();
                    std::sync::Arc::new(move |payload: Value| {
                        let tx = tx.clone();
                        Box::pin(async move {
                            let _ = tx.send(payload);
                        }) as std::pin::Pin<Box<dyn std::future::Future<Output = ()> + Send>>
                    })
                };
                drop(tx); // keep only the cb's clone alive

                let tool_fut = execute_tool_block(
                    block,
                    session_id_ref,
                    Some(&disabled_tools),
                    owner_ref,
                    Some(progress_cb),
                );
                futures_util::pin_mut!(tool_fut);
                let (desc, result) = loop {
                    tokio::select! {
                        biased;
                        maybe_evt = rx.recv() => {
                            if let Some(evt) = maybe_evt {
                                let mut payload = Map::new();
                                payload.insert("type".to_string(), json!("tool_progress"));
                                payload.insert("tool".to_string(), json!(block.tool_type));
                                payload.insert("round".to_string(), json!(round_num));
                                if let Value::Object(m) = evt {
                                    for (k, v) in m {
                                        payload.insert(k, v);
                                    }
                                }
                                yield format!("data: {}\n\n", Value::Object(payload));
                            }
                        }
                        res = &mut tool_fut => {
                            // Drain any remaining progress events.
                            while let Ok(evt) = rx.try_recv() {
                                let mut payload = Map::new();
                                payload.insert("type".to_string(), json!("tool_progress"));
                                payload.insert("tool".to_string(), json!(block.tool_type));
                                payload.insert("round".to_string(), json!(round_num));
                                if let Value::Object(m) = evt {
                                    for (k, v) in m {
                                        payload.insert(k, v);
                                    }
                                }
                                yield format!("data: {}\n\n", Value::Object(payload));
                            }
                            break res;
                        }
                    }
                };
                let mut result = result;

                // Extract structured web sources from web_search output.
                // web_search returns {"output": ..., "exit_code": 0}; check
                // "output" FIRST so the <!-- SOURCES:…--> marker is found and
                // stripped even when the result doesn't carry a "results"/"stdout"
                // key (Python `result.get("output") or .get("results") or
                // .get("stdout")`).
                let src_text = result
                    .get("output")
                    .and_then(|v| v.as_str())
                    .filter(|s| !s.is_empty())
                    .or_else(|| result.get("results").and_then(|v| v.as_str()).filter(|s| !s.is_empty()))
                    .or_else(|| result.get("stdout").and_then(|v| v.as_str()).filter(|s| !s.is_empty()))
                    .unwrap_or("")
                    .to_string();
                if block.tool_type == "web_search" && !src_text.is_empty() {
                    let src_marker = "<!-- SOURCES:";
                    if let Some(src_idx) = src_text.find(src_marker) {
                        if let Some(rel_end) = src_text[src_idx..].find(" -->") {
                            let src_end = src_idx + rel_end;
                            let json_part = &src_text[src_idx + src_marker.len()..src_end];
                            if let Ok(extracted) = serde_json::from_str::<Value>(json_part) {
                                yield format!("data: {}\n\n", json!({"type": "web_sources", "data": extracted}));
                                // Strip the marker from the result so it doesn't
                                // show in chat (output-first elif ladder).
                                let clean = src_text[..src_idx].trim_end().to_string();
                                if result.contains_key("output") {
                                    result.insert("output".to_string(), json!(clean));
                                } else if result.contains_key("results") {
                                    result.insert("results".to_string(), json!(clean));
                                } else if result.contains_key("stdout") {
                                    result.insert("stdout".to_string(), json!(clean));
                                }
                            }
                        }
                    }
                }

                // Emit doc-specific event for document tools.
                if is_doc_tool && result.contains_key("action") {
                    let action = result.get("action").and_then(|a| a.as_str()).unwrap_or("");
                    if action == "suggest" {
                        yield format!(
                            "data: {}\n\n",
                            json!({
                                "type": "doc_suggestions",
                                "doc_id": result.get("doc_id"),
                                "suggestions": result.get("suggestions"),
                            })
                        );
                    } else {
                        yield format!(
                            "data: {}\n\n",
                            json!({
                                "type": "doc_update",
                                "doc_id": result.get("doc_id"),
                                "content": result.get("content"),
                                "version": result.get("version"),
                                "title": result.get("title").cloned().unwrap_or(json!("")),
                                "language": result.get("language"),
                            })
                        );
                    }
                }

                // Emit ui_control event.
                if result.contains_key("ui_event") {
                    yield format!("data: {}\n\n", json!({"type": "ui_control", "data": result}));
                }

                // Build tool-bubble output text.
                let output_text = build_output_text(is_doc_tool, &result);

                // Emit tool_output.
                let mut tool_output_data = Map::new();
                tool_output_data.insert("type".to_string(), json!("tool_output"));
                tool_output_data.insert("tool".to_string(), json!(block.tool_type));
                tool_output_data.insert("command".to_string(), json!(cmd_display));
                tool_output_data.insert("output".to_string(), json!(output_text));
                tool_output_data.insert(
                    "exit_code".to_string(),
                    result.get("exit_code").cloned().unwrap_or(Value::Null),
                );
                if result.contains_key("ui_event") {
                    tool_output_data.insert("ui_event".to_string(), result.get("ui_event").cloned().unwrap());
                    for k in ["toggle_name", "state", "mode", "model", "endpoint_url", "theme_name", "colors"] {
                        if let Some(v) = result.get(k) {
                            tool_output_data.insert(k.to_string(), v.clone());
                        }
                    }
                }
                for k in ["image_url", "image_prompt", "image_model", "image_size", "image_quality"] {
                    if let Some(v) = result.get(k) {
                        tool_output_data.insert(k.to_string(), v.clone());
                    }
                }
                if let Some(images) = result.get("images").and_then(|i| i.as_array()) {
                    if let Some(img) = images.first() {
                        let mime = img.get("mimeType").and_then(|m| m.as_str()).unwrap_or("");
                        let data = img.get("data").and_then(|d| d.as_str()).unwrap_or("");
                        tool_output_data.insert(
                            "screenshot".to_string(),
                            json!(format!("data:{mime};base64,{data}")),
                        );
                    }
                }
                yield format!("data: {}\n\n", Value::Object(tool_output_data));

                // doc_update for native doc tools carrying a real doc id.
                if matches!(block.tool_type.as_str(), "create_document" | "update_document" | "edit_document")
                    && result.get("doc_id").map(|v| !v.is_null()).unwrap_or(false)
                {
                    yield format!(
                        "data: {}\n\n",
                        json!({
                            "type": "doc_update",
                            "doc_id": result.get("doc_id"),
                            "title": result.get("title").cloned().unwrap_or(json!("")),
                            "language": result.get("language").cloned().unwrap_or(json!("")),
                            "content": result.get("content").cloned().unwrap_or(json!("")),
                            "version": result.get("version").cloned().unwrap_or(json!(1)),
                        })
                    );
                }

                // Inline research open-link.
                if let Some(rsid) = result.get("research_session_id").and_then(|v| v.as_str()) {
                    let anchor = format!("\n\n[Open in Deep Research](#research-{rsid})\n");
                    yield format!("data: {}\n\n", json!({"delta": anchor}));
                }

                // Save tool event for history.
                let mut tool_event = Map::new();
                tool_event.insert("round".to_string(), json!(round_num));
                tool_event.insert("tool".to_string(), json!(block.tool_type));
                tool_event.insert("command".to_string(), json!(cmd_display));
                tool_event.insert("output".to_string(), json!(output_text));
                tool_event.insert(
                    "exit_code".to_string(),
                    result.get("exit_code").cloned().unwrap_or(Value::Null),
                );
                if result.get("image_url").map(|v| !v.is_null()).unwrap_or(false) {
                    for ik in ["image_url", "image_prompt", "image_model", "image_size", "image_quality"] {
                        if let Some(v) = result.get(ik) {
                            if !v.is_null() {
                                tool_event.insert(ik.to_string(), v.clone());
                            }
                        }
                    }
                }
                if result.get("doc_id").map(|v| !v.is_null()).unwrap_or(false) {
                    tool_event.insert("doc_id".to_string(), result.get("doc_id").cloned().unwrap());
                    tool_event.insert("doc_title".to_string(), result.get("title").cloned().unwrap_or(json!("")));
                }
                tool_events.push(Value::Object(tool_event));
                if _VERIFIER_EFFECTFUL_TOOLS.contains(block.tool_type.as_str()) {
                    effectful_used = true;
                }

                let formatted = format_tool_result(&desc, &result);
                tool_results.push(formatted.clone());
                tool_result_texts.push(formatted);
            }

            if budget_hit {
                break;
            }

            // Feed results back for the next round.
            _append_tool_results(
                &mut messages,
                &round_response,
                &native_tool_calls,
                &tool_results,
                &tool_result_texts,
                used_native,
                round_num,
                &round_reasoning,
            );

            yield format!("data: {}\n\n", json!({"type": "agent_step", "round": round_num + 1}));
            full_response.push_str("\n\n");
            round_num += 1;
        }

        // If the response is completely empty and no tools were executed, yield a
        // fallback message so the user isn't left hanging.
        let (final_response, fallback_chunk) =
            _empty_response_fallback(&full_response, &last_round_reasoning, &tool_events);
        full_response = final_response;
        if let Some(fc) = fallback_chunk {
            yield fc;
        }

        // ── Final metrics ──
        let total_duration = total_start.elapsed().as_secs_f64();
        let metrics = _compute_final_metrics(
            &messages,
            &full_response,
            total_duration,
            time_to_first_token,
            context_length,
            real_input_tokens,
            real_output_tokens,
            has_real_usage,
            &tool_events,
            &round_texts,
            &model,
            last_round_input_tokens,
            Some(&prep_timings),
            backend_gen_tps,
            backend_prefill_tps,
        );
        yield format!("data: {}\n\n", json!({"type": "metrics", "data": metrics}));

        // Teacher-escalation: inline takeover visible in the chat stream (Python
        // L2087-2104). The student just finished; if Tier 1 flags failure, the
        // teacher gets a turn (its own tool calls forwarded to the user) and a
        // skill is saved ONLY if the teacher succeeds. Skipped when we ARE the
        // teacher (avoid recursion). `run_teacher_inline` self-gates on the
        // teacher-enabled setting + a failure verdict, so an enabled-but-passing
        // turn yields nothing.
        if !is_teacher_run {
            // Box::pin type-erases the teacher stream, breaking the otherwise
            // infinite type cycle (stream_agent_loop -> run_teacher_inline ->
            // stream_agent_loop, runtime-gated by `is_teacher_run`).
            let mut esc: std::pin::Pin<Box<dyn futures_util::Stream<Item = String> + Send>> =
                Box::pin(crate::src::teacher_escalation::run_teacher_inline(
                    endpoint_url.clone(),
                    messages.clone(),
                    tool_events.clone(),
                    full_response.clone(),
                    owner.clone(),
                ));
            while let Some(evt) = esc.next().await {
                yield evt;
            }
        }

        yield "data: [DONE]\n\n".to_string();
    }
}

// ---------------------------------------------------------------------------
// Helpers used only by the streaming loop
// ---------------------------------------------------------------------------

/// Look up `ModelEndpoint.supports_tools` for `endpoint_url` (and its `/`-stripped
/// / `/`-suffixed variants), mirroring the Python DB lookup. `None` = unknown.
fn lookup_endpoint_supports_tools(endpoint_url: &str) -> Option<bool> {
    use rusqlite::OptionalExtension;
    let conn = crate::core::database::session_local().ok()?;
    let query = |url: &str| -> Option<Option<bool>> {
        conn.query_row(
            "SELECT supports_tools FROM model_endpoints WHERE base_url = ?1",
            [url],
            |row| row.get::<_, Option<bool>>(0),
        )
        .optional()
        .ok()
        .flatten()
    };
    if let Some(s) = query(endpoint_url) {
        return s;
    }
    if !endpoint_url.is_empty() {
        let u = endpoint_url.trim_end_matches('/');
        if let Some(s) = query(u) {
            return s;
        }
        let u_slash = format!("{u}/");
        if let Some(s) = query(&u_slash) {
            return s;
        }
    }
    None
}

/// Build the tool-bubble `output_text` (the Python key-presence elif ladder).
fn build_output_text(is_doc_tool: bool, result: &Map<String, Value>) -> String {
    let take2000 = |s: &str| -> String { s.chars().take(2000).collect() };
    let take4000 = |s: &str| -> String { s.chars().take(4000).collect() };
    if is_doc_tool && result.contains_key("action") {
        let action = result.get("action").and_then(|a| a.as_str()).unwrap_or("");
        let title = result.get("title").and_then(|t| t.as_str()).unwrap_or("");
        let ver = match result.get("version") {
            Some(v) if !v.is_null() => v.to_string(),
            _ => "?".to_string(),
        };
        return match action {
            "create" => format!("Document created: \"{title}\" (v{ver})"),
            "edit" => {
                let applied = result.get("applied").and_then(|a| a.as_i64()).unwrap_or(0);
                format!("Document edited: \"{title}\" (v{ver}, {applied} edit(s))")
            }
            "update" => format!("Document updated: \"{title}\" (v{ver})"),
            _ => String::new(),
        };
    }
    if let Some(_stdout) = result.get("stdout") {
        let stdout = result.get("stdout").and_then(|v| v.as_str()).unwrap_or("");
        let val = if !stdout.is_empty() {
            stdout
        } else {
            let stderr = result.get("stderr").and_then(|v| v.as_str()).unwrap_or("");
            if !stderr.is_empty() {
                stderr
            } else {
                result.get("error").and_then(|v| v.as_str()).unwrap_or("")
            }
        };
        return take2000(val);
    }
    if let Some(output) = result.get("output") {
        return take2000(output.as_str().unwrap_or(""));
    }
    if let Some(response) = result.get("response").and_then(|v| v.as_str()) {
        let label = result
            .get("model")
            .and_then(|v| v.as_str())
            .or_else(|| result.get("session_name").and_then(|v| v.as_str()))
            .unwrap_or("AI");
        return take4000(&format!("{label}: {response}"));
    }
    if let Some(content) = result.get("content").and_then(|v| v.as_str()) {
        return take2000(content);
    }
    if let Some(results) = result.get("results").and_then(|v| v.as_str()) {
        return take4000(results);
    }
    if result.contains_key("session_id") && result.contains_key("name") {
        let name = result.get("name").and_then(|v| v.as_str()).unwrap_or("");
        let sid = result.get("session_id").and_then(|v| v.as_str()).unwrap_or("");
        return format!("Session created: {name} (id: {sid})");
    }
    if let Some(success) = result.get("success") {
        return if success.as_bool().unwrap_or(false) {
            format!("Written: {}", result.get("path").and_then(|v| v.as_str()).unwrap_or(""))
        } else {
            format!("Error: {}", result.get("error").and_then(|v| v.as_str()).unwrap_or(""))
        };
    }
    if let Some(error) = result.get("error").and_then(|v| v.as_str()) {
        return take2000(error);
    }
    String::new()
}

// Regexes for the native (tool_call_delta) doc streaming JSON scan.
static DOC_TITLE_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r#""title"\s*:\s*"((?:[^"\\]|\\.)*)""#).unwrap());
static DOC_LANG_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r#""language"\s*:\s*"((?:[^"\\]|\\.)*)""#).unwrap());
static DOC_CONTENT_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r#""content"\s*:\s*""#).unwrap());
static DOC_CONTENT_TAIL_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r#""\s*\}\s*$"#).unwrap());

/// Decode a JSON string fragment: `json.loads('"' + frag + '"')` with the
/// raw-fragment fallback.
fn decode_json_string_fragment(frag: &str) -> String {
    let wrapped = format!("\"{frag}\"");
    match serde_json::from_str::<String>(&wrapped) {
        Ok(s) => s,
        Err(_) => frag.to_string(),
    }
}

/// Decode the partial `content` value (Python's nested try/except ladder).
fn decode_doc_content(raw: &str) -> String {
    let wrapped = format!("\"{raw}\"");
    if let Ok(s) = serde_json::from_str::<String>(&wrapped) {
        return s;
    }
    let trimmed = raw.trim_end_matches('\\');
    let wrapped2 = format!("\"{trimmed}\"");
    if let Ok(s) = serde_json::from_str::<String>(&wrapped2) {
        return s;
    }
    // Final fallback: literal escape substitution.
    raw.replace("\\n", "\n")
        .replace("\\t", "\t")
        .replace("\\\"", "\"")
        .replace("\\\\", "\\")
}

/// Detect + collect `doc_stream_open` / `doc_stream_delta` events for the text-
/// fence `create_document` streaming path (rounds 2+). Mirrors the Python body
/// at L1593-1632. Mutates the streaming-state cursors in place and returns the
/// SSE strings to yield.
fn collect_text_fence_events(
    round_response: &str,
    doc_opened: &mut bool,
    doc_fence_offset: &mut usize,
    doc_scan_from: &mut usize,
    doc_last_len: &mut i64,
) -> Vec<String> {
    let mut out = Vec::new();
    let fence_marker = "```create_document\n";
    let known_langs: HashSet<&str> = [
        "python", "py", "javascript", "js", "typescript", "ts", "html", "css",
        "json", "yaml", "bash", "sql", "rust", "go", "java", "c", "cpp",
        "markdown", "text",
    ]
    .into_iter()
    .collect();

    if !*doc_opened {
        // Byte-index search from doc_scan_from (round_response is ASCII fences;
        // byte offsets are safe for the marker, but content can be UTF-8, so we
        // operate on byte slices carefully).
        let scan_from = (*doc_scan_from).min(round_response.len());
        if let Some(rel) = round_response[scan_from..].find(fence_marker) {
            let fi = scan_from + rel;
            let after = &round_response[fi + fence_marker.len()..];
            let fl: Vec<&str> = after.split('\n').collect();
            if !fl.is_empty() && !fl[0].trim().is_empty() {
                *doc_opened = true;
                let ft = fl[0].trim().to_string();
                let flang = if fl.len() > 1 && known_langs.contains(fl[1].trim().to_lowercase().as_str()) {
                    fl[1].trim().to_string()
                } else {
                    String::new()
                };
                // offset = fi + len(marker) + len(fl[0]) + 1 [+ len(fl[1]) + 1]
                let mut offset = fi + fence_marker.len() + fl[0].len() + 1;
                if !flang.is_empty() {
                    offset += fl[1].len() + 1;
                }
                *doc_fence_offset = offset;
                *doc_last_len = 0;
                out.push(format!(
                    "data: {}\n\n",
                    json!({"type": "doc_stream_open", "title": ft, "language": flang})
                ));
            }
        }
    }
    if *doc_opened {
        let offset = (*doc_fence_offset).min(round_response.len());
        let rc_full = &round_response[offset..];
        let ci = rc_full.find("\n```");
        let rc = match ci {
            Some(idx) => &rc_full[..idx],
            None => rc_full,
        };
        let rc_len = rc.chars().count() as i64;
        if rc_len > *doc_last_len {
            *doc_last_len = rc_len;
            out.push(format!(
                "data: {}\n\n",
                json!({"type": "doc_stream_delta", "content": rc})
            ));
        }
        if let Some(idx) = ci {
            *doc_opened = false;
            *doc_scan_from = *doc_fence_offset + idx + "\n```".len();
            *doc_fence_offset = 0;
            *doc_last_len = 0;
        }
    }
    out
}

/// Find the first auto-document-able fenced code block (>=30 newlines, not a tool
/// tag). Returns `(lang_tag_lower, code_body_stripped)`. Mirrors the Python
/// `_code_block_re.finditer` loop (only the first qualifying block).
fn find_autodoc_code_block(round_response: &str) -> Option<(String, String)> {
    use crate::src::agent_tools::TOOL_TAGS;
    static CODE_BLOCK_RE: Lazy<Regex> =
        Lazy::new(|| Regex::new(r"```(\w*)\n([\s\S]*?)```").unwrap());
    for cap in CODE_BLOCK_RE.captures_iter(round_response) {
        let lang_tag = cap.get(1).map(|m| m.as_str()).unwrap_or("").to_lowercase();
        let code_body = cap.get(2).map(|m| m.as_str()).unwrap_or("").trim().to_string();
        if code_body.matches('\n').count() < 30 {
            continue;
        }
        if TOOL_TAGS.contains(lang_tag.as_str()) {
            continue;
        }
        return Some((lang_tag, code_body));
    }
    None
}

#[cfg(test)]
mod agent_loop_web_tests {
    use super::*;
    use serde_json::json;

    // ── _empty_response_fallback (Python L1318-1339) ──

    #[test]
    fn empty_fallback_keeps_nonempty_response() {
        // Non-empty content -> returned verbatim, nothing emitted.
        let (resp, chunk) = _empty_response_fallback("hello", "", &[]);
        assert_eq!(resp, "hello");
        assert!(chunk.is_none());
    }

    #[test]
    fn empty_fallback_keeps_response_when_tools_ran() {
        // Empty content but tools ran -> keep the (empty) response, no apology.
        let events = vec![json!({"tool": "bash"})];
        let (resp, chunk) = _empty_response_fallback("", "", &events);
        assert_eq!(resp, "");
        assert!(chunk.is_none());
    }

    #[test]
    fn empty_fallback_persists_reasoning_silently() {
        // Thinking model routed all tokens to reasoning_content: persist the
        // reasoning as the final response but emit NOTHING (it was already
        // streamed as {thinking:true} chunks).
        let (resp, chunk) = _empty_response_fallback("   ", "let me think...", &[]);
        assert_eq!(resp, "let me think...");
        assert!(chunk.is_none());
    }

    #[test]
    fn empty_fallback_emits_canned_apology() {
        // Truly empty (no content, no reasoning, no tools) -> canned apology,
        // emitted as a delta SSE chunk.
        let (resp, chunk) = _empty_response_fallback("", "", &[]);
        assert_eq!(
            resp,
            "The model returned an empty response. Please try again or switch to a different model."
        );
        let chunk = chunk.expect("apology must be emitted");
        assert!(chunk.starts_with("data: "));
        assert!(chunk.contains("empty response"));
        // Round-trips as valid SSE JSON with a `delta` key equal to the message.
        let payload: Value =
            serde_json::from_str(chunk.trim_start_matches("data: ").trim()).unwrap();
        assert_eq!(payload.get("delta").and_then(|v| v.as_str()), Some(resp.as_str()));
    }

    // ── item 106: adaptive input-token budget (replaces the old
    // min(context_length, soft_budget) cap that pinned long-context models at
    // 6000). Locks in the values stream_agent_loop now derives via
    // compute_input_token_budget_with. ──

    #[test]
    fn budget_scales_to_window_when_not_overridden() {
        // 128K-context model, default 6000 budget, NOT explicitly set -> scales
        // to 85% of the window (not the old hard cap of 6000).
        let b = crate::src::context_budget::compute_input_token_budget_with(
            crate::src::context_budget::DEFAULT_BUDGET,
            128_000,
            false,
            crate::src::context_budget::DEFAULT_BUDGET,
            crate::src::context_budget::DEFAULT_HEADROOM,
            crate::src::context_budget::DEFAULT_HARD_MAX,
        );
        assert_eq!(b, 108_800); // 0.85 * 128000
    }

    #[test]
    fn budget_capped_at_hard_max_for_huge_windows() {
        let b = crate::src::context_budget::compute_input_token_budget_with(
            crate::src::context_budget::DEFAULT_BUDGET,
            1_000_000,
            false,
            crate::src::context_budget::DEFAULT_BUDGET,
            crate::src::context_budget::DEFAULT_HEADROOM,
            crate::src::context_budget::DEFAULT_HARD_MAX,
        );
        assert_eq!(b, crate::src::context_budget::DEFAULT_HARD_MAX); // 200_000
    }

    #[test]
    fn budget_honours_explicit_setting_clamped_to_window() {
        // Explicit user budget is honoured exactly, clamped to the window.
        let b = crate::src::context_budget::compute_input_token_budget_with(
            8000, 4096, true,
            crate::src::context_budget::DEFAULT_BUDGET,
            crate::src::context_budget::DEFAULT_HEADROOM,
            crate::src::context_budget::DEFAULT_HARD_MAX,
        );
        assert_eq!(b, 4096);
    }

    #[test]
    fn budget_falls_back_when_window_unknown() {
        // Unknown window (context_length == 0) -> configured/default fallback,
        // matching the graceful-degradation requirement of #106.
        let b = crate::src::context_budget::compute_input_token_budget_with(
            6000, 0, false,
            crate::src::context_budget::DEFAULT_BUDGET,
            crate::src::context_budget::DEFAULT_HEADROOM,
            crate::src::context_budget::DEFAULT_HARD_MAX,
        );
        assert_eq!(b, 6000);
    }

    // ── model_supports_tools / deepseek-r1 blocklist (the in-loop API-model
    // detection). Reproduce the exact predicates inline so the keyword-set
    // edits (gemma/deepseek-v/deepseek-chat in, bare deepseek out) and the
    // deepseek-r1 blocklist are guarded against regressions. ──

    fn supports_tools_kw(model: &str) -> bool {
        let model_lc = model.to_lowercase();
        [
            "gpt-4", "gpt-5", "gpt-o", "claude", "gemini", "gemma",
            "qwen3", "qwen2.5", "mixtral", "mistral", "llama-3.1", "llama-3.2",
            "llama-3.3", "llama-4",
            "minimax", "kimi", "yi-", "phi-3", "phi-4", "command-r",
            "glm-4", "internlm", "hermes",
            "deepseek-v", "deepseek-chat",
        ]
        .iter()
        .any(|kw| model_lc.contains(kw))
    }

    fn no_tools_kw(model: &str) -> bool {
        let model_lc = model.to_lowercase();
        ["deepseek-r1"].iter().any(|kw| model_lc.contains(kw))
    }

    #[test]
    fn gemma_now_supports_tools() {
        assert!(supports_tools_kw("gemma-3-27b-it"));
    }

    #[test]
    fn deepseek_chat_and_v_support_tools_bare_deepseek_does_not() {
        assert!(supports_tools_kw("deepseek-chat"));
        assert!(supports_tools_kw("deepseek-v3"));
        // bare "deepseek" prefix no longer auto-qualifies (the old "deepseek"
        // keyword was dropped); only the v/chat variants do.
        assert!(!supports_tools_kw("deepseek-coder"));
    }

    #[test]
    fn deepseek_r1_is_blocklisted() {
        // deepseek-r1 is a reasoning model that rejects tool schemas: it must
        // NOT qualify via the keyword list and IS on the no-tools blocklist.
        assert!(!supports_tools_kw("deepseek-r1-distill-qwen-7b"));
        assert!(no_tools_kw("deepseek-r1-distill-qwen-7b"));
    }
}
