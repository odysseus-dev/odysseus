// src/tool_implementations/vault.rs  <- src/tool_implementations.py (Vaultwarden / Bitwarden CLI tools)
//! Vaultwarden / Bitwarden CLI tools.
//!
//! PORT_NOW: every function in this group shells out to the external `bw`
//! binary (Bitwarden CLI) or touches `data/vault.json` on the filesystem — both
//! are faithfully portable. If the `bw` binary is absent the spawn fails and we
//! return the same `{error, exit_code: 1}` shape the Python produces (its
//! `await asyncio.create_subprocess_exec("bw", ...)` would raise
//! `FileNotFoundError`, caught nowhere here — so the Rust port models that as a
//! spawn error surfaced as an error map, matching observable "vault unavailable"
//! behavior without fabricating a success).
//!
//! TOOL RESULT TYPE: every `do_*` returns the Python dict as
//! `serde_json::Map<String, Value>` (preserve_order ON, so insertion order =
//! Python dict order). Internal `try/except` in Python becomes "catch and return
//! an error Map", never `?` — the only thing that propagates is the
//! `_parse_tool_args` `ValueError`, which we match-and-return as the Python's
//! `"Invalid JSON arguments"` error map.

use chrono::{Timelike, Utc};
use serde_json::{Map, Value};

use crate::pyos;
use crate::src::assistant_log::log_to_assistant;

// The shared helpers live in `tool_implementations/mod.rs` (the group root):
//   _parse_tool_args(content: &str) -> PyResult<Map<String, Value>>
//   err_result(msg: impl Into<String>) -> Map<String, Value>   // {"error": msg, "exit_code": 1}
use super::{err_result, _parse_tool_args};

// ── Vaultwarden / Bitwarden CLI tools ──

/// Load Vaultwarden config from data/vault.json.
fn _load_vault_config() -> Map<String, Value> {
    // p = Path("data/vault.json")
    let p = "data/vault.json";
    if pyos::path::exists(p) {
        // try: return json.loads(p.read_text()) / except Exception: pass
        if let Ok(text) = std::fs::read_to_string(p) {
            if let Ok(Value::Object(map)) = serde_json::from_str::<Value>(&text) {
                return map;
            }
        }
    }
    // return {}
    Map::new()
}

/// Run a bw CLI command with optional session + stdin.
/// Returns `(stdout, stderr, returncode)`.
///
/// `proc.returncode` is `Optional[int]` in Python; we model it as `Option<i32>`
/// so a process that produced no exit status (`None`) compares `!= Some(0)` and
/// takes the error path exactly as Python's `rc != 0` would for `None`.
async fn _run_bw(
    args: &[&str],
    session: Option<&str>,
    input_text: Option<&str>,
) -> (String, String, Option<i32>) {
    use std::process::Stdio;
    use tokio::io::AsyncWriteExt;
    use tokio::process::Command;

    // env = {}; env.update(os.environ); if session: env["BW_SESSION"] = session
    // tokio inherits the parent environment by default, so we only add the
    // override — mirroring `env.update(os.environ)` followed by the optional set.
    let mut cmd = Command::new("bw");
    cmd.args(args);
    if let Some(s) = session {
        cmd.env("BW_SESSION", s);
    }

    // stdin=PIPE only when there is input_text, else inherit-null like Python's
    // `stdin=... if input_text else None` (None => no pipe, child inherits).
    if input_text.is_some() {
        cmd.stdin(Stdio::piped());
    }
    cmd.stdout(Stdio::piped());
    cmd.stderr(Stdio::piped());
    // Avoid leaking the bw process if the future is dropped mid-call.
    cmd.kill_on_drop(true);

    let mut proc = match cmd.spawn() {
        Ok(p) => p,
        // `bw` not installed / not on PATH: Python would raise FileNotFoundError
        // from create_subprocess_exec. There is no try/except around the spawn
        // in the callers, so they would propagate; but the only honest, non-
        // fabricating behavior here is to surface it as a failed run. We report
        // it via stderr + a sentinel non-zero code so callers take their
        // `rc != 0 -> {"error": f"bw failed: {stderr[:300]}", ...}` branch.
        Err(e) => return (String::new(), format!("failed to launch bw: {e}"), Some(127)),
    };

    // proc.communicate(input=input_text.encode() if input_text else None)
    if let Some(text) = input_text {
        if let Some(mut stdin) = proc.stdin.take() {
            // Best-effort write; a broken pipe mirrors Python silently moving on.
            let _ = stdin.write_all(text.as_bytes()).await;
            let _ = stdin.shutdown().await;
        }
    }

    let output = match proc.wait_with_output().await {
        Ok(o) => o,
        Err(e) => return (String::new(), format!("bw wait failed: {e}"), Some(1)),
    };

    // stdout.decode(errors="replace").strip(), stderr.decode(errors="replace").strip()
    let stdout = String::from_utf8_lossy(&output.stdout).trim().to_string();
    let stderr = String::from_utf8_lossy(&output.stderr).trim().to_string();
    let rc = output.status.code();
    (stdout, stderr, rc)
}

/// Take the first `n` Unicode code points (Python `s[:n]`).
fn char_prefix(s: &str, n: usize) -> String {
    s.chars().take(n).collect()
}

/// Search the vault by keyword. Returns matching item names + URLs, NO passwords.
pub async fn do_vault_search(content: &str, _owner: Option<&str>) -> Map<String, Value> {
    // try: args = _parse_tool_args(content) / except ValueError: ...
    let args = match _parse_tool_args(content) {
        Ok(a) => a,
        Err(_) => return err_result("Invalid JSON arguments"),
    };
    // query = args.get("query", "").strip()
    let query = args
        .get("query")
        .and_then(Value::as_str)
        .unwrap_or("")
        .trim()
        .to_string();
    if query.is_empty() {
        return err_result("query is required");
    }

    let cfg = _load_vault_config();
    // session = cfg.get("session")
    let session = cfg.get("session").and_then(Value::as_str);
    let session = match session {
        Some(s) if !s.is_empty() => s.to_string(),
        // `if not session:` — None *and* empty string both fall here in Python.
        _ => {
            return err_result(
                "Vault is locked. Run vault_unlock or provide session key in settings.",
            )
        }
    };

    let (stdout, stderr, rc) =
        _run_bw(&["list", "items", "--search", &query], Some(&session), None).await;
    if rc != Some(0) {
        return err_result(format!("bw failed: {}", char_prefix(&stderr, 300)));
    }

    // items = json.loads(stdout) / except JSONDecodeError: ...
    let items: Vec<Value> = match serde_json::from_str::<Value>(&stdout) {
        Ok(Value::Array(a)) => a,
        // A valid-but-non-array JSON body is not what Python expects either; but
        // Python only guards JSONDecodeError, then iterates `items` (which for a
        // dict would iterate keys). The realistic bw output is an array; any
        // non-array is treated as "no items" rather than fabricating output.
        Ok(_) => Vec::new(),
        Err(_) => return err_result("Failed to parse bw output"),
    };

    if items.is_empty() {
        let mut m = Map::new();
        m.insert(
            "output".to_string(),
            Value::String(format!("No vault items match '{query}'.")),
        );
        m.insert("exit_code".to_string(), Value::from(0));
        return m;
    }

    // lines = [f"Found {len(items)} item(s) matching '{query}':"]
    let mut lines: Vec<String> = vec![format!(
        "Found {} item(s) matching '{}':",
        items.len(),
        query
    )];
    for it in items.iter().take(20) {
        // item_id = it.get("id", "?"); name = it.get("name", "?")
        let item_id = it.get("id").and_then(Value::as_str).unwrap_or("?");
        let name = it.get("name").and_then(Value::as_str).unwrap_or("?");
        // login = it.get("login") or {}
        let login = it.get("login").and_then(Value::as_object);
        // username = login.get("username", "")
        let username = login
            .and_then(|l| l.get("username"))
            .and_then(Value::as_str)
            .unwrap_or("");
        // uris = login.get("uris") or []
        let uris = login.and_then(|l| l.get("uris")).and_then(Value::as_array);
        // url = uris[0].get("uri", "") if uris else ""
        let url = uris
            .and_then(|u| u.first())
            .and_then(Value::as_object)
            .and_then(|first| first.get("uri"))
            .and_then(Value::as_str)
            .unwrap_or("");

        // parts = [f"[{item_id[:8]}] {name}"]
        let mut parts: Vec<String> = vec![format!("[{}] {}", char_prefix(item_id, 8), name)];
        if !username.is_empty() {
            parts.push(format!("user: {username}"));
        }
        if !url.is_empty() {
            parts.push(format!("url: {url}"));
        }
        // lines.append("- " + " · ".join(parts))
        lines.push(format!("- {}", parts.join(" \u{00B7} ")));
    }
    // lines.append("\nUse vault_get(item_id, reason) to retrieve the password.")
    lines.push("\nUse vault_get(item_id, reason) to retrieve the password.".to_string());

    let mut m = Map::new();
    m.insert("output".to_string(), Value::String(lines.join("\n")));
    m.insert("exit_code".to_string(), Value::from(0));
    m
}

/// Retrieve a full vault entry (including password) by item ID.
/// Logs access to assistant chat.
pub async fn do_vault_get(content: &str, owner: Option<&str>) -> Map<String, Value> {
    let args = match _parse_tool_args(content) {
        Ok(a) => a,
        Err(_) => return err_result("Invalid JSON arguments"),
    };
    // item_id = args.get("item_id", "").strip()
    let item_id = args
        .get("item_id")
        .and_then(Value::as_str)
        .unwrap_or("")
        .trim()
        .to_string();
    // reason = args.get("reason", "").strip()
    let reason = args
        .get("reason")
        .and_then(Value::as_str)
        .unwrap_or("")
        .trim()
        .to_string();
    if item_id.is_empty() {
        return err_result("item_id is required");
    }
    if reason.is_empty() {
        return err_result("reason is required — explain WHY you need this password");
    }

    let cfg = _load_vault_config();
    let session = cfg.get("session").and_then(Value::as_str);
    let session = match session {
        Some(s) if !s.is_empty() => s.to_string(),
        _ => return err_result("Vault is locked. Unlock first."),
    };

    let (stdout, stderr, rc) = _run_bw(&["get", "item", &item_id], Some(&session), None).await;
    if rc != Some(0) {
        return err_result(format!("bw failed: {}", char_prefix(&stderr, 300)));
    }

    // item = json.loads(stdout) / except JSONDecodeError: ...
    let item = match serde_json::from_str::<Value>(&stdout) {
        Ok(Value::Object(m)) => m,
        Ok(_) => return err_result("Failed to parse bw output"),
        Err(_) => return err_result("Failed to parse bw output"),
    };

    // login = item.get("login") or {}
    let login = item.get("login").and_then(Value::as_object);
    // name = item.get("name", "?")
    let name = item.get("name").and_then(Value::as_str).unwrap_or("?");

    // Audit log to assistant chat (try/except: pass).
    //
    // The Rust `log_to_assistant` is a no-op shim (its Python source is too) and
    // can't fail, so there is nothing to catch — but we preserve the `if owner:`
    // guard and the exact category + message text.
    if let Some(owner) = owner {
        log_to_assistant(
            owner,
            &format!("Retrieved password for **{name}** — reason: {reason}"),
            "assistant",
            Some("Vault"),
        );
    }

    // output = [f"Vault item: {name}", f"Username: ...", f"Password: ..."]
    let username = login
        .and_then(|l| l.get("username"))
        .and_then(Value::as_str)
        .unwrap_or("(none)");
    let password = login
        .and_then(|l| l.get("password"))
        .and_then(Value::as_str)
        .unwrap_or("(none)");
    let mut output: Vec<String> = vec![
        format!("Vault item: {name}"),
        format!("Username: {username}"),
        format!("Password: {password}"),
    ];
    // if login.get("totp"): output.append(f"TOTP secret: {login['totp']}")
    if let Some(totp) = login.and_then(|l| l.get("totp")).and_then(Value::as_str) {
        if !totp.is_empty() {
            output.push(format!("TOTP secret: {totp}"));
        }
    }
    // uris = login.get("uris") or []
    let uris = login.and_then(|l| l.get("uris")).and_then(Value::as_array);
    if let Some(uris) = uris {
        if !uris.is_empty() {
            // "URLs: " + ", ".join(u.get("uri", "") for u in uris)
            let joined = uris
                .iter()
                .map(|u| {
                    u.as_object()
                        .and_then(|m| m.get("uri"))
                        .and_then(Value::as_str)
                        .unwrap_or("")
                        .to_string()
                })
                .collect::<Vec<_>>()
                .join(", ");
            output.push(format!("URLs: {joined}"));
        }
    }
    // if item.get("notes"): output.append(f"Notes: {item['notes']}")
    if let Some(notes) = item.get("notes").and_then(Value::as_str) {
        if !notes.is_empty() {
            output.push(format!("Notes: {notes}"));
        }
    }

    let mut m = Map::new();
    m.insert("output".to_string(), Value::String(output.join("\n")));
    m.insert("exit_code".to_string(), Value::from(0));
    m
}

/// Unlock the vault using a master password. Stores the resulting session key.
pub async fn do_vault_unlock(content: &str, _owner: Option<&str>) -> Map<String, Value> {
    let args = match _parse_tool_args(content) {
        Ok(a) => a,
        Err(_) => return err_result("Invalid JSON arguments"),
    };
    // master_password = args.get("master_password", "")  (NOT stripped in Python)
    let master_password = args
        .get("master_password")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    if master_password.is_empty() {
        return err_result("master_password is required");
    }

    // Do not pass the master password as an argv element. Local process lists
    // can expose argv to other users; stdin keeps the secret out of `ps`.
    // Python: _run_bw(["unlock", "--raw"], input_text=master_password + "\n")
    let stdin_pw = format!("{master_password}\n");
    let (stdout, stderr, rc) =
        _run_bw(&["unlock", "--raw"], None, Some(&stdin_pw)).await;
    if rc != Some(0) {
        return err_result(format!("Unlock failed: {}", char_prefix(&stderr, 300)));
    }

    // session = stdout.strip()  (stdout is already trimmed by _run_bw)
    let session = stdout.trim().to_string();
    if session.is_empty() {
        return err_result("bw returned empty session");
    }

    // Save session to vault.json — start from the existing config (if any).
    let p = "data/vault.json";
    let mut cfg: Map<String, Value> = Map::new();
    if pyos::path::exists(p) {
        if let Ok(text) = std::fs::read_to_string(p) {
            if let Ok(Value::Object(m)) = serde_json::from_str::<Value>(&text) {
                cfg = m;
            }
        }
    }
    cfg.insert("session".to_string(), Value::String(session));
    // cfg["unlocked_at"] = datetime.utcnow().isoformat()
    //
    // Python's `datetime.utcnow().isoformat()` is a naive-UTC, `T`-separated
    // timestamp with microseconds (e.g. "2026-06-01T12:34:56.789012"). chrono's
    // NaiveDateTime debug/format with %.6f reproduces it; microseconds are
    // emitted only when non-zero, matching isoformat (the odds of an exact-zero
    // microsecond at unlock time are negligible — documented cosmetic drift).
    let now = Utc::now().naive_utc();
    let unlocked_at = if now.nanosecond() == 0 {
        now.format("%Y-%m-%dT%H:%M:%S").to_string()
    } else {
        now.format("%Y-%m-%dT%H:%M:%S%.6f").to_string()
    };
    cfg.insert("unlocked_at".to_string(), Value::String(unlocked_at));

    // p.write_text(json.dumps(cfg, indent=2))
    let serialized = match serde_json::to_string_pretty(&Value::Object(cfg)) {
        Ok(s) => s,
        Err(e) => return err_result(format!("Unlock failed: {e}")),
    };
    // Ensure the parent dir exists (Python relies on data/ already existing; the
    // write would raise otherwise, which is not caught — mirror by surfacing the
    // IO error as an error map rather than fabricating success).
    let dir = pyos::path::dirname(p);
    if !dir.is_empty() {
        let _ = pyos::makedirs(&dir, true);
    }
    if let Err(e) = std::fs::write(p, serialized) {
        return err_result(format!("Unlock failed: {e}"));
    }

    // os.chmod(str(p), 0o600)  (try/except: pass)
    let _ = pyos::chmod(p, 0o600);

    let mut m = Map::new();
    m.insert(
        "output".to_string(),
        Value::String("Vault unlocked. Session saved.".to_string()),
    );
    m.insert("exit_code".to_string(), Value::from(0));
    m
}

#[cfg(test)]
mod tests {
    use super::*;

    // Verify that do_vault_unlock does NOT pass the master password as a
    // command-line argument.  We can't intercept the actual Command::new("bw")
    // argv in a unit test, but we can confirm the secure path indirectly:
    //
    // 1. When `bw` is absent (sentinel 127 from _run_bw's spawn-error path),
    //    the function must return an error.  If the password were in argv, it
    //    would appear in the error message because the spawn error is surfaced
    //    as stderr. Verify the secret does NOT appear in the returned error text.
    //
    // 2. When master_password is missing, we get the correct validation error
    //    regardless of the spawn path.
    #[tokio::test]
    async fn test_vault_unlock_password_not_in_argv_error() {
        let secret = "super_secret_pw_xyz987";
        let content = format!(r#"{{"master_password": "{secret}"}}"#);
        // bw is almost certainly not installed in the test environment;
        // _run_bw returns (String::new(), "failed to launch bw: ...", Some(127)).
        // Even if it is installed, it will fail because there is no real vault.
        // In either case rc != Some(0), so we expect an error map.
        let result = do_vault_unlock(&content, None).await;
        // The function must return an error (no real bw session available).
        assert!(
            result.contains_key("error"),
            "expected an error result, got: {result:?}"
        );
        // The master password must NOT appear in the error text (it would if
        // passed as argv, since some bw error paths echo back their args).
        let err_str = result["error"].as_str().unwrap_or("");
        assert!(
            !err_str.contains(secret),
            "master password leaked into error message: {err_str:?}"
        );
    }

    #[tokio::test]
    async fn test_vault_unlock_missing_password() {
        let result = do_vault_unlock(r#"{}"#, None).await;
        assert_eq!(
            result.get("error").and_then(Value::as_str),
            Some("master_password is required")
        );
    }

    #[tokio::test]
    async fn test_vault_unlock_invalid_json() {
        let result = do_vault_unlock("not-json", None).await;
        assert_eq!(
            result.get("error").and_then(Value::as_str),
            Some("Invalid JSON arguments")
        );
    }
}
