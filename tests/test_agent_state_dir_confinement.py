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
import json
import multiprocessing
import os
import queue
import shutil
import time
from contextlib import contextmanager, nullcontext

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
from src.agent_tools.filesystem_tools import GlobTool, GrepTool, LsTool

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


def test_native_file_tools_hide_control_plane_hardlink_alias(tmp_path, monkeypatch):
    """A pathname inside an allowed root must not alias a protected state inode."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    readable = _configure_test_data_tree(monkeypatch, data_dir)
    workspace = readable["AGENT_WORKSPACE_DIR"]
    workspace.mkdir()
    secret = data_dir / "sessions.json"
    secret.write_text("LIVE_ADMIN_SESSION\n", encoding="utf-8")
    alias = workspace / "notes.txt"
    try:
        os.link(secret, alias)
    except OSError:
        pytest.skip("cannot create hardlink")

    with pytest.raises(ValueError, match="hard-linked"):
        importlib.import_module("src.tool_execution")._resolve_tool_path(str(alias))

    ls_result = asyncio.run(LsTool().execute(
        f'{{"path": "{workspace}"}}', {}
    ))
    glob_result = asyncio.run(GlobTool().execute(
        f'{{"pattern": "**/*", "path": "{workspace}"}}', {}
    ))
    grep_result = asyncio.run(GrepTool().execute(
        f'{{"pattern": "LIVE_ADMIN_SESSION", "path": "{workspace}"}}', {}
    ))
    assert "notes.txt" not in ls_result["output"]
    assert "notes.txt" not in glob_result["output"]
    assert "notes.txt" not in grep_result["output"]
    assert ":1:LIVE_ADMIN_SESSION" not in grep_result["output"]


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


def test_startup_rejects_agent_workspace_symlink_escape(tmp_path, monkeypatch):
    """Startup must not accept a dedicated workspace redirected outside DATA_DIR."""
    import src.app_initializer as app_initializer
    import src.tool_execution as tool_execution

    data_dir = tmp_path / "data"
    outside = tmp_path / "outside"
    data_dir.mkdir()
    outside.mkdir()
    workspace = data_dir / "agent_workspace"
    try:
        workspace.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("cannot create symlink")

    personal = data_dir / "personal_docs"
    monkeypatch.setattr(app_initializer, "DATA_DIR", str(data_dir))
    monkeypatch.setattr(app_initializer, "PERSONAL_DIR", str(personal))
    monkeypatch.setattr(app_initializer, "RUNBOOK_DIR", str(personal / "runbook"))
    monkeypatch.setattr(app_initializer, "UPLOAD_DIR", str(data_dir / "uploads"))
    monkeypatch.setattr(app_initializer, "AGENT_WORKSPACE_DIR", str(workspace))
    monkeypatch.setattr(tool_execution, "AGENT_WORKSPACE_DIR", str(workspace))

    with pytest.raises(RuntimeError, match="real directory"):
        app_initializer.create_directories()


def test_startup_allows_workspace_below_symlinked_data_dir(tmp_path, monkeypatch):
    import src.app_initializer as app_initializer

    real_data = tmp_path / "real-data"
    real_data.mkdir()
    data_link = tmp_path / "mounted-data"
    try:
        data_link.symlink_to(real_data, target_is_directory=True)
    except OSError:
        pytest.skip("cannot create symlink")
    workspace = data_link / "agent_workspace"
    personal = data_link / "personal_docs"
    monkeypatch.setattr(app_initializer, "DATA_DIR", str(data_link))
    monkeypatch.setattr(app_initializer, "PERSONAL_DIR", str(personal))
    monkeypatch.setattr(app_initializer, "RUNBOOK_DIR", str(personal / "runbook"))
    monkeypatch.setattr(app_initializer, "UPLOAD_DIR", str(data_link / "uploads"))
    monkeypatch.setattr(app_initializer, "AGENT_WORKSPACE_DIR", str(workspace))

    app_initializer.create_directories()

    assert workspace.is_dir()
    assert not workspace.is_symlink()
    assert os.path.realpath(workspace) == str(real_data / "agent_workspace")
    readable = _configure_test_data_tree(monkeypatch, data_link)
    note = readable["AGENT_WORKSPACE_DIR"] / "note.txt"
    note.write_text("visible\n", encoding="utf-8")
    assert importlib.import_module("src.tool_execution")._resolve_tool_path(
        str(note)
    ) == os.path.realpath(note)


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


@pytest.mark.parametrize("relative_data", ["data", "./data"])
def test_relative_data_dir_preserves_only_canonical_roles(
    tmp_path, monkeypatch, relative_data
):
    from pathlib import Path

    current_constants = importlib.import_module("src.constants")
    current_execution = importlib.import_module("src.tool_execution")
    monkeypatch.chdir(tmp_path)
    data_dir = Path(relative_data)
    data_dir.mkdir()
    readable = _configure_test_data_tree(monkeypatch, data_dir)
    monkeypatch.setattr(current_constants, "DATA_DIR", relative_data)
    workspace = readable["AGENT_WORKSPACE_DIR"]
    workspace.mkdir()
    visible = workspace / "visible.txt"
    visible.write_text("readable", encoding="utf-8")
    protected = data_dir / "settings.json"
    protected.write_text("protected", encoding="utf-8")
    monkeypatch.setattr(current_execution, "_AGENT_WORKDIR", str(workspace))
    token = current_execution._active_workspace.set(None)
    try:
        assert set(current_execution._agent_readable_data_subdirs()) == {
            os.path.realpath(path) for path in readable.values()
        }
        assert current_execution._resolve_search_root("") == os.path.realpath(workspace)
        assert current_execution.agent_cwd() == os.path.realpath(workspace)
        assert current_execution._resolve_tool_path(str(visible.resolve())) == str(visible.resolve())
        with pytest.raises(ValueError, match="application state"):
            current_execution._resolve_tool_path(str(protected.resolve()))

        external_mail = tmp_path / "outside-mail"
        external_mail.mkdir()
        monkeypatch.setattr(current_constants, "MAIL_ATTACHMENTS_DIR", "outside-mail")
        assert str(external_mail) not in current_execution._agent_readable_data_subdirs()

        # A relative override pointing to a different state role remains denied.
        monkeypatch.setattr(current_constants, "MAIL_ATTACHMENTS_DIR", "data/mcp_oauth")
        assert os.path.realpath("data/mcp_oauth") not in current_execution._agent_readable_data_subdirs()
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


def test_existing_file_cannot_become_a_readable_directory_carveout(
    tmp_path, monkeypatch
):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    _configure_test_data_tree(monkeypatch, data_dir)
    secret = data_dir / "auth.json"
    secret.write_text("STATE_SECRET\n", encoding="utf-8")
    current_constants = importlib.import_module("src.constants")
    monkeypatch.setattr(current_constants, "UPLOAD_DIR", str(secret))
    current_execution = importlib.import_module("src.tool_execution")

    assert (
        os.path.realpath(secret)
        not in current_execution._agent_readable_data_subdirs()
    )
    with pytest.raises(ValueError, match="application state"):
        current_execution._resolve_tool_path(str(secret))


def test_external_mail_attachment_directory_remains_readable(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    _configure_test_data_tree(monkeypatch, data_dir)
    external = tmp_path / "external-mail"
    external.mkdir()
    attachment = external / "message.txt"
    attachment.write_text("mail body\n", encoding="utf-8")
    current_constants = importlib.import_module("src.constants")
    monkeypatch.setattr(current_constants, "MAIL_ATTACHMENTS_DIR", str(external))
    current_execution = importlib.import_module("src.tool_execution")

    assert current_execution._resolve_tool_path(str(attachment)) == os.path.realpath(
        attachment
    )


def test_canonical_internal_mail_attachment_directory_remains_readable(
    tmp_path, monkeypatch
):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    readable = _configure_test_data_tree(monkeypatch, data_dir)
    mail_dir = readable["MAIL_ATTACHMENTS_DIR"]
    mail_dir.mkdir()
    attachment = mail_dir / "message.txt"
    attachment.write_text("mail body\n", encoding="utf-8")
    current_execution = importlib.import_module("src.tool_execution")

    assert current_execution._resolve_tool_path(str(attachment)) == os.path.realpath(
        attachment
    )


@pytest.mark.parametrize("alias_kind", ["direct", "symlink"])
def test_mail_attachment_root_cannot_alias_protected_state(
    tmp_path, monkeypatch, alias_kind
):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    readable = _configure_test_data_tree(monkeypatch, data_dir)
    protected = data_dir / "mcp_oauth"
    protected.mkdir()
    secret = protected / "tokens.json"
    secret.write_text("OAUTH_SECRET\n", encoding="utf-8")
    current_constants = importlib.import_module("src.constants")
    if alias_kind == "direct":
        monkeypatch.setattr(current_constants, "MAIL_ATTACHMENTS_DIR", str(protected))
    else:
        alias = readable["MAIL_ATTACHMENTS_DIR"]
        try:
            alias.symlink_to(protected, target_is_directory=True)
        except OSError:
            pytest.skip("cannot create symlink")
        monkeypatch.setattr(current_constants, "MAIL_ATTACHMENTS_DIR", str(alias))
    current_execution = importlib.import_module("src.tool_execution")

    assert os.path.realpath(protected) not in current_execution._agent_readable_data_subdirs()
    with pytest.raises(ValueError, match="application state"):
        current_execution._resolve_tool_path(str(secret))


@pytest.mark.skipif(
    os.path.normcase("DATA") == os.path.normcase("data"),
    reason="requires a platform with case-sensitive path comparison",
)
def test_case_distinct_path_cannot_masquerade_as_readable_descendant(
    tmp_path, monkeypatch
):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    _configure_test_data_tree(monkeypatch, data_dir)
    distinct = tmp_path / "DATA" / "agent_workspace"
    distinct.mkdir(parents=True)
    current_execution = importlib.import_module("src.tool_execution")
    monkeypatch.setattr(current_execution, "AGENT_WORKSPACE_DIR", str(distinct))

    assert (
        os.path.realpath(distinct)
        not in current_execution._agent_readable_data_subdirs()
    )


@pytest.mark.parametrize("use_workspace", [True, False])
def test_ls_hides_protected_entries_when_root_contains_data(
    tmp_path, monkeypatch, use_workspace
):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    _configure_test_data_tree(monkeypatch, data_dir)
    (tmp_path / "visible.txt").write_text("visible\n", encoding="utf-8")
    secret = data_dir / "settings.json"
    secret.write_text("SECRET_WITH_SIZE\n", encoding="utf-8")
    if use_workspace:
        context = current_workspace_at(tmp_path)
        content = '{"path": ""}'
    else:
        monkeypatch.setattr("src.settings.get_setting", lambda *_a, **_k: [str(tmp_path)])
        context = nullcontext()
        content = f'{{"path": "{tmp_path}"}}'

    with context:
        result = asyncio.run(LsTool().execute(content, {}))

    assert "visible.txt" in result["output"]
    assert "settings.json" not in result["output"]
    assert "SECRET_WITH_SIZE" not in result["output"]
    assert "data/" not in result["output"]


@pytest.mark.skipif(shutil.which("rg") is None, reason="requires ripgrep")
def test_state_spanning_grep_bounds_dangerous_regex(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    readable = _configure_test_data_tree(monkeypatch, data_dir)
    workspace = readable["AGENT_WORKSPACE_DIR"]
    workspace.mkdir()
    (workspace / "long.txt").write_text("a" * 250_000 + "!\n", encoding="utf-8")
    (data_dir / "auth.txt").write_text("a" * 250_000 + "!\n", encoding="utf-8")

    started = time.monotonic()
    with current_workspace_at(tmp_path):
        result = asyncio.run(GrepTool().execute(
            '{"pattern": "(a+)+$", "path": "", "max_results": 1}', {}
        ))
    elapsed = time.monotonic() - started

    assert result["exit_code"] == 0, result
    assert "auth.txt" not in result["output"]
    assert elapsed < 5


def test_state_spanning_grep_stops_process_at_max_results(tmp_path, monkeypatch):
    import subprocess
    import threading

    readers = []
    original_thread = threading.Thread

    class TrackedThread(original_thread):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            if getattr(kwargs.get("target"), "__name__", "") == "read_stdout":
                readers.append(self)

    monkeypatch.setattr(threading, "Thread", TrackedThread)

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    _configure_test_data_tree(monkeypatch, data_dir)
    (tmp_path / "visible.txt").write_text("MATCH\n", encoding="utf-8")
    instances = []

    class FakeProcess:
        def __init__(self, *args, **kwargs):
            self.stdout = iter(json.dumps({
                "type": "match",
                "data": {
                    "path": {"text": "visible.txt"},
                    "lines": {"text": "MATCH\n"},
                    "line_number": 1,
                },
            }) + "\n" for index in range(100))
            self.stderr = type("EmptyStderr", (), {"read": lambda self, _size: ""})()
            self.terminated = False
            instances.append(self)

        def poll(self):
            return 0 if self.terminated else None

        def terminate(self):
            self.terminated = True

        def wait(self, timeout=None):
            return 0

        def kill(self):
            self.terminated = True

    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/rg")
    monkeypatch.setattr(subprocess, "Popen", FakeProcess)
    with current_workspace_at(tmp_path):
        result = asyncio.run(GrepTool().execute(
            '{"pattern": "MATCH", "path": "", "max_results": 1}', {}
        ))

    assert len(instances) == 1
    assert instances[0].terminated is True
    assert result["output"].count(":1:MATCH") == 1
    assert len(readers) == 1
    assert not readers[0].is_alive(), "capped grep must release its stdout reader"


@pytest.mark.skipif(shutil.which("rg") is None, reason="requires ripgrep")
def test_state_spanning_grep_keeps_relative_glob_semantics(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    readable = _configure_test_data_tree(monkeypatch, data_dir)
    nested = readable["AGENT_WORKSPACE_DIR"] / "nested"
    nested.mkdir(parents=True)
    (nested / "readable.py").write_text("PATH_GLOB_MARKER\n", encoding="utf-8")
    (data_dir / "protected.py").write_text("PATH_GLOB_MARKER\n", encoding="utf-8")

    with current_workspace_at(tmp_path):
        result = asyncio.run(GrepTool().execute(
            '{"pattern": "PATH_GLOB_MARKER", "path": "", "glob": "**/*.py"}',
            {},
        ))

    assert "readable.py" in result["output"]
    assert "protected.py" not in result["output"]


@pytest.mark.parametrize("use_rg", [True, False])
def test_state_spanning_grep_hides_sibling_symlink(tmp_path, monkeypatch, use_rg):
    if use_rg and shutil.which("rg") is None:
        pytest.skip("requires ripgrep")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    readable = _configure_test_data_tree(monkeypatch, data_dir)
    readable["AGENT_WORKSPACE_DIR"].mkdir()
    protected = data_dir / "auth.txt"
    protected.write_text("SIBLING_LINK_SECRET\n", encoding="utf-8")
    alias = tmp_path / "public-link"
    try:
        alias.symlink_to(data_dir, target_is_directory=True)
    except OSError:
        pytest.skip("cannot create symlink")
    if not use_rg:
        monkeypatch.setattr(shutil, "which", lambda _name: None)

    with current_workspace_at(tmp_path):
        result = asyncio.run(GrepTool().execute(
            '{"pattern": "SIBLING_LINK_SECRET", "path": ""}', {}
        ))

    assert result["exit_code"] == 0, result
    assert "auth.txt" not in result["output"]


@pytest.mark.parametrize("use_rg", [True, False])
def test_state_spanning_grep_keeps_relative_glob_semantics_in_both_modes(
    tmp_path, monkeypatch, use_rg
):
    if use_rg and shutil.which("rg") is None:
        pytest.skip("requires ripgrep")
    data_dir = tmp_path / "data[secret]"
    data_dir.mkdir()
    readable = _configure_test_data_tree(monkeypatch, data_dir)
    nested = readable["AGENT_WORKSPACE_DIR"] / "nested"
    nested.mkdir(parents=True)
    (nested / "readable.py").write_text("FALLBACK_MARKER public\n", encoding="utf-8")
    (data_dir / "protected.py").write_text("FALLBACK_MARKER secret\n", encoding="utf-8")
    if not use_rg:
        monkeypatch.setattr(shutil, "which", lambda _name: None)

    with current_workspace_at(tmp_path):
        result = asyncio.run(GrepTool().execute(
            '{"pattern": "FALLBACK_MARKER", "path": "", "glob": "**/*.py"}', {}
        ))

    assert result["exit_code"] == 0, result
    assert "readable.py" in result["output"]
    assert "protected.py" not in result["output"]


@pytest.mark.parametrize("use_rg", [True, False])
def test_grep_reports_invalid_regex_as_error(tmp_path, monkeypatch, use_rg):
    if use_rg and shutil.which("rg") is None:
        pytest.skip("requires ripgrep")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    readable = _configure_test_data_tree(monkeypatch, data_dir)
    readable["AGENT_WORKSPACE_DIR"].mkdir()
    if not use_rg:
        monkeypatch.setattr(shutil, "which", lambda _name: None)

    with current_workspace_at(tmp_path):
        result = asyncio.run(GrepTool().execute('{"pattern": "[", "path": ""}', {}))

    assert result["exit_code"] == 1
    assert any(word in result["error"].lower() for word in ("pattern", "regex"))


def test_no_rg_uses_top_level_spawn_worker(tmp_path, monkeypatch):
    import multiprocessing
    import src.agent_tools.filesystem_tools as filesystem_tools

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    readable = _configure_test_data_tree(monkeypatch, data_dir)
    workspace = readable["AGENT_WORKSPACE_DIR"]
    workspace.mkdir()
    (workspace / "visible.txt").write_text("FROZEN_MARKER\n", encoding="utf-8")
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    seen = {}

    class InlineQueue(queue.Queue):
        def close(self):
            pass

    class InlineProcess:
        exitcode = 0

        def __init__(self, target, args):
            seen["target"] = target
            self.target = target
            self.args = args

        def start(self):
            self.target(*self.args)

        def is_alive(self):
            return False

        def join(self, timeout=None):
            pass

    class InlineContext:
        def Queue(self, maxsize):
            return InlineQueue(maxsize=maxsize)

        def Process(self, target, args):
            return InlineProcess(target, args)

    def fake_get_context(method):
        seen["method"] = method
        return InlineContext()

    monkeypatch.setattr(multiprocessing, "get_context", fake_get_context)

    with current_workspace_at(tmp_path):
        result = asyncio.run(GrepTool().execute(
            '{"pattern": "FROZEN_MARKER", "path": ""}', {}
        ))

    assert result["exit_code"] == 0
    assert "visible.txt" in result["output"]
    assert seen["method"] == "spawn"
    assert seen["target"] is filesystem_tools._python_grep_worker


def test_benign_hardlink_is_intentionally_rejected(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    readable = _configure_test_data_tree(monkeypatch, data_dir)
    workspace = readable["AGENT_WORKSPACE_DIR"]
    workspace.mkdir()
    original = workspace / "original.txt"
    alias = workspace / "copy.txt"
    original.write_text("benign\n", encoding="utf-8")
    try:
        os.link(original, alias)
    except OSError:
        pytest.skip("cannot create hardlink")

    with pytest.raises(ValueError, match="hard-linked"):
        importlib.import_module("src.tool_execution")._resolve_tool_path(str(alias))


def test_partition_filters_skip_directories_but_explicit_root_remains_searchable(
    tmp_path, monkeypatch
):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    readable = _configure_test_data_tree(monkeypatch, data_dir)
    readable["AGENT_WORKSPACE_DIR"].mkdir()
    skipped = tmp_path / "node_modules"
    skipped.mkdir()
    (skipped / "package.txt").write_text("SKIP_POLICY_MARKER\n", encoding="utf-8")

    with current_workspace_at(tmp_path):
        partitioned = asyncio.run(GrepTool().execute(
            '{"pattern": "SKIP_POLICY_MARKER", "path": ""}', {}
        ))
    with current_workspace_at(skipped):
        explicit = asyncio.run(GrepTool().execute(
            '{"pattern": "SKIP_POLICY_MARKER", "path": ""}', {}
        ))

    assert "package.txt" not in partitioned["output"]
    assert "package.txt" in explicit["output"]


@pytest.mark.skipif(shutil.which("rg") is None, reason="requires ripgrep")
def test_empty_partition_still_reports_invalid_rg_regex(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    _configure_test_data_tree(monkeypatch, data_dir)

    with current_workspace_at(tmp_path):
        result = asyncio.run(GrepTool().execute('{"pattern": "[", "path": ""}', {}))

    assert result["exit_code"] == 1
    assert "regex" in result["error"].lower()


def test_rg_stderr_is_fully_drained_but_only_prefix_is_reported(tmp_path, monkeypatch):
    import subprocess

    target = tmp_path / "visible.txt"
    target.write_text("text\n", encoding="utf-8")
    chunks = ["PREFIX" + "x" * 12_000, "y" * 12_000, "TAIL"]

    class TrackingStderr:
        def __init__(self):
            self.reads = 0

        def read(self, _size):
            self.reads += 1
            return chunks.pop(0) if chunks else ""

    stderr = TrackingStderr()

    class FakeProcess:
        stdout = iter(())

        def __init__(self, *args, **kwargs):
            self.stderr = stderr

        def poll(self):
            return 2

        def wait(self, timeout=None):
            return 2

        def terminate(self):
            pass

        def kill(self):
            pass

    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/rg")
    monkeypatch.setattr(subprocess, "Popen", FakeProcess)
    with current_workspace_at(tmp_path):
        result = asyncio.run(GrepTool().execute('{"pattern": "text", "path": ""}', {}))

    assert result["exit_code"] == 1
    assert "PREFIX" in result["error"]
    assert "TAIL" not in result["error"]
    assert len(result["error"]) < 20_100
    assert stderr.reads == 4


def test_no_rg_worker_stops_at_bounded_result_queue(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    readable = _configure_test_data_tree(monkeypatch, data_dir)
    workspace = readable["AGENT_WORKSPACE_DIR"]
    workspace.mkdir()
    (workspace / "many.txt").write_text("\n".join(["QUEUE_MARKER"] * 1_000))
    monkeypatch.setattr(shutil, "which", lambda _name: None)

    with current_workspace_at(tmp_path):
        result = asyncio.run(GrepTool().execute(
            '{"pattern": "QUEUE_MARKER", "path": "", "max_results": 3}', {}
        ))

    assert result["exit_code"] == 0, result
    assert result["output"].count(":QUEUE_MARKER") == 3
    assert "capped at 3 matches" in result["output"]


def test_no_rg_worker_is_terminated_at_deadline(tmp_path, monkeypatch):
    import src.agent_tools.filesystem_tools as filesystem_tools

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    readable = _configure_test_data_tree(monkeypatch, data_dir)
    workspace = readable["AGENT_WORKSPACE_DIR"]
    workspace.mkdir()
    (workspace / "long.txt").write_text("a" * 250_000 + "!\n", encoding="utf-8")
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    monkeypatch.setattr(filesystem_tools, "_GREP_TIMEOUT_SECONDS", 0.2)

    started = time.monotonic()
    with current_workspace_at(tmp_path):
        result = asyncio.run(GrepTool().execute(
            '{"pattern": "(a+)+$", "path": ""}', {}
        ))

    assert result == {"error": "grep: timed out", "exit_code": 1}
    assert time.monotonic() - started < 3


def test_no_rg_worker_exit_before_first_record_is_reported_promptly(tmp_path, monkeypatch):
    import src.agent_tools.filesystem_tools as filesystem_tools

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    readable = _configure_test_data_tree(monkeypatch, data_dir)
    workspace = readable["AGENT_WORKSPACE_DIR"]
    workspace.mkdir()
    (workspace / "visible.txt").write_text("EXIT_MARKER\n", encoding="utf-8")
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    monkeypatch.setattr(filesystem_tools, "_GREP_TIMEOUT_SECONDS", 20)

    class EmptyQueue(queue.Queue):
        def close(self):
            pass

    class DeadProcess:
        exitcode = 71

        def __init__(self, target, args):
            self.target = target
            self.args = args

        def start(self):
            pass

        def is_alive(self):
            return False

        def join(self, timeout=None):
            pass

    class DeadContext:
        def Queue(self, maxsize):
            return EmptyQueue(maxsize=maxsize)

        def Process(self, target, args):
            return DeadProcess(target, args)

    monkeypatch.setattr(
        multiprocessing,
        "get_context",
        lambda method: DeadContext(),
    )

    started = time.monotonic()
    with current_workspace_at(tmp_path):
        result = asyncio.run(GrepTool().execute(
            '{"pattern": "EXIT_MARKER", "path": ""}', {}
        ))

    assert result == {"error": "grep: fallback worker exited 71", "exit_code": 1}
    assert time.monotonic() - started < 1
