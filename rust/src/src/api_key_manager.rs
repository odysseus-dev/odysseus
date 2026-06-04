// src/api_key_manager.rs  <- src/api_key_manager.py
//! Encrypted at-rest storage for per-provider API keys.
//!
//! Keys are encrypted with a Fernet symmetric key persisted alongside the
//! key store (`.key`), and the encrypted blobs live in `api_keys.json`.

use crate::error::PyResult;
use crate::pylog as logger;
use crate::pyos as os;
use fernet::Fernet;
use serde_json::{Map, Value};
use std::fs;
use std::io::Write;

pub struct APIKeyManager {
    pub data_dir: String,
    pub api_keys_file: String,
    pub key_file: String,
}

impl APIKeyManager {
    pub fn new(data_dir: &str) -> Self {
        let data_dir = data_dir.to_string();
        let api_keys_file = os::path::join(&data_dir, "api_keys.json");
        let key_file = os::path::join(&data_dir, ".key");
        APIKeyManager {
            data_dir,
            api_keys_file,
            key_file,
        }
    }

    /// Get or create encryption key for API keys
    pub fn get_or_create_key(&self) -> PyResult<Vec<u8>> {
        if os::path::exists(&self.key_file) {
            let data = fs::read(&self.key_file)?;
            Ok(data)
        } else {
            // `Fernet.generate_key()` returns the url-safe-base64 key bytes.
            let key = Fernet::generate_key();
            let mut f = fs::File::create(&self.key_file)?;
            f.write_all(key.as_bytes())?;
            Ok(key.into_bytes())
        }
    }

    /// Encrypt an API key
    pub fn encrypt_api_key(&self, api_key: &str) -> PyResult<String> {
        if api_key.is_empty() {
            return Ok(String::new());
        }
        let key = self.get_or_create_key()?;
        let key_str = String::from_utf8(key)
            .map_err(|e| crate::error::PyError::value(format!("invalid Fernet key: {e}")))?;
        let f = Fernet::new(&key_str)
            .ok_or_else(|| crate::error::PyError::value("invalid Fernet key"))?;
        Ok(f.encrypt(api_key.as_bytes()))
    }

    /// Decrypt an API key
    pub fn decrypt_api_key(&self, encrypted_key: &str) -> PyResult<String> {
        if encrypted_key.is_empty() {
            return Ok(String::new());
        }
        let key = self.get_or_create_key()?;
        let key_str = String::from_utf8(key)
            .map_err(|e| crate::error::PyError::value(format!("invalid Fernet key: {e}")))?;
        let f = Fernet::new(&key_str)
            .ok_or_else(|| crate::error::PyError::value("invalid Fernet key"))?;
        let plaintext = f
            .decrypt(encrypted_key)
            .map_err(|e| crate::error::PyError::value(format!("{e:?}")))?;
        String::from_utf8(plaintext)
            .map_err(|e| crate::error::PyError::value(format!("invalid utf-8: {e}")))
    }

    /// Save encrypted API key to file
    pub fn save(&self, provider: &str, api_key: &str) -> PyResult<()> {
        let mut keys = self.load()?;
        keys.insert(
            provider.to_string(),
            Value::String(self.encrypt_api_key(api_key)?),
        );
        let mut f = fs::File::create(&self.api_keys_file)?;
        f.write_all(serde_json::to_string(&keys)?.as_bytes())?;
        Ok(())
    }

    /// Load and decrypt API keys from `api_keys.json`.
    ///
    /// Mirrors Python error-handling policy:
    /// - Read or JSON-parse failure → log warning, return empty map (don't crash startup).
    /// - JSON root is not an object → log warning, return empty map.
    /// - Per-entry decryption failure → log warning, skip that entry (don't abort the whole load).
    pub fn load(&self) -> PyResult<Map<String, Value>> {
        if !os::path::exists(&self.api_keys_file) {
            return Ok(Map::new());
        }

        // Read file — fall back to empty map on I/O error (corrupt / truncated).
        let contents = match fs::read_to_string(&self.api_keys_file) {
            Ok(s) => s,
            Err(e) => {
                logger::warning(&format!("Failed to read API keys file: {e}"));
                return Ok(Map::new());
            }
        };

        // Parse JSON — fall back to empty map on parse error.
        let parsed: Value = match serde_json::from_str(&contents) {
            Ok(v) => v,
            Err(e) => {
                logger::warning(&format!("Failed to read API keys file: {e}"));
                return Ok(Map::new());
            }
        };

        // Must be a JSON object; ignore legacy/wrong shapes (e.g. arrays).
        // `if not isinstance(encrypted_keys, dict): return {}`
        let encrypted_keys: Map<String, Value> = match parsed {
            Value::Object(m) => m,
            other => {
                let type_name = match &other {
                    Value::Array(_) => "list",
                    Value::String(_) => "str",
                    Value::Number(_) => "int",
                    Value::Bool(_) => "bool",
                    Value::Null => "NoneType",
                    Value::Object(_) => unreachable!(),
                };
                logger::warning(&format!(
                    "API keys file has unexpected shape ({type_name}); ignoring"
                ));
                return Ok(Map::new());
            }
        };

        // Decrypt each entry individually — skip on failure, don't abort.
        let mut result = Map::new();
        for (provider, key) in encrypted_keys.iter() {
            let key_str = key.as_str().unwrap_or("");
            match self.decrypt_api_key(key_str) {
                Ok(plaintext) => {
                    result.insert(provider.clone(), Value::String(plaintext));
                }
                Err(e) => {
                    logger::warning(&format!(
                        "Failed to decrypt API key for {provider}: {e}"
                    ));
                }
            }
        }
        Ok(result)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn mgr_in(dir: &tempfile::TempDir) -> APIKeyManager {
        APIKeyManager::new(dir.path().to_str().unwrap())
    }

    // Helper: write raw bytes into api_keys.json inside the temp dir.
    fn write_keys_file(dir: &tempfile::TempDir, content: &[u8]) {
        let path = dir.path().join("api_keys.json");
        let mut f = std::fs::File::create(path).unwrap();
        f.write_all(content).unwrap();
    }

    /// load() returns empty map when the file doesn't exist.
    #[test]
    fn load_returns_empty_when_file_absent() {
        let dir = tempfile::tempdir().unwrap();
        let result = mgr_in(&dir).load().unwrap();
        assert!(result.is_empty());
    }

    /// load() falls back to empty map on truncated/invalid JSON (read or parse error)
    /// and does NOT propagate an error (Python: `except (json.JSONDecodeError, OSError)`).
    #[test]
    fn load_falls_back_to_empty_on_corrupt_json() {
        let dir = tempfile::tempdir().unwrap();
        write_keys_file(&dir, b"this is not valid JSON{{{{");
        // Must not return Err — startup must survive a corrupt store.
        let result = mgr_in(&dir).load().unwrap();
        assert!(result.is_empty());
    }

    /// load() falls back to empty map when the JSON root is not an object
    /// (Python: `if not isinstance(encrypted_keys, dict): return {}`).
    #[test]
    fn load_falls_back_to_empty_on_non_object_root() {
        let dir = tempfile::tempdir().unwrap();
        write_keys_file(&dir, b"[\"a\",\"b\",\"c\"]");
        let result = mgr_in(&dir).load().unwrap();
        assert!(result.is_empty());
    }

    /// load() skips individual entries whose value cannot be decrypted
    /// (Python: `except (InvalidToken, ValueError): logger.warning(...)`).
    /// The other entries that decrypt correctly must still be returned.
    #[test]
    fn load_skips_bad_entry_but_returns_good_ones() {
        let dir = tempfile::tempdir().unwrap();
        let mgr = mgr_in(&dir);

        // Save a legitimate key so the .key file is created and api_keys.json exists.
        mgr.save("anthropic", "real-key-value").unwrap();

        // Now corrupt one entry by directly injecting a garbled ciphertext.
        // Read current map, add bad entry, write back (bypassing encrypt).
        let path = dir.path().join("api_keys.json");
        let raw = std::fs::read_to_string(&path).unwrap();
        let mut map: serde_json::Map<String, Value> = serde_json::from_str(&raw).unwrap();
        map.insert(
            "openai".to_string(),
            Value::String("this-is-not-valid-fernet-ciphertext".to_string()),
        );
        std::fs::write(&path, serde_json::to_string(&map).unwrap()).unwrap();

        // load() must succeed and omit the bad entry.
        let result = mgr.load().unwrap();
        // "anthropic" was saved via save() which encrypted it; since load() decrypts,
        // we should get the plaintext back.
        assert!(
            result.contains_key("anthropic"),
            "valid entry must survive"
        );
        // "openai" had a garbage ciphertext — must be silently skipped.
        assert!(
            !result.contains_key("openai"),
            "corrupt entry must be skipped"
        );
    }

    /// load() returns empty map on a null JSON root (NoneType branch).
    #[test]
    fn load_falls_back_to_empty_on_null_root() {
        let dir = tempfile::tempdir().unwrap();
        write_keys_file(&dir, b"null");
        let result = mgr_in(&dir).load().unwrap();
        assert!(result.is_empty());
    }

    /// Round-trip: save then load retrieves the original plaintext.
    #[test]
    fn save_then_load_round_trip() {
        let dir = tempfile::tempdir().unwrap();
        let mgr = mgr_in(&dir);
        mgr.save("brave", "sk-brave-test").unwrap();
        // save() re-reads load() which decrypts, then re-encrypts only the new entry —
        // so after one save() the map on disk has the new entry encrypted and any
        // prior entries as stored.  For a single save, we just verify the key comes
        // back decrypted.
        let result = mgr.load().unwrap();
        let brave = result.get("brave").and_then(Value::as_str).unwrap_or("");
        assert_eq!(brave, "sk-brave-test");
    }
}
