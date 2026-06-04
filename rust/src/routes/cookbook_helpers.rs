// routes/cookbook_helpers.rs  <- routes/cookbook_helpers.py
//! cookbook_helpers.py — validators + small helpers shared by the cookbook routes.
//!
//! Extracted from cookbook_routes.py; the routes module imports the symbols it
//! needs. These are security gates: each value lands in a shell / PowerShell /
//! tmux context, so the validators reject anything outside a tight allow-list up
//! front. A `raise HTTPException(400, …)` becomes `Err(HttpException::new(400, …))`.

use crate::pyos as os;
use crate::routes::HttpException;
use once_cell::sync::Lazy;
use regex::Regex;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::HashSet;

type R<T> = Result<T, HttpException>;

// HuggingFace repo IDs are <org>/<name>, both alphanumerics plus ._-
// Rejecting anything else up front closes off shell-interpolation vectors.
static _REPO_ID_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$").unwrap());
// Cached models scanned from a custom/local model dir are keyed by their leaf
// folder name (no slash), e.g. `DeepSeek-R1-UD-IQ4_XS`. The serve command uses
// the real on-disk path separately; this identifier is only for UI/task
// bookkeeping, so serving should accept the same safe glyph set as repo IDs.
static _LOCAL_MODEL_ID_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"^[A-Za-z0-9][A-Za-z0-9._-]*$").unwrap());
// Ollama model names include tags, e.g. `qwen2.5:0.5b` or `llama3.2:latest`.
// Some registries also use a namespace path. Keep this shell-safe: no spaces,
// quotes, `$`, `;`, `&`, pipes, or redirects.
static _OLLAMA_MODEL_ID_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,200}$").unwrap());
// Include pattern is a glob: allow typical safe glyphs only.
static _INCLUDE_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"^[A-Za-z0-9._\-*?/\[\]]+$").unwrap());
// Remote host: user@host (optionally with :port-free hostname parts).
static _REMOTE_HOST_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"^[A-Za-z0-9._-]+@[A-Za-z0-9._-]+$").unwrap());
// HF tokens and API tokens are url-safe base64-like.
static _TOKEN_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"^[A-Za-z0-9._~+/=-]+$").unwrap());
// Session IDs we mint look like "cookbook-deadbeef" or "serve-deadbeef".
#[allow(dead_code)]
static _SESSION_ID_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"^[A-Za-z0-9_-]{1,64}$").unwrap());
static _SSH_PORT_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"^\d{1,5}$").unwrap());
static _GPU_LIST_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"^\d+(?:,\d+)*$").unwrap());
// A download target directory. Absolute or ~-relative path; safe path glyphs only.
static _LOCAL_DIR_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"^~?/[A-Za-z0-9._/-]*$|^~$").unwrap());

// The shell metacharacters never allowed inside a quoted path/env-prefix.
// (CR, LF, ;, &, |, backtick, $, <, >)
const _SHELL_META: &str = "\r\n;&|`$<>";

fn has_shell_meta(s: &str) -> bool {
    s.chars().any(|c| _SHELL_META.contains(c))
}

pub fn _validate_repo_id(v: Option<&str>) -> R<String> {
    // if not v or not _REPO_ID_RE.match(v):
    match v {
        Some(v) if !v.is_empty() && _REPO_ID_RE.is_match(v) => Ok(v.to_string()),
        _ => Err(HttpException::new(
            400,
            "Invalid repo_id — must be <org>/<name> using [A-Za-z0-9._-]",
        )),
    }
}

pub fn _validate_serve_model_id(v: Option<&str>) -> R<String> {
    // if not v: raise HTTPException(400, "repo_id is required")
    let v = match v {
        Some(v) if !v.is_empty() => v,
        _ => return Err(HttpException::new(400, "repo_id is required")),
    };
    // if _REPO_ID_RE.match(v) or _LOCAL_MODEL_ID_RE.match(v) or _OLLAMA_MODEL_ID_RE.match(v):
    if _REPO_ID_RE.is_match(v) || _LOCAL_MODEL_ID_RE.is_match(v) || _OLLAMA_MODEL_ID_RE.is_match(v) {
        return Ok(v.to_string());
    }
    Err(HttpException::new(
        400,
        "Invalid repo_id — must be <org>/<name>, an Ollama name:tag, or a cached local model id",
    ))
}

pub fn _validate_include(v: Option<&str>) -> R<Option<String>> {
    // if v is None or v == "": return None
    let v = match v {
        None | Some("") => return Ok(None),
        Some(v) => v,
    };
    if !_INCLUDE_RE.is_match(v) {
        return Err(HttpException::new(400, "Invalid include pattern"));
    }
    Ok(Some(v.to_string()))
}

pub fn _validate_remote_host(v: Option<&str>) -> R<Option<String>> {
    let v = match v {
        None | Some("") => return Ok(None),
        Some(v) => v,
    };
    if !_REMOTE_HOST_RE.is_match(v) {
        return Err(HttpException::new(
            400,
            "Invalid remote_host — must be user@host, no SSH option syntax",
        ));
    }
    Ok(Some(v.to_string()))
}

pub fn _validate_token(v: Option<&str>) -> R<Option<String>> {
    let v = match v {
        None | Some("") => return Ok(None),
        Some(v) => v,
    };
    if !_TOKEN_RE.is_match(v) {
        return Err(HttpException::new(400, "Invalid token characters"));
    }
    Ok(Some(v.to_string()))
}

pub fn _validate_local_dir(v: Option<&str>) -> R<Option<String>> {
    let v = match v {
        None | Some("") => return Ok(None),
        Some(v) => v,
    };
    // v = v.rstrip("/") or "/"
    let stripped = v.trim_end_matches('/');
    let v = if stripped.is_empty() { "/" } else { stripped };
    if !_LOCAL_DIR_RE.is_match(v) {
        return Err(HttpException::new(
            400,
            "Invalid local_dir — must be an absolute or ~ path with no spaces or shell metacharacters",
        ));
    }
    Ok(Some(v.to_string()))
}

pub fn _validate_ssh_port(v: Option<&str>) -> R<Option<String>> {
    let v = match v {
        None | Some("") => return Ok(None),
        Some(v) => v,
    };
    // if not _SSH_PORT_RE.fullmatch(str(v)): raise
    if !_SSH_PORT_RE.is_match(v) {
        return Err(HttpException::new(400, "Invalid ssh_port"));
    }
    // port = int(v); if port < 1 or port > 65535: raise
    let port: i64 = v.parse().unwrap(); // regex guarantees 1-5 digits
    if !(1..=65535).contains(&port) {
        return Err(HttpException::new(400, "Invalid ssh_port"));
    }
    Ok(Some(port.to_string())) // return str(port)
}

pub fn _validate_gpus(v: Option<&str>) -> R<Option<String>> {
    let v = match v {
        None | Some("") => return Ok(None),
        Some(v) => v,
    };
    if !_GPU_LIST_RE.is_match(v) {
        return Err(HttpException::new(
            400,
            "Invalid gpus — expected comma-separated GPU indexes",
        ));
    }
    Ok(Some(v.to_string()))
}

/// Render a validated path for a double-quoted shell context, expanding a
/// leading ~ to $HOME (single quotes wouldn't expand it). Safe because
/// `_validate_local_dir` already restricts the charset.
pub fn _shell_path(p: &str) -> String {
    if p == "~" {
        return "\"$HOME\"".to_string();
    }
    if let Some(rest) = p.strip_prefix("~/") {
        return format!("\"$HOME/{rest}\"");
    }
    format!("\"{p}\"")
}

/// `repr(s)` for a Python str — CPython prefers single quotes unless the string
/// has a `'` and no `"`. Used to emit `{model_dir!r}` in the scan script.
fn py_repr(s: &str) -> String {
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

/// Build the standalone Python scanner used by /api/model/cached.
pub fn _cached_model_scan_script(model_dirs: &[String]) -> String {
    let mut lines: Vec<String> = vec![
        "import json, os, re, shutil, subprocess, urllib.request".to_string(),
        "models = []".to_string(),
        "seen = set()".to_string(),
        "BLOCKED_ROOTS = ('/sys', '/proc', '/dev', '/run', '/var/run')".to_string(),
        "def safe_path(p):".to_string(),
        "    try:".to_string(),
        "        rp = os.path.realpath(os.path.expanduser(p))".to_string(),
        "        return not any(rp == b or rp.startswith(b + os.sep) for b in BLOCKED_ROOTS)".to_string(),
        "    except Exception:".to_string(),
        "        return False".to_string(),
        "def safe_walk(top):".to_string(),
        "    if not safe_path(top): return".to_string(),
        "    for root, dirs, fns in os.walk(top, followlinks=False):".to_string(),
        "        dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(root, d)) and safe_path(os.path.join(root, d))]".to_string(),
        "        yield root, dirs, fns".to_string(),
        "def gguf_role(name):".to_string(),
        "    n = name.lower()".to_string(),
        "    if n.startswith('mmproj') or 'mmproj' in n: return 'projector'".to_string(),
        "    return 'model'".to_string(),
        "def gguf_quant(name):".to_string(),
        "    m = re.search(r'(?i)(UD-)?(IQ[0-9]_[A-Z0-9_]+|Q[0-9](?:_[A-Z0-9]+)+|BF16|F16|FP16|F32|Q8_0)', name)".to_string(),
        "    return m.group(0).upper() if m else ''".to_string(),
        "def collect_ggufs(base):".to_string(),
        "    files = []".to_string(),
        "    split_groups = {}".to_string(),
        "    if not os.path.isdir(base) or not safe_path(base): return files".to_string(),
        "    for root, dirs, fns in safe_walk(base):".to_string(),
        "        for fn in sorted(fns):".to_string(),
        "            if not fn.lower().endswith('.gguf'): continue".to_string(),
        "            fp = os.path.join(root, fn)".to_string(),
        "            try: size = os.path.getsize(fp)".to_string(),
        "            except Exception: size = 0".to_string(),
        "            try: rel = os.path.relpath(fp, base).replace(os.sep, '/')".to_string(),
        "            except Exception: rel = fn".to_string(),
        "            sm = re.match(r'(?i)^(.+)-(\\d+)-of-(\\d+)\\.gguf$', fn)".to_string(),
        "            if sm:".to_string(),
        "                prefix, part_s, total_s = sm.group(1), sm.group(2), sm.group(3)".to_string(),
        "                key = (root, prefix, total_s)".to_string(),
        "                g = split_groups.setdefault(key, {'name':fn,'rel_path':rel,'size_bytes':0,'role':gguf_role(fn),'quant':gguf_quant(fn),'parts':int(total_s),'split':True})".to_string(),
        "                g['size_bytes'] += size".to_string(),
        "                if int(part_s) == 1:".to_string(),
        "                    g.update({'name':fn,'rel_path':rel,'role':gguf_role(fn),'quant':gguf_quant(fn)})".to_string(),
        "                continue".to_string(),
        "            files.append({'name':fn,'rel_path':rel,'size_bytes':size,'role':gguf_role(fn),'quant':gguf_quant(fn)})".to_string(),
        "    files.extend(split_groups.values())".to_string(),
        "    files.sort(key=lambda f: (f.get('role') != 'model', f.get('rel_path', '')))".to_string(),
        "    return files".to_string(),
        "def scan_hf(cache):".to_string(),
        "    if not os.path.isdir(cache): return".to_string(),
        "    for d in sorted(os.listdir(cache)):".to_string(),
        "        if not d.startswith('models--'): continue".to_string(),
        "        rid = d.replace('models--','').replace('--','/')".to_string(),
        "        if rid in seen: continue".to_string(),
        "        seen.add(rid)".to_string(),
        "        blobs = os.path.join(cache, d, 'blobs')".to_string(),
        "        sz, nf, ic = 0, 0, False".to_string(),
        "        if os.path.isdir(blobs):".to_string(),
        "            for f in os.scandir(blobs):".to_string(),
        "                if f.is_file(): nf += 1; sz += f.stat().st_size".to_string(),
        "                if f.name.endswith('.incomplete'): ic = True".to_string(),
        "        snap = os.path.join(cache, d, 'snapshots')".to_string(),
        "        is_diffusion = False; gguf_files = []".to_string(),
        "        if os.path.isdir(snap):".to_string(),
        "            for sd in os.listdir(snap):".to_string(),
        "                sf = os.path.join(snap, sd)".to_string(),
        "                if not os.path.isdir(sf): continue".to_string(),
        "                if os.path.exists(os.path.join(sf, 'model_index.json')): is_diffusion = True".to_string(),
        "                for f in collect_ggufs(sf): f['rel_path'] = sd + '/' + f['rel_path']; gguf_files.append(f)".to_string(),
        "        models.append({'repo_id':rid,'size_bytes':sz,'nb_files':nf,'has_incomplete':ic,'path':cache,'is_diffusion':is_diffusion,'is_gguf':bool(gguf_files),'gguf_files':gguf_files})".to_string(),
        "def scan_dir(p):".to_string(),
        "    if not os.path.isdir(p) or not safe_path(p): return".to_string(),
        "    for d in sorted(os.listdir(p)):".to_string(),
        "        if d.startswith('.'): continue".to_string(),
        "        if d.startswith('models--'): continue".to_string(),
        "        fp = os.path.join(p, d)".to_string(),
        "        if not os.path.isdir(fp) or os.path.islink(fp) or not safe_path(fp): continue".to_string(),
        "        if d in seen: continue".to_string(),
        "        is_model = False; gguf_files = []".to_string(),
        "        for root, dirs, fns in safe_walk(fp):".to_string(),
        "            for fn in fns:".to_string(),
        "                if fn.lower().endswith('.gguf'): is_model = True".to_string(),
        "                elif fn == 'config.json' or fn.endswith('.safetensors') or fn.endswith('.bin'): is_model = True".to_string(),
        "            if is_model: break".to_string(),
        "        if not is_model: continue".to_string(),
        "        gguf_files = collect_ggufs(fp)".to_string(),
        "        seen.add(d)".to_string(),
        "        sz, nf = 0, 0".to_string(),
        "        for dp, _, fns in safe_walk(fp):".to_string(),
        "            for fn in fns:".to_string(),
        "                try: nf += 1; sz += os.path.getsize(os.path.join(dp, fn))".to_string(),
        "                except Exception: pass".to_string(),
        "        is_diff = os.path.exists(os.path.join(fp, 'model_index.json'))".to_string(),
        "        models.append({'repo_id':d,'size_bytes':sz,'nb_files':nf,'has_incomplete':False,'path':p,'is_local_dir':True,'is_diffusion':is_diff,'is_gguf':bool(gguf_files),'gguf_files':gguf_files})".to_string(),
        "def parse_size(num, unit):".to_string(),
        "    try: n = float(num)".to_string(),
        "    except Exception: return 0".to_string(),
        "    u = (unit or '').upper()".to_string(),
        "    if u.startswith('TB'): return int(n * 1024 ** 4)".to_string(),
        "    if u.startswith('GB'): return int(n * 1024 ** 3)".to_string(),
        "    if u.startswith('MB'): return int(n * 1024 ** 2)".to_string(),
        "    if u.startswith('KB'): return int(n * 1024)".to_string(),
        "    return int(n)".to_string(),
        "def scan_ollama():".to_string(),
        "    if not shutil.which('ollama'): return".to_string(),
        "    try:".to_string(),
        "        p = subprocess.run(['ollama', 'list'], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=6)".to_string(),
        "    except Exception:".to_string(),
        "        return".to_string(),
        "    if p.returncode != 0: return".to_string(),
        "    for line in (p.stdout or '').splitlines()[1:]:".to_string(),
        "        parts = line.split()".to_string(),
        "        if len(parts) < 4: continue".to_string(),
        "        name = parts[0]".to_string(),
        "        if not name or name in seen: continue".to_string(),
        "        size_bytes = parse_size(parts[2], parts[3])".to_string(),
        "        seen.add(name)".to_string(),
        "        models.append({'repo_id':name,'size_bytes':size_bytes,'nb_files':1,'has_incomplete':False,'path':'ollama','backend':'ollama','is_ollama':True})".to_string(),
        "def scan_ollama_api():".to_string(),
        "    urls = ['http://127.0.0.1:11434/api/tags', 'http://localhost:11434/api/tags', 'http://host.docker.internal:11434/api/tags']".to_string(),
        "    for url in urls:".to_string(),
        "        try:".to_string(),
        "            with urllib.request.urlopen(url, timeout=2) as r:".to_string(),
        "                data = json.loads(r.read().decode('utf-8', 'replace'))".to_string(),
        "        except Exception:".to_string(),
        "            continue".to_string(),
        "        for item in data.get('models', []):".to_string(),
        "            name = item.get('name') or item.get('model')".to_string(),
        "            if not name or name in seen: continue".to_string(),
        "            size_bytes = int(item.get('size') or item.get('size_bytes') or 0)".to_string(),
        "            seen.add(name)".to_string(),
        "            models.append({'repo_id':name,'size_bytes':size_bytes,'nb_files':1,'has_incomplete':False,'path':'ollama','backend':'ollama','is_ollama':True})".to_string(),
        "        return".to_string(),
        "scan_hf(os.path.expanduser('~/.cache/huggingface/hub'))".to_string(),
        "scan_ollama()".to_string(),
        "scan_ollama_api()".to_string(),
    ];
    // for model_dir in model_dirs or []: lines.append(f"scan_dir(os.path.expanduser({model_dir!r}))")
    for model_dir in model_dirs {
        lines.push(format!("scan_dir(os.path.expanduser({}))", py_repr(model_dir)));
    }
    lines.push("print(json.dumps(models))".to_string());
    // "\n".join(lines) + "\n"
    let mut out = lines.join("\n");
    out.push('\n');
    out
}

/// Escape a value for PowerShell single-quoted string interpolation.
pub fn _ps_squote(v: &str) -> String {
    v.replace('\'', "''")
}

/// Escape a value for bash/sh single-quoted string interpolation.
pub fn _bash_squote(v: &str) -> String {
    v.replace('\'', "'\\''")
}

/// Bash line prepending the running interpreter's bin dir to PATH.
///
/// When Odysseus runs from a virtualenv, that bin dir holds the tools the
/// cookbook runners shell out to (`hf`, `python`). tmux runners start from a
/// fresh login shell with the venv NOT activated, so without this they can't
/// find `hf` and downloads fail with "hf: command not found" — notably on
/// macOS, where the `pip --user` self-heal also misses (`pip` isn't a command,
/// only `pip3`/`python3 -m pip`). Local runs only; meaningless over SSH.
pub fn _local_tooling_path_export(executable: &str) -> String {
    // This builds a bash snippet, so an explicit POSIX absolute path should keep
    // POSIX semantics even when the app/tests run on Windows. Otherwise
    // os.path.abspath("/opt/...") would incorrectly turn it into "D:\\opt\\...".
    let bin_dir = if executable.starts_with('/') {
        // posixpath.dirname(executable)
        os::path::dirname(executable)
    } else {
        // os.path.dirname(os.path.abspath(executable))
        os::path::dirname(&abspath(executable))
    };
    // Escape for a double-quoted context: $PATH must still expand, but spaces
    // and shell metacharacters in the path must be preserved literally.
    let esc = bin_dir
        .replace('\\', "\\\\")
        .replace('"', "\\\"")
        .replace('$', "\\$")
        .replace('`', "\\`");
    format!("export PATH=\"{esc}:$PATH\"")
}

/// `os.path.abspath(p)` — normalize against the current working directory.
/// Minimal: only the join + as-is behavior the caller relies on (no `..`
/// collapsing is exercised by the executable basenames we feed it).
fn abspath(p: &str) -> String {
    if p.starts_with('/') {
        return p.to_string();
    }
    match std::env::current_dir() {
        Ok(cwd) => cwd.join(p).to_string_lossy().into_owned(),
        Err(_) => p.to_string(),
    }
}

/// Add `--no-cache-dir` to a pip install command.
///
/// Cookbook dependency installs (vLLM, llama-cpp-python, …) build large wheels;
/// pip's default cache lives under `$HOME/.cache/pip` and these builds can fill
/// a small home filesystem with `[Errno 28] No space left on device` mid-build
/// (issue #1219), leaving the dependency "installed" but unusable (#1459).
/// Disabling the cache for these one-off installs keeps them off the home disk.
/// Idempotent; leaves non-pip-install commands untouched.
pub fn _pip_install_no_cache(cmd: &str) -> String {
    // if not cmd or "pip install" not in cmd or "--no-cache-dir" in cmd: return cmd
    if cmd.is_empty() || !cmd.contains("pip install") || cmd.contains("--no-cache-dir") {
        return cmd.to_string();
    }
    // cmd.replace("pip install", "pip install --no-cache-dir", 1)
    cmd.replacen("pip install", "pip install --no-cache-dir", 1)
}

/// Wrap a single pip install command so its exit status survives the fallback
/// chain and its stderr is visible in the tmux log on failure.
///
/// Without this wrapper, `pip … 2>&1 | tail -5` returns `tail`'s exit code (0),
/// masking pip's real failure and preventing the next fallback from running.
/// The generated snippet captures all output to a temp file, prints the last 5
/// lines on failure, cleans up, and exits with pip's original status.
pub fn _pip_install_attempt(pip_cmd: &str) -> String {
    format!(
        "bash -c '_out=$(mktemp) && {pip_cmd} >\"$_out\" 2>&1; _rc=$?; \
         tail -5 \"$_out\"; rm -f \"$_out\"; exit $_rc'"
    )
}

/// Build a bash pip install fallback chain that surfaces errors.
///
/// Try the active interpreter/environment first. `--user` is invalid inside many
/// venvs, so only attempt the `--user` fallback when NOT inside a venv.
///
/// Each attempt is wrapped via [`_pip_install_attempt`] so pip's real exit code
/// is preserved (no `| tail` masking) and the last 5 lines of pip output appear
/// in the Cookbook log on failure.
pub fn _pip_install_fallback_chain(package: &str, python_cmd: &str, upgrade: bool) -> String {
    let upgrade_flag = if upgrade { " -U" } else { "" };
    // Shell-quote the package spec: an extras spec like `llama-cpp-python[server]`
    // contains brackets that bash would treat as a glob, so it must be quoted
    // before being embedded in the install command. Plain names (e.g.
    // `huggingface_hub`) are returned unchanged by shlex.quote.
    let pkg = shquote(package);
    let base = _pip_install_attempt(&format!("{python_cmd} install -q{upgrade_flag} {pkg}"));
    let user = _pip_install_attempt(&format!(
        "{python_cmd} install --user --break-system-packages -q{upgrade_flag} {pkg}"
    ));
    // Derive the python executable for the venv detection check. Must use the
    // same interpreter that pip belongs to; hardcoding python3 breaks when pip
    // lives in a venv that only has "python".
    let python_exe = if python_cmd.contains(" -m pip") {
        python_cmd.replace(" -m pip", "")
    } else if python_cmd.trim() == "pip" {
        "python".to_string()
    } else {
        // "pip3" and any other value both map to "python3".
        "python3".to_string()
    };
    let venv_check = format!(
        "{python_exe} -c \"import sys; sys.exit(0 if sys.prefix != sys.base_prefix else 1)\""
    );
    // Negated: `! venv_check` succeeds (exit 0) when NOT in a venv → `&&` tries
    // --user. When IN a venv `! venv_check` fails → `&&` skips --user and the
    // group exits non-zero, propagating the base-install failure.
    format!("{base} || {{ ! {venv_check} && {user}; }}")
}

/// Drop pip user-install flags that are invalid for local venv installs.
///
/// Cookbook dependency installs run through the model-serve task path so users
/// can watch progress in the same log UI. For local POSIX runs, that task
/// prepends Odysseus' own interpreter directory to PATH. If Odysseus itself is
/// running from a venv, `python3` resolves to the venv Python and pip rejects
/// `--user` with "User site-packages are not visible in this virtualenv".
///
/// Keep remote and non-venv installs unchanged: remotes may intentionally use
/// system Python, and Docker/non-venv installs still need user-site fallback.
pub fn _venv_safe_local_pip_install_cmd(cmd: &str, local: bool, in_venv: bool) -> String {
    if !local || !in_venv {
        return cmd.to_string();
    }
    // if "pip install" not in (cmd or ""): return cmd
    if !cmd.contains("pip install") {
        return cmd.to_string();
    }
    // try: parts = shlex.split(cmd) / except ValueError: return cmd
    let parts = match shlex::split(cmd) {
        Some(p) => p,
        None => return cmd.to_string(),
    };
    let stripped: Vec<String> = parts
        .into_iter()
        .filter(|part| part != "--user" && part != "--break-system-packages")
        .collect();
    // shlex.join(stripped) — `try_join` is the non-deprecated form; it only errors
    // on an interior NUL (impossible for these package-spec tokens), so fall back
    // to "" in that case, matching Python's never-failing `shlex.join`.
    shlex::try_join(stripped.iter().map(String::as_str)).unwrap_or_default()
}

// Allow-list of binaries permitted as the leading token of `req.cmd` for
// /api/model/serve.
static _SERVE_CMD_ALLOWLIST: Lazy<HashSet<&'static str>> = Lazy::new(|| {
    [
        "vllm", "llama-server", "llama_server", "llama.cpp", "ollama",
        "python", "python3",
        "sglang", "lmdeploy",
        "node", "npx",
    ]
    .into_iter()
    .collect()
});

// The llama.cpp GGUF launcher emits a fixed-shape prelude that resolves the
// cached .gguf on the target host before serving. That legitimately needs
// $(...)/&&/||, so we recognise this exact shape and validate the serve
// binaries it guards rather than rejecting it wholesale.
static _GGUF_PRELUDE_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"^MODEL_FILE=\$\([^\n]*?\)\s*&&\s*\{[^{}]*\}\s*\|\|\s*\{[^{}]*\}\s*&&\s*").unwrap()
});

// `(?:^|\s)OLLAMA_HOST=([^\s]+)` — find an `OLLAMA_HOST=` env assignment.
static _OLLAMA_HOST_ASSIGNMENT_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"(?:^|\s)OLLAMA_HOST=([^\s]+)").unwrap());
// `^\[([^\]]+)\]:(\d+)$|^([^:]+):(\d+)$` — bracketed [host]:port or host:port.
static _OLLAMA_BIND_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"^\[([^\]]+)\]:(\d+)$|^([^:]+):(\d+)$").unwrap());
// `^[A-Za-z0-9._:-]+$` — a shell-safe bind host.
static _OLLAMA_BIND_HOST_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"^[A-Za-z0-9._:-]+$").unwrap());

/// Return the Ollama bind host/port requested by a serve command.
///
/// Plain local `ollama serve` defaults to loopback. Remote callers can pass a
/// wider default host so the resulting API is reachable by Odysseus.
pub fn _ollama_bind_from_cmd(cmd: Option<&str>, default_host: &str) -> (String, String) {
    // if not cmd: return default_host, "11434"
    let cmd = match cmd {
        Some(c) if !c.is_empty() => c,
        _ => return (default_host.to_string(), "11434".to_string()),
    };
    // match = _OLLAMA_HOST_ASSIGNMENT_RE.search(cmd) / if not match: ...
    let cap = match _OLLAMA_HOST_ASSIGNMENT_RE.captures(cmd) {
        Some(c) => c,
        None => return (default_host.to_string(), "11434".to_string()),
    };
    // value = match.group(1).strip("'\"")
    let value = cap[1].trim_matches(|c| c == '\'' || c == '"');
    // bind_match = _OLLAMA_BIND_RE.match(value) / if not bind_match: ...
    let bind = match _OLLAMA_BIND_RE.captures(value) {
        Some(b) => b,
        None => return ("127.0.0.1".to_string(), "11434".to_string()),
    };
    // bracketed_host = bind_match.group(1)
    let bracketed_host = bind.get(1).map(|m| m.as_str());
    // host = bracketed_host or bind_match.group(3) or "127.0.0.1"
    let host = bracketed_host
        .filter(|s| !s.is_empty())
        .or_else(|| bind.get(3).map(|m| m.as_str()).filter(|s| !s.is_empty()))
        .unwrap_or("127.0.0.1");
    // port = bind_match.group(2) or bind_match.group(4) or "11434"
    let port = bind
        .get(2)
        .map(|m| m.as_str())
        .filter(|s| !s.is_empty())
        .or_else(|| bind.get(4).map(|m| m.as_str()).filter(|s| !s.is_empty()))
        .unwrap_or("11434");
    // if not _OLLAMA_BIND_HOST_RE.match(host): ...
    if !_OLLAMA_BIND_HOST_RE.is_match(host) {
        return ("127.0.0.1".to_string(), "11434".to_string());
    }
    // try: port_num = int(port, 10) / except ValueError: ...
    let port_num: i64 = match port.parse() {
        Ok(n) => n,
        Err(_) => return ("127.0.0.1".to_string(), "11434".to_string()),
    };
    if !(1..=65535).contains(&port_num) {
        return ("127.0.0.1".to_string(), "11434".to_string());
    }
    // return f"[{host}]" if bracketed_host else host, port
    let out_host = if bracketed_host.filter(|s| !s.is_empty()).is_some() {
        format!("[{host}]")
    } else {
        host.to_string()
    };
    (out_host, port.to_string())
}

// `^[A-Za-z_][A-Za-z0-9_]*=` — leading env-var assignment like `CUDA=0`.
static _ENV_ASSIGN_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"^[A-Za-z_][A-Za-z0-9_]*=").unwrap());

/// Validate that a single command segment starts with an allowlisted binary
/// (after skipping leading env-var assignments like `CUDA_VISIBLE_DEVICES=0`).
pub fn _check_serve_binary(seg: &str) -> R<()> {
    // tokens = shlex.split(seg) if seg.strip() else []
    let tokens: Vec<String> = if !seg.trim().is_empty() {
        match shlex::split(seg) {
            Some(t) => t,
            None => return Err(HttpException::new(400, "Invalid cmd — could not parse")),
        }
    } else {
        Vec::new()
    };
    // if not tokens: return
    if tokens.is_empty() {
        return Ok(());
    }
    // first = next((t for t in tokens if not env_re.match(t)), "")
    let first = tokens
        .iter()
        .find(|t| !_ENV_ASSIGN_RE.is_match(t))
        .cloned()
        .unwrap_or_default();
    // base = os.path.basename(first)
    let base = os::path::basename(&first);
    if !_SERVE_CMD_ALLOWLIST.contains(base.as_str()) {
        // sorted(_SERVE_CMD_ALLOWLIST) — Rust str Ord == Python code-point sort for ASCII.
        let mut allow: Vec<&str> = _SERVE_CMD_ALLOWLIST.iter().copied().collect();
        allow.sort_unstable();
        let base_disp = if base.is_empty() { "(empty)" } else { &base };
        return Err(HttpException::new(
            400,
            format!(
                "cmd binary '{}' is not allowed. Must start with one of: {}",
                base_disp,
                allow.join(", ")
            ),
        ));
    }
    Ok(())
}

// re.sub(r"\\[ \t]*\r?\n[ \t]*", " ", v) — collapse backslash-newline continuations.
static _LINE_CONT_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"\\[ \t]*\r?\n[ \t]*").unwrap());

/// Reject serve commands that aren't in the allowlist or contain shell metachars.
pub fn _validate_serve_cmd(v: Option<&str>) -> R<Option<String>> {
    let v = match v {
        None | Some("") => return Ok(None),
        Some(v) => v,
    };
    // Collapse backslash-newline line continuations into single spaces, then strip.
    let v = _LINE_CONT_RE.replace_all(v, " ").trim().to_string();
    // Backticks and raw newlines are never legitimate here.
    if v.contains('`') || v.contains('\n') || v.contains('\r') {
        return Err(HttpException::new(400, "Invalid characters in cmd"));
    }
    // Known GGUF launcher prelude → validate the serve invocation(s) it guards.
    if let Some(m) = _GGUF_PRELUDE_RE.find(&v) {
        let rest = &v[m.end()..];
        // rest is `[ENV=…] python3 … || [ENV=…] llama-server …`
        for part in rest.split("||") {
            _check_serve_binary(part.trim())?;
        }
        return Ok(Some(v));
    }
    // Otherwise: a single invocation — no shell metacharacters allowed.
    if v.contains(';') || v.contains("&&") || v.contains("||") || v.contains("$(") {
        return Err(HttpException::new(400, "Invalid characters in cmd"));
    }
    _check_serve_binary(&v)?;
    Ok(Some(v))
}

/// Append serve-runner lines that surface preflight failures before exit.
pub fn _append_serve_preflight_exit_lines(runner_lines: &mut Vec<String>, keep_shell_open: bool) {
    runner_lines.push("if [ -n \"$ODYSSEUS_PREFLIGHT_EXIT\" ]; then".to_string());
    runner_lines.push(
        "  echo \"\"; echo \"=== Process exited with code $ODYSSEUS_PREFLIGHT_EXIT ===\"".to_string(),
    );
    if keep_shell_open {
        runner_lines.push("  exec \"${SHELL:-/bin/bash}\"".to_string());
    } else {
        runner_lines.push("  exit \"$ODYSSEUS_PREFLIGHT_EXIT\"".to_string());
    }
    runner_lines.push("fi".to_string());
}

/// Append serve-runner lines that preserve and report the command exit code.
///
/// `is_pip_install`: when `true`, emit a `DOWNLOAD_OK` sentinel on success so
/// callers can detect a clean pip install without parsing exit codes.
pub fn _append_serve_exit_code_lines(
    runner_lines: &mut Vec<String>,
    keep_shell_open: bool,
    is_pip_install: bool,
) {
    runner_lines.push("ODYSSEUS_CMD_EXIT=$?".to_string());
    if is_pip_install {
        runner_lines
            .push("if [ $ODYSSEUS_CMD_EXIT -eq 0 ]; then echo \"\"; echo \"DOWNLOAD_OK\"; fi".to_string());
    }
    if keep_shell_open {
        runner_lines.push(
            "echo \"\"; echo \"=== Process exited with code $ODYSSEUS_CMD_EXIT ===\"; exec \"${SHELL:-/bin/bash}\""
                .to_string(),
        );
    } else {
        runner_lines.push(
            "echo \"\"; echo \"=== Process exited with code $ODYSSEUS_CMD_EXIT ===\"".to_string(),
        );
        runner_lines.push("exit \"$ODYSSEUS_CMD_EXIT\"".to_string());
    }
}

/// Append Linux llama.cpp build lines that prefer ROCm/HIP when available.
///
/// Cookbook already detects AMD GPUs elsewhere, but the llama.cpp bootstrap used
/// to hard-wire CUDA on Linux. That made ROCm hosts attempt a CUDA configure and
/// fail with "CUDA Toolkit not found" instead of building with HIP.
pub fn _append_llama_cpp_linux_accel_build_lines(runner_lines: &mut Vec<String>) {
    // Detect pip-installed nvcc (from vLLM/nvidia CUDA wheels) and put it on PATH
    // so cmake's CUDA configure can find it. We keep this after the ROCm/HIP
    // check — a machine with both stacks should honor the native HIP toolchain on
    // AMD hosts instead of accidentally preferring a stray nvcc wheel.
    runner_lines.push("    for _cudir in ~/.local/lib/python*/site-packages/nvidia/cu13 ~/.local/lib/python*/site-packages/nvidia/cu12 ~/.local/lib/python*/site-packages/nvidia/cuda_nvcc; do".to_string());
    runner_lines.push("      [ -x \"$_cudir/bin/nvcc\" ] && export CUDA_HOME=\"$_cudir\" && export PATH=\"$_cudir/bin:$PATH\" && break".to_string());
    runner_lines.push("    done".to_string());
    // rm -rf build so a prior poisoned CMakeCache.txt (e.g. from a failed CUDA
    // or HIP attempt) doesn't cause the next configure to reuse stale settings.
    runner_lines.push("    cd ~/llama.cpp && rm -rf build".to_string());
    runner_lines.push("    if command -v hipconfig &>/dev/null || [ -d /opt/rocm ] || [ -n \"$ROCM_PATH\" ] || [ -n \"$HIP_PATH\" ]; then".to_string());
    runner_lines.push("      if command -v hipconfig &>/dev/null; then".to_string());
    runner_lines.push("        export HIPCXX=\"${HIPCXX:-$(hipconfig -l)/clang}\"".to_string());
    runner_lines.push("        export HIP_PATH=\"${HIP_PATH:-$(hipconfig -R)}\"".to_string());
    runner_lines.push("      fi".to_string());
    runner_lines.push("      echo \"[odysseus] ROCm/HIP detected — building llama-server with HIP support...\"".to_string());
    runner_lines.push("      cmake -B build -DCMAKE_BUILD_TYPE=Release -DGGML_HIP=ON && cmake --build build -j\"$NPROC\" --target llama-server && ln -sf ~/llama.cpp/build/bin/llama-server ~/bin/llama-server".to_string());
    runner_lines.push("    elif command -v nvcc &>/dev/null; then".to_string());
    // nvcc alone is not sufficient — pip-installed CUDA wheels or incomplete
    // tooling can expose nvcc without shipping libcudart, causing cmake to fail
    // mid-build with "CUDA runtime library not found". Check cudart explicitly
    // via a small helper so the guard stays readable.
    runner_lines.push("      _odysseus_has_cudart() {".to_string());
    runner_lines.push("        ldconfig -p 2>/dev/null | grep -q 'libcudart\\.so' && return 0".to_string());
    runner_lines.push("        local _cuh=\"${CUDA_HOME:-/usr/local/cuda}\"".to_string());
    runner_lines.push("        ls \"$_cuh/lib64/libcudart.so\"* &>/dev/null && return 0".to_string());
    runner_lines.push("        ls \"$_cuh/lib/libcudart.so\"* &>/dev/null && return 0".to_string());
    runner_lines.push("        ls /usr/local/cuda/lib64/libcudart.so* &>/dev/null && return 0".to_string());
    runner_lines.push("        ls /usr/local/cuda/lib/libcudart.so* &>/dev/null && return 0".to_string());
    runner_lines.push("        ls \"${_cuh%/cuda_nvcc}/cuda_runtime/lib/libcudart.so\"* &>/dev/null && return 0".to_string());
    runner_lines.push("        return 1".to_string());
    runner_lines.push("      }".to_string());
    runner_lines.push("      if _odysseus_has_cudart; then".to_string());
    runner_lines.push("        echo \"[odysseus] CUDA nvcc + cudart found — building llama-server with CUDA (GPU) support...\"".to_string());
    runner_lines.push("        cmake -B build -DCMAKE_BUILD_TYPE=Release -DGGML_CUDA=ON && cmake --build build -j\"$NPROC\" --target llama-server && ln -sf ~/llama.cpp/build/bin/llama-server ~/bin/llama-server".to_string());
    runner_lines.push("      else".to_string());
    runner_lines.push("        echo \"[odysseus] WARNING: nvcc found but CUDA runtime (libcudart.so) is not visible — building llama-server for CPU only.\"".to_string());
    runner_lines.push("        echo \"[odysseus]   GPU inference will not be available for this llama.cpp build.\"".to_string());
    runner_lines.push("        echo \"[odysseus]   Ensure libcudart is installed (e.g. cuda-runtime package) and visible via ldconfig or CUDA_HOME.\"".to_string());
    runner_lines.push("        cmake -B build -DCMAKE_BUILD_TYPE=Release && cmake --build build -j\"$NPROC\" --target llama-server && ln -sf ~/llama.cpp/build/bin/llama-server ~/bin/llama-server".to_string());
    runner_lines.push("      fi".to_string());
    runner_lines.push("    else".to_string());
    runner_lines.push("      echo \"[odysseus] WARNING: no HIP/CUDA toolchain found — building llama-server for CPU only.\"".to_string());
    runner_lines.push("      echo \"[odysseus]   GPU inference will not be available for this llama.cpp build.\"".to_string());
    runner_lines.push("      echo \"[odysseus]   Install ROCm for AMD GPUs or vLLM/CUDA tooling for NVIDIA, then re-launch this serve task.\"".to_string());
    runner_lines.push("      cmake -B build -DCMAKE_BUILD_TYPE=Release && cmake --build build -j\"$NPROC\" --target llama-server && ln -sf ~/llama.cpp/build/bin/llama-server ~/bin/llama-server".to_string());
    runner_lines.push("    fi".to_string());
}

/// Shell command that clears the Cookbook-managed llama.cpp build.
///
/// Removes the cached `llama-server` symlink and the `~/llama.cpp/build`
/// directory so the next llama.cpp serve recompiles from source, picking up a
/// CUDA or HIP toolchain if one is now available. The serve bootstrap only
/// builds when `llama-server` is missing from PATH, so without this an existing
/// CPU-only build is reused forever. It deliberately installs and downloads
/// nothing; the rebuild itself happens on the next serve.
pub fn _llama_cpp_rebuild_cmd() -> String {
    concat!(
        "mkdir -p \"$HOME/bin\" && ",
        "rm -f \"$HOME/bin/llama-server\" && ",
        "rm -rf \"$HOME/llama.cpp/build\" && ",
        "echo \"[odysseus] Cleared the cached llama.cpp build. ",
        "Re-launch the serve task to rebuild llama-server from source ",
        "(CUDA or HIP will be used if a toolchain is now available).\""
    )
    .to_string()
}

/// Request model for `/api/model/download`.
#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct ModelDownloadRequest {
    pub repo_id: String,
    #[serde(default)]
    pub include: Option<String>,
    #[serde(default)]
    pub hf_token: Option<String>,
    #[serde(default)]
    pub env_prefix: Option<String>,
    #[serde(default)]
    pub remote_host: Option<String>,
    #[serde(default)]
    pub ssh_port: Option<String>,
    #[serde(default)]
    pub platform: Option<String>,
    #[serde(default)]
    pub local_dir: Option<String>,
    #[serde(default)]
    pub disable_hf_transfer: bool,
}

/// Request model for `/api/model/serve`.
#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct ServeRequest {
    pub repo_id: String,
    pub cmd: String,
    #[serde(default)]
    pub remote_host: Option<String>,
    #[serde(default)]
    pub ssh_port: Option<String>,
    #[serde(default)]
    pub env_prefix: Option<String>,
    #[serde(default)]
    pub hf_token: Option<String>,
    #[serde(default)]
    pub gpus: Option<String>,
    #[serde(default)]
    pub platform: Option<String>,
}

// --- serve-phase parsing (pure regex over a tmux snapshot) ---
static _WS_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"\s+").unwrap());
static _LOAD_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"Loading safetensors.*?(\d+)%").unwrap());
static _DOWNLOADING_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"Downloading.*?(\d+)%").unwrap());
static _FETCHING_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"Fetching.*?(\d+)%").unwrap());
static _TPS_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"(?:Avg )?generation throughput:\s*([\d.]+)\s*tokens/s.*?Running:\s*(\d+)\s*reqs")
        .unwrap()
});
static _HTTP_LOG_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r#"(?:GET|POST)\s+/[^\s]*\s+HTTP/[\d.]+"\s*\d{3}"#).unwrap());
// `re.search(r'Ollama API ready on port\s+\d+', flat, re.I)` — Ollama serve ready.
static _OLLAMA_READY_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"(?i)Ollama API ready on port\s+\d+").unwrap());

/// `re.findall(pat, s)` for a single-group pattern → the captured groups.
fn findall1(re: &Regex, s: &str) -> Vec<String> {
    re.captures_iter(s)
        .filter_map(|c| c.get(1).map(|m| m.as_str().to_string()))
        .collect()
}

/// Parse a tmux snapshot of a serve task into structured phase info.
///
/// Single source of truth for serve task status detection.
pub fn _parse_serve_phase(snapshot: &str, task_type: &str) -> Value {
    // if task_type != "serve" or not snapshot: return {}
    if task_type != "serve" || snapshot.is_empty() {
        return json!({});
    }
    // Strip newlines so tmux line-wrapping doesn't break regex matching.
    let flat = _WS_RE.replace_all(snapshot, " ").into_owned();

    let load_matches = findall1(&_LOAD_RE, &flat);
    // Prefer "Downloading" (real aggregate bytes) over "Fetching" (whole-file count).
    let downloading_matches = findall1(&_DOWNLOADING_RE, &flat);
    let fetching_matches = findall1(&_FETCHING_RE, &flat);
    let dl_matches = if !downloading_matches.is_empty() {
        downloading_matches
    } else {
        fetching_matches
    };
    // Match "Avg generation throughput: X tokens/s, Running: N reqs".
    let tps_matches: Vec<(String, String)> = _TPS_RE
        .captures_iter(&flat)
        .map(|c| (c[1].to_string(), c[2].to_string()))
        .collect();

    // Check throughput FIRST — the throughput log line contains "GPU KV cache
    // usage" which would otherwise false-match the warmup check.
    if let Some((tps_str, reqs_str)) = tps_matches.last() {
        let tps: f64 = tps_str.parse().unwrap_or(0.0);
        let reqs: i64 = reqs_str.parse().unwrap_or(0);
        return json!({
            "phase": if reqs > 0 { format!("{tps_str} tok/s") } else { "idle".to_string() },
            "status": "ready",
            "tps": tps,
            "reqs": reqs,
        });
    }
    if flat.contains("Application startup complete") {
        return json!({"phase": "ready", "status": "ready"});
    }
    if _OLLAMA_READY_RE.is_match(&flat) {
        return json!({"phase": "ready", "status": "ready"});
    }
    // HTTP access logs (e.g. GET /v1/models 200 OK) mean the server is up.
    if _HTTP_LOG_RE.is_match(&flat) {
        return json!({"phase": "idle", "status": "ready"});
    }
    if flat.contains("Loading weights took") {
        return json!({"phase": "initializing", "status": "running"});
    }
    // "GPU KV cache" alone (allocation) — not "GPU KV cache usage" (runtime log).
    if flat.contains("GPU KV cache") && !flat.contains("GPU KV cache usage") {
        return json!({"phase": "warming up", "status": "running"});
    }
    if let Some(last) = load_matches.last() {
        let pct: i64 = last.parse().unwrap_or(0);
        return json!({"phase": format!("loading {pct}%"), "status": "running", "pct": pct});
    }
    if let Some(last) = dl_matches.last() {
        let pct: i64 = last.parse().unwrap_or(0);
        return json!({"phase": format!("downloading {pct}%"), "status": "running", "pct": pct});
    }
    json!({})
}

/// Build SSH command string with optional port.
pub fn _ssh(host: &str, cmd: &str, port: Option<&str>) -> String {
    // pf = f"-p {port} " if port and port != "22" else ""
    let pf = match port {
        Some(p) if !p.is_empty() && p != "22" => format!("-p {p} "),
        _ => String::new(),
    };
    format!("ssh {pf}{host} '{cmd}'")
}

// `eval "$(conda shell.bash hook)" && conda activate ENV` — full-string match
// with one capture (\A/\z = fullmatch).
static _CONDA_EVAL_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r#"\Aeval "\$\(conda shell\.bash hook\)" && conda activate (.+)\z"#).unwrap()
});

/// shlex.quote — POSIX-safe quoting (single-quote wrapping, `'` escaping).
fn shquote(s: &str) -> String {
    shlex::try_quote(s)
        .map(|c| c.into_owned())
        .unwrap_or_else(|_| s.to_string())
}

/// Rewrite a `source <path>` env_prefix so it no-ops if the path is missing,
/// and rewrite leading `~/` → `$HOME/` so the path expands inside double quotes.
pub fn _safe_env_prefix(ep: Option<&str>) -> R<Option<String>> {
    // if not ep: return ep   (None -> None, "" -> "")
    let ep = match ep {
        None => return Ok(None),
        Some("") => return Ok(Some(String::new())),
        Some(s) => s,
    };
    // try: parts = shlex.split(ep, posix=True) / except ValueError: raise
    let parts = match shlex::split(ep) {
        Some(p) => p,
        None => return Err(HttpException::new(400, "Invalid env_prefix")),
    };

    // if len(parts) != 2 or parts[0] not in {"source", "."}:
    if parts.len() != 2 || !(parts[0] == "source" || parts[0] == ".") {
        // Bash conda activation: eval "$(conda shell.bash hook)" && conda activate ENV
        if let Some(m) = _CONDA_EVAL_RE.captures(ep) {
            let env = m.get(1).unwrap().as_str().trim();
            let env_parts = match shlex::split(env) {
                Some(p) => p,
                None => return Err(HttpException::new(400, "Invalid env_prefix")),
            };
            if env_parts.len() != 1 {
                return Err(HttpException::new(400, "Invalid env_prefix"));
            }
            return Ok(Some(format!(
                "eval \"$(conda shell.bash hook)\" && conda activate {}",
                shquote(&env_parts[0])
            )));
        }
        // Plain conda activation (Windows/PowerShell and some manual callers).
        if parts.len() == 3 && parts[0] == "conda" && parts[1] == "activate" {
            return Ok(Some(format!("conda activate {}", shquote(&parts[2]))));
        }
        // PowerShell venv activation: & 'C:\path\Scripts\Activate.ps1'
        if parts.len() == 2 && parts[0] == "&" {
            let path = &parts[1];
            if has_shell_meta(path) {
                return Err(HttpException::new(400, "Invalid env_prefix"));
            }
            return Ok(Some(format!("& '{}'", path.replace('\'', "''"))));
        }
        return Err(HttpException::new(400, "Invalid env_prefix"));
    }

    let mut path = parts[1].clone();
    if has_shell_meta(&path) {
        return Err(HttpException::new(400, "Invalid env_prefix"));
    }
    // Replace a leading "~/" with "$HOME/" so it survives quoting.
    if let Some(rest) = path.strip_prefix("~/") {
        path = format!("$HOME/{rest}");
    } else if path == "~" {
        path = "$HOME".to_string();
    }
    // path = path.replace('"', '\\"')
    let path = path.replace('"', "\\\"");
    Ok(Some(format!(
        "[ -f \"{path}\" ] && source \"{path}\" || true"
    )))
}

/// Build SSH command to run a PowerShell script on a Windows remote.
pub fn _ssh_ps(host: &str, script_path: &str, port: Option<&str>) -> String {
    let pf = match port {
        Some(p) if !p.is_empty() && p != "22" => format!("-p {p} "),
        _ => String::new(),
    };
    format!("ssh {pf}{host} \"powershell -ExecutionPolicy Bypass -File {script_path}\"")
}

// Windows session dir — stored in user's temp on the remote.
// Python value: `$env:TEMP\\odysseus-sessions` (two literal backslashes).
pub const WIN_SESSION_DIR: &str = r"$env:TEMP\\odysseus-sessions";

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn validate_serve_model_id_accepts_three_shapes() {
        // repo id (<org>/<name>)
        assert_eq!(
            _validate_serve_model_id(Some("Org/My-Model")).unwrap(),
            "Org/My-Model"
        );
        // cached local model id (no slash)
        assert_eq!(
            _validate_serve_model_id(Some("DeepSeek-R1-UD-IQ4_XS")).unwrap(),
            "DeepSeek-R1-UD-IQ4_XS"
        );
        // ollama name:tag
        assert_eq!(
            _validate_serve_model_id(Some("qwen2.5:0.5b")).unwrap(),
            "qwen2.5:0.5b"
        );
        assert_eq!(
            _validate_serve_model_id(Some("llama3.2:latest")).unwrap(),
            "llama3.2:latest"
        );
    }

    #[test]
    fn validate_serve_model_id_rejects_empty_and_unsafe() {
        let e = _validate_serve_model_id(None).unwrap_err();
        assert_eq!(e.status_code, 400);
        assert_eq!(e.detail, "repo_id is required");
        let e = _validate_serve_model_id(Some("")).unwrap_err();
        assert_eq!(e.detail, "repo_id is required");
        // shell metacharacters are rejected
        let e = _validate_serve_model_id(Some("foo; rm -rf /")).unwrap_err();
        assert_eq!(e.status_code, 400);
        assert!(e.detail.starts_with("Invalid repo_id"));
        assert!(_validate_serve_model_id(Some("$(whoami)")).is_err());
    }

    #[test]
    fn pip_install_no_cache_is_idempotent_and_targeted() {
        assert_eq!(
            _pip_install_no_cache("pip install foo"),
            "pip install --no-cache-dir foo"
        );
        // already present → unchanged
        assert_eq!(
            _pip_install_no_cache("pip install --no-cache-dir foo"),
            "pip install --no-cache-dir foo"
        );
        // not a pip install → unchanged
        assert_eq!(_pip_install_no_cache("echo hi"), "echo hi");
        assert_eq!(_pip_install_no_cache(""), "");
        // only the first occurrence is rewritten
        assert_eq!(
            _pip_install_no_cache("pip install a && pip install b"),
            "pip install --no-cache-dir a && pip install b"
        );
    }

    #[test]
    fn pip_install_attempt_wraps_and_preserves_exit() {
        let got = _pip_install_attempt("python3 -m pip install -q foo");
        assert_eq!(
            got,
            "bash -c '_out=$(mktemp) && python3 -m pip install -q foo >\"$_out\" 2>&1; \
             _rc=$?; tail -5 \"$_out\"; rm -f \"$_out\"; exit $_rc'"
        );
    }

    #[test]
    fn pip_install_fallback_chain_quotes_extras_and_derives_exe() {
        let got = _pip_install_fallback_chain("llama-cpp-python[server]", "python3 -m pip", false);
        // The extras spec must be shell-quoted before embedding.
        assert!(got.contains("'llama-cpp-python[server]'"));
        // venv-check uses the interpreter derived from the pip command.
        assert!(got.contains("python3 -c \"import sys; sys.exit(0 if sys.prefix != sys.base_prefix else 1)\""));
        // shape: base || { ! venv_check && user; }
        assert!(got.contains("|| { ! "));
        assert!(got.contains("--user --break-system-packages"));
        // upgrade flag injection
        let up = _pip_install_fallback_chain("huggingface_hub", "python3 -m pip", true);
        assert!(up.contains("install -q -U huggingface_hub"));
    }

    #[test]
    fn pip_fallback_chain_derives_exe_from_bare_pip() {
        let got = _pip_install_fallback_chain("foo", "pip", false);
        assert!(got.contains("python -c \"import sys;"));
        let got3 = _pip_install_fallback_chain("foo", "pip3", false);
        assert!(got3.contains("python3 -c \"import sys;"));
    }

    #[test]
    fn venv_safe_local_pip_strips_user_flags_only_when_local_venv() {
        let cmd = "python3 -m pip install --user --break-system-packages -q foo";
        // local + in venv → flags dropped
        assert_eq!(
            _venv_safe_local_pip_install_cmd(cmd, true, true),
            "python3 -m pip install -q foo"
        );
        // remote → unchanged
        assert_eq!(_venv_safe_local_pip_install_cmd(cmd, false, true), cmd);
        // not in venv → unchanged
        assert_eq!(_venv_safe_local_pip_install_cmd(cmd, true, false), cmd);
        // not a pip install → unchanged
        assert_eq!(
            _venv_safe_local_pip_install_cmd("echo hi", true, true),
            "echo hi"
        );
    }

    #[test]
    fn local_tooling_path_export_keeps_posix_for_absolute() {
        // absolute → posix dirname, no abspath rewrite
        assert_eq!(
            _local_tooling_path_export("/opt/venv/bin/python3"),
            "export PATH=\"/opt/venv/bin:$PATH\""
        );
        // metachars in the dir are escaped for the double-quoted context.
        assert_eq!(
            _local_tooling_path_export("/opt/v$x/bin/hf"),
            "export PATH=\"/opt/v\\$x/bin:$PATH\""
        );
    }

    #[test]
    fn cached_model_scan_script_shape_and_per_dir_lines() {
        let script = _cached_model_scan_script(&[]);
        // New preamble imports + ollama scanners.
        assert!(script.starts_with("import json, os, re, shutil, subprocess, urllib.request\n"));
        assert!(script.contains("def scan_ollama():"));
        assert!(script.contains("def scan_ollama_api():"));
        assert!(script.contains("def collect_ggufs(base):"));
        assert!(script.contains("def gguf_quant(name):"));
        // Always-run scanners + final print, no per-dir lines.
        assert!(script.contains("scan_hf(os.path.expanduser('~/.cache/huggingface/hub'))\n"));
        assert!(script.contains("scan_ollama()\n"));
        assert!(script.contains("scan_ollama_api()\n"));
        assert!(script.ends_with("print(json.dumps(models))\n"));
        assert!(!script.contains("scan_dir(os.path.expanduser("));

        // Per-dir lines use repr() and slot between the ollama scanners and print.
        let with_dirs = _cached_model_scan_script(&["/models/x".to_string()]);
        assert!(with_dirs.contains("scan_dir(os.path.expanduser('/models/x'))\n"));
        assert!(with_dirs.ends_with("print(json.dumps(models))\n"));
    }

    #[test]
    fn ollama_bind_from_cmd_defaults_and_parses() {
        // No cmd → default host + 11434
        assert_eq!(
            _ollama_bind_from_cmd(None, "0.0.0.0"),
            ("0.0.0.0".to_string(), "11434".to_string())
        );
        // No OLLAMA_HOST assignment → default host kept
        assert_eq!(
            _ollama_bind_from_cmd(Some("ollama serve"), "0.0.0.0"),
            ("0.0.0.0".to_string(), "11434".to_string())
        );
        // host:port
        assert_eq!(
            _ollama_bind_from_cmd(Some("OLLAMA_HOST=0.0.0.0:11500 ollama serve"), "127.0.0.1"),
            ("0.0.0.0".to_string(), "11500".to_string())
        );
        // bracketed [host]:port
        assert_eq!(
            _ollama_bind_from_cmd(Some("OLLAMA_HOST=[::1]:11434 ollama serve"), "127.0.0.1"),
            ("[::1]".to_string(), "11434".to_string())
        );
        // quotes stripped from the value
        assert_eq!(
            _ollama_bind_from_cmd(Some("OLLAMA_HOST='1.2.3.4:9999' ollama serve"), "127.0.0.1"),
            ("1.2.3.4".to_string(), "9999".to_string())
        );
        // out-of-range port → loopback fallback
        assert_eq!(
            _ollama_bind_from_cmd(Some("OLLAMA_HOST=0.0.0.0:99999 ollama serve"), "0.0.0.0"),
            ("127.0.0.1".to_string(), "11434".to_string())
        );
    }

    #[test]
    fn parse_serve_phase_detects_ollama_ready() {
        let v = _parse_serve_phase("listening... Ollama API ready on port 11434", "serve");
        assert_eq!(v["phase"], "ready");
        assert_eq!(v["status"], "ready");
        // case-insensitive
        let v2 = _parse_serve_phase("ollama api READY ON PORT 11500", "serve");
        assert_eq!(v2["status"], "ready");
    }

    #[test]
    fn serve_preflight_exit_lines_keep_open_vs_exit() {
        let mut lines: Vec<String> = Vec::new();
        _append_serve_preflight_exit_lines(&mut lines, true);
        assert_eq!(lines[0], "if [ -n \"$ODYSSEUS_PREFLIGHT_EXIT\" ]; then");
        assert_eq!(lines[2], "  exec \"${SHELL:-/bin/bash}\"");
        assert_eq!(lines[3], "fi");

        let mut lines2: Vec<String> = Vec::new();
        _append_serve_preflight_exit_lines(&mut lines2, false);
        assert_eq!(lines2[2], "  exit \"$ODYSSEUS_PREFLIGHT_EXIT\"");
    }

    #[test]
    fn serve_exit_code_lines_keep_open_vs_exit() {
        let mut lines: Vec<String> = Vec::new();
        _append_serve_exit_code_lines(&mut lines, true, false);
        assert_eq!(lines[0], "ODYSSEUS_CMD_EXIT=$?");
        assert!(lines[1].ends_with("exec \"${SHELL:-/bin/bash}\""));

        let mut lines2: Vec<String> = Vec::new();
        _append_serve_exit_code_lines(&mut lines2, false, false);
        assert_eq!(lines2[0], "ODYSSEUS_CMD_EXIT=$?");
        assert_eq!(lines2[2], "exit \"$ODYSSEUS_CMD_EXIT\"");
    }

    #[test]
    fn serve_exit_code_lines_pip_install_emits_download_ok_sentinel() {
        // is_pip_install=true → DOWNLOAD_OK sentinel inserted after exit-capture,
        // before the keep-shell-open / exit logic.
        let mut lines: Vec<String> = Vec::new();
        _append_serve_exit_code_lines(&mut lines, false, true);
        assert_eq!(lines[0], "ODYSSEUS_CMD_EXIT=$?");
        assert_eq!(
            lines[1],
            "if [ $ODYSSEUS_CMD_EXIT -eq 0 ]; then echo \"\"; echo \"DOWNLOAD_OK\"; fi"
        );
        // exit-code report and explicit exit still follow.
        assert_eq!(lines[3], "exit \"$ODYSSEUS_CMD_EXIT\"");

        // keep_shell_open variant with is_pip_install
        let mut lines2: Vec<String> = Vec::new();
        _append_serve_exit_code_lines(&mut lines2, true, true);
        assert_eq!(lines2[1],
            "if [ $ODYSSEUS_CMD_EXIT -eq 0 ]; then echo \"\"; echo \"DOWNLOAD_OK\"; fi");
        assert!(lines2[2].ends_with("exec \"${SHELL:-/bin/bash}\""));

        // is_pip_install=false → no DOWNLOAD_OK line.
        let mut lines3: Vec<String> = Vec::new();
        _append_serve_exit_code_lines(&mut lines3, false, false);
        assert!(!lines3.iter().any(|l| l.contains("DOWNLOAD_OK")));
    }

    #[test]
    fn llama_cpp_accel_build_lines_cover_rocm_cuda_and_cpu() {
        let mut lines: Vec<String> = Vec::new();
        _append_llama_cpp_linux_accel_build_lines(&mut lines);
        let joined = lines.join("\n");
        // ROCm/HIP branch
        assert!(joined.contains("ROCm/HIP detected"));
        assert!(joined.contains("-DGGML_HIP=ON"));
        // CUDA branch with cudart guard
        assert!(joined.contains("_odysseus_has_cudart()"));
        assert!(joined.contains("-DGGML_CUDA=ON"));
        // CPU-only fallback warnings
        assert!(joined.contains("no HIP/CUDA toolchain found"));
        // wipes a poisoned build dir first
        assert!(joined.contains("cd ~/llama.cpp && rm -rf build"));
    }

    #[test]
    fn llama_cpp_rebuild_cmd_clears_build() {
        let cmd = _llama_cpp_rebuild_cmd();
        assert!(cmd.starts_with("mkdir -p \"$HOME/bin\" && "));
        assert!(cmd.contains("rm -f \"$HOME/bin/llama-server\""));
        assert!(cmd.contains("rm -rf \"$HOME/llama.cpp/build\""));
        assert!(cmd.contains("Cleared the cached llama.cpp build."));
        assert!(cmd.ends_with("(CUDA or HIP will be used if a toolchain is now available).\""));
    }
}
