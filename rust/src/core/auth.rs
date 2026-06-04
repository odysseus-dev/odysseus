// core/auth.rs  <- core/auth.py
//! Authentication module — multi-user password hashing, session tokens, config
//! persistence. Config stored in data/auth.json. Uses bcrypt directly.

use crate::core::atomic_io::atomic_write_json;
use crate::pylog as logger;
use crate::pyos as os;
use crate::pyotp;
use crate::pysecrets as secrets;
use crate::pytime as time;
use once_cell::sync::Lazy;
use serde_json::{json, Map, Value};
use std::collections::HashMap;
use std::sync::Mutex;

/// `DEFAULT_PRIVILEGES`
pub fn default_privileges() -> Map<String, Value> {
    let mut m = Map::new();
    m.insert("can_use_agent".into(), json!(true));
    m.insert("can_use_browser".into(), json!(true));
    m.insert("can_use_bash".into(), json!(false));
    m.insert("can_use_documents".into(), json!(true));
    m.insert("can_use_research".into(), json!(true));
    m.insert("can_generate_images".into(), json!(true));
    m.insert("can_manage_memory".into(), json!(true));
    m.insert("max_messages_per_day".into(), json!(0));
    m.insert("allowed_models".into(), json!([]));
    m
}

/// `ADMIN_PRIVILEGES = {k: (True if bool else 0 if int else []) for ...}`.
/// Admins get everything.
pub fn admin_privileges() -> Map<String, Value> {
    let mut m = Map::new();
    for (k, v) in default_privileges() {
        let nv = if v.is_boolean() {
            json!(true)
        } else if v.is_i64() || v.is_u64() {
            json!(0)
        } else {
            json!([])
        };
        m.insert(k, nv);
    }
    m
}

pub static DEFAULT_AUTH_PATH: Lazy<String> =
    Lazy::new(|| os::path::join(&crate::core::constants::DATA_DIR, "auth.json"));
pub const TOKEN_TTL: i64 = 60 * 60 * 24 * 7; // 7 days

fn _hash_password(password: &str) -> String {
    bcrypt::hash(password, bcrypt::DEFAULT_COST).expect("bcrypt hash")
}

fn _verify_password(password: &str, hashed: &str) -> bool {
    bcrypt::verify(password, hashed).unwrap_or(false)
}

/// Manages multi-user password + session-token auth system.
/// `RESERVED_USERNAMES` — names that must never become real accounts. Notably
/// `internal-tool`: `core::middleware::require_admin` grants admin to
/// `current_user == "internal-tool"`, so an account literally named that would be
/// a silent admin. `create_user`/`rename_user` refuse these.
const RESERVED_USERNAMES: [&str; 4] = ["internal-tool", "api", "demo", "system"];

pub struct AuthManager {
    pub auth_path: String,
    _sessions_path: String,
    // Python leaves `_config` un-locked (GIL); we wrap it so `&self` methods can
    // mutate it across the FastAPI threadpool.
    _config: Mutex<Value>,
    // token -> {username, expiry}
    // `_sessions_lock` (an RLock in Python) is folded into this Mutex.
    _sessions: Mutex<HashMap<String, Value>>,
    // `_setup_lock` — serializes first-run `setup()` so two concurrent requests
    // cannot both pass the is_configured() check and create an admin.
    _setup_lock: Mutex<()>,
}

impl Default for AuthManager {
    fn default() -> Self {
        Self::new()
    }
}

impl AuthManager {
    pub fn new() -> Self {
        Self::with_path(&DEFAULT_AUTH_PATH)
    }

    pub fn with_path(auth_path: &str) -> Self {
        let _sessions_path = os::path::join(&os::path::dirname(auth_path), "sessions.json");
        let mgr = AuthManager {
            auth_path: auth_path.to_string(),
            _sessions_path,
            _config: Mutex::new(Value::Object(Map::new())),
            _sessions: Mutex::new(HashMap::new()),
            _setup_lock: Mutex::new(()),
        };
        mgr._load();
        mgr._load_sessions();
        mgr._migrate_single_user();
        mgr._migrate_legacy_admin_role();
        mgr
    }

    fn _load(&self) {
        if os::path::exists(&self.auth_path) {
            match std::fs::read_to_string(&self.auth_path)
                .ok()
                .and_then(|s| serde_json::from_str::<Value>(&s).ok())
            {
                Some(mut v) => {
                    // Normalize stored usernames to trimmed-lowercase so a mixed-case
                    // auth.json key still matches the `.strip().lower()` applied at
                    // login/verify time.
                    if let Some(users) = v.get_mut("users").and_then(|u| u.as_object_mut()) {
                        let normalized: Map<String, Value> = users
                            .iter()
                            .map(|(k, val)| (k.trim().to_lowercase(), val.clone()))
                            .collect();
                        *users = normalized;
                    }
                    *self._config.lock().unwrap() = v;
                    logger::info("Auth config loaded");
                }
                None => {
                    logger::error("Failed to load auth config");
                    *self._config.lock().unwrap() = Value::Object(Map::new());
                }
            }
        } else {
            *self._config.lock().unwrap() = Value::Object(Map::new());
            logger::info("No auth config found — first-run setup required");
        }
    }

    /// Load persisted session tokens from disk, pruning expired ones.
    fn _load_sessions(&self) {
        if os::path::exists(&self._sessions_path) {
            match std::fs::read_to_string(&self._sessions_path)
                .ok()
                .and_then(|s| serde_json::from_str::<HashMap<String, Value>>(&s).ok())
            {
                Some(data) => {
                    let now = time::time();
                    let filtered: HashMap<String, Value> = data
                        .iter()
                        .filter(|(_, v)| {
                            v.get("expiry").and_then(|x| x.as_f64()).unwrap_or(0.0) > now
                        })
                        .map(|(k, v)| (k.clone(), v.clone()))
                        .collect();
                    let pruned = data.len() as i64 - filtered.len() as i64;
                    let count = filtered.len();
                    *self._sessions.lock().unwrap() = filtered;
                    if pruned > 0 {
                        self._save_sessions();
                    }
                    logger::info(&format!("Loaded {count} session(s) from disk"));
                }
                None => {
                    logger::error("Failed to load sessions");
                    *self._sessions.lock().unwrap() = HashMap::new();
                }
            }
        }
    }

    /// Persist session tokens to disk (atomic, lock-guarded).
    fn _save_sessions(&self) {
        let snapshot = self._sessions.lock().unwrap().clone();
        if let Err(e) = atomic_write_json(&self._sessions_path, &snapshot, None) {
            logger::error(&format!("Failed to save sessions: {e}"));
        }
    }

    /// Migrate old single-user format to multi-user format.
    fn _migrate_single_user(&self) {
        let mut do_save = false;
        {
            let mut cfg = self._config.lock().unwrap();
            let obj = cfg.as_object().cloned().unwrap_or_default();
            if obj.contains_key("password_hash") && !obj.contains_key("users") {
                let old_user = obj
                    .get("username")
                    .and_then(|v| v.as_str())
                    .unwrap_or("admin")
                    .to_string();
                let old_hash = obj.get("password_hash").cloned().unwrap_or(Value::Null);
                let mut users = Map::new();
                let mut user = Map::new();
                user.insert("password_hash".into(), old_hash);
                user.insert("created".into(), json!(time::time()));
                user.insert("is_admin".into(), json!(true));
                users.insert(old_user.clone(), Value::Object(user));
                let mut new_cfg = Map::new();
                new_cfg.insert("users".into(), Value::Object(users));
                *cfg = Value::Object(new_cfg);
                do_save = true;
                logger::info(&format!(
                    "Migrated single-user auth to multi-user (admin: {old_user})"
                ));
            }
        }
        if do_save {
            self._save();
        }
    }

    /// Normalize setup.py's old role='admin' marker to is_admin=True.
    fn _migrate_legacy_admin_role(&self) {
        let mut changed = false;
        {
            let mut cfg = self._config.lock().unwrap();
            if let Some(users) = cfg
                .as_object_mut()
                .and_then(|o| o.get_mut("users"))
                .and_then(|u| u.as_object_mut())
            {
                for (username, user) in users.iter_mut() {
                    if let Some(u) = user.as_object_mut() {
                        let is_legacy_admin =
                            u.get("role").and_then(|r| r.as_str()) == Some("admin");
                        if is_legacy_admin && !u.contains_key("is_admin") {
                            u.insert("is_admin".into(), json!(true));
                            changed = true;
                            logger::info(&format!("Migrated legacy admin role for '{username}'"));
                        }
                    }
                }
            }
        }
        if changed {
            self._save();
        }
    }

    fn _save(&self) {
        let snapshot = self._config.lock().unwrap().clone();
        let _ = atomic_write_json(&self.auth_path, &snapshot, Some(2));
    }

    /// `@property users`
    pub fn users(&self) -> Map<String, Value> {
        self._config
            .lock()
            .unwrap()
            .get("users")
            .and_then(|v| v.as_object())
            .cloned()
            .unwrap_or_default()
    }

    /// `@property signup_enabled`
    pub fn signup_enabled(&self) -> bool {
        self._config
            .lock()
            .unwrap()
            .get("signup_enabled")
            .and_then(|v| v.as_bool())
            .unwrap_or(false)
    }

    /// `@signup_enabled.setter`
    pub fn set_signup_enabled(&self, value: bool) {
        {
            let mut cfg = self._config.lock().unwrap();
            cfg["signup_enabled"] = json!(value);
        }
        self._save();
    }

    /// `@property is_configured`
    pub fn is_configured(&self) -> bool {
        !self.users().is_empty()
    }

    // ------------------------------------------------------------------
    // Account management
    // ------------------------------------------------------------------

    /// First-run admin setup. Only works if no users exist.
    pub fn setup(&self, username: &str, password: &str) -> bool {
        // Serialize first-run setup so two concurrent requests cannot both see
        // "not configured" and each create an admin.
        let _guard = self._setup_lock.lock().unwrap();
        if self.is_configured() {
            return false;
        }
        self.create_user(username, password, true)
    }

    /// Create a new user account.
    pub fn create_user(&self, username: &str, password: &str, is_admin: bool) -> bool {
        let username = username.trim().to_lowercase();
        if username.is_empty() {
            return false;
        }
        if RESERVED_USERNAMES.contains(&username.as_str()) {
            logger::warning(&format!("Refused to create reserved username '{username}'"));
            return false;
        }
        if self.users().contains_key(&username) {
            return false;
        }
        {
            let mut cfg = self._config.lock().unwrap();
            let obj = cfg.as_object_mut().unwrap();
            if !obj.contains_key("users") {
                obj.insert("users".into(), Value::Object(Map::new()));
            }
            let mut user = Map::new();
            user.insert("password_hash".into(), json!(_hash_password(password)));
            user.insert("created".into(), json!(time::time()));
            user.insert("is_admin".into(), json!(is_admin));
            user.insert(
                "privileges".into(),
                Value::Object(if is_admin {
                    admin_privileges()
                } else {
                    default_privileges()
                }),
            );
            obj.get_mut("users")
                .unwrap()
                .as_object_mut()
                .unwrap()
                .insert(username.clone(), Value::Object(user));
        }
        self._save();
        logger::info(&format!("Created user '{username}' (admin={is_admin})"));
        true
    }

    /// Delete a user. Only admins can delete, and can't delete themselves.
    pub fn delete_user(&self, username: &str, requesting_user: &str) -> bool {
        let username = username.trim().to_lowercase();
        let users = self.users();
        if !users.contains_key(&username) {
            return false;
        }
        if username == requesting_user {
            return false;
        }
        let requester_is_admin = users
            .get(requesting_user)
            .and_then(|u| u.get("is_admin"))
            .and_then(|v| v.as_bool())
            .unwrap_or(false);
        if !requester_is_admin {
            return false;
        }
        {
            let mut cfg = self._config.lock().unwrap();
            if let Some(u) = cfg.as_object_mut().and_then(|o| o.get_mut("users")).and_then(|u| u.as_object_mut()) {
                u.remove(&username);
            }
        }
        self._save();
        // Purge all sessions belonging to this user.
        let mut revoked = 0;
        {
            let mut sessions = self._sessions.lock().unwrap();
            let to_drop: Vec<String> = sessions
                .iter()
                .filter(|(_, sess)| {
                    sess.get("username").and_then(|v| v.as_str()) == Some(username.as_str())
                })
                .map(|(tok, _)| tok.clone())
                .collect();
            for tok in to_drop {
                sessions.remove(&tok);
                revoked += 1;
            }
        }
        if revoked > 0 {
            self._save_sessions();
        }
        logger::info(&format!(
            "Deleted user '{username}' (by {requesting_user}); revoked {revoked} active session(s)"
        ));
        true
    }

    /// Rename a user in the auth config AND active sessions. Admin only; refuses
    /// reserved/empty names and collisions. Returns `true` on success.
    pub fn rename_user(&self, old_username: &str, new_username: &str, requesting_user: &str) -> bool {
        let old_username = old_username.trim().to_lowercase();
        let new_username = new_username.trim().to_lowercase();
        let requesting_user = requesting_user.trim().to_lowercase();
        if old_username.is_empty() || new_username.is_empty() {
            return false;
        }
        if RESERVED_USERNAMES.contains(&new_username.as_str()) {
            logger::warning(&format!(
                "Refused to rename '{old_username}' into reserved username '{new_username}'"
            ));
            return false;
        }
        let users = self.users();
        if !users.contains_key(&old_username) || users.contains_key(&new_username) {
            return false;
        }
        if !users
            .get(&requesting_user)
            .and_then(|u| u.get("is_admin"))
            .and_then(|v| v.as_bool())
            .unwrap_or(false)
        {
            return false;
        }
        {
            let mut cfg = self._config.lock().unwrap();
            if let Some(uobj) = cfg
                .as_object_mut()
                .and_then(|o| o.get_mut("users"))
                .and_then(|u| u.as_object_mut())
            {
                if let Some(entry) = uobj.remove(&old_username) {
                    uobj.insert(new_username.clone(), entry);
                }
            }
        }
        self._save();

        // Update active sessions. Release the sessions lock BEFORE _save_sessions
        // (the Mutex is not reentrant, unlike the Python RLock).
        let mut renamed = 0i64;
        {
            let mut sessions = self._sessions.lock().unwrap();
            for sess in sessions.values_mut() {
                let su = sess
                    .get("username")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .trim()
                    .to_lowercase();
                if su == old_username {
                    sess["username"] = json!(new_username);
                    renamed += 1;
                }
            }
        }
        if renamed > 0 {
            self._save_sessions();
        }
        logger::info(&format!(
            "Renamed user '{old_username}' -> '{new_username}' (by {requesting_user}); updated {renamed} active session(s)"
        ));
        true
    }

    /// Revoke active browser sessions for a user, optionally preserving one
    /// (`except_token`). Returns the number revoked.
    pub fn revoke_user_sessions(&self, username: &str, except_token: Option<&str>) -> i64 {
        let username = username.trim().to_lowercase();
        let mut revoked = 0i64;
        {
            let mut sessions = self._sessions.lock().unwrap();
            let to_drop: Vec<String> = sessions
                .iter()
                .filter(|(token, sess)| {
                    Some(token.as_str()) != except_token
                        && sess.get("username").and_then(|v| v.as_str()) == Some(username.as_str())
                })
                .map(|(t, _)| t.clone())
                .collect();
            for token in to_drop {
                sessions.remove(&token);
                revoked += 1;
            }
        }
        if revoked > 0 {
            self._save_sessions();
        }
        revoked
    }

    pub fn is_admin(&self, username: &str) -> bool {
        self.users()
            .get(username)
            .and_then(|u| u.get("is_admin"))
            .and_then(|v| v.as_bool())
            .unwrap_or(false)
    }

    pub fn list_users(&self) -> Vec<Value> {
        self.users()
            .iter()
            .map(|(u, d)| {
                json!({
                    "username": u,
                    "is_admin": d.get("is_admin").and_then(|v| v.as_bool()).unwrap_or(false),
                    "privileges": self.get_privileges(u),
                })
            })
            .collect()
    }

    /// Get privileges for a user. Admins get all privileges.
    pub fn get_privileges(&self, username: &str) -> Map<String, Value> {
        let users = self.users();
        let user = users.get(username).cloned().unwrap_or(Value::Object(Map::new()));
        if user.get("is_admin").and_then(|v| v.as_bool()).unwrap_or(false) {
            return admin_privileges();
        }
        // Merge stored privileges with defaults.
        let mut merged = default_privileges();
        if let Some(stored) = user.get("privileges").and_then(|v| v.as_object()) {
            for (k, v) in stored {
                merged.insert(k.clone(), v.clone());
            }
        }
        merged
    }

    /// Update privileges for a user. Can't modify admin privileges.
    pub fn set_privileges(&self, username: &str, privileges: &Map<String, Value>) -> bool {
        let username = username.trim().to_lowercase();
        let users = self.users();
        if !users.contains_key(&username) {
            return false;
        }
        if users
            .get(&username)
            .and_then(|u| u.get("is_admin"))
            .and_then(|v| v.as_bool())
            .unwrap_or(false)
        {
            return false; // admins always have full access
        }
        // Only allow known privilege keys.
        let mut current = self.get_privileges(&username);
        let defaults = default_privileges();
        for (k, v) in privileges {
            if defaults.contains_key(k) {
                current.insert(k.clone(), v.clone());
            }
        }
        {
            let mut cfg = self._config.lock().unwrap();
            cfg["users"][&username]["privileges"] = Value::Object(current.clone());
        }
        self._save();
        logger::info(&format!("Updated privileges for '{username}': {current:?}"));
        true
    }

    pub fn change_password(
        &self,
        username: &str,
        current_password: &str,
        new_password: &str,
    ) -> bool {
        let username = username.trim().to_lowercase();
        let users = self.users();
        if !users.contains_key(&username) {
            return false;
        }
        let hash = users[&username]["password_hash"].as_str().unwrap_or("");
        if !_verify_password(current_password, hash) {
            return false;
        }
        {
            let mut cfg = self._config.lock().unwrap();
            cfg["users"][&username]["password_hash"] = json!(_hash_password(new_password));
        }
        self._save();
        true
    }

    // ------------------------------------------------------------------
    // TOTP two-factor authentication
    // ------------------------------------------------------------------

    /// Check if 2FA is enabled for a user.
    pub fn totp_enabled(&self, username: &str) -> bool {
        self.users()
            .get(&username.trim().to_lowercase())
            .and_then(|u| u.get("totp_enabled"))
            .and_then(|v| v.as_bool())
            .unwrap_or(false)
    }

    /// Generate a new TOTP secret for a user (not yet enabled).
    pub fn totp_generate_secret(&self, username: &str) -> Option<String> {
        let username = username.trim().to_lowercase();
        if !self.users().contains_key(&username) {
            return None;
        }
        let secret = pyotp::random_base32();
        {
            let mut cfg = self._config.lock().unwrap();
            cfg["users"][&username]["totp_secret_pending"] = json!(secret);
        }
        self._save();
        Some(secret)
    }

    /// Get the otpauth:// URI for QR code generation.
    pub fn totp_get_provisioning_uri(&self, username: &str, secret: &str) -> String {
        let totp = pyotp::TOTP::new(secret);
        totp.provisioning_uri(username, "Odysseus")
    }

    /// Verify a TOTP code against the pending secret, then enable 2FA.
    pub fn totp_confirm_enable(&self, username: &str, code: &str) -> bool {
        let username = username.trim().to_lowercase();
        let users = self.users();
        let user = users.get(&username).cloned().unwrap_or(Value::Object(Map::new()));
        let secret = match user.get("totp_secret_pending").and_then(|v| v.as_str()) {
            Some(s) if !s.is_empty() => s.to_string(),
            _ => return false,
        };
        let totp = pyotp::TOTP::new(&secret);
        if !totp.verify(code, 1) {
            return false;
        }
        // Enable 2FA + generate backup codes.
        let backup: Vec<Value> = (0..8).map(|_| json!(secrets::token_hex(4))).collect();
        {
            let mut cfg = self._config.lock().unwrap();
            cfg["users"][&username]["totp_secret"] = json!(secret);
            cfg["users"][&username]["totp_enabled"] = json!(true);
            if let Some(u) = cfg["users"][&username].as_object_mut() {
                u.remove("totp_secret_pending");
            }
            cfg["users"][&username]["totp_backup_codes"] = Value::Array(backup);
        }
        self._save();
        logger::info(&format!("2FA enabled for '{username}'"));
        true
    }

    /// Verify a TOTP code for login.
    pub fn totp_verify(&self, username: &str, code: &str) -> bool {
        let username = username.trim().to_lowercase();
        let users = self.users();
        let user = users.get(&username).cloned().unwrap_or(Value::Object(Map::new()));
        if !user.get("totp_enabled").and_then(|v| v.as_bool()).unwrap_or(false) {
            return true; // 2FA not enabled, always pass
        }
        let secret = match user.get("totp_secret").and_then(|v| v.as_str()) {
            Some(s) if !s.is_empty() => s.to_string(),
            // 2FA enabled but no secret stored (corrupt/partial auth.json) — FAIL
            // CLOSED. Returning true here bypassed the second factor entirely.
            _ => return false,
        };
        // Check backup codes first.
        let backup: Vec<String> = user
            .get("totp_backup_codes")
            .and_then(|v| v.as_array())
            .map(|a| a.iter().filter_map(|x| x.as_str().map(String::from)).collect())
            .unwrap_or_default();
        if backup.iter().any(|c| c == code) {
            let remaining: Vec<Value> = backup
                .iter()
                .filter(|c| c.as_str() != code)
                .map(|c| json!(c))
                .collect();
            let remaining_len = remaining.len();
            {
                let mut cfg = self._config.lock().unwrap();
                cfg["users"][&username]["totp_backup_codes"] = Value::Array(remaining);
            }
            self._save();
            logger::info(&format!(
                "Backup code used for '{username}' ({remaining_len} remaining)"
            ));
            return true;
        }
        let totp = pyotp::TOTP::new(&secret);
        totp.verify(code, 1)
    }

    /// Disable 2FA for a user. Requires password confirmation.
    pub fn totp_disable(&self, username: &str, password: &str) -> bool {
        let username = username.trim().to_lowercase();
        if !self.verify_password(&username, password) {
            return false;
        }
        {
            let mut cfg = self._config.lock().unwrap();
            if let Some(u) = cfg["users"][&username].as_object_mut() {
                u.remove("totp_secret");
                u.remove("totp_secret_pending");
                u.remove("totp_backup_codes");
            }
            cfg["users"][&username]["totp_enabled"] = json!(false);
        }
        self._save();
        logger::info(&format!("2FA disabled for '{username}'"));
        true
    }

    // ------------------------------------------------------------------
    // Login / logout / session tokens
    // ------------------------------------------------------------------

    pub fn verify_password(&self, username: &str, password: &str) -> bool {
        let username = username.trim().to_lowercase();
        let users = self.users();
        if !users.contains_key(&username) {
            return false;
        }
        _verify_password(password, users[&username]["password_hash"].as_str().unwrap_or(""))
    }

    /// Verify credentials and return a session token, or None.
    pub fn create_session(&self, username: &str, password: &str) -> Option<String> {
        let username = username.trim().to_lowercase();
        if !self.verify_password(&username, password) {
            return None;
        }
        let token = secrets::token_hex(32);
        {
            let mut sessions = self._sessions.lock().unwrap();
            sessions.insert(
                token.clone(),
                json!({
                    "username": username,
                    "expiry": time::time() + TOKEN_TTL as f64,
                }),
            );
        }
        self._save_sessions();
        Some(token)
    }

    pub fn validate_token(&self, token: Option<&str>) -> bool {
        let token = match token {
            Some(t) if !t.is_empty() => t,
            _ => return false,
        };
        let mut expired = false;
        let mut deleted_user = false;
        {
            let mut sessions = self._sessions.lock().unwrap();
            let session = match sessions.get(token).cloned() {
                Some(s) => s,
                None => return false,
            };
            if time::time() > session.get("expiry").and_then(|v| v.as_f64()).unwrap_or(0.0) {
                sessions.remove(token);
                expired = true;
            } else {
                let uname = session.get("username").and_then(|v| v.as_str());
                // SECURITY: orphan check — drop sessions of deleted users.
                let exists = uname.map(|u| self.users().contains_key(u)).unwrap_or(false);
                if !exists {
                    sessions.remove(token);
                    deleted_user = true;
                }
            }
        }
        if expired || deleted_user {
            self._save_sessions();
            return false;
        }
        true
    }

    /// Return the username associated with a valid token.
    pub fn get_username_for_token(&self, token: Option<&str>) -> Option<String> {
        let token = match token {
            Some(t) if !t.is_empty() => t,
            _ => return None,
        };
        let mut expired = false;
        let mut deleted_user = false;
        let mut found: Option<String> = None;
        {
            let mut sessions = self._sessions.lock().unwrap();
            let session = sessions.get(token).cloned()?;
            if time::time() > session.get("expiry").and_then(|v| v.as_f64()).unwrap_or(0.0) {
                sessions.remove(token);
                expired = true;
            } else {
                let u = session
                    .get("username")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string();
                // SECURITY: orphan check — same rationale as validate_token.
                if !self.users().contains_key(&u) {
                    sessions.remove(token);
                    deleted_user = true;
                } else {
                    found = Some(u);
                }
            }
        }
        if let Some(u) = found {
            return Some(u);
        }
        if expired || deleted_user {
            self._save_sessions();
        }
        None
    }

    pub fn revoke_token(&self, token: &str) {
        {
            let mut sessions = self._sessions.lock().unwrap();
            sessions.remove(token);
        }
        self._save_sessions();
    }

    pub fn status(&self, token: Option<&str>) -> Value {
        let username = self.get_username_for_token(token);
        let authenticated = username.is_some();
        let mut result = Map::new();
        result.insert("configured".into(), json!(self.is_configured()));
        result.insert("authenticated".into(), json!(authenticated));
        result.insert(
            "username".into(),
            match &username {
                Some(u) => json!(u),
                None => Value::Null,
            },
        );
        result.insert(
            "is_admin".into(),
            json!(match &username {
                Some(u) => self.is_admin(u),
                None => false,
            }),
        );
        if authenticated {
            let u = username.as_ref().unwrap();
            result.insert("privileges".into(), Value::Object(self.get_privileges(u)));
        }
        Value::Object(result)
    }
}
