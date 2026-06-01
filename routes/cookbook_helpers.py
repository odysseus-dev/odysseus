"""cookbook_helpers.py — validators + small helpers shared by the cookbook routes.
Extracted from cookbook_routes.py; the routes module imports the symbols it needs."""

import logging
import os
import posixpath
import re
import shlex

from fastapi import HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)


# HuggingFace repo IDs are <org>/<name>, both alphanumerics plus ._-
# Rejecting anything else up front closes off shell-interpolation vectors.
_REPO_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$")
# Cached models scanned from a custom/local model dir are keyed by their leaf
# folder name (no slash), e.g. `DeepSeek-R1-UD-IQ4_XS`. The serve command uses
# the real on-disk path separately; this identifier is only for UI/task
# bookkeeping, so serving should accept the same safe glyph set as repo IDs.
_LOCAL_MODEL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
# Include pattern is a glob: allow typical safe glyphs only.
_INCLUDE_RE = re.compile(r"^[A-Za-z0-9._\-*?/\[\]]+$")
# Remote host: user@host (optionally with :port-free hostname parts).
_REMOTE_HOST_RE = re.compile(r"^[A-Za-z0-9._-]+@[A-Za-z0-9._-]+$")
# HF tokens and API tokens are url-safe base64-like.
_TOKEN_RE = re.compile(r"^[A-Za-z0-9._~+/=-]+$")
# Session IDs we mint look like "cookbook-deadbeef" or "serve-deadbeef".
# Anything beyond plain alphanumerics + dash + underscore could break out
# of the shell/PowerShell contexts the value lands in.
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_SSH_PORT_RE = re.compile(r"^\d{1,5}$")
_GPU_LIST_RE = re.compile(r"^\d+(?:,\d+)*$")
# A download target directory. Absolute or ~-relative path; safe path glyphs
# only (no quotes, shell metacharacters, or spaces) since it lands in a shell
# command. A leading ~ is expanded to $HOME at command-build time.
_LOCAL_DIR_RE = re.compile(r"^~?/[A-Za-z0-9._/-]*$|^~$")


def _validate_repo_id(v: str | None) -> str:
    if not v or not _REPO_ID_RE.match(v):
        raise HTTPException(400, "Invalid repo_id — must be <org>/<name> using [A-Za-z0-9._-]")
    return v


def _validate_serve_model_id(v: str | None) -> str:
    if not v:
        raise HTTPException(400, "repo_id is required")
    if _REPO_ID_RE.match(v) or _LOCAL_MODEL_ID_RE.match(v):
        return v
    raise HTTPException(400, "Invalid repo_id — must be <org>/<name> or a cached local model id using [A-Za-z0-9._-]")


def _validate_include(v: str | None) -> str | None:
    if v is None or v == "":
        return None
    if not _INCLUDE_RE.match(v):
        raise HTTPException(400, "Invalid include pattern")
    return v


def _validate_remote_host(v: str | None) -> str | None:
    if v is None or v == "":
        return None
    if not _REMOTE_HOST_RE.match(v):
        raise HTTPException(400, "Invalid remote_host — must be user@host, no SSH option syntax")
    return v


def _validate_token(v: str | None) -> str | None:
    if v is None or v == "":
        return None
    if not _TOKEN_RE.match(v):
        raise HTTPException(400, "Invalid token characters")
    return v


def _validate_local_dir(v: str | None) -> str | None:
    if v is None or v == "":
        return None
    v = v.rstrip("/") or "/"
    if not _LOCAL_DIR_RE.match(v):
        raise HTTPException(400, "Invalid local_dir — must be an absolute or ~ path with no spaces or shell metacharacters")
    return v


def _validate_ssh_port(v: str | None) -> str | None:
    if v is None or v == "":
        return None
    if not _SSH_PORT_RE.fullmatch(str(v)):
        raise HTTPException(400, "Invalid ssh_port")
    port = int(v)
    if port < 1 or port > 65535:
        raise HTTPException(400, "Invalid ssh_port")
    return str(port)


def _validate_gpus(v: str | None) -> str | None:
    if v is None or v == "":
        return None
    if not _GPU_LIST_RE.fullmatch(str(v)):
        raise HTTPException(400, "Invalid gpus — expected comma-separated GPU indexes")
    return str(v)


def _shell_path(p: str) -> str:
    """Render a validated path for a double-quoted shell context, expanding a
    leading ~ to $HOME (single quotes wouldn't expand it). Safe because
    _validate_local_dir already restricts the charset."""
    if p == "~":
        return '"$HOME"'
    if p.startswith("~/"):
        return '"$HOME/' + p[2:] + '"'
    return '"' + p + '"'


def _local_tooling_path_export(executable: str) -> str:
    """Bash line prepending the running interpreter's bin dir to PATH.

    When Odysseus runs from a virtualenv, that bin dir holds the tools the
    cookbook runners shell out to (`hf`, `python`). tmux runners start from a
    fresh login shell with the venv NOT activated, so without this they can't
    find `hf` and downloads fail with "hf: command not found" — notably on
    macOS, where the `pip --user` self-heal also misses (`pip` isn't a command,
    only `pip3`/`python3 -m pip`). Local runs only; meaningless over SSH.
    """
    # This builds a bash snippet, so an explicit POSIX absolute path should keep
    # POSIX semantics even when the app/tests run on Windows. Otherwise
    # os.path.abspath("/opt/...") would incorrectly turn it into "D:\\opt\\...".
    if executable.startswith("/"):
        bin_dir = posixpath.dirname(executable)
    else:
        bin_dir = os.path.dirname(os.path.abspath(executable))
    # Escape for a double-quoted context: $PATH must still expand, but spaces
    # and shell metacharacters in the path must be preserved literally.
    esc = (
        bin_dir.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("$", "\\$")
        .replace("`", "\\`")
    )
    return f'export PATH="{esc}:$PATH"'


def _cached_model_scan_script(model_dirs: list[str] | None = None) -> str:
    """Build the standalone Python scanner used by /api/model/cached."""
    lines = [
        "import json, os",
        "models = []",
        "seen = set()",
        "BLOCKED_ROOTS = ('/sys', '/proc', '/dev', '/run', '/var/run')",
        "def safe_path(p):",
        "    try:",
        "        rp = os.path.realpath(os.path.expanduser(p))",
        "        return not any(rp == b or rp.startswith(b + os.sep) for b in BLOCKED_ROOTS)",
        "    except Exception:",
        "        return False",
        "def safe_walk(top):",
        "    if not safe_path(top): return",
        "    for root, dirs, fns in os.walk(top, followlinks=False):",
        "        dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(root, d)) and safe_path(os.path.join(root, d))]",
        "        yield root, dirs, fns",
        "def scan_hf(cache):",
        "    if not os.path.isdir(cache): return",
        "    for d in sorted(os.listdir(cache)):",
        "        if not d.startswith('models--'): continue",
        "        rid = d.replace('models--','').replace('--','/')",
        "        if rid in seen: continue",
        "        seen.add(rid)",
        "        blobs = os.path.join(cache, d, 'blobs')",
        "        sz, nf, ic = 0, 0, False",
        "        if os.path.isdir(blobs):",
        "            for f in os.scandir(blobs):",
        "                if f.is_file(): nf += 1; sz += f.stat().st_size",
        "                if f.name.endswith('.incomplete'): ic = True",
        "        snap = os.path.join(cache, d, 'snapshots')",
        "        is_diffusion = False; is_gguf = False",
        "        if os.path.isdir(snap):",
        "            for sd in os.listdir(snap):",
        "                sf = os.path.join(snap, sd)",
        "                if not os.path.isdir(sf): continue",
        "                if os.path.exists(os.path.join(sf, 'model_index.json')): is_diffusion = True",
        "                try:",
        "                    if any(x.endswith('.gguf') for x in os.listdir(sf)): is_gguf = True",
        "                except Exception: pass",
        "        models.append({'repo_id':rid,'size_bytes':sz,'nb_files':nf,'has_incomplete':ic,'path':cache,'is_diffusion':is_diffusion,'is_gguf':is_gguf})",
        "def scan_dir(p):",
        "    if not os.path.isdir(p) or not safe_path(p): return",
        "    for d in sorted(os.listdir(p)):",
        "        if d.startswith('.'): continue",
        "        if d.startswith('models--'): continue",
        "        fp = os.path.join(p, d)",
        "        if not os.path.isdir(fp) or os.path.islink(fp) or not safe_path(fp): continue",
        "        if d in seen: continue",
        "        is_model = False; is_gguf = False",
        "        for root, dirs, fns in safe_walk(fp):",
        "            for fn in fns:",
        "                if fn.endswith('.gguf'): is_gguf = True; is_model = True",
        "                elif fn == 'config.json' or fn.endswith('.safetensors') or fn.endswith('.bin'): is_model = True",
        "            if is_model: break",
        "        if not is_model: continue",
        "        seen.add(d)",
        "        sz, nf = 0, 0",
        "        for dp, _, fns in safe_walk(fp):",
        "            for fn in fns:",
        "                try: nf += 1; sz += os.path.getsize(os.path.join(dp, fn))",
        "                except Exception: pass",
        "        is_diff = os.path.exists(os.path.join(fp, 'model_index.json'))",
        "        models.append({'repo_id':d,'size_bytes':sz,'nb_files':nf,'has_incomplete':False,'path':p,'is_local_dir':True,'is_diffusion':is_diff,'is_gguf':is_gguf})",
        "scan_hf(os.path.expanduser('~/.cache/huggingface/hub'))",
    ]
    for model_dir in model_dirs or []:
        lines.append(f"scan_dir(os.path.expanduser({model_dir!r}))")
    lines.append("print(json.dumps(models))")
    return "\n".join(lines) + "\n"


def _ps_squote(v: str) -> str:
    """Escape a value for PowerShell single-quoted string interpolation.
    Belt-and-suspenders on top of _validate_token's regex — if the regex
    is ever loosened, this still keeps the heredoc shell-safe."""
    return v.replace("'", "''")


def _bash_squote(v: str) -> str:
    """Escape a value for bash/sh single-quoted string interpolation."""
    return v.replace("'", "'\\''")


# Allow-list of binaries permitted as the leading token of `req.cmd` for /api/model/serve.
# Anything else is rejected before the cmd is interpolated into a tmux/PowerShell wrapper.
_SERVE_CMD_ALLOWLIST = {
    "vllm", "llama-server", "llama_server", "llama.cpp", "ollama",
    "python", "python3",
    "sglang", "lmdeploy",
    "node", "npx",
}


# The llama.cpp GGUF launcher (static/js/cookbook.js) emits a fixed-shape
# prelude that resolves the cached .gguf on the target host before serving:
#   MODEL_FILE=$( { find …; find …; } | head -1 ) && { [ -n "$MODEL_FILE" ] && \
#   [ -f "$MODEL_FILE" ]; } || { echo "ERROR…"; exit 1; } && <serve> || <serve>
# That legitimately needs $(...)/&&/||, so we recognise this exact shape and
# validate the serve binaries it guards rather than rejecting it wholesale.
_GGUF_PRELUDE_RE = re.compile(
    r'^MODEL_FILE=\$\([^\n]*?\)\s*&&\s*\{[^{}]*\}\s*\|\|\s*\{[^{}]*\}\s*&&\s*'
)


def _check_serve_binary(seg: str) -> None:
    """Validate that a single command segment starts with an allowlisted binary
    (after skipping leading env-var assignments like `CUDA_VISIBLE_DEVICES=0`)."""
    try:
        tokens = shlex.split(seg) if seg.strip() else []
    except ValueError:
        raise HTTPException(400, "Invalid cmd — could not parse")
    if not tokens:
        return
    env_re = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
    first = next((t for t in tokens if not env_re.match(t)), "")
    base = os.path.basename(first)
    if base not in _SERVE_CMD_ALLOWLIST:
        raise HTTPException(
            400,
            f"cmd binary '{base or '(empty)'}' is not allowed. Must start with one of: "
            f"{', '.join(sorted(_SERVE_CMD_ALLOWLIST))}",
        )


def _validate_serve_cmd(v: str | None) -> str | None:
    """Reject serve commands that aren't in the allowlist or contain shell metachars.

    `req.cmd` is dropped verbatim into a bash/PowerShell wrapper script and
    executed in a tmux session. Without this gate, an admin (or anyone in the
    pre-fix world) could pass arbitrary shell payloads.

    Leading env-var assignments (e.g. `CUDA_VISIBLE_DEVICES=0 python3 ...`)
    are stripped before checking the binary — several of our cmd builders
    prepend them, and they shouldn't trip the allowlist.
    """
    if v is None or v == "":
        return None
    # Collapse backslash-newline line continuations into single spaces. Serve
    # commands (vLLM especially) are routinely pasted multi-line with trailing
    # `\` — that's a safe shell/shlex continuation, so the command stays ONE
    # logical invocation and the leading-token allowlist below still governs.
    v = re.sub(r"\\[ \t]*\r?\n[ \t]*", " ", v).strip()
    # Backticks and raw newlines are never legitimate here.
    if any(c in v for c in ("`", "\n", "\r")):
        raise HTTPException(400, "Invalid characters in cmd")
    # Known GGUF launcher prelude → validate the serve invocation(s) it guards.
    m = _GGUF_PRELUDE_RE.match(v)
    if m:
        rest = v[m.end():]
        # rest is `[ENV=…] python3 -m llama_cpp.server … || [ENV=…] llama-server …`
        for part in rest.split("||"):
            _check_serve_binary(part.strip())
        return v
    # Otherwise: a single invocation — no shell metacharacters allowed.
    # (`$(` was the original intent; bare `$` is fine for shell-safe paths.)
    if any(c in v for c in (";", "&&", "||", "$(")):
        raise HTTPException(400, "Invalid characters in cmd")
    _check_serve_binary(v)
    return v


def _append_serve_preflight_exit_lines(runner_lines: list[str], *, keep_shell_open: bool) -> None:
    """Append serve-runner lines that surface preflight failures before exit."""
    runner_lines.append('if [ -n "$ODYSSEUS_PREFLIGHT_EXIT" ]; then')
    runner_lines.append('  echo ""; echo "=== Process exited with code $ODYSSEUS_PREFLIGHT_EXIT ==="')
    if keep_shell_open:
        runner_lines.append('  exec "${SHELL:-/bin/bash}"')
    else:
        runner_lines.append('  exit "$ODYSSEUS_PREFLIGHT_EXIT"')
    runner_lines.append('fi')


def _append_serve_exit_code_lines(runner_lines: list[str], *, keep_shell_open: bool) -> None:
    """Append serve-runner lines that preserve and report the command exit code."""
    runner_lines.append('ODYSSEUS_CMD_EXIT=$?')
    if keep_shell_open:
        runner_lines.append('echo ""; echo "=== Process exited with code $ODYSSEUS_CMD_EXIT ==="; exec "${SHELL:-/bin/bash}"')
    else:
        runner_lines.append('echo ""; echo "=== Process exited with code $ODYSSEUS_CMD_EXIT ==="')


class ModelDownloadRequest(BaseModel):
    repo_id: str
    include: str | None = None  # glob pattern e.g. "*Q4_K_M*"
    hf_token: str | None = None
    env_prefix: str | None = None  # e.g. "source ~/venv/bin/activate"
    remote_host: str | None = None  # e.g. "gpu-box" — run download on this host via SSH
    ssh_port: str | None = None    # e.g. "8022" for Termux
    platform: str | None = None    # "linux", "termux", or "windows"
    local_dir: str | None = None   # base dir to download into (a per-model subfolder is created under it); None = default HF cache
    disable_hf_transfer: bool = False  # skip the Rust hf_transfer downloader — slower but far more reliable on large files (used by retries)


class ServeRequest(BaseModel):
    repo_id: str
    cmd: str
    remote_host: str | None = None
    ssh_port: str | None = None
    env_prefix: str | None = None
    hf_token: str | None = None
    gpus: str | None = None
    platform: str | None = None    # "linux", "termux", or "windows"


def _parse_serve_phase(snapshot: str, task_type: str = "serve") -> dict:
    """Parse a tmux snapshot of a serve task into structured phase info.

    Single source of truth for serve task status detection. Returns:
        { "phase": str, "status": "ready"|"running"|"", "tps": float|None,
          "reqs": int|None, "pct": int|None }
    """
    import re
    if task_type != "serve" or not snapshot:
        return {}
    # Strip newlines so tmux line-wrapping doesn't break regex matching
    flat = re.sub(r'\s+', ' ', snapshot)

    load_matches = re.findall(r'Loading safetensors.*?(\d+)%', flat)
    # Prefer "Downloading (incomplete total...)" (real aggregate bytes) over
    # "Fetching N files" (whole-file count, lags with hf_transfer's chunked pulls).
    downloading_matches = re.findall(r'Downloading.*?(\d+)%', flat)
    fetching_matches = re.findall(r'Fetching.*?(\d+)%', flat)
    dl_matches = downloading_matches if downloading_matches else fetching_matches
    # Match "Avg generation throughput: X tokens/s, Running: N reqs" (with line-wrap tolerance)
    tps_matches = re.findall(
        r'(?:Avg )?generation throughput:\s*([\d.]+)\s*tokens/s.*?Running:\s*(\d+)\s*reqs',
        flat,
    )

    # Check throughput FIRST — the throughput log line contains "GPU KV cache usage"
    # which would otherwise false-match the warmup check
    if tps_matches:
        tps_str, reqs_str = tps_matches[-1]
        tps = float(tps_str)
        reqs = int(reqs_str)
        return {
            "phase": f"{tps_str} tok/s" if reqs > 0 else "idle",
            "status": "ready",
            "tps": tps,
            "reqs": reqs,
        }
    if "Application startup complete" in flat:
        return {"phase": "ready", "status": "ready"}
    # HTTP access logs (e.g. GET /v1/models 200 OK) mean the server is up and serving
    if re.search(r'(?:GET|POST)\s+/[^\s]*\s+HTTP/[\d.]+"\s*\d{3}', flat):
        return {"phase": "idle", "status": "ready"}
    if "Loading weights took" in flat:
        return {"phase": "initializing", "status": "running"}
    # "GPU KV cache" alone (during allocation) — not "GPU KV cache usage" (runtime log)
    if "GPU KV cache" in flat and "GPU KV cache usage" not in flat:
        return {"phase": "warming up", "status": "running"}
    if load_matches:
        pct = int(load_matches[-1])
        return {"phase": f"loading {pct}%", "status": "running", "pct": pct}
    if dl_matches:
        pct = int(dl_matches[-1])
        return {"phase": f"downloading {pct}%", "status": "running", "pct": pct}
    return {}


def _ssh(host, cmd, port=None):
    """Build SSH command string with optional port."""
    pf = f"-p {port} " if port and port != "22" else ""
    return f"ssh {pf}{host} '{cmd}'"


def _safe_env_prefix(ep: str | None) -> str | None:
    """Rewrite a `source <path>` env_prefix so it no-ops if the path is missing.
    Prevents `line N: <path>: No such file or directory` errors when a serve
    task is launched against a host that doesn't have the expected venv.

    Also rewrites leading `~/` → `$HOME/` so the path expands inside double
    quotes (bash only tilde-expands unquoted tokens at word start)."""
    if not ep:
        return ep
    import shlex
    try:
        parts = shlex.split(ep, posix=True)
    except ValueError:
        raise HTTPException(400, "Invalid env_prefix")
    if len(parts) != 2 or parts[0] not in {"source", "."}:
        # Bash conda activation emitted by the frontend:
        #   eval "$(conda shell.bash hook)" && conda activate ENV
        m = re.fullmatch(r'eval "\$\(conda shell\.bash hook\)" && conda activate (.+)', ep)
        if m:
            env = m.group(1).strip()
            try:
                env_parts = shlex.split(env, posix=True)
            except ValueError:
                raise HTTPException(400, "Invalid env_prefix")
            if len(env_parts) != 1:
                raise HTTPException(400, "Invalid env_prefix")
            return 'eval "$(conda shell.bash hook)" && conda activate ' + shlex.quote(env_parts[0])

        # Plain conda activation, used by Windows/PowerShell and some manual callers.
        if len(parts) == 3 and parts[0] == "conda" and parts[1] == "activate":
            return "conda activate " + shlex.quote(parts[2])

        # PowerShell venv activation emitted by the frontend:
        #   & 'C:\path\Scripts\Activate.ps1'
        if len(parts) == 2 and parts[0] == "&":
            path = parts[1]
            if any(c in path for c in "\r\n;&|`$<>"):
                raise HTTPException(400, "Invalid env_prefix")
            return "& '" + path.replace("'", "''") + "'"

        raise HTTPException(400, "Invalid env_prefix")
    path = parts[1]
    if any(c in path for c in "\r\n;&|`$<>"):
        raise HTTPException(400, "Invalid env_prefix")
    # Replace a leading "~/" with "$HOME/" so it survives quoting
    if path.startswith("~/"):
        path = "$HOME/" + path[2:]
    elif path == "~":
        path = "$HOME"
    path = path.replace('"', '\\"')
    return f'[ -f "{path}" ] && source "{path}" || true'


def _ssh_ps(host, script_path, port=None):
    """Build SSH command to run a PowerShell script on a Windows remote."""
    pf = f"-p {port} " if port and port != "22" else ""
    return f'ssh {pf}{host} "powershell -ExecutionPolicy Bypass -File {script_path}"'


# Windows session dir — stored in user's temp on the remote
WIN_SESSION_DIR = "$env:TEMP\\\\odysseus-sessions"
