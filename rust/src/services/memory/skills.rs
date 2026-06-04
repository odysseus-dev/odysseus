// services/memory/skills.rs  <- services/memory/skills.py
//! Skills storage layer.
//!
//! Skills live on disk as `data/skills/<category>/<name>/SKILL.md` files with
//! YAML frontmatter and a structured markdown body (When to Use / Procedure /
//! Pitfalls / Verification). See [`skill_format`](super::skill_format) for the
//! format.
//!
//! Usage counters (`uses`, `last_used`) live in a sidecar
//! `data/skills/_usage.json` keyed by owner plus skill name so the SKILL.md
//! content doesn't churn on every retrieval.
//!
//! Ownership: skills declare `owner: <username>` in frontmatter. Single-user
//! deployments can leave that blank.
//!
//! This module also retains a JSON fallback for any legacy `data/skills.json`
//! entries — they're surfaced as read-only `Skill` dicts so old data still
//! loads while a user migrates them to disk.
//!
//! ## Port notes
//!
//! Disk-backed, synchronous, no async/HTTP/DB — default-buildable. Skill records
//! flow as [`serde_json::Map`]`<String, Value>` with `preserve_order` so the
//! emitted dict-order matches Python (`load_all`/`index_for`/`get_relevant_skills`
//! all return `dict`s the API serializes). The `_usage.json` sidecar is read as a
//! [`serde_json::Value`] object and persisted via [`crate::core::atomic_io`]
//! (`atomic_write_json`), with the same `os.replace(tmp, path)` fallback the
//! Python `_save_usage` uses. `os.walk(followlinks=False)` -> `walkdir` with
//! `follow_links(false)`; `os.rename` of skill directories -> [`std::fs::rename`];
//! `os.path.realpath`/`os.path.commonpath` path-traversal guard reuses the
//! already-ported helpers in [`crate::src::app_helpers`].

use std::collections::HashSet;
use std::path::Path;

use serde_json::{Map, Value};

use super::skill_format::{slugify, Skill};
use crate::core::atomic_io;
use crate::pylog as logger;
use crate::pyos as os;

// ---------------------------------------------------------------------------
// Token / similarity helpers (kept for the relevance fallback)
// ---------------------------------------------------------------------------

/// `_tokenize` — lowercase split, strip surrounding punctuation, drop 1-char
/// tokens. Returns a set.
fn tokenize(text: &str) -> HashSet<String> {
    // `{w.strip('.,!?";:()[]') for w in (text or "").lower().split() if len(w) > 1}`
    const STRIP: &[char] = &['.', ',', '!', '?', '"', ';', ':', '(', ')', '[', ']'];
    let lowered = text.to_lowercase();
    lowered
        .split_whitespace()
        // Python `if len(w) > 1` filters on the *pre-strip* token length.
        .filter(|w| w.chars().count() > 1)
        .map(|w| w.trim_matches(STRIP).to_string())
        .collect()
}

/// `_jaccard` — intersection over union of two token sets. `0.0` if either is
/// empty.
fn jaccard(a: &HashSet<String>, b: &HashSet<String>) -> f64 {
    if a.is_empty() || b.is_empty() {
        return 0.0;
    }
    let inter = a.intersection(b).count();
    let union = a.union(b).count();
    inter as f64 / union as f64
}

/// `_to_float` — coerce a possibly hand-edited frontmatter value to float
/// without raising. A blank or non-numeric `confidence:` in a SKILL.md must not
/// blow up retrieval or eviction.
fn to_float(x: Option<&Value>, default: f64) -> f64 {
    // Python `float(x)` accepts int/float/numeric-string; everything else
    // (None, etc.) raises TypeError/ValueError -> default.
    match x {
        Some(Value::Number(n)) => n.as_f64().unwrap_or(default),
        Some(Value::String(s)) => s.trim().parse::<f64>().unwrap_or(default),
        Some(Value::Bool(b)) => {
            // `float(True)` == 1.0, `float(False)` == 0.0 (bool is int in Python).
            if *b {
                1.0
            } else {
                0.0
            }
        }
        _ => default,
    }
}

// ---------------------------------------------------------------------------
// Small `dict`-access helpers (bridge the dynamic `dict.get(...)` calls)
// ---------------------------------------------------------------------------

/// `d.get(key, "")` -> owned string ("" for missing / non-string / null).
fn get_str(d: &Map<String, Value>, key: &str) -> String {
    match d.get(key) {
        Some(Value::String(s)) => s.clone(),
        _ => String::new(),
    }
}

/// `d.get(key) or []` flattened to a `Vec<String>`.
fn get_str_list(d: &Map<String, Value>, key: &str) -> Vec<String> {
    match d.get(key) {
        Some(Value::Array(arr)) => arr
            .iter()
            .map(|v| match v {
                Value::String(s) => s.clone(),
                other => other.to_string(),
            })
            .collect(),
        _ => Vec::new(),
    }
}

/// `int(d.get(key, default))` — tolerant integer read.
fn get_i64(d: &Map<String, Value>, key: &str, default: i64) -> i64 {
    match d.get(key) {
        Some(Value::Number(n)) => n.as_i64().unwrap_or(default),
        _ => default,
    }
}

// ---------------------------------------------------------------------------
// SkillsManager
// ---------------------------------------------------------------------------

/// Read/write SKILL.md files under `<data_dir>/skills/`.
pub struct SkillsManager {
    pub data_dir: String,
    pub skills_root: String,
    pub usage_file: String,
    /// back-compat
    pub legacy_file: String,
}

impl SkillsManager {
    /// `__init__(self, data_dir)` — set up paths and ensure `skills_root` exists.
    pub fn new(data_dir: &str) -> Self {
        let skills_root = os::path::join(data_dir, "skills");
        let usage_file = os::path::join(&skills_root, "_usage.json");
        let legacy_file = os::path::join(data_dir, "skills.json");
        // os.makedirs(self.skills_root, exist_ok=True)
        let _ = os::makedirs(&skills_root, true);
        SkillsManager {
            data_dir: data_dir.to_string(),
            skills_root,
            usage_file,
            legacy_file,
        }
    }

    // ----------------------------------------------------------------------
    // Path helpers
    // ----------------------------------------------------------------------

    /// `_skill_dir(category, name)`.
    fn skill_dir(&self, category: &str, name: &str) -> String {
        // `slugify(category or "general", fallback="general")`
        let cat = slugify(
            if category.is_empty() { "general" } else { category },
            "general",
        );
        let nm = slugify(name, "skill");
        os::path::join(&os::path::join(&self.skills_root, &cat), &nm)
    }

    /// `_skill_file(category, name)`.
    fn skill_file(&self, category: &str, name: &str) -> String {
        os::path::join(&self.skill_dir(category, name), "SKILL.md")
    }

    // ----------------------------------------------------------------------
    // Usage sidecar
    // ----------------------------------------------------------------------

    /// `_load_usage` — read the `_usage.json` sidecar; `{}` on any error.
    fn load_usage(&self) -> Map<String, Value> {
        if !os::path::exists(&self.usage_file) {
            return Map::new();
        }
        match std::fs::read_to_string(&self.usage_file) {
            Ok(text) => match serde_json::from_str::<Value>(&text) {
                // `return d if isinstance(d, dict) else {}`
                Ok(Value::Object(m)) => m,
                _ => Map::new(),
            },
            Err(_) => Map::new(),
        }
    }

    /// `_save_usage` — atomic write with the same `tmp + os.replace` fallback.
    fn save_usage(&self, usage: &Map<String, Value>) {
        // Python first tries `core.atomic_io.atomic_write_json(..., indent=2)`;
        // on any exception it falls back to a manual `tmp + os.replace`.
        if atomic_io::atomic_write_json(&self.usage_file, &Value::Object(usage.clone()), Some(2))
            .is_ok()
        {
            return;
        }
        // Fallback: write to `<usage_file>.tmp` then os.replace.
        let tmp = format!("{}.tmp", self.usage_file);
        if let Ok(text) = serde_json::to_string_pretty(&Value::Object(usage.clone())) {
            if std::fs::write(&tmp, text).is_ok() {
                let _ = os::replace(&tmp, &self.usage_file);
            }
        }
    }

    /// `_usage_key(name, owner)` — skill names are not globally unique once
    /// multiple owners are present, so the usage sidecar (and the skill
    /// load/lookup) is keyed the same way the skill file is scoped:
    /// `f"{owner}::{name}"` when an owner is present (truthy), else just `name`.
    fn usage_key(name: &str, owner: Option<&str>) -> String {
        // `f"{owner}::{name}" if owner else name` — empty-string owner is
        // falsy in Python, so it collapses to the bare name.
        match owner {
            Some(o) if !o.is_empty() => format!("{o}::{name}"),
            _ => name.to_string(),
        }
    }

    /// Record the last test/audit result for a skill in the usage sidecar (so it
    /// surfaces in `load()` without touching SKILL.md). Drives the 'verified'
    /// check + teacher mark on the card.
    pub fn set_audit(
        &self,
        name: &str,
        verdict: &str,
        by_teacher: bool,
        worker_model: &str,
        teacher_model: &str,
        owner: Option<&str>,
    ) {
        let mut usage = self.load_usage();
        // `key = self._usage_key(name, owner)`
        // `e = usage.setdefault(key, {"uses": 0, "last_used": None})`
        let key = Self::usage_key(name, owner);
        let entry = usage.entry(key).or_insert_with(default_usage_entry);
        if let Value::Object(e) = entry {
            e.insert(
                "audit_verdict".to_string(),
                Value::String(verdict.to_string()),
            );
            e.insert("audit_by_teacher".to_string(), Value::Bool(by_teacher));
            if !worker_model.is_empty() {
                e.insert(
                    "audit_worker_model".to_string(),
                    Value::String(worker_model.to_string()),
                );
            }
            if !teacher_model.is_empty() {
                e.insert(
                    "audit_teacher_model".to_string(),
                    Value::String(teacher_model.to_string()),
                );
            }
            // `e["audited_at"] = _t.time()` — float seconds.
            e.insert("audited_at".to_string(), float_value(crate::pytime::time()));
        }
        self.save_usage(&usage);
    }

    /// Record the advisory 'is this skill necessary?' judgment in the usage
    /// sidecar. Surfaced on the card as a flag; never acts on the skill.
    pub fn set_necessity(
        &self,
        name: &str,
        necessary: bool,
        redundant_with: Option<&[String]>,
        reason: &str,
        owner: Option<&str>,
    ) {
        let mut usage = self.load_usage();
        // `key = self._usage_key(name, owner)`
        let key = Self::usage_key(name, owner);
        let entry = usage.entry(key).or_insert_with(default_usage_entry);
        if let Value::Object(e) = entry {
            let mut nec = Map::new();
            nec.insert("necessary".to_string(), Value::Bool(necessary));
            nec.insert(
                "redundant_with".to_string(),
                Value::Array(
                    redundant_with
                        .unwrap_or(&[])
                        .iter()
                        .map(|s| Value::String(s.clone()))
                        .collect(),
                ),
            );
            nec.insert("reason".to_string(), Value::String(reason.to_string()));
            e.insert("necessity".to_string(), Value::Object(nec));
        }
        self.save_usage(&usage);
    }

    // ----------------------------------------------------------------------
    // Disk scan
    // ----------------------------------------------------------------------

    /// `_iter_skill_files` — yield every directory's `SKILL.md` path. Mirrors
    /// `os.walk(self.skills_root, followlinks=False)` keeping dirs that contain
    /// a `SKILL.md`.
    fn iter_skill_files(&self) -> Vec<String> {
        let mut out: Vec<String> = Vec::new();
        // `if not os.path.isdir(self.skills_root): return`
        if !Path::new(&self.skills_root).is_dir() {
            return out;
        }
        for entry in walkdir::WalkDir::new(&self.skills_root)
            .follow_links(false)
            .into_iter()
            .filter_map(|e| e.ok())
        {
            // `if "SKILL.md" in files:` — a directory containing a SKILL.md.
            if entry.file_type().is_dir() {
                let candidate = entry.path().join("SKILL.md");
                if candidate.is_file() {
                    out.push(candidate.to_string_lossy().into_owned());
                }
            }
        }
        out
    }

    /// `_read_skill` — parse a SKILL.md, logging + returning `None` on failure.
    fn read_skill(&self, path: &str) -> Option<Skill> {
        match std::fs::read_to_string(path) {
            Ok(text) => Some(Skill::from_markdown(&text, Some(path.to_string()))),
            Err(e) => {
                logger::warning(&format!("Failed to parse {path}: {e}"));
                None
            }
        }
    }

    /// `_write_skill` — render the skill, create parent dirs, atomic-write,
    /// stamp `sk.path`, return the path. `sk` is mutated to record its path.
    fn write_skill(&self, sk: &mut Skill) -> Option<String> {
        let category = if sk.category.is_empty() {
            "general"
        } else {
            &sk.category
        };
        let path = self.skill_file(category, &sk.name);
        // os.makedirs(os.path.dirname(path), exist_ok=True)
        let _ = os::makedirs(&os::path::dirname(&path), true);
        if atomic_io::atomic_write_text(&path, &sk.to_markdown()).is_err() {
            return None;
        }
        sk.path = Some(path.clone());
        Some(path)
    }

    /// Assign legacy/unclaimed skill files to the primary owner.
    ///
    /// Skills are disk-backed, so the DB legacy-owner migration cannot fix them.
    /// If strict owner filtering is enabled and SKILL.md files have no owner or
    /// an owner from a deleted/test account, the UI appears empty even though
    /// files still exist. This mirrors the DB legacy-owner sweep.
    pub fn backfill_owner(
        &self,
        primary_owner: &str,
        valid_owners: Option<&HashSet<String>>,
    ) -> i64 {
        let primary_owner = primary_owner.trim();
        if primary_owner.is_empty() {
            return 0;
        }
        let empty_set = HashSet::new();
        let valid_owners = valid_owners.unwrap_or(&empty_set);
        let mut changed: i64 = 0;
        for path in self.iter_skill_files() {
            let mut sk = match self.read_skill(&path) {
                Some(s) => s,
                None => continue,
            };
            // `owner = (sk.owner or "").strip()`
            let owner = sk.owner.clone().unwrap_or_default();
            let owner = owner.trim().to_string();
            if owner == primary_owner {
                continue;
            }
            if !owner.is_empty() && valid_owners.contains(&owner) {
                continue;
            }
            sk.owner = Some(primary_owner.to_string());
            if self.write_skill(&mut sk).is_some() {
                changed += 1;
            } else {
                logger::warning(&format!("Failed to backfill owner for skill {}", sk.name));
            }
        }
        changed
    }

    // ----------------------------------------------------------------------
    // Public API — keeps the old method names so callers don't break
    // ----------------------------------------------------------------------

    /// `load_all` — return every skill as a plain dict, plus any legacy JSON
    /// entries.
    pub fn load_all(&self) -> Vec<Map<String, Value>> {
        let usage = self.load_usage();
        let mut out: Vec<Map<String, Value>> = Vec::new();
        let mut seen_names: HashSet<String> = HashSet::new();
        for path in self.iter_skill_files() {
            let sk = match self.read_skill(&path) {
                Some(s) => s,
                None => continue,
            };
            let mut d = sk.to_dict();
            // `u = self._usage_entry(usage, sk.name, sk.owner)` — keyed by the
            // owner-scoped usage key.
            let empty = Map::new();
            let usage_key = Self::usage_key(&sk.name, sk.owner.as_deref());
            let u = match usage.get(&usage_key) {
                Some(Value::Object(m)) => m,
                _ => &empty,
            };
            d.insert(
                "uses".to_string(),
                Value::Number(serde_json::Number::from(get_i64(u, "uses", 0))),
            );
            d.insert(
                "last_used".to_string(),
                u.get("last_used").cloned().unwrap_or(Value::Null),
            );
            d.insert(
                "audit_verdict".to_string(),
                u.get("audit_verdict").cloned().unwrap_or(Value::Null),
            );
            // `bool(u.get("audit_by_teacher"))`
            d.insert(
                "audit_by_teacher".to_string(),
                Value::Bool(value_truthy(u.get("audit_by_teacher"))),
            );
            d.insert(
                "audit_worker_model".to_string(),
                u.get("audit_worker_model").cloned().unwrap_or(Value::Null),
            );
            d.insert(
                "audit_teacher_model".to_string(),
                u.get("audit_teacher_model").cloned().unwrap_or(Value::Null),
            );
            d.insert(
                "audited_at".to_string(),
                u.get("audited_at").cloned().unwrap_or(Value::Null),
            );
            d.insert(
                "necessity".to_string(),
                u.get("necessity").cloned().unwrap_or(Value::Null),
            );
            seen_names.insert(sk.name.clone());
            out.push(d);
        }
        // Legacy JSON entries — surfaced as draft, not editable from new flow
        if os::path::exists(&self.legacy_file) {
            if let Ok(text) = std::fs::read_to_string(&self.legacy_file) {
                if let Ok(Value::Array(rows)) = serde_json::from_str::<Value>(&text) {
                    for row in &rows {
                        let row = match row {
                            Value::Object(m) => m,
                            _ => continue,
                        };
                        // `name = slugify(row.get("title") or row.get("id") or "skill")`
                        let name_src = first_truthy_str(row, &["title", "id"], "skill");
                        let name = slugify(&name_src, "skill");
                        if seen_names.contains(&name) {
                            continue;
                        }
                        out.push(legacy_entry(row, &name));
                    }
                }
                // `except Exception: pass` — a malformed legacy file is ignored.
            }
        }
        out
    }

    /// `load(owner)` — `load_all` filtered to the given owner.
    ///
    /// SECURITY: strict ownership filter. The previous predicate also included
    /// skills with NO owner field (`not s.get("owner")`), which leaked legacy /
    /// un-stamped skills to every authenticated user. Hide them now; the owner
    /// needs to be backfilled on disk if those skills should be visible to a
    /// specific user.
    pub fn load(&self, owner: Option<&str>) -> Vec<Map<String, Value>> {
        let entries = self.load_all();
        match owner {
            None => entries,
            Some(o) => entries
                .into_iter()
                .filter(|s| matches!(s.get("owner"), Some(Value::String(v)) if v == o))
                .collect(),
        }
    }

    // ----------------------------------------------------------------------
    // CRUD — disk-backed
    // ----------------------------------------------------------------------

    /// `add_skill(...)` — create a new disk-backed skill (with free
    /// dedup-at-creation for non-`user` sources + name-collision suffixing).
    ///
    /// The Python signature has ~22 keyword arguments; the Rust port groups them
    /// into [`AddSkill`] so call-sites read like the Python kwargs and unspecified
    /// fields take the documented defaults.
    pub fn add_skill(&self, args: AddSkill) -> Map<String, Value> {
        // Normalize name: `slugify(name or title or description or "skill")`
        let name_src = first_nonempty(&[
            args.name.as_deref().unwrap_or(""),
            args.title.as_str(),
            args.description.as_deref().unwrap_or(""),
        ]);
        let mut nm = slugify(if name_src.is_empty() { "skill" } else { name_src }, "skill");

        // Free dedup-at-creation (always, no API): for LLM-authored skills, skip
        // if a near-identical skill already exists (Jaccard over
        // name+description+when_to_use+procedure). User-authored skills are never
        // auto-skipped — a human asked for it. The every-X AI audit handles the
        // fuzzier near-duplicates this cheap check won't catch.
        let all = self.load_all();
        // `_dedup_pool = _all if owner is None else [s for s in _all if s.get("owner") == owner]`
        // Only de-duplicate against skills owned by the same owner (None ->
        // whole library). Name-collision suffixing below still scans `_all`.
        let dedup_pool: Vec<&Map<String, Value>> = match args.owner.as_deref() {
            None => all.iter().collect(),
            Some(o) => all
                .iter()
                .filter(|s| matches!(s.get("owner"), Some(Value::String(v)) if v == o))
                .collect(),
        };
        if args.source != "user" {
            // `description or title or ""`
            let desc_or_title =
                first_nonempty(&[args.description.as_deref().unwrap_or(""), args.title.as_str()])
                    .to_string();
            // `when_to_use if when_to_use is not None else (problem or "")`
            let when = match &args.when_to_use {
                Some(w) => w.clone(),
                None => args.problem.clone(),
            };
            // `procedure if procedure is not None else (steps or [])`
            let proc: Vec<String> = match &args.procedure {
                Some(p) => p.clone(),
                None => args.steps.clone().unwrap_or_default(),
            };
            let cand = tokenize(&[nm.clone(), desc_or_title, when, proc.join(" ")].join(" "));
            if !cand.is_empty() {
                for s in &dedup_pool {
                    let s: &Map<String, Value> = s;
                    let ex = tokenize(
                        &[
                            get_str(s, "name"),
                            get_str(s, "description"),
                            get_str(s, "when_to_use"),
                            get_str_list(s, "procedure").join(" "),
                        ]
                        .join(" "),
                    );
                    if jaccard(&cand, &ex) >= 0.82 {
                        // Near-identical — don't grow the library; bump the
                        // existing skill's usage and return it so the caller
                        // knows it already exists.
                        let existing_name = get_str(s, "name");
                        // `self.record_use(s["name"], owner=s.get("owner"))`
                        let existing_owner = match s.get("owner") {
                            Some(Value::String(v)) => Some(v.clone()),
                            _ => None,
                        };
                        // `try: self.record_use(...) except Exception: pass`
                        self.record_use(&existing_name, existing_owner.as_deref());
                        // `return {**s, "_deduped": True, "_duplicate_of": s.get("name")}`
                        let mut dup = s.clone();
                        dup.insert("_deduped".to_string(), Value::Bool(true));
                        dup.insert(
                            "_duplicate_of".to_string(),
                            s.get("name").cloned().unwrap_or(Value::Null),
                        );
                        return dup;
                    }
                }
            }
        }

        // Avoid clobbering an existing skill with the same name
        let existing: HashSet<String> = all.iter().map(|s| get_str(s, "name")).collect();
        let base = nm.clone();
        let mut i = 2;
        while existing.contains(&nm) {
            nm = format!("{base}-{i}");
            i += 1;
        }

        // `when_to_use if when_to_use is not None else (problem or "")`
        let when_to_use = match &args.when_to_use {
            Some(w) => w.clone(),
            None => args.problem.clone(),
        };
        // `procedure if procedure is not None else (steps or [])`
        let procedure: Vec<String> = match &args.procedure {
            Some(p) => p.clone(),
            None => args.steps.clone().unwrap_or_default(),
        };
        // `solution if solution and not procedure else ""`
        let body_extra = if !args.solution.is_empty() && procedure.is_empty() {
            args.solution.clone()
        } else {
            String::new()
        };
        // `description=(description or title or "").strip()`
        let description =
            first_nonempty(&[args.description.as_deref().unwrap_or(""), args.title.as_str()])
                .trim()
                .to_string();

        let mut sk = Skill {
            name: nm,
            description,
            version: args.version.clone(),
            category: if args.category.is_empty() {
                "general".to_string()
            } else {
                args.category.clone()
            },
            tags: args.tags.clone().unwrap_or_default(),
            platforms: args.platforms.clone().unwrap_or_default(),
            requires_toolsets: args.requires_toolsets.clone().unwrap_or_default(),
            fallback_for_toolsets: args.fallback_for_toolsets.clone().unwrap_or_default(),
            status: if args.status.is_empty() {
                "draft".to_string()
            } else {
                args.status.clone()
            },
            confidence: args.confidence,
            source: args.source.clone(),
            teacher_model: args.teacher_model.clone(),
            owner: args.owner.clone(),
            when_to_use,
            procedure,
            pitfalls: args.pitfalls.clone().unwrap_or_default(),
            verification: args.verification.clone().unwrap_or_default(),
            body_extra,
            ..Skill::default()
        };
        self.write_skill(&mut sk);
        sk.to_dict()
    }

    /// `update_skill(skill_id, updates, owner)` — `skill_id` is the slug name.
    /// Allows updating any field plus renames if `name` changes (file is moved on
    /// disk).
    ///
    /// The call is owner-scoped: it matches a skill on disk only if
    /// `skill.owner == owner` (string compare; both empty-string and `None` mean
    /// "ownerless"). When `owner is None` (the default), the call only matches
    /// skills whose own `owner` field is empty — callers that want to edit an
    /// owned skill must pass the matching owner explicitly. This prevents a caller
    /// with one owner from mutating a file owned by another user that happens to
    /// share the same slug across category directories. The `owner` key in
    /// `updates` is also ignored — ownership is not an editable field via this
    /// path; rename or admin tooling is required for that.
    pub fn update_skill(
        &self,
        skill_id: &str,
        updates: &Map<String, Value>,
        owner: Option<&str>,
    ) -> bool {
        for path in self.iter_skill_files() {
            let mut sk = match self.read_skill(&path) {
                Some(s) => s,
                None => continue,
            };
            if sk.name != skill_id {
                continue;
            }
            // `if (sk.owner or "") != (owner or ""): continue`
            if sk.owner.as_deref().unwrap_or("") != owner.unwrap_or("") {
                continue;
            }
            let old_dir = os::path::dirname(&path);

            // Apply updates in a Skill-shape friendly way (scalar keys).
            apply_scalar_updates(&mut sk, updates);
            // List keys.
            apply_list_updates(&mut sk, updates);

            // Old-schema field aliases
            if updates.contains_key("title") && !updates.contains_key("description") {
                sk.description = value_to_string(&updates["title"]);
            }
            if updates.contains_key("problem") && !updates.contains_key("when_to_use") {
                sk.when_to_use = value_to_string(&updates["problem"]);
            }
            if updates.contains_key("solution")
                && !updates.contains_key("body_extra")
                && sk.procedure.is_empty()
            {
                sk.body_extra = value_to_string(&updates["solution"]);
            }
            if updates.contains_key("steps") && !updates.contains_key("procedure") {
                sk.procedure = value_to_str_list(updates.get("steps"));
            }

            // Rename: `slugify(updates.get("name") or sk.name)`
            let rename_src = match updates.get("name") {
                Some(v) if value_truthy(Some(v)) => value_to_string(v),
                _ => sk.name.clone(),
            };
            let new_name = slugify(&rename_src, "skill");
            if new_name != sk.name {
                sk.name = new_name;
            }

            // Write to potentially new path
            let new_path = self.skill_file(&sk.category, &sk.name);
            if new_path != path {
                // Move the whole skill directory if rename or recategorize
                let new_dir = os::path::dirname(&new_path);
                if Path::new(&new_dir).is_dir() {
                    logger::warning(&format!("Skill rename target exists: {new_dir}"));
                    return false;
                }
                let _ = os::makedirs(&os::path::dirname(&new_dir), true);
                if std::fs::rename(&old_dir, &new_dir).is_err() {
                    // os.rename raised — Python would propagate; here a failed
                    // rename leaves the skill in place. Treat as a failed update.
                    return false;
                }
                // Also rename usage key (owner-scoped on both sides).
                let mut usage = self.load_usage();
                let old_usage_key = Self::usage_key(skill_id, sk.owner.as_deref());
                if usage.contains_key(&old_usage_key) {
                    // `usage[self._usage_key(sk.name, sk.owner)] = usage.pop(old_usage_key)`
                    if let Some(v) = usage.remove(&old_usage_key) {
                        usage.insert(Self::usage_key(&sk.name, sk.owner.as_deref()), v);
                    }
                    self.save_usage(&usage);
                }
            }
            self.write_skill(&mut sk);
            return true;
        }
        false
    }

    /// `delete_skill(skill_id, owner)` — remove the whole skill directory + usage
    /// key. Owner-scoped: only matches a skill whose `owner` equals the requested
    /// owner (empty-string and `None` both mean "ownerless").
    pub fn delete_skill(&self, skill_id: &str, owner: Option<&str>) -> bool {
        for path in self.iter_skill_files() {
            let sk = match self.read_skill(&path) {
                Some(s) => s,
                None => continue,
            };
            if sk.name != skill_id {
                continue;
            }
            // `if (sk.owner or "") != (owner or ""): continue`
            if sk.owner.as_deref().unwrap_or("") != owner.unwrap_or("") {
                continue;
            }
            let skill_dir = os::path::dirname(&path);
            // Remove the whole skill dir (Python walks topdown=False unlinking
            // files then rmdir-ing dirs; `remove_dir_all` is the faithful net
            // effect of removing the directory tree).
            if std::fs::remove_dir_all(&skill_dir).is_err() {
                logger::warning(&format!("Failed to remove skill dir {skill_dir}"));
                return false;
            }
            let mut usage = self.load_usage();
            // `usage_key = self._usage_key(skill_id, sk.owner)`
            let usage_key = Self::usage_key(skill_id, sk.owner.as_deref());
            if usage.contains_key(&usage_key) {
                usage.remove(&usage_key);
                self.save_usage(&usage);
            }
            return true;
        }
        false
    }

    /// `record_use(skill_id, owner)` — bump `uses` and stamp `last_used` (int
    /// seconds), keyed by the owner-scoped usage key.
    pub fn record_use(&self, skill_id: &str, owner: Option<&str>) {
        let mut usage = self.load_usage();
        // `key = self._usage_key(skill_id, owner)`
        let key = Self::usage_key(skill_id, owner);
        let entry = usage.entry(key).or_insert_with(default_usage_entry);
        if let Value::Object(e) = entry {
            // `entry["uses"] = int(entry.get("uses", 0)) + 1`
            let cur = get_i64(e, "uses", 0);
            e.insert(
                "uses".to_string(),
                Value::Number(serde_json::Number::from(cur + 1)),
            );
            // `entry["last_used"] = int(time.time())`
            e.insert(
                "last_used".to_string(),
                Value::Number(serde_json::Number::from(crate::pytime::time() as i64)),
            );
        }
        self.save_usage(&usage);
    }

    // ----------------------------------------------------------------------
    // Reading a single skill (used by the skill_view tool)
    // ----------------------------------------------------------------------

    /// `read_skill_md(name, owner)` — raw SKILL.md text for the named skill.
    /// Owner-scoped: skips a matching-name skill whose `owner` differs from the
    /// requested owner (empty-string and `None` both mean "ownerless").
    pub fn read_skill_md(&self, name: &str, owner: Option<&str>) -> Option<String> {
        for path in self.iter_skill_files() {
            if let Some(sk) = self.read_skill(&path) {
                if sk.name != name {
                    continue;
                }
                // `if (sk.owner or "") != (owner or ""): continue`
                if sk.owner.as_deref().unwrap_or("") != owner.unwrap_or("") {
                    continue;
                }
                return std::fs::read_to_string(&path).ok();
            }
        }
        None
    }

    /// `read_skill_reference(name, ref_path, owner)` — read a sub-file under the
    /// skill's directory (references/, etc). Refuses path traversal. Owner-scoped:
    /// skips a matching-name skill whose `owner` differs from the requested owner
    /// (empty-string and `None` both mean "ownerless").
    pub fn read_skill_reference(
        &self,
        name: &str,
        ref_path: &str,
        owner: Option<&str>,
    ) -> Option<String> {
        for path in self.iter_skill_files() {
            let sk = match self.read_skill(&path) {
                Some(s) => s,
                None => continue,
            };
            if sk.name != name {
                continue;
            }
            // `if (sk.owner or "") != (owner or ""): continue`
            if sk.owner.as_deref().unwrap_or("") != owner.unwrap_or("") {
                continue;
            }
            // `base = os.path.realpath(os.path.dirname(path))`
            let dir = os::path::dirname(&path);
            let base = crate::src::app_helpers::realpath(&dir);
            // `target = os.path.realpath(os.path.join(base, ref_path))`
            let target = crate::src::app_helpers::realpath(&os::path::join(&base, ref_path));
            // `if os.path.commonpath([base, target]) != base or target == os.path.dirname(path):`
            let common = crate::src::app_helpers::commonpath(&[&base, &target]);
            if common.as_deref() != Some(base.as_str()) || target == dir {
                return None;
            }
            // `if not os.path.isfile(target): return None`
            if !Path::new(&target).is_file() {
                return None;
            }
            return std::fs::read_to_string(&target).ok();
        }
        None
    }

    // ----------------------------------------------------------------------
    // Index — the lightweight summary injected into the system prompt
    // ----------------------------------------------------------------------

    /// `index_for(owner, *, active_toolsets, platform)` — return the
    /// `[{name, description, category, status}]` list the agent sees in its
    /// system prompt.
    ///
    /// Includes:
    ///   - All published skills.
    ///   - Drafts written by the teacher-escalation loop
    ///     (`source == "teacher-escalation"`). The whole point of the teacher
    ///     loop is for the student to find the new procedure on the very next
    ///     turn — waiting for a manual publish click defeats the loop.
    ///
    /// Excludes user-created drafts (status=draft, source != teacher-escalation)
    /// — those are work-in-progress and pollute the prompt with half-finished
    /// procedures.
    pub fn index_for(
        &self,
        owner: Option<&str>,
        active_toolsets: Option<&[String]>,
        platform: Option<&str>,
    ) -> Vec<Map<String, Value>> {
        let active_toolsets: Vec<String> = active_toolsets.map(|t| t.to_vec()).unwrap_or_default();
        let mut out: Vec<Map<String, Value>> = Vec::new();
        for s in self.load(owner) {
            // `status = s.get("status")` — may be a string or None.
            let status = match s.get("status") {
                Some(Value::String(v)) => Some(v.clone()),
                _ => None,
            };
            // Published + None (pre-status legacy) always included.
            // Drafts only if the teacher wrote them.
            // `if status not in ("published", None):`
            let is_published_or_none =
                matches!(status.as_deref(), Some("published")) || status.is_none();
            if !is_published_or_none {
                // `if status == "draft" and s.get("source") == "teacher-escalation": pass else: continue`
                let is_teacher_draft = matches!(status.as_deref(), Some("draft"))
                    && matches!(s.get("source"), Some(Value::String(src)) if src == "teacher-escalation");
                if !is_teacher_draft {
                    continue;
                }
            }
            // Platform gating: hide unless the platform is listed (when both set).
            if let Some(p) = platform {
                let platforms = get_str_list(&s, "platforms");
                if !platforms.is_empty() && !platforms.iter().any(|x| x == p) {
                    continue;
                }
            }
            // requires_toolsets: hide unless every required toolset is active.
            let req = get_str_list(&s, "requires_toolsets");
            if !req.is_empty() && !req.iter().all(|t| active_toolsets.contains(t)) {
                continue;
            }
            // fallback_for_toolsets: hide when any of those toolsets is active.
            let fb = get_str_list(&s, "fallback_for_toolsets");
            if !fb.is_empty() && fb.iter().any(|t| active_toolsets.contains(t)) {
                continue;
            }
            // `"description": s.get("description") or s.get("title", "")`
            let description = match s.get("description") {
                Some(Value::String(v)) if !v.is_empty() => v.clone(),
                _ => get_str(&s, "title"),
            };
            let mut entry = Map::new();
            entry.insert(
                "name".to_string(),
                s.get("name").cloned().unwrap_or(Value::Null),
            );
            entry.insert("description".to_string(), Value::String(description));
            // `s.get("category", "general")`
            let category = match s.get("category") {
                Some(Value::String(v)) => v.clone(),
                _ => "general".to_string(),
            };
            entry.insert("category".to_string(), Value::String(category));
            // `status or "published"`
            entry.insert(
                "status".to_string(),
                Value::String(status.unwrap_or_else(|| "published".to_string())),
            );
            out.push(entry);
        }
        // `out.sort(key=lambda x: (x["category"], x["name"]))`
        out.sort_by(|a, b| {
            let ca = get_str(a, "category");
            let cb = get_str(b, "category");
            ca.cmp(&cb)
                .then_with(|| get_str(a, "name").cmp(&get_str(b, "name")))
        });
        out
    }

    // ----------------------------------------------------------------------
    // Relevance search (kept for the existing /api/skills/search endpoint
    // and the `manage_skills` action="search"). Now operates on the new
    // field set.
    // ----------------------------------------------------------------------

    /// `get_relevant_skills(query, skills, threshold, max_items, min_confidence)`.
    pub fn get_relevant_skills(
        &self,
        query: &str,
        skills: Option<Vec<Map<String, Value>>>,
        threshold: f64,
        max_items: usize,
        min_confidence: f64,
    ) -> Vec<Map<String, Value>> {
        let mut skills = match skills {
            Some(s) => s,
            None => self.load_all(),
        };
        if skills.is_empty() || query.trim().is_empty() {
            return Vec::new();
        }
        // Consider published AND draft skills for relevance retrieval.
        // `[s for s in skills if s.get("status") in ("published", "draft")]`
        skills.retain(
            |s| matches!(s.get("status"), Some(Value::String(v)) if v == "published" || v == "draft"),
        );
        // Confidence gate (used by prompt-injection, NOT by search): a DRAFT
        // skill must clear the bar to be injected. Published skills are already
        // vetted, so they always qualify. Missing confidence = treat as 1.0
        // (legacy skills shouldn't silently vanish). 0 disables the gate.
        if min_confidence > 0.0 {
            skills.retain(|s| {
                // published -> always passes
                if matches!(s.get("status"), Some(Value::String(v)) if v == "published") {
                    return true;
                }
                // Teacher-escalation drafts are auto-written from a (possibly
                // untrusted) trace and injected as authoritative guidance, so they
                // must EARN injection with an explicit, parseable confidence that
                // clears the bar — FAIL CLOSED on a missing/garbage value instead
                // of treating it as 1.0. Hand-authored legacy drafts keep the
                // lenient "unset -> keep" behavior so they don't silently vanish.
                if matches!(s.get("source"), Some(Value::String(v)) if v == "teacher-escalation") {
                    // `c = s.get("confidence"); if c is None: return False`
                    match s.get("confidence") {
                        None | Some(Value::Null) => return false,
                        // `_to_float(c, 0.0) >= min_confidence` (unparseable -> fail closed)
                        c => return to_float(c, 0.0) >= min_confidence,
                    }
                }
                // `c = s.get("confidence"); if c is None: return True`
                match s.get("confidence") {
                    None | Some(Value::Null) => true,
                    // `_to_float(c, 1.0) >= min_confidence` (unparseable -> pass)
                    c => to_float(c, 1.0) >= min_confidence,
                }
            });
        }
        if skills.is_empty() {
            return Vec::new();
        }

        let query_lower = query.to_lowercase();
        let query_tokens = tokenize(query);
        let mut scored: Vec<(f64, Map<String, Value>)> = Vec::new();
        for sk in skills {
            let text = [
                get_str(&sk, "name"),
                get_str(&sk, "description"),
                get_str(&sk, "when_to_use"),
                get_str_list(&sk, "tags").join(" "),
                get_str_list(&sk, "procedure").join(" "),
            ]
            .join(" ");
            let mut score = jaccard(&query_tokens, &tokenize(&text));
            // Match tags as WHOLE TOKENS, not substrings: a query's tag tokens
            // must be a subset of the tag's tokens (`tag_tokens <= query_tokens`).
            // Substring matching boosted e.g. an "ai" tag for any query
            // containing "email".
            // `for tag in sk.get("tags", []) or []: tt = _tokenize(tag); if tt and tt <= query_tokens: score = max(score, 0.3) * 1.3`
            for tag in get_str_list(&sk, "tags") {
                let tag_tokens = tokenize(&tag);
                if !tag_tokens.is_empty() && tag_tokens.is_subset(&query_tokens) {
                    score = score.max(0.3) * 1.3;
                }
            }
            // `if query.lower() in (sk.get("description") or "").lower(): score = max(score, 0.6)`
            let desc_lower = get_str(&sk, "description").to_lowercase();
            if desc_lower.contains(&query_lower) {
                score = score.max(0.6);
            }
            // `score *= 1.0 + _to_float(sk.get("confidence"), 0.5) * 0.1`
            score *= 1.0 + to_float(sk.get("confidence"), 0.5) * 0.1;
            // `if sk.get("uses", 0) > 0: score *= 1.05`
            if get_i64(&sk, "uses", 0) > 0 {
                score *= 1.05;
            }
            if score >= threshold {
                scored.push((score, sk));
            }
        }
        // `scored.sort(key=lambda x: x[0], reverse=True)` — stable descending.
        scored.sort_by(|a, b| b.0.partial_cmp(&a.0).unwrap_or(std::cmp::Ordering::Equal));
        scored
            .into_iter()
            .take(max_items)
            .map(|(_, sk)| sk)
            .collect()
    }
}

// ---------------------------------------------------------------------------
// add_skill argument bundle
// ---------------------------------------------------------------------------

/// The keyword arguments of `SkillsManager.add_skill`.
///
/// Mirrors the Python kwargs (with their defaults). Construct with
/// [`AddSkill::default`] and override only the fields you need, matching the
/// call-site ergonomics of Python keyword arguments. `Option<Vec<...>>`
/// distinguishes "not passed" (`None`, falls back to the old-schema alias) from
/// "passed empty" — the Python code branches on `is not None` for `when_to_use`
/// and `procedure`.
#[derive(Debug, Clone)]
pub struct AddSkill {
    pub title: String,
    pub problem: String,
    pub solution: String,
    pub steps: Option<Vec<String>>,
    pub tags: Option<Vec<String>>,
    pub source: String,
    pub teacher_model: Option<String>,
    pub confidence: f64,
    pub session_id: Option<String>,
    pub owner: Option<String>,
    // New-schema fields (optional; fall back to old shape if absent)
    pub name: Option<String>,
    pub description: Option<String>,
    pub category: String,
    pub when_to_use: Option<String>,
    pub procedure: Option<Vec<String>>,
    pub pitfalls: Option<Vec<String>>,
    pub verification: Option<Vec<String>>,
    pub platforms: Option<Vec<String>>,
    pub requires_toolsets: Option<Vec<String>>,
    pub fallback_for_toolsets: Option<Vec<String>>,
    pub status: String,
    pub version: String,
}

impl Default for AddSkill {
    fn default() -> Self {
        // Mirrors the Python `add_skill` parameter defaults.
        AddSkill {
            title: String::new(),
            problem: String::new(),
            solution: String::new(),
            steps: None,
            tags: None,
            source: "learned".to_string(),
            teacher_model: None,
            confidence: 0.8,
            session_id: None,
            owner: None,
            name: None,
            description: None,
            category: "general".to_string(),
            when_to_use: None,
            procedure: None,
            pitfalls: None,
            verification: None,
            platforms: None,
            requires_toolsets: None,
            fallback_for_toolsets: None,
            status: "draft".to_string(),
            version: "1.0.0".to_string(),
        }
    }
}

// ---------------------------------------------------------------------------
// Module-private helpers (bridge the dynamic dict mutation in Python)
// ---------------------------------------------------------------------------

/// `{"uses": 0, "last_used": None}` — the `setdefault` skeleton.
fn default_usage_entry() -> Value {
    let mut e = Map::new();
    e.insert("uses".to_string(), Value::Number(serde_json::Number::from(0)));
    e.insert("last_used".to_string(), Value::Null);
    Value::Object(e)
}

/// A finite `f64` as a JSON number (used for `time.time()` floats).
fn float_value(x: f64) -> Value {
    serde_json::Number::from_f64(x)
        .map(Value::Number)
        .unwrap_or(Value::Null)
}

/// Python truthiness of an optional JSON value (`bool(u.get(...))`).
fn value_truthy(v: Option<&Value>) -> bool {
    match v {
        None | Some(Value::Null) => false,
        Some(Value::Bool(b)) => *b,
        Some(Value::Number(n)) => n.as_f64().map(|f| f != 0.0).unwrap_or(false),
        Some(Value::String(s)) => !s.is_empty(),
        Some(Value::Array(a)) => !a.is_empty(),
        Some(Value::Object(o)) => !o.is_empty(),
    }
}

/// `str(v)` for an arbitrary JSON value (Python-faithful for the cases here).
fn value_to_string(v: &Value) -> String {
    match v {
        Value::String(s) => s.clone(),
        Value::Bool(b) => {
            if *b {
                "True".to_string()
            } else {
                "False".to_string()
            }
        }
        Value::Null => "None".to_string(),
        Value::Number(n) => n.to_string(),
        other => other.to_string(),
    }
}

/// `list(v or [])` -> `Vec<String>` (faithful coercion for update aliases).
fn value_to_str_list(v: Option<&Value>) -> Vec<String> {
    match v {
        Some(Value::Array(arr)) => arr
            .iter()
            .map(|x| match x {
                Value::String(s) => s.clone(),
                other => other.to_string(),
            })
            .collect(),
        _ => Vec::new(),
    }
}

/// First non-empty string from the candidates, else `""`.
fn first_nonempty<'a>(candidates: &[&'a str]) -> &'a str {
    for c in candidates {
        if !c.is_empty() {
            return c;
        }
    }
    ""
}

/// `row.get(a) or row.get(b) or default` over string-ish values (truthy).
fn first_truthy_str(row: &Map<String, Value>, keys: &[&str], default: &str) -> String {
    for k in keys {
        if let Some(v) = row.get(*k) {
            if value_truthy(Some(v)) {
                return value_to_string(v);
            }
        }
    }
    default.to_string()
}

/// Apply the scalar-key updates in `update_skill` (`setattr(sk, k, updates[k])`).
fn apply_scalar_updates(sk: &mut Skill, updates: &Map<String, Value>) {
    // ("description", "version", "category", "status", "confidence", "source",
    //  "teacher_model", "when_to_use", "body_extra") — `owner` is intentionally
    // NOT in this list: ownership is identity, not a mutable column, so a
    // stray `owner` key in `updates` is ignored. "confidence" is float-shaped;
    // the rest are string-shaped. Mirror `setattr` for each present key.
    if let Some(v) = updates.get("description") {
        sk.description = value_to_string(v);
    }
    if let Some(v) = updates.get("version") {
        sk.version = value_to_string(v);
    }
    if let Some(v) = updates.get("category") {
        sk.category = value_to_string(v);
    }
    if let Some(v) = updates.get("status") {
        sk.status = value_to_string(v);
    }
    if let Some(v) = updates.get("confidence") {
        // setattr stores whatever was passed; the dataclass field is float and
        // to_frontmatter/to_dict coerce via `float(...)`. Coerce here so the
        // stored value round-trips like Python's eventual `float(self.confidence)`.
        sk.confidence = to_float(Some(v), sk.confidence);
    }
    if let Some(v) = updates.get("source") {
        sk.source = value_to_string(v);
    }
    if let Some(v) = updates.get("teacher_model") {
        sk.teacher_model = match v {
            Value::Null => None,
            other => Some(value_to_string(other)),
        };
    }
    if let Some(v) = updates.get("when_to_use") {
        sk.when_to_use = value_to_string(v);
    }
    if let Some(v) = updates.get("body_extra") {
        sk.body_extra = value_to_string(v);
    }
}

/// Apply the list-key updates in `update_skill` (`setattr(sk, k, list(updates[k] or []))`).
fn apply_list_updates(sk: &mut Skill, updates: &Map<String, Value>) {
    if updates.contains_key("tags") {
        sk.tags = value_to_str_list(updates.get("tags"));
    }
    if updates.contains_key("procedure") {
        sk.procedure = value_to_str_list(updates.get("procedure"));
    }
    if updates.contains_key("pitfalls") {
        sk.pitfalls = value_to_str_list(updates.get("pitfalls"));
    }
    if updates.contains_key("verification") {
        sk.verification = value_to_str_list(updates.get("verification"));
    }
    if updates.contains_key("platforms") {
        sk.platforms = value_to_str_list(updates.get("platforms"));
    }
    if updates.contains_key("requires_toolsets") {
        sk.requires_toolsets = value_to_str_list(updates.get("requires_toolsets"));
    }
    if updates.contains_key("fallback_for_toolsets") {
        sk.fallback_for_toolsets = value_to_str_list(updates.get("fallback_for_toolsets"));
    }
}

/// Build a legacy `data/skills.json` row's surfaced dict (read-only draft).
fn legacy_entry(row: &Map<String, Value>, name: &str) -> Map<String, Value> {
    let mut d = Map::new();
    // `"id": row.get("id") or name`
    let id = match row.get("id") {
        Some(v) if value_truthy(Some(v)) => v.clone(),
        _ => Value::String(name.to_string()),
    };
    d.insert("id".to_string(), id);
    d.insert("name".to_string(), Value::String(name.to_string()));
    d.insert(
        "description".to_string(),
        Value::String(get_str(row, "title")),
    );
    d.insert("version".to_string(), Value::String("0.0.1".to_string()));
    d.insert("category".to_string(), Value::String("legacy".to_string()));
    // `row.get("tags") or []`
    d.insert("tags".to_string(), or_empty_array(row.get("tags")));
    // `row.get("status") or "draft"`
    d.insert("status".to_string(), or_string(row.get("status"), "draft"));
    // `row.get("confidence", 0.5)`
    d.insert(
        "confidence".to_string(),
        row.get("confidence").cloned().unwrap_or(float_value(0.5)),
    );
    // `row.get("source", "imported")`
    d.insert(
        "source".to_string(),
        row.get("source")
            .cloned()
            .unwrap_or(Value::String("imported".to_string())),
    );
    d.insert(
        "owner".to_string(),
        row.get("owner").cloned().unwrap_or(Value::Null),
    );
    d.insert(
        "when_to_use".to_string(),
        Value::String(get_str(row, "problem")),
    );
    // `row.get("steps") or []`
    d.insert("procedure".to_string(), or_empty_array(row.get("steps")));
    d.insert("pitfalls".to_string(), Value::Array(Vec::new()));
    d.insert("verification".to_string(), Value::Array(Vec::new()));
    d.insert(
        "body_extra".to_string(),
        Value::String(get_str(row, "solution")),
    );
    d.insert("title".to_string(), Value::String(get_str(row, "title")));
    d.insert(
        "problem".to_string(),
        Value::String(get_str(row, "problem")),
    );
    d.insert(
        "solution".to_string(),
        Value::String(get_str(row, "solution")),
    );
    d.insert("steps".to_string(), or_empty_array(row.get("steps")));
    // `row.get("uses", 0)`
    d.insert(
        "uses".to_string(),
        row.get("uses")
            .cloned()
            .unwrap_or(Value::Number(serde_json::Number::from(0))),
    );
    d.insert(
        "last_used".to_string(),
        row.get("last_used").cloned().unwrap_or(Value::Null),
    );
    d.insert("_legacy".to_string(), Value::Bool(true));
    d
}

/// `v or []` — keep an array value, else an empty array.
fn or_empty_array(v: Option<&Value>) -> Value {
    match v {
        Some(arr @ Value::Array(_)) if value_truthy(Some(arr)) => arr.clone(),
        _ => Value::Array(Vec::new()),
    }
}

/// `v or default` for a string-shaped value.
fn or_string(v: Option<&Value>, default: &str) -> Value {
    match v {
        Some(val) if value_truthy(Some(val)) => val.clone(),
        _ => Value::String(default.to_string()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicU64, Ordering};

    static COUNTER: AtomicU64 = AtomicU64::new(0);

    /// A unique temp data_dir per test (so concurrent tests don't collide).
    fn temp_dir() -> String {
        let n = COUNTER.fetch_add(1, Ordering::SeqCst);
        let base = std::env::temp_dir().join(format!("odysseus-skills-test-{}-{}", os::getpid(), n));
        base.to_string_lossy().into_owned()
    }

    fn cleanup(dir: &str) {
        let _ = std::fs::remove_dir_all(dir);
    }

    #[test]
    fn tokenize_and_jaccard() {
        let a = tokenize("Open a PR from branch.");
        // 1-char tokens dropped ("a"); punctuation stripped.
        assert!(a.contains("open"));
        assert!(a.contains("pr"));
        assert!(a.contains("from"));
        assert!(a.contains("branch"));
        assert!(!a.contains("a"));
        let b = tokenize("open pr");
        assert!(jaccard(&a, &b) > 0.0);
        assert_eq!(jaccard(&a, &HashSet::new()), 0.0);
    }

    #[test]
    fn add_load_and_index() {
        let dir = temp_dir();
        let mgr = SkillsManager::new(&dir);
        let d = mgr.add_skill(AddSkill {
            title: "Open PR From Branch".to_string(),
            when_to_use: Some("When you need a PR".to_string()),
            procedure: Some(vec!["push".to_string(), "gh pr create".to_string()]),
            status: "published".to_string(),
            owner: Some("alice".to_string()),
            ..AddSkill::default()
        });
        assert_eq!(
            d.get("name").unwrap(),
            &Value::String("open-pr-from-branch".to_string())
        );

        // load_all sees it
        let all = mgr.load_all();
        assert_eq!(all.len(), 1);
        // load(owner) strict-filters
        assert_eq!(mgr.load(Some("alice")).len(), 1);
        assert_eq!(mgr.load(Some("bob")).len(), 0);

        // index_for: published skill shows
        let idx = mgr.index_for(Some("alice"), None, None);
        assert_eq!(idx.len(), 1);
        assert_eq!(
            idx[0].get("status").unwrap(),
            &Value::String("published".to_string())
        );
        cleanup(&dir);
    }

    #[test]
    fn dedup_for_non_user_source() {
        let dir = temp_dir();
        let mgr = SkillsManager::new(&dir);
        let _ = mgr.add_skill(AddSkill {
            title: "Resize an image with ffmpeg quickly".to_string(),
            when_to_use: Some("resize image ffmpeg".to_string()),
            procedure: Some(vec!["ffmpeg -i in out".to_string()]),
            source: "learned".to_string(),
            ..AddSkill::default()
        });
        // Near-identical learned skill -> deduped (bumps existing, returns it).
        let d = mgr.add_skill(AddSkill {
            title: "Resize an image with ffmpeg quickly".to_string(),
            when_to_use: Some("resize image ffmpeg".to_string()),
            procedure: Some(vec!["ffmpeg -i in out".to_string()]),
            source: "learned".to_string(),
            ..AddSkill::default()
        });
        assert_eq!(d.get("_deduped"), Some(&Value::Bool(true)));
        assert_eq!(mgr.load_all().len(), 1);
        cleanup(&dir);
    }

    #[test]
    fn user_source_never_deduped() {
        let dir = temp_dir();
        let mgr = SkillsManager::new(&dir);
        let _ = mgr.add_skill(AddSkill {
            title: "Resize image".to_string(),
            source: "user".to_string(),
            ..AddSkill::default()
        });
        // Same user-authored skill -> NOT deduped, name-suffixed instead.
        let d = mgr.add_skill(AddSkill {
            title: "Resize image".to_string(),
            source: "user".to_string(),
            ..AddSkill::default()
        });
        assert_eq!(d.get("_deduped"), None);
        assert_eq!(
            d.get("name").unwrap(),
            &Value::String("resize-image-2".to_string())
        );
        assert_eq!(mgr.load_all().len(), 2);
        cleanup(&dir);
    }

    #[test]
    fn record_use_increments() {
        let dir = temp_dir();
        let mgr = SkillsManager::new(&dir);
        let _ = mgr.add_skill(AddSkill {
            title: "Tail logs".to_string(),
            status: "published".to_string(),
            ..AddSkill::default()
        });
        mgr.record_use("tail-logs", None);
        mgr.record_use("tail-logs", None);
        let all = mgr.load_all();
        assert_eq!(
            all[0].get("uses").unwrap(),
            &Value::Number(serde_json::Number::from(2))
        );
        assert!(all[0].get("last_used").unwrap().is_number());
        cleanup(&dir);
    }

    #[test]
    fn update_rename_moves_dir_and_usage() {
        let dir = temp_dir();
        let mgr = SkillsManager::new(&dir);
        let _ = mgr.add_skill(AddSkill {
            title: "Old Name".to_string(),
            status: "published".to_string(),
            ..AddSkill::default()
        });
        mgr.record_use("old-name", None);
        let mut updates = Map::new();
        updates.insert("name".to_string(), Value::String("New Name".to_string()));
        assert!(mgr.update_skill("old-name", &updates, None));
        let all = mgr.load_all();
        assert_eq!(all.len(), 1);
        assert_eq!(
            all[0].get("name").unwrap(),
            &Value::String("new-name".to_string())
        );
        // usage key moved across the rename
        assert_eq!(
            all[0].get("uses").unwrap(),
            &Value::Number(serde_json::Number::from(1))
        );
        cleanup(&dir);
    }

    #[test]
    fn delete_removes_skill() {
        let dir = temp_dir();
        let mgr = SkillsManager::new(&dir);
        let _ = mgr.add_skill(AddSkill {
            title: "Doomed".to_string(),
            ..AddSkill::default()
        });
        assert_eq!(mgr.load_all().len(), 1);
        assert!(mgr.delete_skill("doomed", None));
        assert_eq!(mgr.load_all().len(), 0);
        assert!(!mgr.delete_skill("doomed", None)); // already gone
        cleanup(&dir);
    }

    #[test]
    fn index_hides_user_drafts_but_keeps_teacher_drafts() {
        let dir = temp_dir();
        let mgr = SkillsManager::new(&dir);
        // user draft -> hidden
        let _ = mgr.add_skill(AddSkill {
            title: "User WIP".to_string(),
            status: "draft".to_string(),
            source: "user".to_string(),
            owner: Some("alice".to_string()),
            ..AddSkill::default()
        });
        // teacher-escalation draft -> shown
        let _ = mgr.add_skill(AddSkill {
            title: "Teacher Draft".to_string(),
            status: "draft".to_string(),
            source: "teacher-escalation".to_string(),
            owner: Some("alice".to_string()),
            ..AddSkill::default()
        });
        let idx = mgr.index_for(Some("alice"), None, None);
        assert_eq!(idx.len(), 1);
        assert_eq!(
            idx[0].get("name").unwrap(),
            &Value::String("teacher-draft".to_string())
        );
        cleanup(&dir);
    }

    #[test]
    fn index_toolset_gating() {
        let dir = temp_dir();
        let mgr = SkillsManager::new(&dir);
        let _ = mgr.add_skill(AddSkill {
            title: "Needs git".to_string(),
            status: "published".to_string(),
            owner: Some("alice".to_string()),
            requires_toolsets: Some(vec!["git".to_string()]),
            ..AddSkill::default()
        });
        // git not active -> hidden
        assert_eq!(mgr.index_for(Some("alice"), Some(&[]), None).len(), 0);
        // git active -> shown
        assert_eq!(
            mgr.index_for(Some("alice"), Some(&["git".to_string()]), None)
                .len(),
            1
        );
        cleanup(&dir);
    }

    #[test]
    fn relevance_search() {
        let dir = temp_dir();
        let mgr = SkillsManager::new(&dir);
        let _ = mgr.add_skill(AddSkill {
            title: "Resize an image with ffmpeg".to_string(),
            description: Some("Resize an image with ffmpeg".to_string()),
            when_to_use: Some("when you need to resize an image".to_string()),
            tags: Some(vec!["ffmpeg".to_string(), "image".to_string()]),
            status: "published".to_string(),
            ..AddSkill::default()
        });
        let hits = mgr.get_relevant_skills("resize an image", None, 0.3, 5, 0.0);
        assert_eq!(hits.len(), 1);
        // unrelated query -> no hits above threshold
        let none = mgr.get_relevant_skills("quantum chromodynamics", None, 0.3, 5, 0.0);
        assert_eq!(none.len(), 0);
        cleanup(&dir);
    }

    #[test]
    fn read_skill_reference_blocks_traversal() {
        let dir = temp_dir();
        let mgr = SkillsManager::new(&dir);
        let _ = mgr.add_skill(AddSkill {
            title: "With Ref".to_string(),
            ..AddSkill::default()
        });
        // Write a reference file inside the skill dir.
        let skill_dir = os::path::join(&os::path::join(&mgr.skills_root, "general"), "with-ref");
        let refs = os::path::join(&skill_dir, "references");
        let _ = os::makedirs(&refs, true);
        let _ = std::fs::write(os::path::join(&refs, "note.md"), "hello ref");
        // legitimate read
        assert_eq!(
            mgr.read_skill_reference("with-ref", "references/note.md", None)
                .as_deref(),
            Some("hello ref")
        );
        // traversal blocked
        assert_eq!(
            mgr.read_skill_reference("with-ref", "../../../etc/passwd", None),
            None
        );
        // the skill dir itself is refused (target == dirname(path))
        assert_eq!(mgr.read_skill_reference("with-ref", ".", None), None);
        cleanup(&dir);
    }

    #[test]
    fn legacy_json_entries_surface() {
        let dir = temp_dir();
        let _ = os::makedirs(&dir, true);
        let legacy = serde_json::json!([
            {"id": "old1", "title": "Legacy Skill", "problem": "p", "solution": "s",
             "steps": ["one", "two"], "tags": ["t"], "uses": 3}
        ]);
        let _ = std::fs::write(
            os::path::join(&dir, "skills.json"),
            serde_json::to_string(&legacy).unwrap(),
        );
        let mgr = SkillsManager::new(&dir);
        let all = mgr.load_all();
        assert_eq!(all.len(), 1);
        assert_eq!(
            all[0].get("category").unwrap(),
            &Value::String("legacy".to_string())
        );
        assert_eq!(all[0].get("_legacy").unwrap(), &Value::Bool(true));
        assert_eq!(all[0].get("steps").unwrap(), &serde_json::json!(["one", "two"]));
        cleanup(&dir);
    }

    #[test]
    fn usage_key_owner_scoping() {
        // `_usage_key(name, owner)` == `f"{owner}::{name}"` when owner truthy,
        // else the bare name (empty-string owner collapses to bare name).
        assert_eq!(SkillsManager::usage_key("foo", None), "foo");
        assert_eq!(SkillsManager::usage_key("foo", Some("")), "foo");
        assert_eq!(SkillsManager::usage_key("foo", Some("alice")), "alice::foo");
    }

    #[test]
    fn record_use_keyed_by_owner() {
        // The usage sidecar is keyed by usage_key(name, owner): bumping a skill
        // under one owner must not leak into another owner's counter. (Note the
        // second skill's slug is suffixed because name-collision suffixing scans
        // the whole library, so we bump alice's by her actual slug.)
        let dir = temp_dir();
        let mgr = SkillsManager::new(&dir);
        let a = mgr.add_skill(AddSkill {
            title: "Shared Name".to_string(),
            status: "published".to_string(),
            owner: Some("alice".to_string()),
            ..AddSkill::default()
        });
        let b = mgr.add_skill(AddSkill {
            title: "Shared Name".to_string(),
            status: "published".to_string(),
            owner: Some("bob".to_string()),
            ..AddSkill::default()
        });
        let alice_name = get_str(&a, "name");
        let bob_name = get_str(&b, "name");

        // Bump only alice's, keyed by her owner.
        mgr.record_use(&alice_name, Some("alice"));
        mgr.record_use(&alice_name, Some("alice"));

        let alice = mgr.load(Some("alice"));
        assert_eq!(alice.len(), 1);
        assert_eq!(
            alice[0].get("uses").unwrap(),
            &Value::Number(serde_json::Number::from(2))
        );
        let bob = mgr.load(Some("bob"));
        assert_eq!(bob.len(), 1);
        assert_eq!(bob[0].get("name").unwrap(), &Value::String(bob_name));
        // bob's counter is untouched
        assert_eq!(
            bob[0].get("uses").unwrap(),
            &Value::Number(serde_json::Number::from(0))
        );
        cleanup(&dir);
    }

    #[test]
    fn update_and_delete_are_owner_scoped() {
        let dir = temp_dir();
        let mgr = SkillsManager::new(&dir);
        let _ = mgr.add_skill(AddSkill {
            title: "Owned Skill".to_string(),
            status: "published".to_string(),
            owner: Some("alice".to_string()),
            ..AddSkill::default()
        });
        // Wrong owner -> no match.
        let mut updates = Map::new();
        updates.insert(
            "description".to_string(),
            Value::String("hacked".to_string()),
        );
        assert!(!mgr.update_skill("owned-skill", &updates, Some("bob")));
        // No owner passed -> still no match (skill owner is "alice", not "").
        assert!(!mgr.update_skill("owned-skill", &updates, None));
        // Correct owner -> match.
        assert!(mgr.update_skill("owned-skill", &updates, Some("alice")));
        assert_eq!(
            mgr.load(Some("alice"))[0].get("description").unwrap(),
            &Value::String("hacked".to_string())
        );

        // delete: wrong owner refused, correct owner succeeds.
        assert!(!mgr.delete_skill("owned-skill", Some("bob")));
        assert!(mgr.delete_skill("owned-skill", Some("alice")));
        assert_eq!(mgr.load_all().len(), 0);
        cleanup(&dir);
    }

    #[test]
    fn update_ignores_owner_key() {
        // `owner` is identity, not a mutable column: an `owner` key in `updates`
        // must be ignored.
        let dir = temp_dir();
        let mgr = SkillsManager::new(&dir);
        let _ = mgr.add_skill(AddSkill {
            title: "Keep Owner".to_string(),
            status: "published".to_string(),
            owner: Some("alice".to_string()),
            ..AddSkill::default()
        });
        let mut updates = Map::new();
        updates.insert("owner".to_string(), Value::String("mallory".to_string()));
        updates.insert("status".to_string(), Value::String("draft".to_string()));
        assert!(mgr.update_skill("keep-owner", &updates, Some("alice")));
        // Owner unchanged; the other scalar update still applied.
        let alice = mgr.load(Some("alice"));
        assert_eq!(alice.len(), 1);
        assert_eq!(
            alice[0].get("status").unwrap(),
            &Value::String("draft".to_string())
        );
        assert!(mgr.load(Some("mallory")).is_empty());
        cleanup(&dir);
    }

    #[test]
    fn read_skill_md_and_reference_owner_scoped() {
        let dir = temp_dir();
        let mgr = SkillsManager::new(&dir);
        let _ = mgr.add_skill(AddSkill {
            title: "Secret Skill".to_string(),
            owner: Some("alice".to_string()),
            ..AddSkill::default()
        });
        // Correct owner can read the markdown; wrong owner / no owner cannot.
        assert!(mgr.read_skill_md("secret-skill", Some("alice")).is_some());
        assert!(mgr.read_skill_md("secret-skill", Some("bob")).is_none());
        assert!(mgr.read_skill_md("secret-skill", None).is_none());

        // Reference reads are owner-scoped too.
        let skill_dir =
            os::path::join(&os::path::join(&mgr.skills_root, "general"), "secret-skill");
        let refs = os::path::join(&skill_dir, "references");
        let _ = os::makedirs(&refs, true);
        let _ = std::fs::write(os::path::join(&refs, "note.md"), "owned ref");
        assert_eq!(
            mgr.read_skill_reference("secret-skill", "references/note.md", Some("alice"))
                .as_deref(),
            Some("owned ref")
        );
        assert!(mgr
            .read_skill_reference("secret-skill", "references/note.md", Some("bob"))
            .is_none());
        cleanup(&dir);
    }

    #[test]
    fn dedup_pool_is_owner_scoped() {
        // A near-identical skill owned by a DIFFERENT owner must NOT trigger
        // dedup — the candidate pool is filtered by owner.
        let dir = temp_dir();
        let mgr = SkillsManager::new(&dir);
        let _ = mgr.add_skill(AddSkill {
            title: "Resize an image with ffmpeg quickly".to_string(),
            when_to_use: Some("resize image ffmpeg".to_string()),
            procedure: Some(vec!["ffmpeg -i in out".to_string()]),
            source: "learned".to_string(),
            owner: Some("alice".to_string()),
            ..AddSkill::default()
        });
        // Same content, different owner -> NOT deduped (creates a new skill).
        let d = mgr.add_skill(AddSkill {
            title: "Resize an image with ffmpeg quickly".to_string(),
            when_to_use: Some("resize image ffmpeg".to_string()),
            procedure: Some(vec!["ffmpeg -i in out".to_string()]),
            source: "learned".to_string(),
            owner: Some("bob".to_string()),
            ..AddSkill::default()
        });
        assert_eq!(d.get("_deduped"), None);
        assert_eq!(mgr.load_all().len(), 2);

        // Same content, SAME owner -> deduped.
        let d2 = mgr.add_skill(AddSkill {
            title: "Resize an image with ffmpeg quickly".to_string(),
            when_to_use: Some("resize image ffmpeg".to_string()),
            procedure: Some(vec!["ffmpeg -i in out".to_string()]),
            source: "learned".to_string(),
            owner: Some("alice".to_string()),
            ..AddSkill::default()
        });
        assert_eq!(d2.get("_deduped"), Some(&Value::Bool(true)));
        assert_eq!(mgr.load_all().len(), 2);
        cleanup(&dir);
    }

    #[test]
    fn teacher_escalation_draft_fails_closed_on_missing_confidence() {
        // A teacher-escalation DRAFT with a missing/unparseable confidence must
        // be REJECTED when a min_confidence gate is active (fail closed), while a
        // hand-authored legacy draft with no source keeps the lenient behavior.
        let mut teacher = Map::new();
        teacher.insert("name".to_string(), Value::String("teach".to_string()));
        teacher.insert(
            "description".to_string(),
            Value::String("resize an image".to_string()),
        );
        teacher.insert("status".to_string(), Value::String("draft".to_string()));
        teacher.insert(
            "source".to_string(),
            Value::String("teacher-escalation".to_string()),
        );
        // No "confidence" key at all -> fail closed.

        let dir = temp_dir();
        let mgr = SkillsManager::new(&dir);
        let hits = mgr.get_relevant_skills(
            "resize an image",
            Some(vec![teacher.clone()]),
            0.3,
            5,
            0.5, // gate active
        );
        assert_eq!(hits.len(), 0, "missing-confidence teacher draft must fail closed");

        // Unparseable confidence -> also fail closed.
        let mut teacher_garbage = teacher.clone();
        teacher_garbage.insert(
            "confidence".to_string(),
            Value::String("not-a-number".to_string()),
        );
        let hits2 = mgr.get_relevant_skills(
            "resize an image",
            Some(vec![teacher_garbage]),
            0.3,
            5,
            0.5,
        );
        assert_eq!(hits2.len(), 0, "garbage-confidence teacher draft must fail closed");

        // Explicit confidence clearing the bar -> passes.
        let mut teacher_ok = teacher.clone();
        teacher_ok.insert("confidence".to_string(), float_value(0.9));
        let hits3 =
            mgr.get_relevant_skills("resize an image", Some(vec![teacher_ok]), 0.3, 5, 0.5);
        assert_eq!(hits3.len(), 1);

        // A non-teacher legacy draft with NO confidence keeps the lenient
        // "unset -> keep" behavior even under the gate.
        let mut legacy = Map::new();
        legacy.insert("name".to_string(), Value::String("legacy".to_string()));
        legacy.insert(
            "description".to_string(),
            Value::String("resize an image".to_string()),
        );
        legacy.insert("status".to_string(), Value::String("draft".to_string()));
        // no source, no confidence
        let hits4 =
            mgr.get_relevant_skills("resize an image", Some(vec![legacy]), 0.3, 5, 0.5);
        assert_eq!(hits4.len(), 1, "legacy draft without confidence stays lenient");
        cleanup(&dir);
    }

    #[test]
    fn tag_matching_is_whole_token_subset() {
        // Whole-token subset matching: an "ai" tag must NOT match a query that
        // merely contains the substring "ai" inside "email"; a multi-word tag
        // matches only when all its tokens are present as whole query tokens.
        let dir = temp_dir();
        let mgr = SkillsManager::new(&dir);

        // Skill with an "ai" tag and otherwise-unrelated body.
        let mut ai_skill = Map::new();
        ai_skill.insert("name".to_string(), Value::String("zzz".to_string()));
        ai_skill.insert(
            "description".to_string(),
            Value::String("quantum chromodynamics".to_string()),
        );
        ai_skill.insert("status".to_string(), Value::String("published".to_string()));
        ai_skill.insert(
            "tags".to_string(),
            Value::Array(vec![Value::String("ai".to_string())]),
        );

        // Substring match would have boosted this for "send an email"; whole-
        // token matching must NOT (the query has no standalone "ai" token).
        let hits = mgr.get_relevant_skills(
            "send an email",
            Some(vec![ai_skill.clone()]),
            0.3,
            5,
            0.0,
        );
        assert_eq!(hits.len(), 0, "substring 'ai' inside 'email' must not match");

        // A query with a standalone "ai" token DOES match (subset satisfied).
        let hits2 = mgr.get_relevant_skills(
            "ai workflows",
            Some(vec![ai_skill]),
            0.3,
            5,
            0.0,
        );
        assert_eq!(hits2.len(), 1, "standalone 'ai' token must match the tag");
        cleanup(&dir);
    }
}
