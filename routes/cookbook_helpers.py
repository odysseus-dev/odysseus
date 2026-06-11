"""cookbook_helpers.py — validators + small helpers shared by the cookbook routes.
Extracted from cookbook_routes.py; the routes module imports the symbols it needs."""

import logging
import ntpath
import os
import posixpath
import re
import shlex

from fastapi import HTTPException
from pydantic import BaseModel

from routes._validators import validate_remote_host, validate_ssh_port
from core.platform_compat import _ssh_exec_argv

logger = logging.getLogger(__name__)


# HuggingFace repo IDs are <org>/<name>, both alphanumerics plus ._-
# Rejecting anything else up front closes off shell-interpolation vectors.
_REPO_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$")
# Cached models scanned from a custom/local model dir are keyed by their leaf
# folder name (no slash), e.g. `DeepSeek-R1-UD-IQ4_XS`. The serve command uses
# the real on-disk path separately; this identifier is only for UI/task
# bookkeeping, so serving should accept the same safe glyph set as repo IDs.
_LOCAL_MODEL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
# Ollama model names include tags, e.g. `qwen2.5:0.5b` or `llama3.2:latest`.
# Some registries also use a namespace path. Keep this shell-safe: no spaces,
# quotes, `$`, `;`, `&`, pipes, or redirects.
_OLLAMA_MODEL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,200}$")
# Include pattern is a glob: allow typical safe glyphs only.
_INCLUDE_RE = re.compile(r"^[A-Za-z0-9._\-*?/\[\]]+$")
# HF tokens and API tokens are url-safe base64-like.
_TOKEN_RE = re.compile(r"^[A-Za-z0-9._~+/=-]+$")
# Session IDs we mint look like "cookbook-deadbeef" or "serve-deadbeef".
# Anything beyond plain alphanumerics + dash + underscore could break out
# of the shell/PowerShell contexts the value lands in.
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_GPU_LIST_RE = re.compile(r"^\d+(?:,\d+)*$")
# A download target directory. Absolute or ~-relative path; safe path glyphs
# only (no quotes or shell metacharacters). Spaces are allowed because command
# builders pass the value through quoted shell/Python contexts. The character
# class uses ``\w`` — Unicode word characters under Python 3's default str
# matching — so non-ASCII folder names pass validation too: Cyrillic, accented
# Latin, CJK, e.g. ``/Volumes/Модели`` or ``D:\AI Models\Модели``. This stays
# shell-safe: none of ``; & | ` $ '' "" () {}`` newlines etc. are in ``[\w. -]``,
# so injection vectors remain rejected. A leading ~ is expanded to $HOME at
# command-build time. (Drive letters stay ASCII: ``[A-Za-z]:``.)
_LOCAL_DIR_RE = re.compile(r"^~?(?:/[\w. -]*)+$|^~$")
_WINDOWS_LOCAL_DIR_RE = re.compile(r"^[A-Za-z]:[\\/](?:[\w. -]+(?:[\\/][\w. -]+)*[\\/]?)?$")
_WINDOWS_DRIVE_PATH_RE = re.compile(r"^[A-Za-z]:[\\/]")


def _git_bash_path(path: str) -> str:
    m = re.match(r"^([A-Za-z]):[\\/](.*)$", path)
    if not m:
        return path
    drive, rest = m.groups()
    return f"/{drive.lower()}/{rest.replace(chr(92), '/')}"


def _validate_repo_id(v: str | None) -> str:
    if not v or not _REPO_ID_RE.match(v):
        raise HTTPException(400, "Invalid repo_id — must be <org>/<name> using [A-Za-z0-9._-]")
    return v


def _validate_serve_model_id(v: str | None) -> str:
    if not v:
        raise HTTPException(400, "repo_id is required")
    if _REPO_ID_RE.match(v) or _LOCAL_MODEL_ID_RE.match(v) or _OLLAMA_MODEL_ID_RE.match(v):
        return v
    raise HTTPException(400, "Invalid repo_id — must be <org>/<name>, an Ollama name:tag, or a cached local model id")


def _validate_include(v: str | None) -> str | None:
    if v is None or v == "":
        return None
    if not _INCLUDE_RE.match(v):
        raise HTTPException(400, "Invalid include pattern")
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
    if len(v) >= 2 and v[0] == v[-1] and v[0] in {"'", '"'}:
        v = v[1:-1]
    v = v.rstrip("/") or "/"
    if not (_LOCAL_DIR_RE.match(v) or _WINDOWS_LOCAL_DIR_RE.match(v)):
        raise HTTPException(400, "Invalid local_dir — must be an absolute or ~ path with no shell metacharacters")
    # Reject path segments that start with '-' (option injection). '-' is in the
    # allowlist, so a dir like ``/models/-rf`` or ``D:\models\-rf`` could be read
    # as a CLI flag by hf/etc. — and quoting does NOT stop a value from being
    # parsed as an option. This is the one residual that command-build-time
    # quoting can't cover, so the guard lives here, keeping the safety wholly
    # inside the validator rather than relying on consumers.
    if any(seg.startswith("-") for seg in re.split(r"[\\/]", v) if seg):
        raise HTTPException(400, "Invalid local_dir — path segments cannot start with '-'")
    return v


def _validate_gpus(v: str | None) -> str | None:
    if v is None or v == "":
        return None
    if not _GPU_LIST_RE.fullmatch(str(v)):
        raise HTTPException(400, "Invalid gpus — expected comma-separated GPU indexes")
    return str(v)


def _shell_path(p: str) -> str:
    """Render a validated path for a double-quoted shell context, expanding a
    leading ~ to $HOME (single quotes wouldn't expand it). Safe because
    _validate_local_dir already rejects quotes and shell metacharacters."""
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
    elif _WINDOWS_DRIVE_PATH_RE.match(executable):
        bin_dir = ntpath.dirname(executable)
    else:
        bin_dir = os.path.dirname(os.path.abspath(executable))
    bin_dir = _git_bash_path(bin_dir)
    # Escape for a double-quoted context: $PATH must still expand, but spaces
    # and shell metacharacters in the path must be preserved literally.
    esc = (
        bin_dir.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("$", "\\$")
        .replace("`", "\\`")
    )
    return f'export PATH="{esc}:$PATH"'


def _pip_install_no_cache(cmd: str) -> str:
    """Add ``--no-cache-dir`` to a pip install command.

    Cookbook dependency installs (vLLM, llama-cpp-python, …) build large wheels;
    pip's default cache lives under ``$HOME/.cache/pip`` and these builds can fill
    a small home filesystem with ``[Errno 28] No space left on device`` mid-build
    (issue #1219), leaving the dependency "installed" but unusable (#1459).
    Disabling the cache for these one-off installs keeps them off the home disk
    (the maintainer's suggested ``PIP_CACHE_DIR=`` workaround, made the default).
    Idempotent; leaves non-pip-install commands untouched."""
    if not cmd or "pip install" not in cmd or "--no-cache-dir" in cmd:
        return cmd
    return cmd.replace("pip install", "pip install --no-cache-dir", 1)


def _pip_install_attempt(pip_cmd: str) -> str:
    """Wrap a single pip install command so its exit status survives the
    fallback chain and its stderr is visible in the tmux log on failure.

    Without this wrapper, `pip … 2>&1 | tail -5` returns ``tail``'s exit
    code (0), masking pip's real failure and preventing the next fallback
    from running.  The generated snippet captures all output to a temp
    file, prints the last 5 lines on failure (so the Cookbook log panel
    shows useful diagnostics), cleans up, and exits with pip's original
    status.
    """
    return (
        "bash -c '"
        f'_out=$(mktemp) && {pip_cmd} >"$_out" 2>&1; _rc=$?; '
        'tail -5 "$_out"; rm -f "$_out"; exit $_rc'
        "'"
    )


def _pip_command(python_cmd: str) -> str:
    """Return a pip command for either a pip executable or a Python executable."""
    cmd = python_cmd.strip()
    if " -m pip" in cmd or cmd in {"pip", "pip3"}:
        return python_cmd
    if cmd in {"python", "python3", "python.exe"} or cmd.endswith(("/python", "/python3", "\\python.exe")):
        return f"{python_cmd} -m pip"
    return python_cmd


def _pip_break_system_packages_check(pip_cmd: str) -> str:
    return f"{pip_cmd} install --help 2>/dev/null | grep -q -- --break-system-packages"


def _pip_install_fallback_chain(package: str, *, python_cmd: str = "python3 -m pip", upgrade: bool = False) -> str:
    """Build a bash pip install fallback chain that surfaces errors.

    Try the active interpreter/environment first. ``--user`` is invalid
    inside many venvs, so only attempt the ``--user`` fallback when NOT
    inside a venv.

    Each attempt is wrapped via :func:`_pip_install_attempt` so pip's real
    exit code is preserved (no ``| tail`` masking) and the last 5 lines of
    pip output appear in the Cookbook log on failure.
    """
    from core.platform_compat import IS_WINDOWS
    upgrade_flag = " -U" if upgrade else ""
    # Shell-quote the package spec: an extras spec like ``llama-cpp-python[server]``
    # contains brackets that bash would treat as a glob, so it must be quoted
    # before being embedded in the install command. Plain names (e.g.
    # ``huggingface_hub``) are returned unchanged by ``shlex.quote``.
    pkg = shlex.quote(package)
    # llama-cpp-python source builds are brittle on older distro pip/packaging
    # stacks (common on WSL images). Prefer the prebuilt wheel index whenever
    # this package is requested so dependency-install tasks are reliable.
    if "llama-cpp-python" in package:
        pkg += " --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu"

    pip_cmd = _pip_command(python_cmd)
    base = _pip_install_attempt(f"{pip_cmd} install -q{upgrade_flag} {pkg}")
    user = _pip_install_attempt(f"{pip_cmd} install --user -q{upgrade_flag} {pkg}")
    user_break_system = _pip_install_attempt(f"{pip_cmd} install --user --break-system-packages -q{upgrade_flag} {pkg}")
    user_fallback = f"( {user} || {{ {_pip_break_system_packages_check(pip_cmd)} && {user_break_system}; }} )"
    # Derive the python executable for the venv detection check.
    # Must use the same interpreter that pip belongs to; hardcoding
    # python3 breaks when pip lives in a venv that only has "python".
    if " -m pip" in pip_cmd:
        python_exe = pip_cmd.replace(" -m pip", "")
    elif pip_cmd.strip() == "pip":
        python_exe = "python"
    elif pip_cmd.strip() == "pip3":
        python_exe = "python3"
    else:
        python_exe = "python3"
    venv_check = f'{python_exe} -c "import sys; sys.exit(0 if sys.prefix != sys.base_prefix else 1)"'
    # Negated: `! venv_check` succeeds (exit 0) when NOT in a venv -> `&&` tries
    # --user. When IN a venv `! venv_check` fails -> `&&` skips --user and the
    # group exits non-zero, propagating the base-install failure instead of
    # masking it as success (the `|| { venv_check || … }` shape from #903
    # swallowed the exit code because venv_check's exit-0 became the group's
    # result). `--break-system-packages` is only attempted when the active pip
    # supports it; older pip versions abort with "no such option" otherwise.
    return f"{base} || {{ ! {venv_check} && {user_fallback}; }}"


def _venv_safe_local_pip_install_cmd(cmd: str, *, local: bool, in_venv: bool) -> str:
    """Drop pip user-install flags that are invalid for local venv installs.

    Cookbook dependency installs run through the model-serve task path so users
    can watch progress in the same log UI. For local POSIX runs, that task
    prepends Odysseus' own interpreter directory to PATH. If Odysseus itself is
    running from a venv, `python3` resolves to the venv Python and pip rejects
    `--user` with "User site-packages are not visible in this virtualenv".

    Keep remote and non-venv installs unchanged: remotes may intentionally use
    system Python, and Docker/non-venv installs still need user-site fallback.
    """
    if not local or not in_venv:
        return cmd
    if "pip install" not in (cmd or ""):
        return cmd
    try:
        parts = shlex.split(cmd)
    except ValueError:
        return cmd
    stripped = [
        part
        for part in parts
        if part not in {"--user", "--break-system-packages"}
    ]
    return shlex.join(stripped)


def _pip_install_command_without_break_system_packages(cmd: str) -> str:
    try:
        parts = shlex.split(cmd)
    except ValueError:
        return cmd
    stripped = [part for part in parts if part != "--break-system-packages"]
    return shlex.join(stripped)


def _pip_install_help_check_from_cmd(cmd: str) -> str | None:
    try:
        parts = shlex.split(cmd)
    except ValueError:
        return None
    try:
        install_index = parts.index("install")
    except ValueError:
        return None
    if install_index <= 0:
        return None
    pip_prefix = parts[:install_index]
    return f"{shlex.join(pip_prefix + ['install', '--help'])} 2>/dev/null | grep -q -- --break-system-packages"


def _append_pip_install_runner_lines(runner_lines: list[str], cmd: str) -> None:
    """Append a pip install command, guarding --break-system-packages support.

    The Dependencies UI may submit ``python3 -m pip install --user
    --break-system-packages ...`` for non-venv installs. That flag is useful on
    PEP-668-locked distros, but older pip (including Ubuntu 22.04's apt pip in
    the NVIDIA CUDA base image) aborts with "no such option". Branch at runner
    time so stale browser JS and remote targets are handled by the server too.
    """
    if "--break-system-packages" not in (cmd or ""):
        runner_lines.append(cmd)
        return
    help_check = _pip_install_help_check_from_cmd(cmd)
    without_break = _pip_install_command_without_break_system_packages(cmd)
    if not help_check or without_break == cmd:
        runner_lines.append(cmd)
        return
    runner_lines.append(f"if {help_check}; then")
    runner_lines.append(f"  {cmd}")
    runner_lines.append("else")
    runner_lines.append('  echo "[odysseus] pip does not support --break-system-packages; installing without it."')
    runner_lines.append(f"  {without_break}")
    runner_lines.append("fi")


def _user_shell_path_bootstrap() -> list[str]:
    return [
        'ODYSSEUS_USER_SHELL="${SHELL:-}"',
        'if [ -n "$ODYSSEUS_USER_SHELL" ] && [ -x "$ODYSSEUS_USER_SHELL" ]; then',
        '  ODYSSEUS_USER_PATH="$("$ODYSSEUS_USER_SHELL" -ic \'printf "__ODYSSEUS_PATH__%s\\n" "$PATH"\' 2>/dev/null | sed -n \'s/^__ODYSSEUS_PATH__//p\' | tail -n 1 || true)"',
        '  if [ -n "$ODYSSEUS_USER_PATH" ]; then export PATH="$ODYSSEUS_USER_PATH:$PATH"; fi',
        'fi',
        'command -v python3 >/dev/null 2>&1 || python3() { python "$@"; }',
        'command -v python >/dev/null 2>&1 || python() { python3 "$@"; }',
    ]


def _cached_model_scan_script(model_dirs: list[str] | None = None, add_hf_cache: str | None = None) -> str:
    """Build the standalone Python scanner used by /api/model/cached.
    Allows for an additional HuggingFace cache path to be scanned (i.e. Windows HF cache for local WSL envs.)
    """
    lines = [
        "import json, os, re, shutil, subprocess, urllib.request",
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
        "def gguf_role(name):",
        "    n = name.lower()",
        "    if n.startswith('mmproj') or 'mmproj' in n: return 'projector'",
        "    return 'model'",
        "def gguf_quant(name):",
        "    m = re.search(r'(?i)(UD-)?(IQ[0-9]_[A-Z0-9_]+|Q[0-9](?:_[A-Z0-9]+)+|BF16|F16|FP16|F32|Q8_0)', name)",
        "    return m.group(0).upper() if m else ''",
        "def collect_ggufs(base):",
        "    files = []",
        "    split_groups = {}",
        "    if not os.path.isdir(base) or not safe_path(base): return files",
        "    for root, dirs, fns in safe_walk(base):",
        "        for fn in sorted(fns):",
        "            if not fn.lower().endswith('.gguf'): continue",
        "            if fn.startswith('._'): continue  # macOS AppleDouble sidecar, not a real GGUF",
        "            fp = os.path.join(root, fn)",
        "            try: size = os.path.getsize(fp)",
        "            except Exception: size = 0",
        "            try: rel = os.path.relpath(fp, base).replace(os.sep, '/')",
        "            except Exception: rel = fn",
        "            sm = re.match(r'(?i)^(.+)-(\\d+)-of-(\\d+)\\.gguf$', fn)",
        "            if sm:",
        "                prefix, part_s, total_s = sm.group(1), sm.group(2), sm.group(3)",
        "                key = (root, prefix, total_s)",
        "                g = split_groups.setdefault(key, {'name':fn,'rel_path':rel,'size_bytes':0,'role':gguf_role(fn),'quant':gguf_quant(fn),'parts':int(total_s),'split':True})",
        "                g['size_bytes'] += size",
        "                if int(part_s) == 1:",
        "                    g.update({'name':fn,'rel_path':rel,'role':gguf_role(fn),'quant':gguf_quant(fn)})",
        "                continue",
        "            files.append({'name':fn,'rel_path':rel,'size_bytes':size,'role':gguf_role(fn),'quant':gguf_quant(fn)})",
        "    files.extend(split_groups.values())",
        "    files.sort(key=lambda f: (f.get('role') != 'model', f.get('rel_path', '')))",
        "    return files",
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
        "        # Windows HF cache stores files directly in snapshots/; blobs/ may be empty.",
        "        # Fallback: scan snapshots for real files when blobs yielded nothing.",
        "        if sz == 0 and os.path.isdir(snap):",
        "            for sd in os.listdir(snap):",
        "                sf = os.path.join(snap, sd)",
        "                if not os.path.isdir(sf): continue",
        "                for f in os.scandir(sf):",
        "                    if f.is_file(): nf += 1; sz += f.stat().st_size",
        "                    if f.name.endswith('.incomplete'): ic = True",
        "        is_diffusion = False; gguf_files = []",
        "        if os.path.isdir(snap):",
        "            for sd in os.listdir(snap):",
        "                sf = os.path.join(snap, sd)",
        "                if not os.path.isdir(sf): continue",
        "                if os.path.exists(os.path.join(sf, 'model_index.json')): is_diffusion = True",
        "                for f in collect_ggufs(sf): f['rel_path'] = sd + '/' + f['rel_path']; gguf_files.append(f)",
        "        models.append({'repo_id':rid,'size_bytes':sz,'nb_files':nf,'has_incomplete':ic,'path':cache,'is_diffusion':is_diffusion,'is_gguf':bool(gguf_files),'gguf_files':gguf_files})",
        "def hf_cache_paths():",
        "    candidates = []",
        "    def add(p):",
        "        if not p: return",
        "        p = os.path.expanduser(p)",
        "        if p not in candidates: candidates.append(p)",
        "    add(os.environ.get('HUGGINGFACE_HUB_CACHE'))",
        "    hf_home = os.environ.get('HF_HOME')",
        "    if hf_home: add(os.path.join(hf_home, 'hub'))",
        "    add('~/.cache/huggingface/hub')",
        "    # Docker images mount ./data/huggingface at /app/.cache/huggingface.",
        "    # When HOME is /root, expanduser() misses that persisted cache.",
        "    add('/app/.cache/huggingface/hub')",
        f"    add({add_hf_cache!r})" if add_hf_cache else "",
        "    return candidates",
        "def scan_dir(p):",
        "    if not os.path.isdir(p) or not safe_path(p): return",
        "    for d in sorted(os.listdir(p)):",
        "        if d.startswith('.'): continue",
        "        if d.startswith('models--'): continue",
        "        fp = os.path.join(p, d)",
        "        if not os.path.isdir(fp) or os.path.islink(fp) or not safe_path(fp): continue",
        "        if d in seen: continue",
        "        is_model = False; gguf_files = []",
        "        for root, dirs, fns in safe_walk(fp):",
        "            for fn in fns:",
        "                if fn.lower().endswith('.gguf'): is_model = True",
        "                elif fn == 'config.json' or fn.endswith('.safetensors') or fn.endswith('.bin'): is_model = True",
        "            if is_model: break",
        "        if not is_model: continue",
        "        gguf_files = collect_ggufs(fp)",
        "        seen.add(d)",
        "        sz, nf = 0, 0",
        "        for dp, _, fns in safe_walk(fp):",
        "            for fn in fns:",
        "                try: nf += 1; sz += os.path.getsize(os.path.join(dp, fn))",
        "                except Exception: pass",
        "        is_diff = os.path.exists(os.path.join(fp, 'model_index.json'))",
        "        models.append({'repo_id':d,'size_bytes':sz,'nb_files':nf,'has_incomplete':False,'path':p,'is_local_dir':True,'is_diffusion':is_diff,'is_gguf':bool(gguf_files),'gguf_files':gguf_files})",
        "def parse_size(num, unit):",
        "    try: n = float(num)",
        "    except Exception: return 0",
        "    u = (unit or '').upper()",
        "    if u.startswith('TB'): return int(n * 1024 ** 4)",
        "    if u.startswith('GB'): return int(n * 1024 ** 3)",
        "    if u.startswith('MB'): return int(n * 1024 ** 2)",
        "    if u.startswith('KB'): return int(n * 1024)",
        "    return int(n)",
        "def scan_ollama():",
        "    if not shutil.which('ollama'): return",
        "    try:",
        "        p = subprocess.run(['ollama', 'list'], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=6)",
        "    except Exception:",
        "        return",
        "    if p.returncode != 0: return",
        "    for line in (p.stdout or '').splitlines()[1:]:",
        "        parts = line.split()",
        "        if len(parts) < 4: continue",
        "        name = parts[0]",
        "        if not name or name in seen: continue",
        "        size_bytes = parse_size(parts[2], parts[3])",
        "        seen.add(name)",
        "        models.append({'repo_id':name,'size_bytes':size_bytes,'nb_files':1,'has_incomplete':False,'path':'ollama','backend':'ollama','is_ollama':True})",
        "def scan_ollama_api():",
        "    urls = ['http://127.0.0.1:11434/api/tags', 'http://localhost:11434/api/tags', 'http://host.docker.internal:11434/api/tags']",
        "    for url in urls:",
        "        try:",
        "            with urllib.request.urlopen(url, timeout=2) as r:",
        "                data = json.loads(r.read().decode('utf-8', 'replace'))",
        "        except Exception:",
        "            continue",
        "        for item in data.get('models', []):",
        "            name = item.get('name') or item.get('model')",
        "            if not name or name in seen: continue",
        "            size_bytes = int(item.get('size') or item.get('size_bytes') or 0)",
        "            seen.add(name)",
        "            models.append({'repo_id':name,'size_bytes':size_bytes,'nb_files':1,'has_incomplete':False,'path':'ollama','backend':'ollama','is_ollama':True})",
        "        return",
        "for _hf_cache in hf_cache_paths(): scan_hf(_hf_cache)",
        "scan_ollama()",
        "scan_ollama_api()",
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
_OLLAMA_HOST_ASSIGNMENT_RE = re.compile(r"(?:^|\s)OLLAMA_HOST=([^\s]+)")
_OLLAMA_BIND_RE = re.compile(r"^\[([^\]]+)\]:(\d+)$|^([^:]+):(\d+)$")
_OLLAMA_BIND_HOST_RE = re.compile(r"^[A-Za-z0-9._:-]+$")


def _ollama_bind_from_cmd(cmd: str | None, *, default_host: str = "127.0.0.1") -> tuple[str, str]:
    """Return the Ollama bind host/port requested by a serve command.

    Plain local `ollama serve` defaults to loopback. Remote callers can pass a
    wider default host so the resulting API is reachable by Odysseus.
    """
    if not cmd:
        return default_host, "11434"
    match = _OLLAMA_HOST_ASSIGNMENT_RE.search(cmd)
    if not match:
        return default_host, "11434"
    value = match.group(1).strip("'\"")
    bind_match = _OLLAMA_BIND_RE.match(value)
    if not bind_match:
        return "127.0.0.1", "11434"
    bracketed_host = bind_match.group(1)
    host = bracketed_host or bind_match.group(3) or "127.0.0.1"
    port = bind_match.group(2) or bind_match.group(4) or "11434"
    if not _OLLAMA_BIND_HOST_RE.match(host):
        return "127.0.0.1", "11434"
    try:
        port_num = int(port, 10)
    except ValueError:
        return "127.0.0.1", "11434"
    if port_num < 1 or port_num > 65535:
        return "127.0.0.1", "11434"
    return f"[{host}]" if bracketed_host else host, port


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
    # Temporarily replace safe $(printf %s ...) expressions with a placeholder
    # to avoid triggering the metacharacter/command-injection checks.
    cleaned_v = v
    printf_matches = list(re.finditer(r"\$\(\s*printf\s+%s\s+([^\n()]*?)\)", v))
    for match in printf_matches:
        inner = match.group(1)
        if not any(c in inner for c in (";", "&&", "||", "$(", "`")):
            cleaned_v = cleaned_v.replace(match.group(0), "/placeholder/safe/path.gguf")

    # (`$(` was the original intent; bare `$` is fine for shell-safe paths.)
    if any(c in cleaned_v for c in (";", "&&", "||", "$(")):
        raise HTTPException(400, "Invalid characters in cmd")
    _check_serve_binary(v)
    return v


def _append_serve_preflight_exit_lines(runner_lines: list[str], *, keep_shell_open: bool) -> None:
    """Append serve-runner lines that surface preflight failures before exit."""
    runner_lines.append('if [ -n "$ODYSSEUS_PREFLIGHT_EXIT" ]; then')
    runner_lines.append('  echo ""; echo "=== Process exited with code $ODYSSEUS_PREFLIGHT_EXIT ==="')
    if keep_shell_open:
        # Decouple the post-crash interactive shell from the persistent log
        # file. fds 3/4 were saved BEFORE the tee redirect at the top of
        # the runner; restoring them here means the neofetch banner the
        # user's .zshrc prints lands on the tmux pane only, not in the
        # log file the agent's tail_serve_output reads.
        runner_lines.append('  exec 1>&3 2>&4 3>&- 4>&- 2>/dev/null || true')
        runner_lines.append('  sleep 0.2  # let tee child flush + exit')
        runner_lines.append('  exec "${SHELL:-/bin/bash}"')
    else:
        runner_lines.append('  exit "$ODYSSEUS_PREFLIGHT_EXIT"')
    runner_lines.append('fi')


def _append_vllm_linux_preflight_lines(runner_lines: list[str]) -> None:
    """Append Linux vLLM readiness lines that identify the runtime being used."""
    # Keep the user install bin visible for Odysseus-managed `pip install --user`
    # installs, but then report the actual CLI path so external runtimes are clear.
    runner_lines.append('export PATH="$HOME/.local/bin:$PATH"')
    runner_lines.append('ODYSSEUS_VLLM_BIN="$(command -v vllm 2>/dev/null || true)"')
    runner_lines.append('if [ -z "$ODYSSEUS_VLLM_BIN" ]; then')
    runner_lines.append('  echo "ERROR: vLLM is not installed."')
    runner_lines.append('  ODYSSEUS_PREFLIGHT_EXIT=127')
    runner_lines.append('else')
    runner_lines.append('  echo "[odysseus] vLLM CLI: $ODYSSEUS_VLLM_BIN"')
    runner_lines.append('  ODYSSEUS_VLLM_VERSION="$("$ODYSSEUS_VLLM_BIN" --version 2>&1 | head -n 1 || true)"')
    runner_lines.append('  if [ -n "$ODYSSEUS_VLLM_VERSION" ]; then echo "[odysseus] vLLM version: $ODYSSEUS_VLLM_VERSION"; fi')
    runner_lines.append('fi')

def _append_serve_exit_code_lines(
    runner_lines: list[str],
    *,
    keep_shell_open: bool,
    is_pip_install: bool = False,
) -> None:
    """Append serve-runner lines that preserve and report the command exit code."""
    runner_lines.append('ODYSSEUS_CMD_EXIT=$?')
    if is_pip_install:
        runner_lines.append('if [ $ODYSSEUS_CMD_EXIT -eq 0 ]; then echo ""; echo "DOWNLOAD_OK"; fi')
    if keep_shell_open:
        runner_lines.append('echo ""; echo "=== Process exited with code $ODYSSEUS_CMD_EXIT ==="')
        # See preflight branch above for the rationale on restoring fds 3/4.
        runner_lines.append('exec 1>&3 2>&4 3>&- 4>&- 2>/dev/null || true')
        runner_lines.append('sleep 0.2  # let tee child flush + exit')
        runner_lines.append('exec "${SHELL:-/bin/bash}"')
    else:
        runner_lines.append('echo ""; echo "=== Process exited with code $ODYSSEUS_CMD_EXIT ==="')
        runner_lines.append('exit "$ODYSSEUS_CMD_EXIT"')


def _append_llama_cpp_linux_accel_build_lines(runner_lines: list[str]) -> None:
    """Append Linux llama.cpp build lines that prefer ROCm/HIP when available.

    Cookbook already detects AMD GPUs elsewhere, but the llama.cpp bootstrap used
    to hard-wire CUDA on Linux. That made ROCm hosts attempt a CUDA configure and
    fail with "CUDA Toolkit not found" instead of building with HIP.
    """
    # Detect pip-installed nvcc (from vLLM/nvidia CUDA wheels) and put it on PATH
    # so cmake's CUDA configure can find it. We keep this after the ROCm/HIP
    # check — a machine with both stacks should honor the native HIP toolchain on
    # AMD hosts instead of accidentally preferring a stray nvcc wheel.
    runner_lines.append('    for _cudir in ~/.local/lib/python*/site-packages/nvidia/cu13 ~/.local/lib/python*/site-packages/nvidia/cu12 ~/.local/lib/python*/site-packages/nvidia/cuda_nvcc; do')
    runner_lines.append('      [ -x "$_cudir/bin/nvcc" ] && export CUDA_HOME="$_cudir" && export PATH="$_cudir/bin:$PATH" && break')
    runner_lines.append('    done')
    # rm -rf build so a prior poisoned CMakeCache.txt (e.g. from a failed CUDA
    # or HIP attempt) doesn't cause the next configure to reuse stale settings.
    runner_lines.append('    cd ~/llama.cpp && rm -rf build')
    runner_lines.append('    if command -v hipconfig &>/dev/null || [ -d /opt/rocm ] || [ -n "$ROCM_PATH" ] || [ -n "$HIP_PATH" ]; then')
    runner_lines.append('      if command -v hipconfig &>/dev/null; then')
    runner_lines.append('        export HIPCXX="${HIPCXX:-$(hipconfig -l)/clang}"')
    runner_lines.append('        export HIP_PATH="${HIP_PATH:-$(hipconfig -R)}"')
    runner_lines.append('      fi')
    runner_lines.append('      echo "[odysseus] ROCm/HIP detected — building llama-server with HIP support..."')
    runner_lines.append('      cmake -B build -DCMAKE_BUILD_TYPE=Release -DGGML_HIP=ON && cmake --build build -j"$NPROC" --target llama-server && ln -sf ~/llama.cpp/build/bin/llama-server ~/bin/llama-server')
    runner_lines.append('    elif command -v nvcc &>/dev/null; then')
    # nvcc alone is not sufficient — pip-installed CUDA wheels or incomplete
    # tooling can expose nvcc without shipping libcudart, causing cmake to fail
    # mid-build with "CUDA runtime library not found". Check cudart explicitly
    # via a small helper so the guard stays readable.
    runner_lines.append('      _odysseus_has_cudart() {')
    runner_lines.append('        ldconfig -p 2>/dev/null | grep -q \'libcudart\\.so\' && return 0')
    runner_lines.append('        local _cuh="${CUDA_HOME:-/usr/local/cuda}"')
    runner_lines.append('        ls "$_cuh/lib64/libcudart.so"* &>/dev/null && return 0')
    runner_lines.append('        ls "$_cuh/lib/libcudart.so"* &>/dev/null && return 0')
    runner_lines.append('        ls /usr/local/cuda/lib64/libcudart.so* &>/dev/null && return 0')
    runner_lines.append('        ls /usr/local/cuda/lib/libcudart.so* &>/dev/null && return 0')
    runner_lines.append('        ls "${_cuh%/cuda_nvcc}/cuda_runtime/lib/libcudart.so"* &>/dev/null && return 0')
    runner_lines.append('        return 1')
    runner_lines.append('      }')
    runner_lines.append('      if _odysseus_has_cudart; then')
    runner_lines.append('        echo "[odysseus] CUDA nvcc + cudart found — building llama-server with CUDA (GPU) support..."')
    runner_lines.append('        cmake -B build -DCMAKE_BUILD_TYPE=Release -DGGML_CUDA=ON && cmake --build build -j"$NPROC" --target llama-server && ln -sf ~/llama.cpp/build/bin/llama-server ~/bin/llama-server')
    runner_lines.append('      else')
    runner_lines.append('        echo "[odysseus] WARNING: nvcc found but CUDA runtime (libcudart.so) is not visible — building llama-server for CPU only."')
    runner_lines.append('        echo "[odysseus]   GPU inference will not be available for this llama.cpp build."')
    runner_lines.append('        echo "[odysseus]   Ensure libcudart is installed (e.g. cuda-runtime package) and visible via ldconfig or CUDA_HOME."')
    runner_lines.append('        cmake -B build -DCMAKE_BUILD_TYPE=Release && cmake --build build -j"$NPROC" --target llama-server && ln -sf ~/llama.cpp/build/bin/llama-server ~/bin/llama-server')
    runner_lines.append('      fi')
    runner_lines.append('    else')
    runner_lines.append('      echo "[odysseus] WARNING: no HIP/CUDA toolchain found — building llama-server for CPU only."')
    runner_lines.append('      echo "[odysseus]   GPU inference will not be available for this llama.cpp build."')
    runner_lines.append('      echo "[odysseus]   Install ROCm for AMD GPUs or vLLM/CUDA tooling for NVIDIA, then re-launch this serve task."')
    runner_lines.append('      cmake -B build -DCMAKE_BUILD_TYPE=Release && cmake --build build -j"$NPROC" --target llama-server && ln -sf ~/llama.cpp/build/bin/llama-server ~/bin/llama-server')
    runner_lines.append('    fi')


def _llama_cpp_rebuild_cmd() -> str:
    """Shell command that clears the Cookbook-managed llama.cpp build.

    Removes the cached ``llama-server`` symlink and the ``~/llama.cpp/build``
    directory so the next llama.cpp serve recompiles from source, picking up a
    CUDA or HIP toolchain if one is now available. The serve bootstrap only
    builds when ``llama-server`` is missing from PATH, so without this an
    existing CPU-only build is reused forever. It deliberately installs and
    downloads nothing; the rebuild itself happens on the next serve.
    """
    return (
        'mkdir -p "$HOME/bin" && '
        'rm -f "$HOME/bin/llama-server" && '
        'rm -rf "$HOME/llama.cpp/build" && '
        'echo "[odysseus] Cleared the cached llama.cpp build. '
        'Re-launch the serve task to rebuild llama-server from source '
        '(CUDA or HIP will be used if a toolchain is now available)."'
    )


class ModelDownloadRequest(BaseModel):
    repo_id: str
    backend: str | None = None  # "hf" (default) or "ollama"
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
    if re.search(r'Ollama API ready on port\s+\d+', flat, re.I):
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


def _diagnose_serve_output(text: str) -> dict | None:
    """Server-side mirror of the Cookbook UI's common serve diagnoses.

    The browser uses cookbook-diagnosis.js for clickable fixes. This gives
    the agent/tool path the same structured signal so it can retry with an
    adjusted command instead of guessing from raw tmux output.
    """
    if not text:
        return None
    tail = text[-6000:]
    patterns = [
        (
            r"No available memory for the cache blocks|Available KV cache memory:.*-",
            "No GPU memory left for KV cache after loading model.",
            [
                {"label": "retry with GPU memory utilization 0.95", "op": "replace", "flag": "--gpu-memory-utilization", "value": "0.95"},
                {"label": "retry with context 2048", "op": "replace", "flag": "--max-model-len", "value": "2048"},
            ],
        ),
        (
            r"CUDA out of memory|torch\.cuda\.OutOfMemoryError|CUDA error: out of memory|warming up sampler|max_num_seqs.*gpu_memory_utilization",
            "GPU ran out of memory during startup or warmup.",
            [
                {"label": "retry with context 4096", "op": "replace", "flag": "--max-model-len", "value": "4096"},
                {"label": "retry with GPU memory utilization 0.80", "op": "replace", "flag": "--gpu-memory-utilization", "value": "0.80"},
                {"label": "retry with --enforce-eager", "op": "append", "arg": "--enforce-eager"},
            ],
        ),
        (
            r"not divisib|must be divisible|attention heads.*divisible",
            "Tensor parallel size is incompatible with the model.",
            [
                {"label": "retry with tensor parallel size 1", "op": "replace", "flag": "--tensor-parallel-size", "value": "1"},
                {"label": "retry with tensor parallel size 2", "op": "replace", "flag": "--tensor-parallel-size", "value": "2"},
            ],
        ),
        (
            r"KV cache.*too (small|large)|max_model_len.*exceeds|maximum.*context",
            "Context length is too large for available GPU memory.",
            [
                {"label": "retry with context 8192", "op": "replace", "flag": "--max-model-len", "value": "8192"},
                {"label": "retry with context 4096", "op": "replace", "flag": "--max-model-len", "value": "4096"},
            ],
        ),
        (
            r"enable-auto-tool-choice requires --tool-call-parser",
            "Auto tool choice requires an explicit tool call parser.",
            [{"label": "retry with Hermes tool parser", "op": "append", "arg": "--tool-call-parser hermes"}],
        ),
        (
            r"Please pass.*trust.remote.code=True|contains custom code which must be executed to correctly load|does not recognize this architecture|model type.*but Transformers does not",
            "Model requires custom code or newer model support.",
            [{"label": "retry with --trust-remote-code", "op": "append", "arg": "--trust-remote-code"}],
        ),
        (
            r"There is no module or parameter named ['\"]lm_head\.input_scale['\"]|lm_head\.input_scale|weight_scale_2",
            "vLLM cannot load this ModelOpt LM-head quantized checkpoint with the current runtime.",
            [
                {
                    "label": "upgrade vLLM through the environment that provides this CLI, or use a compatible checkpoint",
                    "op": "manual",
                }
            ],
        ),
        (
            r"Either a revision or a version must be specified|transformers\.integrations\.hub_kernels|kernels/layer",
            "vLLM/Transformers kernel package mismatch.",
            [{"label": "update vLLM, Transformers, and kernels on this server", "op": "dependency", "package": "vllm transformers kernels"}],
        ),
        (
            r"Address already in use|bind.*address.*in use",
            "Port is already in use.",
            [{"label": "retry on port 8001", "op": "replace", "flag": "--port", "value": "8001"}],
        ),
        (
            r"No CUDA GPUs are available|no GPU.*found|CUDA_VISIBLE_DEVICES.*invalid",
            "No GPUs are visible to the serve process.",
            [{"label": "clear Cookbook GPU selection or choose available GPUs", "op": "settings", "field": "gpus", "value": ""}],
        ),
        (
            r"Failed to infer device type|NVML Shared Library Not Found|No module named 'amdsmi'|platform is not available",
            "vLLM could not find a supported GPU (CUDA or ROCm). "
            "This machine may have integrated or unsupported graphics only.",
            [
                {"label": "switch to llama.cpp (CPU/Metal, works without a discrete GPU)", "op": "manual"},
                {"label": "switch to Ollama (CPU/Metal, works without a discrete GPU)", "op": "manual"},
            ],
        ),
        (
            r"vllm.*command not found|No module named vllm|ERROR: vLLM is not installed",
            "vLLM is not installed or not in PATH on this server.",
            [{"label": "install vLLM in Cookbook Dependencies", "op": "dependency", "package": "vllm"}],
        ),
        (
            r"sglang.*command not found|No module named sglang|SGLang is not installed",
            "SGLang is not installed or not in PATH on this server.",
            [{"label": "install SGLang in Cookbook Dependencies", "op": "dependency", "package": "sglang[all]"}],
        ),
        (
            r"llama-server.*command not found|llama\.cpp.*not found|No module named.*llama_cpp|No module named 'starlette_context'|git: command not found|cmake: command not found",
            "llama.cpp / llama-cpp-python dependencies are missing.",
            [{"label": "install llama.cpp dependencies or llama-cpp-python[server]", "op": "dependency", "package": "llama-cpp-python[server]"}],
        ),
        (
            r"No GGUF found on this host|no \.gguf file|No GGUF file found",
            "No GGUF file found for this model on this host. The llama.cpp backend needs a .gguf file.",
            [{"label": "download a GGUF build of this model (repo name usually ends in -GGUF, file like Q4_K_M.gguf)", "op": "manual"}],
        ),
        (
            r"No module named 'torch'|No module named torch|No module named 'diffusers'|No module named diffusers",
            "Diffusion serving requires PyTorch and diffusers.",
            [{"label": "install diffusers[torch] in Cookbook Dependencies", "op": "dependency", "package": "diffusers[torch]"}],
        ),
        (
            r"403 Forbidden|401 Unauthorized|Access to model.*is restricted|gated repo|not in the authorized list|awaiting a review",
            "Model access is gated or unauthorized.",
            [{"label": "set HF token and request model access on HuggingFace", "op": "manual"}],
        ),
    ]
    for pattern, message, suggestions in patterns:
        if re.search(pattern, tail, re.I):
            return {"message": message, "suggestions": suggestions}
    if re.search(r"Traceback \(most recent call last\)", tail, re.I) and not re.search(
        r"Application startup complete|GET /v1/|Uvicorn running on", tail, re.I
    ):
        return {
            "message": "Python traceback detected during serve startup.",
            "suggestions": [{"label": "inspect traceback and retry with adjusted backend/settings", "op": "manual"}],
        }
    return None


async def run_ssh_command_async(
    remote: str,
    ssh_port: str | None,
    remote_cmd: str,
    *,
    timeout: float,
    connect_timeout: int | None = None,
    strict_host_key_checking: bool | None = None,
    stdin_data: bytes | None = None,
) -> tuple[int, bytes, bytes]:
    """Run an ssh command with centralized timeout and stderr/stdout capture.
    Async version of core.platform_compat.run_ssh_command_sync.
    """
    import asyncio
    proc = await asyncio.create_subprocess_exec(
        *_ssh_exec_argv(
            remote,
            ssh_port,
            remote_cmd=remote_cmd,
            connect_timeout=connect_timeout,
            strict_host_key_checking=strict_host_key_checking,
        ),
        stdin=asyncio.subprocess.PIPE if stdin_data is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(input=stdin_data), timeout=timeout
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        raise
    return proc.returncode or 0, stdout, stderr
