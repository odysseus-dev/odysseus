// services/memory/skill_format.rs  <- services/memory/skill_format.py
//! SKILL.md parser & writer.
//!
//! Reads/writes a single skill from a `SKILL.md` file with YAML frontmatter
//! and a structured markdown body. Inspired by Hermes' skills format
//! (<https://hermes-agent.nousresearch.com/docs/user-guide/features/skills>).
//!
//! Frontmatter shape (YAML):
//!
//! ```text
//!     ---
//!     name: open-pr-from-branch
//!     description: One-line summary surfaced in the skills index.
//!     version: 1.0.0
//!     category: dev
//!     tags: [git, github]
//!     platforms: [linux, macos]            # optional
//!     requires_toolsets: []                # optional
//!     fallback_for_toolsets: []            # optional
//!     status: published                    # draft | published
//!     confidence: 0.8                      # 0..1
//!     source: learned                      # learned | taught | imported
//!     teacher_model: claude-opus-4-7       # optional
//!     created: 2026-05-09T21:43:00Z
//!     ---
//! ```
//!
//! Body sections (any subset; rendered as headings):
//!
//! ```text
//!     ## When to Use
//!     Trigger conditions in plain English.
//!
//!     ## Procedure
//!     1. First step
//!     2. Second step
//!
//!     ## Pitfalls
//!     - Common failure mode + how to recover
//!
//!     ## Verification
//!     - How to confirm success
//! ```
//!
//! Anything else (raw paragraphs after the last known section) is preserved
//! in `body_extra` and round-trips on save.
//!
//! Usage counters (`uses`, `last_used`) live in a sidecar `_usage.json` keyed
//! by skill name, so the SKILL.md file doesn't churn on every retrieval.
//!
//! ## Port notes
//!
//! Pure, zero-IO module. Frontmatter values are dynamic in Python (`Any`); the
//! Rust port carries them as [`serde_json::Value`] with `preserve_order` so the
//! emitted key order matches the insertion order Python's `dict` preserves. The
//! hand-rolled mini-YAML is reproduced verbatim rather than pulling in a YAML
//! crate (the Python comment is explicit about not adding PyYAML "for one
//! feature"). `json.dumps(s)` string quoting in `_emit_scalar` is reproduced via
//! `serde_json::to_string`.

use once_cell::sync::Lazy;
use regex::Regex;
use serde_json::{Map, Value};

use chrono::Utc;

// ---------------------------------------------------------------------------
// Slugify
// ---------------------------------------------------------------------------

static SLUG_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"[^a-z0-9]+").unwrap());

/// Convert a free-form title to a kebab-case slug suitable for a directory
/// name. Strips non-alphanumerics, collapses runs, trims leading/trailing
/// dashes. Caps at 60 chars.
pub fn slugify(text: &str, fallback: &str) -> String {
    // `str(text or "").strip().lower()` — the caller passes the already-resolved
    // string; an empty string is the falsy case here.
    let s = text.trim().to_lowercase();
    let s = SLUG_RE.replace_all(&s, "-");
    let s = s.trim_matches('-');
    let chosen: &str = if s.is_empty() { fallback } else { s };
    // Python `[:60]` slices by character; the slug is ASCII so bytes == chars,
    // but truncate on a char boundary to stay correct for an ASCII `fallback`.
    chosen.chars().take(60).collect()
}

// ---------------------------------------------------------------------------
// Frontmatter (minimal YAML — we don't pull in PyYAML for one feature)
// ---------------------------------------------------------------------------

// We accept a tiny subset of YAML: scalar `key: value`, inline lists `[a, b]`,
// and block lists with `-`. That covers everything in our schema and avoids
// a new dependency.

static FM_KEY_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"(?i)^([a-z_][a-z0-9_]*):\s*(.*)$").unwrap());
static FM_BLOCK_LIST_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"^\s*-\s*(.*)$").unwrap());

/// `_parse_scalar` — coerce a raw YAML scalar token to a JSON value.
fn parse_scalar(raw: &str) -> Value {
    let raw = raw.trim();
    if raw.is_empty() {
        return Value::String(String::new());
    }
    if raw.starts_with('[') && raw.ends_with(']') {
        let inner = raw[1..raw.len() - 1].trim();
        if inner.is_empty() {
            return Value::Array(Vec::new());
        }
        let parts = split_top_level(inner, ',');
        return Value::Array(parts.iter().map(|p| parse_scalar(p)).collect());
    }
    let lower = raw.to_lowercase();
    if lower == "true" || lower == "yes" {
        return Value::Bool(true);
    }
    if lower == "false" || lower == "no" {
        return Value::Bool(false);
    }
    if lower == "null" || lower == "none" || lower == "~" {
        return Value::Null;
    }
    // `(raw[0] == raw[-1]) and raw[0] in ("'", '"')`
    let first = raw.chars().next().unwrap();
    let last = raw.chars().last().unwrap();
    if first == last && (first == '\'' || first == '"') {
        // Python `raw[1:-1]` — strip the matching outer quote chars. Both quote
        // characters are single-byte ASCII so byte slicing is safe.
        return Value::String(raw[1..raw.len() - 1].to_string());
    }
    // Try number — Python: `if "." in raw: float(raw) else: int(raw)`,
    // falling through to the raw string on ValueError.
    if raw.contains('.') {
        if let Ok(f) = raw.parse::<f64>() {
            if let Some(n) = serde_json::Number::from_f64(f) {
                return Value::Number(n);
            }
        }
    } else if let Ok(i) = raw.parse::<i64>() {
        return Value::Number(serde_json::Number::from(i));
    }
    Value::String(raw.to_string())
}

/// `_split_top_level` — split `s` on `sep` ignoring separators inside `[]` or
/// quotes.
fn split_top_level(s: &str, sep: char) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    let mut buf = String::new();
    let mut depth: i64 = 0;
    let mut quote: Option<char> = None;
    for ch in s.chars() {
        if let Some(q) = quote {
            buf.push(ch);
            if ch == q {
                quote = None;
            }
            continue;
        }
        if ch == '\'' || ch == '"' {
            quote = Some(ch);
            buf.push(ch);
            continue;
        }
        if ch == '[' {
            depth += 1;
        } else if ch == ']' {
            depth = std::cmp::max(0, depth - 1);
        }
        if ch == sep && depth == 0 {
            out.push(buf.trim().to_string());
            buf = String::new();
            continue;
        }
        buf.push(ch);
    }
    if !buf.is_empty() {
        out.push(buf.trim().to_string());
    }
    out
}

/// Pull the YAML frontmatter out of a SKILL.md and return `(fm, body)`.
pub fn parse_frontmatter(text: &str) -> (Map<String, Value>, String) {
    if !text.starts_with("---") {
        return (Map::new(), text.to_string());
    }
    // Python `text.find("\n---", 3)` — search for the closing fence starting
    // after the opening `---`.
    let end = match text.get(3..).and_then(|tail| tail.find("\n---")) {
        Some(rel) => rel + 3,
        None => return (Map::new(), text.to_string()),
    };
    // `text[3:end].lstrip("\n")`
    let fm_text = text[3..end].trim_start_matches('\n');
    // `text[end + 4:].lstrip("\n")`
    let body = text[end + 4..].trim_start_matches('\n').to_string();
    let mut fm: Map<String, Value> = Map::new();
    let mut pending_key: Option<String> = None;
    for line in fm_text.split('\n') {
        // `if not line.strip() or line.lstrip().startswith("#"): continue`
        let stripped = line.trim();
        if stripped.is_empty() || line.trim_start().starts_with('#') {
            continue;
        }
        if let Some(m) = FM_KEY_RE.captures(line) {
            let key = m.get(1).unwrap().as_str().to_string();
            let val = m.get(2).unwrap().as_str();
            if val.trim().is_empty() {
                pending_key = Some(key.clone());
                fm.insert(key, Value::Array(Vec::new()));
            } else {
                fm.insert(key, parse_scalar(val));
                pending_key = None;
            }
            continue;
        }
        if let Some(m2) = FM_BLOCK_LIST_RE.captures(line) {
            if let Some(pk) = pending_key.clone() {
                // `if not isinstance(existing, list): fm[pending_key] = []`
                let is_list = matches!(fm.get(&pk), Some(Value::Array(_)));
                if !is_list {
                    fm.insert(pk.clone(), Value::Array(Vec::new()));
                }
                let item = parse_scalar(m2.get(1).unwrap().as_str());
                if let Some(Value::Array(arr)) = fm.get_mut(&pk) {
                    arr.push(item);
                }
            }
        }
    }
    (fm, body)
}

/// `_emit_scalar` — render a JSON value back to its YAML scalar token.
fn emit_scalar(v: &Value) -> String {
    match v {
        Value::Null => "null".to_string(),
        Value::Bool(b) => {
            if *b {
                "true".to_string()
            } else {
                "false".to_string()
            }
        }
        Value::Number(n) => python_number_str(n),
        Value::Array(arr) => {
            let parts: Vec<String> = arr.iter().map(emit_scalar).collect();
            format!("[{}]", parts.join(", "))
        }
        Value::String(s) => {
            // `if any(c in s for c in (...)): return json.dumps(s)`
            const SPECIALS: &[char] = &[
                ':', '#', '\n', '[', ']', '{', '}', ',', '&', '*', '!', '|', '>', '\'', '"', '%',
                '@',
            ];
            if s.chars().any(|c| SPECIALS.contains(&c)) {
                // `json.dumps(s)` — serde_json::to_string of a string produces
                // the same double-quoted, backslash-escaped JSON token.
                serde_json::to_string(s).unwrap_or_else(|_| s.clone())
            } else {
                s.clone()
            }
        }
        // Frontmatter never holds nested objects; fall back to a plain dump.
        Value::Object(_) => serde_json::to_string(v).unwrap_or_default(),
    }
}

/// `_as_list` — coerce a JSON value to a list of strings, dropping `None`/`""`.
fn as_list(v: Option<&Value>) -> Vec<String> {
    match v {
        None | Some(Value::Null) => Vec::new(),
        Some(Value::Array(arr)) => arr
            .iter()
            .filter_map(|x| match x {
                Value::Null => None,
                Value::String(s) if s.is_empty() => None,
                other => Some(value_to_str(other)),
            })
            .collect(),
        Some(other) => vec![value_to_str(other)],
    }
}

/// `_as_float` — coerce a JSON value to `f64`, falling back to `default`.
fn as_float(v: Option<&Value>, default: f64) -> f64 {
    match v {
        Some(Value::Number(n)) => n.as_f64().unwrap_or(default),
        Some(Value::String(s)) => s.trim().parse::<f64>().unwrap_or(default),
        Some(Value::Bool(b)) => {
            // Python `float(True)` == 1.0, `float(False)` == 0.0.
            if *b {
                1.0
            } else {
                0.0
            }
        }
        _ => default,
    }
}

/// `emit_frontmatter` — render the ordered `key: value` block.
pub fn emit_frontmatter(fm: &Map<String, Value>) -> String {
    let mut lines: Vec<String> = Vec::new();
    for (k, v) in fm.iter() {
        // `if v is None or v == [] or v == "": continue`
        let skip = match v {
            Value::Null => true,
            Value::Array(arr) => arr.is_empty(),
            Value::String(s) => s.is_empty(),
            _ => false,
        };
        if skip {
            continue;
        }
        lines.push(format!("{}: {}", k, emit_scalar(v)));
    }
    lines.join("\n")
}

// ---------------------------------------------------------------------------
// Skill body sections
// ---------------------------------------------------------------------------

const KNOWN_SECTIONS: [&str; 4] = ["when_to_use", "procedure", "pitfalls", "verification"];

/// `_HEADING_TO_KEY` — lowercased heading text -> section key.
fn heading_to_key(heading: &str) -> Option<&'static str> {
    match heading {
        "when to use" => Some("when_to_use"),
        "procedure" => Some("procedure"),
        "steps" => Some("procedure"),
        "pitfalls" => Some("pitfalls"),
        "verification" => Some("verification"),
        _ => None,
    }
}

/// `_KEY_TO_HEADING` — section key -> rendered heading text.
fn key_to_heading(key: &str) -> &'static str {
    match key {
        "when_to_use" => "When to Use",
        "procedure" => "Procedure",
        "pitfalls" => "Pitfalls",
        "verification" => "Verification",
        _ => "",
    }
}

static SECTION_HEADING_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"^##\s+(.*?)\s*$").unwrap());
static LIST_LINE_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"^(?:[-*]|\d+[.)])\s+(.*)$").unwrap());

/// Parsed SKILL.md body sections.
///
/// Mirrors the dict `parse_body` returns:
/// ```text
/// {
///     "when_to_use": str,
///     "procedure":   list[str],   # numbered/bulleted lines
///     "pitfalls":    list[str],
///     "verification": list[str],
///     "body_extra":  str,         # anything not under a known heading
/// }
/// ```
#[derive(Debug, Default, Clone)]
pub struct BodySections {
    pub when_to_use: String,
    pub procedure: Vec<String>,
    pub pitfalls: Vec<String>,
    pub verification: Vec<String>,
    pub body_extra: String,
}

/// Split a SKILL.md body into known sections.
pub fn parse_body(body: &str) -> BodySections {
    let mut out = BodySections::default();
    if body.trim().is_empty() {
        return out;
    }

    // `sections: List[tuple[Optional[str], List[str]]] = [(None, [])]`
    let mut sections: Vec<(Option<&'static str>, Vec<String>)> = vec![(None, Vec::new())];
    for line in body.split('\n') {
        if let Some(m) = SECTION_HEADING_RE.captures(line) {
            let heading = m.get(1).unwrap().as_str().trim().to_lowercase();
            let key = heading_to_key(&heading);
            sections.push((key, Vec::new()));
            continue;
        }
        sections
            .last_mut()
            .unwrap()
            .1
            .push(line.to_string());
    }

    for (key, lines) in sections {
        // `text = "\n".join(lines).strip("\n")`
        let text = lines.join("\n");
        let text = text.trim_matches('\n');
        match key {
            None => {
                let extras = text.trim();
                if !extras.is_empty() {
                    // `(out["body_extra"] + "\n\n" + extras).strip()`
                    let combined = format!("{}\n\n{}", out.body_extra, extras);
                    out.body_extra = combined.trim().to_string();
                }
            }
            Some("when_to_use") => {
                out.when_to_use = text.trim().to_string();
            }
            Some("procedure") => {
                out.procedure = parse_list_lines(text);
            }
            Some("pitfalls") => {
                out.pitfalls = parse_list_lines(text);
            }
            Some("verification") => {
                out.verification = parse_list_lines(text);
            }
            // Unreachable: heading_to_key only returns the four known keys.
            Some(other) => {
                debug_assert!(KNOWN_SECTIONS.contains(&other));
            }
        }
    }
    out
}

/// `_parse_list_lines` — pull bullet/numbered lines out of a section body.
/// Plain paragraphs are treated as a single entry.
fn parse_list_lines(text: &str) -> Vec<String> {
    let mut items: Vec<String> = Vec::new();
    for line in text.split('\n') {
        let s = line.trim();
        if s.is_empty() {
            continue;
        }
        if let Some(m) = LIST_LINE_RE.captures(s) {
            items.push(m.get(1).unwrap().as_str().trim().to_string());
        } else if let Some(last) = items.last_mut() {
            // continuation of previous bullet
            last.push(' ');
            last.push_str(s);
        } else {
            items.push(s.to_string());
        }
    }
    items
}

/// `emit_body` — render the structured body back to markdown.
pub fn emit_body(sections: &BodySections) -> String {
    let mut parts: Vec<String> = Vec::new();
    let when = sections.when_to_use.trim();
    if !when.is_empty() {
        parts.push(format!("## {}\n\n{}", key_to_heading("when_to_use"), when));
    }
    for key in ["procedure", "pitfalls", "verification"] {
        let items: &Vec<String> = match key {
            "procedure" => &sections.procedure,
            "pitfalls" => &sections.pitfalls,
            "verification" => &sections.verification,
            _ => unreachable!(),
        };
        if items.is_empty() {
            continue;
        }
        let heading = key_to_heading(key);
        let body = if key == "procedure" {
            items
                .iter()
                .enumerate()
                .map(|(i, x)| format!("{}. {}", i + 1, x))
                .collect::<Vec<_>>()
                .join("\n")
        } else {
            items
                .iter()
                .map(|x| format!("- {}", x))
                .collect::<Vec<_>>()
                .join("\n")
        };
        parts.push(format!("## {}\n\n{}", heading, body));
    }
    let extra = sections.body_extra.trim();
    if !extra.is_empty() {
        parts.push(extra.to_string());
    }
    // `"\n\n".join(parts) + ("\n" if parts else "")`
    let joined = parts.join("\n\n");
    if parts.is_empty() {
        joined
    } else {
        format!("{}\n", joined)
    }
}

// ---------------------------------------------------------------------------
// Skill record
// ---------------------------------------------------------------------------

/// A single skill record (the Python `@dataclass Skill`).
///
/// All fields are public and mutable so the [`SkillsManager`](super::skills)
/// can edit them in place the way the Python code mutates `sk.<field>`.
#[derive(Debug, Clone)]
pub struct Skill {
    /// slug, dir name
    pub name: String,
    pub description: String,
    pub version: String,
    pub category: String,
    pub tags: Vec<String>,
    pub platforms: Vec<String>,
    pub requires_toolsets: Vec<String>,
    pub fallback_for_toolsets: Vec<String>,
    /// draft | published
    pub status: String,
    pub confidence: f64,
    pub source: String,
    pub teacher_model: Option<String>,
    pub owner: Option<String>,
    /// ISO8601
    pub created: String,
    pub when_to_use: String,
    pub procedure: Vec<String>,
    pub pitfalls: Vec<String>,
    pub verification: Vec<String>,
    pub body_extra: String,
    // Sidecar (not persisted in SKILL.md)
    pub uses: i64,
    pub last_used: Option<i64>,
    // File path on disk (set when read)
    pub path: Option<String>,
}

impl Default for Skill {
    fn default() -> Self {
        // Mirrors the Python dataclass field defaults.
        Skill {
            name: String::new(),
            description: String::new(),
            version: "1.0.0".to_string(),
            category: "general".to_string(),
            tags: Vec::new(),
            platforms: Vec::new(),
            requires_toolsets: Vec::new(),
            fallback_for_toolsets: Vec::new(),
            status: "draft".to_string(),
            confidence: 0.8,
            source: "learned".to_string(),
            teacher_model: None,
            owner: None,
            created: String::new(),
            when_to_use: String::new(),
            procedure: Vec::new(),
            pitfalls: Vec::new(),
            verification: Vec::new(),
            body_extra: String::new(),
            uses: 0,
            last_used: None,
            path: None,
        }
    }
}

impl Skill {
    // ----------------------------------------------------------------------
    // Serialization
    // ----------------------------------------------------------------------

    /// `to_frontmatter` — the persisted-in-SKILL.md ordered key/value map.
    pub fn to_frontmatter(&self) -> Map<String, Value> {
        let mut fm: Map<String, Value> = Map::new();
        fm.insert("name".to_string(), Value::String(self.name.clone()));
        fm.insert(
            "description".to_string(),
            Value::String(self.description.clone()),
        );
        fm.insert("version".to_string(), Value::String(self.version.clone()));
        fm.insert("category".to_string(), Value::String(self.category.clone()));
        if !self.tags.is_empty() {
            fm.insert("tags".to_string(), str_vec_to_value(&self.tags));
        }
        if !self.platforms.is_empty() {
            fm.insert("platforms".to_string(), str_vec_to_value(&self.platforms));
        }
        if !self.requires_toolsets.is_empty() {
            fm.insert(
                "requires_toolsets".to_string(),
                str_vec_to_value(&self.requires_toolsets),
            );
        }
        if !self.fallback_for_toolsets.is_empty() {
            fm.insert(
                "fallback_for_toolsets".to_string(),
                str_vec_to_value(&self.fallback_for_toolsets),
            );
        }
        fm.insert("status".to_string(), Value::String(self.status.clone()));
        fm.insert("confidence".to_string(), round3_value(self.confidence));
        fm.insert("source".to_string(), Value::String(self.source.clone()));
        if let Some(tm) = &self.teacher_model {
            // Python guards on truthiness — an empty string would be skipped.
            if !tm.is_empty() {
                fm.insert("teacher_model".to_string(), Value::String(tm.clone()));
            }
        }
        if let Some(owner) = &self.owner {
            if !owner.is_empty() {
                fm.insert("owner".to_string(), Value::String(owner.clone()));
            }
        }
        let created = if self.created.is_empty() {
            now_iso()
        } else {
            self.created.clone()
        };
        fm.insert("created".to_string(), Value::String(created));
        fm
    }

    /// `to_dict` — the full record (incl. sidecar + back-compat aliases).
    pub fn to_dict(&self) -> Map<String, Value> {
        let mut d: Map<String, Value> = Map::new();
        // slug doubles as id
        d.insert("id".to_string(), Value::String(self.name.clone()));
        d.insert("name".to_string(), Value::String(self.name.clone()));
        d.insert(
            "description".to_string(),
            Value::String(self.description.clone()),
        );
        d.insert("version".to_string(), Value::String(self.version.clone()));
        d.insert("category".to_string(), Value::String(self.category.clone()));
        d.insert("tags".to_string(), str_vec_to_value(&self.tags));
        d.insert("platforms".to_string(), str_vec_to_value(&self.platforms));
        d.insert(
            "requires_toolsets".to_string(),
            str_vec_to_value(&self.requires_toolsets),
        );
        d.insert(
            "fallback_for_toolsets".to_string(),
            str_vec_to_value(&self.fallback_for_toolsets),
        );
        d.insert("status".to_string(), Value::String(self.status.clone()));
        d.insert("confidence".to_string(), round3_value(self.confidence));
        d.insert("source".to_string(), Value::String(self.source.clone()));
        d.insert(
            "teacher_model".to_string(),
            opt_str_to_value(&self.teacher_model),
        );
        d.insert("owner".to_string(), opt_str_to_value(&self.owner));
        d.insert("created".to_string(), Value::String(self.created.clone()));
        d.insert(
            "when_to_use".to_string(),
            Value::String(self.when_to_use.clone()),
        );
        d.insert("procedure".to_string(), str_vec_to_value(&self.procedure));
        d.insert("pitfalls".to_string(), str_vec_to_value(&self.pitfalls));
        d.insert(
            "verification".to_string(),
            str_vec_to_value(&self.verification),
        );
        d.insert(
            "body_extra".to_string(),
            Value::String(self.body_extra.clone()),
        );
        // `int(self.uses or 0)` — already an int in Rust.
        d.insert(
            "uses".to_string(),
            Value::Number(serde_json::Number::from(self.uses)),
        );
        d.insert(
            "last_used".to_string(),
            match self.last_used {
                Some(v) => Value::Number(serde_json::Number::from(v)),
                None => Value::Null,
            },
        );
        d.insert("path".to_string(), opt_str_to_value(&self.path));
        // Back-compat aliases for the old API/UI
        // `d["title"] = self.description or self.name.replace("-", " ").title()`
        let title = if !self.description.is_empty() {
            self.description.clone()
        } else {
            title_case(&self.name.replace('-', " "))
        };
        d.insert("title".to_string(), Value::String(title));
        d.insert(
            "problem".to_string(),
            Value::String(self.when_to_use.clone()),
        );
        // `(self.procedure[0] if self.procedure else "") if not self.body_extra else self.body_extra`
        let solution = if self.body_extra.is_empty() {
            self.procedure.first().cloned().unwrap_or_default()
        } else {
            self.body_extra.clone()
        };
        d.insert("solution".to_string(), Value::String(solution));
        d.insert("steps".to_string(), str_vec_to_value(&self.procedure));
        d
    }

    /// `Skill.from_markdown` — parse a SKILL.md. Never raises: missing/odd
    /// fields fall back to the dataclass defaults.
    pub fn from_markdown(text: &str, path: Option<String>) -> Skill {
        let (fm, body) = parse_frontmatter(text);
        let sections = parse_body(&body);
        // `name = slugify(raw_name if raw_name not in (None, "") else fm.get("description", ""), fallback="skill")`
        let raw_name = fm.get("name");
        let name_source = match raw_name {
            Some(Value::Null) | None => fm_get_str(&fm, "description", ""),
            Some(Value::String(s)) if s.is_empty() => fm_get_str(&fm, "description", ""),
            Some(v) => value_to_str(v),
        };
        let name = slugify(&name_source, "skill");

        Skill {
            name,
            description: fm_get_str_or(&fm, "description", ""),
            version: fm_get_str_or(&fm, "version", "1.0.0"),
            category: fm_get_str_or(&fm, "category", "general"),
            tags: as_list(fm.get("tags")),
            platforms: as_list(fm.get("platforms")),
            requires_toolsets: as_list(fm.get("requires_toolsets")),
            fallback_for_toolsets: as_list(fm.get("fallback_for_toolsets")),
            status: fm_get_str_or(&fm, "status", "draft"),
            confidence: as_float(fm.get("confidence"), 0.8),
            source: fm_get_str_or(&fm, "source", "learned"),
            teacher_model: fm_get_opt_str(&fm, "teacher_model"),
            owner: fm_get_opt_str(&fm, "owner"),
            // `str(fm.get("created") or _now_iso())`
            created: {
                let c = fm_get_str(&fm, "created", "");
                if c.is_empty() {
                    now_iso()
                } else {
                    c
                }
            },
            when_to_use: sections.when_to_use,
            procedure: sections.procedure,
            pitfalls: sections.pitfalls,
            verification: sections.verification,
            body_extra: sections.body_extra,
            path,
            ..Skill::default()
        }
    }

    /// `to_markdown` — render the full SKILL.md (frontmatter + body).
    pub fn to_markdown(&self) -> String {
        let fm = emit_frontmatter(&self.to_frontmatter());
        let body = emit_body(&BodySections {
            when_to_use: self.when_to_use.clone(),
            procedure: self.procedure.clone(),
            pitfalls: self.pitfalls.clone(),
            verification: self.verification.clone(),
            body_extra: self.body_extra.clone(),
        });
        format!("---\n{}\n---\n\n{}", fm, body)
    }
}

/// `_now_iso` — `datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")`.
fn now_iso() -> String {
    Utc::now().format("%Y-%m-%dT%H:%M:%SZ").to_string()
}

// ---------------------------------------------------------------------------
// Internal value helpers (no Python counterpart — bridge dynamic `Any`)
// ---------------------------------------------------------------------------

/// `str(v)` for the JSON values frontmatter can hold — matches Python `str()`
/// for the cases that actually occur (strings, bools, numbers).
fn value_to_str(v: &Value) -> String {
    match v {
        Value::String(s) => s.clone(),
        Value::Bool(b) => {
            // Python `str(True)` == "True", `str(False)` == "False".
            if *b {
                "True".to_string()
            } else {
                "False".to_string()
            }
        }
        Value::Number(n) => python_number_str(n),
        Value::Null => "None".to_string(),
        other => other.to_string(),
    }
}

/// Render a JSON number the way Python's `str()` would: integers without a
/// trailing `.0`, floats with the shortest round-tripping decimal.
fn python_number_str(n: &serde_json::Number) -> String {
    if let Some(i) = n.as_i64() {
        return i.to_string();
    }
    if let Some(u) = n.as_u64() {
        return u.to_string();
    }
    // serde_json renders floats with the shortest round-trip form, matching
    // CPython's `repr`/`str` for the small confidence values used here
    // (e.g. `0.8` -> "0.8", `0.123` -> "0.123").
    n.to_string()
}

/// `fm.get(key, "")` then `str(...)` -> owned `String` (empty default).
fn fm_get_str(fm: &Map<String, Value>, key: &str, default: &str) -> String {
    match fm.get(key) {
        Some(Value::Null) | None => default.to_string(),
        Some(v) => value_to_str(v),
    }
}

/// `str(fm.get(key, default) or default)` — Python's `or` collapses an empty
/// string / falsy value back to the default.
fn fm_get_str_or(fm: &Map<String, Value>, key: &str, default: &str) -> String {
    let v = fm_get_str(fm, key, default);
    if v.is_empty() {
        default.to_string()
    } else {
        v
    }
}

/// `str(fm.get(key)) if fm.get(key) else None` — truthiness-gated optional.
fn fm_get_opt_str(fm: &Map<String, Value>, key: &str) -> Option<String> {
    match fm.get(key) {
        None | Some(Value::Null) => None,
        Some(Value::String(s)) if s.is_empty() => None,
        Some(Value::Bool(false)) => None,
        Some(v) => {
            let s = value_to_str(v);
            if s.is_empty() {
                None
            } else {
                Some(s)
            }
        }
    }
}

fn opt_str_to_value(v: &Option<String>) -> Value {
    match v {
        Some(s) => Value::String(s.clone()),
        None => Value::Null,
    }
}

fn str_vec_to_value(v: &[String]) -> Value {
    Value::Array(v.iter().map(|s| Value::String(s.clone())).collect())
}

/// `round(float(x), 3)` rendered as a JSON number. Python's `round` uses
/// banker's (round-half-to-even) rounding; reproduce it so the emitted token
/// matches.
fn round3_value(x: f64) -> Value {
    let r = round_half_even(x, 3);
    match serde_json::Number::from_f64(r) {
        Some(n) => Value::Number(n),
        None => Value::Number(serde_json::Number::from(0)),
    }
}

/// Round `x` to `ndigits` decimal places using round-half-to-even, matching
/// Python's built-in `round`.
fn round_half_even(x: f64, ndigits: i32) -> f64 {
    if !x.is_finite() {
        return x;
    }
    let factor = 10f64.powi(ndigits);
    let scaled = x * factor;
    let floor = scaled.floor();
    let diff = scaled - floor;
    let rounded = if diff > 0.5 {
        floor + 1.0
    } else if diff < 0.5 {
        floor
    } else {
        // Exactly halfway -> round to even.
        if (floor as i64) % 2 == 0 {
            floor
        } else {
            floor + 1.0
        }
    };
    rounded / factor
}

/// Python `str.title()` — capitalize the first letter of each
/// alphabetic run, lowercasing the rest.
fn title_case(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    let mut prev_alpha = false;
    for ch in s.chars() {
        if ch.is_alphabetic() {
            if prev_alpha {
                out.extend(ch.to_lowercase());
            } else {
                out.extend(ch.to_uppercase());
            }
            prev_alpha = true;
        } else {
            out.push(ch);
            prev_alpha = false;
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn slugify_basic() {
        assert_eq!(slugify("Open PR From Branch", "skill"), "open-pr-from-branch");
        assert_eq!(slugify("  Hello, World!! ", "skill"), "hello-world");
        assert_eq!(slugify("", "skill"), "skill");
        assert_eq!(slugify("!!!", "fallback"), "fallback");
        // cap at 60 chars
        let long = "a".repeat(100);
        assert_eq!(slugify(&long, "skill").len(), 60);
    }

    #[test]
    fn parse_scalar_kinds() {
        assert_eq!(parse_scalar("true"), Value::Bool(true));
        assert_eq!(parse_scalar("No"), Value::Bool(false));
        assert_eq!(parse_scalar("null"), Value::Null);
        assert_eq!(parse_scalar("~"), Value::Null);
        assert_eq!(parse_scalar("42"), Value::Number(serde_json::Number::from(42)));
        assert_eq!(parse_scalar("0.8").as_f64(), Some(0.8));
        assert_eq!(parse_scalar("'quoted'"), Value::String("quoted".to_string()));
        assert_eq!(
            parse_scalar("[a, b]"),
            Value::Array(vec![
                Value::String("a".to_string()),
                Value::String("b".to_string())
            ])
        );
        assert_eq!(parse_scalar("[]"), Value::Array(vec![]));
        assert_eq!(parse_scalar("plain text"), Value::String("plain text".to_string()));
    }

    #[test]
    fn split_top_level_respects_brackets_and_quotes() {
        assert_eq!(
            split_top_level("a, b, c", ','),
            vec!["a".to_string(), "b".to_string(), "c".to_string()]
        );
        // nested bracket should not split
        assert_eq!(
            split_top_level("a, [b, c]", ','),
            vec!["a".to_string(), "[b, c]".to_string()]
        );
        // quoted comma not split
        assert_eq!(
            split_top_level("\"a, b\", c", ','),
            vec!["\"a, b\"".to_string(), "c".to_string()]
        );
    }

    #[test]
    fn frontmatter_round_trip() {
        let text = "---\nname: my-skill\ndescription: A test skill\nversion: 1.0.0\ncategory: dev\ntags: [git, github]\nstatus: published\nconfidence: 0.9\n---\n\n## When to Use\nWhen you need it.\n\n## Procedure\n1. First\n2. Second\n";
        let (fm, body) = parse_frontmatter(text);
        assert_eq!(fm.get("name").unwrap(), &Value::String("my-skill".to_string()));
        assert_eq!(
            fm.get("tags").unwrap(),
            &Value::Array(vec![
                Value::String("git".to_string()),
                Value::String("github".to_string())
            ])
        );
        assert!(body.starts_with("## When to Use"));
    }

    #[test]
    fn block_list_frontmatter() {
        let text = "---\nname: s\ntags:\n  - alpha\n  - beta\n---\nbody\n";
        let (fm, _body) = parse_frontmatter(text);
        assert_eq!(
            fm.get("tags").unwrap(),
            &Value::Array(vec![
                Value::String("alpha".to_string()),
                Value::String("beta".to_string())
            ])
        );
    }

    #[test]
    fn parse_body_sections() {
        let body = "## When to Use\nTrigger here.\n\n## Procedure\n1. Step one\n2. Step two\n\n## Pitfalls\n- watch out\n\nleftover paragraph\n";
        let s = parse_body(body);
        assert_eq!(s.when_to_use, "Trigger here.");
        assert_eq!(s.procedure, vec!["Step one".to_string(), "Step two".to_string()]);
        // "leftover paragraph" is under the Pitfalls heading; per
        // `_parse_list_lines` a non-bullet line after a bullet continues that
        // bullet (the intervening blank line is skipped), so it joins onto
        // "watch out" rather than becoming body_extra. This mirrors Python.
        assert_eq!(s.pitfalls, vec!["watch out leftover paragraph".to_string()]);
        assert!(s.body_extra.is_empty());
    }

    #[test]
    fn list_line_continuation() {
        let items = parse_list_lines("- first\n  still first\n- second");
        assert_eq!(items, vec!["first still first".to_string(), "second".to_string()]);
    }

    #[test]
    fn emit_scalar_quotes_specials() {
        assert_eq!(emit_scalar(&Value::String("plain".to_string())), "plain");
        assert_eq!(
            emit_scalar(&Value::String("has: colon".to_string())),
            "\"has: colon\""
        );
        assert_eq!(emit_scalar(&Value::Null), "null");
        assert_eq!(emit_scalar(&Value::Bool(true)), "true");
        assert_eq!(
            emit_scalar(&Value::Array(vec![
                Value::String("a".to_string()),
                Value::String("b".to_string())
            ])),
            "[a, b]"
        );
    }

    #[test]
    fn skill_markdown_round_trip() {
        let mut sk = Skill {
            name: "test-skill".to_string(),
            description: "A test skill".to_string(),
            category: "dev".to_string(),
            tags: vec!["git".to_string()],
            status: "published".to_string(),
            confidence: 0.9,
            when_to_use: "When testing.".to_string(),
            procedure: vec!["Run it".to_string(), "Check output".to_string()],
            pitfalls: vec!["Don't forget".to_string()],
            created: "2026-05-09T21:43:00Z".to_string(),
            ..Skill::default()
        };
        sk.path = None;
        let md = sk.to_markdown();
        let parsed = Skill::from_markdown(&md, None);
        assert_eq!(parsed.name, "test-skill");
        assert_eq!(parsed.description, "A test skill");
        assert_eq!(parsed.category, "dev");
        assert_eq!(parsed.tags, vec!["git".to_string()]);
        assert_eq!(parsed.status, "published");
        assert!((parsed.confidence - 0.9).abs() < 1e-9);
        assert_eq!(parsed.when_to_use, "When testing.");
        assert_eq!(parsed.procedure, vec!["Run it".to_string(), "Check output".to_string()]);
        assert_eq!(parsed.pitfalls, vec!["Don't forget".to_string()]);
        assert_eq!(parsed.created, "2026-05-09T21:43:00Z");
    }

    #[test]
    fn to_dict_back_compat_aliases() {
        let sk = Skill {
            name: "open-pr".to_string(),
            description: String::new(),
            when_to_use: "need a pr".to_string(),
            procedure: vec!["do it".to_string()],
            body_extra: String::new(),
            ..Skill::default()
        };
        let d = sk.to_dict();
        // title falls back to titled name
        assert_eq!(d.get("title").unwrap(), &Value::String("Open Pr".to_string()));
        assert_eq!(d.get("problem").unwrap(), &Value::String("need a pr".to_string()));
        // solution = first procedure step when no body_extra
        assert_eq!(d.get("solution").unwrap(), &Value::String("do it".to_string()));
        assert_eq!(d.get("id").unwrap(), &Value::String("open-pr".to_string()));
    }

    #[test]
    fn confidence_rounds_to_three_decimals() {
        let sk = Skill {
            name: "x".to_string(),
            confidence: 0.123456,
            ..Skill::default()
        };
        let fm = sk.to_frontmatter();
        assert_eq!(fm.get("confidence").unwrap().as_f64(), Some(0.123));
    }

    #[test]
    fn from_markdown_never_panics_on_garbage() {
        // No frontmatter at all
        let sk = Skill::from_markdown("just some text", None);
        assert_eq!(sk.name, "skill"); // slugify("") -> fallback
        // Empty
        let sk2 = Skill::from_markdown("", None);
        assert_eq!(sk2.name, "skill");
    }
}
