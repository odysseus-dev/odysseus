// src/tool_implementations/management_service.rs  <- src/tool_implementations.py
//! The "management_service" tool group: handlers grouped by "needs a service,
//! HTTP, or an unported subsystem".
//!
//! Tools: `do_manage_skills`, `do_manage_settings`, `do_api_call`,
//! `do_manage_mcp`, `do_manage_research`, `do_trigger_research`,
//! `do_resolve_contact`, `do_manage_contact`.
//!
//! ## Faithful coverage vs. honest DEFER
//!
//! - `do_manage_mcp` — DB CRUD (`list`/`add`/`delete`/`enable`/`disable`) is
//!   ported faithfully against the existing `mcp_servers` table, and the
//!   manager-dependent actions (status enrichment on `list`, the post-`add`
//!   `connect_server`, the `delete` disconnect, `reconnect`, `list_tools`) are
//!   now wired through the live `McpManager` reachable via
//!   `agent_tools::get_mcp_manager()` (an `Arc<McpManager>` slot populated at
//!   startup). When no manager is available the EXACT Python soft-fail shapes
//!   are preserved (`list` -> `{"response":"No MCP manager available", ...}`,
//!   `reconnect` -> `{"error":"MCP manager not available", ...}`,
//!   `list_tools` -> `{"response":"No MCP manager", ...}`); nothing is ever
//!   fabricated as a fake success.
//! - `do_manage_research` — PORT_NOW. Pure filesystem (`data/deep_research/*.json`)
//!   + JSON, including the EXACT `^[A-Za-z0-9_-]+$` path-traversal guard.
//! - `do_api_call` — WIRED. Resolves + dispatches via the ported
//!   `src::integrations::{load_integrations, execute_api_call}`.
//! - `do_trigger_research` — WIRED. Self-POSTs `/api/research/start` (served
//!   in-process by `routes::research_routes`) over loopback with the
//!   internal-tool headers, the same self-HTTP class as the cookbook tools.
//! - `do_manage_skills` — WIRED. Drives the ported
//!   `services::memory::skills::SkillsManager` +
//!   `services::memory::skill_format::{Skill, slugify}` for the full
//!   list/view/view_ref/add/edit/patch/publish/delete/search CRUD.
//! - `do_manage_settings` — WIRED. Reads/writes the ported settings store
//!   (`src::settings::{load_settings, save_settings, get_setting, DEFAULT_SETTINGS}`)
//!   plus the `model_endpoints.cached_models` cache (via `session_local()`),
//!   reproducing the alias / coerce / secret-mask / endpoint-pairing tables.
//! - `do_resolve_contact`, `do_manage_contact` — WIRED. Drive the ported CardDAV
//!   stack via the public async wrappers in `routes::contacts_routes`
//!   (`fetch_contacts_json_async` / `create_contact_pub` / `update_contact_pub`
//!   / `delete_contact_pub`); `do_resolve_contact` also self-GETs the in-process
//!   `/api/email/resolve-contact` route over loopback for email-history matches.


use std::collections::HashMap;

use serde_json::{Map, Value};

use crate::pylog as logger;
// `get_mcp_manager()` returns `Option<Arc<McpManager>>`; the `Arc<McpManager>`
// type is used purely by inference here (all access is via method calls), so
// neither `std::sync::Arc` nor `McpManager` needs an explicit `use`.
use crate::src::agent_tools::get_mcp_manager;
// Ported skills subsystem (manage_skills), settings store (manage_settings),
// and the CardDAV contacts wrappers (resolve_contact / manage_contact).
use crate::routes::contacts_routes;
use crate::services::memory::skill_format::{slugify, Skill};
use crate::services::memory::skills::{AddSkill, SkillsManager};
use crate::src::settings::{get_setting, load_settings, save_settings, DEFAULT_SETTINGS};

// `_parse_tool_args` (PyResult<Map>) and `err_result` live in the group root
// `tool_implementations/mod.rs` (the shared helpers); the module tree is wired
// in a later step. We reference them via `super::`.
use super::{_parse_tool_args, err_result};

/// `{"action": <s>}` accessor mirroring Python's `args.get("action", "list")`.
/// The default fires ONLY when the key is ABSENT (Python's `.get` default).
/// A present-but-non-string action keeps its raw value (rendered via `str()`),
/// so it matches no string branch and falls through to the unknown-action arm —
/// it must NOT be coerced to the default.
fn action_or(args: &Map<String, Value>, default: &str) -> String {
    match args.get("action") {
        Some(Value::String(s)) => s.clone(),
        Some(other) => py_str(other),
        None => default.to_string(),
    }
}

/// `(args.get(key) or "").strip()` over a string field (non-strings -> "").
fn str_field<'a>(args: &'a Map<String, Value>, key: &str) -> &'a str {
    match args.get(key) {
        Some(Value::String(s)) => s.as_str(),
        _ => "",
    }
}

// ---------------------------------------------------------------------------
// Python `.get(...)`-truthiness helpers (manage_skills)
// ---------------------------------------------------------------------------

/// `args.get(key)` as an owned `String` only when it is a present string
/// (mirrors Python's `args.get(key)` where the caller then treats `None`/missing
/// the same; non-string JSON yields `None`).
fn opt_str(args: &Map<String, Value>, key: &str) -> Option<String> {
    match args.get(key) {
        Some(Value::String(s)) => Some(s.clone()),
        _ => None,
    }
}

/// `args.get(key) or <fallback>` over a string field: a non-empty string wins,
/// otherwise the fallback (Python truthiness — empty string is falsy).
fn str_or(args: &Map<String, Value>, key: &str, fallback: &str) -> String {
    match args.get(key) {
        Some(Value::String(s)) if !s.is_empty() => s.clone(),
        _ => fallback.to_string(),
    }
}

/// `args.get(key) or []` coerced to a list of strings (mirrors the Python
/// `args.get(key) or []` used for `tags`/`platforms`/etc. — a JSON array yields
/// its string entries; anything else yields `None` so `add_skill` applies its
/// own default). Returns `None` when the key is absent / not a non-empty array,
/// matching how the Rust `AddSkill` `Option<Vec<String>>` fields default.
fn opt_str_list(args: &Map<String, Value>, key: &str) -> Option<Vec<String>> {
    match args.get(key) {
        Some(Value::Array(a)) => Some(
            a.iter()
                .filter_map(|x| x.as_str().map(str::to_string))
                .collect(),
        ),
        _ => None,
    }
}

/// `args.get(key, default)` for an `f64` (Python `args.get("confidence", 0.8)`):
/// a JSON number wins, otherwise the default. Non-numeric JSON falls back to the
/// default (Python would only error later on `float(...)`, never here).
fn f64_or(args: &Map<String, Value>, key: &str, default: f64) -> f64 {
    match args.get(key) {
        Some(Value::Number(n)) => n.as_f64().unwrap_or(default),
        _ => default,
    }
}

/// `Value::Array` of `Skill`-derived `Vec<String>` for the `_skill_dump` map.
fn str_vec_value(v: &[String]) -> Value {
    Value::Array(v.iter().map(|s| Value::String(s.clone())).collect())
}

/// `_skill_dump(sk)` — translate a parsed `Skill` back into the `updates` map
/// `SkillsManager::update_skill` consumes (tool_implementations.py:785-806).
fn skill_dump(sk: &Skill) -> Map<String, Value> {
    let mut m = Map::new();
    m.insert("name".into(), Value::String(sk.name.clone()));
    m.insert("description".into(), Value::String(sk.description.clone()));
    m.insert("version".into(), Value::String(sk.version.clone()));
    m.insert("category".into(), Value::String(sk.category.clone()));
    m.insert("tags".into(), str_vec_value(&sk.tags));
    m.insert("platforms".into(), str_vec_value(&sk.platforms));
    m.insert(
        "requires_toolsets".into(),
        str_vec_value(&sk.requires_toolsets),
    );
    m.insert(
        "fallback_for_toolsets".into(),
        str_vec_value(&sk.fallback_for_toolsets),
    );
    m.insert("status".into(), Value::String(sk.status.clone()));
    m.insert(
        "confidence".into(),
        serde_json::Number::from_f64(sk.confidence)
            .map(Value::Number)
            .unwrap_or(Value::Null),
    );
    m.insert("source".into(), Value::String(sk.source.clone()));
    m.insert(
        "teacher_model".into(),
        match &sk.teacher_model {
            Some(t) => Value::String(t.clone()),
            None => Value::Null,
        },
    );
    m.insert(
        "owner".into(),
        match &sk.owner {
            Some(o) => Value::String(o.clone()),
            None => Value::Null,
        },
    );
    m.insert("when_to_use".into(), Value::String(sk.when_to_use.clone()));
    m.insert("procedure".into(), str_vec_value(&sk.procedure));
    m.insert("pitfalls".into(), str_vec_value(&sk.pitfalls));
    m.insert("verification".into(), str_vec_value(&sk.verification));
    m.insert("body_extra".into(), Value::String(sk.body_extra.clone()));
    m
}

/// `s.get(key, default)` over a loaded-skill dict (the `Map<String, Value>` rows
/// `SkillsManager::load` returns), as a `&str`-ish owned `String`.
fn skill_get(s: &Map<String, Value>, key: &str, default: &str) -> String {
    match s.get(key) {
        Some(Value::String(v)) => v.clone(),
        _ => default.to_string(),
    }
}

// ---------------------------------------------------------------------------
// MCP JSON-coercion helpers (mirror mcp_routes.rs value_array_to_string_vec /
// value_object_to_string_map + parse_json_array / parse_json_object).
// ---------------------------------------------------------------------------

/// Coerce a JSON value into the `Vec<String>` `connect_server` wants.
///
/// Mirrors Python's `cmd_args if isinstance(cmd_args, list) else json.loads(cmd_args)`:
/// a JSON array yields its string entries (non-strings dropped, like the live
/// route's `value_array_to_string_vec`); a JSON string is parsed as JSON and
/// re-coerced; anything else yields an empty list.
fn json_value_to_string_vec(v: &Value) -> Vec<String> {
    match v {
        Value::Array(a) => a
            .iter()
            .filter_map(|x| x.as_str().map(str::to_string))
            .collect(),
        // `json.loads(cmd_args)` when the column held a JSON string.
        Value::String(s) => match serde_json::from_str::<Value>(s) {
            Ok(parsed) => json_value_to_string_vec(&parsed),
            Err(_) => Vec::new(),
        },
        _ => Vec::new(),
    }
}

/// Coerce a JSON value into the `HashMap<String, String>` env map.
///
/// Mirrors Python's `env if isinstance(env, dict) else json.loads(env)`: a JSON
/// object yields its string-valued entries (non-strings dropped, like the live
/// route's `value_object_to_string_map`); a JSON string is parsed and
/// re-coerced; anything else yields an empty map.
fn json_value_to_string_map(v: &Value) -> HashMap<String, String> {
    match v {
        Value::Object(o) => o
            .iter()
            .filter_map(|(k, val)| val.as_str().map(|s| (k.clone(), s.to_string())))
            .collect(),
        Value::String(s) => match serde_json::from_str::<Value>(s) {
            Ok(parsed) => json_value_to_string_map(&parsed),
            Err(_) => HashMap::new(),
        },
        _ => HashMap::new(),
    }
}

/// `json.loads(col) if col else []` constrained to a JSON array (mirrors
/// `mcp_routes::parse_json_array`).
fn parse_json_col_array(col: Option<&str>) -> Value {
    match col {
        Some(s) if !s.is_empty() => serde_json::from_str::<Value>(s)
            .ok()
            .filter(|v| v.is_array())
            .unwrap_or_else(|| Value::Array(vec![])),
        _ => Value::Array(vec![]),
    }
}

/// `json.loads(col) if col else {}` constrained to a JSON object (mirrors
/// `mcp_routes::parse_json_object`).
fn parse_json_col_object(col: Option<&str>) -> Value {
    match col {
        Some(s) if !s.is_empty() => serde_json::from_str::<Value>(s)
            .ok()
            .filter(|v| v.is_object())
            .unwrap_or_else(|| Value::Object(Map::new())),
        _ => Value::Object(Map::new()),
    }
}

/// `{"results": <s>}` — the bare success shape manage_skills uses (no
/// `exit_code`, matching Python's `return {"results": ...}`).
fn results_result(msg: impl Into<String>) -> Map<String, Value> {
    let mut m = Map::new();
    m.insert("results".to_string(), Value::String(msg.into()));
    m
}

// ---------------------------------------------------------------------------
// do_manage_skills  (WIRED)
// ---------------------------------------------------------------------------

/// Handle manage_skills tool calls.
///
/// SKILL.md-backed CRUD with progressive disclosure (Hermes-style). Actions:
/// `list`/`index`/`` (Level 0 summary), `view` (Level 1 full SKILL.md),
/// `view_ref` (Level 2 sub-file), `add`, `edit` (full replace), `patch`
/// (surgical), `publish`, `delete`, `search`. Drives the ported
/// `SkillsManager` + `Skill`/`slugify` (tool_implementations.py:576-806).
pub async fn do_manage_skills(content: &str, owner: Option<&str>) -> Map<String, Value> {
    // Python parses args first and returns the Invalid-JSON error before
    // reaching the SkillsManager. Preserve that observable order.
    let args = match _parse_tool_args(content) {
        Ok(a) => a,
        Err(_) => {
            let mut m = Map::new();
            m.insert(
                "error".to_string(),
                Value::String("Invalid JSON arguments".to_string()),
            );
            m.insert("exit_code".to_string(), Value::from(1));
            return m;
        }
    };

    // action = (args.get("action") or "").lower()
    let action = str_field(&args, "action").to_lowercase();
    let sm = SkillsManager::new(&crate::src::constants::DATA_DIR);

    // Accept legacy `skill_id` as an alias for `name`.
    // name = (args.get("name") or args.get("skill_id") or "").strip()
    let name = first_nonempty_str(&args, &["name", "skill_id"])
        .trim()
        .to_string();

    if action == "list" || action == "index" || action.is_empty() {
        let all_skills = sm.load(owner);
        if all_skills.is_empty() {
            return results_result("No skills yet. Create one with action='add'.");
        }
        let mut published: Vec<&Map<String, Value>> = all_skills
            .iter()
            .filter(|s| skill_get(s, "status", "") == "published")
            .collect();
        let mut drafts: Vec<&Map<String, Value>> = all_skills
            .iter()
            .filter(|s| skill_get(s, "status", "") == "draft")
            .collect();
        let mut lines: Vec<String> = Vec::new();
        if !published.is_empty() {
            lines.push("## Published".to_string());
            // sorted(published, key=lambda x: x["name"])
            published.sort_by_key(|s| skill_get(s, "name", ""));
            for s in &published {
                lines.push(format!(
                    "- **{}** ({}): {}",
                    skill_get(s, "name", ""),
                    skill_get(s, "category", "general"),
                    skill_get(s, "description", "")
                ));
            }
        }
        if !drafts.is_empty() {
            lines.push("\n## Drafts".to_string());
            drafts.sort_by_key(|s| skill_get(s, "name", ""));
            for s in &drafts {
                lines.push(format!(
                    "- **{}** [draft]: {}",
                    skill_get(s, "name", ""),
                    skill_get(s, "description", "")
                ));
            }
        }
        // "\n".join(lines) if lines else "No skills yet."
        return results_result(if lines.is_empty() {
            "No skills yet.".to_string()
        } else {
            lines.join("\n")
        });
    }

    if action == "view" {
        if name.is_empty() {
            return err_result("name is required for view");
        }
        match sm.read_skill_md(&name, owner) {
            Some(md) => results_result(md),
            None => err_result(format!("Skill {name:?} not found")),
        }
    } else if action == "view_ref" {
        if name.is_empty() {
            return err_result("name is required for view_ref");
        }
        // ref = (args.get("path") or "").strip()
        let reference = str_field(&args, "path").trim().to_string();
        if reference.is_empty() {
            return err_result("path is required for view_ref");
        }
        match sm.read_skill_reference(&name, &reference, owner) {
            Some(text) => results_result(text),
            None => err_result(format!("Reference {reference:?} not found under {name:?}")),
        }
    } else if action == "add" {
        if name.is_empty() {
            return err_result(
                "name is required for add. Provide the exact slug the user should see, then report the returned name.",
            );
        }
        // proc = args.get("procedure"); if proc is None: proc = args.get("steps") or []
        let proc: Option<Vec<String>> = match args.get("procedure") {
            Some(_) => opt_str_list(&args, "procedure"),
            None => Some(opt_str_list(&args, "steps").unwrap_or_default()),
        };
        let proc_for_guard = proc.clone().unwrap_or_default();
        // if not proc and not args.get("body_extra") and not args.get("solution")
        let has_body_extra = !str_field(&args, "body_extra").is_empty();
        let has_solution = !str_field(&args, "solution").is_empty();
        if proc_for_guard.is_empty() && !has_body_extra && !has_solution {
            return err_result("procedure (or solution body) is required");
        }
        // description=(args.get("description") or args.get("title") or "").strip()
        let description = first_nonempty_str(&args, &["description", "title"])
            .trim()
            .to_string();
        // when_to_use = args.get("when_to_use") if not None else args.get("problem", "")
        let when_to_use = match args.get("when_to_use") {
            Some(_) => opt_str(&args, "when_to_use"),
            None => Some(str_field(&args, "problem").to_string()),
        };
        let entry = sm.add_skill(AddSkill {
            // name=args.get("name") (raw, may differ from the trimmed alias)
            name: opt_str(&args, "name"),
            description: Some(description),
            category: str_or(&args, "category", "general"),
            tags: opt_str_list(&args, "tags"),
            platforms: opt_str_list(&args, "platforms"),
            requires_toolsets: opt_str_list(&args, "requires_toolsets"),
            fallback_for_toolsets: opt_str_list(&args, "fallback_for_toolsets"),
            when_to_use,
            procedure: proc,
            pitfalls: opt_str_list(&args, "pitfalls"),
            verification: opt_str_list(&args, "verification"),
            status: str_or(&args, "status", "draft"),
            version: str_or(&args, "version", "1.0.0"),
            confidence: f64_or(&args, "confidence", 0.8),
            source: str_or(&args, "source", "learned"),
            teacher_model: opt_str(&args, "teacher_model"),
            owner: owner.map(str::to_string),
            title: str_field(&args, "title").to_string(),
            problem: str_field(&args, "problem").to_string(),
            solution: str_field(&args, "solution").to_string(),
            steps: opt_str_list(&args, "steps"),
            ..AddSkill::default()
        });
        // if entry.get("_deduped"): ...
        if matches!(entry.get("_deduped"), Some(Value::Bool(true))) {
            let dup_name = skill_get(&entry, "name", "");
            return results_result(format!(
                "A near-identical skill already exists: `{dup_name}` — not creating \
a duplicate. View or edit it with action='view', name='{dup_name}'."
            ));
        }
        // try: fire_event("skill_added", owner) except: debug
        crate::src::event_bus::fire_event("skill_added", owner);
        let entry_name = skill_get(&entry, "name", "");
        let mut verify_hint = String::new();
        if skill_get(&entry, "status", "") == "draft" {
            verify_hint = format!(
                "\n\nThis skill is a DRAFT. Run through the procedure once to verify, \
then publish with action='publish', name='{entry_name}'."
            );
        }
        results_result(format!(
            "Created skill `{entry_name}` — {}{verify_hint}",
            skill_get(&entry, "description", "")
        ))
    } else if action == "edit" {
        if name.is_empty() {
            return err_result("name is required for edit");
        }
        // new_content = args.get("content"); must be a non-empty (stripped) string.
        let new_content = match args.get("content") {
            Some(Value::String(s)) if !s.trim().is_empty() => s.clone(),
            _ => return err_result("content (full SKILL.md) is required for edit"),
        };
        // Skill.from_markdown never raises -> the `except` branch is unreachable.
        let mut sk_new = Skill::from_markdown(&new_content, None);
        // sk_new.name = slugify(sk_new.name or name)
        let slug_src = if sk_new.name.is_empty() {
            name.as_str()
        } else {
            sk_new.name.as_str()
        };
        sk_new.name = slugify(slug_src, "skill");
        let existing = sm.load(owner);
        let matched = existing.iter().find(|s| skill_get(s, "name", "") == name);
        let matched = match matched {
            Some(m) => m,
            None => return err_result(format!("Skill {name:?} not found")),
        };
        // if not sk_new.owner: sk_new.owner = match.get("owner") or owner
        if sk_new.owner.as_deref().unwrap_or("").is_empty() {
            let m_owner = skill_get(matched, "owner", "");
            sk_new.owner = if !m_owner.is_empty() {
                Some(m_owner)
            } else {
                owner.map(str::to_string)
            };
        }
        let ok = sm.update_skill(&name, &skill_dump(&sk_new), owner);
        if ok {
            results_result(format!("Edited skill `{}`.", sk_new.name))
        } else {
            err_result("Update failed")
        }
    } else if action == "patch" {
        if name.is_empty() {
            return err_result("name is required for patch");
        }
        // old = args.get("old_string"); must be a non-empty string.
        let old = match args.get("old_string") {
            Some(Value::String(s)) if !s.is_empty() => s.clone(),
            _ => return err_result("old_string is required and must be non-empty"),
        };
        // new_str = args.get("new_string", "")
        let new_str = match args.get("new_string") {
            Some(Value::String(s)) => s.clone(),
            _ => String::new(),
        };
        let md = match sm.read_skill_md(&name, owner) {
            Some(m) => m,
            None => return err_result(format!("Skill {name:?} not found")),
        };
        let count = md.matches(&old).count();
        if count == 0 {
            return err_result("old_string not found in SKILL.md");
        }
        if count > 1 {
            return err_result(format!(
                "old_string is ambiguous (appears {count} times). Make it more specific."
            ));
        }
        // md.replace(old, new_str, 1)
        let new_md = md.replacen(&old, &new_str, 1);
        let mut sk_new = Skill::from_markdown(&new_md, None);
        let slug_src = if sk_new.name.is_empty() {
            name.as_str()
        } else {
            sk_new.name.as_str()
        };
        sk_new.name = slugify(slug_src, "skill");
        let ok = sm.update_skill(&name, &skill_dump(&sk_new), owner);
        if ok {
            results_result(format!("Patched skill `{}`.", sk_new.name))
        } else {
            err_result("Patch update failed")
        }
    } else if action == "publish" {
        if name.is_empty() {
            return err_result("name is required for publish");
        }
        let all_skills = sm.load(owner);
        if !all_skills.iter().any(|s| skill_get(s, "name", "") == name) {
            return err_result(format!("Skill {name:?} not found"));
        }
        let mut updates = Map::new();
        updates.insert("status".to_string(), Value::String("published".to_string()));
        // if args.get("confidence") is not None:
        //     updates["confidence"] = max(0.0, min(1.0, float(args["confidence"])))
        if let Some(c) = args.get("confidence") {
            if !c.is_null() {
                if let Some(cf) = c.as_f64() {
                    let clamped = cf.clamp(0.0, 1.0);
                    updates.insert(
                        "confidence".to_string(),
                        serde_json::Number::from_f64(clamped)
                            .map(Value::Number)
                            .unwrap_or(Value::Null),
                    );
                }
            }
        }
        sm.update_skill(&name, &updates, owner);
        results_result(format!(
            "✅ Published `{name}`. It now appears in the skills index for future turns."
        ))
    } else if action == "delete" {
        if name.is_empty() {
            return err_result("name is required for delete");
        }
        if sm.delete_skill(&name, owner) {
            results_result(format!("Deleted skill `{name}`."))
        } else {
            err_result(format!("Skill {name:?} not found"))
        }
    } else if action == "search" {
        // query = (args.get("query") or "").strip()
        let query = str_field(&args, "query").trim().to_string();
        if query.is_empty() {
            return err_result("query is required for search");
        }
        // get_relevant_skills(query, sm.load(owner=owner), max_items=5)
        // -> Rust defaults: threshold=0.3, min_confidence=0.0 (per skills_routes.rs:1002).
        let results = sm.get_relevant_skills(&query, Some(sm.load(owner)), 0.3, 5, 0.0);
        if results.is_empty() {
            return results_result("No matching skills found.");
        }
        let mut lines: Vec<String> = Vec::new();
        for sk in &results {
            // proc = sk.get("procedure") or sk.get("steps") or []
            let proc = skill_str_list(sk, "procedure")
                .or_else(|| skill_str_list(sk, "steps"))
                .unwrap_or_default();
            // " → ".join(proc[:5])
            let steps_str = proc
                .iter()
                .take(5)
                .cloned()
                .collect::<Vec<_>>()
                .join(" → ");
            lines.push(format!(
                "**{}**: {}\n  When: {}\n  Steps: {}",
                skill_get(sk, "name", ""),
                skill_get(sk, "description", ""),
                skill_get(sk, "when_to_use", ""),
                steps_str
            ));
        }
        results_result(lines.join("\n\n"))
    } else {
        err_result(format!(
            "Unknown action: {action:?}. \
Use one of: list, view, view_ref, add, edit, patch, publish, delete, search."
        ))
    }
}

/// `sk.get(key) or []` over a loaded-skill dict, returning the string list when
/// the key holds a non-empty JSON array (else `None`, so the `or` chain
/// continues — Python truthiness: an empty list is falsy).
fn skill_str_list(s: &Map<String, Value>, key: &str) -> Option<Vec<String>> {
    match s.get(key) {
        Some(Value::Array(a)) if !a.is_empty() => Some(
            a.iter()
                .filter_map(|x| x.as_str().map(str::to_string))
                .collect(),
        ),
        _ => None,
    }
}

// ---------------------------------------------------------------------------
// do_manage_settings  (WIRED) — helpers
// ---------------------------------------------------------------------------

/// `_SECRET_KEYS` (tool_implementations.py:1476-1479) — credentials the agent
/// must never write through chat.
const SECRET_KEYS: [&str; 6] = [
    "brave_api_key",
    "google_pse_key",
    "google_pse_cx",
    "tavily_api_key",
    "serper_api_key",
    "app_public_url",
];

/// `_is_secret(k)` — in `_SECRET_KEYS`, ends with `token`, or contains any of
/// the secret substrings. `token` must be a SUFFIX (not a substring): otherwise
/// the int setting `agent_input_token_budget` (which even has a "token budget"
/// alias to set it from chat) and keys like `tokenizer` would be wrongly
/// classified as credentials and masked.
fn is_secret(k: &str) -> bool {
    if SECRET_KEYS.contains(&k) {
        return true;
    }
    if k.ends_with("token") {
        return true;
    }
    ["api_key", "_key", "secret", "password"]
        .iter()
        .any(|t| k.contains(t))
}

/// `_ALIASES_SET` (tool_implementations.py:1484-1505) — friendly text -> real key.
const ALIASES_SET: &[(&str, &str)] = &[
    ("voice", "tts_voice"),
    ("tts voice", "tts_voice"),
    ("tts", "tts_enabled"),
    ("text to speech", "tts_enabled"),
    ("tts provider", "tts_provider"),
    ("speech speed", "tts_speed"),
    ("voice speed", "tts_speed"),
    ("stt", "stt_enabled"),
    ("speech to text", "stt_enabled"),
    ("transcription", "stt_enabled"),
    ("search engine", "search_provider"),
    ("search provider", "search_provider"),
    ("search results", "search_result_count"),
    ("result count", "search_result_count"),
    ("default model", "default_model"),
    ("chat model", "default_model"),
    ("default endpoint", "default_endpoint_id"),
    ("task model", "task_model"),
    ("background model", "task_model"),
    ("teacher model", "teacher_model"),
    ("teacher", "teacher_enabled"),
    ("utility model", "utility_model"),
    ("research model", "research_model"),
    ("research max tokens", "research_max_tokens"),
    ("vision model", "vision_model"),
    ("vision", "vision_enabled"),
    ("image model", "image_model"),
    ("image quality", "image_quality"),
    ("image gen", "image_gen_enabled"),
    ("image generation", "image_gen_enabled"),
    ("reminder channel", "reminder_channel"),
    ("reminders", "reminder_channel"),
    ("ntfy topic", "reminder_ntfy_topic"),
    ("agent tool calls", "agent_max_tool_calls"),
    ("max tool calls", "agent_max_tool_calls"),
    ("agent timeout", "agent_stream_timeout_seconds"),
    ("stream timeout", "agent_stream_timeout_seconds"),
    ("token budget", "agent_input_token_budget"),
];

/// `_resolve(k)` — real key if present, else alias, else the stripped original.
fn resolve_setting_key(k: &str) -> String {
    let k2 = k.trim().to_lowercase();
    if DEFAULT_SETTINGS.contains_key(&k2) {
        return k2;
    }
    for (alias, real) in ALIASES_SET {
        if *alias == k2 {
            return (*real).to_string();
        }
    }
    k.trim().to_string()
}

/// `_ENUMS` (tool_implementations.py:1512-1515).
fn setting_enum(key: &str) -> Option<&'static [&'static str]> {
    match key {
        "image_quality" => Some(&["low", "medium", "high"]),
        "reminder_channel" => Some(&["browser", "email", "ntfy"]),
        _ => None,
    }
}

/// Python `str(value)` for a JSON value as used by the f-strings below
/// (`f"...{value}"`). Mirrors CPython's `str()`: bool -> `True`/`False`,
/// ints/floats via their repr, strings verbatim (no quotes), `None` -> `None`.
fn py_str(v: &Value) -> String {
    match v {
        Value::Null => "None".to_string(),
        Value::Bool(b) => {
            if *b {
                "True".to_string()
            } else {
                "False".to_string()
            }
        }
        Value::String(s) => s.clone(),
        Value::Number(n) => n.to_string(),
        // Lists/dicts: the `get` branch does NOT refuse structured settings (only
        // `set` does), so a list/dict value DOES reach this f-string. Python's
        // f-string applies `str()`, which for containers reprs each element
        // (single-quoted strings, True/False/None, etc.) — NOT serde_json. Mirror
        // Python's `str(list)`/`str(dict)` rather than JSON serialisation.
        Value::Array(_) | Value::Object(_) => py_repr(v),
    }
}

/// Python `repr()` of a JSON value, used inside container `str()` formatting.
/// `str([...])`/`str({...})` calls `repr` on each element; scalars at the top
/// level differ only in that `str("x")` is `x` while `repr("x")` is `'x'`.
fn py_repr(v: &Value) -> String {
    match v {
        Value::Null => "None".to_string(),
        Value::Bool(b) => {
            if *b {
                "True".to_string()
            } else {
                "False".to_string()
            }
        }
        Value::Number(n) => n.to_string(),
        // repr('x') -> 'x'; Python prefers single quotes, switching to double
        // quotes only when the string contains a single but no double quote.
        Value::String(s) => {
            if s.contains('\'') && !s.contains('"') {
                format!("\"{s}\"")
            } else {
                format!("'{}'", s.replace('\'', "\\'"))
            }
        }
        Value::Array(arr) => {
            let inner: Vec<String> = arr.iter().map(py_repr).collect();
            format!("[{}]", inner.join(", "))
        }
        Value::Object(map) => {
            let inner: Vec<String> = map
                .iter()
                .map(|(k, val)| format!("{}: {}", py_repr(&Value::String(k.clone())), py_repr(val)))
                .collect();
            format!("{{{}}}", inner.join(", "))
        }
    }
}

/// `_coerce(value, default)` — bool/int/str coercion keyed on the default's
/// Python type. `Err(())` mirrors Python's `int(value)` raising
/// `ValueError`/`TypeError` (the caller turns it into the "isn't a valid value"
/// error). The returned `Value` is the coerced setting value.
fn coerce_setting(value: &Value, default: &Value) -> Result<Value, ()> {
    match default {
        // isinstance(default, bool)
        Value::Bool(_) => {
            let b = match value {
                Value::Bool(b) => *b,
                // str(value).strip().lower() in (truthy strings)
                _ => {
                    let s = py_str(value).trim().to_lowercase();
                    matches!(
                        s.as_str(),
                        "true" | "on" | "yes" | "1" | "enable" | "enabled"
                    )
                }
            };
            Ok(Value::Bool(b))
        }
        // isinstance(default, int)  (bool already handled above; JSON ints only)
        Value::Number(n) if n.is_i64() || n.is_u64() => {
            // int(value): JSON number truncates toward zero; numeric string
            // parses; bool -> 0/1; otherwise raise.
            match value {
                Value::Number(num) => {
                    if let Some(i) = num.as_i64() {
                        Ok(Value::from(i))
                    } else if let Some(u) = num.as_u64() {
                        Ok(Value::from(u))
                    } else if let Some(f) = num.as_f64() {
                        // int(12.5) -> 12 (truncate toward zero)
                        Ok(Value::from(f.trunc() as i64))
                    } else {
                        Err(())
                    }
                }
                Value::String(s) => s.trim().parse::<i64>().map(Value::from).map_err(|_| ()),
                Value::Bool(b) => Ok(Value::from(if *b { 1 } else { 0 })),
                _ => Err(()),
            }
        }
        // else: return value (passthrough — covers str/float defaults)
        _ => Ok(value.clone()),
    }
}

/// `_model_slug(value)` — `re.sub(r"[^a-z0-9]+", "", value.lower())`.
fn model_slug(value: &str) -> String {
    value
        .to_lowercase()
        .chars()
        .filter(|c| c.is_ascii_lowercase() || c.is_ascii_digit())
        .collect()
}

/// `_endpoint_model_from_cache(model_query)` — resolve friendly model text to an
/// enabled endpoint id + real model id by scanning `model_endpoints.cached_models`
/// for enabled endpoints. Returns `(endpoint_id, model)` on the best match.
fn endpoint_model_from_cache(model_query: &str) -> Option<(String, String)> {
    let wanted = model_query.trim().to_string();
    let wanted_slug = model_slug(&wanted);
    // wanted_tokens = [_model_slug(t) for t in re.findall(r"[A-Za-z0-9]+", wanted)]
    let wanted_tokens: Vec<String> = {
        let mut toks: Vec<String> = Vec::new();
        let mut cur = String::new();
        for ch in wanted.chars() {
            if ch.is_ascii_alphanumeric() {
                cur.push(ch);
            } else if !cur.is_empty() {
                toks.push(std::mem::take(&mut cur));
            }
        }
        if !cur.is_empty() {
            toks.push(cur);
        }
        toks.iter()
            .map(|t| model_slug(t))
            .filter(|t| !t.is_empty())
            .collect()
    };
    if wanted_slug.is_empty() {
        return None;
    }
    let wanted_lower = wanted.to_lowercase();

    // SELECT id, cached_models FROM model_endpoints WHERE is_enabled = 1
    let rows: Vec<(String, Option<String>)> = match crate::core::database::session_local() {
        Ok(conn) => {
            let mut stmt = match conn
                .prepare("SELECT id, cached_models FROM model_endpoints WHERE is_enabled = 1")
            {
                Ok(s) => s,
                Err(_) => return None,
            };
            let mapped = stmt.query_map([], |r| {
                Ok((r.get::<_, String>(0)?, r.get::<_, Option<String>>(1)?))
            });
            match mapped {
                Ok(it) => it.filter_map(|x| x.ok()).collect(),
                Err(_) => return None,
            }
        }
        Err(_) => return None,
    };

    // best = (score, endpoint_id, model)
    let mut best: Option<(i32, String, String)> = None;
    for (ep_id, cached) in rows {
        // raw_models = json.loads(ep.cached_models or "[]") or []
        let raw_models: Vec<Value> = match &cached {
            Some(s) if !s.is_empty() => serde_json::from_str::<Value>(s)
                .ok()
                .and_then(|v| v.as_array().cloned())
                .unwrap_or_default(),
            _ => Vec::new(),
        };
        for mid_v in &raw_models {
            // mid = str(mid)
            let mid = py_str(mid_v);
            let mid_slug = model_slug(&mid);
            if mid_slug.is_empty() {
                continue;
            }
            let exact = mid.to_lowercase() == wanted_lower;
            let compact_match = mid_slug.contains(&wanted_slug) || wanted_slug.contains(&mid_slug);
            let token_match =
                !wanted_tokens.is_empty() && wanted_tokens.iter().all(|tok| mid_slug.contains(tok));
            if exact || compact_match || token_match {
                let score = if exact {
                    3
                } else if compact_match {
                    2
                } else {
                    1
                };
                if best.as_ref().map(|b| score > b.0).unwrap_or(true) {
                    best = Some((score, ep_id.clone(), mid.clone()));
                }
            }
        }
    }
    best.map(|(_, id, model)| (id, model))
}

/// `_mask(k, v)` — secret + truthy value -> placeholder, else the value itself.
fn mask_setting(k: &str, v: &Value) -> Value {
    // _is_secret(k) and v   (Python truthiness of v)
    let truthy = match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64().map(|f| f != 0.0).unwrap_or(true),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    };
    if is_secret(k) && truthy {
        Value::String("••••• (set in panel)".to_string())
    } else {
        v.clone()
    }
}

/// `_ALIASES` for the disabled_tools toggle (tool_implementations.py:1642-1659).
const TOOL_TOGGLE_ALIASES: &[(&str, &[&str])] = &[
    ("shell", &["bash"]),
    ("terminal", &["bash"]),
    ("search", &["web_search"]),
    ("web", &["web_search"]),
    ("browser", &["builtin_browser"]),
    (
        "documents",
        &[
            "create_document",
            "edit_document",
            "update_document",
            "suggest_document",
        ],
    ),
    (
        "doc",
        &[
            "create_document",
            "edit_document",
            "update_document",
            "suggest_document",
        ],
    ),
    ("memory", &["manage_memory"]),
    ("skills", &["manage_skills"]),
    ("images", &["generate_image"]),
    ("image", &["generate_image"]),
    ("tasks", &["manage_tasks"]),
    ("notes", &["manage_notes"]),
    ("calendar", &["manage_calendar"]),
    (
        "email",
        &[
            "mcp__email__list_emails",
            "mcp__email__read_email",
            "mcp__email__send_email",
        ],
    ),
    ("research", &["web_search"]),
];

/// `{"response": <s>, "exit_code": 0}` success shape (settings list/get/set/etc.).
fn response_ok(msg: impl Into<String>) -> Map<String, Value> {
    let mut m = Map::new();
    m.insert("response".to_string(), Value::String(msg.into()));
    m.insert("exit_code".to_string(), Value::from(0));
    m
}

// ---------------------------------------------------------------------------
// do_manage_settings  (WIRED)
// ---------------------------------------------------------------------------

/// Manage user settings and preferences.
///
/// Reads/writes the live app settings store
/// (`src::settings::{load_settings, save_settings, get_setting, DEFAULT_SETTINGS}`)
/// plus the `model_endpoints.cached_models` cache, so changing a model / voice /
/// search engine / reminder channel from chat actually takes effect. Faithful
/// port of tool_implementations.py:1457-1709 (the whole body is wrapped in the
/// Python try/except -> `{"error": str(e), "exit_code": 1}`).
///
/// `owner` is accepted (the Rust dispatcher passes it) but, like the Python tool,
/// is never consumed — settings are global here.
pub async fn do_manage_settings(content: &str, _owner: Option<&str>) -> Map<String, Value> {
    let args = match _parse_tool_args(content) {
        Ok(a) => a,
        Err(_) => {
            let mut m = Map::new();
            m.insert(
                "error".to_string(),
                Value::String("Invalid JSON arguments".to_string()),
            );
            m.insert("exit_code".to_string(), Value::from(1));
            return m;
        }
    };

    // action = args.get("action", "list")
    let action = action_or(&args, "list");

    if action == "list" {
        // s = load_settings()
        // shown = {k: _mask(k, v) for k, v in s.items()
        //          if k in DEFAULT_SETTINGS and not isinstance(v, dict)}
        let s = load_settings();
        let mut shown = Map::new();
        for (k, v) in &s {
            if DEFAULT_SETTINGS.contains_key(k) && !v.is_object() {
                shown.insert(k.clone(), mask_setting(k, v));
            }
        }
        let mut out = Map::new();
        out.insert(
            "response".to_string(),
            Value::String(format!(
                "{} settings (use get/set with a key)",
                shown.len()
            )),
        );
        out.insert("settings".to_string(), Value::Object(shown));
        out.insert("exit_code".to_string(), Value::from(0));
        out
    } else if action == "get" {
        // key = _resolve(args.get("key", ""))
        let key = resolve_setting_key(str_field(&args, "key"));
        if key.is_empty() {
            return err_result("key is required");
        }
        if !DEFAULT_SETTINGS.contains_key(&key) {
            return err_result(format!(
                "Unknown setting '{}'. Use action='list' to see them.",
                str_field(&args, "key")
            ));
        }
        // val = load_settings().get(key, DEFAULT_SETTINGS.get(key))
        let val = load_settings()
            .get(&key)
            .cloned()
            .unwrap_or_else(|| DEFAULT_SETTINGS.get(&key).cloned().unwrap_or(Value::Null));
        let masked = mask_setting(&key, &val);
        let mut out = Map::new();
        out.insert(
            "response".to_string(),
            Value::String(format!("{key} = {}", py_str(&masked))),
        );
        out.insert("value".to_string(), masked);
        out.insert("exit_code".to_string(), Value::from(0));
        out
    } else if action == "set" {
        // raw = args.get("key", ""); value = args.get("value")
        let raw = str_field(&args, "key").to_string();
        let mut value = args.get("value").cloned().unwrap_or(Value::Null);
        if raw.is_empty() {
            return err_result("key is required");
        }
        let key = resolve_setting_key(&raw);
        if !DEFAULT_SETTINGS.contains_key(&key) {
            return err_result(format!(
                "Unknown setting '{raw}'. Use action='list' to see available settings."
            ));
        }
        if is_secret(&key) {
            return response_ok(format!(
                "'{key}' is a credential/secret — for security I can't set it from chat. Open Settings and set it there."
            ));
        }
        let default = DEFAULT_SETTINGS.get(&key).cloned().unwrap_or(Value::Null);
        // isinstance(DEFAULT_SETTINGS[key], (dict, list)) -> structured, refuse.
        if default.is_object() || default.is_array() {
            return response_ok(format!(
                "'{key}' is a structured setting — edit it in its panel, not from chat. (You can reset it to default here.)"
            ));
        }
        // value = _coerce(value, DEFAULT_SETTINGS[key])  (ValueError/TypeError ->
        // the "isn't a valid value" error).
        value = match coerce_setting(&value, &default) {
            Ok(v) => v,
            Err(()) => {
                return err_result(format!(
                    "'{}' isn't a valid value for {key} (expected {}).",
                    py_str(&value),
                    py_type_name(&default)
                ));
            }
        };
        // if key in _ENUMS and str(value).lower() not in _ENUMS[key]
        if let Some(allowed) = setting_enum(&key) {
            let vl = py_str(&value).to_lowercase();
            if !allowed.contains(&vl.as_str()) {
                return err_result(format!(
                    "{key} must be one of: {}.",
                    allowed.join(", ")
                ));
            }
        }
        let mut s = load_settings();
        s.insert(key.clone(), value.clone());
        // *_model -> *_endpoint_id pairing via the cache.
        if matches!(
            key.as_str(),
            "default_model"
                | "research_model"
                | "utility_model"
                | "task_model"
                | "vision_model"
                | "image_model"
        ) {
            if let Some((endpoint_id, model)) = endpoint_model_from_cache(&py_str(&value)) {
                // prefix = key[:-6]
                let prefix = &key[..key.len() - 6];
                s.insert(
                    format!("{prefix}_endpoint_id"),
                    Value::String(endpoint_id),
                );
                s.insert(key.clone(), Value::String(model.clone()));
                value = Value::String(model);
            }
        }
        // save_settings(s) — Python's call never surfaces an error here; on a
        // write failure we fall into the same {"error": ..., "exit_code": 1}.
        if let Err(e) = save_settings(&s) {
            logger::error(&format!("manage_settings error: {e}"));
            return err_result(e.to_string());
        }
        // if key.endswith("_model") and s.get(f"{key[:-6]}_endpoint_id"):
        if key.ends_with("_model") {
            let prefix = &key[..key.len() - 6];
            if let Some(Value::String(ep)) = s.get(&format!("{prefix}_endpoint_id")) {
                if !ep.is_empty() {
                    return response_ok(format!(
                        "Set {key} = {} (endpoint {ep}).",
                        py_str(&value)
                    ));
                }
            }
        }
        response_ok(format!("Set {key} = {}.", py_str(&value)))
    } else if action == "delete" || action == "reset" {
        let key = resolve_setting_key(str_field(&args, "key"));
        if !DEFAULT_SETTINGS.contains_key(&key) {
            return err_result(format!("Unknown setting '{}'.", str_field(&args, "key")));
        }
        if is_secret(&key) {
            return response_ok(format!("'{key}' is a credential — reset it in the panel."));
        }
        let default = DEFAULT_SETTINGS.get(&key).cloned().unwrap_or(Value::Null);
        let mut s = load_settings();
        s.insert(key.clone(), default.clone());
        if let Err(e) = save_settings(&s) {
            logger::error(&format!("manage_settings error: {e}"));
            return err_result(e.to_string());
        }
        response_ok(format!(
            "Reset {key} to default ({}).",
            py_str(&default)
        ))
    } else if action == "disable_tool" || action == "enable_tool" || action == "list_tools" {
        if action == "list_tools" {
            // current = get_setting("disabled_tools", []) or []
            let current = setting_str_list(get_setting(
                "disabled_tools",
                Value::Array(vec![]),
            ));
            let joined = if current.is_empty() {
                "(none)".to_string()
            } else {
                current.join(", ")
            };
            let mut out = Map::new();
            out.insert(
                "response".to_string(),
                Value::String(format!(
                    "Currently disabled: {joined}.\n\
Common toggles: shell (bash), search (web_search), browser, documents, \
memory, skills, images, tasks, notes, calendar, email."
                )),
            );
            out.insert(
                "disabled".to_string(),
                Value::Array(current.iter().map(|s| Value::String(s.clone())).collect()),
            );
            out.insert("exit_code".to_string(), Value::from(0));
            return out;
        }

        // tool_name = (args.get("tool") or args.get("name") or "").strip().lower()
        let tool_name = first_nonempty_str(&args, &["tool", "name"])
            .trim()
            .to_lowercase();
        if tool_name.is_empty() {
            return err_result("tool name required (e.g. 'shell', 'search', 'bash')");
        }
        // targets = _ALIASES.get(tool_name, [tool_name])
        let targets: Vec<String> = TOOL_TOGGLE_ALIASES
            .iter()
            .find(|(a, _)| *a == tool_name)
            .map(|(_, v)| v.iter().map(|s| s.to_string()).collect())
            .unwrap_or_else(|| vec![tool_name.clone()]);

        let mut settings = load_settings();
        // current = list(settings.get("disabled_tools") or [])
        let mut current: Vec<String> = settings
            .get("disabled_tools")
            .map(|v| setting_str_list(v.clone()))
            .unwrap_or_default();
        let before: std::collections::HashSet<String> = current.iter().cloned().collect();
        if action == "disable_tool" {
            for t in &targets {
                if !current.contains(t) {
                    current.push(t.clone());
                }
            }
        } else {
            // enable_tool: [t for t in current if t not in targets]
            current.retain(|t| !targets.contains(t));
        }
        let after: std::collections::HashSet<String> = current.iter().cloned().collect();
        settings.insert(
            "disabled_tools".to_string(),
            Value::Array(current.iter().map(|s| Value::String(s.clone())).collect()),
        );
        if let Err(e) = save_settings(&settings) {
            logger::error(&format!("manage_settings error: {e}"));
            return err_result(e.to_string());
        }

        let verb = if action == "disable_tool" {
            "Disabled"
        } else {
            "Enabled"
        };
        // changed = sorted(after.symmetric_difference(before))
        let mut changed: Vec<String> = after.symmetric_difference(&before).cloned().collect();
        changed.sort();
        let now_disabled = if current.is_empty() {
            "(none)".to_string()
        } else {
            current.join(", ")
        };
        let mut out = Map::new();
        out.insert(
            "response".to_string(),
            Value::String(format!(
                "{verb} {tool_name} ({}). Now disabled: {now_disabled}.",
                targets.join(", ")
            )),
        );
        out.insert(
            "changed".to_string(),
            Value::Array(changed.into_iter().map(Value::String).collect()),
        );
        out.insert(
            "disabled".to_string(),
            Value::Array(current.iter().map(|s| Value::String(s.clone())).collect()),
        );
        out.insert("exit_code".to_string(), Value::from(0));
        out
    } else {
        err_result(format!("Unknown action: {action}"))
    }
}

/// `type(default).__name__` for the coerce error message (`int`/`bool`/`str`/
/// `float`). JSON has no separate int/float at the type layer, so an integer
/// `Value::Number` reports `int` (matching the Python defaults, which are ints).
fn py_type_name(default: &Value) -> &'static str {
    match default {
        Value::Bool(_) => "bool",
        Value::Number(n) if n.is_f64() && !n.is_i64() && !n.is_u64() => "float",
        Value::Number(_) => "int",
        _ => "str",
    }
}

/// `get_setting("disabled_tools", []) or []` / `settings.get("disabled_tools")`
/// coerced to a `Vec<String>` (a JSON array's string entries; anything else ->
/// empty, mirroring the Python `or []`).
fn setting_str_list(v: Value) -> Vec<String> {
    match v {
        Value::Array(a) => a
            .into_iter()
            .filter_map(|x| match x {
                Value::String(s) => Some(s),
                _ => None,
            })
            .collect(),
        _ => Vec::new(),
    }
}

// ---------------------------------------------------------------------------
// do_api_call
// ---------------------------------------------------------------------------

/// Execute an API call to a registered integration.
///
/// Mirrors Python `do_api_call` (tool_implementations.py:1716-1750). Resolves
/// the integration via `crate::src::integrations::load_integrations` and
/// dispatches the request via `crate::src::integrations::execute_api_call`
/// (both ported). Content is parsed as a JSON object first; on a JSON-decode
/// failure Python falls back to a line-based format
/// (`integration\nmethod path\nbody`) — there is no "Invalid JSON arguments"
/// early return here.
///
/// Note: the Python signature is `do_api_call(content)` (no `owner`). The Rust
/// dispatcher in `tool_execution` calls every `do_*` as `(content, owner)`, so
/// this port accepts `owner` and simply ignores it — matching Python's behavior
/// where `owner` is never consumed.
pub async fn do_api_call(content: &str, _owner: Option<&str>) -> Map<String, Value> {
    // Python:
    //   try: args = json.loads(content)
    //   except json.JSONDecodeError: <line-based fallback>
    let args: Map<String, Value> = match serde_json::from_str::<Value>(content) {
        Ok(Value::Object(m)) => m,
        // `json.loads` succeeded but the top level is not an object — Python would
        // bind `args` to that value and then `args.get(...)` would raise
        // AttributeError. There is no observable success in that case, so we treat
        // any non-object decode like the JSONDecodeError fallback (line-based),
        // which is the only path that yields a usable `args` dict.
        _ => {
            // lines = content.strip().split("\n")
            let stripped = content.trim();
            let lines: Vec<&str> = stripped.split('\n').collect();
            let mut a = Map::new();
            // args = {"integration": lines[0].strip() if lines else ""}
            let integration = lines.first().map(|s| s.trim()).unwrap_or("");
            a.insert(
                "integration".to_string(),
                Value::String(integration.to_string()),
            );
            if lines.len() > 1 {
                // parts = lines[1].strip().split(" ", 1)
                let line1 = lines[1].trim();
                let mut parts = line1.splitn(2, ' ');
                let method = parts.next().unwrap_or("");
                // args["method"] = parts[0] if parts else "GET"
                // (a `"".split(" ",1)` yields `[""]` in Python, so method=="" here;
                //  `execute_api_call` upper-cases it downstream, matching parity.)
                a.insert("method".to_string(), Value::String(method.to_string()));
                // args["path"] = parts[1] if len(parts) > 1 else "/"
                let path = parts.next().unwrap_or("/");
                a.insert("path".to_string(), Value::String(path.to_string()));
            }
            if lines.len() > 2 {
                // try: args["body"] = json.loads("\n".join(lines[2:]))
                // except json.JSONDecodeError: pass
                let body_raw = lines[2..].join("\n");
                if let Ok(v) = serde_json::from_str::<Value>(&body_raw) {
                    a.insert("body".to_string(), v);
                }
            }
            a
        }
    };

    // integration_name = args.get("integration", "")
    let integration_name = args
        .get("integration")
        .and_then(|v| v.as_str())
        .unwrap_or("");
    // integrations = load_integrations()
    let integrations = crate::src::integrations::load_integrations();
    // intg = next((i for i in integrations
    //              if i["id"] == integration_name
    //              or i["name"].lower() == integration_name.lower()), None)
    let name_lower = integration_name.to_lowercase();
    let intg = integrations.iter().find(|i| {
        let id = i.get("id").and_then(|v| v.as_str()).unwrap_or("");
        let iname = i.get("name").and_then(|v| v.as_str()).unwrap_or("");
        id == integration_name || iname.to_lowercase() == name_lower
    });
    let intg = match intg {
        Some(i) => i,
        None => {
            // available = ", ".join(i["name"] for i in integrations if i.get("enabled", True))
            let available: Vec<String> = integrations
                .iter()
                .filter(|i| {
                    // i.get("enabled", True) truthiness — default True when absent.
                    match i.get("enabled") {
                        None => true,
                        Some(Value::Bool(b)) => *b,
                        Some(Value::Null) => false,
                        Some(Value::Number(n)) => n.as_f64().map(|f| f != 0.0).unwrap_or(false),
                        Some(Value::String(s)) => !s.is_empty(),
                        Some(Value::Array(a)) => !a.is_empty(),
                        Some(Value::Object(o)) => !o.is_empty(),
                    }
                })
                .map(|i| {
                    i.get("name")
                        .and_then(|v| v.as_str())
                        .unwrap_or("")
                        .to_string()
                })
                .collect();
            let available = if available.is_empty() {
                "none configured".to_string()
            } else {
                available.join(", ")
            };
            return err_result(format!(
                "No integration matching '{integration_name}'. Available: {available}"
            ));
        }
    };

    // return await execute_api_call(
    //     intg["id"], args.get("method", "GET"), args.get("path", "/"),
    //     params=args.get("params"), body=args.get("body"),
    //     extra_headers=args.get("headers"))
    let intg_id = intg.get("id").and_then(|v| v.as_str()).unwrap_or("");
    let method = args.get("method").and_then(|v| v.as_str()).unwrap_or("GET");
    let path = args.get("path").and_then(|v| v.as_str()).unwrap_or("/");
    crate::src::integrations::execute_api_call(
        intg_id,
        method,
        path,
        args.get("params").and_then(|v| v.as_object()).cloned(),
        args.get("body").cloned(),
        args.get("headers").and_then(|v| v.as_object()).cloned(),
    )
    .await
}

// ---------------------------------------------------------------------------
// do_manage_mcp  (DB CRUD ported; manager-dependent actions DEFER)
// ---------------------------------------------------------------------------

/// Manage MCP servers: list, add, delete, enable, disable, reconnect, list_tools.
///
/// DB CRUD (`add`/`delete`/`enable`/`disable`) is ported faithfully against the
/// `mcp_servers` table. The manager-dependent actions (status enrichment on
/// `list`, the post-`add` `connect_server`, the `delete` disconnect,
/// `reconnect`, `list_tools`) are wired through the live `McpManager` reached
/// via `agent_tools::get_mcp_manager()` (an `Arc<McpManager>` populated at
/// startup). When no manager is available, the exact Python soft-fail shapes
/// are preserved.
pub async fn do_manage_mcp(content: &str, _owner: Option<&str>) -> Map<String, Value> {
    let args = match _parse_tool_args(content) {
        Ok(a) => a,
        Err(_) => {
            let mut m = Map::new();
            m.insert(
                "error".to_string(),
                Value::String("Invalid JSON arguments".to_string()),
            );
            m.insert("exit_code".to_string(), Value::from(1));
            return m;
        }
    };

    let action = action_or(&args, "list");

    match action.as_str() {
        "list" => mcp_list().await,

        "add" => mcp_add(&args).await,

        "delete" => mcp_delete(&args).await,

        "reconnect" => mcp_reconnect(&args).await,

        "enable" | "disable" => mcp_set_enabled(&args, action == "enable").await,

        "list_tools" => mcp_list_tools(),

        other => {
            let mut m = Map::new();
            m.insert(
                "error".to_string(),
                Value::String(format!("Unknown action: {other}")),
            );
            m.insert("exit_code".to_string(), Value::from(1));
            m
        }
    }
}

/// `list` action: enumerate the `mcp_servers` rows and enrich each with the
/// live connection status/tool-count from the manager.
///
/// Python (`tool_implementations.py:1080-1098`):
/// ```python
/// mcp = get_mcp_manager()
/// if not mcp:
///     return {"response": "No MCP manager available", "servers": [], "exit_code": 0}
/// servers = db.query(McpServer).all()
/// items = []
/// for s in servers:
///     st = mcp.get_server_status(s.id)
///     status = st.get("status", "disconnected")
///     tool_count = st.get("tool_count", 0)
///     items.append({"id": s.id, "name": s.name, "transport": s.transport,
///                   "is_enabled": s.is_enabled, "status": status,
///                   "tool_count": tool_count})
/// return {"response": f"{len(items)} MCP servers", "servers": items, "exit_code": 0}
/// ```
async fn mcp_list() -> Map<String, Value> {
    let mcp = match get_mcp_manager() {
        Some(m) => m,
        None => {
            // `{"response": "No MCP manager available", "servers": [], "exit_code": 0}`.
            let mut m = Map::new();
            m.insert(
                "response".to_string(),
                Value::String("No MCP manager available".to_string()),
            );
            m.insert("servers".to_string(), Value::Array(vec![]));
            m.insert("exit_code".to_string(), Value::from(0));
            return m;
        }
    };

    let conn = match crate::core::database::session_local() {
        Ok(c) => c,
        Err(e) => return err_result(e.to_string()),
    };

    // `db.query(McpServer).all()` — no explicit ordering.
    let rows = {
        let mut stmt = match conn
            .prepare("SELECT id, name, transport, is_enabled FROM mcp_servers")
        {
            Ok(s) => s,
            Err(e) => return err_result(e.to_string()),
        };
        let mapped = stmt.query_map([], |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, String>(1)?,
                row.get::<_, String>(2)?,
                row.get::<_, bool>(3)?,
            ))
        });
        match mapped {
            Ok(it) => {
                let mut out: Vec<(String, String, String, bool)> = Vec::new();
                for r in it {
                    match r {
                        Ok(t) => out.push(t),
                        Err(e) => return err_result(e.to_string()),
                    }
                }
                out
            }
            Err(e) => return err_result(e.to_string()),
        }
    };

    let mut items: Vec<Value> = Vec::new();
    for (id, name, transport, is_enabled) in &rows {
        // `st = mcp.get_server_status(s.id)`.
        let st = mcp.get_server_status(id);
        // `status = st.get("status", "disconnected")`.
        let status = st
            .get("status")
            .and_then(|v| v.as_str())
            .unwrap_or("disconnected")
            .to_string();
        // `tool_count = st.get("tool_count", 0)`.
        let tool_count = st
            .get("tool_count")
            .cloned()
            .unwrap_or_else(|| Value::from(0));
        let mut item = Map::new();
        item.insert("id".to_string(), Value::String(id.clone()));
        item.insert("name".to_string(), Value::String(name.clone()));
        item.insert("transport".to_string(), Value::String(transport.clone()));
        item.insert("is_enabled".to_string(), Value::Bool(*is_enabled));
        item.insert("status".to_string(), Value::String(status));
        item.insert("tool_count".to_string(), tool_count);
        items.push(Value::Object(item));
    }

    let mut m = Map::new();
    m.insert(
        "response".to_string(),
        Value::String(format!("{} MCP servers", items.len())),
    );
    m.insert("servers".to_string(), Value::Array(items));
    m.insert("exit_code".to_string(), Value::from(0));
    m
}

/// `add` action: insert a row into `mcp_servers`, then try to connect via the
/// live manager. On connect failure (or when no manager is available) we log a
/// warning and fall through with `tool_count = 0` — matching Python's
/// try/except branch.
async fn mcp_add(args: &Map<String, Value>) -> Map<String, Value> {
    let name = str_field(args, "name").to_string();
    let command = str_field(args, "command").to_string();
    // Python: cmd_args = args.get("args", []); env = args.get("env", {}).
    let cmd_args = args.get("args").cloned().unwrap_or(Value::Array(vec![]));
    let env = args
        .get("env")
        .cloned()
        .unwrap_or(Value::Object(Map::new()));

    if name.is_empty() || command.is_empty() {
        return err_result("name and command are required");
    }

    // sid = str(uuid4())[:8]
    let sid: String = uuid::Uuid::new_v4().to_string().chars().take(8).collect();

    // args / env are JSON-dumped if list/dict (they are by default), else stored
    // as-is. We store the JSON text for both. (json.dumps(list)/json.dumps(dict).)
    let args_text = match &cmd_args {
        Value::Array(_) => serde_json::to_string(&cmd_args).unwrap_or_else(|_| "[]".to_string()),
        Value::String(s) => s.clone(),
        other => serde_json::to_string(other).unwrap_or_else(|_| "[]".to_string()),
    };
    let env_text = match &env {
        Value::Object(_) => serde_json::to_string(&env).unwrap_or_else(|_| "{}".to_string()),
        Value::String(s) => s.clone(),
        other => serde_json::to_string(other).unwrap_or_else(|_| "{}".to_string()),
    };

    let now = crate::pydatetime::utcnow_naive_iso();

    let conn = match crate::core::database::session_local() {
        Ok(c) => c,
        Err(e) => return err_result(e.to_string()),
    };
    // transport="stdio", is_enabled=True, created_at/updated_at=utcnow().
    if let Err(e) = conn.execute(
        "INSERT INTO mcp_servers (id, name, transport, command, args, env, is_enabled, created_at, updated_at) \
         VALUES (?1, ?2, 'stdio', ?3, ?4, ?5, 1, ?6, ?6)",
        rusqlite::params![sid, name, command, args_text, env_text, now],
    ) {
        return err_result(e.to_string());
    }

    // Python then tries `mcp.connect_server(...)`; on success read the live
    // tool_count, otherwise log a warning and fall through with tool_count=0.
    //   mcp = get_mcp_manager(); tool_count = 0
    //   if mcp:
    //       try:
    //           await mcp.connect_server(sid, name, "stdio", command=command,
    //               args=cmd_args if list else json.loads(cmd_args),
    //               env=env if dict else json.loads(env))
    //           st = mcp.get_server_status(sid)
    //           tool_count = st.get("tool_count", 0)
    //       except Exception as e:
    //           logger.warning(f"MCP connect failed for {name}: {e}")
    let mut tool_count: Value = Value::from(0);
    if let Some(mcp) = get_mcp_manager() {
        // `cmd_args if isinstance(cmd_args, list) else json.loads(cmd_args)` — the
        // default value (and the common case) is a JSON list; a string is parsed.
        let args_vec = json_value_to_string_vec(&cmd_args);
        let env_map = json_value_to_string_map(&env);
        let connected = mcp
            .connect_server(&sid, &name, "stdio", Some(&command), args_vec, env_map, None)
            .await;
        if connected {
            let st = mcp.get_server_status(&sid);
            tool_count = st
                .get("tool_count")
                .cloned()
                .unwrap_or_else(|| Value::from(0));
        } else {
            // `connect_server` returns false but records the underlying error in
            // the connection map (`{"status":"error","error":<e>,...}`). Recover
            // that `e` so the warning matches Python's
            // `logger.warning(f"MCP connect failed for {name}: {e}")`
            // (tool_implementations.py:1134) instead of dropping the detail.
            let e = mcp
                .get_server_status(&sid)
                .get("error")
                .and_then(|v| v.as_str())
                .map(|s| s.to_string())
                .unwrap_or_default();
            logger::warning(&format!("MCP connect failed for {name}: {e}"));
        }
    }

    let tc_display = tool_count.as_i64().unwrap_or(0);
    let mut m = Map::new();
    m.insert(
        "response".to_string(),
        Value::String(format!("Added MCP server '{name}' ({tc_display} tools)")),
    );
    m.insert("exit_code".to_string(), Value::from(0));
    m
}

/// `delete` action: remove the `mcp_servers` row (the manager disconnect is a
/// best-effort no-op in Python; the DB delete is the load-bearing effect).
async fn mcp_delete(args: &Map<String, Value>) -> Map<String, Value> {
    let sid = str_field(args, "server_id").to_string();

    let conn = match crate::core::database::session_local() {
        Ok(c) => c,
        Err(e) => return err_result(e.to_string()),
    };

    // Look up the name first (for the response + not-found check).
    let name: Option<String> = conn
        .query_row(
            "SELECT name FROM mcp_servers WHERE id = ?1",
            [&sid],
            |row| row.get::<_, String>(0),
        )
        .ok();
    let name = match name {
        Some(n) => n,
        None => return err_result(format!("Server {sid} not found")),
    };

    // Python: best-effort `mcp.disconnect_server(sid)` in a try/except pass.
    // `disconnect_server` is infallible in the Rust port (it never returns an
    // error), so there is no try/except to mirror — just call it best-effort.
    if let Some(mcp) = get_mcp_manager() {
        mcp.disconnect_server(&sid).await;
    }

    if let Err(e) = conn.execute("DELETE FROM mcp_servers WHERE id = ?1", [&sid]) {
        return err_result(e.to_string());
    }

    let mut m = Map::new();
    m.insert(
        "response".to_string(),
        Value::String(format!("Deleted MCP server '{name}'")),
    );
    m.insert("exit_code".to_string(), Value::from(0));
    m
}

/// `reconnect` action: disconnect then reconnect a server from its persisted
/// `mcp_servers` row.
///
/// Python (`tool_implementations.py:1158-1177`):
/// ```python
/// sid = args.get("server_id", "")
/// mcp = get_mcp_manager()
/// if not mcp:
///     return {"error": "MCP manager not available", "exit_code": 1}
/// try:
///     await mcp.disconnect_server(sid)
///     srv = db2.query(McpServer).filter(McpServer.id == sid).first()
///     if srv:
///         await mcp.connect_server(sid)
///         st = mcp.get_server_status(sid)
///         return {"response": f"Reconnected '{srv.name}' ({st.get('tool_count', 0)} tools)", "exit_code": 0}
///     return {"error": f"Server {sid} not found", "exit_code": 1}
/// except Exception as e:
///     return {"error": str(e), "exit_code": 1}
/// ```
///
/// DEVIATION (deliberate, documented): the literal Python `mcp.connect_server(sid)`
/// passes only the server id and would raise a `TypeError` (the method requires
/// `name`/`transport`/...). The faithful *observable* intent — and the precedent
/// set by the live HTTP route `mcp_routes::reconnect_server` — is the DB-driven
/// reconnect: resolve `name`/`transport`/`command`/`args`/`env`/`url` from the
/// row and pass them to `connect_server`. We follow that DB-driven shape.
async fn mcp_reconnect(args: &Map<String, Value>) -> Map<String, Value> {
    let sid = str_field(args, "server_id").to_string();

    // `mcp = get_mcp_manager(); if not mcp -> {"error": "MCP manager not available", "exit_code": 1}`.
    let mcp = match get_mcp_manager() {
        Some(m) => m,
        None => {
            let mut m = Map::new();
            m.insert(
                "error".to_string(),
                Value::String("MCP manager not available".to_string()),
            );
            m.insert("exit_code".to_string(), Value::from(1));
            return m;
        }
    };

    // `await mcp.disconnect_server(sid)`.
    mcp.disconnect_server(&sid).await;

    let conn = match crate::core::database::session_local() {
        Ok(c) => c,
        Err(e) => return err_result(e.to_string()),
    };

    // `srv = db2.query(McpServer).filter(McpServer.id == sid).first()`.
    let row = conn
        .query_row(
            "SELECT name, transport, command, args, env, url FROM mcp_servers WHERE id = ?1",
            [&sid],
            |r| {
                Ok((
                    r.get::<_, String>(0)?,         // name
                    r.get::<_, String>(1)?,         // transport
                    r.get::<_, Option<String>>(2)?, // command
                    r.get::<_, Option<String>>(3)?, // args
                    r.get::<_, Option<String>>(4)?, // env
                    r.get::<_, Option<String>>(5)?, // url
                ))
            },
        )
        .ok();

    let (name, transport, command, args_col, env_col, url) = match row {
        Some(r) => r,
        None => {
            // `return {"error": f"Server {sid} not found", "exit_code": 1}`.
            let mut m = Map::new();
            m.insert(
                "error".to_string(),
                Value::String(format!("Server {sid} not found")),
            );
            m.insert("exit_code".to_string(), Value::from(1));
            return m;
        }
    };

    // DB-driven reconnect (mirrors `mcp_routes::reconnect_server`): resolve the
    // persisted args/env JSON columns into the Vec/HashMap `connect_server` wants.
    let args_vec = json_value_to_string_vec(&parse_json_col_array(args_col.as_deref()));
    let env_map = json_value_to_string_map(&parse_json_col_object(env_col.as_deref()));
    mcp.connect_server(
        &sid,
        &name,
        &transport,
        command.as_deref(),
        args_vec,
        env_map,
        url.as_deref(),
    )
    .await;

    // `st = mcp.get_server_status(sid); ... ({st.get('tool_count', 0)} tools)`.
    let st = mcp.get_server_status(&sid);
    let tc = st.get("tool_count").and_then(|v| v.as_i64()).unwrap_or(0);
    let mut m = Map::new();
    m.insert(
        "response".to_string(),
        Value::String(format!("Reconnected '{name}' ({tc} tools)")),
    );
    m.insert("exit_code".to_string(), Value::from(0));
    m
}

/// `list_tools` action: a flat list of every discovered MCP tool.
///
/// Python (`tool_implementations.py:1193-1200`):
/// ```python
/// mcp = get_mcp_manager()
/// if not mcp:
///     return {"response": "No MCP manager", "tools": [], "exit_code": 0}
/// tools = mcp.get_all_tools()
/// items = [{"name": t["name"], "server": t["server_name"],
///           "description": t.get("description", "")[:100]} for t in tools]
/// return {"response": f"{len(items)} MCP tools available", "tools": items, "exit_code": 0}
/// ```
fn mcp_list_tools() -> Map<String, Value> {
    let mcp = match get_mcp_manager() {
        Some(m) => m,
        None => {
            // `{"response": "No MCP manager", "tools": [], "exit_code": 0}`.
            let mut m = Map::new();
            m.insert(
                "response".to_string(),
                Value::String("No MCP manager".to_string()),
            );
            m.insert("tools".to_string(), Value::Array(vec![]));
            m.insert("exit_code".to_string(), Value::from(0));
            return m;
        }
    };

    // `tools = mcp.get_all_tools()` — no disabled-map filtering here (Python
    // passes no argument).
    let tools = mcp.get_all_tools(None);
    let items: Vec<Value> = tools
        .iter()
        .map(|t| {
            let name = t.get("name").and_then(|v| v.as_str()).unwrap_or("");
            let server = t.get("server_name").and_then(|v| v.as_str()).unwrap_or("");
            // `t.get("description", "")[:100]` — char slice (truncate to 100 chars).
            let desc_full = t.get("description").and_then(|v| v.as_str()).unwrap_or("");
            let description: String = desc_full.chars().take(100).collect();
            let mut item = Map::new();
            item.insert("name".to_string(), Value::String(name.to_string()));
            item.insert("server".to_string(), Value::String(server.to_string()));
            item.insert("description".to_string(), Value::String(description));
            Value::Object(item)
        })
        .collect();

    let mut m = Map::new();
    m.insert(
        "response".to_string(),
        Value::String(format!("{} MCP tools available", items.len())),
    );
    m.insert("tools".to_string(), Value::Array(items));
    m.insert("exit_code".to_string(), Value::from(0));
    m
}

/// `enable` / `disable` action: flip `is_enabled` on the row.
async fn mcp_set_enabled(args: &Map<String, Value>, enable: bool) -> Map<String, Value> {
    let sid = str_field(args, "server_id").to_string();

    let conn = match crate::core::database::session_local() {
        Ok(c) => c,
        Err(e) => return err_result(e.to_string()),
    };

    let name: Option<String> = conn
        .query_row(
            "SELECT name FROM mcp_servers WHERE id = ?1",
            [&sid],
            |row| row.get::<_, String>(0),
        )
        .ok();
    let name = match name {
        Some(n) => n,
        None => return err_result(format!("Server {sid} not found")),
    };

    // Python sets srv.is_enabled then commits (no updated_at touch on this path
    // — SQLAlchemy's onupdate is absent, and the source does not set it here, so
    // we leave updated_at untouched to match).
    if let Err(e) = conn.execute(
        "UPDATE mcp_servers SET is_enabled = ?1 WHERE id = ?2",
        rusqlite::params![enable as i64, sid],
    ) {
        return err_result(e.to_string());
    }

    let verb = if enable { "enabled" } else { "disabled" };
    let mut m = Map::new();
    m.insert(
        "response".to_string(),
        Value::String(format!("MCP server '{name}' {verb}")),
    );
    m.insert("exit_code".to_string(), Value::from(0));
    m
}

// ---------------------------------------------------------------------------
// do_manage_research  (PORT_NOW — filesystem only)
// ---------------------------------------------------------------------------

/// List, read/open, or delete saved deep-research results from the Library.
///
/// Args (JSON): `{"action": "list|read|delete", "id": "<id>", "search": "..."}`.
/// Research is stored as `data/deep_research/<id>.json` (query, summary, sources).
///
/// SECURITY: the research id is interpolated straight into a filesystem path for
/// read AND delete; the `^[A-Za-z0-9_-]+$` full-match guard is preserved EXACTLY
/// to prevent `../`-style traversal.
pub async fn do_manage_research(content: &str, _owner: Option<&str>) -> Map<String, Value> {
    // Python: args = _parse_tool_args(content) if content.strip().startswith("{") else {}
    //         except ValueError: args = {}
    //         if not isinstance(args, dict): args = {}
    let args: Map<String, Value> = if content.trim_start().starts_with('{') {
        _parse_tool_args(content).unwrap_or_default()
    } else {
        Map::new()
    };

    // action = (args.get("action") or "list").lower()
    let action = match args.get("action") {
        Some(Value::String(s)) if !s.is_empty() => s.to_lowercase(),
        _ => "list".to_string(),
    };

    // rid = (args.get("id") or args.get("session_id") or args.get("research_id") or "").strip()
    let rid = {
        let raw = first_nonempty_str(&args, &["id", "session_id", "research_id"]);
        raw.trim().to_string()
    };

    // Path-traversal guard: re.fullmatch(r"[A-Za-z0-9_-]+", rid).
    if !rid.is_empty() && !is_safe_research_id(&rid) {
        let mut m = Map::new();
        m.insert(
            "error".to_string(),
            Value::String("Invalid research id.".to_string()),
        );
        return m;
    }

    let data_dir = std::path::PathBuf::from("data/deep_research");

    // _load(p): json.loads(p.read_text()) or None on any error.
    let load = |p: &std::path::Path| -> Option<Value> {
        let text = std::fs::read_to_string(p).ok()?;
        serde_json::from_str::<Value>(&text).ok()
    };

    if matches!(action.as_str(), "read" | "open" | "view" | "get") {
        if rid.is_empty() {
            let mut m = Map::new();
            m.insert(
                "error".to_string(),
                Value::String("Provide the research id (from action='list').".to_string()),
            );
            return m;
        }
        let p = data_dir.join(format!("{rid}.json"));
        if !p.exists() {
            let mut m = Map::new();
            m.insert(
                "error".to_string(),
                Value::String(format!("Research '{rid}' not found.")),
            );
            return m;
        }
        let d = load(&p).unwrap_or(Value::Object(Map::new()));
        // summary = result or raw_report or summary or report or "(no report body)"
        let summary = json_first_str(
            &d,
            &["result", "raw_report", "summary", "report"],
            "(no report body)",
        );
        // srcs = d.get("sources", []) or []
        let srcs: Vec<Value> = match d.get("sources") {
            Some(Value::Array(a)) => a.clone(),
            _ => vec![],
        };
        let query = json_str_or(&d, "query", "(untitled)");
        let mut out = format!("# {query}\n\n{summary}");
        if !srcs.is_empty() {
            // "\n\nSources:\n" + lines for srcs[:30]
            let mut lines: Vec<String> = Vec::new();
            for s in srcs.iter().take(30) {
                // f"- {title or url}: {url}"
                let url = json_str_or(s, "url", "");
                let title = match s.get("title") {
                    Some(Value::String(t)) if !t.is_empty() => t.clone(),
                    _ => url.clone(),
                };
                lines.push(format!("- {title}: {url}"));
            }
            out.push_str("\n\nSources:\n");
            out.push_str(&lines.join("\n"));
        }
        // out[:16000] — char slice.
        let out_capped: String = out.chars().take(16000).collect();
        let mut m = Map::new();
        m.insert("output".to_string(), Value::String(out_capped));
        m.insert("exit_code".to_string(), Value::from(0));
        return m;
    }

    if action == "delete" {
        if rid.is_empty() {
            let mut m = Map::new();
            m.insert(
                "error".to_string(),
                Value::String(
                    "Provide the research id to delete (from action='list').".to_string(),
                ),
            );
            return m;
        }
        let p = data_dir.join(format!("{rid}.json"));
        if p.exists() {
            if let Err(e) = std::fs::remove_file(&p) {
                let mut m = Map::new();
                m.insert(
                    "error".to_string(),
                    Value::String(format!("Failed to delete: {e}")),
                );
                return m;
            }
            let mut m = Map::new();
            m.insert(
                "output".to_string(),
                Value::String(format!("Deleted research '{rid}'.")),
            );
            m.insert("exit_code".to_string(), Value::from(0));
            return m;
        }
        let mut m = Map::new();
        m.insert(
            "error".to_string(),
            Value::String(format!("Research '{rid}' not found.")),
        );
        return m;
    }

    // default: list
    // search = (args.get("search") or "").lower()
    let search = match args.get("search") {
        Some(Value::String(s)) => s.to_lowercase(),
        _ => String::new(),
    };

    // items: (completed_at, stem, query, sources_count)
    let mut items: Vec<(f64, String, String, usize)> = Vec::new();
    if data_dir.exists() {
        if let Ok(entries) = std::fs::read_dir(&data_dir) {
            for entry in entries.flatten() {
                let path = entry.path();
                // glob("*.json")
                if path.extension().and_then(|e| e.to_str()) != Some("json") {
                    continue;
                }
                let d = match load(&path) {
                    Some(v) => v,
                    None => continue,
                };
                let q = json_str_or(&d, "query", "");
                if !search.is_empty() && !q.to_lowercase().contains(&search) {
                    continue;
                }
                // completed_at: d.get("completed_at", 0) or 0 -> number or 0.
                let completed = match d.get("completed_at") {
                    Some(Value::Number(n)) => n.as_f64().unwrap_or(0.0),
                    _ => 0.0,
                };
                let stem = path
                    .file_stem()
                    .and_then(|s| s.to_str())
                    .unwrap_or("")
                    .to_string();
                let n_sources = match d.get("sources") {
                    Some(Value::Array(a)) => a.len(),
                    _ => 0,
                };
                items.push((completed, stem, q, n_sources));
            }
        }
    }

    // items.sort(reverse=True): Python compares the whole tuple. The first
    // element (completed_at: f64) is the primary key; ties fall through to
    // stem (str), then query (str), then count. Sort descending on the tuple.
    // f64 has no total Ord, so compare by (completed, stem, query, count) with a
    // partial_cmp fallback (research timestamps are finite, never NaN here).
    items.sort_by(|a, b| {
        b.0.partial_cmp(&a.0)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| b.1.cmp(&a.1))
            .then_with(|| b.2.cmp(&a.2))
            .then_with(|| b.3.cmp(&a.3))
    });

    if items.is_empty() {
        let suffix = if search.is_empty() {
            String::new()
        } else {
            format!(" (search: {search})")
        };
        let mut m = Map::new();
        m.insert(
            "output".to_string(),
            Value::String(format!("No research found in the library.{suffix}")),
        );
        m.insert("exit_code".to_string(), Value::from(0));
        return m;
    }

    // rows: items[:50], each `- [{q or '(untitled)'}](#research-{sid}) — {n} sources`
    let rows: Vec<String> = items
        .iter()
        .take(50)
        .map(|(_, sid, q, n)| {
            let label = if q.is_empty() { "(untitled)" } else { q.as_str() };
            format!("- [{label}](#research-{sid}) — {n} sources")
        })
        .collect();
    let plural = if items.len() != 1 { "s" } else { "" };
    let mut m = Map::new();
    m.insert(
        "output".to_string(),
        Value::String(format!(
            "Research library ({} item{}):\n{}",
            items.len(),
            plural,
            rows.join("\n")
        )),
    );
    m.insert("exit_code".to_string(), Value::from(0));
    m
}

/// `re.fullmatch(r"[A-Za-z0-9_-]+", rid)` — non-empty string of only ASCII
/// alphanumerics, underscore, and hyphen.
fn is_safe_research_id(rid: &str) -> bool {
    !rid.is_empty()
        && rid
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '-')
}

/// `args.get(k1) or args.get(k2) or ... or ""` over string fields.
fn first_nonempty_str(args: &Map<String, Value>, keys: &[&str]) -> String {
    for k in keys {
        if let Some(Value::String(s)) = args.get(*k) {
            if !s.is_empty() {
                return s.clone();
            }
        }
    }
    String::new()
}

/// `d.get(key, default)` as a string (only `Value::String` counts; matches
/// Python's `.get(..., "<default>")` where the JSON value is a string).
fn json_str_or(d: &Value, key: &str, default: &str) -> String {
    match d.get(key) {
        Some(Value::String(s)) => s.clone(),
        _ => default.to_string(),
    }
}

/// `d.get(a) or d.get(b) or ... or default` over string fields (Python's `or`
/// chain: a value counts only if it is a non-empty string).
fn json_first_str(d: &Value, keys: &[&str], default: &str) -> String {
    for k in keys {
        if let Some(Value::String(s)) = d.get(*k) {
            if !s.is_empty() {
                return s.clone();
            }
        }
    }
    default.to_string()
}

// ---------------------------------------------------------------------------
// do_trigger_research
// ---------------------------------------------------------------------------

/// Start a live deep-research job that appears in the Deep Research sidebar.
///
/// Mirrors Python `do_trigger_research` (tool_implementations.py:3685-3731).
/// POSTs to `{_COOKBOOK_BASE}/api/research/start` — the same path the sidebar's
/// "Research" button uses — so the session is discoverable + streamable there.
/// That route is now served IN-PROCESS by `routes::research_routes`
/// (`setup_research_routes`, research_routes.rs), reached over loopback with the
/// internal-tool headers (the same self-HTTP class already wired for the cookbook
/// tools). The route returns `{"session_id": ...}`.
///
/// `owner` is threaded into `_internal_headers` so the backend impersonates the
/// user (matching Python's `headers=_internal_headers(owner)`).
pub async fn do_trigger_research(content: &str, owner: Option<&str>) -> Map<String, Value> {
    let args = match _parse_tool_args(content) {
        Ok(a) => a,
        Err(_) => {
            let mut m = Map::new();
            m.insert(
                "error".to_string(),
                Value::String("Invalid JSON arguments".to_string()),
            );
            m.insert("exit_code".to_string(), Value::from(1));
            return m;
        }
    };
    // topic = args.get("topic", "") or args.get("query", "")
    let topic = first_nonempty_str(&args, &["topic", "query"]);
    if topic.is_empty() {
        return err_result("topic (or query) is required");
    }

    // payload: Dict[str, Any] = {"query": topic}
    let mut payload = Map::new();
    payload.insert("query".to_string(), Value::String(topic.clone()));
    // Optional knobs the research panel supports.
    // if args.get("max_rounds") is not None:
    //     try: payload["max_rounds"] = int(args["max_rounds"])
    //     except (ValueError, TypeError): pass
    for key in ["max_rounds", "max_time"] {
        if let Some(v) = args.get(key) {
            if !v.is_null() {
                if let Some(n) = value_to_i64(v) {
                    payload.insert(key.to_string(), Value::from(n));
                }
            }
        }
    }
    // if args.get("category"): payload["category"] = args["category"]
    // if args.get("search_provider"): payload["search_provider"] = args["search_provider"]
    for key in ["category", "search_provider"] {
        if let Some(Value::String(s)) = args.get(key) {
            if !s.is_empty() {
                payload.insert(key.to_string(), Value::String(s.clone()));
            }
        }
    }

    // async with httpx.AsyncClient(timeout=30) as client:
    let cl = match reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(30))
        .build()
    {
        Ok(c) => c,
        Err(e) => return err_result(e.to_string()),
    };
    // resp = await client.post(f"{_COOKBOOK_BASE}/api/research/start",
    //                          json=payload, headers=_internal_headers(owner))
    let url = format!("{}/api/research/start", super::_COOKBOOK_BASE);
    let mut rb = cl.post(&url).json(&Value::Object(payload));
    for (k, v) in super::_internal_headers(owner) {
        rb = rb.header(k, v);
    }
    let resp = match rb.send().await {
        Ok(r) => r,
        // broad `except Exception as e: return {"error": str(e), "exit_code": 1}`
        Err(e) => return err_result(e.to_string()),
    };
    // if resp.status_code >= 400:
    //     return {"error": f"research/start returned HTTP {code}: {text[:200]}", ...}
    let status = resp.status().as_u16();
    if status >= 400 {
        let text = resp.text().await.unwrap_or_default();
        let snippet: String = text.chars().take(200).collect();
        return err_result(format!(
            "research/start returned HTTP {status}: {snippet}"
        ));
    }
    // data = resp.json(); sid = data.get("session_id", "?")
    let data: Value = resp.json().await.unwrap_or(Value::Null);
    let sid = data
        .get("session_id")
        .and_then(|v| v.as_str())
        .unwrap_or("?");

    // return { "output", "session_id", "anchor", "ui_event", "research_session_id",
    //          "exit_code" } in Python key order.
    let mut out = Map::new();
    out.insert(
        "output".to_string(),
        Value::String(format!(
            "Deep research started: [{topic}](#research-{sid}). \
Click to open the Deep Research sidebar and watch progress / read the report."
        )),
    );
    out.insert("session_id".to_string(), Value::String(sid.to_string()));
    out.insert(
        "anchor".to_string(),
        Value::String(format!("[{topic}](#research-{sid})")),
    );
    out.insert(
        "ui_event".to_string(),
        Value::String("research_started".to_string()),
    );
    out.insert(
        "research_session_id".to_string(),
        Value::String(sid.to_string()),
    );
    out.insert("exit_code".to_string(), Value::from(0));
    out
}

/// `int(x)` parity for the research knobs: Python's `int(args[key])` accepts
/// JSON numbers (truncating floats toward zero) and numeric strings, and raises
/// `ValueError`/`TypeError` otherwise (which the caller swallows). Returns `None`
/// for any value Python's `int(...)` would reject.
fn value_to_i64(v: &Value) -> Option<i64> {
    match v {
        // int(<int>) and int(<float>) -> truncate toward zero.
        Value::Number(n) => {
            if let Some(i) = n.as_i64() {
                Some(i)
            } else {
                n.as_f64().map(|f| f.trunc() as i64)
            }
        }
        // int("123") parses; int("12.5") raises ValueError -> None.
        Value::String(s) => s.trim().parse::<i64>().ok(),
        // int(True)==1, int(False)==0 (bool is a subclass of int in Python).
        Value::Bool(b) => Some(if *b { 1 } else { 0 }),
        _ => None,
    }
}

/// `c.get("emails") or []` over a contact JSON dict — the string entries of the
/// `emails` array (anything else -> empty, mirroring the Python `or []`).
fn contact_emails(c: &Value) -> Vec<String> {
    match c.get("emails") {
        Some(Value::Array(a)) => a
            .iter()
            .filter_map(|x| x.as_str().map(str::to_string))
            .collect(),
        _ => Vec::new(),
    }
}

/// `(c.get("name") or "")` over a contact JSON dict.
fn contact_name(c: &Value) -> String {
    match c.get("name") {
        Some(Value::String(s)) => s.clone(),
        _ => String::new(),
    }
}

// ---------------------------------------------------------------------------
// do_resolve_contact  (WIRED)
// ---------------------------------------------------------------------------

/// Look up a contact by name. Searches CardDAV (the ported
/// `contacts_routes::fetch_contacts_json_async`) then the in-process email-history
/// route `/api/email/resolve-contact`. Faithful port of
/// tool_implementations.py:3736-3787; both lookup steps swallow their errors
/// (`except Exception: pass`).
///
/// `owner` is accepted (the Rust dispatcher passes it) but, like the Python tool,
/// is never consumed: the email step is a PLAIN loopback GET with NO internal
/// headers (Python sends none), so under single-user / auth-unconfigured mode the
/// in-process route resolves with an empty owner exactly like Python.
pub async fn do_resolve_contact(content: &str, _owner: Option<&str>) -> Map<String, Value> {
    let args = match _parse_tool_args(content) {
        Ok(a) => a,
        Err(_) => {
            let mut m = Map::new();
            m.insert(
                "error".to_string(),
                Value::String("Invalid JSON arguments".to_string()),
            );
            m.insert("exit_code".to_string(), Value::from(1));
            return m;
        }
    };
    // Python: name = args.get("name", ""); if empty -> error. Preserve.
    let name = str_field(&args, "name").to_string();
    if name.is_empty() {
        return err_result("name is required");
    }

    // contacts: email -> {name, source}. Insertion-ordered (serde Map +
    // preserve_order) so the output order matches Python dict order.
    let mut contacts: Map<String, Value> = Map::new();

    // 1. CardDAV — structured contacts. `try: ... except Exception: pass`.
    // `fetch_contacts_json_async(False)` mirrors `asyncio.to_thread(cc._fetch_contacts)`.
    {
        let all_contacts = contacts_routes::fetch_contacts_json_async(false).await;
        let q = name.to_lowercase();
        for c in &all_contacts {
            let hay_name = contact_name(c).to_lowercase();
            // match = q in name OR q in any email
            let emails = contact_emails(c);
            let is_match = hay_name.contains(&q)
                || emails.iter().any(|e| e.to_lowercase().contains(&q));
            if !is_match {
                continue;
            }
            for email in &emails {
                let email = email.trim().to_lowercase();
                if !email.is_empty() && email.contains('@') {
                    let display = {
                        let n = contact_name(c);
                        if n.is_empty() {
                            email.clone()
                        } else {
                            n
                        }
                    };
                    let mut entry = Map::new();
                    entry.insert("name".to_string(), Value::String(display));
                    entry.insert("source".to_string(), Value::String("contacts".to_string()));
                    contacts.insert(email, Value::Object(entry));
                }
            }
        }
    }

    // 2. Email history — self GET to the in-process route. `try: ... except: pass`.
    // PLAIN GET (no internal headers — Python sends none); timeout 30.
    if let Ok(cl) = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(30))
        .build()
    {
        let url = format!("{}/api/email/resolve-contact", super::_COOKBOOK_BASE);
        if let Ok(resp) = cl.get(&url).query(&[("name", name.as_str())]).send().await {
            if resp.status().as_u16() == 200 {
                if let Ok(body) = resp.json::<Value>().await {
                    if let Some(Value::Array(list)) = body.get("contacts") {
                        for c in list {
                            let email = match c.get("email") {
                                Some(Value::String(s)) => s.trim().to_lowercase(),
                                _ => String::new(),
                            };
                            if !email.is_empty() && !contacts.contains_key(&email) {
                                let display = {
                                    let n = contact_name(c);
                                    if n.is_empty() {
                                        email.clone()
                                    } else {
                                        n
                                    }
                                };
                                let mut entry = Map::new();
                                entry.insert("name".to_string(), Value::String(display));
                                entry.insert(
                                    "source".to_string(),
                                    Value::String("email history".to_string()),
                                );
                                contacts.insert(email, Value::Object(entry));
                            }
                        }
                    }
                }
            }
        }
    }

    if contacts.is_empty() {
        let mut m = Map::new();
        m.insert(
            "output".to_string(),
            Value::String(format!("No contacts found matching '{name}'.")),
        );
        m.insert("exit_code".to_string(), Value::from(0));
        return m;
    }

    let mut lines: Vec<String> = vec![format!("Contacts matching '{name}':")];
    for (email, info) in &contacts {
        let iname = match info.get("name") {
            Some(Value::String(s)) => s.clone(),
            _ => String::new(),
        };
        let source = match info.get("source") {
            Some(Value::String(s)) => s.clone(),
            _ => String::new(),
        };
        lines.push(format!("- {iname} <{email}> ({source})"));
    }
    let mut m = Map::new();
    m.insert("output".to_string(), Value::String(lines.join("\n")));
    m.insert("exit_code".to_string(), Value::from(0));
    m
}

/// `{"output": <s>, "exit_code": <code>}` — the manage_contact success/result
/// shape (an `exit_code` that is 0 on success, 1 on a failed op).
fn output_result(msg: impl Into<String>, exit_code: i64) -> Map<String, Value> {
    let mut m = Map::new();
    m.insert("output".to_string(), Value::String(msg.into()));
    m.insert("exit_code".to_string(), Value::from(exit_code));
    m
}

// ---------------------------------------------------------------------------
// do_manage_contact  (WIRED)
// ---------------------------------------------------------------------------

/// Add / update / delete / list CardDAV contacts. Calls the ported contacts
/// helpers IN-PROCESS via `contacts_routes::{fetch_contacts_json_async,
/// create_contact_pub, update_contact_pub, delete_contact_pub}` (which run the
/// sync CardDAV helpers off the event loop, the `asyncio.to_thread` analogue).
/// Faithful port of tool_implementations.py:3790-3858.
pub async fn do_manage_contact(content: &str, _owner: Option<&str>) -> Map<String, Value> {
    let args = match _parse_tool_args(content) {
        Ok(a) => a,
        Err(_) => {
            let mut m = Map::new();
            m.insert(
                "error".to_string(),
                Value::String("Invalid JSON arguments".to_string()),
            );
            m.insert("exit_code".to_string(), Value::from(1));
            return m;
        }
    };
    // action = (args.get("action") or "").strip().lower()
    let action = str_field(&args, "action").trim().to_lowercase();

    // The whole dispatch is wrapped in the Python `try/except Exception as e ->
    // {"error": f"Contact operation failed: {e}", "exit_code": 1}`. The ported
    // wrappers return `bool` and never raise, and the module-unavailable branch
    // (`Contacts module unavailable`) is unreachable-by-construction in Rust
    // (the module is compiled in), so there is no fallible step left to catch.

    if action == "list" {
        // rows = await asyncio.to_thread(cc._fetch_contacts, True)
        let rows = contacts_routes::fetch_contacts_json_async(true).await;
        if rows.is_empty() {
            return output_result("No contacts.", 0);
        }
        let mut lines: Vec<String> = vec![format!("{} contacts:", rows.len())];
        for c in &rows {
            // em = ", ".join(c.get("emails") or [])
            let em = contact_emails(c).join(", ");
            // c.get("name") or "(no name)"
            let nm = {
                let n = contact_name(c);
                if n.is_empty() {
                    "(no name)".to_string()
                } else {
                    n
                }
            };
            // c.get("uid", "")
            let uid = match c.get("uid") {
                Some(Value::String(s)) => s.clone(),
                _ => String::new(),
            };
            lines.push(format!("- {nm} <{em}>  [uid={uid}]"));
        }
        output_result(lines.join("\n"), 0)
    } else if action == "add" {
        // email = (args.get("email") or "").strip()
        let email = str_field(&args, "email").trim().to_string();
        if email.is_empty() {
            return err_result("email is required for add");
        }
        // name = (args.get("name") or "").strip() or email.split("@")[0]
        let name = {
            let n = str_field(&args, "name").trim().to_string();
            if n.is_empty() {
                email.split('@').next().unwrap_or("").to_string()
            } else {
                n
            }
        };
        // Dedupe by email against a (non-forced) fetch.
        let existing = contacts_routes::fetch_contacts_json_async(false).await;
        for c in &existing {
            let lowered: Vec<String> = contact_emails(c)
                .iter()
                .map(|e| e.to_lowercase())
                .collect();
            if lowered.contains(&email.to_lowercase()) {
                return output_result(
                    format!("{email} is already a contact ({}).", contact_name(c)),
                    0,
                );
            }
        }
        let ok = contacts_routes::create_contact_pub(name.clone(), email.clone()).await;
        output_result(
            format!(
                "{} {name} <{email}>.",
                if ok { "Added" } else { "Failed to add" }
            ),
            if ok { 0 } else { 1 },
        )
    } else if action == "update" || action == "edit" {
        // uid = (args.get("uid") or "").strip()
        let uid = str_field(&args, "uid").trim().to_string();
        if uid.is_empty() {
            return err_result("uid is required for update (use action=list to find it)");
        }
        let mut name = str_field(&args, "name").trim().to_string();
        // emails = args.get("emails"); if None and args.get("email"): emails = [email]
        let raw_emails: Vec<String> = match args.get("emails") {
            Some(Value::Array(a)) => a
                .iter()
                .filter_map(|x| x.as_str().map(str::to_string))
                .collect(),
            // emails is None -> fall back to a single `email`, if truthy.
            _ => match args.get("email") {
                Some(Value::String(s)) if !s.is_empty() => vec![s.clone()],
                _ => Vec::new(),
            },
        };
        // emails = [e.strip() for e in (emails or []) if e and e.strip()]
        let emails: Vec<String> = raw_emails
            .into_iter()
            .filter(|e| !e.is_empty() && !e.trim().is_empty())
            .map(|e| e.trim().to_string())
            .collect();
        // phones = [p.strip() for p in (args.get("phones") or []) if p and p.strip()]
        let phones: Vec<String> = match args.get("phones") {
            Some(Value::Array(a)) => a
                .iter()
                .filter_map(|x| x.as_str())
                .filter(|p| !p.is_empty() && !p.trim().is_empty())
                .map(|p| p.trim().to_string())
                .collect(),
            _ => Vec::new(),
        };
        if name.is_empty() && emails.is_empty() {
            return err_result("Provide a name or emails to update");
        }
        // if not name and emails: name = emails[0].split("@")[0]
        if name.is_empty() && !emails.is_empty() {
            name = emails[0].split('@').next().unwrap_or("").to_string();
        }
        let ok = contacts_routes::update_contact_pub(uid, name, emails, phones).await;
        output_result(
            if ok {
                "Contact updated."
            } else {
                "Update failed."
            },
            if ok { 0 } else { 1 },
        )
    } else if action == "delete" {
        let uid = str_field(&args, "uid").trim().to_string();
        if uid.is_empty() {
            return err_result("uid is required for delete (use action=list to find it)");
        }
        let ok = contacts_routes::delete_contact_pub(uid).await;
        output_result(
            if ok {
                "Contact deleted."
            } else {
                "Delete failed."
            },
            if ok { 0 } else { 1 },
        )
    } else {
        err_result(format!(
            "Unknown action '{action}'. Use list, add, update, or delete."
        ))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// `_is_secret` parity (tool_implementations.py:1539-1547): the `_SECRET_KEYS`
    /// set, an `endswith("token")` suffix test, and a substring test over
    /// (`api_key`, `_key`, `secret`, `password`).
    #[test]
    fn is_secret_matches_python() {
        // _SECRET_KEYS members.
        for k in SECRET_KEYS {
            assert!(is_secret(k), "{k} should be secret (in _SECRET_KEYS)");
        }

        // endswith("token") — real token credentials.
        assert!(is_secret("token"));
        assert!(is_secret("access_token"));
        assert!(is_secret("refresh_token"));
        assert!(is_secret("github_token"));

        // substring tokens.
        assert!(is_secret("openai_api_key"));
        assert!(is_secret("some_key")); // contains "_key"
        assert!(is_secret("client_secret"));
        assert!(is_secret("db_password"));

        // The whole point of the suffix change: "token" mid-key must NOT be
        // masked. `tokenizer` and `agent_input_token_budget` (which even has a
        // "token budget" set-alias) are NOT credentials.
        assert!(
            !is_secret("tokenizer"),
            "tokenizer contains 'token' but doesn't end with it — not a secret"
        );
        assert!(
            !is_secret("agent_input_token_budget"),
            "token budget setting must remain settable from chat"
        );
        assert!(!is_secret("token_count"));

        // Plain non-secret settings stay visible.
        assert!(!is_secret("default_model"));
        assert!(!is_secret("tts_voice"));
        assert!(!is_secret("search_provider"));
        // "key" without the "_key" / "api_key" boundary is not flagged.
        assert!(!is_secret("keymap"));
    }

    /// `_mask` honours the corrected `is_secret`: a truthy `agent_input_token_budget`
    /// is shown verbatim, while a truthy real token is masked.
    #[test]
    fn mask_setting_respects_token_suffix() {
        assert_eq!(
            mask_setting("agent_input_token_budget", &Value::from(120000)),
            Value::from(120000)
        );
        assert_eq!(
            mask_setting("tokenizer", &Value::String("gpt2".into())),
            Value::String("gpt2".into())
        );
        assert_eq!(
            mask_setting("access_token", &Value::String("abc".into())),
            Value::String("••••• (set in panel)".into())
        );
        // Falsy secret is left unmasked (Python `_is_secret(k) and v`).
        assert_eq!(
            mask_setting("access_token", &Value::String(String::new())),
            Value::String(String::new())
        );
    }
}
