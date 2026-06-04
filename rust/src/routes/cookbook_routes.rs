// routes/cookbook_routes.rs  <- routes/cookbook_routes.py
//! Cookbook routes — model download, serve, cache scanning, and cookbook state sync.
//!
//! Wave-6 DOCS port (app.py include #28). The full `tokio::process` orchestration
//! surface: ssh-key generation, HuggingFace model download in a tmux session,
//! cached-model scanning, model serve (vLLM / llama.cpp / sglang / ollama /
//! diffusion), remote server setup, GPU probing (nvidia-smi / AMD sysfs /
//! /dev/dri holders), PID killing, cross-device cookbook state persistence (with
//! encrypted HF token), the HuggingFace "latest models" pool fetch, and the tmux
//! task-status poller.
//!
//! Binds to the already-ported [`crate::routes::cookbook_helpers`] validators /
//! request bodies / serve-phase parser.
//!
//! ## TMUX_LOG_DIR — first definer
//! `routes/cookbook_routes.py` imports `TMUX_LOG_DIR` from `routes/shell_routes.py`
//! (`Path(tempfile.gettempdir()) / "odysseus-tmux"`). `shell_routes` is a wave-7
//! port that does not exist yet, so this module DEFINES the shared
//! [`TMUX_LOG_DIR`] here (its first definer). When `shell_routes` lands it reuses
//! this constant rather than re-defining it.
//!
//! ## Faithfulness notes
//! * `_cookbook_state_path` is `Path(os.environ.get("DATA_DIR", "data")) /
//!   "cookbook_state.json"` — the `DATA_DIR` ENV VAR with a literal `"data"`
//!   default, NOT `crate::core::constants::DATA_DIR` (which uses
//!   `ODYSSEUS_DATA_DIR`/`BASE_DIR`). Mirrored exactly so the on-disk location
//!   matches the Python.
//! * `asyncio.create_subprocess_shell(cmd)` runs `/bin/sh -c cmd`; the axum port
//!   uses `Command::new("sh").arg("-c").arg(cmd)`. `create_subprocess_exec(prog,
//!   *args)` -> `Command::new(prog).args(args)`.
//! * The shell/PowerShell escaping (`shlex.quote`, `_bash_squote`, `_ps_squote`,
//!   `repr()`) is reproduced byte-for-byte via the helper crate + a local
//!   `py_repr`.
//! * `_cookbook_tasks_status_sync` was run via `asyncio.to_thread` (blocking
//!   `subprocess.run`); the port is a plain async fn using `tokio::process`
//!   (already non-blocking), so no worker-thread hop is needed.


use std::collections::{HashMap, HashSet};
use std::path::{Path as FsPath, PathBuf};
use std::process::Stdio;

use axum::extract::{Query, State};
use axum::http::HeaderMap;
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Extension, Json, Router};
use once_cell::sync::Lazy;
use regex::Regex;
use serde::Deserialize;
use serde_json::{json, Value};
use tokio::process::Command;

use crate::core::middleware::INTERNAL_TOOL_HEADER;
use crate::routes::auth_adapter;
use crate::routes::cookbook_helpers::{
    ModelDownloadRequest, ServeRequest, _bash_squote, _cached_model_scan_script, _parse_serve_phase,
    _ps_squote, _safe_env_prefix, _shell_path, _validate_gpus, _validate_include,
    _validate_local_dir, _validate_remote_host, _validate_repo_id, _validate_serve_cmd,
    _validate_serve_model_id, _validate_ssh_port, _validate_token,
};
use crate::routes::{AppState, CurrentUser, HttpException};

// ===========================================================================
// Shared regexes (mirrors of the helper-module `_SESSION_ID_RE` / `_REMOTE_HOST_RE`
// / `_SSH_PORT_RE`, re-declared locally for the tasks-status poller — the helper
// crate keeps them private/`#[allow(dead_code)]`).
// ===========================================================================

static _SESSION_ID_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"^[A-Za-z0-9_-]{1,64}$").unwrap());
static _REMOTE_HOST_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"^[A-Za-z0-9._-]+@[A-Za-z0-9._-]+$").unwrap());
static _SSH_PORT_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"^\d{1,5}$").unwrap());
// `_SSH_PORT_RE.fullmatch(...)` form: anchored at both ends (1-5 digits only).
static _SSH_PORT_FULL_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"^\d{1,5}$").unwrap());
// `re.fullmatch(r"\d{1,5}", port)` in server_setup.
static _PORT_5_FULL_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"^\d{1,5}$").unwrap());
// `re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._\-\[\]<>=!,~]{0,200}", repo_id)` (pip).
static _PIP_NAME_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"^[A-Za-z0-9][A-Za-z0-9._\-\[\]<>=!,~]{0,200}$").unwrap());
// `--port\s+(\d+)` in `_auto_register_image_endpoint`.
static _PORT_FLAG_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"--port\s+(\d+)").unwrap());
// `_needs_binary`: `(^|[\s;&|()])<binary>($|[\s;&|()])`.
static _NEEDS_BINARY_RE_DOCKER: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"(^|[\s;&|()])docker($|[\s;&|()])").unwrap());
// `re.search(r"=== process exited with code\s+(-?\d+)", full_snapshot, re.I)` — the
// tasks-status exit-marker parse (captures the numeric exit code).
static _EXIT_CODE_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"(?i)=== process exited with code\s+(-?\d+)").unwrap());
// `re.search(r"Fetching\s+0\s+files", full_snapshot, re.IGNORECASE)` — a 100%
// "complete" download that actually fetched nothing (bad repo / quant pattern).
static _FETCHING_ZERO_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"(?i)Fetching\s+0\s+files").unwrap());
// `re.findall(r"model-(\d+)-of-(\d+)\.safetensors", _out)` — the per-shard
// progress markers used by the state-POST stale-done anti-poisoning guard.
static _SHARD_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"model-(\d+)-of-(\d+)\.safetensors").unwrap());

// ===========================================================================
// TMUX_LOG_DIR — first definer (shell_routes will reuse it in wave-7)
// ===========================================================================

/// `TMUX_LOG_DIR = Path(tempfile.gettempdir()) / "odysseus-tmux"`.
///
/// The shared scratch dir for tmux wrapper scripts + scan code. `std::env::temp_dir()`
/// is the `tempfile.gettempdir()` equivalent (honors `$TMPDIR`).
pub fn tmux_log_dir() -> PathBuf {
    std::env::temp_dir().join("odysseus-tmux")
}

/// `_HF_TOKEN_STATUS_SNIPPET` — module-level bash snippet that echoes whether the
/// HF token reached the run (masked).
const HF_TOKEN_STATUS_SNIPPET: &str = concat!(
    "if [ -n \"$HF_TOKEN\" ]; then ",
    "echo \"[odysseus] HF token: applied\"; ",
    "else ",
    "echo \"[odysseus] HF token: NOT SET — gated/private models will be denied. ",
    "Add one in Odysseus Settings -> Cookbook -> HuggingFace Token.\"; ",
    "fi"
);

// ===========================================================================
// Cookbook state path + secret helpers (closures in the Python factory)
// ===========================================================================

/// `_cookbook_state_path = Path(os.environ.get("DATA_DIR", "data")) /
/// "cookbook_state.json"`. NOTE: the `DATA_DIR` ENV var with a `"data"` default —
/// not the constants module.
fn cookbook_state_path() -> PathBuf {
    let data_dir = std::env::var("DATA_DIR").unwrap_or_else(|_| "data".to_string());
    FsPath::new(&data_dir).join("cookbook_state.json")
}

/// `_mask_secret(value)`.
fn mask_secret(value: &str) -> String {
    if value.is_empty() {
        return String::new();
    }
    // `if len(value) <= 8: return "stored"` — len is Unicode code points.
    if value.chars().count() <= 8 {
        return "stored".to_string();
    }
    // `f"{value[:4]}...{value[-4:]}"` — first 4 + last 4 code points.
    let chars: Vec<char> = value.chars().collect();
    let n = chars.len();
    let first4: String = chars[..4].iter().collect();
    let last4: String = chars[n - 4..].iter().collect();
    format!("{first4}...{last4}")
}

/// `_decrypt_secret(value)` — `decrypt(value)` or `""` for empty/None.
fn decrypt_secret(value: Option<&str>) -> String {
    match value {
        None | Some("") => String::new(),
        Some(v) => crate::src::secret_storage::decrypt(v),
    }
}

/// `_encrypt_secret(value)` — `encrypt(value)`.
fn encrypt_secret(value: &str) -> String {
    crate::src::secret_storage::encrypt(value)
}

/// `_strip_task_secrets(state)` — drop `hf_token` from each task payload, in place.
fn strip_task_secrets(state: &mut Value) {
    // `tasks = state.get("tasks") if isinstance(state, dict) else None`
    if let Some(tasks) = state.get_mut("tasks").and_then(Value::as_array_mut) {
        for task in tasks.iter_mut() {
            if let Some(payload) = task.get_mut("payload").and_then(Value::as_object_mut) {
                payload.remove("hf_token");
            }
        }
    }
}

/// `_state_for_client(state)` — strip raw secrets for browser clients (mutate +
/// return).
fn state_for_client(mut state: Value) -> Value {
    strip_task_secrets(&mut state);
    // `env = state.get("env") if isinstance(state, dict) else None`
    if let Some(env) = state.get_mut("env").and_then(Value::as_object_mut) {
        let token = decrypt_secret(env.get("hfToken").and_then(Value::as_str));
        env.remove("hfToken");
        env.insert("hfTokenConfigured".to_string(), Value::Bool(!token.is_empty()));
        env.insert("hfTokenMasked".to_string(), Value::String(mask_secret(&token)));
    }
    state
}

/// `_state_for_storage(state, on_disk=None)` — encrypt secrets before writing.
fn state_for_storage(mut state: Value, on_disk: &Value) -> R<Value> {
    strip_task_secrets(&mut state);
    // disk_env = on_disk.get("env") if isinstance(on_disk, dict) and isinstance(...) else {}
    let disk_hf: Option<String> = on_disk
        .get("env")
        .and_then(Value::as_object)
        .and_then(|e| e.get("hfToken"))
        .and_then(Value::as_str)
        .map(str::to_string);
    if let Some(env) = state.get_mut("env").and_then(Value::as_object_mut) {
        // `incoming = env.get("hfToken")` — truthy iff present and non-empty string.
        let incoming = env.get("hfToken").and_then(Value::as_str).map(str::to_string);
        match incoming {
            Some(ref v) if !v.is_empty() => {
                _validate_token(Some(v))?;
                env.insert("hfToken".to_string(), Value::String(encrypt_secret(v)));
            }
            _ => {
                // `elif disk_env.get("hfToken"):` — non-empty on-disk token.
                if let Some(disk) = disk_hf.filter(|d| !d.is_empty()) {
                    env.insert("hfToken".to_string(), Value::String(disk));
                } else {
                    env.remove("hfToken");
                }
            }
        }
        env.remove("hfTokenMasked");
        env.remove("hfTokenConfigured");
    }
    Ok(state)
}

/// `_load_stored_hf_token()` — read + decrypt the saved HF token, or `""`.
fn load_stored_hf_token() -> String {
    let path = cookbook_state_path();
    if !path.exists() {
        return String::new();
    }
    // `try: ... except Exception: return ""`
    let text = match std::fs::read_to_string(&path) {
        Ok(t) => t,
        Err(_) => return String::new(),
    };
    let state: Value = match serde_json::from_str(&text) {
        Ok(v) => v,
        Err(_) => return String::new(),
    };
    let token = state
        .get("env")
        .and_then(Value::as_object)
        .and_then(|e| e.get("hfToken"))
        .and_then(Value::as_str);
    decrypt_secret(token)
}

/// `_cookbook_ssh_dir()`.
fn cookbook_ssh_dir() -> PathBuf {
    // `if Path("/app").exists(): return Path("/app/.ssh")` else `~/.ssh`
    if FsPath::new("/app").exists() {
        return PathBuf::from("/app/.ssh");
    }
    PathBuf::from(home_dir()).join(".ssh")
}

/// `_cookbook_ssh_key_path()` — `<ssh_dir>/id_ed25519`.
fn cookbook_ssh_key_path() -> PathBuf {
    cookbook_ssh_dir().join("id_ed25519")
}

/// `_read_cookbook_public_key()` — read `<key>.pub`, stripped, or `""`.
fn read_cookbook_public_key() -> String {
    // `pub = _cookbook_ssh_key_path().with_suffix(".pub")`
    let pub_path = cookbook_ssh_key_path().with_extension("pub");
    if !pub_path.exists() {
        return String::new();
    }
    // `pub.read_text(encoding="utf-8", errors="replace").strip()`
    match std::fs::read(&pub_path) {
        Ok(bytes) => String::from_utf8_lossy(&bytes).trim().to_string(),
        Err(_) => String::new(),
    }
}

// ===========================================================================
// `_user_shell_path_bootstrap` / `_needs_binary` / `_missing_binary_message`
// ===========================================================================

/// `_user_shell_path_bootstrap()` — the bash prelude that pulls the user's login
/// shell PATH into the non-login runner.
fn user_shell_path_bootstrap() -> Vec<String> {
    vec![
        r#"ODYSSEUS_USER_SHELL="${SHELL:-}""#.to_string(),
        r#"if [ -n "$ODYSSEUS_USER_SHELL" ] && [ -x "$ODYSSEUS_USER_SHELL" ]; then"#.to_string(),
        r#"  ODYSSEUS_USER_PATH="$("$ODYSSEUS_USER_SHELL" -ic 'printf "__ODYSSEUS_PATH__%s\n" "$PATH"' 2>/dev/null | sed -n 's/^__ODYSSEUS_PATH__//p' | tail -n 1 || true)""#.to_string(),
        r#"  if [ -n "$ODYSSEUS_USER_PATH" ]; then export PATH="$ODYSSEUS_USER_PATH:$PATH"; fi"#.to_string(),
        "fi".to_string(),
    ]
}

/// `_needs_binary(cmd, binary)` — true if `binary` appears as a whole word in `cmd`.
/// Only `docker` is ever checked here, so a single precompiled regex suffices.
fn needs_binary_docker(cmd: &str) -> bool {
    _NEEDS_BINARY_RE_DOCKER.is_match(cmd)
}

/// `_missing_binary_message(binary, target)`.
fn missing_binary_message(binary: &str, target: &str) -> String {
    match binary {
        "tmux" => format!(
            "tmux is required for Cookbook background downloads/serves on {target}. \
Install it with your OS package manager, or run Cookbook server setup for that server."
        ),
        "docker" => format!(
            "Docker is required by this Cookbook launch command on {target}, but the docker CLI was not found. \
Install Docker and make sure this user can run `docker`, then retry."
        ),
        _ => format!("{binary} is required on {target}, but it was not found."),
    }
}

/// `_remote_binary_available(remote, ssh_port, binary, windows=False)`.
async fn remote_binary_available(
    remote: &str,
    ssh_port: Option<&str>,
    binary: &str,
    windows: bool,
) -> bool {
    // `_port = ssh_port or ""`
    let port = ssh_port.unwrap_or("");
    // `_pf = ["-p", _port] if _port and _port != "22" else []`
    let pf: Vec<&str> = if !port.is_empty() && port != "22" {
        vec!["-p", port]
    } else {
        vec![]
    };
    let check = if windows {
        format!(
            "powershell -NoProfile -Command \"if (Get-Command {binary} -ErrorAction SilentlyContinue) {{ exit 0 }} else {{ exit 127 }}\""
        )
    } else {
        // `command -v {shlex.quote(binary)} >/dev/null 2>&1`
        format!("command -v {} >/dev/null 2>&1", shquote(binary))
    };
    // `ssh -o ConnectTimeout=6 -o StrictHostKeyChecking=no [*_pf] remote check`
    let mut cmd = Command::new("ssh");
    cmd.args(["-o", "ConnectTimeout=6", "-o", "StrictHostKeyChecking=no"]);
    for a in &pf {
        cmd.arg(a);
    }
    cmd.arg(remote).arg(&check);
    cmd.stdout(Stdio::piped()).stderr(Stdio::piped());
    // `await asyncio.wait_for(proc.communicate(), timeout=10); return rc == 0`
    match run_with_timeout(cmd, 10).await {
        Ok(Some(out)) => out.status.success(),
        // TimeoutError / launch error -> `except Exception: return False`.
        _ => false,
    }
}

/// `_binary_available(binary, remote, ssh_port, windows=False)`.
async fn binary_available(
    binary: &str,
    remote: Option<&str>,
    ssh_port: Option<&str>,
    windows: bool,
) -> bool {
    if let Some(remote) = remote.filter(|r| !r.is_empty()) {
        return remote_binary_available(remote, ssh_port, binary, windows).await;
    }
    // `return shutil.which(binary) is not None`
    shutil_which(binary).is_some()
}

// ===========================================================================
// `_diagnose_serve_output` — server-side mirror of the UI's serve diagnoses
// ===========================================================================

/// One diagnosis rule: a (case-insensitive) pattern, a message, and the
/// structured suggestion list. The suggestion shapes are heterogeneous dicts, so
/// they're stored as pre-built `serde_json::Value`s (in the exact Python key order).
struct DiagRule {
    re: Regex,
    message: &'static str,
    suggestions: Vec<Value>,
}

static _DIAG_RULES: Lazy<Vec<DiagRule>> = Lazy::new(|| {
    // Helper: `(?i)` case-insensitive, matching Python `re.search(pat, tail, re.I)`.
    let ci = |p: &str| Regex::new(&format!("(?i){p}")).unwrap();
    vec![
        DiagRule {
            re: ci(r"No available memory for the cache blocks|Available KV cache memory:.*-"),
            message: "No GPU memory left for KV cache after loading model.",
            suggestions: vec![
                json!({"label": "retry with GPU memory utilization 0.95", "op": "replace", "flag": "--gpu-memory-utilization", "value": "0.95"}),
                json!({"label": "retry with context 2048", "op": "replace", "flag": "--max-model-len", "value": "2048"}),
            ],
        },
        DiagRule {
            re: ci(r"CUDA out of memory|torch\.cuda\.OutOfMemoryError|CUDA error: out of memory|warming up sampler|max_num_seqs.*gpu_memory_utilization"),
            message: "GPU ran out of memory during startup or warmup.",
            suggestions: vec![
                json!({"label": "retry with context 4096", "op": "replace", "flag": "--max-model-len", "value": "4096"}),
                json!({"label": "retry with GPU memory utilization 0.80", "op": "replace", "flag": "--gpu-memory-utilization", "value": "0.80"}),
                json!({"label": "retry with --enforce-eager", "op": "append", "arg": "--enforce-eager"}),
            ],
        },
        DiagRule {
            re: ci(r"not divisib|must be divisible|attention heads.*divisible"),
            message: "Tensor parallel size is incompatible with the model.",
            suggestions: vec![
                json!({"label": "retry with tensor parallel size 1", "op": "replace", "flag": "--tensor-parallel-size", "value": "1"}),
                json!({"label": "retry with tensor parallel size 2", "op": "replace", "flag": "--tensor-parallel-size", "value": "2"}),
            ],
        },
        DiagRule {
            re: ci(r"KV cache.*too (small|large)|max_model_len.*exceeds|maximum.*context"),
            message: "Context length is too large for available GPU memory.",
            suggestions: vec![
                json!({"label": "retry with context 8192", "op": "replace", "flag": "--max-model-len", "value": "8192"}),
                json!({"label": "retry with context 4096", "op": "replace", "flag": "--max-model-len", "value": "4096"}),
            ],
        },
        DiagRule {
            re: ci(r"enable-auto-tool-choice requires --tool-call-parser"),
            message: "Auto tool choice requires an explicit tool call parser.",
            suggestions: vec![json!({"label": "retry with Hermes tool parser", "op": "append", "arg": "--tool-call-parser hermes"})],
        },
        DiagRule {
            re: ci(r"Please pass.*trust.remote.code=True|contains custom code which must be executed to correctly load|does not recognize this architecture|model type.*but Transformers does not"),
            message: "Model requires custom code or newer model support.",
            suggestions: vec![json!({"label": "retry with --trust-remote-code", "op": "append", "arg": "--trust-remote-code"})],
        },
        DiagRule {
            re: ci(r"Either a revision or a version must be specified|transformers\.integrations\.hub_kernels|kernels/layer"),
            message: "vLLM/Transformers kernel package mismatch.",
            suggestions: vec![json!({"label": "update vLLM, Transformers, and kernels on this server", "op": "dependency", "package": "vllm transformers kernels"})],
        },
        DiagRule {
            re: ci(r"Address already in use|bind.*address.*in use"),
            message: "Port is already in use.",
            suggestions: vec![json!({"label": "retry on port 8001", "op": "replace", "flag": "--port", "value": "8001"})],
        },
        DiagRule {
            re: ci(r"No CUDA GPUs are available|no GPU.*found|CUDA_VISIBLE_DEVICES.*invalid"),
            message: "No GPUs are visible to the serve process.",
            suggestions: vec![json!({"label": "clear Cookbook GPU selection or choose available GPUs", "op": "settings", "field": "gpus", "value": ""})],
        },
        DiagRule {
            re: ci(r"Failed to infer device type|NVML Shared Library Not Found|No module named 'amdsmi'|platform is not available"),
            message: "vLLM could not find a supported GPU (CUDA or ROCm). \
This machine may have integrated or unsupported graphics only.",
            suggestions: vec![
                json!({"label": "switch to llama.cpp (CPU/Metal, works without a discrete GPU)", "op": "manual"}),
                json!({"label": "switch to Ollama (CPU/Metal, works without a discrete GPU)", "op": "manual"}),
            ],
        },
        DiagRule {
            re: ci(r"vllm.*command not found|No module named vllm|ERROR: vLLM is not installed"),
            message: "vLLM is not installed or not in PATH on this server.",
            suggestions: vec![json!({"label": "install vLLM in Cookbook Dependencies", "op": "dependency", "package": "vllm"})],
        },
        DiagRule {
            re: ci(r"sglang.*command not found|No module named sglang|SGLang is not installed"),
            message: "SGLang is not installed or not in PATH on this server.",
            suggestions: vec![json!({"label": "install SGLang in Cookbook Dependencies", "op": "dependency", "package": "sglang[all]"})],
        },
        DiagRule {
            re: ci(r"llama-server.*command not found|llama\.cpp.*not found|No module named.*llama_cpp|No module named 'starlette_context'|git: command not found|cmake: command not found"),
            message: "llama.cpp / llama-cpp-python dependencies are missing.",
            suggestions: vec![json!({"label": "install llama.cpp dependencies or llama-cpp-python[server]", "op": "dependency", "package": "llama-cpp-python[server]"})],
        },
        DiagRule {
            re: ci(r"No GGUF found on this host|no \.gguf file|No GGUF file found"),
            message: "No GGUF file found for this model on this host. The llama.cpp backend needs a .gguf file.",
            suggestions: vec![json!({"label": "download a GGUF build of this model (repo name usually ends in -GGUF, file like Q4_K_M.gguf)", "op": "manual"})],
        },
        DiagRule {
            re: ci(r"No module named 'torch'|No module named torch|No module named 'diffusers'|No module named diffusers"),
            message: "Diffusion serving requires PyTorch and diffusers.",
            suggestions: vec![json!({"label": "install diffusers[torch] in Cookbook Dependencies", "op": "dependency", "package": "diffusers[torch]"})],
        },
        DiagRule {
            re: ci(r"403 Forbidden|401 Unauthorized|Access to model.*is restricted|gated repo|not in the authorized list|awaiting a review"),
            message: "Model access is gated or unauthorized.",
            suggestions: vec![json!({"label": "set HF token and request model access on HuggingFace", "op": "manual"})],
        },
    ]
});

static _TRACEBACK_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"(?i)Traceback \(most recent call last\)").unwrap());
static _TRACEBACK_OK_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"(?i)Application startup complete|GET /v1/|Uvicorn running on").unwrap()
});

/// `_diagnose_serve_output(text)` — structured serve diagnosis or `None`.
fn diagnose_serve_output(text: &str) -> Option<Value> {
    // `if not text: return None`
    if text.is_empty() {
        return None;
    }
    // `tail = text[-6000:]` — last 6000 code points.
    let tail = tail_chars(text, 6000);
    for rule in _DIAG_RULES.iter() {
        if rule.re.is_match(&tail) {
            return Some(json!({"message": rule.message, "suggestions": rule.suggestions}));
        }
    }
    // `if re.search(traceback) and not re.search(startup-complete):`
    if _TRACEBACK_RE.is_match(&tail) && !_TRACEBACK_OK_RE.is_match(&tail) {
        return Some(json!({
            "message": "Python traceback detected during serve startup.",
            "suggestions": [{"label": "inspect traceback and retry with adjusted backend/settings", "op": "manual"}],
        }));
    }
    None
}

// ===========================================================================
// `_auto_register_image_endpoint` — register a diffusion model as an image endpoint
// ===========================================================================

/// `_auto_register_image_endpoint(req, remote)` — upsert a `model_endpoints` row
/// of `model_type="image"` so the diffusion server appears in the model selector.
/// Returns the endpoint id, or `None` on error.
fn auto_register_image_endpoint(req: &ServeRequest, remote: Option<&str>) -> Option<String> {
    // `port_match = re.search(r'--port\s+(\d+)', req.cmd); port = int(...) if match else 8100`
    let port: i64 = _PORT_FLAG_RE
        .captures(&req.cmd)
        .and_then(|c| c.get(1))
        .and_then(|m| m.as_str().parse().ok())
        .unwrap_or(8100);

    // host = remote.split("@")[-1] if "@" in remote else remote; else "localhost"
    let host = match remote.filter(|r| !r.is_empty()) {
        Some(r) => {
            if r.contains('@') {
                r.rsplit('@').next().unwrap_or(r).to_string()
            } else {
                r.to_string()
            }
        }
        None => "localhost".to_string(),
    };
    let base_url = format!("http://{host}:{port}/v1");

    // short_name = repo_id.split("/")[-1] if "/" in repo_id else repo_id
    let short_name = if req.repo_id.contains('/') {
        req.repo_id.rsplit('/').next().unwrap_or(&req.repo_id).to_string()
    } else {
        req.repo_id.clone()
    };
    let display_name = format!("{short_name} (image)");

    let conn = match crate::core::database::session_local() {
        Ok(c) => c,
        Err(e) => {
            crate::pylog::error(&format!("Failed to auto-register image endpoint: {e}"));
            return None;
        }
    };

    // `existing = db.query(ModelEndpoint).filter(base_url == base_url).first()`
    use rusqlite::OptionalExtension;
    let existing: Result<Option<String>, rusqlite::Error> = conn
        .query_row(
            "SELECT id FROM model_endpoints WHERE base_url = ?1",
            rusqlite::params![base_url],
            |r| r.get::<_, String>(0),
        )
        .optional();

    match existing {
        Ok(Some(existing_id)) => {
            // Update it: is_enabled=True, model_type="image", name=display_name.
            // (the SQLAlchemy commit also bumps updated_at via the onupdate hook).
            let now = crate::pydatetime::utcnow_naive_iso();
            let res = conn.execute(
                "UPDATE model_endpoints SET is_enabled = 1, model_type = 'image', name = ?2, updated_at = ?3 WHERE id = ?1",
                rusqlite::params![existing_id, display_name, now],
            );
            match res {
                Ok(_) => {
                    crate::pylog::info(&format!("Updated existing image endpoint: {base_url}"));
                    Some(existing_id)
                }
                Err(e) => {
                    crate::pylog::error(&format!("Failed to auto-register image endpoint: {e}"));
                    None
                }
            }
        }
        Ok(None) => {
            // `ep_id = f"img-{uuid.uuid4().hex[:8]}"`
            let ep_id = format!("img-{}", &uuid::Uuid::new_v4().simple().to_string()[..8]);
            let now = crate::pydatetime::utcnow_naive_iso();
            let api_key: Option<String> = None;
            let res = conn.execute(
                "INSERT INTO model_endpoints \
                   (id, name, base_url, api_key, is_enabled, model_type, created_at, updated_at) \
                 VALUES (?1, ?2, ?3, ?4, 1, 'image', ?5, ?5)",
                rusqlite::params![ep_id, display_name, base_url, api_key, now],
            );
            match res {
                Ok(_) => {
                    crate::pylog::info(&format!(
                        "Auto-registered image endpoint: {display_name} @ {base_url}"
                    ));
                    Some(ep_id)
                }
                Err(e) => {
                    crate::pylog::error(&format!("Failed to auto-register image endpoint: {e}"));
                    None
                }
            }
        }
        Err(e) => {
            crate::pylog::error(&format!("Failed to auto-register image endpoint: {e}"));
            None
        }
    }
}

// ===========================================================================
// `_auto_register_llm_endpoint` — register a freshly-served LLM as a model endpoint
// ===========================================================================

/// `_auto_register_llm_endpoint(req, remote)` — the text-model sibling of
/// [`auto_register_image_endpoint`]. Upserts a `model_endpoints` row of
/// `model_type="llm"` pointed at the served OpenAI-compatible server's `/v1` so it
/// appears in the model picker with no manual `/setup` step. Returns the endpoint
/// id, or `None` on error.
fn auto_register_llm_endpoint(req: &ServeRequest, remote: Option<&str>) -> Option<String> {
    // Port: an explicit `--port` wins. Otherwise fall back by backend — Ollama is
    // the only server in our generated commands that omits `--port`.
    let port: i64 = if let Some(p) = _PORT_FLAG_RE
        .captures(&req.cmd)
        .and_then(|c| c.get(1))
        .and_then(|m| m.as_str().parse().ok())
    {
        p
    } else if req.cmd.contains("ollama") {
        11434
    } else {
        // llama.cpp's llama-server default — the Apple Silicon path.
        8080
    };

    // host = remote.split("@")[-1] if "@" in remote else remote; else "localhost"
    let host = match remote.filter(|r| !r.is_empty()) {
        Some(r) => {
            if r.contains('@') {
                r.rsplit('@').next().unwrap_or(r).to_string()
            } else {
                r.to_string()
            }
        }
        None => "localhost".to_string(),
    };
    let base_url = format!("http://{host}:{port}/v1");

    // short_name = repo_id.split("/")[-1] if "/" in repo_id else repo_id
    let short_name = if req.repo_id.contains('/') {
        req.repo_id.rsplit('/').next().unwrap_or(&req.repo_id).to_string()
    } else {
        req.repo_id.clone()
    };
    // display_name = short_name or "Local model"
    let display_name = if short_name.is_empty() {
        "Local model".to_string()
    } else {
        short_name
    };

    // `supports_tools = True if "--enable-auto-tool-choice" in req.cmd else None`
    let supports_tools: Option<bool> = if req.cmd.contains("--enable-auto-tool-choice") {
        Some(true)
    } else {
        None
    };

    let conn = match crate::core::database::session_local() {
        Ok(c) => c,
        Err(e) => {
            crate::pylog::error(&format!("Failed to auto-register local model endpoint: {e}"));
            return None;
        }
    };

    use rusqlite::OptionalExtension;
    let existing: Result<Option<String>, rusqlite::Error> = conn
        .query_row(
            "SELECT id FROM model_endpoints WHERE base_url = ?1",
            rusqlite::params![base_url],
            |r| r.get::<_, String>(0),
        )
        .optional();

    match existing {
        Ok(Some(existing_id)) => {
            // Update it: is_enabled=True, model_type="llm", name=display_name, and
            // supports_tools only when not None (matching the Python guard).
            let now = crate::pydatetime::utcnow_naive_iso();
            let res = if let Some(st) = supports_tools {
                conn.execute(
                    "UPDATE model_endpoints SET is_enabled = 1, model_type = 'llm', name = ?2, supports_tools = ?3, updated_at = ?4 WHERE id = ?1",
                    rusqlite::params![existing_id, display_name, st, now],
                )
            } else {
                conn.execute(
                    "UPDATE model_endpoints SET is_enabled = 1, model_type = 'llm', name = ?2, updated_at = ?3 WHERE id = ?1",
                    rusqlite::params![existing_id, display_name, now],
                )
            };
            match res {
                Ok(_) => {
                    crate::pylog::info(&format!("Updated existing local model endpoint: {base_url}"));
                    Some(existing_id)
                }
                Err(e) => {
                    crate::pylog::error(&format!("Failed to auto-register local model endpoint: {e}"));
                    None
                }
            }
        }
        Ok(None) => {
            // `ep_id = f"local-{uuid.uuid4().hex[:8]}"`
            let ep_id = format!("local-{}", &uuid::Uuid::new_v4().simple().to_string()[..8]);
            let now = crate::pydatetime::utcnow_naive_iso();
            let api_key: Option<String> = None;
            let res = conn.execute(
                "INSERT INTO model_endpoints \
                   (id, name, base_url, api_key, is_enabled, model_type, supports_tools, created_at, updated_at) \
                 VALUES (?1, ?2, ?3, ?4, 1, 'llm', ?5, ?6, ?6)",
                rusqlite::params![ep_id, display_name, base_url, api_key, supports_tools, now],
            );
            match res {
                Ok(_) => {
                    crate::pylog::info(&format!(
                        "Auto-registered local model endpoint: {display_name} @ {base_url}"
                    ));
                    Some(ep_id)
                }
                Err(e) => {
                    crate::pylog::error(&format!("Failed to auto-register local model endpoint: {e}"));
                    None
                }
            }
        }
        Err(e) => {
            crate::pylog::error(&format!("Failed to auto-register local model endpoint: {e}"));
            None
        }
    }
}

// ===========================================================================
// Router factory (`setup_cookbook_routes`)
// ===========================================================================

/// `setup_cookbook_routes()` — `APIRouter(tags=["cookbook"])`.
///
/// app.py registers this as include-router #28. Each handler is mounted at the
/// exact absolute path the Python `@router.get/post(...)` decorator names.
pub fn setup_cookbook_routes() -> Router<AppState> {
    Router::new()
        // `@router.get("/api/cookbook/ssh-key")` + `@router.post(...)`
        .route(
            "/api/cookbook/ssh-key",
            get(get_cookbook_ssh_key).post(generate_cookbook_ssh_key),
        )
        // `@router.post("/api/model/download")`
        .route("/api/model/download", post(model_download))
        // `@router.get("/api/model/cached")`
        .route("/api/model/cached", get(model_cached))
        // `@router.post("/api/model/serve")`
        .route("/api/model/serve", post(model_serve))
        // `@router.post("/api/cookbook/setup")`
        .route("/api/cookbook/setup", post(server_setup))
        // `@router.get("/api/cookbook/gpus")`
        .route("/api/cookbook/gpus", get(list_gpus))
        // `@router.post("/api/cookbook/kill-pid")`
        .route("/api/cookbook/kill-pid", post(kill_pid))
        // `@router.get("/api/cookbook/state")` + `@router.post(...)`
        .route(
            "/api/cookbook/state",
            get(get_cookbook_state).post(save_cookbook_state),
        )
        // `@router.get("/api/cookbook/hf-latest")`
        .route("/api/cookbook/hf-latest", get(hf_latest))
        // `@router.get("/api/cookbook/tasks/status")`
        .route("/api/cookbook/tasks/status", get(cookbook_tasks_status))
}

// ===========================================================================
// Auth glue
// ===========================================================================

/// `require_admin(request)` — header + stamped-user admin gate.
fn admin_gate(
    state: &AppState,
    headers: &HeaderMap,
    user: Option<&CurrentUser>,
) -> Result<(), HttpException> {
    let internal_header = headers.get(INTERNAL_TOOL_HEADER).and_then(|v| v.to_str().ok());
    let user = user.map(|u| u.0.as_str());
    auth_adapter::require_admin(state, internal_header, user)
}

/// `Depends(require_user)` — header-derived client host + stamped user.
fn require_user_gate(
    state: &AppState,
    headers: &HeaderMap,
    user: Option<&CurrentUser>,
) -> Result<String, HttpException> {
    // No `ConnectInfo` here (mirrors the rest of the wave routers); `None`
    // client host keeps `require_user` strict for the non-loopback case.
    let _ = headers;
    auth_adapter::require_user(user.map(|u| u.0.as_str()), state, None)
}

/// `get_current_user(request)` — the resolved username (or `None`), used by the
/// download/serve assistant-log calls.
fn current_user_opt(user: Option<&CurrentUser>) -> Option<String> {
    user.map(|u| u.0.clone())
}

// ===========================================================================
// Handlers
// ===========================================================================

/// `GET /api/cookbook/ssh-key` — report whether a cookbook SSH key exists.
async fn get_cookbook_ssh_key(
    State(state): State<AppState>,
    headers: HeaderMap,
    user: Option<Extension<CurrentUser>>,
) -> Result<Response, HttpException> {
    admin_gate(&state, &headers, user.as_deref())?;
    let public_key = read_cookbook_public_key();
    Ok(Json(json!({
        "configured": !public_key.is_empty(),
        "public_key": public_key,
    }))
    .into_response())
}

/// `POST /api/cookbook/ssh-key` — generate an ed25519 keypair if absent.
async fn generate_cookbook_ssh_key(
    State(state): State<AppState>,
    headers: HeaderMap,
    user: Option<Extension<CurrentUser>>,
) -> Result<Response, HttpException> {
    admin_gate(&state, &headers, user.as_deref())?;
    let ssh_dir = cookbook_ssh_dir();
    let key_path = cookbook_ssh_key_path();
    // `ssh_dir.mkdir(parents=True, exist_ok=True)`
    let _ = std::fs::create_dir_all(&ssh_dir);
    // `try: os.chmod(ssh_dir, 0o700) except Exception: pass`
    let _ = crate::pyos::chmod(&ssh_dir.to_string_lossy(), 0o700);

    if !key_path.exists() {
        // `ssh-keygen -t ed25519 -N "" -C odysseus-cookbook -f <key>`
        let key_str = key_path.to_string_lossy().into_owned();
        let mut cmd = Command::new("ssh-keygen");
        cmd.args([
            "-t",
            "ed25519",
            "-N",
            "",
            "-C",
            "odysseus-cookbook",
            "-f",
            &key_str,
        ]);
        cmd.stdout(Stdio::piped()).stderr(Stdio::piped());
        match cmd.output().await {
            Ok(out) if !out.status.success() => {
                // `detail = (stderr or stdout).decode(...).strip()[-500:]`
                let stderr = String::from_utf8_lossy(&out.stderr);
                let chosen = if !out.stderr.is_empty() {
                    stderr.into_owned()
                } else {
                    String::from_utf8_lossy(&out.stdout).into_owned()
                };
                let trimmed = chosen.trim();
                let detail = tail_chars(trimmed, 500);
                let detail = if detail.is_empty() {
                    "Failed to generate SSH key".to_string()
                } else {
                    detail
                };
                return Ok(Json(json!({"ok": false, "error": detail})).into_response());
            }
            Ok(_) => {}
            // A launch error (ssh-keygen missing) is NOT caught in Python — the
            // subprocess would raise. But create_subprocess_exec failing to find
            // the binary raises FileNotFoundError, which propagates as a 500.
            Err(e) => {
                crate::pylog::error(&format!("ssh-keygen launch failed: {e}"));
                return Err(HttpException::new(500, "Internal Server Error"));
            }
        }
    }
    // `try: os.chmod(...) except Exception: pass`
    let _ = crate::pyos::chmod(&key_path.to_string_lossy(), 0o600);
    let _ = crate::pyos::chmod(&key_path.with_extension("pub").to_string_lossy(), 0o644);
    Ok(Json(json!({"ok": true, "public_key": read_cookbook_public_key()})).into_response())
}

/// `POST /api/model/download` — kick off a HuggingFace download in a tmux session.
async fn model_download(
    State(state): State<AppState>,
    headers: HeaderMap,
    user: Option<Extension<CurrentUser>>,
    Json(mut req): Json<ModelDownloadRequest>,
) -> Result<Response, HttpException> {
    admin_gate(&state, &headers, user.as_deref())?;
    // Validation (each `_validate_*` raises HTTPException(400, ...) on bad input).
    _validate_repo_id(Some(&req.repo_id))?;
    _validate_include(req.include.as_deref())?;
    _validate_remote_host(req.remote_host.as_deref())?;
    req.ssh_port = _validate_ssh_port(req.ssh_port.as_deref())?;
    req.local_dir = _validate_local_dir(req.local_dir.as_deref())?;
    // `req.hf_token = req.hf_token or _load_stored_hf_token()`
    let hf_token = nonempty(req.hf_token.clone()).unwrap_or_else(load_stored_hf_token);
    req.hf_token = if hf_token.is_empty() { None } else { Some(hf_token.clone()) };
    _validate_token(nonempty_ref(&req.hf_token))?;

    let log_dir = tmux_log_dir();
    let _ = std::fs::create_dir_all(&log_dir);
    // `session_id = f"cookbook-{uuid.uuid4().hex[:8]}"`
    let session_id = format!("cookbook-{}", &uuid::Uuid::new_v4().simple().to_string()[..8]);
    let wrapper_script = log_dir.join(format!("{session_id}.sh"));

    // _dl_short = repo_id.split("/")[-1] if "/" in repo_id else repo_id
    let dl_short = if req.repo_id.contains('/') {
        req.repo_id.rsplit('/').next().unwrap_or(&req.repo_id).to_string()
    } else {
        req.repo_id.clone()
    };
    // _dl_base = (local_dir.rstrip("/") + "/" + _dl_short) if local_dir else None
    let dl_base: Option<String> = req
        .local_dir
        .as_deref()
        .filter(|d| !d.is_empty())
        .map(|d| format!("{}/{}", d.trim_end_matches('/'), dl_short));
    // _dl_shell = _shell_path(_dl_base) if _dl_base else None
    let dl_shell: Option<String> = dl_base.as_deref().map(_shell_path);
    // _dl_pyarg = (", local_dir=os.path.expanduser(" + repr(_dl_base) + ")") if _dl_base else ""
    let dl_pyarg: String = match &dl_base {
        Some(b) => format!(", local_dir=os.path.expanduser({})", py_repr(b)),
        None => String::new(),
    };

    // hf_cmd = f"hf download {repo_id}" [+ --include] [+ --local-dir]
    let mut hf_cmd = format!("hf download {}", req.repo_id);
    if let Some(inc) = req.include.as_deref().filter(|s| !s.is_empty()) {
        hf_cmd.push_str(&format!(" --include '{inc}'"));
    }
    if let Some(ds) = dl_shell.as_deref() {
        hf_cmd.push_str(&format!(" --local-dir {ds}"));
    }

    // Build the local-bash wrapper prelude `lines`.
    let mut lines: Vec<String> = vec!["#!/bin/bash".to_string()];
    lines.extend(user_shell_path_bootstrap());
    if let Some(tok) = nonempty_ref(&req.hf_token) {
        lines.push(format!("export HF_TOKEN='{}'", _bash_squote(tok)));
    }
    lines.push(r#"export PATH="$HOME/.local/bin:$PATH""#.to_string());
    lines.push("command -v hf >/dev/null 2>&1 || pip install --user --break-system-packages -q -U huggingface_hub 2>/dev/null || pip install -q -U huggingface_hub 2>/dev/null".to_string());
    if req.disable_hf_transfer {
        lines.push("export HF_HUB_ENABLE_HF_TRANSFER=0".to_string());
        lines.push("export HF_HUB_DOWNLOAD_MAX_WORKERS=4".to_string());
    } else {
        lines.push("python3 -c 'import hf_transfer' 2>/dev/null || pip install --user --break-system-packages -q hf_transfer 2>/dev/null || pip install -q hf_transfer 2>/dev/null".to_string());
        lines.push("python3 -c 'import hf_transfer' 2>/dev/null && export HF_HUB_ENABLE_HF_TRANSFER=1".to_string());
        lines.push("export HF_HUB_DOWNLOAD_MAX_WORKERS=8".to_string());
    }

    let remote = req.remote_host.as_deref().filter(|r| !r.is_empty());
    let is_windows = req.platform.as_deref() == Some("windows");
    crate::pylog::info(&format!(
        "Download request: repo={}, remote={}, ssh_port={}, platform={}",
        req.repo_id,
        remote.unwrap_or(""),
        req.ssh_port.as_deref().unwrap_or(""),
        req.platform.as_deref().unwrap_or(""),
    ));

    // `if not is_windows and not await _binary_available("tmux", remote, ssh_port):`
    if !is_windows && !binary_available("tmux", remote, req.ssh_port.as_deref(), false).await {
        return Ok(Json(json!({
            "ok": false,
            "error": missing_binary_message("tmux", remote.unwrap_or("local server")),
            "session_id": session_id,
        }))
        .into_response());
    }

    let setup_cmd: String;
    if let (Some(remote), true) = (remote, is_windows) {
        // ── Windows remote ──
        let remote_runner = format!(".{session_id}_run.ps1");
        let mut ps_lines: Vec<String> = Vec::new();
        ps_lines.push(r#"$sessionDir = "$env:TEMP\odysseus-sessions""#.to_string());
        ps_lines.push("New-Item -ItemType Directory -Force -Path $sessionDir | Out-Null".to_string());
        if let Some(tok) = nonempty_ref(&req.hf_token) {
            ps_lines.push(format!("$env:HF_TOKEN = '{}'", _ps_squote(tok)));
        }
        if let Some(ep) = req.env_prefix.as_deref().filter(|s| !s.is_empty()) {
            ps_lines.push(safe_env_prefix_str(ep)?);
        }
        // NOTE: the Python uses `.append('try {{')` etc. — the doubled braces are a
        // latent f-string bug (these lines have no interpolation, so `{{`/`}}`
        // render LITERALLY as `{{`/`}}`). Reproduce byte-for-byte.
        ps_lines.push("try {{".to_string());
        ps_lines.push("  $hfPath = Get-Command hf -ErrorAction SilentlyContinue".to_string());
        ps_lines.push("  if ($hfPath) {{".to_string());
        ps_lines.push(format!("    $null | {hf_cmd}"));
        ps_lines.push("  }} else {{".to_string());
        ps_lines.push("    python -c \"import huggingface_hub\" 2>$null".to_string());
        ps_lines.push("    if ($LASTEXITCODE -eq 0) {{".to_string());
        ps_lines.push("      Write-Host \"hf CLI not found, using Python huggingface_hub...\"".to_string());
        ps_lines.push("      python -m pip install -q hf_transfer 2>$null".to_string());
        ps_lines.push("      $env:HF_HUB_ENABLE_HF_TRANSFER = \"1\"".to_string());
        ps_lines.push(format!(
            "      python -c \"import os; from huggingface_hub import snapshot_download; snapshot_download('{}'{}, max_workers=8)\"",
            req.repo_id, dl_pyarg
        ));
        ps_lines.push("    }} else {{".to_string());
        ps_lines.push("      Write-Host \"Installing huggingface-hub...\"".to_string());
        ps_lines.push("      python -m pip install -q huggingface-hub hf_transfer".to_string());
        ps_lines.push("      $env:HF_HUB_ENABLE_HF_TRANSFER = \"1\"".to_string());
        ps_lines.push(format!(
            "      python -c \"import os; from huggingface_hub import snapshot_download; snapshot_download('{}'{}, max_workers=8)\"",
            req.repo_id, dl_pyarg
        ));
        ps_lines.push("    }}".to_string());
        ps_lines.push("  }}".to_string());
        ps_lines.push("  if ($LASTEXITCODE -eq 0) {{ Write-Host \"\"; Write-Host \"DOWNLOAD_OK\" }}".to_string());
        ps_lines.push("  else {{ Write-Host \"\"; Write-Host \"DOWNLOAD_FAILED (exit $LASTEXITCODE)\" }}".to_string());
        ps_lines.push("}} catch {{".to_string());
        ps_lines.push("  Write-Host \"\"; Write-Host \"DOWNLOAD_FAILED ($_)\"".to_string());
        ps_lines.push("}}".to_string());
        ps_lines.push(format!(
            "Remove-Item -Force \"$HOME\\{remote_runner}\" -ErrorAction SilentlyContinue"
        ));
        let runner_path = log_dir.join(format!("{session_id}_run.ps1"));
        // `runner_path.write_text("\r\n".join(ps_lines) + "\r\n")`
        let _ = std::fs::write(&runner_path, ps_lines.join("\r\n") + "\r\n");

        let port = req.ssh_port.as_deref();
        let cap_pf = port_flag_cap_p(port); // "-P {port} " or ""
        let low_pf = port_flag_low_p(port); // "-p {port} " or ""
        let launch_ps = format!(
            "$sd = \\\"$env:TEMP\\odysseus-sessions\\\"; \
Start-Process powershell -ArgumentList '-ExecutionPolicy','Bypass','-File','$HOME\\{remote_runner}' \
-RedirectStandardOutput \\\"$sd\\{session_id}.log\\\" \
-RedirectStandardError \\\"$sd\\{session_id}.err.log\\\" \
-NoNewWindow -PassThru | ForEach-Object {{ $_.Id | Out-File \\\"$sd\\{session_id}.pid\\\" }}"
        );
        setup_cmd = format!(
            "scp -O {cap_pf}-q '{runner}' {remote}:{remote_runner} && \
ssh {low_pf}{remote} \"powershell -Command \\\"{launch_ps}\\\"\"",
            runner = runner_path.to_string_lossy(),
        );
    } else if let Some(remote) = remote {
        // ── Linux/Termux remote ──
        let remote_runner = format!(".{session_id}_run.sh");
        let mut runner_lines: Vec<String> = vec!["#!/bin/bash".to_string()];
        runner_lines.extend(user_shell_path_bootstrap());
        runner_lines.push("# Auto-detect environment".to_string());
        runner_lines.push("deactivate 2>/dev/null; hash -r".to_string());
        if let Some(tok) = nonempty_ref(&req.hf_token) {
            runner_lines.push(format!("export HF_TOKEN='{}'", _bash_squote(tok)));
        }
        if let Some(ep) = req.env_prefix.as_deref().filter(|s| !s.is_empty()) {
            runner_lines.push(safe_env_prefix_str(ep)?);
        } else {
            runner_lines.push(
                r#"for p in ~/vllm-env ~/venv ~/.venv; do if [ -f "$p/bin/activate" ]; then source "$p/bin/activate"; break; fi; done"#
                    .to_string(),
            );
        }
        runner_lines.push(r#"export PATH="$HOME/.local/bin:$PATH""#.to_string());
        runner_lines.push("command -v hf >/dev/null 2>&1 || pip install --user --break-system-packages -q -U huggingface_hub 2>/dev/null || pip install -q -U huggingface_hub 2>/dev/null".to_string());
        runner_lines.push("python3 -c 'import hf_transfer' 2>/dev/null || pip install --user --break-system-packages -q hf_transfer 2>/dev/null || pip install -q hf_transfer 2>/dev/null".to_string());
        runner_lines.push("python3 -c 'import hf_transfer' 2>/dev/null && export HF_HUB_ENABLE_HF_TRANSFER=1".to_string());
        runner_lines.push("export HF_HUB_DOWNLOAD_MAX_WORKERS=8".to_string());
        runner_lines.push(HF_TOKEN_STATUS_SNIPPET.to_string());
        runner_lines.push("if command -v hf &>/dev/null; then".to_string());
        runner_lines.push(format!("  {hf_cmd} < /dev/null"));
        runner_lines.push("elif python3 -c \"import huggingface_hub\" 2>/dev/null; then".to_string());
        runner_lines.push("  echo \"hf CLI not found, using Python huggingface_hub...\"".to_string());
        runner_lines.push(format!(
            "  python3 -c \"import os; from huggingface_hub import snapshot_download; snapshot_download('{}'{}, max_workers=8)\"",
            req.repo_id, dl_pyarg
        ));
        runner_lines.push("else".to_string());
        runner_lines.push("  echo \"Installing huggingface-hub and dependencies...\"".to_string());
        runner_lines.push("  pip install --no-deps -q huggingface-hub 2>/dev/null".to_string());
        runner_lines.push("  pip install -q filelock fsspec packaging pyyaml tqdm typer httpx requests hf_transfer 2>/dev/null".to_string());
        runner_lines.push("  python3 -c 'import hf_transfer' 2>/dev/null && export HF_HUB_ENABLE_HF_TRANSFER=1".to_string());
        runner_lines.push(format!(
            "  python3 -c \"import os; from huggingface_hub import snapshot_download; snapshot_download('{}'{}, max_workers=8)\"",
            req.repo_id, dl_pyarg
        ));
        runner_lines.push("fi".to_string());
        runner_lines.push(r#"if [ $? -eq 0 ]; then echo ""; echo "DOWNLOAD_OK"; else echo ""; echo "DOWNLOAD_FAILED (exit $?)"; fi"#.to_string());
        runner_lines.push(format!("rm -f {remote_runner}"));
        runner_lines.push(r#"exec "${SHELL:-/bin/bash}""#.to_string());
        let runner_path = log_dir.join(format!("{session_id}_run.sh"));
        let _ = std::fs::write(&runner_path, runner_lines.join("\n") + "\n");
        let _ = crate::pyos::chmod(&runner_path.to_string_lossy(), 0o755);

        let port = req.ssh_port.as_deref();
        let scp_pf = port_flag_cap_p(port); // "-P {port} " or ""
        let ssh_pf = port_flag_low_p(port); // "-p {port} " or ""
        setup_cmd = format!(
            "scp -O {scp_pf}-q '{runner}' {remote}:{remote_runner} && \
ssh {ssh_pf}{remote} 'chmod +x {remote_runner} && tmux new-session -d -s {session_id} \"./{remote_runner}\"'",
            runner = runner_path.to_string_lossy(),
        );
    } else {
        // ── Local ──
        if let Some(ep) = req.env_prefix.as_deref().filter(|s| !s.is_empty()) {
            lines.push(safe_env_prefix_str(ep)?);
        } else {
            lines.push("deactivate 2>/dev/null; hash -r".to_string());
        }
        lines.push(HF_TOKEN_STATUS_SNIPPET.to_string());
        lines.push(format!("{hf_cmd} < /dev/null"));
        lines.push(r#"if [ $? -eq 0 ]; then echo ""; echo "DOWNLOAD_OK"; else echo ""; echo "DOWNLOAD_FAILED (exit $?)"; fi"#.to_string());
        lines.push(format!("rm -f '{}'", wrapper_script.to_string_lossy()));
        lines.push(r#"exec "${SHELL:-/bin/bash}""#.to_string());
        let _ = std::fs::write(&wrapper_script, lines.join("\n") + "\n");
        let _ = crate::pyos::chmod(&wrapper_script.to_string_lossy(), 0o755);
        setup_cmd = format!(
            "tmux new-session -d -s {session_id} {}",
            shquote(&wrapper_script.to_string_lossy())
        );
    }

    crate::pylog::info(&format!(
        "Model download: {} (include={}, session={}, remote={})",
        req.repo_id,
        req.include.as_deref().unwrap_or("None"),
        session_id,
        remote.unwrap_or(""),
    ));
    crate::pylog::info(&format!("Download setup_cmd: {setup_cmd}"));

    // `proc = await create_subprocess_shell(setup_cmd, ...); await proc.wait()`
    let output = run_shell(&setup_cmd).await;
    match output {
        Ok(out) if !out.status.success() => {
            let stderr = String::from_utf8_lossy(&out.stderr).into_owned();
            crate::pylog::error(&format!(
                "Download failed (rc={}): {stderr}",
                out.status.code().unwrap_or(-1)
            ));
            return Ok(Json(json!({"ok": false, "error": stderr, "session_id": session_id})).into_response());
        }
        Ok(_) => {}
        Err(e) => {
            // create_subprocess_shell launch failure -> unhandled -> 500.
            crate::pylog::error(&format!("Download setup launch failed: {e}"));
            return Err(HttpException::new(500, "Internal Server Error"));
        }
    }

    // Best-effort assistant log (try/except pass).
    if let Some(owner) = current_user_opt(user.as_deref()) {
        crate::src::assistant_log::log_to_assistant(
            &owner,
            &format!("Started downloading {} to {}", req.repo_id, remote.unwrap_or("local")),
            "assistant",
            Some("Download"),
        );
    }

    Ok(Json(json!({
        "ok": true,
        "session_id": session_id,
        "remote": remote.unwrap_or("local"),
    }))
    .into_response())
}

/// `GET /api/model/cached` — list cached models (HF cache + custom dirs).
async fn model_cached(
    State(state): State<AppState>,
    headers: HeaderMap,
    user: Option<Extension<CurrentUser>>,
    Query(q): Query<HashMap<String, String>>,
) -> Result<Response, HttpException> {
    admin_gate(&state, &headers, user.as_deref())?;
    // host: str | None, model_dir: str | None, ssh_port: str | None, platform: str | None
    let host_in = q.get("host").map(String::as_str);
    let model_dir = q.get("model_dir").map(String::as_str);
    let ssh_port = q.get("ssh_port").map(String::as_str);
    let platform = q.get("platform").map(String::as_str);

    // `host = _validate_remote_host(host)`
    let host = _validate_remote_host(host_in)?;
    // `if ssh_port is not None and ssh_port != "" and not _SSH_PORT_RE.fullmatch(ssh_port): raise`
    if let Some(p) = ssh_port {
        if !p.is_empty() && !_SSH_PORT_FULL_RE.is_match(p) {
            return Err(HttpException::new(400, "Invalid ssh_port"));
        }
    }
    let log_dir = tmux_log_dir();
    let _ = std::fs::create_dir_all(&log_dir);

    // `model_dirs = [d.strip() for d in model_dir.split(',') if d.strip()]` then
    // `paths_code = _cached_model_scan_script(model_dirs)`. The helper emits the
    // full HF-cache + ollama + custom-dir scan script (including the gguf/ollama
    // backend fields surfaced below) byte-for-byte.
    let mut model_dirs: Vec<String> = Vec::new();
    if let Some(md) = model_dir {
        for d in md.split(',') {
            let d = d.trim();
            if !d.is_empty() {
                model_dirs.push(d.to_string());
            }
        }
    }
    let paths_code = _cached_model_scan_script(&model_dirs);

    let scan_py = log_dir.join("scan_cache.py");
    let _ = std::fs::write(&scan_py, &paths_code);
    let scan_py_str = scan_py.to_string_lossy().into_owned();

    // Build the run command.
    let cmd: String = if let Some(host) = host.as_deref().filter(|h| !h.is_empty()) {
        let pf = port_flag_low_p(ssh_port);
        if platform == Some("windows") {
            format!("ssh {pf}{host} \"python -\" < '{scan_py_str}'")
        } else {
            format!("ssh {pf}{host} 'python3 -' < '{scan_py_str}'")
        }
    } else {
        format!("python3 '{scan_py_str}'")
    };

    // `proc = await create_subprocess_shell(cmd, ..., cwd=str(Path.home()))`
    // `stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=60)`
    let mut command = Command::new("sh");
    command.arg("-c").arg(&cmd);
    command.current_dir(home_dir());
    command.stdout(Stdio::piped()).stderr(Stdio::piped());
    let out = match run_with_timeout(command, 60).await {
        Ok(Some(o)) => o,
        // TimeoutError: Python lets `asyncio.TimeoutError` propagate (no except) ->
        // FastAPI surfaces it as a 500. A launch error likewise becomes a 500.
        _ => return Err(HttpException::new(500, "Internal Server Error")),
    };

    let stdout = String::from_utf8_lossy(&out.stdout).trim().to_string();
    let stderr = String::from_utf8_lossy(&out.stderr).into_owned();

    let mut models: Vec<Value> = Vec::new();
    // `try: raw = json.loads(...); for m in raw: ... except Exception as e:`
    match serde_json::from_str::<Value>(&stdout) {
        Ok(Value::Array(raw)) => {
            for m in raw {
                let size_bytes = m.get("size_bytes").and_then(Value::as_f64).unwrap_or(0.0);
                let size_gb = size_bytes / (1024f64.powi(3));
                let size_str = if size_gb >= 1.0 {
                    format!("{:.1} GB", size_gb)
                } else {
                    format!("{:.0} MB", size_bytes / (1024f64.powi(2)))
                };
                let mut entry = serde_json::Map::new();
                entry.insert("repo_id".to_string(), m.get("repo_id").cloned().unwrap_or(Value::Null));
                entry.insert("size".to_string(), Value::String(size_str));
                entry.insert("nb_files".to_string(), m.get("nb_files").cloned().unwrap_or(json!(0)));
                let has_incomplete = m.get("has_incomplete").and_then(Value::as_bool).unwrap_or(false);
                entry.insert("has_incomplete".to_string(), Value::Bool(has_incomplete));
                entry.insert(
                    "status".to_string(),
                    Value::String(if has_incomplete { "downloading" } else { "ready" }.to_string()),
                );
                entry.insert(
                    "path".to_string(),
                    m.get("path").cloned().unwrap_or(Value::String(String::new())),
                );
                entry.insert(
                    "is_diffusion".to_string(),
                    Value::Bool(m.get("is_diffusion").and_then(Value::as_bool).unwrap_or(false)),
                );
                // `if m.get("is_local_dir"): entry["is_local_dir"] = True`
                if m.get("is_local_dir").map(value_truthy).unwrap_or(false) {
                    entry.insert("is_local_dir".to_string(), Value::Bool(true));
                }
                // `if m.get("is_gguf"): entry["is_gguf"] = True`
                if m.get("is_gguf").map(value_truthy).unwrap_or(false) {
                    entry.insert("is_gguf".to_string(), Value::Bool(true));
                }
                // `if m.get("backend"): entry["backend"] = m.get("backend")`
                if let Some(backend) = m.get("backend").filter(|v| value_truthy(v)) {
                    entry.insert("backend".to_string(), backend.clone());
                }
                // `if m.get("is_ollama"): entry["is_ollama"] = True`
                if m.get("is_ollama").map(value_truthy).unwrap_or(false) {
                    entry.insert("is_ollama".to_string(), Value::Bool(true));
                }
                // `if isinstance(m.get("gguf_files"), list): entry["gguf_files"] = m["gguf_files"]`
                if let Some(gguf_files) = m.get("gguf_files").filter(|v| v.is_array()) {
                    entry.insert("gguf_files".to_string(), gguf_files.clone());
                }
                models.push(Value::Object(entry));
            }
        }
        _ => {
            crate::pylog::warning("Failed to parse cached models");
            crate::pylog::warning(&format!("stderr: {}", tail_chars_front(&stderr, 500)));
        }
    }

    Ok(Json(json!({
        "models": models,
        "host": host.as_deref().filter(|h| !h.is_empty()).unwrap_or("local"),
    }))
    .into_response())
}

/// `POST /api/model/serve` — launch a model server in tmux (or a PowerShell bg proc).
async fn model_serve(
    State(state): State<AppState>,
    headers: HeaderMap,
    user: Option<Extension<CurrentUser>>,
    Json(mut req): Json<ServeRequest>,
) -> Result<Response, HttpException> {
    admin_gate(&state, &headers, user.as_deref())?;
    _validate_remote_host(req.remote_host.as_deref())?;
    req.ssh_port = _validate_ssh_port(req.ssh_port.as_deref())?;
    req.gpus = _validate_gpus(req.gpus.as_deref())?;
    let hf_token = nonempty(req.hf_token.clone()).unwrap_or_else(load_stored_hf_token);
    req.hf_token = if hf_token.is_empty() { None } else { Some(hf_token.clone()) };
    _validate_token(nonempty_ref(&req.hf_token))?;
    // `req.cmd = _validate_serve_cmd(req.cmd) or ""`
    req.cmd = _validate_serve_cmd(Some(&req.cmd))?.unwrap_or_default();
    let is_pip_install = !req.cmd.is_empty() && req.cmd.contains("pip install");
    if is_pip_install {
        // `if not req.repo_id or not re.fullmatch(PIP_RE, req.repo_id): raise`
        if req.repo_id.is_empty() || !_PIP_NAME_RE.is_match(&req.repo_id) {
            return Err(HttpException::new(400, "Invalid pip package name"));
        }
    } else {
        _validate_serve_model_id(Some(&req.repo_id))?;
    }
    let log_dir = tmux_log_dir();
    let _ = std::fs::create_dir_all(&log_dir);
    // `session_id = f"serve-{uuid.uuid4().hex[:8]}"`
    let session_id = format!("serve-{}", &uuid::Uuid::new_v4().simple().to_string()[..8]);
    let remote = req.remote_host.as_deref().filter(|r| !r.is_empty());
    let is_windows = req.platform.as_deref() == Some("windows");

    if !is_windows && !binary_available("tmux", remote, req.ssh_port.as_deref(), false).await {
        return Ok(Json(json!({
            "ok": false,
            "error": missing_binary_message("tmux", remote.unwrap_or("local server")),
            "session_id": session_id,
        }))
        .into_response());
    }
    if needs_binary_docker(&req.cmd)
        && !binary_available("docker", remote, req.ssh_port.as_deref(), is_windows).await
    {
        return Ok(Json(json!({
            "ok": false,
            "error": missing_binary_message("docker", remote.unwrap_or("local server")),
            "session_id": session_id,
        }))
        .into_response());
    }

    let setup_cmd: String;
    if let (true, Some(remote)) = (is_windows, remote) {
        // ── Windows remote serve runner ──
        let remote_runner = format!(".{session_id}_run.ps1");
        let mut ps_lines: Vec<String> = Vec::new();
        ps_lines.push(r#"$sessionDir = "$env:TEMP\odysseus-sessions""#.to_string());
        ps_lines.push("New-Item -ItemType Directory -Force -Path $sessionDir | Out-Null".to_string());
        if let Some(tok) = nonempty_ref(&req.hf_token) {
            ps_lines.push(format!("$env:HF_TOKEN = '{}'", _ps_squote(tok)));
        }
        if let Some(g) = req.gpus.as_deref().filter(|g| !g.is_empty()) {
            ps_lines.push(format!("$env:CUDA_VISIBLE_DEVICES = '{g}'"));
        }
        if let Some(ep) = req.env_prefix.as_deref().filter(|s| !s.is_empty()) {
            ps_lines.push(safe_env_prefix_str(ep)?);
        }
        if req.cmd.contains("ollama") {
            ps_lines.push("# Check if ollama is available".to_string());
            ps_lines.push("if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {".to_string());
            ps_lines.push("  Write-Host \"Ollama not found. Please install from https://ollama.com/download/windows\"".to_string());
            ps_lines.push("  exit 1".to_string());
            ps_lines.push("}".to_string());
        } else if req.cmd.contains("llama_cpp") || req.cmd.contains("llama-server") {
            ps_lines.push("# Auto-install llama-cpp-python if missing".to_string());
            ps_lines.push("try { python -c \"import llama_cpp\" 2>$null } catch {}".to_string());
            ps_lines.push("if ($LASTEXITCODE -ne 0) {".to_string());
            ps_lines.push("  Write-Host \"Installing llama-cpp-python...\"".to_string());
            ps_lines.push("  python -m pip install llama-cpp-python[server]".to_string());
            ps_lines.push("}".to_string());
        } else if req.cmd.contains("vllm") {
            ps_lines.push("Write-Host \"ERROR: vLLM is not supported on Windows. Use Ollama or llama.cpp instead.\"".to_string());
            ps_lines.push("exit 1".to_string());
        }
        ps_lines.push(req.cmd.clone());
        ps_lines.push("Write-Host \"\"".to_string());
        ps_lines.push("Write-Host \"=== Process exited with code $LASTEXITCODE ===\"".to_string());
        let runner_path = log_dir.join(format!("{session_id}_run.ps1"));
        let _ = std::fs::write(&runner_path, ps_lines.join("\r\n") + "\r\n");

        let port = req.ssh_port.as_deref();
        let cap_pf = port_flag_cap_p(port);
        let low_pf = port_flag_low_p(port);
        let launch_ps = format!(
            "$sd = \\\"$env:TEMP\\odysseus-sessions\\\"; \
Start-Process powershell -ArgumentList '-ExecutionPolicy','Bypass','-File','$HOME\\{remote_runner}' \
-RedirectStandardOutput \\\"$sd\\{session_id}.log\\\" \
-RedirectStandardError \\\"$sd\\{session_id}.err.log\\\" \
-NoNewWindow -PassThru | ForEach-Object {{ $_.Id | Out-File \\\"$sd\\{session_id}.pid\\\" }}"
        );
        setup_cmd = format!(
            "scp -O {cap_pf}-q '{runner}' {remote}:{remote_runner} && \
ssh {low_pf}{remote} \"powershell -Command \\\"{launch_ps}\\\"\"",
            runner = runner_path.to_string_lossy(),
        );
    } else {
        // ── Linux/Termux: bash + tmux ──
        let mut runner_lines: Vec<String> = vec!["#!/bin/bash".to_string()];
        runner_lines.extend(user_shell_path_bootstrap());
        runner_lines.push("export FLASHINFER_DISABLE_VERSION_CHECK=1".to_string());
        if let Some(tok) = nonempty_ref(&req.hf_token) {
            runner_lines.push(format!("export HF_TOKEN='{}'", _bash_squote(tok)));
        }
        if let Some(g) = req.gpus.as_deref().filter(|g| !g.is_empty()) {
            runner_lines.push(format!("export CUDA_VISIBLE_DEVICES='{g}'"));
        }
        if let Some(ep) = req.env_prefix.as_deref().filter(|s| !s.is_empty()) {
            runner_lines.push(safe_env_prefix_str(ep)?);
        } else {
            runner_lines.push("deactivate 2>/dev/null; hash -r".to_string());
        }
        runner_lines.push(HF_TOKEN_STATUS_SNIPPET.to_string());

        if req.cmd.contains("llama_cpp") || req.cmd.contains("llama-server") {
            runner_lines.push("# Ensure a llama.cpp server (prefer native llama-server)".to_string());
            runner_lines.push(r#"export PATH="$HOME/.local/bin:$HOME/bin:$HOME/llama.cpp/build/bin:$PATH""#.to_string());
            runner_lines.push("if [ -d /data/data/com.termux ]; then".to_string());
            runner_lines.push("  # Termux: no native build — use the Python bindings (CPU).".to_string());
            runner_lines.push("  if ! python3 -c \"import llama_cpp\" 2>/dev/null; then".to_string());
            runner_lines.push("    pkg install -y cmake 2>/dev/null".to_string());
            runner_lines.push("    pip install numpy diskcache jinja2 2>/dev/null".to_string());
            runner_lines.push("    CMAKE_ARGS=\"-DGGML_BLAS=OFF -DGGML_LLAMAFILE=OFF\" pip install llama-cpp-python --no-build-isolation --no-cache-dir 2>&1 || true".to_string());
            runner_lines.push("  fi".to_string());
            runner_lines.push("elif ! command -v llama-server &>/dev/null; then".to_string());
            runner_lines.push("  echo \"Native llama-server not found — building from source (one-time, may take a few minutes)...\"".to_string());
            runner_lines.push("  mkdir -p ~/bin".to_string());
            runner_lines.push("  cd ~ && [ -d llama.cpp ] || git clone --depth 1 https://github.com/ggml-org/llama.cpp".to_string());
            runner_lines.push("  cd ~/llama.cpp && { cmake -B build -DGGML_CUDA=ON 2>/dev/null || cmake -B build; } \\".to_string());
            runner_lines.push("    && cmake --build build -j\"$(nproc)\" --target llama-server \\".to_string());
            runner_lines.push("    && ln -sf ~/llama.cpp/build/bin/llama-server ~/bin/llama-server".to_string());
            runner_lines.push("  # If the native build failed, fall back to the Python bindings.".to_string());
            runner_lines.push("  if ! command -v llama-server &>/dev/null && ! python3 -c \"import llama_cpp\" 2>/dev/null; then".to_string());
            runner_lines.push("    echo \"llama-server build failed — installing Python bindings as fallback...\"".to_string());
            runner_lines.push("    pip install --user --break-system-packages -q llama-cpp-python 2>/dev/null || pip install -q llama-cpp-python 2>/dev/null || true".to_string());
            runner_lines.push("  fi".to_string());
            runner_lines.push("fi".to_string());
        } else if req.cmd.contains("vllm serve") {
            runner_lines.push(r#"export PATH="$HOME/.local/bin:$PATH""#.to_string());
            runner_lines.push("if ! command -v vllm &>/dev/null; then".to_string());
            runner_lines.push("  echo \"ERROR: vLLM is not installed. Open Cookbook -> Dependencies and install vllm on this server, then launch again.\"".to_string());
            runner_lines.push("  exit 127".to_string());
            runner_lines.push("fi".to_string());
        } else if req.cmd.contains("sglang.launch_server") {
            runner_lines.push(r#"export PATH="$HOME/.local/bin:$PATH""#.to_string());
            runner_lines.push("if ! python3 -c \"import sglang\" 2>/dev/null; then".to_string());
            runner_lines.push("  echo \"ERROR: SGLang is not installed. Open Cookbook -> Dependencies and install sglang on this server, then launch again.\"".to_string());
            runner_lines.push("  exit 127".to_string());
            runner_lines.push("fi".to_string());
        }

        runner_lines.push(req.cmd.clone());
        runner_lines.push(r#"echo ""; echo "=== Process exited with code $? ==="; exec "${SHELL:-/bin/bash}""#.to_string());

        let runner_path = log_dir.join(format!("{session_id}_run.sh"));
        let _ = std::fs::write(&runner_path, runner_lines.join("\n") + "\n");
        let _ = crate::pyos::chmod(&runner_path.to_string_lossy(), 0o755);

        if let Some(remote) = remote {
            let remote_runner = format!(".{session_id}_run.sh");
            let port = req.ssh_port.as_deref();
            let cap_pf = port_flag_cap_p(port);
            let low_pf = port_flag_low_p(port);
            let mut scp_extras = String::new();
            // `if "scripts/diffusion_server.py" in req.cmd:`
            if req.cmd.contains("scripts/diffusion_server.py") {
                let diff_script = FsPath::new(&*crate::core::constants::BASE_DIR)
                    .join("scripts")
                    .join("diffusion_server.py");
                if diff_script.exists() {
                    scp_extras = format!(
                        "scp -O {cap_pf}-q '{}' {remote}:.diffusion_server.py && ",
                        diff_script.to_string_lossy()
                    );
                    // Rewrite the runner to reference the scp'd basename.
                    if let Ok(text) = std::fs::read_to_string(&runner_path) {
                        let _ = std::fs::write(
                            &runner_path,
                            text.replace("scripts/diffusion_server.py", ".diffusion_server.py"),
                        );
                    }
                }
            }
            setup_cmd = format!(
                "{scp_extras}scp -O {cap_pf}-q '{runner}' {remote}:{remote_runner} && \
ssh {low_pf}{remote} 'chmod +x {remote_runner} && tmux new-session -d -s {session_id} \"./{remote_runner}\"'",
                runner = runner_path.to_string_lossy(),
            );
        } else {
            setup_cmd = format!(
                "tmux new-session -d -s {session_id} {}",
                shquote(&runner_path.to_string_lossy())
            );
        }
    }

    // `proc = await create_subprocess_shell(setup_cmd, ...); await proc.wait()`
    let output = run_shell(&setup_cmd).await;
    match output {
        Ok(out) if !out.status.success() => {
            let stderr = String::from_utf8_lossy(&out.stderr).into_owned();
            return Ok(Json(json!({"ok": false, "error": stderr, "session_id": session_id})).into_response());
        }
        Ok(_) => {}
        Err(e) => {
            crate::pylog::error(&format!("Serve setup launch failed: {e}"));
            return Err(HttpException::new(500, "Internal Server Error"));
        }
    }

    // Auto-register a model endpoint so the served model shows up in the model
    // picker with no manual /setup step. Diffusion models get an image endpoint;
    // any other real model serve (i.e. not a pip-install task) gets a local LLM
    // endpoint pointed at its /v1.
    let mut endpoint_id: Option<String> = None;
    let is_diffusion = req.cmd.contains("diffusion_server.py");
    if is_diffusion {
        endpoint_id = auto_register_image_endpoint(&req, remote);
    } else if !is_pip_install {
        endpoint_id = auto_register_llm_endpoint(&req, remote);
    }

    // Best-effort assistant log.
    if let Some(owner) = current_user_opt(user.as_deref()) {
        let short = if req.repo_id.contains('/') {
            req.repo_id.rsplit('/').next().unwrap_or(&req.repo_id).to_string()
        } else {
            req.repo_id.clone()
        };
        crate::src::assistant_log::log_to_assistant(
            &owner,
            &format!("Started serving {short} on {}", remote.unwrap_or("local")),
            "assistant",
            Some("Serve"),
        );
    }

    Ok(Json(json!({
        "ok": true,
        "session_id": session_id,
        "remote": remote.unwrap_or("local"),
        "endpoint_id": endpoint_id,
    }))
    .into_response())
}

/// `class SetupRequest(BaseModel)` — `host` (required) + optional `ssh_port`.
#[derive(Debug, Deserialize)]
struct SetupRequest {
    host: String,
    #[serde(default)]
    ssh_port: Option<String>,
}

/// `POST /api/cookbook/setup` — install deps on a remote server over SSH.
async fn server_setup(
    State(state): State<AppState>,
    headers: HeaderMap,
    user: Option<Extension<CurrentUser>>,
    Json(req): Json<SetupRequest>,
) -> Result<Response, HttpException> {
    admin_gate(&state, &headers, user.as_deref())?;
    // `host = _validate_remote_host(req.host); if not host: raise HTTPException(400, "host is required")`
    let host = _validate_remote_host(Some(&req.host))?;
    let host = match host.filter(|h| !h.is_empty()) {
        Some(h) => h,
        None => return Err(HttpException::new(400, "host is required")),
    };
    let port = req.ssh_port.as_deref();
    // `if port is not None and port != "" and not re.fullmatch(r"\d{1,5}", port): raise`
    if let Some(p) = port {
        if !p.is_empty() && !_PORT_5_FULL_RE.is_match(p) {
            return Err(HttpException::new(400, "Invalid ssh_port"));
        }
    }
    let pf = port_flag_low_p(port);

    // Detect platform: Windows first (echo %OS% -> Windows_NT), then Termux, then Linux.
    // `platform = "linux"` is the default the try/except falls back to; reproduced
    // as the value the detection block yields (assigned once, no dead initializer).
    let detect_cmd = format!("ssh {pf}{host} \"echo %OS%\"");
    // `try: ... await asyncio.wait_for(..., timeout=10) ... except Exception: platform = "linux"`
    let platform: String = 'detect: {
        let mut c1 = Command::new("sh");
        c1.arg("-c").arg(&detect_cmd);
        c1.stdout(Stdio::piped()).stderr(Stdio::piped());
        let out = match run_with_timeout(c1, 10).await {
            Ok(Some(o)) => o,
            _ => break 'detect "linux".to_string(),
        };
        let detected = String::from_utf8_lossy(&out.stdout).trim().to_string();
        if detected.contains("Windows_NT") {
            break 'detect "windows".to_string();
        }
        // Check for Termux.
        let detect_cmd2 =
            format!("ssh {pf}{host} 'test -d /data/data/com.termux && echo termux || echo linux'");
        let mut c2 = Command::new("sh");
        c2.arg("-c").arg(&detect_cmd2);
        c2.stdout(Stdio::piped()).stderr(Stdio::piped());
        match run_with_timeout(c2, 10).await {
            Ok(Some(o2)) => String::from_utf8_lossy(&o2.stdout).trim().to_string(),
            _ => "linux".to_string(),
        }
    };

    let cmd: String = if platform == "windows" {
        let setup_script = concat!(
            "powershell -Command \"",
            "New-Item -ItemType Directory -Force -Path $env:TEMP\\odysseus-sessions | Out-Null; ",
            "try { python --version } catch { Write-Host 'ERROR: Python not found — install from python.org'; exit 1 }; ",
            "python -m pip install -q huggingface-hub 2>$null; ",
            "python -c \\\"from huggingface_hub import snapshot_download; print('OK')\\\"",
            "\""
        );
        format!("ssh {pf}{host} {setup_script}")
    } else if platform == "termux" {
        let setup_script = concat!(
            "pkg install -y python tmux 2>/dev/null; ",
            "pip install --no-deps -q huggingface-hub 2>/dev/null; ",
            "pip install -q filelock fsspec packaging pyyaml tqdm typer httpx requests 2>/dev/null; ",
            "python3 -c 'from huggingface_hub import snapshot_download; print(\"OK\")'"
        );
        format!("ssh {pf}{host} '{setup_script}'")
    } else {
        let setup_script = concat!(
            "if ! command -v tmux >/dev/null 2>&1; then ",
            "  if command -v apt-get >/dev/null 2>&1; then sudo -n apt-get install -y tmux 2>/dev/null; ",
            "  elif command -v pacman >/dev/null 2>&1; then sudo -n pacman -S --noconfirm tmux 2>/dev/null; ",
            "  elif command -v dnf >/dev/null 2>&1; then sudo -n dnf install -y tmux 2>/dev/null; ",
            "  elif command -v apk >/dev/null 2>&1; then sudo -n apk add --no-interactive tmux 2>/dev/null; ",
            "  elif command -v zypper >/dev/null 2>&1; then sudo -n zypper --non-interactive install tmux 2>/dev/null; ",
            "  fi; ",
            "fi; ",
            "command -v tmux >/dev/null 2>&1 || echo 'WARNING: tmux missing and auto-install failed (need passwordless sudo). Install manually.'; ",
            "pip install -q huggingface_hub hf_transfer 2>/dev/null || ",
            "pip install --user --break-system-packages -q huggingface_hub hf_transfer 2>/dev/null || ",
            "pip3 install --user --break-system-packages -q huggingface_hub hf_transfer 2>/dev/null; ",
            "python3 -c 'from huggingface_hub import snapshot_download; print(\"OK\")'"
        );
        format!("ssh {pf}{host} '{setup_script}'")
    };

    // `try: ... await asyncio.wait_for(..., timeout=120) ... except TimeoutError / Exception`
    let mut c = Command::new("sh");
    c.arg("-c").arg(&cmd);
    c.stdout(Stdio::piped()).stderr(Stdio::piped());
    match run_with_timeout(c, 120).await {
        Ok(Some(out)) => {
            // `output = stdout.decode() + stderr.decode()`
            let output = format!(
                "{}{}",
                String::from_utf8_lossy(&out.stdout),
                String::from_utf8_lossy(&out.stderr)
            );
            let ok = output.contains("OK");
            Ok(Json(json!({"ok": ok, "output": output.trim(), "platform": platform})).into_response())
        }
        Ok(None) => {
            // asyncio.TimeoutError branch.
            Ok(Json(json!({"ok": false, "error": "Setup timed out (120s)", "platform": platform}))
                .into_response())
        }
        Err(e) => {
            // `except Exception as e: return {"ok": False, "error": str(e), "platform": platform}`
            Ok(Json(json!({"ok": false, "error": e.to_string(), "platform": platform})).into_response())
        }
    }
}

/// `GET /api/cookbook/gpus` — probe GPU memory/process state.
async fn list_gpus(
    State(state): State<AppState>,
    headers: HeaderMap,
    user: Option<Extension<CurrentUser>>,
    Query(q): Query<HashMap<String, String>>,
) -> Result<Response, HttpException> {
    admin_gate(&state, &headers, user.as_deref())?;
    let host_in = q.get("host").map(String::as_str);
    let ssh_port = q.get("ssh_port").map(String::as_str);
    let host = _validate_remote_host(host_in)?;
    if let Some(p) = ssh_port {
        if !p.is_empty() && !_SSH_PORT_FULL_RE.is_match(p) {
            return Err(HttpException::new(400, "Invalid ssh_port"));
        }
    }
    let host = host.filter(|h| !h.is_empty());
    let host_ref = host.as_deref();

    let gpu_query = "nvidia-smi --query-gpu=index,name,memory.free,memory.total,memory.used,utilization.gpu,uuid --format=csv,noheader,nounits";
    // `try: ... except FileNotFoundError: ... except Exception as e:`
    let (gpu_out, nvidia_error) = match run_nvidia_smi(gpu_query, host_ref, ssh_port, 8).await {
        (Some(out), err) => {
            if let Some(e) = err {
                (String::new(), Some(e))
            } else {
                (out, None)
            }
        }
        (None, err) => (String::new(), err.or(Some("nvidia-smi failed".to_string()))),
    };

    let mut gpus: Vec<Value> = Vec::new();
    let mut uuid_to_idx: HashMap<String, i64> = HashMap::new();
    for line in gpu_out.trim().lines() {
        let parts: Vec<String> = line.split(',').map(|p| p.trim().to_string()).collect();
        if parts.len() < 7 {
            continue;
        }
        // `try: idx=int(parts[0]); ... except (ValueError, IndexError): continue`
        let parsed = (|| -> Option<(i64, String, i64, i64, i64, i64, String)> {
            let idx = parse_int_via_float(&parts[0])?;
            let name = parts[1].clone();
            let free_mb = parse_int_via_float(&parts[2])?;
            let total_mb = parse_int_via_float(&parts[3])?;
            let used_mb = parse_int_via_float(&parts[4])?;
            let util_pct = parse_int_via_float(&parts[5])?;
            let gpu_uuid = parts[6].clone();
            Some((idx, name, free_mb, total_mb, used_mb, util_pct, gpu_uuid))
        })();
        let (idx, name, free_mb, total_mb, used_mb, util_pct, gpu_uuid) = match parsed {
            Some(t) => t,
            None => continue,
        };
        let busy = total_mb > 0 && (free_mb as f64 / total_mb as f64) < 0.5;
        uuid_to_idx.insert(gpu_uuid.clone(), idx);
        gpus.push(json!({
            "index": idx, "name": name, "uuid": gpu_uuid,
            "free_mb": free_mb, "total_mb": total_mb,
            "used_mb": used_mb, "util_pct": util_pct,
            "busy": busy, "processes": [],
        }));
    }

    // Best-effort process listing.
    let proc_query = "nvidia-smi --query-compute-apps=pid,gpu_uuid,process_name,used_memory --format=csv,noheader,nounits";
    // `try: ... except Exception: pass`
    let (proc_out, proc_err) = run_nvidia_smi(proc_query, host_ref, ssh_port, 5).await;
    if proc_err.is_none() {
        if let Some(proc_out) = proc_out.filter(|s| !s.is_empty()) {
            // `gpus_by_idx = {g["index"]: g for g in gpus}` — index into the vec.
            let mut idx_pos: HashMap<i64, usize> = HashMap::new();
            for (pos, g) in gpus.iter().enumerate() {
                if let Some(i) = g.get("index").and_then(Value::as_i64) {
                    idx_pos.insert(i, pos);
                }
            }
            for line in proc_out.trim().lines() {
                let parts: Vec<String> = line.split(',').map(|p| p.trim().to_string()).collect();
                if parts.len() < 4 {
                    continue;
                }
                let parsed = (|| -> Option<(i64, String, i64)> {
                    let pid = parse_int_via_float(&parts[0])?;
                    let pname = parts[2].clone();
                    let pmem = parse_int_via_float(&parts[3])?;
                    Some((pid, pname, pmem))
                })();
                let (pid, pname, pmem) = match parsed {
                    Some(t) => t,
                    None => continue,
                };
                // `idx = uuid_to_idx.get(parts[1]); if idx is None or idx not in gpus_by_idx: continue`
                let idx = match uuid_to_idx.get(&parts[1]) {
                    Some(i) => *i,
                    None => continue,
                };
                if let Some(&pos) = idx_pos.get(&idx) {
                    if let Some(procs) = gpus[pos].get_mut("processes").and_then(Value::as_array_mut) {
                        procs.push(json!({"pid": pid, "name": pname, "used_mb": pmem}));
                    }
                }
            }
        }
    }

    if !gpus.is_empty() {
        return Ok(Json(json!({"ok": true, "gpus": gpus, "backend": "cuda", "source": "nvidia-smi"})).into_response());
    }

    let amd_gpus = probe_amd_sysfs(host_ref, ssh_port).await;
    if !amd_gpus.is_empty() {
        return Ok(Json(json!({
            "ok": true, "gpus": amd_gpus, "backend": "rocm", "source": "amd-sysfs",
            "fallback_from": "nvidia-smi", "nvidia_error": nvidia_error,
        }))
        .into_response());
    }

    let processes = probe_gpu_device_processes(host_ref, ssh_port).await;
    if !processes.is_empty() {
        return Ok(Json(json!({
            "ok": true,
            "gpus": [{
                "index": 0, "name": "GPU device holders", "uuid": "dev-dri",
                "free_mb": 0, "total_mb": 0, "used_mb": 0, "util_pct": 0,
                "busy": true, "processes": processes,
                "backend": "generic", "source": "gpu-devices",
            }],
            "backend": "generic", "source": "gpu-devices",
            "fallback_from": "nvidia-smi", "nvidia_error": nvidia_error,
        }))
        .into_response());
    }

    // `nvidia_error or "No GPU memory probe available"`
    let err = nvidia_error
        .filter(|e| !e.is_empty())
        .unwrap_or_else(|| "No GPU memory probe available".to_string());
    Ok(Json(json!({"ok": false, "error": err, "gpus": []})).into_response())
}

/// `class KillPidRequest(BaseModel)`.
#[derive(Debug, Deserialize)]
struct KillPidRequest {
    pid: i64,
    #[serde(default)]
    host: Option<String>,
    #[serde(default)]
    ssh_port: Option<String>,
    #[serde(default = "default_kill_signal")]
    signal: String,
}

fn default_kill_signal() -> String {
    "TERM".to_string()
}

/// `POST /api/cookbook/kill-pid` — signal a GPU-holding PID.
async fn kill_pid(
    State(state): State<AppState>,
    headers: HeaderMap,
    user: Option<Extension<CurrentUser>>,
    Json(req): Json<KillPidRequest>,
) -> Result<Response, HttpException> {
    admin_gate(&state, &headers, user.as_deref())?;
    // `if req.pid < 100: raise HTTPException(400, ...)`
    if req.pid < 100 {
        return Err(HttpException::new(
            400,
            format!("Refusing to signal PID {} (<100, likely system process)", req.pid),
        ));
    }
    // `sig = (req.signal or "TERM").upper()`
    let sig = if req.signal.is_empty() {
        "TERM".to_string()
    } else {
        req.signal.to_uppercase()
    };
    if sig != "TERM" && sig != "KILL" && sig != "INT" {
        return Err(HttpException::new(400, "signal must be TERM, KILL, or INT"));
    }
    let host = _validate_remote_host(req.host.as_deref())?;
    // `if req.ssh_port and not _SSH_PORT_RE.fullmatch(req.ssh_port): raise`
    if let Some(p) = req.ssh_port.as_deref().filter(|p| !p.is_empty()) {
        if !_SSH_PORT_FULL_RE.is_match(p) {
            return Err(HttpException::new(400, "Invalid ssh_port"));
        }
    }
    let kill_cmd = format!("kill -{sig} {}", req.pid);

    // `try: ... except asyncio.TimeoutError / Exception`
    let result = async {
        if let Some(host) = host.as_deref().filter(|h| !h.is_empty()) {
            let pf = port_flag_low_p(req.ssh_port.as_deref());
            let cmd = format!(
                "ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no {pf}{host} '{kill_cmd}'"
            );
            let mut c = Command::new("sh");
            c.arg("-c").arg(&cmd);
            c.stdout(Stdio::piped()).stderr(Stdio::piped());
            run_with_timeout(c, 5).await
        } else {
            let mut c = Command::new("kill");
            c.arg(format!("-{sig}")).arg(req.pid.to_string());
            c.stdout(Stdio::piped()).stderr(Stdio::piped());
            run_with_timeout(c, 5).await
        }
    }
    .await;

    match result {
        Ok(Some(out)) => {
            if !out.status.success() {
                let err = tail_chars_front(String::from_utf8_lossy(&out.stderr).trim(), 200);
                let err = if err.is_empty() {
                    format!("kill returned {}", out.status.code().unwrap_or(-1))
                } else {
                    err
                };
                Ok(Json(json!({"ok": false, "error": err})).into_response())
            } else {
                Ok(Json(json!({"ok": true, "pid": req.pid, "signal": sig})).into_response())
            }
        }
        Ok(None) => Ok(Json(json!({"ok": false, "error": "kill command timed out"})).into_response()),
        Err(e) => {
            let msg = tail_chars_front(&e.to_string(), 200);
            Ok(Json(json!({"ok": false, "error": msg})).into_response())
        }
    }
}

/// `GET /api/cookbook/state` — load saved cookbook state (secrets stripped).
async fn get_cookbook_state(
    State(state): State<AppState>,
    headers: HeaderMap,
    user: Option<Extension<CurrentUser>>,
) -> Result<Response, HttpException> {
    admin_gate(&state, &headers, user.as_deref())?;
    let path = cookbook_state_path();
    if path.exists() {
        // `try: return _state_for_client(json.loads(...)) except Exception: return {}`
        match std::fs::read_to_string(&path).ok().and_then(|t| serde_json::from_str::<Value>(&t).ok()) {
            Some(v) => return Ok(Json(state_for_client(v)).into_response()),
            None => return Ok(Json(json!({})).into_response()),
        }
    }
    Ok(Json(json!({})).into_response())
}

/// `POST /api/cookbook/state` — persist cookbook state (encrypt secrets + race guards).
async fn save_cookbook_state(
    State(state): State<AppState>,
    headers: HeaderMap,
    user: Option<Extension<CurrentUser>>,
    raw_body: axum::body::Bytes,
) -> Result<Response, HttpException> {
    admin_gate(&state, &headers, user.as_deref())?;
    const RACE_WINDOW_MS: i64 = 60_000;
    // `try: ... except Exception as e: return {"ok": False, "error": str(e)}`
    let result = (|| -> Result<i64, String> {
        // `data = await request.json(); if not isinstance(data, dict): data = {}`
        let mut data: Value = match serde_json::from_slice::<Value>(&raw_body) {
            Ok(v @ Value::Object(_)) => v,
            Ok(_) => Value::Object(serde_json::Map::new()),
            Err(e) => return Err(e.to_string()),
        };
        // `try: on_disk = json.loads(...) if exists else {} except Exception: on_disk = {}`
        let path = cookbook_state_path();
        let on_disk: Value = if path.exists() {
            std::fs::read_to_string(&path)
                .ok()
                .and_then(|t| serde_json::from_str::<Value>(&t).ok())
                .unwrap_or_else(|| Value::Object(serde_json::Map::new()))
        } else {
            Value::Object(serde_json::Map::new())
        };

        // Anti-wipe guard for env.servers.
        let disk_env = on_disk
            .get("env")
            .filter(|v| v.is_object())
            .cloned();
        if let Some(disk_env) = disk_env.filter(|e| e.as_object().map(|o| !o.is_empty()).unwrap_or(false)) {
            // `inc_env = data.get("env") if isinstance(data.get("env"), dict) else None`
            let has_inc_env = data.get("env").map(Value::is_object).unwrap_or(false);
            if !has_inc_env {
                if let Value::Object(d) = &mut data {
                    d.insert("env".to_string(), disk_env.clone());
                }
                crate::pylog::warning("cookbook state POST: incoming body had no env; preserved on-disk env (anti-wipe guard)");
            } else {
                // `elif disk_env.get("servers") and not inc_env.get("servers"):`
                let disk_servers = disk_env.get("servers").map(value_truthy).unwrap_or(false);
                let inc_servers = data
                    .get("env")
                    .and_then(|e| e.get("servers"))
                    .map(value_truthy)
                    .unwrap_or(false);
                if disk_servers && !inc_servers {
                    if let Some(servers) = disk_env.get("servers").cloned() {
                        if let Some(inc_env) = data.get_mut("env").and_then(Value::as_object_mut) {
                            inc_env.insert("servers".to_string(), servers);
                        }
                    }
                    crate::pylog::warning("cookbook state POST: incoming env.servers empty; preserved on-disk servers (anti-wipe guard)");
                }
            }
        }

        // Task race-window merge.
        let disk_tasks: Vec<Value> = on_disk
            .get("tasks")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default();
        // Anti-poisoning guard: a stale browser tab can keep POSTing a download
        // task as status='done' from before the strict-finish fix landed, undoing
        // any server-side correction. For each incoming "done" download, override to
        // "running" if the last shard pattern says N<total AND no DOWNLOAD_OK/
        // DOWNLOAD_FAILED/`/snapshots/` sentinel is in the output. Mutated directly
        // on `data["tasks"]` (Python mutates the same list object) so the override
        // persists even when no race-window task is preserved.
        if let Some(tasks_arr) = data.get_mut("tasks").and_then(Value::as_array_mut) {
            for it in tasks_arr.iter_mut() {
                // `if (not dict) or type != "download" or status != "done": continue`
                let is_done_download = it
                    .as_object()
                    .map(|o| {
                        o.get("type").and_then(Value::as_str) == Some("download")
                            && o.get("status").and_then(Value::as_str) == Some("done")
                    })
                    .unwrap_or(false);
                if !is_done_download {
                    continue;
                }
                // `_out = _it.get("output") or ""`
                let out = it.get("output").and_then(Value::as_str).unwrap_or("").to_string();
                // `if OK or FAILED or "/snapshots/" in out: continue`
                if out.contains("DOWNLOAD_OK")
                    || out.contains("DOWNLOAD_FAILED")
                    || out.contains("/snapshots/")
                {
                    continue;
                }
                // `_it.get('sessionId')` in the log f-string: a missing key prints
                // Python's `None`.
                let session_id = it
                    .get("sessionId")
                    .and_then(Value::as_str)
                    .map(str::to_string)
                    .unwrap_or_else(|| "None".to_string());
                // `_shards = re.findall(r"model-(\d+)-of-(\d+)\.safetensors", _out)`
                let shards: Vec<(i64, i64)> = _SHARD_RE
                    .captures_iter(&out)
                    .filter_map(|c| {
                        let n = c.get(1)?.as_str().parse::<i64>().ok()?;
                        let tot = c.get(2)?.as_str().parse::<i64>().ok()?;
                        Some((n, tot))
                    })
                    .collect();
                let mut reject = false;
                if let Some(&(n, tot)) = shards.last() {
                    if n < tot {
                        crate::pylog::info(&format!(
                            "cookbook state POST: rejecting stale done for {session_id} (last shard {n}/{tot}, no DOWNLOAD_OK)"
                        ));
                        reject = true;
                    }
                } else {
                    // `_completed = _out.count("Download complete")`
                    let completed = out.matches("Download complete").count();
                    // `_starts = _out.count("Downloading '")`
                    let starts = out.matches("Downloading '").count();
                    if starts > completed {
                        crate::pylog::info(&format!(
                            "cookbook state POST: rejecting stale done for {session_id} ({completed}/{starts} files complete, no DOWNLOAD_OK)"
                        ));
                        reject = true;
                    }
                }
                if reject {
                    if let Some(o) = it.as_object_mut() {
                        o.insert("status".to_string(), Value::String("running".to_string()));
                    }
                }
            }
        }
        let incoming_tasks: Vec<Value> = data
            .get("tasks")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default();
        // `incoming_ids = {t.get("sessionId") for t in incoming_tasks if dict and sessionId}`
        let mut incoming_ids: HashSet<String> = HashSet::new();
        for t in &incoming_tasks {
            if let Some(sid) = t.get("sessionId").and_then(Value::as_str).filter(|s| !s.is_empty()) {
                incoming_ids.insert(sid.to_string());
            }
        }
        let now_ms: i64 = (crate::pytime::time() * 1000.0) as i64;
        let mut preserved: Vec<Value> = Vec::new();
        for t in &disk_tasks {
            if !t.is_object() {
                continue;
            }
            let sid = t.get("sessionId").and_then(Value::as_str);
            // `if not sid or sid in incoming_ids: continue`
            let sid = match sid.filter(|s| !s.is_empty()) {
                Some(s) => s,
                None => continue,
            };
            if incoming_ids.contains(sid) {
                continue;
            }
            // `ts = t.get("ts") or 0; if isinstance(ts, (int, float)) and (now_ms - ts) <= WINDOW:`
            let ts_val = t.get("ts");
            let ts: f64 = match ts_val {
                Some(Value::Number(n)) => n.as_f64().unwrap_or(0.0),
                // `t.get("ts") or 0`: a missing/None/falsy ts becomes 0 (an int).
                _ => 0.0,
            };
            if (now_ms as f64 - ts) <= RACE_WINDOW_MS as f64 {
                preserved.push(t.clone());
            }
        }
        if !preserved.is_empty() {
            let ids: Vec<&str> = preserved
                .iter()
                .map(|t| t.get("sessionId").and_then(Value::as_str).unwrap_or(""))
                .collect();
            crate::pylog::info(&format!(
                "cookbook state POST: preserving {} recent task(s) not in incoming body (race guard): {:?}",
                preserved.len(),
                ids
            ));
            // `data["tasks"] = incoming_tasks + preserved`
            let mut merged = incoming_tasks.clone();
            merged.extend(preserved.iter().cloned());
            if let Value::Object(d) = &mut data {
                d.insert("tasks".to_string(), Value::Array(merged));
            }
        }

        // `atomic_write_json(str(path), _state_for_storage(data, on_disk), indent=2)`
        // On a raised HTTPException, Python's outer `except Exception as e: str(e)`
        // surfaces the `"<status>: <detail>"` form (e.g. "400: Invalid token
        // characters"); `HttpException`'s Display matches that, so use `to_string()`
        // rather than the bare `.detail` (which would drop the "400: " prefix).
        let to_store = state_for_storage(data, &on_disk).map_err(|e| e.to_string())?;
        crate::core::atomic_io::atomic_write_json(&path.to_string_lossy(), &to_store, Some(2))
            .map_err(|e| e.to_string())?;
        Ok(preserved.len() as i64)
    })();

    match result {
        Ok(n) => Ok(Json(json!({"ok": true, "preserved": n})).into_response()),
        Err(e) => Ok(Json(json!({"ok": false, "error": e})).into_response()),
    }
}

/// `GET /api/cookbook/hf-latest` — fetch trending HF models filtered by VRAM fit.
async fn hf_latest(
    State(state): State<AppState>,
    headers: HeaderMap,
    user: Option<Extension<CurrentUser>>,
    Query(q): Query<HashMap<String, String>>,
) -> Result<Response, HttpException> {
    // `owner: str = Depends(require_user)` — authenticate (owner unused below).
    let _owner = require_user_gate(&state, &headers, user.as_deref())?;
    // FastAPI query coercion: `vram_gb: float = 0`, `limit: int = 10`,
    // `pipeline: str = "text-generation"`. A non-coercible value 422s.
    let vram_gb: f64 = match q.get("vram_gb") {
        Some(v) => coerce_float(v)?,
        None => 0.0,
    };
    let limit: i64 = match q.get("limit") {
        Some(v) => coerce_int(v)?,
        None => 10,
    };
    let pipeline = q
        .get("pipeline")
        .cloned()
        .unwrap_or_else(|| "text-generation".to_string());

    // `pool_size = max(limit * 15, 100)`
    let pool_size = (limit * 15).max(100);
    let url = format!(
        "https://huggingface.co/api/models?sort=trendingScore&direction=-1&limit={pool_size}&filter={pipeline}"
    );
    // `async with httpx.AsyncClient(timeout=15) as client: resp = await client.get(url)`
    let client = match reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(15))
        .build()
    {
        Ok(c) => c,
        Err(e) => return Ok(Json(json!({"models": [], "error": e.to_string()})).into_response()),
    };
    let raw: Value = match client.get(&url).send().await {
        Ok(resp) => {
            if resp.status().as_u16() != 200 {
                return Ok(Json(json!({"models": [], "error": format!("HF API HTTP {}", resp.status().as_u16())})).into_response());
            }
            match resp.json::<Value>().await {
                Ok(v) => v,
                Err(e) => return Ok(Json(json!({"models": [], "error": e.to_string()})).into_response()),
            }
        }
        Err(e) => return Ok(Json(json!({"models": [], "error": e.to_string()})).into_response()),
    };

    let raw_list = raw.as_array().cloned().unwrap_or_default();
    let mut out: Vec<Value> = Vec::new();
    for entry in &raw_list {
        // `repo_id = entry.get("modelId") or entry.get("id") or ""`
        let repo_id = entry
            .get("modelId")
            .and_then(Value::as_str)
            .filter(|s| !s.is_empty())
            .or_else(|| entry.get("id").and_then(Value::as_str).filter(|s| !s.is_empty()))
            .unwrap_or("")
            .to_string();
        if repo_id.is_empty() {
            continue;
        }
        // `tags = entry.get("tags") or []`
        let tags: Vec<String> = entry
            .get("tags")
            .and_then(Value::as_array)
            .map(|a| a.iter().filter_map(|t| t.as_str().map(str::to_string)).collect())
            .unwrap_or_default();
        let pipeline_tag = entry.get("pipeline_tag").and_then(Value::as_str).unwrap_or("").to_string();

        // `if pipeline and pipeline_tag and pipeline_tag != pipeline: continue`
        if !pipeline.is_empty() && !pipeline_tag.is_empty() && pipeline_tag != pipeline {
            continue;
        }
        if hf_is_excluded(&repo_id, &tags) {
            continue;
        }

        let est_fp16 = hf_est_vram_fp16(&repo_id);
        let quant_mult = hf_quant_factor(&repo_id, &tags);
        // `est_vram = (est_fp16 * quant_mult) if est_fp16 else None` — the guard is
        // Python truthiness on `est_fp16`, so a falsy `0.0` (e.g. a "0B" repo_id,
        // where `_est_vram_fp16` returns `0.0`) coerces `est_vram` to None just like
        // `None` does. `Option::map` would keep `Some(0.0)`, so filter the falsy
        // value out first to reproduce the coercion exactly.
        let est_vram = est_fp16.filter(|&f| f != 0.0).map(|f| f * quant_mult);
        // `needed_vram = (est_vram * 1.3) if est_vram else None` — same truthiness
        // guard; `est_vram` is already None-or-positive-nonzero here, so a plain
        // map is faithful.
        let needed_vram = est_vram.filter(|&v| v != 0.0).map(|v| v * 1.3);

        // `if vram_gb > 0 and needed_vram is not None and needed_vram > vram_gb: continue`
        if vram_gb > 0.0 {
            if let Some(nv) = needed_vram {
                if nv > vram_gb {
                    continue;
                }
            }
        }
        // Unknown-size models (e.g. MiniMax-M2.7, DeepSeek-V4-Flash) have no "NB"
        // in the repo id, so the regex above can't extract their param count.
        // Previously we dropped them entirely (`if est_vram is None: continue`),
        // which made brand-new flagship releases silently vanish from this list
        // even on rigs with hundreds of GB of VRAM. Adapters/LoRAs are already
        // filtered by `_is_excluded()`, so what falls through here is overwhelmingly
        // full models — keep them, just without a size badge (the frontend handles
        // needed_vram_gb=null gracefully).

        // tags[:5]
        let tags_trim: Vec<&String> = tags.iter().take(5).collect();
        out.push(json!({
            "repo_id": repo_id,
            "downloads": entry.get("downloads").cloned().unwrap_or(json!(0)),
            "likes": entry.get("likes").cloned().unwrap_or(json!(0)),
            "createdAt": entry.get("createdAt").cloned().unwrap_or(json!("")),
            "tags": tags_trim,
            "pipeline_tag": pipeline_tag,
            // `round(est_vram, 1) if est_vram else None` / `... if needed_vram else None`:
            // Python truthiness on the field write. Unknown-size models now fall
            // through with `est_vram = None` (no longer dropped), so this maps to
            // JSON null — a missing size badge — while a known positive est_vram is
            // rounded. The `.filter(v != 0.0)` keeps the `if est_vram` truthiness
            // guard explicit/faithful.
            "est_vram_gb": est_vram.filter(|&v| v != 0.0).map(round1),
            "needed_vram_gb": needed_vram.filter(|&v| v != 0.0).map(round1),
        }));
        if out.len() as i64 >= limit {
            break;
        }
    }

    Ok(Json(json!({"models": out})).into_response())
}

/// `GET /api/cookbook/tasks/status` — poll all active cookbook tmux sessions.
async fn cookbook_tasks_status(
    State(state): State<AppState>,
    headers: HeaderMap,
    user: Option<Extension<CurrentUser>>,
) -> Result<Response, HttpException> {
    admin_gate(&state, &headers, user.as_deref())?;
    // `return await asyncio.to_thread(_cookbook_tasks_status_sync)` — tokio::process
    // is already async, so no worker-thread hop is required.
    let body = cookbook_tasks_status_inner().await;
    Ok(Json(body).into_response())
}

/// `_download_cache_complete(repo_id, remote_host="", ssh_port="")` — best-effort
/// check for a completed HF cache entry.
///
/// tmux output can stop at a stale progress line if the pane/session disappears
/// before Cookbook captures the final `DOWNLOAD_OK` marker. In that case, trust the
/// cache shape: a snapshot directory with files and no `*.incomplete` blobs means
/// HuggingFace finished materializing the model. Runs a tiny Python probe locally
/// or over ssh (exit 0 = complete).
async fn download_cache_complete(repo_id: &str, remote_host: &str, ssh_port: &str) -> bool {
    // `if not repo_id or "/" not in repo_id: return False`
    if repo_id.is_empty() || !repo_id.contains('/') {
        return false;
    }
    let py = concat!(
        "import os,sys;",
        "repo=sys.argv[1];",
        "base=os.environ.get('HUGGINGFACE_HUB_CACHE') or os.path.join(os.environ.get('HF_HOME', os.path.expanduser('~/.cache/huggingface')), 'hub');",
        "d=os.path.join(base,'models--'+repo.replace('/','--'));",
        "snap=os.path.join(d,'snapshots');",
        "ok=os.path.isdir(snap) and any(os.path.isdir(os.path.join(snap,x)) and os.listdir(os.path.join(snap,x)) for x in os.listdir(snap));",
        "inc=False;",
        "blobs=os.path.join(d,'blobs');",
        "inc=os.path.isdir(blobs) and any(x.endswith('.incomplete') for x in os.listdir(blobs));",
        "sys.exit(0 if ok and not inc else 1)"
    );
    // `cmd = ["python3", "-c", py, repo_id]`
    let cmd = vec![
        "python3".to_string(),
        "-c".to_string(),
        py.to_string(),
        repo_id.to_string(),
    ];
    // `try: ... except Exception: return False`
    let argv: Vec<String> = if !remote_host.is_empty() {
        // ssh_base = ["ssh"] (+ ["-p", port] if port and != "22"); then remote_host
        // + " ".join(shlex.quote(x) for x in cmd)
        let mut ssh_base = vec!["ssh".to_string()];
        if !ssh_port.is_empty() && ssh_port != "22" {
            ssh_base.push("-p".to_string());
            ssh_base.push(ssh_port.to_string());
        }
        let shell_cmd = cmd.iter().map(|x| shquote(x)).collect::<Vec<_>>().join(" ");
        ssh_base.push(remote_host.to_string());
        ssh_base.push(shell_cmd);
        ssh_base
    } else {
        cmd
    };
    matches!(run_exec_timeout(&argv, 12).await, Ok(Some(out)) if out.status.success())
}

/// `_cookbook_tasks_status_sync()` — the bulk of the status poller.
// Mirrors Python cookbook_routes.py:1680-1695: a deliberate if/elif chain whose
// distinct conditions intentionally yield the same "error" status.
#[allow(clippy::if_same_then_else)]
// `status` starts at the Python default "unknown" and is overwritten on every
// classification branch — the initial value is intentionally a faithful default,
// not a dead store.
#[allow(unused_assignments)]
async fn cookbook_tasks_status_inner() -> Value {
    // Load saved tasks from cookbook state.
    let mut tasks: Vec<Value> = Vec::new();
    let path = cookbook_state_path();
    if path.exists() {
        if let Some(state) = std::fs::read_to_string(&path)
            .ok()
            .and_then(|t| serde_json::from_str::<Value>(&t).ok())
        {
            match state.get("tasks") {
                Some(Value::Array(a)) => tasks = a.clone(),
                // `elif isinstance(saved_tasks, dict): tasks = list(saved_tasks.values())`
                Some(Value::Object(o)) => tasks = o.values().cloned().collect(),
                _ => {}
            }
        }
    }

    let mut results: Vec<Value> = Vec::new();
    for task in &tasks {
        let session_id = task.get("sessionId").and_then(Value::as_str).unwrap_or("");
        if session_id.is_empty() {
            continue;
        }
        let remote = task.get("remoteHost").and_then(Value::as_str).unwrap_or("");
        let task_type = task.get("type").and_then(Value::as_str).unwrap_or("download");
        let payload = task.get("payload").filter(|v| v.is_object());
        // model = modelId or repoId or name or payload.repo_id or payload.modelId or ""
        let model = task
            .get("modelId")
            .and_then(Value::as_str)
            .filter(|s| !s.is_empty())
            .or_else(|| task.get("repoId").and_then(Value::as_str).filter(|s| !s.is_empty()))
            .or_else(|| task.get("name").and_then(Value::as_str).filter(|s| !s.is_empty()))
            .or_else(|| payload.and_then(|p| p.get("repo_id")).and_then(Value::as_str).filter(|s| !s.is_empty()))
            .or_else(|| payload.and_then(|p| p.get("modelId")).and_then(Value::as_str).filter(|s| !s.is_empty()))
            .unwrap_or("")
            .to_string();
        let task_platform = task.get("platform").and_then(Value::as_str).unwrap_or("");
        // `_tport = task.get("sshPort", "")` — may be a string OR a number in state.
        let tport = json_to_port_string(task.get("sshPort"));

        // Defense-in-depth filters.
        if !_SESSION_ID_RE.is_match(session_id) {
            crate::pylog::warning(&format!("Skipping task with unsafe session_id: {}", py_repr(session_id)));
            continue;
        }
        if !remote.is_empty() && !_REMOTE_HOST_RE.is_match(remote) {
            crate::pylog::warning(&format!("Skipping task with unsafe remoteHost: {}", py_repr(remote)));
            continue;
        }
        if !tport.is_empty() && !_SSH_PORT_RE.is_match(&tport) {
            crate::pylog::warning(&format!("Skipping task with unsafe sshPort: {}", py_repr(&tport)));
            continue;
        }

        // Build check + capture commands.
        let (check_cmd, capture_cmd): (Vec<String>, Vec<String>) =
            if task_platform == "windows" && !remote.is_empty() {
                let sd = r"$env:TEMP\odysseus-sessions";
                let mut ssh_base = vec!["ssh".to_string()];
                if !tport.is_empty() && tport != "22" {
                    ssh_base.push("-p".to_string());
                    ssh_base.push(tport.clone());
                }
                // NOTE the doubled `{{`/`}}` in the Python f-string are a latent bug
                // (no interpolation in those segments) — reproduced literally.
                let mut check = ssh_base.clone();
                check.extend([
                    remote.to_string(),
                    "powershell".to_string(),
                    "-Command".to_string(),
                    format!(
                        "$pid = Get-Content \"{sd}\\{session_id}.pid\" -ErrorAction SilentlyContinue; \
if ($pid) {{ Get-Process -Id $pid -ErrorAction SilentlyContinue | Out-Null; if ($?) {{ exit 0 }} else {{ exit 1 }} }} else {{ exit 1 }}"
                    ),
                ]);
                let mut capture = ssh_base.clone();
                capture.extend([
                    remote.to_string(),
                    "powershell".to_string(),
                    "-Command".to_string(),
                    format!("Get-Content \"{sd}\\{session_id}.log\" -Tail 10 -ErrorAction SilentlyContinue"),
                ]);
                (check, capture)
            } else if !remote.is_empty() {
                let mut ssh_base = vec!["ssh".to_string()];
                if !tport.is_empty() && tport != "22" {
                    ssh_base.push("-p".to_string());
                    ssh_base.push(tport.clone());
                }
                let mut check = ssh_base.clone();
                check.extend([
                    remote.to_string(),
                    "tmux".to_string(),
                    "has-session".to_string(),
                    "-t".to_string(),
                    session_id.to_string(),
                ]);
                let mut capture = ssh_base.clone();
                capture.extend([
                    remote.to_string(),
                    "tmux".to_string(),
                    "capture-pane".to_string(),
                    "-t".to_string(),
                    session_id.to_string(),
                    "-p".to_string(),
                    "-S".to_string(),
                    "-50".to_string(),
                ]);
                (check, capture)
            } else {
                (
                    vec![
                        "tmux".to_string(),
                        "has-session".to_string(),
                        "-t".to_string(),
                        session_id.to_string(),
                    ],
                    vec![
                        "tmux".to_string(),
                        "capture-pane".to_string(),
                        "-t".to_string(),
                        session_id.to_string(),
                        "-p".to_string(),
                        "-S".to_string(),
                        "-50".to_string(),
                    ],
                )
            };

        // `try: alive = subprocess.run(check_cmd, timeout=10, ...); is_alive = rc == 0 except: False`
        let is_alive = match run_exec_timeout(&check_cmd, 10).await {
            Ok(Some(out)) => out.status.success(),
            _ => false,
        };

        let mut progress_text = String::new();
        let mut full_snapshot = String::new();
        if is_alive {
            // `try: cap = subprocess.run(capture_cmd, timeout=10, text=True); ... except: pass`
            if let Ok(Some(cap)) = run_exec_timeout(&capture_cmd, 10).await {
                if cap.status.success() {
                    full_snapshot = String::from_utf8_lossy(&cap.stdout).trim().to_string();
                    // lines = [l.strip() for l in split('\n') if l.strip()]
                    let lines: Vec<&str> = full_snapshot
                        .split('\n')
                        .map(str::trim)
                        .filter(|l| !l.is_empty())
                        .collect();
                    let downloading: Vec<&str> =
                        lines.iter().copied().filter(|l| l.starts_with("Downloading")).collect();
                    if let Some(last) = downloading.last() {
                        progress_text = last.to_string();
                    } else if let Some(last) = lines.last() {
                        progress_text = last.to_string();
                    }
                }
            }
        }

        // Determine status.
        let mut download_zero_files = false;
        // `status = "unknown"` initial default.
        let mut status: String = "unknown".to_string();
        if is_alive {
            let lower = full_snapshot.to_lowercase();
            // `exit_match = re.search(r"=== process exited with code\s+(-?\d+)", ...)`
            let exit_caps = _EXIT_CODE_RE.captures(&full_snapshot);
            let has_exit = exit_caps.is_some();
            let exit_code: Option<i64> = exit_caps
                .and_then(|c| c.get(1))
                .and_then(|m| m.as_str().parse().ok());
            let has_error = lower.contains("error") || lower.contains("failed") || lower.contains("traceback");
            if has_exit && task_type == "serve" {
                // Serve tasks that exit are always errors — they should run forever.
                status = "error".to_string();
            } else if has_exit && task_type == "download" {
                // Dependency installs are tracked as download tasks but only emit
                // the generic runner exit marker, not HF download markers.
                status = if exit_code == Some(0) { "completed" } else { "error" }.to_string();
            } else if has_exit && lower.contains("unrecognized arguments") {
                status = "error".to_string();
            } else if has_error && !lower.contains("application startup complete") {
                status = "error".to_string();
            } else if task_type == "download"
                && (full_snapshot.contains("100%") || full_snapshot.contains("DOWNLOAD_OK"))
            {
                // Only download tasks treat 100% as "completed". A 100% line with
                // "Fetching 0 files" actually fetched nothing (bad repo / quant
                // pattern) — flag it as an error instead.
                if _FETCHING_ZERO_RE.is_match(&full_snapshot) {
                    status = "error".to_string();
                    download_zero_files = true;
                } else {
                    status = "completed".to_string();
                }
            } else if lower.contains("application startup complete") {
                status = "ready".to_string();
            } else {
                status = "running".to_string();
            }
        } else {
            // Session is dead — check if it completed (cache shape) or crashed.
            // `_payload.get("repo_id") or model`
            let cache_repo = payload
                .and_then(|p| p.get("repo_id"))
                .and_then(Value::as_str)
                .filter(|s| !s.is_empty())
                .unwrap_or(&model);
            if task_type == "download"
                && download_cache_complete(cache_repo, remote, &tport).await
            {
                status = "completed".to_string();
                if progress_text.is_empty() {
                    progress_text = "Download complete".to_string();
                }
                if full_snapshot.is_empty() {
                    full_snapshot = "DOWNLOAD_OK".to_string();
                }
            } else {
                status = "stopped".to_string();
            }
        }

        // Parse structured phase info.
        let phase_info: Value =
            if task_type == "serve" && status == "running" && !full_snapshot.is_empty() {
                _parse_serve_phase(&full_snapshot, task_type)
            } else {
                json!({})
            };
        if phase_info.get("status").and_then(Value::as_str) == Some("ready") {
            status = "ready".to_string();
        }
        let serve_phase = phase_info.get("phase").and_then(Value::as_str).unwrap_or("").to_string();
        let mut diagnosis = if task_type == "serve" && !full_snapshot.is_empty() {
            diagnose_serve_output(&full_snapshot)
        } else {
            None
        };
        // `if diagnosis and status in {"running", "unknown", "stopped"}: status = "error"`
        if diagnosis.is_some() && matches!(status.as_str(), "running" | "unknown" | "stopped") {
            status = "error".to_string();
        }
        // A download whose 100% line fetched 0 files: attach a clear diagnosis
        // pointing at a wrong repo / quant pattern. Overrides any serve diagnosis
        // (download tasks never produce one).
        if download_zero_files {
            diagnosis = Some(json!({"message": "No matching files were downloaded. The model repo or filename/quant pattern may be wrong (for example a ':Q4_K_M' tag that does not exist in the repo). Check the repo and the include/quant pattern."}));
        }
        // output_tail = "\n".join(full_snapshot.splitlines()[-12:])
        let output_tail = if full_snapshot.is_empty() {
            String::new()
        } else {
            let lines: Vec<&str> = full_snapshot.split('\n').collect();
            let start = lines.len().saturating_sub(12);
            lines[start..].join("\n")
        };

        // model.split("/")[-1] if "/" in model else model
        let model_disp = if model.contains('/') {
            model.rsplit('/').next().unwrap_or(&model).to_string()
        } else {
            model.clone()
        };
        // progress = serve_phase if serve else progress_text[:120]
        let progress: Value = if task_type == "serve" {
            Value::String(serve_phase.clone())
        } else {
            Value::String(tail_chars_front(&progress_text, 120))
        };

        results.push(json!({
            "session_id": session_id,
            "type": task_type,
            "model": model_disp,
            "status": status,
            "progress": progress,
            "phase": serve_phase,
            "diagnosis": diagnosis,
            "output_tail": output_tail,
            "cmd": payload.and_then(|p| p.get("_cmd")).and_then(Value::as_str).unwrap_or(""),
            "tps": phase_info.get("tps").cloned().unwrap_or(Value::Null),
            "reqs": phase_info.get("reqs").cloned().unwrap_or(Value::Null),
            "pct": phase_info.get("pct").cloned().unwrap_or(Value::Null),
            "remote": if remote.is_empty() { "local" } else { remote },
        }));
    }

    json!({"tasks": results})
}

// ===========================================================================
// GPU probe helpers (the inner `_run_nvidia_smi` / `_run_gpu_shell` / etc.)
// ===========================================================================

/// `_run_nvidia_smi(query, host, ssh_port, timeout=8)` -> `(stdout, error_or_None)`.
async fn run_nvidia_smi(
    query: &str,
    host: Option<&str>,
    ssh_port: Option<&str>,
    timeout: u64,
) -> (Option<String>, Option<String>) {
    let result = if let Some(host) = host.filter(|h| !h.is_empty()) {
        let pf = port_flag_low_p(ssh_port);
        let cmd = format!("ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no {pf}{host} '{query}'");
        let mut c = Command::new("sh");
        c.arg("-c").arg(&cmd);
        c.stdout(Stdio::piped()).stderr(Stdio::piped());
        run_with_timeout(c, timeout).await
    } else {
        // `proc = await create_subprocess_exec(*shlex.split(query), ...)`
        let argv = shlex::split(query).unwrap_or_default();
        if argv.is_empty() {
            // shlex.split of a non-empty string is non-empty; defensive.
            return (None, Some("nvidia-smi failed".to_string()));
        }
        let mut c = Command::new(&argv[0]);
        c.args(&argv[1..]);
        c.stdout(Stdio::piped()).stderr(Stdio::piped());
        // `except FileNotFoundError:` in list_gpus catches the missing binary; here
        // the launch error maps to that same branch (handled by the caller checking
        // for None). We surface a launch failure as a timeout-style None below.
        run_with_timeout(c, timeout).await
    };
    match result {
        Ok(Some(out)) => {
            if !out.status.success() {
                // err = stderr.decode().strip()[:200]; return None, err or "nvidia-smi failed"
                let err = tail_chars_front(String::from_utf8_lossy(&out.stderr).trim(), 200);
                let err = if err.is_empty() { "nvidia-smi failed".to_string() } else { err };
                return (None, Some(err));
            }
            (Some(String::from_utf8_lossy(&out.stdout).into_owned()), None)
        }
        // `except asyncio.TimeoutError: proc.kill(); return None, "nvidia-smi timed out"`
        Ok(None) => (None, Some("nvidia-smi timed out".to_string())),
        // Launch failure (FileNotFoundError equivalent) — the local path's
        // `except FileNotFoundError` sets `nvidia-smi not found`; the remote path
        // would not raise. Surface a generic failure.
        Err(_) => (None, Some("nvidia-smi not found".to_string())),
    }
}

/// `_run_gpu_shell(cmd_text, host, ssh_port, timeout=8)` -> `(stdout, error_or_None)`.
async fn run_gpu_shell(
    cmd_text: &str,
    host: Option<&str>,
    ssh_port: Option<&str>,
    timeout: u64,
) -> (Option<String>, Option<String>) {
    let result = if let Some(host) = host.filter(|h| !h.is_empty()) {
        let pf = port_flag_low_p(ssh_port);
        let quoted_cmd = shquote(cmd_text);
        let remote_cmd = format!(
            "if command -v sh >/dev/null 2>&1; then sh -lc {quoted_cmd}; \
elif command -v bash >/dev/null 2>&1; then bash -lc {quoted_cmd}; \
elif command -v zsh >/dev/null 2>&1; then zsh -lc {quoted_cmd}; \
else echo 'No POSIX shell found for GPU probe' >&2; exit 127; fi"
        );
        let cmd = format!(
            "ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no {pf}{host} {}",
            shquote(&remote_cmd)
        );
        let mut c = Command::new("sh");
        c.arg("-c").arg(&cmd);
        c.stdout(Stdio::piped()).stderr(Stdio::piped());
        run_with_timeout(c, timeout).await
    } else {
        let mut c = Command::new("sh");
        c.arg("-c").arg(cmd_text);
        c.stdout(Stdio::piped()).stderr(Stdio::piped());
        run_with_timeout(c, timeout).await
    };
    match result {
        Ok(Some(out)) => {
            if !out.status.success() {
                let err = tail_chars_front(String::from_utf8_lossy(&out.stderr).trim(), 200);
                let rc = out.status.code().unwrap_or(-1);
                let err = if err.is_empty() {
                    format!("GPU probe failed ({rc})")
                } else {
                    err
                };
                return (None, Some(err));
            }
            (Some(String::from_utf8_lossy(&out.stdout).into_owned()), None)
        }
        Ok(None) => (None, Some("GPU probe timed out".to_string())),
        Err(_) => (None, Some("GPU probe failed (1)".to_string())),
    }
}

/// `_gpu_read_file(path, host, ssh_port)`.
async fn gpu_read_file(path: &str, host: Option<&str>, ssh_port: Option<&str>) -> Option<String> {
    let (out, err) =
        run_gpu_shell(&format!("cat {} 2>/dev/null", shquote(path)), host, ssh_port, 4).await;
    if err.is_some() || out.is_none() {
        return None;
    }
    out.map(|s| s.trim().to_string())
}

/// `_probe_gpu_device_processes(host, ssh_port)`.
async fn probe_gpu_device_processes(host: Option<&str>, ssh_port: Option<&str>) -> Vec<Value> {
    let pid_cmd = "{ command -v lsof >/dev/null 2>&1 && \
lsof -w -t /dev/kfd /dev/dri/renderD* 2>/dev/null || true; \
command -v fuser >/dev/null 2>&1 && \
fuser /dev/kfd /dev/dri/renderD* 2>/dev/null || true; } \
| tr ' ' '\\n' | sed '/^[0-9][0-9]*$/!d' | sort -n -u";
    let (out, err) = run_gpu_shell(pid_cmd, host, ssh_port, 5).await;
    let out = match out {
        Some(o) if err.is_none() && !o.is_empty() => o,
        _ => return Vec::new(),
    };
    let mut processes: Vec<Value> = Vec::new();
    let mut seen: HashSet<i64> = HashSet::new();
    for raw in out.lines() {
        let pid: i64 = match raw.trim().parse() {
            Ok(p) => p,
            Err(_) => continue,
        };
        if seen.contains(&pid) {
            continue;
        }
        seen.insert(pid);
        let (name_out, _) =
            run_gpu_shell(&format!("ps -p {pid} -o comm= 2>/dev/null"), host, ssh_port, 3).await;
        // name = (name_out or "").strip().splitlines()[0] if stripped else "process"
        let name = match name_out {
            Some(n) if !n.trim().is_empty() => {
                n.trim().lines().next().unwrap_or("process").to_string()
            }
            _ => "process".to_string(),
        };
        // name[:80]
        let name80: String = name.chars().take(80).collect();
        processes.push(json!({"pid": pid, "name": name80, "used_mb": 0}));
    }
    processes
}

/// `_probe_amd_sysfs(host, ssh_port)`.
async fn probe_amd_sysfs(host: Option<&str>, ssh_port: Option<&str>) -> Vec<Value> {
    let (out, err) = run_gpu_shell("ls -1 /sys/class/drm 2>/dev/null", host, ssh_port, 4).await;
    let out = match out {
        Some(o) if err.is_none() && !o.is_empty() => o,
        _ => return Vec::new(),
    };
    let mut gpus: Vec<Value> = Vec::new();
    // `for entry in out.split():` — whitespace split.
    for entry in out.split_whitespace() {
        if !entry.starts_with("card") || entry.contains('-') {
            continue;
        }
        let base = format!("/sys/class/drm/{entry}/device");
        let vendor = gpu_read_file(&format!("{base}/vendor"), host, ssh_port).await;
        if vendor.as_deref() != Some("0x1002") {
            continue;
        }
        let vram_raw = gpu_read_file(&format!("{base}/mem_info_vram_total"), host, ssh_port).await;
        let vis_raw = gpu_read_file(&format!("{base}/mem_info_vis_vram_total"), host, ssh_port).await;
        let gtt_raw = gpu_read_file(&format!("{base}/mem_info_gtt_total"), host, ssh_port).await;
        let vram_bytes = digits_to_i64(vram_raw.as_deref());
        let vis_bytes = digits_to_i64(vis_raw.as_deref());
        let gtt_bytes = digits_to_i64(gtt_raw.as_deref());
        let mut total_bytes = vram_bytes.max(vis_bytes);
        let mut used_attr = if vis_bytes != 0 && vis_bytes >= vram_bytes {
            "mem_info_vis_vram_used"
        } else {
            "mem_info_vram_used"
        };
        let mut unified = vis_bytes != 0 && vis_bytes >= vram_bytes;
        if total_bytes <= 0 {
            total_bytes = gtt_bytes;
            used_attr = "mem_info_gtt_used";
            unified = true;
        }
        if total_bytes <= 0 {
            continue;
        }
        let used_raw = gpu_read_file(&format!("{base}/{used_attr}"), host, ssh_port).await;
        let used_bytes = digits_to_i64(used_raw.as_deref());
        let mut name = gpu_read_file(&format!("{base}/product_name"), host, ssh_port).await;
        if name.as_deref().map(str::is_empty).unwrap_or(true) {
            let device = gpu_read_file(&format!("{base}/device"), host, ssh_port).await;
            let dev = device.filter(|d| !d.is_empty()).unwrap_or_else(|| entry.to_string());
            name = Some(format!("AMD GPU {dev}"));
        }
        let name = name.unwrap_or_default();
        // total_mb = max(0, int(total_bytes / (1024*1024)))
        let total_mb = (total_bytes / (1024 * 1024)).max(0);
        // used_mb = max(0, min(total_mb, int(used_bytes / (1024*1024))))
        let used_mb = (used_bytes / (1024 * 1024)).min(total_mb).max(0);
        let free_mb = (total_mb - used_mb).max(0);
        let idx = gpus.len() as i64;
        let busy = total_mb != 0 && (free_mb as f64 / total_mb as f64) < 0.85;
        gpus.push(json!({
            "index": idx, "name": name, "uuid": entry,
            "free_mb": free_mb, "total_mb": total_mb, "used_mb": used_mb,
            "util_pct": 0, "busy": busy,
            "processes": [], "backend": "rocm", "source": "amd-sysfs",
            "unified_memory": unified,
        }));
    }
    if !gpus.is_empty() {
        let processes = probe_gpu_device_processes(host, ssh_port).await;
        if !processes.is_empty() {
            if let Some(Value::Object(o)) = gpus.first_mut() {
                o.insert("processes".to_string(), Value::Array(processes));
                o.insert("busy".to_string(), Value::Bool(true));
            }
        }
    }
    gpus
}

// ===========================================================================
// Small faithful utility helpers
// ===========================================================================

type R<T> = Result<T, HttpException>;

/// `_safe_env_prefix(ep)` always returns a string at the Python call sites (each
/// is inside `if req.env_prefix:`, so `ep` is non-empty). The helper returns
/// `R<Option<String>>`; here `ep` is guaranteed non-empty, so `Ok(Some(s))`.
fn safe_env_prefix_str(ep: &str) -> R<String> {
    Ok(_safe_env_prefix(Some(ep))?.unwrap_or_default())
}

/// `os.path.expanduser("~")` — the user's home directory.
fn home_dir() -> String {
    std::env::var("HOME").unwrap_or_default()
}

/// `shutil.which(name)` — first executable `name` on `$PATH`, else `None`.
fn shutil_which(name: &str) -> Option<String> {
    let path_var = std::env::var_os("PATH")?;
    for dir in std::env::split_paths(&path_var) {
        let candidate = dir.join(name);
        if candidate.is_file() && is_executable(&candidate) {
            return Some(candidate.to_string_lossy().into_owned());
        }
    }
    None
}

#[cfg(unix)]
fn is_executable(p: &FsPath) -> bool {
    use std::os::unix::fs::PermissionsExt;
    std::fs::metadata(p).map(|m| m.permissions().mode() & 0o111 != 0).unwrap_or(false)
}

#[cfg(not(unix))]
fn is_executable(p: &FsPath) -> bool {
    p.is_file()
}

/// `shlex.quote` — POSIX-safe quoting.
fn shquote(s: &str) -> String {
    shlex::try_quote(s).map(|c| c.into_owned()).unwrap_or_else(|_| s.to_string())
}

/// Python `repr(s)` for a string — single-quote-preferred with the standard
/// escapes. Matches CPython's `repr()` closely enough for the `scan_dir(...)`
/// embedding (the chars allowed by `_validate_local_dir` / session ids never need
/// the double-quote-switch path, but reproduce the full algorithm anyway).
fn py_repr(s: &str) -> String {
    // CPython prefers single quotes unless the string has a `'` and no `"`.
    let has_single = s.contains('\'');
    let has_double = s.contains('"');
    let (quote, escape_quote) = if has_single && !has_double {
        ('"', '"')
    } else {
        ('\'', '\'')
    };
    let mut out = String::new();
    out.push(quote);
    for c in s.chars() {
        match c {
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if c == escape_quote => {
                out.push('\\');
                out.push(c);
            }
            c if (c as u32) < 0x20 || (c as u32) == 0x7f => {
                out.push_str(&format!("\\x{:02x}", c as u32));
            }
            c => out.push(c),
        }
    }
    out.push(quote);
    out
}

/// `-P {port} ` (capital, scp) when port set and != "22", else "".
fn port_flag_cap_p(port: Option<&str>) -> String {
    match port {
        Some(p) if !p.is_empty() && p != "22" => format!("-P {p} "),
        _ => String::new(),
    }
}

/// `-p {port} ` (lowercase, ssh) when port set and != "22", else "".
fn port_flag_low_p(port: Option<&str>) -> String {
    match port {
        Some(p) if !p.is_empty() && p != "22" => format!("-p {p} "),
        _ => String::new(),
    }
}

/// `nonempty(Some(""))` -> None; `nonempty(Some("x"))` -> Some("x"); None -> None.
fn nonempty(v: Option<String>) -> Option<String> {
    v.filter(|s| !s.is_empty())
}

/// Borrowed non-empty view of an `Option<String>`.
fn nonempty_ref(v: &Option<String>) -> Option<&str> {
    v.as_deref().filter(|s| !s.is_empty())
}

/// `text[-n:]` — last `n` code points.
fn tail_chars(s: &str, n: usize) -> String {
    let chars: Vec<char> = s.chars().collect();
    if chars.len() <= n {
        return s.to_string();
    }
    chars[chars.len() - n..].iter().collect()
}

/// `text[:n]` — first `n` code points.
fn tail_chars_front(s: &str, n: usize) -> String {
    s.chars().take(n).collect()
}

/// Python truthiness for a JSON value (used by the env.servers / is_local_dir / ts
/// `... or ...` checks).
fn value_truthy(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::String(s) => !s.is_empty(),
        Value::Number(n) => n.as_f64().map(|f| f != 0.0).unwrap_or(true),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

/// `int(float(s))` — Python parses the CSV cell as a float then truncates to int.
/// Returns `None` on a non-numeric cell (the `except (ValueError, IndexError)`).
fn parse_int_via_float(s: &str) -> Option<i64> {
    s.trim().parse::<f64>().ok().map(|f| f as i64)
}

/// `int(raw) if raw and raw.isdigit() else 0`.
fn digits_to_i64(raw: Option<&str>) -> i64 {
    match raw {
        Some(r) if !r.is_empty() && r.chars().all(|c| c.is_ascii_digit()) => r.parse().unwrap_or(0),
        _ => 0,
    }
}

/// `task.get("sshPort", "")` may be a string or a number in the JSON state; Python
/// passes it through `str(_tport)` before the regex check. Reproduce: a string
/// stays as-is, a number renders without a trailing `.0` when integral.
fn json_to_port_string(v: Option<&Value>) -> String {
    match v {
        Some(Value::String(s)) => s.clone(),
        Some(Value::Number(n)) => {
            if let Some(i) = n.as_i64() {
                i.to_string()
            } else if let Some(f) = n.as_f64() {
                // `str(float)` — but ports are integral; render plainly.
                if f.fract() == 0.0 {
                    (f as i64).to_string()
                } else {
                    f.to_string()
                }
            } else {
                n.to_string()
            }
        }
        // missing / null -> "" (the `.get("sshPort", "")` default; null is falsy too).
        _ => String::new(),
    }
}

/// FastAPI/pydantic `float` query coercion — `422` on a non-float value.
fn coerce_float(v: &str) -> R<f64> {
    v.trim()
        .parse::<f64>()
        .map_err(|_| HttpException::new(422, "Input should be a valid number"))
}

/// FastAPI/pydantic `int` query coercion — `422` on a non-int value.
fn coerce_int(v: &str) -> R<i64> {
    v.trim()
        .parse::<i64>()
        .map_err(|_| HttpException::new(422, "Input should be a valid integer"))
}

/// `round(x, 1)` — banker's rounding (round-half-to-even), matching CPython.
fn round1(x: f64) -> f64 {
    round_half_even(x, 1)
}

// Round-half-to-even: the floor/floor and +1.0/+1.0 branches are deliberately
// identical, distinguished only by the half-to-even tie-break condition.
#[allow(clippy::if_same_then_else)]
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
    } else if (floor as i64) % 2 == 0 {
        floor
    } else {
        floor + 1.0
    };
    rounded / factor
}

// --- hf_latest pure helpers ------------------------------------------------

static _EST_VRAM_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"[-_/](\d+(?:\.\d+)?)\s*[Bb]([^a-zA-Z]|$)").unwrap());

/// `_est_vram_fp16(repo_id)` — params*2 (fp16) from a `7B`/`70B`/`1.5B` token.
///
/// Python: `re.search(r'[-_/](\d+(?:\.\d+)?)\s*[Bb](?![a-zA-Z])', repo_id)`. The
/// `(?![a-zA-Z])` negative-lookahead is unsupported by the `regex` crate, so it is
/// re-expressed as `([^a-zA-Z]|$)` and the group-1 capture is read explicitly.
fn hf_est_vram_fp16(repo_id: &str) -> Option<f64> {
    let caps = _EST_VRAM_RE.captures(repo_id)?;
    let params_b: f64 = caps.get(1)?.as_str().parse().ok()?;
    Some(params_b * 2.0)
}

/// `_quant_factor(repo_id, tags)`.
fn hf_quant_factor(repo_id: &str, tags: &[String]) -> f64 {
    let text = format!("{} {}", repo_id, tags.join(" ")).to_lowercase();
    let any = |needles: &[&str]| needles.iter().any(|n| text.contains(n));
    if any(&["fp4", "nf4", "int4", "4bit", "q4", "awq", "gptq"]) {
        return 0.25;
    }
    if any(&["int8", "8bit", "q8", "fp8"]) {
        return 0.5;
    }
    // bf16/fp16 and the default both return 1.0.
    1.0
}

const HF_EXCLUDE_TAG_SUBSTRINGS: &[&str] = &[
    "lora", "adapter", "peft", "qlora",
    "dataset", "embeddings",
    "merge", "control-lora",
    "diffusion-lora", "stable-diffusion-lora",
    "text-classification", "token-classification",
    "feature-extraction", "sentence-similarity",
];
const HF_EXCLUDE_NAME_SUBSTRINGS: &[&str] = &[
    "lora", "adapter", "peft", "qlora",
    "embedding", "embed-",
    "dataset",
];

/// `_is_excluded(repo_id, tags)`.
fn hf_is_excluded(repo_id: &str, tags: &[String]) -> bool {
    let text = repo_id.to_lowercase();
    for s in HF_EXCLUDE_NAME_SUBSTRINGS {
        if text.contains(s) {
            return true;
        }
    }
    let tag_text = tags.iter().map(|t| t.to_lowercase()).collect::<Vec<_>>().join(" ");
    for s in HF_EXCLUDE_TAG_SUBSTRINGS {
        if tag_text.contains(s) {
            return true;
        }
    }
    false
}

// ===========================================================================
// Subprocess helpers (`create_subprocess_shell` / `_exec` + `asyncio.wait_for`)
// ===========================================================================

/// `await create_subprocess_shell(cmd); await proc.wait()` then read stderr —
/// runs `/bin/sh -c cmd` to completion (no timeout) collecting output.
async fn run_shell(cmd: &str) -> std::io::Result<std::process::Output> {
    Command::new("sh")
        .arg("-c")
        .arg(cmd)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .output()
        .await
}

/// `await asyncio.wait_for(proc.communicate(), timeout=secs)` for a pre-built
/// `Command`. `Ok(Some(out))` on completion, `Ok(None)` on timeout (the
/// `asyncio.TimeoutError` branch — the process is killed), `Err` on launch error.
async fn run_with_timeout(mut cmd: Command, secs: u64) -> std::io::Result<Option<std::process::Output>> {
    let fut = cmd.output();
    match tokio::time::timeout(std::time::Duration::from_secs(secs), fut).await {
        Ok(res) => res.map(Some),
        Err(_) => Ok(None),
    }
}

/// `subprocess.run(argv, timeout=secs, capture_output=True)` — exec a program by
/// argv with a timeout. `Ok(Some(out))` / `Ok(None)` on timeout / `Err` on launch
/// failure (the Python `except Exception` branch the callers wrap).
async fn run_exec_timeout(argv: &[String], secs: u64) -> std::io::Result<Option<std::process::Output>> {
    if argv.is_empty() {
        return Ok(None);
    }
    let mut cmd = Command::new(&argv[0]);
    cmd.args(&argv[1..]);
    cmd.stdout(Stdio::piped()).stderr(Stdio::piped());
    run_with_timeout(cmd, secs).await
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn mask_secret_matches_python() {
        assert_eq!(mask_secret(""), "");
        assert_eq!(mask_secret("short"), "stored"); // len 5 <= 8
        assert_eq!(mask_secret("12345678"), "stored"); // len 8 <= 8
        assert_eq!(mask_secret("123456789"), "1234...6789"); // len 9 > 8
    }

    #[test]
    fn diagnose_serve_output_oom() {
        let d = diagnose_serve_output("...CUDA out of memory while loading...").unwrap();
        assert_eq!(d["message"], "GPU ran out of memory during startup or warmup.");
        assert_eq!(d["suggestions"][0]["flag"], "--max-model-len");
    }

    #[test]
    fn diagnose_serve_output_traceback_only() {
        let d = diagnose_serve_output("Traceback (most recent call last):\n  boom").unwrap();
        assert_eq!(d["message"], "Python traceback detected during serve startup.");
        // Startup-complete suppresses the traceback diagnosis.
        assert!(diagnose_serve_output(
            "Traceback (most recent call last):\nApplication startup complete"
        )
        .is_none());
    }

    #[test]
    fn diagnose_serve_output_empty_is_none() {
        assert!(diagnose_serve_output("").is_none());
        assert!(diagnose_serve_output("all good here").is_none());
    }

    #[test]
    fn diagnose_serve_output_new_rules() {
        // Kernel package mismatch.
        let d = diagnose_serve_output("Either a revision or a version must be specified").unwrap();
        assert_eq!(d["message"], "vLLM/Transformers kernel package mismatch.");
        assert_eq!(d["suggestions"][0]["package"], "vllm transformers kernels");

        // No supported GPU (CUDA/ROCm).
        let d = diagnose_serve_output("RuntimeError: Failed to infer device type").unwrap();
        assert!(d["message"].as_str().unwrap().starts_with("vLLM could not find a supported GPU (CUDA or ROCm). "));
        assert_eq!(d["suggestions"].as_array().unwrap().len(), 2);
        assert_eq!(d["suggestions"][0]["op"], "manual");

        // Missing GGUF file for the llama.cpp backend.
        let d = diagnose_serve_output("ERROR: No GGUF file found").unwrap();
        assert_eq!(
            d["message"],
            "No GGUF file found for this model on this host. The llama.cpp backend needs a .gguf file."
        );

        // Diffusion serving missing torch/diffusers.
        let d = diagnose_serve_output("ModuleNotFoundError: No module named 'diffusers'").unwrap();
        assert_eq!(d["message"], "Diffusion serving requires PyTorch and diffusers.");
        assert_eq!(d["suggestions"][0]["package"], "diffusers[torch]");
    }

    #[test]
    fn tasks_status_classification_regexes() {
        // Exit-marker parse captures the numeric code (incl. negatives).
        let caps = _EXIT_CODE_RE.captures("=== Process exited with code 0 ===").unwrap();
        assert_eq!(caps.get(1).unwrap().as_str(), "0");
        let caps = _EXIT_CODE_RE.captures("=== process exited with code -1 ===").unwrap();
        assert_eq!(caps.get(1).unwrap().as_str(), "-1");
        assert!(_EXIT_CODE_RE.captures("no marker here").is_none());

        // "Fetching 0 files" (case-insensitive) → zero-files signal.
        assert!(_FETCHING_ZERO_RE.is_match("Fetching 0 files: 100%"));
        assert!(_FETCHING_ZERO_RE.is_match("fetching   0   files"));
        assert!(!_FETCHING_ZERO_RE.is_match("Fetching 3 files"));

        // Shard markers used by the stale-done guard.
        let shards: Vec<(&str, &str)> = _SHARD_RE
            .captures_iter("model-00002-of-00005.safetensors model-00003-of-00005.safetensors")
            .map(|c| (c.get(1).unwrap().as_str(), c.get(2).unwrap().as_str()))
            .collect();
        assert_eq!(shards.last(), Some(&("00003", "00005")));
    }

    #[tokio::test]
    async fn download_cache_complete_rejects_non_repo() {
        // `if not repo_id or "/" not in repo_id: return False` — no subprocess.
        assert!(!download_cache_complete("", "", "").await);
        assert!(!download_cache_complete("no-slash-here", "", "").await);
    }

    #[test]
    fn est_vram_parses_b_token() {
        assert_eq!(hf_est_vram_fp16("org/Llama-7B"), Some(14.0));
        assert_eq!(hf_est_vram_fp16("org/Qwen-1.5B-Instruct"), Some(3.0));
        // `(?![a-zA-Z])` — "7Bit" must NOT match (B followed by a letter).
        assert_eq!(hf_est_vram_fp16("org/x-7Bit"), None);
        assert_eq!(hf_est_vram_fp16("org/no-size"), None);
    }

    #[test]
    fn quant_factor_buckets() {
        assert_eq!(hf_quant_factor("org/model-awq", &[]), 0.25);
        assert_eq!(hf_quant_factor("org/model-fp8", &[]), 0.5);
        assert_eq!(hf_quant_factor("org/model", &[]), 1.0);
        assert_eq!(hf_quant_factor("org/model", &["int4".to_string()]), 0.25);
    }

    #[test]
    fn is_excluded_name_and_tag() {
        assert!(hf_is_excluded("org/my-lora", &[]));
        assert!(hf_is_excluded("org/plain", &["adapter".to_string()]));
        assert!(!hf_is_excluded("org/plain-model", &["text-generation".to_string()]));
    }

    #[test]
    fn py_repr_quotes_like_python() {
        assert_eq!(py_repr("/models/x"), "'/models/x'");
        assert_eq!(py_repr("a'b"), "\"a'b\"");
        assert_eq!(py_repr("a'b\"c"), "'a\\'b\"c'");
        assert_eq!(py_repr("tab\there"), "'tab\\there'");
    }

    #[test]
    fn port_flags() {
        assert_eq!(port_flag_low_p(Some("2222")), "-p 2222 ");
        assert_eq!(port_flag_low_p(Some("22")), "");
        assert_eq!(port_flag_low_p(Some("")), "");
        assert_eq!(port_flag_low_p(None), "");
        assert_eq!(port_flag_cap_p(Some("2222")), "-P 2222 ");
    }

    #[test]
    fn json_port_string_handles_number_and_string() {
        assert_eq!(json_to_port_string(Some(&json!("2222"))), "2222");
        assert_eq!(json_to_port_string(Some(&json!(2222))), "2222");
        assert_eq!(json_to_port_string(Some(&json!(2222.0))), "2222");
        assert_eq!(json_to_port_string(Some(&Value::Null)), "");
        assert_eq!(json_to_port_string(None), "");
    }

    #[test]
    fn digits_to_i64_only_pure_digits() {
        assert_eq!(digits_to_i64(Some("12345")), 12345);
        assert_eq!(digits_to_i64(Some("0x1002")), 0);
        assert_eq!(digits_to_i64(Some("")), 0);
        assert_eq!(digits_to_i64(None), 0);
    }

    #[test]
    fn tail_chars_slices_code_points() {
        assert_eq!(tail_chars("hello", 3), "llo");
        assert_eq!(tail_chars("hi", 10), "hi");
        assert_eq!(tail_chars_front("hello world", 5), "hello");
    }

    #[test]
    fn cached_scan_script_comes_from_helper() {
        // The handler now delegates the scan-script construction to
        // `_cached_model_scan_script` (which emits the HF-cache + ollama + custom
        // -dir scan, including the gguf/ollama backend fields). Sanity-check the
        // shape it produces so the passthrough below has fields to surface.
        let empty: Vec<String> = Vec::new();
        let script = _cached_model_scan_script(&empty);
        assert!(script.contains("scan_hf(os.path.expanduser('~/.cache/huggingface/hub'))"));
        assert!(script.contains("scan_ollama()"));
        assert!(script.trim_end().ends_with("print(json.dumps(models))"));
        // A custom dir is appended as a quoted `scan_dir(...)` call.
        let with_dir = _cached_model_scan_script(&["/models/x".to_string()]);
        assert!(with_dir.contains("scan_dir(os.path.expanduser('/models/x'))"));
    }

    // Serialize the DATABASE_URL-mutating tests in this binary (env vars are
    // process-global; cargo runs tests in parallel threads).
    use crate::core::database::DB_TEST_LOCK as DB_ENV_LOCK;

    fn serve_req(repo_id: &str, cmd: &str) -> ServeRequest {
        ServeRequest {
            repo_id: repo_id.to_string(),
            cmd: cmd.to_string(),
            remote_host: None,
            ssh_port: None,
            env_prefix: None,
            hf_token: None,
            gpus: None,
            platform: None,
        }
    }

    #[test]
    fn auto_register_llm_endpoint_insert_update_and_tools() {
        let _g = DB_ENV_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let tmp = std::env::temp_dir().join(format!("cookbook-llm-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(&tmp).unwrap();
        let db_path = tmp.join("app.db");
        std::env::set_var("DATABASE_URL", format!("sqlite:///{}", db_path.display()));
        crate::core::database::create_all().unwrap();

        // First serve: llama.cpp on the default 8080 (no --port, no ollama, no tool
        // choice) → a fresh `local-…` endpoint with supports_tools left NULL.
        let req = serve_req("Org/My-Model", "llama-server -m model.gguf");
        let id1 = auto_register_llm_endpoint(&req, None).expect("insert");
        assert!(id1.starts_with("local-"));

        let conn = crate::core::database::session_local().unwrap();
        let (name, base_url, model_type, st): (String, String, String, Option<i64>) = conn
            .query_row(
                "SELECT name, base_url, model_type, supports_tools FROM model_endpoints WHERE id = ?1",
                rusqlite::params![id1],
                |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?)),
            )
            .unwrap();
        assert_eq!(name, "My-Model");
        assert_eq!(base_url, "http://localhost:8080/v1");
        assert_eq!(model_type, "llm");
        assert_eq!(st, None); // supports_tools NULL when not opted in
        drop(conn);

        // Second serve at the SAME base_url (same default port) but opting into
        // OpenAI tool-calling → reuses the row (no duplicate) and sets supports_tools.
        let req2 = serve_req("Org/My-Model", "llama-server -m model.gguf --enable-auto-tool-choice");
        let id2 = auto_register_llm_endpoint(&req2, None).expect("update");
        assert_eq!(id2, id1, "same base_url must reuse the endpoint");

        let conn = crate::core::database::session_local().unwrap();
        let count: i64 = conn
            .query_row("SELECT COUNT(*) FROM model_endpoints", [], |r| r.get(0))
            .unwrap();
        assert_eq!(count, 1, "no duplicate endpoint for the same base_url");
        let st2: Option<i64> = conn
            .query_row(
                "SELECT supports_tools FROM model_endpoints WHERE id = ?1",
                rusqlite::params![id1],
                |r| r.get(0),
            )
            .unwrap();
        assert_eq!(st2, Some(1)); // supports_tools now true
        drop(conn);

        // Ollama serve (no --port) lands on port 11434 with a distinct base_url.
        let req3 = serve_req("Org/My-Model", "ollama serve");
        let id3 = auto_register_llm_endpoint(&req3, None).expect("ollama insert");
        assert_ne!(id3, id1);
        let conn = crate::core::database::session_local().unwrap();
        let url3: String = conn
            .query_row(
                "SELECT base_url FROM model_endpoints WHERE id = ?1",
                rusqlite::params![id3],
                |r| r.get(0),
            )
            .unwrap();
        assert_eq!(url3, "http://localhost:11434/v1");
        drop(conn);

        std::env::remove_var("DATABASE_URL");
        let _ = std::fs::remove_dir_all(&tmp);
    }

    #[test]
    fn auto_register_llm_endpoint_explicit_port_and_remote_host() {
        let _g = DB_ENV_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let tmp = std::env::temp_dir().join(format!("cookbook-llm2-{}", uuid::Uuid::new_v4().simple()));
        std::fs::create_dir_all(&tmp).unwrap();
        let db_path = tmp.join("app.db");
        std::env::set_var("DATABASE_URL", format!("sqlite:///{}", db_path.display()));
        crate::core::database::create_all().unwrap();

        // Explicit --port wins; remote host uses the SSH alias (part after '@').
        let req = serve_req("bare-id", "vllm serve bare-id --port 9001");
        let id = auto_register_llm_endpoint(&req, Some("user@gpu-box")).expect("insert");
        let conn = crate::core::database::session_local().unwrap();
        let (name, base_url): (String, String) = conn
            .query_row(
                "SELECT name, base_url FROM model_endpoints WHERE id = ?1",
                rusqlite::params![id],
                |r| Ok((r.get(0)?, r.get(1)?)),
            )
            .unwrap();
        // repo_id without "/" is used verbatim as the display name.
        assert_eq!(name, "bare-id");
        assert_eq!(base_url, "http://gpu-box:9001/v1");
        drop(conn);

        std::env::remove_var("DATABASE_URL");
        let _ = std::fs::remove_dir_all(&tmp);
    }
}
