"""Deterministic regression coverage for runtime behaviours, test-only.

These drive ``stream_agent_loop`` with a fake model so the assertions are about
what the runtime offers and does, not about what a model happens to answer.
Nothing here imports benchmark fixtures or allowlists, and nothing here touches
production runtime code: the lane exists so the implementation side can move
without losing the behaviours underneath it.

The fake-model pattern is the one already used by tests/test_tool_policy.py:
patch ``stream_llm_with_fallback`` and inspect the ``tools`` kwarg the loop
hands it, which is the runtime's decision about what the turn may do.
"""

import asyncio
import json

import pytest

from core import platform_compat
import src.agent_loop as al
import src.agent_tools.web_tools as al_web


def _collect(gen):
    async def _run():
        return [c async for c in gen]

    return asyncio.run(_run())


def _delta_chunk(text):
    # stream_llm_with_fallback exposes normalized SSE, not provider wire JSON.
    payload = {"delta": text}
    return f"data: {json.dumps(payload)}\n\n"


def _schema_names(tools):
    return {
        tool.get("function", {}).get("name") or tool.get("name")
        for tool in (tools or [])
    }


def _patch_loop_basics(monkeypatch):
    monkeypatch.setattr(al, "get_setting", lambda key, default=None: default, raising=False)
    monkeypatch.setattr(al, "get_mcp_manager", lambda: None, raising=False)
    monkeypatch.setattr(al, "estimate_tokens", lambda *a, **k: 10, raising=False)


def _run_turn(monkeypatch, messages, **kwargs):
    """Drive one agent turn and return the tool sets offered to the model."""
    _patch_loop_basics(monkeypatch)
    offered = []

    async def _fake_stream(_candidates, _messages, **kw):
        offered.append(kw.get("tools"))
        yield _delta_chunk("ok")
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)
    chunks = _collect(
        al.stream_agent_loop(
            "http://local.test/v1",
            "moonshotai/kimi-k3",
            messages,
            max_rounds=kwargs.pop("max_rounds", 1),
            relevant_tools=kwargs.pop("relevant_tools", {"ask_user", "update_plan"}),
            owner=kwargs.pop("owner", "sft_alex_creator"),
            **kwargs,
        )
    )
    return offered, chunks



def _contract(offered=("ask_user", "update_plan", "manage_notes"),
              required=("manage_notes",)):
    """A minimal valid TurnContract.

    The dataclass validates required <= offered <= executable and that the
    schema inventory matches offered exactly, so the schemas are built from
    the same names rather than hand-written.
    """
    from src.turn_contract import TurnContract

    return TurnContract(
        capabilities=frozenset({"notes"}),
        required=frozenset(required),
        offered=frozenset(offered),
        executable=frozenset(offered),
        unavailable=frozenset(),
        schema_json=tuple(
            json.dumps({"type": "function", "function": {"name": n, "parameters": {}}})
            for n in offered
        ),
    )


# ── negative capability wording ─────────────────────────────────────────────
# A turn that says not to search must not be handed the search tools. The
# failure this guards is a model that obeys the wording while the runtime
# contradicted it by offering the tool anyway.
#
# These cover inferred intent with no explicit web toggle or supplied contract.
# On the frozen Wave 3 base all three negatives are recognized. Two are still
# misclassified as web-dependent turns and short-circuit to "web disabled",
# preventing the requested answer from memory. Only that precise failure is
# expected below; unrelated exceptions must fail normally.

HELD = ["Answer from memory only, don't search online."]

BLOCKED_MEMORY_ONLY = [
    "Summarise what you already know. Do not search the web.",
    "No web search please, just tell me what you know about Python decorators.",
]


class _MemoryOnlyTurnBlocked(AssertionError):
    """The real loop blocked a memory-only answer as requiring web access."""


def _assert_negative_web_turn(monkeypatch, phrasing):
    offered, chunks = _run_turn(monkeypatch, [{"role": "user", "content": phrasing}])
    events = [json.loads(chunk[6:]) for chunk in chunks
              if chunk.startswith("data: ") and chunk.strip() != "data: [DONE]"]
    finals = [event for event in events if event.get("type") == "final_response"]
    if not offered and finals == [{
        "type": "final_response",
        "content": "Web access is disabled for this turn. Enable web search and resend the request.",
    }]:
        raise _MemoryOnlyTurnBlocked(
            f"Memory-only turn was blocked before any model call: {phrasing!r}; "
            f"actual response: {finals[0]['content']}"
        )

    assert len(offered) == 1, f"Expected one memory-only model call; events: {events!r}"
    names = _schema_names(offered[0])
    assert "web_search" not in names, f"web_search offered despite: {phrasing!r}"
    assert "web_fetch" not in names, f"web_fetch offered despite: {phrasing!r}"
    assert any(event.get("delta") == "ok" for event in events), events
    assert chunks[-1] == "data: [DONE]\n\n"


@pytest.mark.parametrize("phrasing", HELD)
def test_negative_web_wording_withholds_the_web_tools(monkeypatch, phrasing):
    _assert_negative_web_turn(monkeypatch, phrasing)


@pytest.mark.parametrize("phrasing", BLOCKED_MEMORY_ONLY)
def test_negative_web_wording_withholds_the_web_tools_memory_only(monkeypatch, phrasing):
    _assert_negative_web_turn(monkeypatch, phrasing)


def test_plain_web_request_still_offers_search(monkeypatch):
    """The guard above must not become a blanket removal of the web tools."""
    offered, chunks = _run_turn(
        monkeypatch,
        [{"role": "user", "content": "Search the web for the latest Python release."}],
    )

    assert len(offered) == 1, chunks
    assert "web_search" in _schema_names(offered[0])


# ── supplied workspace context must not produce a clarification ─────────────
# When the turn already carries what it needs, an answer that hands the next
# decision back to the user is a failed turn, not a polite one. The runtime
# detects that shape; these pin the detector so a reworded prompt cannot slip
# past it silently.

@pytest.mark.parametrize(
    "answer",
    [
        "Could you please share the file you want me to edit?",
        "Would you like me to go ahead and refactor it?",
        "Shall I start with the parser?",
        "Please let me know which approach you prefer.",
    ],
)
def test_handing_the_decision_back_is_recognised_as_clarification(answer):
    assert al._looks_like_unattended_clarification(answer) is True


@pytest.mark.parametrize(
    "answer",
    [
        "I read config.py and the timeout is set to 30 seconds.",
        "The parser fails on empty input because it indexes before checking length.",
        "Done. The workspace now has three files.",
    ],
)
def test_ordinary_answers_are_not_clarifications(answer):
    assert al._looks_like_unattended_clarification(answer) is False


# ── repeated update_plan is not the turn's actionable work ─────────────────
# update_plan and ask_user are permitted on almost every turn, so if they
# counted as execution a model could loop on them forever and look busy. The
# runtime must not advertise them as the tools that satisfy the request.

def test_plan_and_ask_are_not_advertised_as_the_turns_available_tools():
    contract = _contract()

    reason = al._tool_rejection_reason("web_search", set(), None, contract=contract)

    assert "manage_notes" in reason
    assert "update_plan" not in reason, "update_plan advertised as actionable work"
    assert "ask_user" not in reason, "ask_user advertised as actionable work"


def test_update_plan_is_permitted_but_never_the_requirement():
    contract = _contract()

    assert contract.permits("update_plan") is True
    assert "update_plan" not in contract.required


# ── request-scoped tool authority ──────────────────────────────────────────
# An external contract names what the request may do. A tool the caller never
# declared must not become executable just because the runtime knows it.

def test_request_scope_excludes_tools_the_caller_never_declared():
    declared = [{"function": {"name": "write_file"}}]
    offered = [{"function": {"name": "write_file"}}, {"function": {"name": "bash"}}]

    allowed = al._request_scoped_allowed_tool_names(
        declared, offered, native_terminal_runtime=False
    )

    assert allowed == {"write_file"}
    assert "bash" not in allowed, "an undeclared tool became executable"


def test_native_terminal_runtime_adds_offered_tools_deliberately():
    """The widening exists, so pin it: it is opt-in, not the default."""
    declared = [{"function": {"name": "write_file"}}]
    offered = [{"function": {"name": "write_file"}}, {"function": {"name": "bash"}}]

    allowed = al._request_scoped_allowed_tool_names(
        declared, offered, native_terminal_runtime=True
    )

    assert allowed == {"write_file", "bash"}


# ── owned process cleanup, foreign-process safety ──────────────────────────
# The Chrome sweep matches on this runtime's own profile prefix. A browser
# belonging to the user, or to another worktree, must survive it.

def test_chrome_sweep_kills_only_this_runtimes_profile(monkeypatch, tmp_path):
    import shutil

    from src import process_ownership
    from src.agent_tools.web_tools import PrivateBrowserTool

    proc = tmp_path / "proc"
    tmpdir = tmp_path / "runtime-tmp"
    tmpdir.mkdir()
    ours = str(tmpdir.resolve() / "agent-browser-chrome-")

    # A fake process needs an identity, not only a command line: the sweep
    # signals a process only after verifying the start token it matched on.
    # Without a stat here the token would be read from the host's real /proc,
    # so the outcome would depend on whether this pid happens to exist.
    boot = proc / "sys/kernel/random/boot_id"
    boot.parent.mkdir(parents=True)
    boot.write_text("fake-boot\n")

    def _pid(pid, cmdline):
        entry = proc / pid
        entry.mkdir(parents=True)
        starttime = " ".join(["0"] * 15 + [str(1000 + int(pid))])
        (entry / "stat").write_text(f"{pid} (chrome) S 1 {pid} {pid} {starttime}")
        (entry / "cmdline").write_bytes(cmdline.replace(" ", "\0").encode())

    _pid("101", f"chrome --user-data-dir={ours}session-a")
    _pid("202", "chrome --user-data-dir=/Users/someone/Library/Chrome")
    _pid("303", "chrome --user-data-dir=/tmp/other-worktree/agent-browser-chrome-x")
    (proc / "self").mkdir()

    monkeypatch.setattr(platform_compat, "PROC_ROOT", proc)
    monkeypatch.setattr(process_ownership, "PROC_ROOT", proc)
    killed = []

    def _kill(pid, sig):
        killed.append(pid)
        shutil.rmtree(proc / str(pid), ignore_errors=True)

    monkeypatch.setattr(al_web.os, "kill", _kill)

    PrivateBrowserTool._terminate_owned_chrome({"TMPDIR": str(tmpdir)})

    assert killed == [101], f"swept a process that was not ours: {killed}"
