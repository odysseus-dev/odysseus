// src/preset_manager.rs  <- src/preset_manager.py
//! JSON-file-backed store for chat presets (`PresetManager`).
//!
//! Faithful port of `src/preset_manager.py`. The `DEFAULT_PRESETS` dict is a
//! `Lazy<serde_json::Value>` built with `json!` (verbatim multi-line prompts,
//! float temperatures, integer `max_tokens`). The store is a
//! `serde_json::Map<String, Value>` with `preserve_order` so JSON key ordering
//! matches Python `dict` insertion order across load/save round-trips.
//!
//! This struct also implements [`crate::src::chat_handler::PresetManager`] (the
//! narrow `presets()` accessor `ChatHandler` consumes), so the real manager can
//! thread through the chat path.

use crate::pyos as os;
use crate::pylog as logger;
use once_cell::sync::Lazy;
use serde_json::{json, Map, Value};

/// The built-in presets created on first run (`PresetManager.DEFAULT_PRESETS`).
///
/// The system prompts are reproduced verbatim, including trailing whitespace /
/// newlines, so a freshly-written `presets.json` is byte-identical to Python's.
pub static DEFAULT_PRESETS: Lazy<Value> = Lazy::new(|| {
    json!({
        "code_analyze": {
            "name": "Code Analyze",
            "temperature": 0.2,
            "max_tokens": 8000,
            "system_prompt": "You are a code analyzer. \nANALYSIS FORMAT:\n- Issues: [specific problems found]\n- Security: [vulnerabilities if any]\n- Performance: [optimization opportunities]\n- Fix: [concrete solutions with code examples]\n\nStart directly with findings. No preamble. If input isn't code, state: \"Input is not code. Please provide code to analyze.\"\n"
        },
        "brainstorm": {
            "name": "Brainstorm",
            "temperature": 0.9,
            "max_tokens": 4096,
            "system_prompt": "You are a creative ideation assistant focused on divergent thinking.\n\nGenerate diverse, unexpected ideas that span from practical to experimental. \n- Mix conventional and unconventional approaches\n- Connect unrelated concepts to spark innovation\n- Consider multiple perspectives and contexts\n- Include both immediate solutions and long-term possibilities\n- Challenge assumptions without being absurd for absurdity's sake\n\nStructure ideas clearly but allow creative freedom in presentation. Aim for quantity and variety over filtering.\n"
        },
        "reason": {
            "name": "Reason",
            "temperature": 0.3,
            "max_tokens": 6000,
            "system_prompt": "You are a systematic reasoning assistant.\n\nStructure all responses using clear logical progression:\n1. Identify key components of the question\n2. State relevant principles or facts\n3. Build argument step by step\n4. Address potential counterarguments\n5. Conclude with justified answer\n\nUse precise language. Show causal relationships explicitly. Quantify uncertainty where applicable.\n"
        },
        "custom": {
            "name": "Custom",
            "temperature": 1.0,
            "max_tokens": 0,
            "system_prompt": "",
            "inject_prefix": "",
            "inject_suffix": "",
            "enabled": false
        }
    })
});

/// The legacy custom-preset system prompt that triggers the one-time migration
/// (`legacy_prompt` in `load`).
const LEGACY_PROMPT: &str =
    "You are a helpful, balanced assistant. Match your response style to the user's needs.";

/// `DEFAULT_PRESETS` as an owned `Map` (the object form Python copies from).
fn default_presets_map() -> Map<String, Value> {
    DEFAULT_PRESETS
        .as_object()
        .cloned()
        .unwrap_or_default()
}

/// JSON-file-backed preset store.
pub struct PresetManager {
    /// `os.path.join(data_dir, "presets.json")`.
    pub presets_file: String,
    /// The in-memory preset registry (`self.presets`).
    presets: Map<String, Value>,
}

impl PresetManager {
    /// `__init__(self, data_dir)`: resolve `presets.json` and load it.
    pub fn new(data_dir: &str) -> Self {
        let presets_file = os::path::join(data_dir, "presets.json");
        let mut mgr = PresetManager {
            presets_file,
            presets: Map::new(),
        };
        mgr.presets = mgr.load();
        mgr
    }

    /// Load presets from file, creating defaults if needed.
    ///
    /// Mirrors `PresetManager.load`, including the one-time legacy-custom
    /// migration (custom preset lacking `enabled` whose name is `"Custom"`,
    /// has no `character_name`, and carries the legacy system prompt is reset
    /// to the disabled/empty default and re-saved).
    pub fn load(&mut self) -> Map<String, Value> {
        if !os::path::exists(&self.presets_file) {
            let defaults = default_presets_map();
            self.save(&Value::Object(defaults.clone()));
            // `return self.DEFAULT_PRESETS.copy()`
            return defaults;
        }

        let raw = match std::fs::read_to_string(&self.presets_file) {
            Ok(s) => s,
            Err(e) => {
                logger::error(&format!("Error loading presets: {e}"));
                return default_presets_map();
            }
        };
        let parsed: Value = match serde_json::from_str(&raw) {
            Ok(v) => v,
            Err(e) => {
                logger::error(&format!("Error loading presets: {e}"));
                return default_presets_map();
            }
        };

        let mut presets = match parsed {
            Value::Object(m) => m,
            // `if not isinstance(presets, dict): ... return self.DEFAULT_PRESETS.copy()`
            // Non-object top-level JSON -> log the error and return defaults.
            _ => {
                logger::error("Error loading presets: expected an object");
                return default_presets_map();
            }
        };

        // Legacy-custom migration.
        let needs_migration = match presets.get("custom") {
            Some(Value::Object(custom)) => !custom.contains_key("enabled"),
            _ => false,
        };
        if needs_migration {
            let custom = presets.get("custom").and_then(Value::as_object).cloned();
            if let Some(custom) = custom {
                let name_is_custom =
                    custom.get("name").and_then(Value::as_str) == Some("Custom");
                // `not custom.get("character_name")` — falsy when missing/empty.
                let no_character_name = match custom.get("character_name") {
                    None | Some(Value::Null) => true,
                    Some(Value::String(s)) => s.is_empty(),
                    Some(other) => is_falsy(other),
                };
                let legacy_prompt =
                    custom.get("system_prompt").and_then(Value::as_str) == Some(LEGACY_PROMPT);
                if name_is_custom && no_character_name && legacy_prompt {
                    let mut new_custom = custom.clone();
                    new_custom.insert("enabled".to_string(), json!(false));
                    new_custom.insert("system_prompt".to_string(), json!(""));
                    new_custom.insert("temperature".to_string(), json!(1.0));
                    new_custom.insert("max_tokens".to_string(), json!(0));
                    // setdefault: only insert if absent.
                    new_custom
                        .entry("inject_prefix".to_string())
                        .or_insert_with(|| json!(""));
                    new_custom
                        .entry("inject_suffix".to_string())
                        .or_insert_with(|| json!(""));
                    presets.insert("custom".to_string(), Value::Object(new_custom));
                    self.save(&Value::Object(presets.clone()));
                }
            }
        }
        // Heal missing built-in keys: if any key from DEFAULT_PRESETS is absent
        // from the loaded map, overlay defaults under user values and re-save.
        // `presets = {**self.DEFAULT_PRESETS, **presets}` -> defaults first, then
        // loaded values win (user edits are preserved).
        let defaults = default_presets_map();
        if defaults.keys().any(|k| !presets.contains_key(k)) {
            let mut healed = defaults;
            for (k, v) in presets {
                healed.insert(k, v);
            }
            self.save(&Value::Object(healed.clone()));
            return healed;
        }
        presets
    }

    /// Save presets to file. Returns `True`/`False` mirroring the Python bool.
    ///
    /// `json.dump(presets, f, indent=2)` -> `serde_json::to_string_pretty`
    /// (2-space indentation, matching `indent=2`). On success the in-memory
    /// `self.presets` is updated to the saved object.
    pub fn save(&mut self, presets: &Value) -> bool {
        let dir = os::path::dirname(&self.presets_file);
        if let Err(e) = os::makedirs(&dir, true) {
            logger::error(&format!("Error saving presets: {e}"));
            return false;
        }
        let serialized = match serde_json::to_string_pretty(presets) {
            Ok(s) => s,
            Err(e) => {
                logger::error(&format!("Error saving presets: {e}"));
                return false;
            }
        };
        if let Err(e) = std::fs::write(&self.presets_file, serialized) {
            logger::error(&format!("Error saving presets: {e}"));
            return false;
        }
        self.presets = presets.as_object().cloned().unwrap_or_default();
        true
    }

    /// Get a specific preset. `None` when absent (Python `dict.get` -> `None`).
    pub fn get(&self, preset_id: &str) -> Option<&Value> {
        self.presets.get(preset_id)
    }

    /// Update the custom preset (`update_custom`).
    #[allow(clippy::too_many_arguments)]
    pub fn update_custom(
        &mut self,
        temperature: f64,
        max_tokens: i64,
        system_prompt: &str,
        name: &str,
        enabled: bool,
        inject_prefix: &str,
        inject_suffix: &str,
    ) -> bool {
        let display_name = if name.is_empty() { "Custom" } else { name };
        let custom = json!({
            "name": display_name,
            "character_name": name,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "system_prompt": system_prompt,
            "inject_prefix": inject_prefix,
            "inject_suffix": inject_suffix,
            "enabled": enabled,
        });
        self.presets.insert("custom".to_string(), custom);
        let snapshot = Value::Object(self.presets.clone());
        self.save(&snapshot)
    }

    /// Get all presets (a copy, like Python `self.presets.copy()`).
    pub fn get_all(&self) -> Map<String, Value> {
        self.presets.clone()
    }

    /// Get user-saved character templates (`get_user_templates`).
    ///
    /// `self.presets.get("user_templates", [])` — missing/non-array -> `[]`.
    pub fn get_user_templates(&self) -> Vec<Value> {
        match self.presets.get("user_templates") {
            Some(Value::Array(a)) => a.clone(),
            _ => Vec::new(),
        }
    }

    /// Save a new user template or update existing by id (`save_user_template`).
    pub fn save_user_template(&mut self, template: &Value) -> bool {
        let mut templates = self.get_user_templates();
        let tid = template.get("id");
        // `next((i for i, t in enumerate(templates) if t.get("id") == template.get("id")), None)`
        let existing = templates
            .iter()
            .position(|t| t.get("id") == tid);
        match existing {
            Some(i) => templates[i] = template.clone(),
            None => templates.push(template.clone()),
        }
        self.presets
            .insert("user_templates".to_string(), Value::Array(templates));
        let snapshot = Value::Object(self.presets.clone());
        self.save(&snapshot)
    }

    /// Delete a user template by id (`delete_user_template`).
    pub fn delete_user_template(&mut self, template_id: &str) -> bool {
        let templates = self.get_user_templates();
        let kept: Vec<Value> = templates
            .into_iter()
            .filter(|t| t.get("id").and_then(Value::as_str) != Some(template_id))
            .collect();
        self.presets
            .insert("user_templates".to_string(), Value::Array(kept));
        let snapshot = Value::Object(self.presets.clone());
        self.save(&snapshot)
    }

    /// Get saved group chat presets (`get_group_presets`).
    pub fn get_group_presets(&self) -> Vec<Value> {
        match self.presets.get("group_presets") {
            Some(Value::Array(a)) => a.clone(),
            _ => Vec::new(),
        }
    }

    /// Save group chat presets (`save_group_presets`).
    pub fn save_group_presets(&mut self, groups: Vec<Value>) -> bool {
        self.presets
            .insert("group_presets".to_string(), Value::Array(groups));
        let snapshot = Value::Object(self.presets.clone());
        self.save(&snapshot)
    }
}

/// `chat_handler` only needs the ordered preset registry.
///
/// The `chat_handler` module declares the `PresetManager` trait; everything is
/// always compiled (no cargo feature flags), so this `impl` is unconditional.
impl crate::src::chat_handler::PresetManager for PresetManager {
    fn presets(&self) -> &Map<String, Value> {
        &self.presets
    }
}

/// Python truthiness for the migration falsy-checks.
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

#[cfg(test)]
mod tests {
    // Parity checks against the live Python (`src.preset_manager`).
    use super::*;
    use crate::src::chat_handler::PresetManager as PresetManagerTrait;

    fn tmp_dir() -> tempfile::TempDir {
        tempfile::tempdir().expect("tempdir")
    }

    #[test]
    fn creates_defaults_on_first_load() {
        let dir = tmp_dir();
        let mgr = PresetManager::new(dir.path().to_str().unwrap());
        assert!(os::path::exists(&mgr.presets_file));
        // The four default presets are present, in insertion order.
        let keys: Vec<&str> = mgr.presets.keys().map(|s| s.as_str()).collect();
        assert_eq!(keys, vec!["code_analyze", "brainstorm", "reason", "custom"]);
        // Default custom preset is disabled.
        assert_eq!(
            mgr.get("custom").unwrap().get("enabled"),
            Some(&json!(false))
        );
        assert_eq!(
            mgr.get("code_analyze").unwrap().get("max_tokens"),
            Some(&json!(8000))
        );
    }

    #[test]
    fn save_writes_two_space_indent() {
        let dir = tmp_dir();
        let mut mgr = PresetManager::new(dir.path().to_str().unwrap());
        let snapshot = Value::Object(mgr.presets.clone());
        assert!(mgr.save(&snapshot));
        let txt = std::fs::read_to_string(&mgr.presets_file).unwrap();
        // indent=2 -> "  \"" two-space leading indent for the first key.
        assert!(txt.contains("\n  \"code_analyze\""));
    }

    #[test]
    fn legacy_custom_migration() {
        let dir = tmp_dir();
        let path = dir.path().join("presets.json");
        // A legacy custom preset that lacks `enabled`.
        let legacy = json!({
            "custom": {
                "name": "Custom",
                "system_prompt": LEGACY_PROMPT,
                "temperature": 0.7,
                "max_tokens": 2048
            }
        });
        std::fs::write(&path, serde_json::to_string(&legacy).unwrap()).unwrap();
        let mgr = PresetManager::new(dir.path().to_str().unwrap());
        let custom = mgr.get("custom").unwrap();
        assert_eq!(custom.get("enabled"), Some(&json!(false)));
        assert_eq!(custom.get("system_prompt"), Some(&json!("")));
        assert_eq!(custom.get("temperature"), Some(&json!(1.0)));
        assert_eq!(custom.get("max_tokens"), Some(&json!(0)));
        assert_eq!(custom.get("inject_prefix"), Some(&json!("")));
        assert_eq!(custom.get("inject_suffix"), Some(&json!("")));
    }

    #[test]
    fn no_migration_when_enabled_present() {
        let dir = tmp_dir();
        let path = dir.path().join("presets.json");
        let existing = json!({
            "custom": {
                "name": "Custom",
                "system_prompt": LEGACY_PROMPT,
                "temperature": 0.7,
                "max_tokens": 2048,
                "enabled": true
            }
        });
        std::fs::write(&path, serde_json::to_string(&existing).unwrap()).unwrap();
        let mgr = PresetManager::new(dir.path().to_str().unwrap());
        // `enabled` present -> no migration; temperature stays 0.7.
        let custom = mgr.get("custom").unwrap();
        assert_eq!(custom.get("temperature"), Some(&json!(0.7)));
        assert_eq!(custom.get("enabled"), Some(&json!(true)));
    }

    #[test]
    fn update_custom_round_trip() {
        let dir = tmp_dir();
        let mut mgr = PresetManager::new(dir.path().to_str().unwrap());
        assert!(mgr.update_custom(0.5, 1234, "prompt", "Alice", true, "pre", "suf"));
        let custom = mgr.get("custom").unwrap();
        assert_eq!(custom.get("name"), Some(&json!("Alice")));
        assert_eq!(custom.get("character_name"), Some(&json!("Alice")));
        assert_eq!(custom.get("temperature"), Some(&json!(0.5)));
        assert_eq!(custom.get("max_tokens"), Some(&json!(1234)));
        assert_eq!(custom.get("enabled"), Some(&json!(true)));
        // Empty name -> display "Custom" but character_name "".
        assert!(mgr.update_custom(1.0, 0, "", "", false, "", ""));
        let custom = mgr.get("custom").unwrap();
        assert_eq!(custom.get("name"), Some(&json!("Custom")));
        assert_eq!(custom.get("character_name"), Some(&json!("")));
    }

    #[test]
    fn user_templates_upsert_and_delete() {
        let dir = tmp_dir();
        let mut mgr = PresetManager::new(dir.path().to_str().unwrap());
        mgr.save_user_template(&json!({"id": "a", "name": "first"}));
        mgr.save_user_template(&json!({"id": "b", "name": "second"}));
        assert_eq!(mgr.get_user_templates().len(), 2);
        // Upsert by id replaces.
        mgr.save_user_template(&json!({"id": "a", "name": "renamed"}));
        let tpls = mgr.get_user_templates();
        assert_eq!(tpls.len(), 2);
        assert_eq!(tpls[0].get("name"), Some(&json!("renamed")));
        // Delete by id.
        mgr.delete_user_template("a");
        let tpls = mgr.get_user_templates();
        assert_eq!(tpls.len(), 1);
        assert_eq!(tpls[0].get("id"), Some(&json!("b")));
    }

    #[test]
    fn group_presets_round_trip() {
        let dir = tmp_dir();
        let mut mgr = PresetManager::new(dir.path().to_str().unwrap());
        assert!(mgr.get_group_presets().is_empty());
        mgr.save_group_presets(vec![json!({"g": 1})]);
        assert_eq!(mgr.get_group_presets().len(), 1);
    }

    #[test]
    fn implements_chat_handler_trait() {
        let dir = tmp_dir();
        let mgr = PresetManager::new(dir.path().to_str().unwrap());
        let registry = PresetManagerTrait::presets(&mgr);
        assert!(registry.contains_key("code_analyze"));
    }

    /// Non-object top-level JSON (e.g. `[]`) must return DEFAULT_PRESETS, not an
    /// empty map. Mirrors `if not isinstance(presets, dict): return DEFAULT_PRESETS.copy()`.
    #[test]
    fn non_object_json_returns_defaults() {
        let dir = tmp_dir();
        let path = dir.path().join("presets.json");
        std::fs::write(&path, "[1, 2, 3]").unwrap();
        let mgr = PresetManager::new(dir.path().to_str().unwrap());
        // All four built-in presets must be present.
        assert!(mgr.presets.contains_key("code_analyze"));
        assert!(mgr.presets.contains_key("brainstorm"));
        assert!(mgr.presets.contains_key("reason"));
        assert!(mgr.presets.contains_key("custom"));
    }

    /// A presets file missing some built-in keys gets them overlaid from
    /// DEFAULT_PRESETS (defaults first, user values win) and the file is
    /// re-saved. Mirrors the `{**self.DEFAULT_PRESETS, **presets}` healing block.
    #[test]
    fn heals_missing_builtin_keys_and_resaves() {
        let dir = tmp_dir();
        let path = dir.path().join("presets.json");
        // File with only "custom" — missing code_analyze, brainstorm, reason.
        let partial = json!({
            "custom": {
                "name": "Custom",
                "temperature": 0.5,
                "max_tokens": 999,
                "system_prompt": "custom prompt",
                "inject_prefix": "",
                "inject_suffix": "",
                "enabled": true
            }
        });
        std::fs::write(&path, serde_json::to_string(&partial).unwrap()).unwrap();
        let mgr = PresetManager::new(dir.path().to_str().unwrap());

        // All built-in keys must now be present.
        assert!(mgr.presets.contains_key("code_analyze"));
        assert!(mgr.presets.contains_key("brainstorm"));
        assert!(mgr.presets.contains_key("reason"));

        // User-edited custom preset must NOT be clobbered.
        let custom = mgr.get("custom").unwrap();
        assert_eq!(custom.get("temperature"), Some(&json!(0.5)));
        assert_eq!(custom.get("max_tokens"), Some(&json!(999)));
        assert_eq!(custom.get("system_prompt"), Some(&json!("custom prompt")));
        assert_eq!(custom.get("enabled"), Some(&json!(true)));

        // The file must have been re-saved (readable and contains the healed keys).
        let saved_text = std::fs::read_to_string(&path).unwrap();
        let saved: Value = serde_json::from_str(&saved_text).unwrap();
        assert!(saved.get("code_analyze").is_some());
        assert!(saved.get("brainstorm").is_some());
        assert!(saved.get("reason").is_some());
    }

    /// A file with ALL built-in keys present must not trigger the healing branch
    /// (no extra re-save, user values are returned as-is).
    #[test]
    fn no_heal_when_all_builtins_present() {
        let dir = tmp_dir();
        // First load writes defaults.
        let mut mgr = PresetManager::new(dir.path().to_str().unwrap());
        // Mutate a value on disk by updating custom, then reload.
        mgr.update_custom(0.77, 512, "sp", "Bob", true, "", "");
        // Reload from disk — should not re-overlay defaults over the updated custom.
        let mgr2 = PresetManager::new(dir.path().to_str().unwrap());
        let custom = mgr2.get("custom").unwrap();
        assert_eq!(custom.get("temperature"), Some(&json!(0.77)));
        assert_eq!(custom.get("name"), Some(&json!("Bob")));
    }
}
