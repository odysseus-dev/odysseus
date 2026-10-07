#!/usr/bin/env python3
"""Generate the ODYSSEUS_* configuration reference from the source tree.

The page at website/configuration-reference.md is generated, never hand-edited.
This script walks the Python sources, collects every ODYSSEUS_* environment read
with the default it falls back to and the file it is read in, merges the
hand-written area/audience notes in VARIABLE_NOTES, and writes the Markdown.

    python3 scripts/generate_env_reference.py            # rewrite the page
    python3 scripts/generate_env_reference.py --check     # fail if it is stale
    python3 scripts/generate_env_reference.py --stdout    # print, write nothing
    python3 scripts/generate_env_reference.py --list      # one name per line

Reads are found three ways, because one pattern is not enough:

1. Direct reads: `os.getenv(...)`, and any `.get` / `.setdefault` / `.pop` call
   or subscript load keyed by an `ODYSSEUS_*` literal. The receiver is
   deliberately not required to be `os.environ` - `src/tool_index.py` reads
   through `source = os.environ if environ is None else environ`, and
   `src/agent_tools/web_tools.py` reads from an env mapping passed in as an
   argument. The `ODYSSEUS_` prefix is specific enough that keying anything else
   by one of these names would itself be the bug.
2. Calls to env-reader helpers - any function that passes one of its own
   parameters to an environment read. This is detected, not hardcoded, so a new
   helper is picked up without editing this script. It is what finds the
   read_byte_limit_env family in src/upload_limits.py and the media-ingress
   overrides in src/media_ingress.py, both of which a grep for `os.environ.get(`
   misses entirely.
3. A regex sweep of the raw file text, to catch reads that the AST cannot see -
   notably a read inside a Python snippet that is itself a string literal
   (routes/cookbook_helpers.py builds an Ollama probe script that way).
"""
import argparse
import ast
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = REPO_ROOT / "website" / "configuration-reference.md"
PREFIX = "ODYSSEUS_"

# Source roots walked for reads. Order is irrelevant; results are sorted.
SOURCE_ROOTS = (
    "app.py",
    "launcher.py",
    "setup.py",
    "companion",
    "config",
    "core",
    "integrations",
    "mcp_servers",
    "routes",
    "scripts",
    "services",
    "src",
    "tests",
)

# Mapping methods that read a variable out of an environment-like mapping.
ENVIRON_READERS = ("get", "setdefault", "pop")

# Files whose ODYSSEUS_* text is deliberately not a real read. The generator's
# own test builds a synthetic source tree out of string literals, and the text
# sweep below would otherwise document its fixtures as configuration.
EXCLUDED_FILES = ("tests/test_env_reference.py",)

# Reads the AST walk cannot reach (strings holding generated code) are found by
# this. It allows any quoting and any whitespace, and the lookahead keeps a
# subscript ASSIGNMENT - `os.environ["ODYSSEUS_X"] = "1"` - from counting as a
# read, which the AST pass already excludes by checking the expression context.
TEXT_READ_RE = re.compile(
    r"""(?:os\.)?(?:environ\.(?:get|setdefault|pop)|getenv)\(\s*['"](ODYSSEUS_[A-Z0-9_]+)['"]"""
    r"""|environ\[\s*['"](ODYSSEUS_[A-Z0-9_]+)['"]\s*\](?!\s*=[^=])"""
)

# Markdown table order. A variable whose area is missing from here is a bug in
# VARIABLE_NOTES, and check_notes() reports it.
AREA_ORDER = (
    "Deployment and first run",
    "Data directories and paths",
    "Model routing and providers",
    "Agent loop and tool execution",
    "Browser automation",
    "Container and workspace mounts",
    "Email",
    "Calendar, notes and single-user mode",
    "Upload and media limits",
    "Search",
    "Memory and skills",
    "Speech and vision models",
    "Auth and internal API",
    "Security perimeter",
    "Integrations (Claude, Codex)",
    "Testing, capture and development tooling",
    "Build and release metadata",
)

USER = "user"
INTERNAL = "internal"


@dataclass
class Read:
    """One place a variable is read."""

    path: str
    lineno: int
    how: str
    default: str | None

    @property
    def location(self) -> str:
        return f"{self.path}:{self.lineno}"


@dataclass
class Variable:
    name: str
    reads: list[Read] = field(default_factory=list)

    @property
    def primary(self) -> Read:
        """The read to quote: prefer application code over tooling and tests."""
        return min(self.reads, key=_read_rank)

    @property
    def defaults(self) -> list[str]:
        seen = []
        for read in sorted(self.reads, key=_read_rank):
            shown = read.default if read.default is not None else "unset"
            if shown not in seen:
                seen.append(shown)
        return seen

    @property
    def test_only(self) -> bool:
        return all(read.path.startswith("tests/") for read in self.reads)


def _read_rank(read: "Read") -> tuple[int, int, str, int]:
    """Sort key picking the most informative read first.

    Application code beats tooling beats tests, and within one tier a read that
    carries an explicit default beats one that does not - otherwise the Default
    column quotes a call site that simply has no fallback to report.
    """
    if read.path.startswith("tests/"):
        tier = 3
    elif read.path.startswith("scripts/"):
        tier = 2
    elif read.path.startswith("integrations/"):
        tier = 1
    else:
        tier = 0
    return (tier, 0 if read.default is not None else 1, read.path, read.lineno)


def iter_source_files(repo_root: Path):
    for entry in SOURCE_ROOTS:
        target = repo_root / entry
        if target.is_file():
            candidates = [target]
        elif target.is_dir():
            candidates = sorted(target.rglob("*.py"))
        else:
            continue
        for path in candidates:
            if "__pycache__" in path.parts:
                continue
            if path.relative_to(repo_root).as_posix() in EXCLUDED_FILES:
                continue
            yield path


def _fold(node: ast.AST, constants: dict[str, ast.AST]) -> str:
    """Render a default expression, resolving one level of indirection.

    Names bound to a module-level literal and attributes of a locally
    constructed dataclass are resolved to the literal in the source. Anything
    else is rendered as written, which keeps the table honest about defaults
    that are computed at import time.
    """
    if isinstance(node, ast.Name) and node.id in constants:
        return ast.unparse(constants[node.id])
    if isinstance(node, ast.Attribute):
        resolved = constants.get(f".{node.attr}")
        if resolved is not None:
            return ast.unparse(resolved)
    return ast.unparse(node)


def _collect_constants(tree: ast.Module) -> dict[str, ast.AST]:
    """Module-level `NAME = <expr>` bindings, plus dataclass field defaults.

    Dataclass fields are keyed as `.field_name` so an attribute read on any
    local instance resolves. That is loose, but every collision would have to be
    two dataclasses in one module using one field name with different defaults,
    and check_notes() makes a wrong default visible rather than silent.
    """
    constants: dict[str, ast.AST] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    constants[target.id] = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.value is not None:
                constants[node.target.id] = node.value
        elif isinstance(node, ast.ClassDef):
            for stmt in node.body:
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                    if stmt.value is not None:
                        constants.setdefault(f".{stmt.target.id}", stmt.value)
    return constants


def _env_name(node: ast.AST, constants: dict[str, ast.AST]) -> str | None:
    """The variable name a call/subscript argument refers to, if any."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        bound = constants.get(node.id)
        if isinstance(bound, ast.Constant) and isinstance(bound.value, str):
            return bound.value
    return None


def find_env_reader_helpers(tree: ast.Module) -> dict[str, int]:
    """Functions in this module that read `os.environ` from a parameter.

    Returns helper name -> index of the parameter holding the variable name.
    Nested functions count: src/media_ingress.py defines its reader inside
    limits_from_env().
    """
    helpers: dict[str, int] = {}
    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        params = [arg.arg for arg in func.args.posonlyargs + func.args.args]
        if not params:
            continue
        for node in ast.walk(func):
            arg = None
            if isinstance(node, ast.Call):
                attr = getattr(node.func, "attr", None)
                base = getattr(node.func, "value", None)
                if node.args and (attr in ENVIRON_READERS or attr == "getenv"):
                    arg = node.args[0]
            elif isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load):
                arg = node.slice
            if isinstance(arg, ast.Name) and arg.id in params:
                helpers[func.name] = params.index(arg.id)
                break
    return helpers


def scan_file(path: Path, repo_root: Path) -> list[tuple[str, Read]]:
    """Every ODYSSEUS_* read in one file."""
    rel = path.relative_to(repo_root).as_posix()
    text = path.read_text(encoding="utf-8", errors="replace")
    if PREFIX not in text:
        return []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []

    constants = _collect_constants(tree)
    helpers = find_env_reader_helpers(tree)
    results: list[tuple[str, Read]] = []
    seen_by_ast: set[str] = set()

    for node in ast.walk(tree):
        name = None
        how = None
        default_node = None

        if isinstance(node, ast.Call):
            func = node.func
            attr = getattr(func, "attr", None)
            base = getattr(func, "value", None)
            if node.args and attr in ENVIRON_READERS and base is not None:
                name = _env_name(node.args[0], constants)
                how = f"environ.{attr}"
                default_node = node.args[1] if len(node.args) > 1 else None
            elif node.args and attr == "getenv" and base is not None:
                name = _env_name(node.args[0], constants)
                how = "os.getenv"
                default_node = node.args[1] if len(node.args) > 1 else None
            elif isinstance(func, ast.Name) and func.id in helpers:
                index = helpers[func.id]
                if len(node.args) > index:
                    name = _env_name(node.args[index], constants)
                    how = f"{func.id}()"
                    if len(node.args) > index + 1:
                        default_node = node.args[index + 1]
            elif isinstance(func, ast.Name) and func.id == "getenv" and node.args:
                name = _env_name(node.args[0], constants)
                how = "os.getenv"
                default_node = node.args[1] if len(node.args) > 1 else None
        elif isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load):
            name = _env_name(node.slice, constants)
            how = "environ[...]"

        if not name or not name.startswith(PREFIX):
            continue
        default = _fold(default_node, constants) if default_node is not None else None
        results.append((name, Read(rel, node.lineno, how, default)))
        seen_by_ast.add(name)

    # Pass 3: reads the AST cannot reach, e.g. inside generated-code strings.
    for lineno, line in enumerate(text.splitlines(), start=1):
        for match in TEXT_READ_RE.finditer(line):
            name = match.group(1) or match.group(2)
            if name in seen_by_ast:
                continue
            results.append((name, Read(rel, lineno, "read in generated code", None)))
    return results


def collect(repo_root: Path = REPO_ROOT) -> dict[str, Variable]:
    variables: dict[str, Variable] = {}
    for path in iter_source_files(repo_root):
        for name, read in scan_file(path, repo_root):
            variables.setdefault(name, Variable(name)).reads.append(read)
    for variable in variables.values():
        variable.reads.sort(key=_read_rank)
    return dict(sorted(variables.items()))


# Hand-written notes, one entry per variable: (area, audience, summary).
#
# This is the only part of the page that is not derived from the source, and it
# is why the test is worth having: check_notes() fails when a variable is read
# without an entry here, so a new ODYSSEUS_* knob cannot land undocumented.
# Every default in the table comes from the source, not from this table.
#
# audience is USER for something an operator may reasonably set on a real
# install, and INTERNAL for sentinels, fixture switches, capture hooks and
# development tooling. Internal variables are listed on the page too, in their
# own section, rather than hidden.
VARIABLE_NOTES: dict[str, tuple[str, str, str]] = {
    # -- Deployment and first run ------------------------------------------
    "ODYSSEUS_ADMIN_USER": (
        "Deployment and first run", USER,
        "Username for the admin account created on first run. Setup uses env vars "
        "first, then an interactive prompt, then a random password.",
    ),
    "ODYSSEUS_ADMIN_PASSWORD": (
        "Deployment and first run", USER,
        "Password for the admin account created on first run. Setup refuses a value "
        "shorter than its minimum length rather than silently falling back.",
    ),
    "ODYSSEUS_SKIP_ADMIN_PROMPT": (
        "Deployment and first run", USER,
        "Any non-empty value suppresses the interactive admin-credential prompt even "
        "on a TTY, for unattended installs.",
    ),
    "ODYSSEUS_INPROCESS_TASKS": (
        "Deployment and first run", USER,
        "Set to 0, false, no or off to stop the in-process scheduled-task runner, for "
        "deployments where an external worker drives task firing.",
    ),
    "ODYSSEUS_INPROCESS_POLLERS": (
        "Deployment and first run", USER,
        "The same off switch for the in-process email pollers, when "
        "`odysseus-mail poll-scheduled` is the sole external driver.",
    ),
    "ODYSSEUS_STARTUP_WARMUPS": (
        "Deployment and first run", USER,
        "Opt-in startup pings of the configured model endpoints. Off by default "
        "because they compete with the first seconds of UI use.",
    ),
    "ODYSSEUS_MODEL_KEEPALIVE": (
        "Deployment and first run", USER,
        "Opt-in periodic model keep-alive pings. Off by default: the ping path runs "
        "model discovery, so stale LAN endpoints add background pressure.",
    ),
    "ODYSSEUS_SLOW_REQUEST_LOG_SECONDS": (
        "Deployment and first run", USER,
        "Request duration in seconds above which the middleware logs a slow-request "
        "warning.",
    ),
    "ODYSSEUS_REQUIRE_TOOL_INDEX_READY": (
        "Deployment and first run", USER,
        "Set truthy to make semantic tool-index readiness gate startup. Off by "
        "default so an install stays available on deterministic tool selection.",
    ),
    "ODYSSEUS_TOOL_INDEX_PREWARM": (
        "Deployment and first run", USER,
        "Set to 0, false, no or off to skip background initialization of semantic "
        "tool retrieval at startup.",
    ),
    "ODYSSEUS_ENABLE_HOST_DOCKER": (
        "Deployment and first run", USER,
        "Security-relevant. Must be exactly `true` before tools may use a mounted "
        "host Docker socket, and the socket itself must exist.",
    ),
    "ODYSSEUS_CONTAINER_NETWORK_MODE": (
        "Deployment and first run", USER,
        "Declares the container's Docker network mode. Set to `host` to skip "
        "host-gateway probing when discovering local model endpoints.",
    ),
    "ODYSSEUS_ALLOW_OLLAMA_CLI_SCAN": (
        "Deployment and first run", USER,
        "On Windows only, set truthy to let the Cookbook dependency probe shell out "
        "to `ollama list`. Ignored on other platforms, where the scan always runs.",
    ),

    # -- Data directories and paths ----------------------------------------
    "ODYSSEUS_DATA_DIR": (
        "Data directories and paths", USER,
        "Root directory for every persisted file. Prefer this over the per-path "
        "overrides; the rest of `src/constants.py` derives from it.",
    ),
    "ODYSSEUS_MAIL_ATTACHMENTS_DIR": (
        "Data directories and paths", USER,
        "Dedicated override for the mail attachment store, which otherwise lives "
        "under the data directory.",
    ),
    "ODYSSEUS_INTERNAL_BASE": (
        "Auth and internal API", USER,
        "Base URL the in-app tool layer uses for loopback HTTP calls. Set it when "
        "the app is not reachable at the port it thinks it is bound to.",
    ),

    # -- Model routing and providers ---------------------------------------
    "ODYSSEUS_LOCAL_MODEL_GATE": (
        "Model routing and providers", USER,
        "On by default. Set 0, false, no or off to drop the gate that checks a local "
        "endpoint before routing a request to it.",
    ),
    "ODYSSEUS_FIRST_TOKEN_TIMEOUT": (
        "Model routing and providers", USER,
        "Seconds to wait for the first streamed token from a local endpoint before "
        "failing. Unset keeps the generous read timeout, which makes a stalled "
        "backend look like a hung agent.",
    ),
    "ODYSSEUS_MISTRAL_REASONING_EFFORT": (
        "Model routing and providers", USER,
        "Reasoning effort sent to Mistral thinking-capable models. The API accepts "
        "high, medium, low and none.",
    ),
    "ODYSSEUS_DEEPSEEK_REASONING_EFFORT": (
        "Model routing and providers", USER,
        "Reasoning effort for DeepSeek. Only `high` and `max` are accepted; any "
        "other value falls back to the default.",
    ),
    "ODYSSEUS_QWEN_ROUTE_THINKING": (
        "Model routing and providers", USER,
        "Thinking policy for the Qwen routing step. An unrecognized value falls back "
        "to `auto`.",
    ),
    "ODYSSEUS_COPILOT_CLIENT_ID": (
        "Model routing and providers", USER,
        "GitHub OAuth client id for the Copilot device flow. The default is the "
        "public VS Code client id; override it only with your own allow-listed app.",
    ),
    "ODYSSEUS_COPILOT_API_VERSION": (
        "Model routing and providers", USER,
        "Dated API-version header the Copilot models and chat endpoints require.",
    ),
    "ODYSSEUS_MLX_IMAGE_VLM_MODEL": (
        "Model routing and providers", USER,
        "Vision-language model id for the MLX image server script. Required unless "
        "`--vlm-model` is passed on the command line.",
    ),
    "ODYSSEUS_COPILOT_USER_AGENT": (
        "Model routing and providers", INTERNAL,
        "Editor-like User-Agent presented to the Copilot API. Kept stable on purpose.",
    ),
    "ODYSSEUS_COPILOT_INTEGRATION_ID": (
        "Model routing and providers", INTERNAL,
        "Integration id presented to the Copilot API. Kept stable on purpose.",
    ),
    "ODYSSEUS_COPILOT_EDITOR_VERSION": (
        "Model routing and providers", INTERNAL,
        "Editor-version header presented to the Copilot API. Kept stable on purpose.",
    ),
    "ODYSSEUS_DEBUG_LLM_SHAPE": (
        "Model routing and providers", INTERNAL,
        "Truthy logs the shape of streamed provider chunks. Debugging aid for "
        "provider response parsing.",
    ),

    # -- Agent loop and tool execution -------------------------------------
    "ODYSSEUS_TOOL_APPROVAL_GATE": (
        "Agent loop and tool execution", USER,
        "Security-relevant. On by default: after external content enters a run, "
        "tools that execute code, mutate state or cause external side effects need "
        "a separate approval. Set to 0 to opt out.",
    ),
    "ODYSSEUS_MCP_ALLOWED_COMMANDS": (
        "Agent loop and tool execution", USER,
        "Security-relevant. Comma-separated allowlist of MCP launcher basenames the "
        "agent may start. Empty by default, and the deny list still wins.",
    ),
    "ODYSSEUS_MCP_MEMORY_OWNER": (
        "Memory and skills", USER,
        "Application owner binding for the configured memory MCP backend. Takes "
        "precedence over ODYSSEUS_MEMORY_OWNER; missing ownership fails closed.",
    ),
    "ODYSSEUS_MEMORY_OWNER": (
        "Memory and skills", USER,
        "Fallback application owner binding for the memory MCP backend. This "
        "configuration identifies ownership; it does not grant read or egress authority.",
    ),
    "ODYSSEUS_BROWSER_LIVE_CONTRACT": (
        "Testing, capture and development tooling", INTERNAL,
        "Set 1 only in the allowlisted release Docker environment to run the "
        "browser producer contract tests. Does not enable browser page operations.",
    ),
    "ODYSSEUS_PYTHON_TOOL_SITE_PACKAGES": (
        "Agent loop and tool execution", USER,
        "Security-relevant. Absolute package roots, separated by the platform path "
        "separator, exposed to the sandboxed Python tool. Empty exposes none.",
    ),
    "ODYSSEUS_DISABLE_MCP": (
        "Agent loop and tool execution", USER,
        "Truthy disables MCP entirely, as an escape hatch for compatibility "
        "problems with a server.",
    ),
    "ODYSSEUS_SCRIPT_HOST": (
        "Agent loop and tool execution", USER,
        "Default host for the run-script action. `localhost`, `127.0.0.1`, `local` "
        "and empty run locally; any other value runs over SSH.",
    ),
    "ODYSSEUS_MAX_VISUAL_EVIDENCE_IMAGES": (
        "Agent loop and tool execution", USER,
        "How many images one tool result may contribute to the model turn. Clamped "
        "to 1-8.",
    ),
    "ODYSSEUS_MAX_VISUAL_EVIDENCE_FRAMES": (
        "Agent loop and tool execution", USER,
        "How many video frames one tool result may contribute. Clamped to 1-8.",
    ),
    "ODYSSEUS_CAPTURE_MODEL_REQUESTS": (
        "Agent loop and tool execution", INTERNAL,
        "Truthy writes model-request snapshots for local debugging. The marker file "
        "`/tmp/odysseus_capture_model_requests` enables the same thing.",
    ),
    "ODYSSEUS_EXPOSE_RAW_BROWSER_MCP": (
        "Agent loop and tool execution", INTERNAL,
        "Truthy stops hiding the raw Playwright MCP tools from agent prompts when "
        "the private-browser tool is available.",
    ),
    "ODYSSEUS_TOOL_CONTRACT_ROOT": (
        "Agent loop and tool execution", INTERNAL,
        "Directory holding the tool-contract scripts the clean-agent preview loads. "
        "The default resolves to the bundled scripts directory relative to the installed/source tree.",
    ),

    # -- Browser automation -------------------------------------------------
    "ODYSSEUS_BROWSER_EXECUTABLE": (
        "Browser automation", USER,
        "Absolute path to the Chrome or Chromium binary. Empty searches the usual "
        "names, then lets Playwright MCP pick its own browser.",
    ),
    "ODYSSEUS_BROWSER_ISOLATED": (
        "Browser automation", USER,
        "Security-relevant. On by default, adding `--isolated` so each browser "
        "session starts clean. Set 0, false or no to keep a persistent profile.",
    ),
    "ODYSSEUS_BROWSER_NO_SANDBOX": (
        "Browser automation", USER,
        "Security-relevant. On by default, adding `--no-sandbox` because the Docker "
        "image cannot use the Chromium sandbox. Set 0, false or no to keep it.",
    ),
    "ODYSSEUS_BROWSER_MCP_CACHE": (
        "Browser automation", USER,
        "Cache directory handed to the browser MCP server, so its npm download "
        "survives a container rebuild.",
    ),
    "ODYSSEUS_BROWSER_MCP_CALL_TIMEOUT_S": (
        "Browser automation", USER,
        "Upper bound in seconds for one browser MCP tool call. A call that exceeds "
        "it fails without being retried.",
    ),
    "ODYSSEUS_BROWSER_MCP_REQUIRE_CACHE": (
        "Browser automation", USER,
        "Truthy refuses to start the browser MCP server unless its npm package is "
        "already in the npx cache, instead of installing it at startup.",
    ),
    "ODYSSEUS_BROWSER_NAMESPACE": (
        "Browser automation", USER,
        "Namespace for the detached agent-browser daemon's pid files, so two "
        "runtimes on one machine do not terminate each other's browsers.",
    ),
    "ODYSSEUS_BROWSER_SCREENSHOT_DIR": (
        "Browser automation", USER,
        "Where private-browser screenshots are written. Falls back to the container "
        "path, then the system temp directory.",
    ),

    # -- Container and workspace mounts ------------------------------------
    "ODYSSEUS_WORKSPACE_MOUNTS": (
        "Container and workspace mounts", USER,
        "`host=container` path pairs separated by commas or semicolons, so the agent "
        "can translate a container path back to the host path a user typed.",
    ),
    "ODYSSEUS_WORKSPACE_HOST_ROOT": (
        "Container and workspace mounts", USER,
        "Single host-side root, paired with the container root below. Simpler than "
        "the explicit mount list when there is only one mount.",
    ),
    "ODYSSEUS_WORKSPACE_CONTAINER_ROOT": (
        "Container and workspace mounts", USER,
        "Container-side root that the host root maps onto. An `or` fallback, not a "
        "read default, supplies `/workspace` when it is unset.",
    ),
    "ODYSSEUS_WORKSPACE_DEFAULT": (
        "Container and workspace mounts", USER,
        "Default workspace path the admin-only workspace route reports. Empty means "
        "no default is configured.",
    ),

    # -- Email --------------------------------------------------------------
    "ODYSSEUS_IMAP_TIMEOUT_SECONDS": (
        "Email", USER,
        "IMAP socket timeout in seconds, clamped to 5-300. A non-numeric value falls "
        "back to 30 rather than failing.",
    ),
    "ODYSSEUS_DOCUMENT_OWNER": (
        "Email", USER,
        "Owner stamped on documents the email MCP server creates. Stdio MCP tools "
        "get no authenticated user, so without this a draft is invisible.",
    ),
    "ODYSSEUS_EMAIL_FIXTURE": (
        "Email", INTERNAL,
        "Exactly `1`, plus a fixture file on disk, makes the email MCP server serve "
        "fixtures instead of a real mailbox.",
    ),

    # -- Calendar, notes and single-user mode -------------------------------
    "ODYSSEUS_SINGLE_USER": (
        "Calendar, notes and single-user mode", USER,
        "Security-relevant. On by default. Set to 0 on a real multi-user install so "
        "unauthenticated calendar writes are rejected rather than absorbed.",
    ),
    "ODYSSEUS_FALLBACK_OWNER": (
        "Calendar, notes and single-user mode", USER,
        "Owner address that single-user mode attributes an unauthenticated request "
        "to. Only reachable while single-user mode is on.",
    ),
    "ODYSSEUS_ALLOW_PRIVATE_CALDAV": (
        "Calendar, notes and single-user mode", USER,
        "Security-relevant. Truthy lets CalDAV sync reach private and link-local "
        "addresses. Off by default; this is an SSRF guard.",
    ),

    # -- Upload and media limits -------------------------------------------
    "ODYSSEUS_CHAT_UPLOAD_MAX_BYTES": (
        "Upload and media limits", USER, "Maximum bytes accepted for a chat attachment.",
    ),
    "ODYSSEUS_GALLERY_UPLOAD_MAX_BYTES": (
        "Upload and media limits", USER, "Maximum bytes accepted for a gallery upload.",
    ),
    "ODYSSEUS_GALLERY_TRANSFORM_UPLOAD_MAX_BYTES": (
        "Upload and media limits", USER,
        "Maximum bytes accepted for an image handed to a gallery transform.",
    ),
    "ODYSSEUS_EDITOR_DRAFT_MAX_BYTES": (
        "Upload and media limits", USER, "Maximum bytes accepted for a saved editor draft.",
    ),
    "ODYSSEUS_MEMORY_IMPORT_MAX_BYTES": (
        "Upload and media limits", USER, "Maximum bytes accepted for a memory import file.",
    ),
    "ODYSSEUS_PERSONAL_UPLOAD_MAX_BYTES": (
        "Upload and media limits", USER,
        "Maximum bytes accepted for a personal-documents upload.",
    ),
    "ODYSSEUS_EMAIL_COMPOSE_UPLOAD_MAX_BYTES": (
        "Upload and media limits", USER,
        "Maximum bytes accepted for an attachment added while composing mail.",
    ),
    "ODYSSEUS_STT_MAX_AUDIO_BYTES": (
        "Upload and media limits", USER,
        "Maximum bytes accepted for an audio file submitted for transcription.",
    ),
    "ODYSSEUS_ICS_MAX_BYTES": (
        "Upload and media limits", USER, "Maximum bytes accepted for an imported ICS file.",
    ),
    "ODYSSEUS_MEDIA_MAX_FILES": (
        "Upload and media limits", USER,
        "How many local media files one agent turn may ingest.",
    ),
    "ODYSSEUS_MEDIA_MAX_IMAGE_BYTES": (
        "Upload and media limits", USER, "Largest source image the media pipeline will read.",
    ),
    "ODYSSEUS_MEDIA_MAX_VIDEO_BYTES": (
        "Upload and media limits", USER, "Largest source video the media pipeline will read.",
    ),
    "ODYSSEUS_MEDIA_MAX_DOCUMENT_BYTES": (
        "Upload and media limits", USER, "Largest source document the media pipeline will read.",
    ),
    "ODYSSEUS_MEDIA_MAX_AUDIO_BYTES": (
        "Upload and media limits", USER, "Largest source audio file the media pipeline will read.",
    ),
    "ODYSSEUS_MEDIA_MAX_ENCODED_BYTES": (
        "Upload and media limits", USER,
        "Budget for the encoded payload handed to the model, counted cumulatively "
        "across one turn's attachments rather than per file.",
    ),
    "ODYSSEUS_MEDIA_MAX_DOCUMENT_CHARS": (
        "Upload and media limits", USER,
        "How many characters of an ingested document are inlined into the turn.",
    ),
    "ODYSSEUS_MEDIA_MAX_DIMENSION": (
        "Upload and media limits", USER,
        "Longest edge in pixels an image is resized down to before encoding.",
    ),
    "ODYSSEUS_MEDIA_MAX_PIXELS": (
        "Upload and media limits", USER,
        "Total pixel budget for a source image, as a decompression-bomb guard.",
    ),
    "ODYSSEUS_MEDIA_MAX_VIDEO_FRAMES": (
        "Upload and media limits", USER, "How many frames are sampled from a video.",
    ),
    "ODYSSEUS_MEDIA_PROBE_TIMEOUT": (
        "Upload and media limits", USER,
        "Seconds allowed for probing a video's metadata before giving up.",
    ),
    "ODYSSEUS_MEDIA_FRAME_TIMEOUT": (
        "Upload and media limits", USER,
        "Seconds allowed for extracting frames from a video before giving up.",
    ),

    # -- Search -------------------------------------------------------------
    "ODYSSEUS_SEARCH_PROVIDER": (
        "Search", USER,
        "Forces the search provider, overriding the Settings value. Empty keeps the "
        "UI authoritative, which is what a normal install wants.",
    ),

    # -- Memory and skills --------------------------------------------------
    "ODYSSEUS_SKILL_SEMANTIC_RETRIEVAL": (
        "Memory and skills", USER,
        "On by default. Set 0, false, no or off to fall back to keyword-only skill "
        "retrieval when no vector store is reachable.",
    ),
    "ODYSSEUS_SKILL_SEMANTIC_THRESHOLD": (
        "Memory and skills", USER,
        "Minimum semantic score a skill needs to be retrieved. A non-numeric value "
        "falls back to the default.",
    ),

    # -- Speech and vision models -------------------------------------------
    "ODYSSEUS_STT_MODEL": (
        "Speech and vision models", USER,
        "Default speech-to-text model for media transcription when the tool call "
        "does not name one.",
    ),
    "ODYSSEUS_TTS_CACHE_MAX_BYTES": (
        "Speech and vision models", USER,
        "Cap on the synthesized-speech cache. A non-numeric value falls back to the "
        "default.",
    ),
    "ODYSSEUS_SAM_MODEL": (
        "Speech and vision models", USER,
        "Segmentation model id the gallery loads for subject selection.",
    ),
    "ODYSSEUS_GROUNDING_MODEL": (
        "Speech and vision models", USER,
        "Object-grounding model id the gallery loads for text-driven selection.",
    ),

    # -- Auth and internal API ----------------------------------------------
    "ODYSSEUS_INTERNAL_TOKEN": (
        "Auth and internal API", USER,
        "Security-relevant. Token that lets the in-app tool layer reach admin-gated "
        "routes over loopback. Unset generates a fresh per-process token, which is "
        "what you want unless something outside the process needs the same value.",
    ),

    # -- Security perimeter --------------------------------------------------
    "ODYSSEUS_GUARD_ENABLED": (
        "Security perimeter", USER,
        "Master switch for the optional guard-core perimeter (rate-limit ceilings, "
        "WAF/recon detection, honeypot auto-ban, per-route caps). Off by default; "
        "enabling it requires `pip install -r requirements-optional.txt`.",
    ),
    "ODYSSEUS_GUARD_PASSIVE": (
        "Security perimeter", USER,
        "Defaults to true: the perimeter only logs what it would block. Set to "
        "false to enforce once the passive log is clean for your traffic.",
    ),
    "ODYSSEUS_GUARD_EMERGENCY": (
        "Security perimeter", USER,
        "Starts the perimeter in emergency lock-down: only loopback is served "
        "until the flag is cleared.",
    ),
    "ODYSSEUS_GUARD_BLOCK_CLOUDS": (
        "Security perimeter", USER,
        "Comma list of cloud providers whose published IP ranges are blocked at "
        "the perimeter; empty means no cloud blocking.",
    ),
    "ODYSSEUS_GUARD_TRUSTED_PROXIES": (
        "Security perimeter", USER,
        "Comma list of proxy CIDRs allowed to set X-Forwarded-For; the client IP "
        "is resolved through them, and a threshold ban targeting one of them is "
        "refused so the perimeter cannot ban its own proxy.",
    ),
    "ODYSSEUS_GUARD_BLOCK_COUNTRIES": (
        "Security perimeter", USER,
        "Comma list of ISO country codes to geo-deny; needs ODYSSEUS_GUARD_GEOIP_DB "
        "and is ignored without it.",
    ),
    "ODYSSEUS_GUARD_GEOIP_DB": (
        "Security perimeter", USER,
        "Path to a MaxMind GeoLite2/GeoIP2 country .mmdb backing the country block "
        "list; a missing or unreadable file disables country blocking with a "
        "warning.",
    ),

    # -- Integrations (Claude, Codex) ---------------------------------------
    "ODYSSEUS_URL": (
        "Integrations (Claude, Codex)", USER,
        "Base URL of the Odysseus instance the bundled integration scripts call.",
    ),
    "ODYSSEUS_API_TOKEN": (
        "Integrations (Claude, Codex)", USER,
        "API token those scripts authenticate with. Both this and the URL are "
        "required; the scripts name whichever is missing.",
    ),

    # -- Testing, capture and development tooling ---------------------------
    "ODYSSEUS_SKIP_RUN_HINT": (
        "Testing, capture and development tooling", INTERNAL,
        "Any non-empty value suppresses the `start the server with` hint at the end "
        "of setup. `start-macos.sh` sets it because it starts the server itself.",
    ),
    "ODYSSEUS_TEST_STATIC_ORIGIN": (
        "Testing, capture and development tooling", INTERNAL,
        "Origin an already-running static server is serving the repository from, so "
        "snapshot tooling reuses it instead of starting its own.",
    ),
    "ODYSSEUS_TEST_STATIC_PORT": (
        "Testing, capture and development tooling", INTERNAL,
        "Fixed port for the test suite's static server. Unset takes an ephemeral "
        "port, which is what keeps parallel runs from colliding.",
    ),
    "ODYSSEUS_SFT_TRACE_CAPTURE": (
        "Testing, capture and development tooling", INTERNAL,
        "On by default, but only for owners whose name starts with `sft_`. Set 0, "
        "false, no or off to stop writing training traces.",
    ),
    "ODYSSEUS_SFT_TRACE_DIR": (
        "Testing, capture and development tooling", INTERNAL,
        "Directory the SFT trace JSONL files are written to. Defaults to "
        "`sft_traces` under the data directory.",
    ),
    "ODYSSEUS_SFT_FORCE_UTC_TIMEZONE": (
        "Testing, capture and development tooling", INTERNAL,
        "Truthy forces `sft_` accounts to UTC for deterministic batch generation. "
        "Interactive accounts still follow the browser timezone.",
    ),
    "ODYSSEUS_SFT_DISABLE_WORKSPACE_TOOLS": (
        "Testing, capture and development tooling", INTERNAL,
        "On by default. Keeps synthetic personal-assistant fixtures out of workspace "
        "mode; set 0, false, no or off to let them through.",
    ),
    "ODYSSEUS_AJAX_TEST_URL": (
        "Testing, capture and development tooling", INTERNAL,
        "Chat-completions URL of a live Ajax endpoint. Unset skips the opt-in live "
        "Ajax email tests.",
    ),
    "ODYSSEUS_EDITOR_TEST_ENDPOINT": (
        "Testing, capture and development tooling", INTERNAL,
        "Chat-completions URL the opt-in editor-writing and organizer smoke tools "
        "drive. Both tools require it.",
    ),
    "ODYSSEUS_EDITOR_ACTIONS": (
        "Testing, capture and development tooling", INTERNAL,
        "Comma-separated writing actions the editor-writing smoke tool runs. Unset "
        "runs every action plus edit and update.",
    ),
    "ODYSSEUS_EDITOR_MAX_TOKENS": (
        "Testing, capture and development tooling", INTERNAL,
        "Completion token limit for each editor-writing smoke request.",
    ),
    "ODYSSEUS_EDITOR_RICH_FIXTURE": (
        "Testing, capture and development tooling", INTERNAL,
        "Set to 1 to run the editor-writing smoke tool against a rich-text document "
        "fixture instead of Markdown.",
    ),
    "ODYSSEUS_EDITOR_TRACE": (
        "Testing, capture and development tooling", INTERNAL,
        "Any non-empty value prints every stream event after each editor-writing "
        "smoke action.",
    ),
    "ODYSSEUS_ORGANIZER_TRACE": (
        "Testing, capture and development tooling", INTERNAL,
        "Any non-empty value prints each provider request the organizer smoke tool "
        "sends.",
    ),
    "ODYSSEUS_ORGANIZER_AUTO_CHOICE": (
        "Testing, capture and development tooling", INTERNAL,
        "With organizer tracing on, any non-empty value replaces forced tool choice "
        "with auto on traced requests.",
    ),
    "ODYSSEUS_ORGANIZER_TRACE_MESSAGES": (
        "Testing, capture and development tooling", INTERNAL,
        "With organizer tracing on, any non-empty value also prints the request "
        "messages.",
    ),
    "ODYSSEUS_ORGANIZER_CASES": (
        "Testing, capture and development tooling", INTERNAL,
        "Comma-separated organizer smoke case names to run. Unset runs every case.",
    ),
    "ODYSSEUS_RUNTIME_REVISION": (
        "Testing, capture and development tooling", INTERNAL,
        "Revision string stamped into each captured SFT trace record, so a trace can "
        "be tied back to the build that produced it.",
    ),
    "ODYSSEUS_QA_TEACHER_ATTEMPTS": (
        "Testing, capture and development tooling", INTERNAL,
        "Retry budget for the conversation-QA teacher model call. Clamped to 1-3.",
    ),
    "ODYSSEUS_QA_TEACHER_TIMEOUT": (
        "Testing, capture and development tooling", INTERNAL,
        "Timeout in seconds for that call. Clamped to 15-120.",
    ),
    "ODYSSEUS_DOCKER_TEST_IMAGE": (
        "Testing, capture and development tooling", INTERNAL,
        "Docker image tag exercised by the DevOps Docker entrypoint integration tests.",
    ),
    "ODYSSEUS_LLAMA_SERVER": (
        "Testing, capture and development tooling", INTERNAL,
        "Binary name or path to llama-server used by the related-flow audit script.",
    ),
    "ODYSSEUS_QA_PASSWORD": (
        "Testing, capture and development tooling", INTERNAL,
        "Account password passed to the related-flow audit script when authenticating.",
    ),
    "ODYSSEUS_SFT_DIR": (
        "Testing, capture and development tooling", INTERNAL,
        "Base directory containing SFT training datasets for the search teacher pipeline.",
    ),
    "ODYSSEUS_TINY_MODEL_PATH": (
        "Testing, capture and development tooling", INTERNAL,
        "Path to a local compact model GGUF file used in Cookbook serve lifecycle audit flows.",
    ),

    # -- Build and release metadata ----------------------------------------
    "ODYSSEUS_BUILD_VERSION": (
        "Build and release metadata", INTERNAL,
        "Overrides the build-version string the API and UI report, without touching "
        "the public application version.",
    ),
    "ODYSSEUS_SOURCE_COMMIT": (
        "Build and release metadata", INTERNAL,
        "Overrides the source commit reported for runtime provenance, for builds "
        "that ship without a git directory.",
    ),
}


GENERATED_WARNING = (
    "This page is generated from the source tree by "
    "`scripts/generate_env_reference.py`. Do not edit it by hand: run the script "
    "instead. `tests/test_env_reference.py` fails when the committed page and the "
    "source disagree, or when a variable is read without an entry in the script's "
    "notes table."
)

INTRO = """Odysseus reads its runtime configuration from the Settings UI. The
environment variables below are the deployment-level escape hatches underneath
that: they are read directly from the process environment, mostly at import or
startup, and most installs never need any of them.

`.env.example` stays a short, deployment-level example on purpose - `APP_BIND`,
`APP_PORT`, `AUTH_ENABLED`, `DATABASE_URL` and a pre-seeded admin password. This
page is the complete list, which is a different job.

Truthiness is not uniform across the codebase. Where a variable is described as
"truthy" the read accepts `1`, `true`, `yes` and usually `on`; where it is
described as a switch that turns something off, the read rejects `0`, `false`,
`no` and `off` and treats everything else as on. The `Default` column is the
value the code falls back to when the variable is unset, quoted from the source."""

HOW_SECTION = """The generator walks the Python sources under {roots} and finds
reads three ways, because no single pattern covers the codebase:

- Direct reads: `os.getenv(...)`, and any `.get` / `.setdefault` / `.pop` call or
  subscript keyed by an `ODYSSEUS_*` literal, including names held in a
  module-level constant. The receiver is not required to be `os.environ`, because
  several call sites read through a mapping passed in as an argument
  (`src/tool_index.py`, `src/host_docker_access.py`).
- Calls to env-reader helpers - any function that forwards one of its own
  parameters to an environment read. This is detected rather than hardcoded, so a
  new helper needs no change here. It is what finds the upload caps in
  `src/upload_limits.py` and the media-ingress overrides in
  `src/media_ingress.py`.
- A regex sweep of the raw file text, for reads the AST cannot see.
  `routes/cookbook_helpers.py` builds an Ollama probe script as a list of source
  lines, so one read lives inside a string literal.

The three passes are not redundancy. A line-based grep for a direct
`os.environ.get("ODYSSEUS_...` call finds {naive} of the {total} variables on this
page. What it misses is reads through an env-reader helper, reads whose call
spans more than one line, reads whose variable name is held in a module
constant, and reads through a mapping passed in as an argument - which is the
whole reason this page is generated rather than maintained.

The `Default` column shows the expression as written, with one level of
indirection resolved: a module-level constant and a dataclass field default are
replaced by the literal they hold, so `defaults.max_media_files` shows as `4`.
Anything computed at import time - `get_default_data_dir()` - is shown as
written, because that is the honest answer. A few call sites supply their
fallback with `or` rather than a default argument; those show as *unset* and say
so in the last column."""


def naive_line_scan(repo_root: Path = REPO_ROOT) -> set[str]:
    """What a line-based grep for the direct call pattern would find.

    Reported on the page so the gap between this and the real inventory stays
    true as the code changes, instead of being a number somebody wrote down once.
    """
    pattern = re.compile(
        r"""(?:os\.environ\.get|os\.getenv|environ\.get|getenv)\(\s*['"](ODYSSEUS_[A-Z0-9_]+)['"]"""
        r"""|environ\[\s*['"](ODYSSEUS_[A-Z0-9_]+)['"]"""
    )
    found = set()
    for path in iter_source_files(repo_root):
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            for match in pattern.finditer(line):
                found.add(match.group(1) or match.group(2))
    return found


def check_notes(variables: dict[str, Variable]) -> list[str]:
    """Problems that make the page wrong. Empty means the notes table is sound."""
    problems = []
    for name in variables:
        if name not in VARIABLE_NOTES:
            location = variables[name].primary.location
            problems.append(
                f"{name} is read at {location} but has no entry in VARIABLE_NOTES "
                f"in scripts/generate_env_reference.py - add one, with the area, "
                f"whether an operator would ever set it, and one sentence on what "
                f"it does"
            )
    for name, (area, audience, summary) in VARIABLE_NOTES.items():
        if name not in variables:
            problems.append(
                f"{name} has a VARIABLE_NOTES entry but is no longer read anywhere "
                f"- remove the entry"
            )
        if area not in AREA_ORDER:
            problems.append(f"{name} has area {area!r}, which is not in AREA_ORDER")
        if audience not in (USER, INTERNAL):
            problems.append(f"{name} has audience {audience!r}")
        if not summary.endswith("."):
            problems.append(f"{name} summary should end with a full stop")
    return problems


def _cell(text: str) -> str:
    return text.replace("|", "\\|")


def _read_cell(variable: Variable) -> str:
    """The file a variable is read in, and how many other files read it.

    Files only, never line numbers. A line number here goes stale whenever an
    unrelated edit shifts the file, which made every such PR regenerate this page
    and conflict with every other PR doing the same.
    """
    primary = variable.primary.path
    extra = len({read.path for read in variable.reads} - {primary})
    return f"`{primary}`" if extra <= 0 else f"`{primary}` (+{extra} more)"


def _default_cell(variable: Variable) -> str:
    default = variable.primary.default
    return "*unset*" if default is None else f"`{_cell(default)}`"


def _table(rows: list[Variable]) -> list[str]:
    lines = ["| Variable | Default | Read in | What it does |", "|---|---|---|---|"]
    for variable in rows:
        summary = VARIABLE_NOTES[variable.name][2]
        lines.append(
            f"| `{variable.name}` | {_default_cell(variable)} | "
            f"{_read_cell(variable)} | {_cell(summary)} |"
        )
    return lines


def _sections(variables: dict[str, Variable], audience: str) -> list[str]:
    by_area: dict[str, list[Variable]] = defaultdict(list)
    for variable in variables.values():
        area, own_audience, _ = VARIABLE_NOTES[variable.name]
        if own_audience == audience:
            by_area[area].append(variable)
    lines = []
    for area in AREA_ORDER:
        rows = by_area.get(area)
        if not rows:
            continue
        lines += ["", f"### {area}", ""]
        lines += _table(sorted(rows, key=lambda item: item.name))
    return lines


def render(variables: dict[str, Variable], naive: set[str]) -> str:
    operator = [n for n, (_, aud, _) in VARIABLE_NOTES.items() if aud == USER and n in variables]
    internal = [n for n, (_, aud, _) in VARIABLE_NOTES.items() if aud == INTERNAL and n in variables]
    lines = [
        "---",
        "layout: default",
        "---",
        "",
        "# Configuration reference: ODYSSEUS_* environment variables",
        "",
        f"<!-- {GENERATED_WARNING} -->",
        "",
        INTRO,
        "",
        f"The source tree reads **{len(variables)}** `ODYSSEUS_*` variables: "
        f"{len(operator)} an operator may want to set, and {len(internal)} that are "
        f"internal - sentinels, fixture switches, capture hooks and development "
        f"tooling. The internal ones are listed too, in their own section, so this "
        f"page can be checked against the source mechanically.",
        "",
        "> This page is generated. Edit `scripts/generate_env_reference.py` and",
        "> re-run it; `tests/test_env_reference.py` enforces that the committed page",
        "> matches the source.",
        "",
        "## Variables you can set",
    ]
    lines += _sections(variables, USER)
    lines += ["", "## Internal and development-only variables", "",
              "Listed for completeness. Setting one of these on a real install is "
              "either a no-op or a way to break something quietly."]
    lines += _sections(variables, INTERNAL)
    lines += [
        "",
        "## How this page is generated",
        "",
        HOW_SECTION.format(
            roots="`" + "`, `".join(SOURCE_ROOTS) + "`",
            naive=len(naive & set(variables)),
            total=len(variables),
        ),
        "",
        "Regenerate it with:",
        "",
        "```bash",
        "python3 scripts/generate_env_reference.py",
        "```",
        "",
    ]
    return "\n".join(lines)


def build(repo_root: Path = REPO_ROOT) -> tuple[str, dict[str, Variable], list[str]]:
    """The page text, the inventory behind it, and any notes-table problems."""
    variables = collect(repo_root)
    problems = check_notes(variables)
    if problems:
        # Rendering an undocumented variable would raise a KeyError and bury the
        # message that actually tells a contributor what to do.
        return "", variables, problems
    return render(variables, naive_line_scan(repo_root)), variables, problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true",
                        help="exit non-zero if the committed page is stale")
    parser.add_argument("--stdout", action="store_true",
                        help="print the page instead of writing it")
    parser.add_argument("--list", action="store_true",
                        help="print one variable name per line and nothing else")
    args = parser.parse_args(argv)

    page, variables, problems = build()

    if args.list:
        for name in variables:
            print(name)
        return 0

    for problem in problems:
        print(f"error: {problem}", file=sys.stderr)
    if problems:
        return 1

    if args.stdout:
        print(page, end="")
        return 0

    try:
        relative = OUTPUT_PATH.relative_to(REPO_ROOT).as_posix()
    except ValueError:  # an output path outside the repo, as the tests use
        relative = str(OUTPUT_PATH)
    current = OUTPUT_PATH.read_text(encoding="utf-8") if OUTPUT_PATH.exists() else None
    if args.check:
        if current == page:
            print(f"{relative} is up to date ({len(variables)} variables)")
            return 0
        print(f"error: {relative} is stale; run "
              f"python3 scripts/generate_env_reference.py", file=sys.stderr)
        return 1

    if current == page:
        print(f"{relative} unchanged ({len(variables)} variables)")
        return 0
    OUTPUT_PATH.write_text(page, encoding="utf-8")
    print(f"wrote {relative} ({len(variables)} variables)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
