// src/secret_storage.rs  <- src/secret_storage.py
//! Fernet-based symmetric encryption for secrets stored in the SQLite DB
//! (IMAP / SMTP passwords today; safe to extend). The key lives at
//! `data/.app_key`, mode 0o600, generated on first call. `data/` is gitignored
//! so the key never ships with the repo.
//!
//! Encrypted values carry an `enc:` prefix so the migration is idempotent:
//! passing an already-encrypted value to `encrypt()` is a no-op; passing a
//! plaintext value to `decrypt()` returns it unchanged.

use crate::pylog as logger;
use crate::pyos as os;
use fernet::Fernet;
use once_cell::sync::Lazy;
use std::sync::Mutex;

static _KEY_PATH: Lazy<String> =
    Lazy::new(|| os::path::join(&crate::core::constants::DATA_DIR, ".app_key"));
const _PREFIX: &str = "enc:";
static _FERNET: Lazy<Mutex<Option<Fernet>>> = Lazy::new(|| Mutex::new(None));

fn _load_or_create_key() -> Vec<u8> {
    if os::path::exists(&_KEY_PATH) {
        return std::fs::read(&*_KEY_PATH).unwrap_or_default();
    }
    let _ = os::makedirs(&os::path::dirname(&_KEY_PATH), true);
    let key = Fernet::generate_key();
    let _ = std::fs::write(&*_KEY_PATH, key.as_bytes());
    let _ = os::chmod(&_KEY_PATH, 0o600);
    logger::info(&format!("Generated new app key at {}", &*_KEY_PATH));
    key.into_bytes()
}

/// Build (and cache) the process Fernet, exactly like `_get_fernet`.
fn with_fernet<R>(f: impl FnOnce(&Fernet) -> R) -> R {
    let mut slot = _FERNET.lock().unwrap();
    if slot.is_none() {
        let key = _load_or_create_key();
        let key_str = String::from_utf8(key).expect("fernet key is ascii");
        *slot = Some(Fernet::new(&key_str).expect("valid fernet key"));
    }
    f(slot.as_ref().unwrap())
}

/// Encrypt a string. Empty input passes through. Already-encrypted values pass
/// through unchanged so re-encrypting is a no-op.
pub fn encrypt(plaintext: &str) -> String {
    if plaintext.is_empty() {
        return String::new();
    }
    if plaintext.starts_with(_PREFIX) {
        return plaintext.to_string();
    }
    let token = with_fernet(|f| f.encrypt(plaintext.as_bytes()));
    format!("{_PREFIX}{token}")
}

/// Decrypt an `enc:`-prefixed value. Plaintext (legacy) passes through
/// unchanged. Returns "" on decryption failure so a corrupt or rotated-key row
/// degrades to "unconfigured" rather than 500.
pub fn decrypt(value: &str) -> String {
    if value.is_empty() {
        return String::new();
    }
    if !value.starts_with(_PREFIX) {
        return value.to_string();
    }
    let token = &value[_PREFIX.len()..];
    match with_fernet(|f| f.decrypt(token)) {
        Ok(bytes) => String::from_utf8(bytes).unwrap_or_default(),
        Err(_) => {
            logger::error("Failed to decrypt stored secret — wrong key or corrupt token");
            String::new()
        }
    }
}

pub fn is_encrypted(value: &str) -> bool {
    !value.is_empty() && value.starts_with(_PREFIX)
}
