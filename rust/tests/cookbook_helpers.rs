//! Ported verbatim from the Python `tests/test_cookbook_helpers.py`
//! (the real one — shell-security validators), plus a few extra cases over the
//! other translated helpers in routes/cookbook_helpers.py.

use odysseus::routes::cookbook_helpers::{
    _parse_serve_phase, _safe_env_prefix, _validate_gpus, _validate_repo_id, _validate_serve_cmd,
    _validate_ssh_port,
};
use serde_json::json;

// ---- the four ported tests ----

#[test]
fn safe_env_prefix_accepts_quoted_venv_path() {
    assert_eq!(
        _safe_env_prefix(Some("source '~/vllm-env/bin/activate'")).unwrap(),
        Some(
            "[ -f \"$HOME/vllm-env/bin/activate\" ] && source \"$HOME/vllm-env/bin/activate\" || true"
                .to_string()
        )
    );
}

#[test]
fn safe_env_prefix_leaves_compound_conda_prefix_unchanged() {
    let prefix = "eval \"$(conda shell.bash hook)\" && conda activate qwen35";
    assert_eq!(_safe_env_prefix(Some(prefix)).unwrap(), Some(prefix.to_string()));
}

#[test]
fn safe_env_prefix_rejects_freeform_shell() {
    assert!(_safe_env_prefix(Some("echo ok; curl https://example.invalid")).is_err());
}

#[test]
fn safe_env_prefix_accepts_powershell_activation_path() {
    // Note: the test source string is `& 'C:\Users\me\venv\Scripts\Activate.ps1'`
    // (single backslashes after Rust unescaping of `\\`).
    let input = "& 'C:\\Users\\me\\venv\\Scripts\\Activate.ps1'";
    assert_eq!(_safe_env_prefix(Some(input)).unwrap(), Some(input.to_string()));
}

#[test]
fn validate_ssh_port_rejects_shell_payload() {
    assert!(_validate_ssh_port(Some("22; touch /tmp/pwned")).is_err());
    assert_eq!(_validate_ssh_port(Some("2222")).unwrap(), Some("2222".to_string()));
}

#[test]
fn validate_gpus_accepts_indexes_only() {
    assert_eq!(_validate_gpus(Some("0,1,2")).unwrap(), Some("0,1,2".to_string()));
    assert!(_validate_gpus(Some("0; rm -rf /")).is_err());
}

// ---- extra coverage over the other translated validators ----

#[test]
fn validate_ssh_port_range_and_empty() {
    assert_eq!(_validate_ssh_port(None).unwrap(), None);
    assert_eq!(_validate_ssh_port(Some("")).unwrap(), None);
    assert!(_validate_ssh_port(Some("0")).is_err()); // < 1
    assert!(_validate_ssh_port(Some("70000")).is_err()); // 5 digits but > 65535
    assert!(_validate_ssh_port(Some("123456")).is_err()); // 6 digits
    assert_eq!(_validate_ssh_port(Some("22")).unwrap(), Some("22".to_string()));
}

#[test]
fn validate_repo_id_cases() {
    assert_eq!(
        _validate_repo_id(Some("meta-llama/Llama-2-7b")).unwrap(),
        "meta-llama/Llama-2-7b"
    );
    assert!(_validate_repo_id(None).is_err());
    assert!(_validate_repo_id(Some("")).is_err());
    assert!(_validate_repo_id(Some("noslash")).is_err());
    assert!(_validate_repo_id(Some("a/b/c")).is_err()); // second slash breaks the pattern
    assert!(_validate_repo_id(Some("org/name; rm -rf /")).is_err());
}

#[test]
fn validate_serve_cmd_cases() {
    // Allowlisted binary passes through (trimmed).
    assert_eq!(
        _validate_serve_cmd(Some("vllm serve my-model --port 8000")).unwrap(),
        Some("vllm serve my-model --port 8000".to_string())
    );
    // Leading env-var assignment is skipped before the allowlist check.
    assert!(
        _validate_serve_cmd(Some("CUDA_VISIBLE_DEVICES=0 python3 -m vllm.entrypoints.api_server"))
            .unwrap()
            .unwrap()
            .contains("python3")
    );
    // Non-allowlisted binary is rejected.
    assert!(_validate_serve_cmd(Some("curl http://evil")).is_err());
    // Shell metacharacters rejected.
    assert!(_validate_serve_cmd(Some("vllm serve x; rm -rf /")).is_err());
    assert!(_validate_serve_cmd(Some("vllm serve x && curl evil")).is_err());
    assert!(_validate_serve_cmd(Some("vllm serve `whoami`")).is_err());
    // Empty / None -> None.
    assert_eq!(_validate_serve_cmd(None).unwrap(), None);
    assert_eq!(_validate_serve_cmd(Some("")).unwrap(), None);
    // Backslash-newline continuation is collapsed and still governed by the
    // allowlist. The regex `\\[ \t]*\r?\n[ \t]*` consumes the `\`, the newline,
    // and the two trailing spaces -> one space; the space BEFORE the `\` stays,
    // so the result has a double space (byte-identical to CPython's re.sub).
    assert_eq!(
        _validate_serve_cmd(Some("vllm serve x \\\n  --port 8000")).unwrap(),
        Some("vllm serve x  --port 8000".to_string())
    );
}

#[test]
fn parse_serve_phase_cases() {
    // Non-serve task or empty snapshot -> {}.
    assert_eq!(_parse_serve_phase("anything", "download"), json!({}));
    assert_eq!(_parse_serve_phase("", "serve"), json!({}));

    // Throughput line -> ready with tps/reqs.
    let snap = "INFO Avg generation throughput: 42.5 tokens/s, Running: 3 reqs, GPU KV cache usage: 1%";
    let v = _parse_serve_phase(snap, "serve");
    assert_eq!(v["status"], json!("ready"));
    assert_eq!(v["phase"], json!("42.5 tok/s"));
    assert_eq!(v["reqs"], json!(3));

    // Startup complete -> ready.
    assert_eq!(
        _parse_serve_phase("INFO: Application startup complete.", "serve"),
        json!({"phase": "ready", "status": "ready"})
    );

    // Loading safetensors percentage -> running with pct.
    let v2 = _parse_serve_phase("Loading safetensors checkpoint shards: 60% done", "serve");
    assert_eq!(v2["status"], json!("running"));
    assert_eq!(v2["pct"], json!(60));
    assert_eq!(v2["phase"], json!("loading 60%"));

    // GPU KV cache (allocation, not "usage") -> warming up.
    assert_eq!(
        _parse_serve_phase("Allocating GPU KV cache blocks", "serve"),
        json!({"phase": "warming up", "status": "running"})
    );
}
