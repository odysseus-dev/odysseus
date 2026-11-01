import asyncio
import ast
import logging
import os
import re
import shlex
import secrets
import shutil
import subprocess
import sys
import time
import json
from typing import Optional
from urllib.parse import urlparse, urlsplit, urlunsplit

import httpx

from src import containment
from src.constants import AGENT_ISOLATED_TMP_DIRNAME, MAX_OUTPUT_CHARS, WORKSPACE_MOUNT

logger = logging.getLogger(__name__)

# Agent shell calls must fail fast enough for the loop to recover and choose a
# better tool.  A one-hour default can pin an entire benchmark worker on an
# accidental recursive scan, even though ordinary artifact commands complete
# in seconds.  Long-running work belongs in manage_bg_jobs.
DEFAULT_BASH_TIMEOUT = 120
DEFAULT_PYTHON_TIMEOUT = 60 * 60

_HOST_SHELL_BRIDGE_HOSTS = {"127.0.0.1", "localhost", "::1", "host.docker.internal"}
IS_WINDOWS = sys.platform.startswith("win")
_HOST_SHELL_CANCEL_TASKS: set[asyncio.Task] = set()


def _ffmpeg_unicode_drawtext_needs_fontfile(command: str) -> bool:
    """Require a deliberate font for non-ASCII text rendered by ffmpeg.

    Fontconfig's fallback is platform-dependent and commonly resolves to a
    font without the requested glyphs.  An explicit ``fontfile`` makes the
    rendered artifact portable and prevents successful commands that produce
    tofu boxes instead of text.
    """
    text = str(command or "")
    lowered = text.lower()
    return (
        bool(re.search(r"\bffmpeg\b", lowered))
        and "drawtext" in lowered
        and "fontfile" not in lowered
        and any(ord(char) > 127 for char in text)
    )


def _resolve_fontfile_for_text(text: str) -> str:
    """Resolve a host font covering the first requested non-ASCII codepoint."""
    codepoint = next((ord(char) for char in str(text or "") if ord(char) > 127), None)
    matcher = shutil.which("fc-match")
    if codepoint is None or not matcher:
        return ""
    try:
        completed = subprocess.run(
            [matcher, "-f", "%{file}", f":charset={codepoint:04x}"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    candidate = str(completed.stdout or "").strip().splitlines()[0:1]
    if completed.returncode != 0 or not candidate:
        return ""
    path = candidate[0].strip()
    return path if os.path.isfile(path) else ""


async def _cancel_host_shell_bridge_request(
    url: str, token: str, request_id: str,
) -> None:
    if not token or not is_host_shell_bridge_url_allowed(url):
        return
    target = host_shell_bridge_endpoint_url(url, "/cancel")
    try:
        timeout = httpx.Timeout(5.0, connect=2.0, write=2.0, pool=2.0)
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            await client.post(
                target,
                json={"request_id": request_id},
                headers={"X-Odysseus-TUI-Bridge-Token": token},
            )
    except Exception:
        pass


def find_bash() -> Optional[str]:
    """Find a real Bash executable for native Windows agent runs."""
    candidates = [
        shutil.which("bash"),
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files (x86)\Git\bin\bash.exe",
    ]
    return next((path for path in candidates if path and os.path.isfile(path)), None)


async def _create_bash_subprocess(
    command: str,
    *,
    cwd: Optional[str] = None,
    env: Optional[dict] = None,
):
    """Create Bash structurally, avoiding cmd.exe and stray Windows tmux."""
    if IS_WINDOWS:
        bash = find_bash()
        if not bash:
            raise RuntimeError(
                "Git Bash is required for the Bash tool on Windows; install Git for Windows."
            )
        return await asyncio.create_subprocess_exec(
            bash,
            "-c",
            str(command or ""),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            cwd=cwd,
        )
    kwargs = {"cwd": cwd} if cwd is not None else {}
    return await asyncio.create_subprocess_shell(command, **kwargs)


def _host_shell_requires_detach(command: str) -> bool:
    """Recognize commands that must not block an interactive agent turn.

    Models occasionally omit ``detach`` even after the host-shell contract
    tells them to poll long jobs. Keep the normal synchronous path for short
    commands, but make explicit background markers and clearly long sleeps
    deterministic so the bridge returns a job id instead of holding the SSE
    stream open.
    """
    text = str(command or "").strip()
    if not text:
        return False
    first = next((line.strip().lower() for line in text.splitlines() if line.strip()), "")
    if first in {"#!bg", "#bg", "# bg", "#background", "# background", "@background", "# @background"}:
        return True
    match = re.search(r"\bsleep\s+(\d+(?:\.\d+)?)\b", text, re.IGNORECASE)
    if match:
        try:
            return float(match.group(1)) >= 20
        except ValueError:
            return False
    return False


def _host_shell_should_auto_poll(command: str) -> bool:
    """Poll implicit long-sleep jobs so a false completion cannot escape."""
    text = str(command or "").lower()
    if not _host_shell_requires_detach(command):
        return False
    return not any(
        marker in text
        for marker in ("#!bg", "#bg", "# bg", "#background", "# background", "@background")
    )


def _docker_default_gateway_ips() -> set[str]:
    gateways: set[str] = set()
    try:
        with open("/proc/net/route", "r", encoding="utf-8", errors="ignore") as fh:
            for line in fh.readlines()[1:]:
                parts = line.split()
                if len(parts) < 3 or parts[1] != "00000000":
                    continue
                raw = parts[2]
                if len(raw) != 8:
                    continue
                octets = [str(int(raw[i:i + 2], 16)) for i in range(6, -1, -2)]
                gateways.add(".".join(octets))
    except Exception:
        return set()
    return gateways


def _is_private_bridge_ip(host: str) -> bool:
    """LAN + CGNAT/Tailscale (100.64.0.0/10) literal IPs — the ranges a remote
    TUI legitimately advertises when the backend is reachable over the LAN or
    Tailscale. The 172.16/12 docker-private range is deliberately EXCLUDED:
    on a container host those addresses are neighboring containers, not the
    TUI — only the actual default gateway (checked separately) is trusted."""
    parts = host.split(".")
    if len(parts) != 4 or not all(p.isdigit() and 0 <= int(p) <= 255 for p in parts):
        return False
    a, b = int(parts[0]), int(parts[1])
    if a == 10:
        return True
    if a == 192 and b == 168:
        return True
    if a == 100 and 64 <= b <= 127:
        return True
    return False


def is_host_shell_bridge_url_allowed(url: str) -> bool:
    parsed = urlparse(str(url or "").strip())
    host = (parsed.hostname or "").strip().lower().rstrip(".")
    if parsed.scheme != "http" or not parsed.netloc or parsed.username or parsed.password:
        return False
    if (
        host not in _HOST_SHELL_BRIDGE_HOSTS
        and host not in _docker_default_gateway_ips()
        and not _is_private_bridge_ip(host)
    ):
        return False
    if parsed.path not in ("", "/run"):
        return False
    if parsed.query or parsed.fragment:
        return False
    return True


def host_shell_bridge_endpoint_url(url: str, path: str) -> str:
    """Replace a validated bridge URL's path without changing its authority."""
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _replace_workspace_alias(content: str, cwd: str) -> str:
    """Map virtual /workspace paths without corrupting absolute host paths."""
    return re.sub(
        r"(^|[\s'\"=:(\[,])/workspace(?=$|[/\s'\"`),;\]])",
        lambda match: match.group(1) + cwd,
        str(content or ""),
    )


#: Roots the namespace argv mounts itself. A host path under one of these is
#: already reachable inside the namespace, so it needs no bind and must not get
#: a ``--dir`` chain: mkdir inside a read-only bind fails and takes the whole
#: namespace with it.
_NAMESPACE_MOUNTED_ROOTS = ("/usr", "/home", "/mnt")

#: Destinations a bind must never overlay. Replacing the private root, the
#: private /tmp or the workspace mount with a host directory undoes the
#: namespace from inside the argv that builds it.
_NAMESPACE_RESERVED_DESTS = frozenset({
    "/", "/tmp", "/var", "/opt", "/etc", WORKSPACE_MOUNT,
    "/root", "/run", "/proc", "/dev", "/sys", *_NAMESPACE_MOUNTED_ROOTS,
})


def _namespace_visible_without_bind(path: str) -> bool:
    """True when ``path`` is already reachable through a root the argv mounts."""
    return any(
        path == root or path.startswith(root + os.sep)
        for root in _NAMESPACE_MOUNTED_ROOTS
    )


def _namespace_dir_chain(path: str) -> list[str]:
    """``--dir`` args for every ancestor of ``path`` the argv has to create.

    bwrap mounts into a tmpfs root, so a bind destination's parents have to
    exist before the bind. Returns nothing when the parents already exist by
    virtue of a mount the argv made — creating a directory inside a read-only
    bind is an error, not a no-op.
    """
    if _namespace_visible_without_bind(path):
        return []
    parents: list[str] = []
    parent = os.path.dirname(path)
    while parent not in ("/", "", "/tmp", "/etc", WORKSPACE_MOUNT, *_NAMESPACE_MOUNTED_ROOTS):
        parents.append(parent)
        parent = os.path.dirname(parent)
    args: list[str] = []
    for directory in reversed(parents):
        args.extend(("--dir", directory))
    return args


def _isolated_tmp_dir(cwd: str) -> str:
    """The workspace-local stand-in for the host ``/tmp``.

    Creation is best-effort: the source tree is read-only in Docker and a
    workspace can be mounted read-only, and a command that mentions ``/tmp/``
    must not die with an OSError traceback because a scratch directory could
    not be made. The rewrite still points at the workspace, so a command that
    really needs to write there fails on its own terms, inside the boundary,
    with its own error message.
    """
    path = os.path.join(cwd, AGENT_ISOLATED_TMP_DIRNAME)
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        pass
    return path


def _execution_boundary(
    cwd: str, *, wall_clock_s: int = DEFAULT_BASH_TIMEOUT,
) -> "containment.ContainmentProbe":
    """What this host can actually enforce for an agent command in ``cwd``.

    The single place the shell and Python tools ask. Both used to decide for
    themselves, by testing whether a namespace wrapper came back non-None, and
    both then fell through to a regex if it had not — so "was that command
    confined" had no answer and no field in the result. Routing the question
    through :mod:`src.containment` means one mechanism table, one answer, and a
    ``containment`` block in the tool result either way.

    ``network`` is left inherited on purpose: ``--unshare-net`` was measured to
    cut the loopback sidecars this product depends on (ChromaDB on 8100), and
    the Dockerfile installs ``nmap``/``iproute2``/``dnsutils`` because
    Docker-hosted agents are expected to do LAN work. It is a reported
    dimension here, not an enforced one.
    """
    try:
        return containment.probe(
            containment.agent_spec(
                workspace=cwd,
                env={},
                wall_clock_s=wall_clock_s,
                max_output_bytes=MAX_OUTPUT_CHARS,
            )
        )
    except ValueError as exc:
        # A workspace that is not a usable directory is a caller bug to
        # containment, which raises rather than reporting. Here it must not
        # take out the tool, and it is still a containment failure: nothing can
        # be confined to a directory that is not there. Fail closed. The reason
        # goes in the message rather than a traceback -- this is a known shape,
        # not an unexpected exception.
        logger.warning(
            "execution boundary: cannot probe containment for workspace %r (%s); "
            "treating every required dimension as unenforced",
            cwd, exc,
        )
        return containment.ContainmentProbe(
            mechanism="none",
            enforced=frozenset(),
            degraded=(),
            unenforced_required=tuple(sorted(containment.DEFAULT_REQUIRED)),
            mode=containment.CONTAINMENT_MODE,
        )


#: What the fallback actually is, named so it cannot be mistaken for a
#: mechanism. ``_replace_workspace_alias`` rewrites the literal token
#: ``/workspace`` to the real path in the command string; a command that never
#: mentions ``/workspace`` is untouched by it and runs on the host unrestricted.
ALIAS_REWRITE_MECHANISM = "workspace_alias_rewrite"

#: Guards the one-per-process fallback warning below. Module state, because the
#: fact it reports is a property of the host rather than of a command.
_ALIAS_FALLBACK_LOGGED = False


def _filesystem_boundary_block(mechanism: str, mode: str, *, confined: bool) -> dict:
    """The ``containment`` block for a spawn these tools still build themselves.

    Reports the **filesystem dimension only**, deliberately. The probe knows
    this host could also give a process group and a real wall clock, but
    Compatibility namespace previews assemble their own ``create_subprocess_*``
    call and pass neither ``start_new_session`` nor a group-wide kill, so
    listing those dimensions here would be the false claim
    :mod:`src.containment` calls worse than an honest absence. They arrive when
    this spawn path moves onto :func:`containment.run`, not before.
    """
    return {
        "mechanism": mechanism,
        "mode": mode,
        "enforced": [containment.FILESYSTEM] if confined else [],
        "unenforced_required": [] if confined else [containment.FILESYSTEM],
        "contained": confined,
        "executed": True,
        # Names the scope of the claim, so "process_tree is absent from
        # enforced" reads as "not reported here" rather than "not enforced".
        "reported_dimensions": [containment.FILESYSTEM],
    }


def _contained_command(
    content: str,
    cwd: str,
    *,
    chdir: str = WORKSPACE_MOUNT,
    interpreter_prefix: str | None = None,
) -> tuple[str, dict, bool]:
    """Resolve ``content`` into the strongest form this host can run.

    Returns ``(command, containment_block, confined)``. The caller spawns
    ``command``, copies ``containment_block`` into its result verbatim, and
    refuses instead when ``confined`` is false under enforcing mode.

    This replaces ``namespaced or _replace_workspace_alias(...)``, the line this
    ticket exists to delete. The two branches it chose between are not
    comparable — one is a mount namespace, the other is a regex — and choosing
    the second silently means an uncontained host execution reads in the
    transcript exactly like a contained one. The fallback still happens under
    an explicit report-only diagnostic mode; the difference is that it is now
    recorded in the result.

    :raises containment.ContainmentUnavailable: filesystem containment could
        not be established and the mode is enforcing. The command is not run.
    """
    probe = _execution_boundary(cwd)
    wrapped = _wrap_workspace_namespace(
        content, cwd, chdir=chdir, interpreter_prefix=interpreter_prefix,
    )
    # The probe's filesystem answer and the wrapper's None/not-None answer rest
    # on the same functional namespace probe, so they agree
    # by construction. `wrapped` is still what decides, because it is what
    # actually runs: a probe that said yes to a wrapper that declined would be
    # the same false claim in the other direction.
    if wrapped is not None:
        return wrapped, _filesystem_boundary_block(
            probe.mechanism, probe.mode, confined=True,
        ), True
    if probe.mode == containment.MODE_ENFORCING:
        raise containment.ContainmentUnavailable(
            frozenset({containment.FILESYSTEM}), ALIAS_REWRITE_MECHANISM,
        )
    # Once per process, not once per command. The host's ability to establish a
    # namespace does not change between calls, so a per-call warning would
    # drown the log on every macOS install while adding nothing — and the
    # per-call fact is already in the result block, which is where a reader
    # looking at one command will look.
    global _ALIAS_FALLBACK_LOGGED
    if not _ALIAS_FALLBACK_LOGGED:
        _ALIAS_FALLBACK_LOGGED = True
        logger.warning(
            "execution boundary: no filesystem containment is available on this "
            "host (mechanism %r); agent commands fall back to the %s, which is "
            "a path rewrite and not a boundary. Reported per command in the "
            "result's containment block.",
            probe.mechanism, ALIAS_REWRITE_MECHANISM,
        )
    return (
        _replace_workspace_alias(content, cwd),
        _filesystem_boundary_block(
            ALIAS_REWRITE_MECHANISM, probe.mode, confined=False,
        ),
        False,
    )


def _wrap_workspace_namespace(
    content: str,
    cwd: str,
    *,
    chdir: str = WORKSPACE_MOUNT,
    interpreter_prefix: str | None = None,
) -> str | None:
    """Run a shell command with the active workspace mounted at /workspace.

    Rewriting the command line alone is insufficient when a generated Python
    script itself contains paths such as ``/workspace/chart.png``.  A small
    bubblewrap namespace preserves that public contract for each concurrent
    agent without creating a process-global /workspace symlink.
    """
    if IS_WINDOWS or not containment._bwrap_available():
        return None
    readonly = [path for path in ("/home", "/mnt") if os.path.isdir(path)]
    # setup-python installs interpreters under /opt, and local CI virtualenvs
    # can live under /tmp. Those paths are hidden by the private root/tmpfs.
    # Expose only the active interpreter environment, read-only, so Python
    # tools keep their installed packages without exposing the host /tmp.
    if interpreter_prefix:
        prefix = os.path.abspath(interpreter_prefix)
        resolved_prefix = os.path.realpath(prefix)
        already_visible = _namespace_visible_without_bind(prefix)
        # A prefix is trusted only when it names a specific interpreter tree.
        # In particular, never overlay the private root, tmpfs, or workspace
        # with a broad host directory. Reject symlinked prefixes too: bwrap
        # would otherwise bind the resolved source at a different destination.
        has_environment_layout = (
            os.path.isfile(os.path.join(prefix, "pyvenv.cfg"))
            or (
                os.path.isfile(os.path.join(prefix, "bin", "python"))
                and os.path.isdir(os.path.join(
                    prefix, "lib", f"python{sys.version_info.major}.{sys.version_info.minor}",
                ))
            )
        )
        if (
            not already_visible
            and prefix == resolved_prefix
            and prefix not in _NAMESPACE_RESERVED_DESTS
            and len(prefix.split(os.sep)) >= 3
            and os.path.isdir(prefix)
            and has_environment_layout
        ):
            readonly.append(prefix)
    spec = containment.ContainmentSpec(
        workspace=cwd, env={}, wall_clock_s=DEFAULT_BASH_TIMEOUT,
        readonly_extra=tuple(readonly),
    )
    args = containment._bwrap_prefix(spec)
    args[-1] = chdir
    args.extend(("/bin/bash", "-lc", content))
    return shlex.join(args)


def _owned_spec(cwd: str, env: Optional[dict], timeout: int, readonly_extra: tuple = ()) -> containment.ContainmentSpec:
    """Server-defined boundary shared by the native execution tools."""
    readonly = []
    for prefix in (sys.prefix, sys.base_prefix):
        prefix = os.path.realpath(prefix)
        visible = any(prefix == root or prefix.startswith(root + os.sep) for root in ("/usr", "/etc"))
        if not visible and prefix not in _NAMESPACE_RESERVED_DESTS:
            readonly.append(prefix)
    from src.tool_execution import _agent_subprocess_env
    clean_env = _agent_subprocess_env() if env is None else dict(env)
    return containment.agent_spec(
        cwd, clean_env, timeout,
        readonly_extra=tuple(dict.fromkeys([*readonly, *readonly_extra])),
    )


async def _run_owned_command(command, ctx: dict, *, tool: str, timeout: int, argv: bool = False,
                             readonly_extra: tuple = ()) -> dict:
    from src.tool_execution import agent_cwd, _truncate

    grant = None
    launch = None
    result = None
    try:
        from src.agent_runtime.process_resources import require_launch, publish_launch, validate_launch_spec
        from src.agent_runtime.authority import active_request_authority
        launch = require_launch(tool, cwd=agent_cwd())
        authority = active_request_authority()
        if (str(ctx.get("owner") or "").strip().casefold(), str(ctx.get("session_id") or "")) != (
                authority.owner, authority.session_id):
            raise ValueError("Native producer owner or session changed")
        spec = _owned_spec(agent_cwd(), ctx.get("subproc_env"), timeout, readonly_extra)
        validate_launch_spec(launch, spec)
        grant = containment.acquire(
            spec,
            owner=str(ctx.get("session_id") or ctx.get("owner") or tool),
        )
        containment._update_record(grant.id, launch_generation=launch.generation)
        publish_launch(launch, authority, grant.id)
        if containment.FILESYSTEM not in grant.enforced:
            if argv:
                command = [*command[:-1], _replace_workspace_alias(command[-1], grant.workspace)]
            else:
                command = _replace_workspace_alias(command, grant.workspace)
        result = await containment.run(grant, command, argv=argv, progress_cb=ctx.get("progress_cb"))
        from src.agent_runtime.process_resources import attach_containment_processes
        attach_containment_processes(launch, grant.id)
    except containment.ContainmentUnavailable as exc:
        return containment.unavailable_tool_result(exc, tool=tool)
    except (OSError, RuntimeError, ValueError) as exc:
        if grant is not None:
            record = containment._load_records().get(grant.id, {})
            if not record.get("pid") and not record.get("release"):
                containment.release(grant, grace_s=0)
        boundary = result.grant.to_dict() if result is not None else grant.to_dict() if grant else {}
        boundary["executed"] = result is not None or bool(getattr(exc, "containment_executed", False))
        if result is None and not getattr(exc, "containment_established", False):
            boundary.update(contained=False, enforced=[])
        return {"error": f"{tool}: execution failed: {exc}", "exit_code": 1,
                "containment": boundary,
                **({"failure_kind": "resource_linkage_unavailable",
                    "teardown": result.release.to_dict() if result.release else {"dead": False}}
                   if result is not None else {})}
    finally:
        if launch is not None and grant is not None:
            record = containment._load_records().get(grant.id, {})
            if (record.get("launch_generation") == launch.generation
                    and (record.get("release") or {}).get("dead") is True):
                from src.agent_runtime.process_resources import retire_launch
                try:
                    retire_launch(launch, grant.id)
                except (OSError, ValueError, TypeError):
                    logger.warning("Foreground launch publication retirement failed", exc_info=True)

    boundary = result.grant.to_dict()
    boundary["executed"] = True
    teardown = result.release.to_dict() if result.release else {"dead": False}
    output = result.stdout.rstrip()
    if result.stderr.rstrip():
        output = (output + "\nSTDERR: " + result.stderr.rstrip()).strip()
    truncated = result.output_truncated or len(output) > MAX_OUTPUT_CHARS
    capture_note = " Captured output was truncated." if truncated else ""
    common = {"containment": boundary, "teardown": teardown, "output_truncated": truncated}
    if not teardown["dead"]:
        return {**common, "error": f"{tool}: process teardown could not verify death.{capture_note}",
                "failure_kind": "process_teardown_failed", "exit_code": 1,
                "stdout": _truncate(result.stdout, MAX_OUTPUT_CHARS),
                "stderr": _truncate(result.stderr, MAX_OUTPUT_CHARS)}
    if result.timed_out:
        return {**common, "error": f"{tool}: timed out after {timeout}s; process tree terminated.{capture_note}",
                "exit_code": 124, "timed_out": True, "stdout": _truncate(result.stdout, MAX_OUTPUT_CHARS),
                "stderr": _truncate(result.stderr, MAX_OUTPUT_CHARS)}
    if tool == "python":
        child_failure = _python_child_runtime_failure(result.stdout, result.stderr, result.exit_code)
        if child_failure:
            return {**common, "error": _truncate("python: a child operation failed despite a zero Python exit status:\n" + child_failure, MAX_OUTPUT_CHARS),
                    "exit_code": 1, "stderr": _truncate(result.stderr, MAX_OUTPUT_CHARS)}
    if truncated:
        note = "\n…[output truncated by containment capture limit]…"
        output = output[:MAX_OUTPUT_CHARS - len(note)] + note
    return {**common, "output": _truncate(output, MAX_OUTPUT_CHARS) or "(no output)",
            "exit_code": result.exit_code if result.exit_code is not None else 1}


class BashTool:
    async def execute(self, content: str, ctx: dict) -> dict:
        from src.tool_execution import agent_cwd, _truncate
        if isinstance(content, dict):
            content = str(content.get("command") or content.get("cmd") or content.get("code") or "")
        content = str(content or "").strip()
        if not content:
            return {
                "error": "bash: command is required; no command was executed",
                "exit_code": 1,
            }
        if re.search(r"(?:^|[;&|]\s*)sudo\b|^\s*sudo\b", content, re.IGNORECASE):
            return {
                "error": "bash: sudo/privilege escalation is unavailable in agent execution",
                "exit_code": 1,
            }
        if re.search(r"\b(?:curl|wget)\b[^\n]*https?://", content, re.IGNORECASE):
            return {
                "error": (
                    "bash: ad-hoc HTTP downloads are disabled when native web tools are "
                    "available. Use pdf_extract for online PDFs, web_fetch for a concrete "
                    "page, or web_search for discovery. For PDF extraction "
                    "tasks, treat pdf_extract as the download+scan step: extract the "
                    "requested values, then create the requested output artifacts directly "
                    "from that evidence instead of trying curl/wget again."
                ),
                "exit_code": 1,
            }
        from src.agent_runtime.process_resources import require_launch
        from src.agent_runtime.resources import ResourceIdentityError
        try:
            require_launch("bash", cwd=agent_cwd(), content=content)
        except ResourceIdentityError as error:
            return {"error": str(error), "exit_code": 1, "blocked": True, "failure_kind": "resource_identity_denied"}
        if _ffmpeg_unicode_drawtext_needs_fontfile(content):
            resolved_font = _resolve_fontfile_for_text(content)
            resolved_hint = (
                f" Host fontconfig resolved a covering font at `{resolved_font}`; "
                f"pass `fontfile={resolved_font}`."
                if resolved_font
                else ""
            )
            return {
                "error": (
                    "bash: ffmpeg drawtext with non-ASCII text requires an explicit "
                    "fontfile to avoid missing-glyph boxes."
                    + resolved_hint
                    + " If needed, resolve another suitable installed font with "
                    "`fc-match -f '%{file}' ':charset=<hex-codepoint>'`, then pass that "
                    "path as `drawtext=fontfile=...` and rerun the command."
                ),
                "exit_code": 1,
            }
        if "/tmp/" in content:
            isolated_tmp = _isolated_tmp_dir(agent_cwd())
            content = content.replace("/tmp/", isolated_tmp.rstrip("/") + "/")
        return await _run_owned_command(content, ctx, tool="bash", timeout=DEFAULT_BASH_TIMEOUT)

class HostShellTool:
    async def execute(self, content: str, ctx: dict) -> dict:
        from src.tool_execution import _truncate

        try:
            args = json.loads(content) if str(content or "").strip().startswith("{") else {}
        except Exception:
            args = {}
        command = str(
            args.get("command")
            or args.get("cmd")
            or (content if not args else "")
            or ""
        ).strip()
        runtime = ctx.get("client_runtime_context")
        if not isinstance(runtime, dict):
            return {"error": "host_shell: no TUI host bridge advertised", "exit_code": 1}
        bridge = runtime.get("host_shell_bridge") or runtime.get("hostShellBridge")
        if not isinstance(bridge, dict):
            return {"error": "host_shell: no TUI host bridge advertised", "exit_code": 1}

        url = str(bridge.get("url") or "").strip()
        token = str(bridge.get("token") or "").strip()
        parsed = urlparse(url)
        if not is_host_shell_bridge_url_allowed(url):
            return {"error": "host_shell: invalid bridge URL", "exit_code": 1}
        if not token:
            return {"error": "host_shell: bridge token missing", "exit_code": 1}
        run_url = host_shell_bridge_endpoint_url(url, "/run")

        job_id = str(args.get("job_id") or "").strip()
        if not command and not job_id:
            return {"error": "host_shell: command or job_id required", "exit_code": 1}

        try:
            requested_timeout = int(args.get("timeout") or 30)
        except Exception:
            requested_timeout = 30
        timeout = max(1, min(requested_timeout, 120))

        from src import containment
        from src.tool_execution import agent_cwd

        sanitized_endpoint = f"{parsed.scheme}://{parsed.netloc}{parsed.path}" if parsed.scheme and parsed.netloc else "host_shell_bridge"
        owner = str(ctx.get("session_id") or ctx.get("owner") or "host_shell")
        spec = _owned_spec(agent_cwd(), ctx.get("subproc_env"), timeout)
        grant = containment.declare_external_bridge(spec, owner=owner, endpoint=sanitized_endpoint)
        boundary = grant.to_dict()
        boundary["executed"] = False

        request_body: dict[str, object] = {"timeout": timeout}
        request_id = ""
        if job_id:
            request_body["job_id"] = job_id
        else:
            request_body["command"] = command
            if bool(args.get("detach")) or _host_shell_requires_detach(command):
                request_body["detach"] = True
            else:
                request_id = secrets.token_urlsafe(18)
                request_body["request_id"] = request_id

        try:
            async with httpx.AsyncClient(timeout=timeout + 5, trust_env=False) as client:
                resp = await client.post(
                    run_url,
                    json=request_body,
                    headers={"X-Odysseus-TUI-Bridge-Token": token},
                )
                if resp.status_code >= 400:
                    return {
                        "error": f"host_shell: bridge returned HTTP {resp.status_code}",
                        "exit_code": 1,
                        "host_bridge": "tui",
                        "containment": boundary,
                    }
                data = resp.json()

                # A long command may be detached even when the model omitted
                # the flag. Complete that implicit job at the transport layer
                # so the model cannot report success from a mere start ack.
                if (
                    not job_id
                    and _host_shell_should_auto_poll(command)
                    and isinstance(data, dict)
                    and data.get("job_id")
                    and data.get("status") == "running"
                ):
                    auto_job_id = str(data["job_id"])
                    deadline = time.monotonic() + timeout
                    while time.monotonic() < deadline:
                        await asyncio.sleep(0.25)
                        poll = await client.post(
                            run_url,
                            json={"job_id": auto_job_id},
                            headers={"X-Odysseus-TUI-Bridge-Token": token},
                        )
                        if poll.status_code >= 400:
                            return {
                                "error": f"host_shell: bridge returned HTTP {poll.status_code}",
                                "exit_code": 1,
                                "host_bridge": "tui",
                                "containment": boundary,
                            }
                        data = poll.json()
                        if not isinstance(data, dict):
                            continue
                        # A bridge may briefly lose the job record while its
                        # detached worker is being registered. Keep polling;
                        # do not turn that transient state into exit code 1.
                        if data.get("status") in {"running", "unknown"}:
                            continue
                        if data.get("status") != "running":
                            break
                    if isinstance(data, dict) and data.get("status") in {"running", "unknown"}:
                        data = {
                            **data,
                            "status": "running",
                            "detached": True,
                            "job_id": auto_job_id,
                            "output": "host job still running; poll the returned job_id",
                            "exit_code": 0,
                        }
        except asyncio.CancelledError:
            if request_id:
                task = asyncio.create_task(
                    _cancel_host_shell_bridge_request(url, token, request_id),
                    name=f"cancel-host-shell-{request_id[:24]}",
                )
                _HOST_SHELL_CANCEL_TASKS.add(task)
                task.add_done_callback(_HOST_SHELL_CANCEL_TASKS.discard)
            raise
        except Exception as e:
            return {"error": f"host_shell: bridge call failed: {e}", "exit_code": 1, "containment": boundary}

        if not isinstance(data, dict):
            return {"error": "host_shell: bridge returned invalid payload", "exit_code": 1, "containment": boundary}
        if data.get("error"):
            return {
                "error": _truncate(str(data["error"]), MAX_OUTPUT_CHARS),
                "exit_code": 1,
                "host_bridge": "tui",
                "containment": boundary,
            }
        stdout = str(data.get("stdout") or data.get("output") or "")
        stderr = str(data.get("stderr") or "")
        raw_exit_code = data.get("exit_code")
        if raw_exit_code is None:
            raw_exit_code = data.get("returncode")
        if raw_exit_code is None:
            raw_exit_code = 0
        if isinstance(raw_exit_code, bool) or not isinstance(raw_exit_code, int):
            return {
                "error": "host_shell: bridge returned an invalid exit_code",
                "exit_code": 1,
                "host_bridge": "tui",
                "containment": boundary,
            }
        exit_code = raw_exit_code
        output = stdout.rstrip()
        if stderr.strip():
            output = (output + "\nSTDERR: " + stderr.strip()).strip() if output else "STDERR: " + stderr.strip()
        boundary["executed"] = True
        result = {
            "output": _truncate(output, MAX_OUTPUT_CHARS) or "(no output)",
            "exit_code": exit_code,
            "host_bridge": "tui",
            "containment": boundary,
        }
        for key in ("detached", "job_id", "status", "running", "finished", "cwd"):
            if key in data:
                result[key] = data[key]
        return result

def _python_child_runtime_failure(stdout: str, stderr: str, returncode: int) -> str:
    """Return an unmistakable nested-runtime failure hidden by Python exit 0.

    Libraries such as Pillow may spawn a viewer and then return normally even
    when that child cannot display anything.  Keep this deliberately narrow:
    arbitrary stderr is often a warning and must not turn a successful data
    transformation into a failed tool call.
    """
    if returncode != 0 or str(stdout or "").strip():
        return ""
    err = str(stderr or "").strip()
    if re.search(r"(?im)^xdg-open: no method available for opening\b", err):
        return err
    return ""


def _python_with_visible_final_expression(content: str) -> str:
    """Give the Python tool REPL-like visibility for one final bare value.

    The code still runs once as a normal script. Only a final expression is
    assigned and rendered; explicit print calls and statement-only programs
    retain their historical behavior.
    """
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return content
    if not tree.body or not isinstance(tree.body[-1], ast.Expr):
        return content
    final = tree.body[-1]
    if (
        isinstance(final.value, ast.Call)
        and isinstance(final.value.func, ast.Name)
        and final.value.func.id == "print"
    ):
        return content
    result_name = "__odysseus_final_expression_value__"
    tree.body[-1:] = [
        ast.Assign(targets=[ast.Name(id=result_name, ctx=ast.Store())], value=final.value),
        ast.If(
            test=ast.Compare(
                left=ast.Name(id=result_name, ctx=ast.Load()),
                ops=[ast.IsNot()],
                comparators=[ast.Constant(value=None)],
            ),
            body=[ast.Expr(value=ast.Call(
                func=ast.Name(id="print", ctx=ast.Load()),
                args=[ast.Call(
                    func=ast.Name(id="repr", ctx=ast.Load()),
                    args=[ast.Name(id=result_name, ctx=ast.Load())],
                    keywords=[],
                )],
                keywords=[],
            ))],
            orelse=[],
        ),
    ]
    ast.fix_missing_locations(tree)
    return ast.unparse(tree)


def _python_with_configured_import_paths(content: str, env: dict | None) -> str:
    """Expose only explicitly configured package roots under Python ``-I``."""
    raw = str((env or {}).get("ODYSSEUS_PYTHON_TOOL_SITE_PACKAGES", ""))
    paths = [item for item in raw.split(os.pathsep) if item and os.path.isabs(item)]
    if not paths:
        return content
    return f"import site\n[site.addsitedir(path) for path in {paths!r}]\nexec(compile({content!r}, '<odysseus-python-tool>', 'exec'))"


class PythonTool:
    async def execute(self, content: str, ctx: dict) -> dict:
        from src.tool_execution import agent_cwd, _truncate
        if re.search(
            r"\b(?:requests\.(?:get|post|put|delete|request)|urllib\.request(?:\.\w+)?|httpx\.(?:get|post|request))\s*\(",
            content,
            re.IGNORECASE,
        ) and re.search(r"https?://", content, re.IGNORECASE) or (
            re.search(r"[\"'](?:curl|wget)[\"']", content, re.IGNORECASE)
            and re.search(r"https?://", content, re.IGNORECASE)
        ):
            return {
                "error": (
                    "python: ad-hoc HTTP access is disabled when native web tools are "
                    "available. Use pdf_extract for online PDFs, web_fetch for a concrete "
                    "page, or web_search for discovery. For PDF extraction "
                    "tasks, treat pdf_extract as the download+scan step: extract the "
                    "requested values, then create the requested output artifacts directly "
                    "from that evidence instead of trying requests/urllib again."
                ),
                "exit_code": 1,
            }
        from src.agent_runtime.process_resources import require_launch
        from src.agent_runtime.resources import ResourceIdentityError
        try:
            require_launch("python", cwd=agent_cwd(), content=content)
        except ResourceIdentityError as error:
            return {"error": str(error), "exit_code": 1, "blocked": True, "failure_kind": "resource_identity_denied"}
        if "/tmp/" in content:
            isolated_tmp = _isolated_tmp_dir(agent_cwd())
            content = content.replace("/tmp/", isolated_tmp.rstrip("/") + "/")
        _subproc_env = ctx.get("subproc_env")
        content = _python_with_configured_import_paths(
            _python_with_visible_final_expression(content), _subproc_env
        )
        # All Python code acquires the same server-defined boundary, including
        # arithmetic and ordinary imports. Source text never selects a scope.
        raw_paths = str((_subproc_env or {}).get("ODYSSEUS_PYTHON_TOOL_SITE_PACKAGES", ""))
        roots = tuple(path for path in raw_paths.split(os.pathsep) if path and os.path.isabs(path))
        return await _run_owned_command(
            [sys.executable or "python", "-I", "-c", content], ctx,
            tool="python", timeout=DEFAULT_PYTHON_TIMEOUT, argv=True, readonly_extra=roots,
        )
