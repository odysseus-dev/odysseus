"""
claude_code_tool.py — delegate a coding task to Claude Code, backed by Ollama.

The Odysseus agent (powered by the user's local chat model) can hand a
self-contained build/coding task to **Claude Code running inside this
container**, with Claude Code's model served by the local Ollama daemon which
brokers an Ollama Cloud model (default ``kimi-k2.7-code:cloud``). The heavy
coding model therefore runs on Ollama's cloud — billed to the operator's Ollama
subscription, using zero host GPU/RAM.

Direction note (see integrations/claude/README.md): the *inbound* Claude Agent
API (``/api/codex/*``) is the opposite of this — that is an EXTERNAL Claude Code
reaching into Odysseus. THIS tool is the local agent reaching OUT to a
contained, headless Claude Code.

Safety: this spawns an autonomous Claude Code with ``--permission-mode
bypassPermissions`` and shell/file access inside the container. It is therefore
ADMIN-ONLY (registered in tool_security.NON_ADMIN_BLOCKED_TOOLS). Its working
dir and sole ``--add-dir`` are ``$DATA_DIR/claude_builds/<subdir>`` (bind-mounted
to the host's ``./data/claude_builds`` for retrieval), but note ``bypassPermissions``
is NOT path-confined — a determined or misbehaving child can still write
elsewhere in the container, so the CONTAINER is the real isolation boundary, not
the build dir. (Set ``CLAUDE_DELEGATE_PERMISSION_MODE=acceptEdits`` to drop the
unattended-shell autonomy.) The child gets a minimal allowlisted env — not the
app's secrets — and a hard wall-clock timeout SIGKILLs the whole process group
to cap runaway runs/spend.

Requires:
  - ``OLLAMA_API_KEY`` in the container env (generate at ollama.com/settings/keys)
    for ``:cloud`` models.
  - ``ollama`` + ``claude`` installed in the image (Dockerfile INSTALL_CLAUDE=true).
"""

import asyncio
import json
import logging
import os
import re

from src.constants import MAX_OUTPUT_CHARS
from .subprocess_tools import _run_subprocess_streaming

logger = logging.getLogger(__name__)

# Defaults are overridable via container env so the operator can tune them
# without a code change.
DEFAULT_MODEL = os.getenv("CLAUDE_DELEGATE_MODEL", "kimi-k2.7-code:cloud")
DELEGATE_TIMEOUT = int(os.getenv("CLAUDE_DELEGATE_TIMEOUT", str(20 * 60)))  # 20 min hard cap
# "ollama-launch" (default): use Ollama's first-party `ollama launch claude`
# integration, which wires the Anthropic-compatible backend for us.
# "direct": run `claude` directly with ANTHROPIC_* env pointing at the daemon.
DELEGATE_VIA = os.getenv("CLAUDE_DELEGATE_VIA", "ollama-launch").strip().lower()

OLLAMA_BIND_HOST = "127.0.0.1"
OLLAMA_PORT = int(os.getenv("OLLAMA_DELEGATE_PORT", "11434"))
OLLAMA_URL = f"http://{OLLAMA_BIND_HOST}:{OLLAMA_PORT}"
BUILDS_SUBDIR = "claude_builds"

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_subdir(name: str) -> str:
    """Sanitise a caller-supplied folder name: basename only, no traversal."""
    name = (name or "").strip().strip("/")
    name = name.rsplit("/", 1)[-1]            # drop any path components
    name = _UNSAFE.sub("-", name).strip("-.")  # only [A-Za-z0-9._-]
    return (name or "build")[:64]


def _list_files(root: str, cap: int = 300) -> list:
    out = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for fn in filenames:
            out.append(os.path.relpath(os.path.join(dirpath, fn), root))
            if len(out) >= cap:
                return out
    return out


async def _port_open() -> bool:
    try:
        fut = asyncio.open_connection(OLLAMA_BIND_HOST, OLLAMA_PORT)
        _reader, writer = await asyncio.wait_for(fut, timeout=2)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return True
    except Exception:
        return False


async def _ensure_ollama(env: dict):
    """Make sure a local ollama daemon is listening. Return None on success,
    or a human-readable error string on failure."""
    if await _port_open():
        return None
    try:
        # Detached: the daemon must outlive this tool call. It reads
        # OLLAMA_API_KEY from env to authenticate `:cloud` models upstream.
        await asyncio.create_subprocess_exec(
            "ollama", "serve",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            env=env,
            start_new_session=True,
        )
    except FileNotFoundError:
        return ("ollama is not installed in this container. Rebuild the image with "
                "the INSTALL_CLAUDE=true build arg (see docker-compose.override.yml), "
                "then recreate the container.")
    except Exception as exc:  # noqa: BLE001
        return f"failed to start ollama daemon: {exc}"
    for _ in range(40):  # up to ~20s
        await asyncio.sleep(0.5)
        if await _port_open():
            return None
    return "ollama daemon did not become ready within 20s."


class DelegateToClaudeCodeTool:
    """Run a headless Claude Code session (Ollama-backed) on a coding task."""

    async def execute(self, content: str, ctx: dict) -> dict:
        from src.tool_execution import _AGENT_WORKDIR, _truncate
        progress_cb = ctx.get("progress_cb")

        # ---- parse args (JSON object from function-calling, or a bare task) --
        task = subdir = model = None
        raw = (content or "").strip()
        if raw.startswith("{"):
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    task = parsed.get("task") or parsed.get("prompt")
                    subdir = parsed.get("subdir") or parsed.get("dir")
                    model = parsed.get("model")
            except json.JSONDecodeError:
                pass
        if task is None:
            task = raw  # whole string is the task (XML/text-tag invocation)
        task = (task or "").strip()
        if not task:
            return {"error": "delegate_to_claude_code: empty task", "exit_code": 1}

        model = (model or DEFAULT_MODEL).strip()
        subdir = _safe_subdir(subdir or "build")

        # ---- auth precheck: cloud models need EITHER an API key OR an existing
        #      `ollama signin` keypair bridged into the daemon's home dir --------
        api_key = os.getenv("OLLAMA_API_KEY", "")
        ollama_home = os.path.join(os.path.expanduser("~"), ".ollama")
        signed_in = os.path.exists(os.path.join(ollama_home, "id_ed25519"))
        if not api_key and not signed_in and model.endswith(":cloud"):
            return {"error": ("No Ollama Cloud auth in the container. Either set OLLAMA_API_KEY "
                              "(ollama.com/settings/keys) in .env, or bridge an `ollama signin` "
                              f"keypair into {ollama_home}/ (id_ed25519 + id_ed25519.pub). "
                              f"The cloud model '{model}' is unreachable until then."),
                    "exit_code": 1}

        # ---- confined build workspace (bind-mounted to host ./data) ---------
        build_dir = os.path.join(_AGENT_WORKDIR, BUILDS_SUBDIR, subdir)
        os.makedirs(build_dir, exist_ok=True)

        # ---- env for daemon + claude ---------------------------------------
        # Build a MINIMAL, allowlisted env rather than inheriting the full
        # process environment. The delegated Claude Code runs with
        # bypassPermissions, so handing it ctx["subproc_env"]/os.environ would
        # expose the internal-tool loopback token and every provider/DB/SMTP/
        # integration secret to a child that can read its own environment. Copy
        # only what ollama+claude+node need; OLLAMA_API_KEY is set explicitly.
        _SAFE_ENV_KEYS = (
            "PATH", "HOME", "USER", "LOGNAME", "LANG", "TERM", "TZ", "TMPDIR",
            "SHELL", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME",
            "NODE_EXTRA_CA_CERTS", "SSL_CERT_FILE", "SSL_CERT_DIR",
        )
        env = {k: os.environ[k] for k in _SAFE_ENV_KEYS if k in os.environ}
        # Non-secret OLLAMA_* runtime config (host, models dir, …); the API key
        # is injected separately below so it isn't pulled in by the prefix copy.
        for _k, _v in os.environ.items():
            if _k.startswith("OLLAMA_") and _k != "OLLAMA_API_KEY":
                env[_k] = _v
        env["OLLAMA_API_KEY"] = api_key
        # Keep Claude Code non-chatty/non-self-updating in a container.
        env.setdefault("DISABLE_AUTOUPDATER", "1")
        env.setdefault("DISABLE_TELEMETRY", "1")
        env.setdefault("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", "1")
        if DELEGATE_VIA == "direct":
            # Point Claude Code straight at the Ollama daemon's
            # Anthropic-compatible endpoint (Ollama >= 0.14).
            env["ANTHROPIC_BASE_URL"] = OLLAMA_URL
            env["ANTHROPIC_AUTH_TOKEN"] = "ollama"
            env["ANTHROPIC_MODEL"] = model

        ensure_err = await _ensure_ollama(env)
        if ensure_err:
            return {"error": ensure_err, "exit_code": 1}

        before = set(_list_files(build_dir))

        # ---- build the invocation ------------------------------------------
        claude_args = [
            "-p", task,
            # Headless autonomy. Configurable for hardening — e.g. set
            # CLAUDE_DELEGATE_PERMISSION_MODE=acceptEdits to drop bash autonomy
            # (auto-applies file edits but won't run arbitrary shell unattended).
            "--permission-mode", os.getenv("CLAUDE_DELEGATE_PERMISSION_MODE", "bypassPermissions"),
            "--output-format", "text",
            "--add-dir", build_dir,
        ]
        if DELEGATE_VIA == "direct":
            cmd = ["claude", "--model", model, *claude_args]
        else:
            # First-party Ollama integration wires the backend for this
            # claude version; everything after `--` is passed to claude.
            cmd = ["ollama", "launch", "claude", "--model", model, "-y", "--", *claude_args]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                cwd=build_dir,
                # Own session/process-group so a timeout can SIGKILL the whole
                # tree (ollama launch -> claude -> node -> shells). Without this
                # the wall-clock cap kills only the wrapper PID and the real
                # coding model keeps running (and billing) as an orphan.
                start_new_session=True,
            )
        except FileNotFoundError as exc:
            return {"error": (f"delegate_to_claude_code: '{cmd[0]}' not found in container "
                              f"({exc}). Rebuild with INSTALL_CLAUDE=true."),
                    "exit_code": 1}

        stdout, stderr, rc, timed_out = await _run_subprocess_streaming(
            proc, timeout=DELEGATE_TIMEOUT, progress_cb=progress_cb,
            kill_process_group=True,
        )

        created = sorted(set(_list_files(build_dir)) - before)
        # /app/data/... is bind-mounted to ./data/... on the host.
        host_hint = build_dir.replace("/app/data", "./data", 1)

        if timed_out:
            return {"error": f"delegate_to_claude_code: timed out after {DELEGATE_TIMEOUT}s — killed",
                    "exit_code": 124, "model": model, "dir": host_hint,
                    "files_created": created,
                    "output": _truncate(stdout, MAX_OUTPUT_CHARS)}

        out = stdout.rstrip()
        err = stderr.rstrip()
        if err and (rc not in (0, None)):
            out = (out + "\nSTDERR: " + err).strip() if out else "STDERR: " + err
        return {
            "output": _truncate(out, MAX_OUTPUT_CHARS) or "(no output)",
            "exit_code": rc or 0,
            "model": model,
            "dir": host_hint,
            "files_created": created,
        }
