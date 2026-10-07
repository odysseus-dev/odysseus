"""The check-in digest must gather email from the built-in MCP server.

A scheduled run cannot call an email tool itself: the skills/integrations/MCP
context blocks arm the post-external tool gate before round one, and there is
no interactive surface to approve past it. So email has to be fetched
server-side alongside calendar and notes, which means the built-in email
server must not be skipped as "built-in", and the args sent to it must match
the names that server actually declares.
"""
import ast
from pathlib import Path

from src.task_scheduler import TaskScheduler


def _email_patterns():
    return [p for p in TaskScheduler.CHECKIN_MCP_PATTERNS if p["section"] == "Email"]


def _declared_schema_props(tool_name):
    """Property names the built-in email server declares for a tool."""
    src = Path("mcp_servers/email_server.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        kw = {k.arg: k.value for k in node.keywords}
        name = kw.get("name")
        if not isinstance(name, ast.Constant) or name.value != tool_name:
            continue
        schema = kw.get("inputSchema")
        if not isinstance(schema, ast.Dict):
            continue
        for key, val in zip(schema.keys, schema.values):
            if isinstance(key, ast.Constant) and key.value == "properties" and isinstance(val, ast.Dict):
                return {
                    k.value for k in val.keys
                    if isinstance(k, ast.Constant) and isinstance(k.value, str)
                }
    return set()


def test_email_patterns_carry_builtin_args():
    patterns = _email_patterns()
    assert patterns, "no Email check-in pattern registered"
    for p in patterns:
        assert p.get("builtin_args"), f"{p['tool']} has no builtin_args"


def test_builtin_args_match_the_servers_declared_schema():
    # The generic `args` use a third-party spelling (mailbox/limit). Sending
    # those to the built-in server silently falls back to its defaults, so the
    # unread filter and result cap are lost.
    for p in _email_patterns():
        declared = _declared_schema_props(p["tool"])
        assert declared, f"could not read inputSchema for {p['tool']}"
        unknown = set(p["builtin_args"]) - declared
        assert not unknown, f"{p['tool']} builtin_args not in schema: {sorted(unknown)}"


def test_builtin_args_do_not_pin_an_account_selector():
    # "default" is matched as a substring against account name/user/address,
    # so it resolves to nothing. A null selector picks the is_default row.
    for p in _email_patterns():
        assert "account" not in p["builtin_args"]


def test_only_the_email_builtin_serves_a_checkin_pattern():
    # The discovery loop admits built-in `email` and skips the rest; that is
    # only safe while no other built-in exposes a detect name.
    from src.mcp_manager import McpManager

    detects = {p["detect"] for p in TaskScheduler.CHECKIN_MCP_PATTERNS}
    other_builtins = ["rag", "memory", "image_gen", "builtin_browser"]
    mgr = McpManager.__new__(McpManager)
    assert mgr.is_builtin("email")
    for sid in other_builtins:
        assert mgr.is_builtin(sid), f"{sid} no longer counts as built-in"

    email_src = Path("mcp_servers/email_server.py").read_text(encoding="utf-8")
    assert "list_emails" in email_src
    for other in ("rag_server.py", "memory_server.py", "image_gen_server.py"):
        path = Path("mcp_servers") / other
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        for detect in detects:
            assert f'name="{detect}"' not in text, f"{other} now exposes {detect}"


def test_owner_arg_constant_matches_the_server():
    from src.tool_execution import _EMAIL_MCP_OWNER_ARG

    src = Path("mcp_servers/email_server.py").read_text(encoding="utf-8")
    assert f'_MCP_OWNER_ARG = "{_EMAIL_MCP_OWNER_ARG}"' in src


def test_digest_email_fetch_is_owner_scoped(monkeypatch):
    """The server-side email fetch must carry the task owner.

    Email accounts are owner-scoped and MCP stdio receives no request user, so
    an unscoped call comes back as "requires an authenticated owner" and that
    error text lands in the digest instead of the inbox.
    """
    import asyncio
    from types import SimpleNamespace

    import src.task_scheduler as ts
    import src.tool_utils as tu
    import src.tool_implementations as ti

    calls = []

    class _FakeMcp:
        _tools = {"email": [{"name": "list_emails"}, {"name": "search_emails"}]}
        _connections = {"email": {"status": "connected", "identity": "me@example.com"}}

        def is_builtin(self, server_id):
            return server_id in {"email", "rag", "memory", "image_gen"}

        async def call_tool(self, qualified, args):
            calls.append((qualified, dict(args)))
            return {"exit_code": 0, "output": "[1] Subject From: A Human | Sep 24"}

    monkeypatch.setattr(ts, "_resolve_task_timezone", lambda db, task: None, raising=False)
    monkeypatch.setattr(ts, "_checkin_calendar_events", lambda *a, **k: [], raising=False)
    monkeypatch.setattr(tu, "get_mcp_manager", lambda: _FakeMcp(), raising=False)
    monkeypatch.setattr(ts, "_cached", lambda key, ttl, fn: fn(), raising=False)

    async def _fake_notes(*a, **k):
        return {"results": "no notes"}

    monkeypatch.setattr(ti, "do_manage_notes", _fake_notes, raising=False)

    captured = {}

    async def _fake_run(self, endpoint_url, model, task, session_id, **kwargs):
        captured["context"] = kwargs.get("override_user_message", "")
        return "ok"

    monkeypatch.setattr(ts.TaskScheduler, "_run_agent_loop", _fake_run, raising=False)

    sched = ts.TaskScheduler(session_manager=None)
    task = SimpleNamespace(owner="c", name="Daily check-in", prompt="write it", max_steps=5)

    asyncio.run(sched._execute_checkin(task, None, None, "sess", "http://x", "m"))

    email_calls = [c for c in calls if c[0].startswith("mcp__email__")]
    assert email_calls, f"built-in email server was not queried; calls={calls}"
    qualified, args = email_calls[0]
    assert args.get("_odysseus_owner") == "c", f"owner not stamped: {args}"
    assert "account" not in args, f"literal account selector leaked: {args}"
    assert "A Human" in captured["context"], "email data never reached the digest"
