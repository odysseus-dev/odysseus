"""The agent's file tools must not reach the application's own state.

read_file / grep / glob / ls resolve model-supplied paths against
_tool_path_roots(), and the data directory holds the session store, the
credential database, the encryption key and the settings file. A read tool
pointed at those is a credential disclosure, and no approval prompt stands in
the way because reads are classified read_workspace and pass the untrusted-
context gate untouched.

The agent gets its own subdirectory instead. Three routes have to close
together, because closing only the first leaves the other two working:

  - the default roots, which put DATA_DIR first
  - an active workspace bound at (or above) the data directory
  - a tool_path_extra_roots setting that covers the data directory

so the guard is a property of the path, not of the root it arrived through.
"""

import asyncio
import importlib
import os
from contextlib import contextmanager

import pytest

from src.constants import (
    AGENT_WORKSPACE_DIR,
    DATA_DIR,
    MAIL_ATTACHMENTS_DIR,
    PERSONAL_DIR,
    PERSONAL_UPLOADS_DIR,
    RUNBOOK_DIR,
    UPLOAD_DIR,
)
from src.tool_execution import (
    _active_workspace,
    _resolve_search_root,
    _resolve_tool_path,
    agent_cwd,
    vet_workspace,
)
from src.agent_tools.filesystem_tools import GlobTool, GrepTool

APP_STATE_FILES = [
    "sessions.json",   # session token -> username, cleartext
    "auth.json",       # bcrypt hashes, admin flags, privileges
    "app.db",          # every user's notes, documents, mail rows
    ".app_key",        # Fernet key for secret_storage
    "settings.json",   # provider API keys
]


@contextmanager
def workspace_at(path):
    """Bind an active workspace for the body of a test.

    Set and reset in the same context; a ContextVar token cannot be reset from
    fixture teardown, which runs in a different one.
    """
    token = _active_workspace.set(os.path.realpath(path))
    try:
        yield
    finally:
        _active_workspace.reset(token)


# ── The default roots ────────────────────────────────────────────────

@pytest.mark.parametrize("name", APP_STATE_FILES)
def test_blocks_app_state_file(name):
    with pytest.raises(ValueError, match="application state"):
        _resolve_tool_path(os.path.join(DATA_DIR, name))


def test_blocks_listing_the_data_directory_itself():
    """`ls data` enumerated the state files, which is how an attacker who
    does not know the install path finds them."""
    with pytest.raises(ValueError, match="application state"):
        _resolve_tool_path(DATA_DIR)


def test_blocks_app_state_reached_by_relative_path(monkeypatch):
    """The data directory is a relative hop from the checkout root, so
    confinement cannot depend on the model supplying an absolute path."""
    monkeypatch.chdir(os.path.dirname(DATA_DIR))
    with pytest.raises(ValueError, match="application state"):
        _resolve_tool_path(os.path.join(os.path.basename(DATA_DIR), "sessions.json"))


def test_blocks_app_state_reached_through_a_symlink(tmp_path):
    """/tmp is an allowed root and the agent can create links there in an
    un-armed turn, so containment has to survive one."""
    link = tmp_path / "shortcut"
    try:
        link.symlink_to(DATA_DIR)
    except OSError:
        pytest.skip("cannot create symlink")
    with pytest.raises(ValueError, match="application state"):
        _resolve_tool_path(str(link / "sessions.json"))


def test_blocks_app_state_on_a_case_insensitive_filesystem():
    """On default macOS a case-variant path opens the same file, and realpath
    does not canonicalise case there the way it does on Windows.

    This deny rule fails OPEN when containment misses, unlike the allowlist
    beside it, which fails closed. So it folds case, for the same reason
    _is_sensitive_path does and not with normcase, which is a no-op on POSIX.
    """
    shouty = os.path.join(DATA_DIR.upper(), "SESSIONS.JSON")
    with pytest.raises(ValueError, match="application state"):
        _resolve_tool_path(shouty)


def test_default_search_root_is_the_agent_workspace():
    """grep/glob/ls with no path fall back to roots[0]. That was DATA_DIR."""
    assert _resolve_search_root("") == os.path.realpath(AGENT_WORKSPACE_DIR)


def test_agent_workspace_is_inside_the_data_directory():
    """It has to stay under data/ to be covered by the Docker bind mount,
    so the guard cannot simply be 'anything under DATA_DIR is denied'."""
    assert os.path.realpath(AGENT_WORKSPACE_DIR).startswith(
        os.path.realpath(DATA_DIR) + os.sep
    )


# ── An active workspace ──────────────────────────────────────────────

def test_workspace_bound_at_the_data_directory_still_blocks_app_state():
    """vet_workspace() accepts the data directory, and chat_routes auto-binds
    a workspace from a path named in the message, so this is reachable."""
    with workspace_at(DATA_DIR):
        with pytest.raises(ValueError, match="application state"):
            _resolve_tool_path("sessions.json")


def test_workspace_bound_above_the_data_directory_still_blocks_app_state():
    with workspace_at(os.path.dirname(DATA_DIR)):
        with pytest.raises(ValueError, match="application state"):
            _resolve_tool_path(os.path.join(DATA_DIR, "sessions.json"))


def test_workspace_bound_at_the_data_directory_refuses_the_empty_search_root():
    """grep/glob/ls with no path take the workspace itself as the root, which
    skipped the in-workspace resolver and enumerated the state directory."""
    with workspace_at(DATA_DIR):
        with pytest.raises(ValueError, match="application state"):
            _resolve_search_root("")


def test_vet_workspace_refuses_the_data_directory():
    """Rejecting the bind is the cleaner failure: the client is told the
    workspace was refused instead of every tool call erroring separately."""
    assert vet_workspace(DATA_DIR) is None


def test_vet_workspace_accepts_the_agent_workspace():
    os.makedirs(AGENT_WORKSPACE_DIR, exist_ok=True)
    assert vet_workspace(AGENT_WORKSPACE_DIR) == os.path.realpath(AGENT_WORKSPACE_DIR)


def test_workspace_bound_at_the_data_directory_still_allows_the_agent_workspace():
    with workspace_at(DATA_DIR):
        resolved = _resolve_tool_path(os.path.join("agent_workspace", "notes.txt"))
    assert resolved == os.path.realpath(os.path.join(AGENT_WORKSPACE_DIR, "notes.txt"))


# ── An opt-in extra root ─────────────────────────────────────────────

def test_extra_root_covering_the_data_directory_still_blocks_app_state(monkeypatch):
    monkeypatch.setattr(
        "src.settings.get_setting", lambda *_a, **_k: [os.path.dirname(DATA_DIR)]
    )
    with pytest.raises(ValueError, match="application state"):
        _resolve_tool_path(os.path.join(DATA_DIR, "sessions.json"))


# ── What the agent keeps ─────────────────────────────────────────────

def test_allows_files_in_the_agent_workspace():
    resolved = _resolve_tool_path(os.path.join(AGENT_WORKSPACE_DIR, "scratch.txt"))
    assert resolved == os.path.realpath(os.path.join(AGENT_WORKSPACE_DIR, "scratch.txt"))


@pytest.mark.parametrize("directory, why", [
    (UPLOAD_DIR,
     "_uploaded_files_context_message emits path= and tells the model to "
     "read it with read_file (src/agent_loop.py)"),
    (MAIL_ATTACHMENTS_DIR,
     "download_attachment returns the path and its description says to read "
     "it with read_file (mcp_servers/email_server.py)"),
    (PERSONAL_DIR,
     "GET /api/personal returns a path per file and is reachable through the "
     "app_api tool, which does not block that prefix"),
    (PERSONAL_UPLOADS_DIR,
     "indexed into personal docs by routes/personal_routes.py, and listed as "
     "an absolute path by manage_rag"),
])
def test_allows_user_content_the_app_hands_to_the_model(directory, why):
    """Carving these out is not convenience. The app gives the model these
    paths and tells it to read them, so denying them breaks the feature."""
    target = os.path.join(directory, "example.txt")
    assert _resolve_tool_path(target) == os.path.realpath(target), why


def test_runbook_is_covered_by_the_personal_docs_carve_out():
    """RUNBOOK_DIR nests under PERSONAL_DIR, so it needs no entry of its own."""
    target = os.path.join(RUNBOOK_DIR, "notes.md")
    assert _resolve_tool_path(target) == os.path.realpath(target)


def test_allows_tmp():
    """Unchanged: /tmp is still a root and holds no application state."""
    assert _resolve_tool_path("/tmp/scratch.txt") == os.path.realpath("/tmp/scratch.txt")


def test_subprocess_cwd_is_the_agent_workspace():
    """bash/python cwd has to move with the file root, or the agent writes
    where read_file can no longer look."""
    assert agent_cwd() == os.path.realpath(AGENT_WORKSPACE_DIR)


def test_sensitive_deny_list_still_fires_inside_the_agent_workspace():
    """The new guard is layered on the existing one, not a replacement."""
    with pytest.raises(ValueError, match="sensitive directory"):
        _resolve_tool_path(os.path.join(AGENT_WORKSPACE_DIR, "id_rsa"))


# ── Misconfigured carve-outs and recursive traversal ────────────────

def _configure_test_data_tree(monkeypatch, data_dir):
    current_constants = importlib.import_module("src.constants")
    current_execution = importlib.import_module("src.tool_execution")
    monkeypatch.setattr(current_constants, "DATA_DIR", str(data_dir), raising=False)
    readable = {
        "AGENT_WORKSPACE_DIR": data_dir / "agent_workspace",
        "UPLOAD_DIR": data_dir / "uploads",
        "MAIL_ATTACHMENTS_DIR": data_dir / "mail-attachments",
        "PERSONAL_DIR": data_dir / "personal_docs",
        "PERSONAL_UPLOADS_DIR": data_dir / "personal_uploads",
    }
    for name, path in readable.items():
        monkeypatch.setattr(current_constants, name, str(path), raising=False)
    monkeypatch.setattr(
        current_execution,
        "AGENT_WORKSPACE_DIR",
        str(readable["AGENT_WORKSPACE_DIR"]),
    )
    return readable


@contextmanager
def current_workspace_at(path):
    current_execution = importlib.import_module("src.tool_execution")
    token = current_execution._active_workspace.set(os.path.realpath(path))
    try:
        yield
    finally:
        current_execution._active_workspace.reset(token)


@pytest.mark.parametrize(
    "bad_kind", ["equal", "ancestor", "root", "empty", "dot", "symlink"]
)
def test_invalid_readable_carveout_cannot_cancel_state_deny(
    tmp_path, monkeypatch, bad_kind
):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    readable = _configure_test_data_tree(monkeypatch, data_dir)
    bad = {
        "equal": str(data_dir),
        "ancestor": str(tmp_path),
        "root": os.path.abspath(os.sep),
        "empty": "",
        "dot": ".",
    }.get(bad_kind)
    if bad_kind == "symlink":
        link = tmp_path / "data-link"
        try:
            link.symlink_to(data_dir, target_is_directory=True)
        except OSError:
            pytest.skip("cannot create symlink")
        bad = str(link)
    current_execution = importlib.import_module("src.tool_execution")
    monkeypatch.setattr(current_execution, "AGENT_WORKSPACE_DIR", bad)
    secret = data_dir / "settings.json"
    secret.write_text("STATE_SECRET\n", encoding="utf-8")

    with pytest.raises(ValueError, match="application state"):
        current_execution._resolve_tool_path(str(secret))
    with pytest.raises(ValueError, match="default agent workspace"):
        current_execution._resolve_search_root("")
    assert os.path.realpath(readable["UPLOAD_DIR"]) in current_execution._tool_path_roots()


def test_recursive_glob_and_grep_hide_state_but_keep_readable_descendants(
    tmp_path, monkeypatch
):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    readable = _configure_test_data_tree(monkeypatch, data_dir)
    workspace = readable["AGENT_WORKSPACE_DIR"]
    workspace.mkdir()
    (workspace / "notes.json").write_text("SHARED_MARKER readable\n", encoding="utf-8")
    (data_dir / "settings.json").write_text("SHARED_MARKER secret\n", encoding="utf-8")

    with current_workspace_at(tmp_path):
        glob_result = asyncio.run(GlobTool().execute(
            '{"pattern": "**/*.json", "path": ""}', {}
        ))
        grep_result = asyncio.run(GrepTool().execute(
            '{"pattern": "SHARED_MARKER", "path": ""}', {}
        ))

    assert "notes.json" in glob_result["output"]
    assert "settings.json" not in glob_result["output"]
    assert "notes.json" in grep_result["output"]
    assert "settings.json" not in grep_result["output"]


def test_recursive_glob_and_grep_hide_state_from_extra_root(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    readable = _configure_test_data_tree(monkeypatch, data_dir)
    readable["AGENT_WORKSPACE_DIR"].mkdir()
    (readable["AGENT_WORKSPACE_DIR"] / "public.txt").write_text(
        "TOKEN visible\n", encoding="utf-8"
    )
    (data_dir / "auth.json").write_text("TOKEN hidden\n", encoding="utf-8")
    monkeypatch.setattr("src.settings.get_setting", lambda *_a, **_k: [str(tmp_path)])

    glob_result = asyncio.run(GlobTool().execute(
        f'{{"pattern": "**/*", "path": "{tmp_path}"}}', {}
    ))
    grep_result = asyncio.run(GrepTool().execute(
        f'{{"pattern": "TOKEN", "path": "{tmp_path}"}}', {}
    ))

    assert "public.txt" in glob_result["output"]
    assert "auth.json" not in glob_result["output"]
    assert "public.txt" in grep_result["output"]
    assert "auth.json" not in grep_result["output"]
