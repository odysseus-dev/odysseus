"""Pure browser formatting/path and Wave 5B cleanup regressions.

Legacy successful page-command/batch/recovery tests have been superseded by
failed-before-dispatch resource tests in test_browser_resource_identity.py.
"""
import asyncio
import pytest
import base64
import json
import shutil
from pathlib import Path

from core import platform_compat
import src.agent_tools.web_tools as web_tools
from src.agent_tools.web_tools import (
    PrivateBrowserTool,
    YouTubeTool,
    shutdown_private_browser_sessions,
)
from src.tool_schemas import FUNCTION_TOOL_SCHEMAS


@pytest.mark.parametrize('snapshot,empty', [
    ('- generic\n  - generic\n    - generic', True),
    ('(empty page)', True),
    ('- heading "No results found"', False),
    ('- button "Accept cookies" [ref=e1]', False),
    ('- generic "GameStop"', False),
])
def test_browser_distinguishes_loading_scaffolding_from_content(snapshot, empty):
    observation = json.dumps([{'success': True, 'result': {'snapshot': snapshot}}])
    assert PrivateBrowserTool._empty_dom_observation(observation) is empty


def test_private_browser_maps_workspace_file_urls_and_screenshot_paths(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("src.tool_execution.get_active_workspace", lambda: str(tmp_path))

    source = tmp_path / "task browser.html"
    source.write_text("<html></html>")

    resolved_url = PrivateBrowserTool._resolve_local_file_url(
        "file:///workspace/task%20browser.html"
    )
    resolved_path = PrivateBrowserTool._resolve_workspace_path(
        "/workspace/output.png"
    )

    assert resolved_url == source.as_uri()
    assert resolved_path == tmp_path / "output.png"


def test_generate_image_has_stable_native_schema() -> None:
    names = {
        schema.get("function", {}).get("name")
        for schema in FUNCTION_TOOL_SCHEMAS
    }

    assert "generate_image" in names


def test_all_native_array_schemas_declare_items() -> None:
    """Provider APIs reject an array schema without an item schema."""

    def walk(value, path="schema"):
        if isinstance(value, dict):
            if value.get("type") == "array":
                assert "items" in value, f"missing items at {path}"
            for key, child in value.items():
                yield from walk(child, f"{path}.{key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                yield from walk(child, f"{path}[{index}]")

    # Consume the generator so assertions execute for every schema node.
    list(walk(FUNCTION_TOOL_SCHEMAS, "FUNCTION_TOOL_SCHEMAS"))


def test_private_browser_exposes_global_store_landing_link_ref() -> None:
    output = '''
    - heading "Welcome to IKEA Global!"
    - link "Go shopping at IKEA dot j p(English), or use the store selector to search for another store" [ref=e172]
    '''

    hint = PrivateBrowserTool._shopping_landing_hint(output)
    assert "global store-selector landing page" in hint
    assert "@e172" in hint


def test_snapshot_observation_preserves_dom_refs_without_duplicate_metadata():
    snapshot = '- searchbox "Search catalog" [ref=e2]\n- button "Search" [ref=e3]'
    raw = json.dumps([{'success': True, 'result': {
        'lifecycle': {'noise': 'x' * 9000}, 'refs': {'e2': {'role': 'searchbox'}},
        'origin': 'https://example.org', 'snapshot': snapshot,
    }}])
    assert PrivateBrowserTool._snapshot_observation(raw) == 'https://example.org\n' + snapshot
    assert PrivateBrowserTool._snapshot_observation('unparsed failure') == 'unparsed failure'
    errors = json.dumps([{'error': 'snapshot failed'}])
    assert PrivateBrowserTool._snapshot_observation(errors) == errors


def test_browser_executable_discovery_supports_chromium_snapshot_cache(
    monkeypatch, tmp_path
) -> None:
    chrome = (
        tmp_path
        / ".chromium-browser-snapshots"
        / "chromium"
        / "linux-123"
        / "chrome-linux"
        / "chrome"
    )
    chrome.parent.mkdir(parents=True)
    chrome.write_text("#!/bin/sh\n")
    chrome.chmod(0o755)
    monkeypatch.setattr(web_tools, "_service_home", lambda: tmp_path)
    real_glob = Path.glob

    def _glob(path, pattern):
        if path == Path("/home") or path == Path("/root"):
            return iter(())
        return real_glob(path, pattern)

    monkeypatch.setattr(Path, "glob", _glob)

    assert web_tools._browser_executable_candidates() == [chrome]


def test_browser_pid_candidates_include_upstream_root_session() -> None:
    runtime = Path("/run/user/1000")
    namespace = "clawmm-test"
    session = "session-1"

    candidates = web_tools._browser_pid_file_candidates(runtime, namespace, session)

    assert candidates[0] == runtime / "agent-browser" / (
        web_tools._scoped_browser_session(namespace, session) + ".pid"
    )
    assert all("session-1" not in str(path) for path in candidates)


def test_browser_pid_candidates_do_not_sweep_shared_root_without_session(
    tmp_path,
) -> None:
    legacy = tmp_path / "agent-browser" / "namespaces" / "legacy" / "run"
    legacy.mkdir(parents=True)
    (legacy / "ody-old.pid").write_text("1")

    candidates = web_tools._browser_pid_file_candidates(tmp_path, "legacy", None)

    assert candidates == [legacy / "ody-old.pid"]


def _cli_proc(calls, identity=None):
    class _Proc:
        pid = 1234
        returncode = None
        _ody_identity = identity

        def kill(self):
            calls.append("fallback-kill")

    return _Proc()


def test_private_browser_timeout_terminates_the_process_group(monkeypatch) -> None:
    from src import process_lifecycle, process_ownership

    calls = []
    monkeypatch.setattr(web_tools.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(process_ownership, "verify", lambda pid, token: process_ownership.OWNED)
    monkeypatch.setattr(
        web_tools.os,
        "killpg",
        lambda pgid, signum: calls.append((pgid, signum)),
    )

    PrivateBrowserTool._terminate_subprocess(
        _cli_proc(calls, process_lifecycle.ProcessIdentity(1234, "spawned", pgid=1234)))

    assert calls == [(1234, web_tools.signal.SIGKILL), "fallback-kill"]


@pytest.mark.parametrize("verdict", ["foreign", "unverifiable", "gone"])
def test_cli_group_is_not_signalled_once_its_identity_is_lost(monkeypatch, verdict) -> None:
    """A reaped CLI's pid may be reissued; its group is then not ours to kill."""
    from src import process_lifecycle, process_ownership

    calls = []
    monkeypatch.setattr(web_tools.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(process_ownership, "verify", lambda pid, token: verdict)
    monkeypatch.setattr(web_tools.os, "killpg", lambda *a: pytest.fail("signalled an unowned group"))

    PrivateBrowserTool._terminate_subprocess(
        _cli_proc(calls, process_lifecycle.ProcessIdentity(1234, "spawned", pgid=1234)))

    assert calls == ["fallback-kill"]


def test_cli_without_a_spawn_identity_is_never_group_signalled(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(web_tools.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(web_tools.os, "killpg", lambda *a: pytest.fail("signalled a bare pid's group"))

    PrivateBrowserTool._terminate_subprocess(_cli_proc(calls))

    assert calls == ["fallback-kill"]


def test_cli_group_that_moved_is_not_signalled(monkeypatch) -> None:
    """The identity verifies but no longer leads the recorded group."""
    from src import process_lifecycle, process_ownership

    calls = []
    monkeypatch.setattr(web_tools.os, "getpgid", lambda pid: 999)
    monkeypatch.setattr(process_ownership, "verify", lambda pid, token: process_ownership.OWNED)
    monkeypatch.setattr(web_tools.os, "killpg", lambda *a: pytest.fail("signalled a group it does not lead"))

    PrivateBrowserTool._terminate_subprocess(
        _cli_proc(calls, process_lifecycle.ProcessIdentity(1234, "spawned", pgid=1234)))

    assert calls == ["fallback-kill"]


def test_youtube_tool_comments_falls_back_to_ytdlp(monkeypatch) -> None:
    from services.youtube import youtube_handler

    async def _fake_comments(video_id, max_comments=25, timeout=30):
        return {
            "success": True,
            "title": "Example Video",
            "channel": "Example Channel",
            "comments": [
                {"author": "Alice", "text": "Useful demo.", "likes": 12},
                {"author": "Bob", "text": "Loved the walkthrough.", "likes": 3},
            ],
        }

    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    monkeypatch.setattr(youtube_handler, "fetch_youtube_comments", _fake_comments)

    result = asyncio.run(YouTubeTool().execute(
        json.dumps({
            "action": "comments",
            "url": "https://www.youtube.com/watch?v=abc123DEF45",
            "max_results": 2,
        }),
        {},
    ))

    assert result["exit_code"] == 0
    assert "Example Video" in result["output"]
    assert "@Alice [12 likes]: Useful demo." in result["output"]


def test_youtube_tool_latest_channel_parses_playlist_json() -> None:
    parsed = YouTubeTool._parse_ytdlp_json_output(json.dumps({
        "entries": [
            {"id": "vid123", "title": "Latest upload"},
            {"id": "vid122", "title": "Previous upload"},
        ]
    }))

    assert parsed["entries"][0]["title"] == "Latest upload"


def test_youtube_tool_latest_channel_parses_json_lines() -> None:
    parsed = YouTubeTool._parse_ytdlp_json_output(
        '{"id":"vid123","title":"Latest upload"}\n'
        '{"id":"vid122","title":"Previous upload"}\n'
    )

    assert [entry["id"] for entry in parsed["entries"]] == ["vid123", "vid122"]


def test_youtube_tool_latest_channel_formats_requested_upload_count(monkeypatch) -> None:
    async def _fake_ytdlp_json(self, url, *, timeout, flat_playlist=False, playlist_end=5):
        assert flat_playlist is True
        assert playlist_end == 5
        return {
            "success": True,
            "data": {
                "entries": [
                    {"id": f"vid{i}", "title": f"Upload {i}", "duration": 60 + i}
                    for i in range(1, 8)
                ]
            },
        }

    monkeypatch.setattr(YouTubeTool, "_ytdlp_json", _fake_ytdlp_json)

    result = asyncio.run(YouTubeTool().execute(
        json.dumps({
            "action": "latest_channel_video",
            "channel_url": "https://www.youtube.com/c/RAINBOLTGEO/videos",
            "max_results": 5,
        }),
        {},
    ))

    assert result["exit_code"] == 0
    assert "Latest 5 channel videos" in result["output"]
    assert "1. Upload 1" in result["output"]
    assert "5. Upload 5" in result["output"]
    assert "6. Upload 6" not in result["output"]


def test_youtube_tool_latest_channel_resolves_bad_handle(monkeypatch) -> None:
    calls = []

    async def _fake_ytdlp_json(self, url, *, timeout, flat_playlist=False, playlist_end=5):
        calls.append(url)
        if url == "https://www.youtube.com/@rainbolt/videos":
            return {"success": False, "error": "HTTP Error 404: Not Found"}
        if url.startswith("ytsearch10:"):
            return {
                "success": True,
                "data": {
                    "entries": [
                        {
                            "title": "rainbolt clips",
                            "channel": "rainbolt clips",
                            "uploader_id": "@rainboltshorts",
                            "channel_url": "https://www.youtube.com/channel/clips",
                            "view_count": 1000000,
                        },
                        {
                            "title": "geoguessr pro reacts",
                            "channel": "RAINBOLT",
                            "uploader_id": "@georainbolt",
                            "channel_url": "https://www.youtube.com/channel/official",
                            "view_count": 100,
                        },
                    ]
                },
            }
        if url == "https://www.youtube.com/channel/official/videos":
            return {
                "success": True,
                "data": {"entries": [{"id": "vid123", "title": "Official latest upload"}]},
            }
        return {"success": False, "error": f"unexpected url {url}"}

    monkeypatch.setattr(YouTubeTool, "_ytdlp_json", _fake_ytdlp_json)

    result = asyncio.run(YouTubeTool().execute(
        json.dumps({
            "action": "latest_channel_video",
            "handle": "@rainbolt",
            "max_results": 5,
        }),
        {},
    ))

    assert result["exit_code"] == 0
    assert "Official latest upload" in result["output"]
    assert calls == [
        "https://www.youtube.com/@rainbolt/videos",
        "ytsearch10:rainbolt official YouTube channel",
        "https://www.youtube.com/channel/official/videos",
    ]


def test_youtube_tool_channel_search_query_handles_urls_and_handles() -> None:
    assert YouTubeTool._channel_search_query("@rainbolt") == "rainbolt"
    assert YouTubeTool._channel_search_query("https://www.youtube.com/@rainbolt/videos") == "rainbolt"
    assert YouTubeTool._channel_search_query("https://www.youtube.com/c/RainboltGeo") == "RainboltGeo"


def test_youtube_tool_metadata_with_channel_url_returns_channel_uploads(monkeypatch) -> None:
    async def _fake_ytdlp_json(self, url, *, timeout, flat_playlist=False, playlist_end=5):
        assert flat_playlist is True
        return {
            "success": True,
            "data": {"entries": [{"id": "vid123", "title": "Latest upload"}]},
        }

    monkeypatch.setattr(YouTubeTool, "_ytdlp_json", _fake_ytdlp_json)

    result = asyncio.run(YouTubeTool().execute(
        json.dumps({
            "action": "metadata",
            "channel_url": "https://www.youtube.com/@example/videos",
            "max_results": 1,
        }),
        {},
    ))

    assert result["exit_code"] == 0
    assert "Latest channel video" in result["output"]
    assert "Latest upload" in result["output"]


def test_youtube_tool_comment_author_keeps_single_at_prefix() -> None:
    output = YouTubeTool()._format_comments(
        {
            "comments": [
                {"author": "@AlreadyPrefixed", "text": "Looks good.", "likes": 1}
            ]
        },
        "https://www.youtube.com/watch?v=abc123DEF45",
    )

    assert "@AlreadyPrefixed [1 likes]: Looks good." in output
    assert "@@AlreadyPrefixed" not in output


class _FakeBrowserMcp:
    def get_all_tools(self, disabled_map=None):
        disabled = (disabled_map or {}).get("builtin_browser", set())
        return [
            {
                "server_id": "builtin_browser",
                "server_name": "Built-in: Browser",
                "name": "browser_navigate",
                "qualified_name": "mcp__builtin_browser__browser_navigate",
                "description": "Navigate",
                "input_schema": {"type": "object", "properties": {}},
                "is_disabled": "browser_navigate" in disabled,
            }
        ]

    def get_all_openai_schemas(self, disabled_map=None):
        return [
            {
                "type": "function",
                "function": {
                    "name": tool["qualified_name"],
                    "description": tool["description"],
                    "parameters": tool["input_schema"],
                },
            }
            for tool in self.get_all_tools(disabled_map)
            if not tool["is_disabled"]
        ]

    def get_tool_descriptions_for_prompt(self, disabled_map=None, allowed_names=None):
        lines = []
        for tool in self.get_all_tools(disabled_map):
            if tool["is_disabled"]:
                continue
            if allowed_names is not None and tool["qualified_name"] not in allowed_names:
                continue
            lines.append(tool["qualified_name"])
        return "\n".join(lines)


def test_raw_browser_mcp_is_hidden_when_private_browser_is_available() -> None:
    from src.agent_loop import _build_system_prompt

    messages, schemas = _build_system_prompt(
        [{"role": "user", "content": "open https://example.com and take a screenshot"}],
        "gpt-5.5",
        None,
        _FakeBrowserMcp(),
        disabled_tools=set(),
        relevant_tools={
            "private_browser",
            "builtin_browser",
            "mcp__builtin_browser__browser_navigate",
        },
        compact=True,
    )

    prompt_text = "\n".join(str(message.get("content") or "") for message in messages)
    schema_names = {
        schema.get("function", {}).get("name")
        for schema in schemas
    }

    assert "native tool schemas provided for this turn" in prompt_text
    assert "mcp__builtin_browser__browser_navigate" not in prompt_text
    assert "mcp__builtin_browser__browser_navigate" not in schema_names


def test_generic_go_to_phrase_is_browser_interaction() -> None:
    from src.agent_loop import _looks_like_explicit_browser_interaction

    assert _looks_like_explicit_browser_interaction(
        "Go to Airbnb and find stays in Tokyo for next weekend under $150/night."
    )
    assert _looks_like_explicit_browser_interaction(
        "Navigate to the vendor portal and check the pricing table."
    )
    assert _looks_like_explicit_browser_interaction(
        "Open Spotify's web player and search for Bach cello suites."
    )
    assert not _looks_like_explicit_browser_interaction(
        "Find rules for getting rid of garbage in Setagaya-ku."
    )


def test_terminate_owned_chrome_skips_the_sweep_without_procfs(
    monkeypatch, tmp_path
) -> None:
    """macOS and Windows have no /proc; shutdown must degrade, not raise."""

    missing = tmp_path / "no-procfs"
    monkeypatch.setattr(platform_compat, "PROC_ROOT", missing)

    def _unexpected_iterdir(*args, **kwargs):
        raise AssertionError("the pid sweep must not run without procfs")

    monkeypatch.setattr(Path, "iterdir", _unexpected_iterdir)

    PrivateBrowserTool._terminate_owned_chrome({"TMPDIR": str(tmp_path)})


def test_terminate_owned_chrome_kills_only_this_runtimes_profile(
    monkeypatch, tmp_path
) -> None:
    """With procfs present, match on the runtime-owned profile prefix alone."""

    proc = _fake_procfs(monkeypatch, tmp_path)
    tmpdir = tmp_path / "runtime-tmp"
    tmpdir.mkdir()
    profile_prefix = str(tmpdir.resolve() / "agent-browser-chrome-")

    _fake_process(proc, 101, f"chrome --user-data-dir={profile_prefix}abc")
    _fake_process(proc, 202, "chrome --user-data-dir=/Users/someone/Library/Chrome")
    (proc / "self").mkdir()

    killed = _install_lethal_kill(monkeypatch, proc)

    PrivateBrowserTool._terminate_owned_chrome({"TMPDIR": str(tmpdir)})

    assert killed == [101]


def _fake_procfs(monkeypatch, tmp_path):
    from src import process_ownership

    proc = tmp_path / "proc"
    boot = proc / "sys/kernel/random/boot_id"
    boot.parent.mkdir(parents=True)
    boot.write_text("fake-boot\n")
    monkeypatch.setattr(platform_compat, "PROC_ROOT", proc)
    monkeypatch.setattr(process_ownership, "PROC_ROOT", proc)
    return proc


def _fake_process(proc, pid: int, cmdline: str, *, starttime: int | None = None) -> None:
    entry = proc / str(pid)
    entry.mkdir(parents=True)
    tail = " ".join(["0"] * 15 + [str(starttime if starttime is not None else 1000 + pid)])
    (entry / "stat").write_text(f"{pid} (x) S 1 {pid} {pid} {tail}")
    (entry / "cmdline").write_bytes(cmdline.replace(" ", "\0").encode())


def _install_lethal_kill(monkeypatch, proc, *, before_kill=None):
    killed: list[int] = []

    def _kill(pid, sig):
        if before_kill is not None:
            before_kill(pid)
        entry = proc / str(pid)
        if not entry.exists():
            raise ProcessLookupError(pid)
        killed.append(pid)
        shutil.rmtree(entry)

    monkeypatch.setattr(web_tools.os, "kill", _kill)
    return killed


def test_owned_chrome_sweep_never_signals_a_reused_pid(monkeypatch, tmp_path) -> None:
    """Matched by profile, then reissued to a stranger before the signal."""
    from src import process_lifecycle

    proc = _fake_procfs(monkeypatch, tmp_path)
    tmpdir = tmp_path / "runtime-tmp"
    tmpdir.mkdir()
    _fake_process(proc, 101, f"chrome --user-data-dir={tmpdir.resolve()}/agent-browser-chrome-x")
    killed = _install_lethal_kill(monkeypatch, proc)
    real_terminate = process_lifecycle.terminate_identities

    def recycle_then_terminate(identities, **kwargs):
        identities = list(identities)
        shutil.rmtree(proc / "101")
        _fake_process(proc, 101, "postgres", starttime=99999)
        return real_terminate(identities, **kwargs)

    monkeypatch.setattr(process_lifecycle, "terminate_identities", recycle_then_terminate)

    PrivateBrowserTool._terminate_owned_chrome({"TMPDIR": str(tmpdir)})

    assert killed == [] and (proc / "101").exists()


def _legacy_pid_file_for(tmp_path, monkeypatch, namespace, session, pid):
    """A pid file in the legacy namespace layout, which only the fallback loop reads."""
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setenv("ODYSSEUS_BROWSER_NAMESPACE", namespace)
    legacy_run = (tmp_path / "agent-browser" / "namespaces"
                  / web_tools._bounded_browser_identity(namespace) / "run")
    legacy_run.mkdir(parents=True, exist_ok=True)
    target = legacy_run / f"ody-{web_tools._bounded_browser_identity(session)}.pid"
    assert target in web_tools._browser_pid_file_candidates(tmp_path, namespace, session)
    target.write_text(str(pid))
    return target


def test_legacy_daemon_pid_file_kills_only_the_verified_daemon(monkeypatch, tmp_path) -> None:
    proc = _fake_procfs(monkeypatch, tmp_path)
    _fake_process(proc, 4401, "node agent-browser --serve")
    killed = _install_lethal_kill(monkeypatch, proc)
    pid_file = _legacy_pid_file_for(tmp_path, monkeypatch, "clawmm-test", "session-7", 4401)

    PrivateBrowserTool._terminate_owned_daemon({}, "session-7")

    assert killed == [4401] and not pid_file.exists()


def test_legacy_daemon_pid_reused_before_the_signal_is_spared(monkeypatch, tmp_path) -> None:
    """The pid file still names the slot; the daemon in it was replaced."""
    from src import process_lifecycle

    proc = _fake_procfs(monkeypatch, tmp_path)
    _fake_process(proc, 4402, "node agent-browser --serve")
    killed = _install_lethal_kill(monkeypatch, proc)
    pid_file = _legacy_pid_file_for(tmp_path, monkeypatch, "clawmm-test", "session-8", 4402)
    real_terminate = process_lifecycle.terminate_identities

    def recycle_then_terminate(identities, **kwargs):
        identities = list(identities)
        shutil.rmtree(proc / "4402")
        _fake_process(proc, 4402, "node agent-browser --serve", starttime=99999)
        return real_terminate(identities, **kwargs)

    monkeypatch.setattr(process_lifecycle, "terminate_identities", recycle_then_terminate)

    PrivateBrowserTool._terminate_owned_daemon({}, "session-8")

    # Even a lookalike command line is not the process the match was made on.
    assert killed == [] and (proc / "4402").exists()


def test_legacy_daemon_without_identity_keeps_its_pid_file(monkeypatch, tmp_path) -> None:
    proc = _fake_procfs(monkeypatch, tmp_path)
    _fake_process(proc, 4403, "node agent-browser --serve")
    (proc / "4403" / "stat").write_text("4403 (x) S 1")  # no start time: unidentifiable
    monkeypatch.setattr(web_tools.os, "kill", lambda *a: pytest.fail("signalled an unidentified pid"))
    pid_file = _legacy_pid_file_for(tmp_path, monkeypatch, "clawmm-test", "session-9", 4403)

    PrivateBrowserTool._terminate_owned_daemon({}, "session-9")

    assert pid_file.exists()


def _pid_file_for(tmp_path, monkeypatch, namespace, session, pid):
    """Write a pid file where the daemon helpers will look for it."""
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setenv("ODYSSEUS_BROWSER_NAMESPACE", namespace)
    candidates = web_tools._browser_pid_file_candidates(tmp_path, namespace, session)
    target = candidates[0]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(str(pid))
    return target


def test_live_daemon_pid_file_survives_a_host_without_procfs(
    monkeypatch, tmp_path
) -> None:
    """Off Linux a missing cmdline is not evidence the daemon exited."""

    monkeypatch.setattr(platform_compat, "PROC_ROOT", tmp_path / "no-procfs")
    monkeypatch.setattr(web_tools, "_process_is_alive", lambda pid: True)
    killed: list[int] = []
    monkeypatch.setattr(web_tools.os, "kill", lambda pid, sig: killed.append(pid))
    pid_file = _pid_file_for(tmp_path, monkeypatch, "clawmm-test", "session-1", 4321)

    PrivateBrowserTool._terminate_owned_daemon({}, "session-1")

    assert pid_file.exists(), "a live daemon's pid file must not be removed"
    assert killed == [], "an unverified process must not be killed"


def test_dead_daemon_pid_file_is_removed_without_procfs(monkeypatch, tmp_path) -> None:
    """A pid that no longer exists is the one case that justifies forgetting it."""

    monkeypatch.setattr(platform_compat, "PROC_ROOT", tmp_path / "no-procfs")
    monkeypatch.setattr(web_tools, "_process_is_alive", lambda pid: False)
    pid_file = _pid_file_for(tmp_path, monkeypatch, "clawmm-test", "session-2", 4322)

    PrivateBrowserTool._terminate_owned_daemon({}, "session-2")

    assert not pid_file.exists()


def test_owned_daemon_is_detected_from_a_live_pid_without_procfs(
    monkeypatch, tmp_path
) -> None:
    """Answering "no daemon" here is what lets close bootstrap a fresh one."""

    monkeypatch.setattr(platform_compat, "PROC_ROOT", tmp_path / "no-procfs")
    monkeypatch.setattr(web_tools, "_process_is_alive", lambda pid: True)
    _pid_file_for(tmp_path, monkeypatch, "clawmm-test", "session-3", 4323)

    assert PrivateBrowserTool._owned_daemon_exists({}, "session-3") is True


def test_owned_daemon_absent_when_the_pid_is_gone(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(platform_compat, "PROC_ROOT", tmp_path / "no-procfs")
    monkeypatch.setattr(web_tools, "_process_is_alive", lambda pid: False)
    _pid_file_for(tmp_path, monkeypatch, "clawmm-test", "session-4", 4324)

    assert PrivateBrowserTool._owned_daemon_exists({}, "session-4") is False


def test_procfs_host_still_matches_on_the_command_line(monkeypatch, tmp_path) -> None:
    """With procfs present the identity check stays exact, not pid-liveness."""

    proc = tmp_path / "proc"
    (proc / "5555").mkdir(parents=True)
    (proc / "5555" / "cmdline").write_bytes(b"node\0agent-browser\0--serve")
    (proc / "6666").mkdir(parents=True)
    (proc / "6666" / "cmdline").write_bytes(b"some\0other\0process")
    monkeypatch.setattr(platform_compat, "PROC_ROOT", proc)
    monkeypatch.setattr(web_tools, "_process_is_alive", lambda pid: True)

    _pid_file_for(tmp_path, monkeypatch, "clawmm-test", "session-5", 5555)
    assert PrivateBrowserTool._owned_daemon_exists({}, "session-5") is True

    _pid_file_for(tmp_path, monkeypatch, "clawmm-test", "session-6", 6666)
    assert PrivateBrowserTool._owned_daemon_exists({}, "session-6") is False


def test_liveness_probe_goes_through_the_platform_safe_helper(monkeypatch) -> None:
    """The no-procfs path must not reach a bare ``os.kill(pid, 0)``.

    CPython's Windows ``os.kill`` calls ``TerminateProcess(handle, sig)`` for
    any signal other than CTRL_C / CTRL_BREAK, so probing liveness with signal
    0 terminates the process it asks about — and the only hosts that reach this
    probe are the ones with no procfs, Windows among them.
    ``core.platform_compat.pid_alive`` is the tree's platform-safe answer.
    """

    asked: list[int] = []
    monkeypatch.setattr(
        platform_compat, "pid_alive", lambda pid: asked.append(pid) or True
    )
    monkeypatch.setattr(
        web_tools.os,
        "kill",
        lambda *a, **kw: pytest.fail("os.kill must not be used to probe liveness"),
    )

    assert web_tools._process_is_alive(4242) is True
    assert asked == [4242]


@pytest.mark.asyncio
async def test_shutdown_cleans_up_invalidated_registered_browser_session(monkeypatch) -> None:
    """Shutdown cleanup must terminate owned daemons even if record.session was invalidated."""
    from unittest.mock import MagicMock
    from src import browser_identity as browser

    cleaned: list[tuple[Path, str]] = []
    def fake_force_cleanup(root, key, **kwargs):
        cleaned.append((Path(root), key))

    monkeypatch.setattr("src.browser_lifecycle.force_cleanup", fake_force_cleanup)

    record = MagicMock()
    record.key = "ody-test1234"
    record.env = {"AGENT_BROWSER_SOCKET_DIR": "/tmp/test-socket-dir"}
    record.session = None  # Simulates cancellation / invalidate()
    record.invalidate = MagicMock()

    monkeypatch.setattr(browser, "_REGISTRY", {("alice", "thread"): record})

    await shutdown_private_browser_sessions()

    assert cleaned == [(Path("/tmp/test-socket-dir"), "ody-test1234")]
    assert browser._REGISTRY == {}
    record.invalidate.assert_called_once()
