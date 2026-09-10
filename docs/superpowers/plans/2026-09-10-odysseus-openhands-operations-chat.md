# Odysseus OpenHands Operations Chat Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Interactive Chat and Agent composer sends share one OpenHands conversation; Chat is read-only on the human/production workspace (tools and sandbox drafts allowed); Agent is write privilege, still governed.

**Architecture:** Add a small privilege helper keyed off the user’s explicit Agent toggle (`user_requested_agent`), not auto-escalation. Route Chat through the existing `stream_governed_agent` path. Pass empty `workspace_grants` and ignore bound owner workspace unless that toggle is true. Do not start 9router. Do not delete `src/agent_loop.py`. Do not edit parent spec/plan bodies.

**Tech Stack:** Flask/FastAPI Odysseus, `services/agents/*`, Agent Server conversation API, existing chat SSE, vanilla JS / `static/index.html`.

## Global Constraints

- Odysseus is the operations UI; Agent Canvas is the engineering UI. Do not add “add agent”, “add workflow”, LLM-admin, Canvas iframe, or Canvas diagnostics to Odysseus.
- Chat is not “no tools.” Isolation is the human/production workspace (host files, mounted folders, Odysseus library / mutating domain records). Sandbox and thread writes stay allowed.
- `human_workspace_writable` is true only when the user explicitly chose Agent (`user_requested_agent`). Chat→Agent auto-escalation must not grant workspace writes or copy drafts into the owner tree.
- Interactive composer Chat send uses `stream_governed_agent`. No `stream_llm` / `stream_llm_with_fallback` fallback when Agent Server is down.
- Skip Odysseus `ModelEndpoint` requirement for interactive Chat and Agent (not compare mode). Do not restyle, relocate, disable, or rewire `#model-picker-wrap`.
- Hermes is not a selectable agent type. Native/OpenCode chooser stays.
- Do not start 9router. Do not modify other repos. Do not delete `src/agent_loop.py`.
- Do not change Odysseus MCP operation implementations. Do not add an inbound OpenAI completions gateway.
- Token-auth `POST /api/v1/chat` is out of this slice.
- Do not edit parent spec/plan bodies (`2026-09-08` cutover, `2026-09-09` one-chat).
- Local tests: `docker run --rm -e PYTHONDONTWRITEBYTECODE=1 -e PYTHON_DOTENV_DISABLED=1 -v /Users/maxholden/odysseus:/app -w /app --entrypoint python odysseus-odysseus:latest -m pytest -q <paths> --tb=short`
- Commit messages: `feat:` / `test:` / `fix:` / `docs:`. Do not push.

## File map

- Create: `services/agents/workspace_privilege.py` — Chat vs Agent write privilege
- Create: `tests/agents/test_workspace_privilege.py`
- Modify: `services/agents/legacy_bridge.py` — pass grants; suppress human-workspace write confirmations in Chat
- Modify: `routes/chat_routes.py` — Chat uses OpenHands; skip model picker; gate workspace
- Modify: `tests/test_chat_openhands_binding.py`
- Modify: `tests/agents/test_legacy_bridge.py`
- Modify: `tests/test_agent_ui_js.py` — no add-agent / add-workflow controls
- Modify only as needed: `tests/test_foreground_model_routing.py` tests that drive `mode=chat` through `chat_stream` expecting `stream_llm_with_fallback`

---

### Task 1: Workspace privilege helper

**Files:**
- Create: `services/agents/workspace_privilege.py`
- Create: `tests/agents/test_workspace_privilege.py`

**Interfaces:**
- Consumes: nothing from later tasks
- Produces:
  - `HUMAN_WORKSPACE_MUTATING_OPS: frozenset[str]` containing exactly `notes.write`, `documents.index`, `mail.send`, `calendar.write`, `memory.write`, `tasks.write`
  - `human_workspace_writable(*, user_requested_agent: bool) -> bool`
  - `workspace_grants_for_turn(*, user_requested_agent: bool, requested: tuple[str, ...] = ()) -> tuple[str, ...]`
  - `is_human_workspace_mutating_action(tool_name: str | None) -> bool`

- [ ] **Step 1: Write the failing test**

```python
from tests.agents._import_agents import ensure_agents_package

ensure_agents_package()

from services.agents.workspace_privilege import (
    HUMAN_WORKSPACE_MUTATING_OPS,
    human_workspace_writable,
    is_human_workspace_mutating_action,
    workspace_grants_for_turn,
)


def test_only_explicit_agent_toggle_grants_human_workspace_writes():
    assert human_workspace_writable(user_requested_agent=False) is False
    assert human_workspace_writable(user_requested_agent=True) is True


def test_chat_turn_drops_requested_workspace_grants():
    assert workspace_grants_for_turn(
        user_requested_agent=False,
        requested=("/Users/me/proj",),
    ) == ()


def test_agent_turn_keeps_requested_workspace_grants():
    assert workspace_grants_for_turn(
        user_requested_agent=True,
        requested=("/Users/me/proj",),
    ) == ("/Users/me/proj",)


def test_mutating_ops_are_human_workspace_writes():
    assert HUMAN_WORKSPACE_MUTATING_OPS == frozenset(
        {
            "notes.write",
            "documents.index",
            "mail.send",
            "calendar.write",
            "memory.write",
            "tasks.write",
        }
    )
    assert is_human_workspace_mutating_action("mail.send") is True
    assert is_human_workspace_mutating_action("notes.read") is False
    assert is_human_workspace_mutating_action("terminal") is False
    assert is_human_workspace_mutating_action(None) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker run --rm -e PYTHONDONTWRITEBYTECODE=1 -e PYTHON_DOTENV_DISABLED=1 -v /Users/maxholden/odysseus:/app -w /app --entrypoint python odysseus-odysseus:latest -m pytest -q tests/agents/test_workspace_privilege.py --tb=short`

Expected: FAIL (module not found)

- [ ] **Step 3: Write minimal implementation**

```python
"""Human-workspace write privilege for interactive Chat vs Agent."""

from __future__ import annotations

HUMAN_WORKSPACE_MUTATING_OPS = frozenset(
    {
        "notes.write",
        "documents.index",
        "mail.send",
        "calendar.write",
        "memory.write",
        "tasks.write",
    }
)


def human_workspace_writable(*, user_requested_agent: bool) -> bool:
    """Return True only when the user explicitly chose Agent mode."""

    return bool(user_requested_agent)


def workspace_grants_for_turn(
    *,
    user_requested_agent: bool,
    requested: tuple[str, ...] = (),
) -> tuple[str, ...]:
    """Chat turns never receive owner-workspace grants, including auto-escalation."""

    if not human_workspace_writable(user_requested_agent=user_requested_agent):
        return ()
    return tuple(requested)


def is_human_workspace_mutating_action(tool_name: str | None) -> bool:
    """Whether an OpenHands/MCP action mutates Odysseus production records."""

    raw = str(tool_name or "").strip()
    if not raw:
        return False
    if raw in HUMAN_WORKSPACE_MUTATING_OPS:
        return True
    dotted = raw.replace("__", ".").replace("_", ".")
    return any(dotted.endswith(op) or op in dotted for op in HUMAN_WORKSPACE_MUTATING_OPS)
```

- [ ] **Step 4: Run tests and make sure they pass**

Run: `docker run --rm -e PYTHONDONTWRITEBYTECODE=1 -e PYTHON_DOTENV_DISABLED=1 -v /Users/maxholden/odysseus:/app -w /app --entrypoint python odysseus-odysseus:latest -m pytest -q tests/agents/test_workspace_privilege.py --tb=short`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add services/agents/workspace_privilege.py tests/agents/test_workspace_privilege.py
git commit -m "feat: add Chat vs Agent human-workspace write privilege"
```

---

### Task 2: Chat composer send uses OpenHands

**Files:**
- Modify: `routes/chat_routes.py` (`chat_stream`: model-picker gate ~1263; `elif chat_mode == "chat"` ~1963)
- Modify: `tests/test_chat_openhands_binding.py`
- Modify as needed: `tests/test_foreground_model_routing.py` functions that call `_chat_stream_endpoint(..., "chat")` and monkeypatch `chat_routes.stream_llm_with_fallback`

**Interfaces:**
- Consumes: none (this task does not yet pass grants)
- Produces: interactive `mode=chat` (and Agent) composer turns, except `compare_mode`, call `stream_governed_agent`; Chat no longer requires an Odysseus model

- [ ] **Step 1: Write the failing tests**

Replace `test_agent_mode_skips_odysseus_model_picker` in `tests/test_chat_openhands_binding.py` with:

```python
from pathlib import Path


def test_interactive_openhands_turns_skip_odysseus_model_picker():
    """OpenHands owns the LLM for Chat and Agent; compare still uses Odysseus models."""
    source = Path("routes/chat_routes.py").read_text(encoding="utf-8")
    stream = source.split("async def chat_stream", 1)[1]
    gated = stream.split("if compare_mode:", 1)[1]
    assert "No model selected for this chat" in gated[:1200]
    assert "Selected model endpoint is not configured" in gated[:1200]
    picker = stream.split("if compare_mode:", 1)[0]
    assert "No model selected for this chat" not in picker


def test_chat_mode_does_not_call_stream_llm_in_chat_stream():
    source = Path("routes/chat_routes.py").read_text(encoding="utf-8")
    stream = source.split("async def chat_stream", 1)[1].split("async def chat_resume", 1)[0]
    assert "stream_governed_agent" in stream
    chat_branch = stream.split('elif chat_mode == "chat"', 1)
    if len(chat_branch) > 1:
        body = chat_branch[1].split("else:", 1)[0]
        assert "stream_llm_with_fallback" not in body or "compare_mode" in body
    assert 'elif chat_mode == "chat":' not in stream or "compare_mode" in stream.split(
        'elif chat_mode == "chat"', 1
    )[0][-80:] + stream.split('elif chat_mode == "chat"', 1)[1][:200]
```

Keep `test_chat_route_passes_binding_kwargs` unchanged.

Tighten the source assertion so it is not ambiguous: after the image-generation early return, Chat without compare must not enter `stream_llm_with_fallback`. Preferred structure:

```python
def test_non_compare_chat_uses_governed_agent_not_stream_llm():
    source = Path("routes/chat_routes.py").read_text(encoding="utf-8")
    stream = source.split("async def chat_stream", 1)[1].split("async def chat_resume", 1)[0]
    assert "async for chunk in stream_governed_agent(" in stream
    # Non-compare Chat must not keep the old tool-less stream_llm branch.
    assert "# ── Chat mode: call stream_llm directly, NO tools, NO document access ──" not in stream
```

Use this third test as the authoritative one; drop the ambiguous `test_chat_mode_does_not_call_stream_llm_in_chat_stream` if both would overlap.

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker run --rm -e PYTHONDONTWRITEBYTECODE=1 -e PYTHON_DOTENV_DISABLED=1 -v /Users/maxholden/odysseus:/app -w /app --entrypoint python odysseus-odysseus:latest -m pytest -q tests/test_chat_openhands_binding.py --tb=short`

Expected: FAIL (picker still gated on `user_requested_agent`; chat comment / `stream_llm` branch still present)

- [ ] **Step 3: Implement**

1. In `chat_stream`, replace the block:

```python
            if not user_requested_agent:
                if not getattr(sess, "model", "").strip():
                    raise HTTPException(
                        400,
                        "No model selected for this chat. Open the model picker and choose one before sending.",
                    )
                if not (getattr(sess, "endpoint_url", "") or "").strip():
                    raise HTTPException(400, "Selected model endpoint is not configured")
```

with:

```python
            if compare_mode:
                if not getattr(sess, "model", "").strip():
                    raise HTTPException(
                        400,
                        "No model selected for this chat. Open the model picker and choose one before sending.",
                    )
                if not (getattr(sess, "endpoint_url", "") or "").strip():
                    raise HTTPException(400, "Selected model endpoint is not configured")
```

2. Delete the `elif chat_mode == "chat":` branch that calls `stream_llm_with_fallback` (the block whose comment is `Chat mode: call stream_llm directly, NO tools, NO document access`) so image generation still returns early and every remaining Chat/Agent turn uses the existing `else` `stream_governed_agent` loop.

3. If compare-mode Chat still needs the Odysseus LLM pane path, keep `stream_llm_with_fallback` only behind `compare_mode` (for example `elif compare_mode and chat_mode == "chat":` using the previous chat-mode body). Do not keep a non-compare Chat `stream_llm` path.

4. Do not change `#model-picker-wrap` markup. Do not hide the picker.

5. Update `tests/test_foreground_model_routing.py` tests that send `mode=chat` through `chat_stream` and assert Odysseus `stream_llm_with_fallback` behavior. Point those tests at `stream_governed_agent` (same fake agent stream already installed by `_chat_stream_endpoint`) or skip them with a one-line reason that non-compare Chat is OpenHands now. Do not rewrite the whole file. Known `_chat_stream_endpoint(..., "chat")` call sites: compaction persistence, allowlist, form endpoint id (~536, ~590, ~1069, ~1729). Agent-mode tests in that file must stay green.

- [ ] **Step 4: Run tests**

Run: `docker run --rm -e PYTHONDONTWRITEBYTECODE=1 -e PYTHON_DOTENV_DISABLED=1 -v /Users/maxholden/odysseus:/app -w /app --entrypoint python odysseus-odysseus:latest -m pytest -q tests/test_chat_openhands_binding.py tests/test_foreground_model_routing.py tests/test_agent_ui_js.py --tb=short`

Expected: PASS. If a leftover Chat `stream_llm` test fails, fix only that test or the compare-mode guard — do not restore non-compare Chat `stream_llm`.

- [ ] **Step 5: Commit**

```bash
git add routes/chat_routes.py tests/test_chat_openhands_binding.py tests/test_foreground_model_routing.py
git commit -m "feat: send interactive Chat through OpenHands Agent Server"
```

---

### Task 3: Wire Chat deny of human-workspace writes

**Files:**
- Modify: `services/agents/legacy_bridge.py` (`stream_governed_agent`)
- Modify: `routes/chat_routes.py` (workspace auto-bind; `stream_governed_agent` kwargs)
- Modify: `tests/agents/test_legacy_bridge.py`
- Modify: `tests/test_chat_openhands_binding.py` (source asserts for kwargs)
- Modify: `tests/test_agent_ui_js.py`

**Interfaces:**
- Consumes: Task 1 functions; `user_requested_agent` already computed in `chat_stream` before auto-escalation
- Produces: `stream_governed_agent` forwards `workspace_grants` and `human_workspace_writable`; Chat does not emit `pending_confirmation` for mutating domain actions; owner workspace is not auto-bound unless the user chose Agent

- [ ] **Step 1: Write the failing tests**

Append to `tests/agents/test_legacy_bridge.py`:

```python
def test_chat_turn_sends_empty_workspace_grants():
    client = ScriptedClient([])
    _collect(
        dispatcher=AgentDispatcher(client=client),
        messages=[{"role": "user", "content": "hi"}],
        turn_id="t-chat-ws",
        user_requested_agent=False,
        workspace_grants=("/Users/me/proj",),
        poll_timeout_s=0,
    )
    assert client.calls[0]["workspace_grants"] == ()


def test_agent_turn_keeps_workspace_grants():
    client = ScriptedClient([])
    _collect(
        dispatcher=AgentDispatcher(client=client),
        messages=[{"role": "user", "content": "hi"}],
        turn_id="t-agent-ws",
        user_requested_agent=True,
        workspace_grants=("/Users/me/proj",),
        poll_timeout_s=0,
    )
    assert client.calls[0]["workspace_grants"] == ("/Users/me/proj",)


def test_chat_does_not_confirm_human_workspace_mutating_actions():
    action = {"id": "a1", "kind": "ActionEvent", "tool_name": "mail.send"}
    client = ScriptedClient(
        [
            action,
            {"id": "s1", "kind": "ConversationStateUpdate", "status": "paused"},
        ]
    )
    chunks = _collect(
        dispatcher=AgentDispatcher(client=client),
        messages=[{"role": "user", "content": "send"}],
        turn_id="t-chat-deny",
        user_requested_agent=False,
        poll_timeout_s=0,
    )
    assert '"type": "pending_confirmation"' not in "".join(chunks)


def test_chat_still_confirms_sandbox_actions():
    action = {"id": "a1", "kind": "ActionEvent", "tool_name": "terminal"}
    client = ScriptedClient(
        [
            action,
            {"id": "s1", "kind": "ConversationStateUpdate", "status": "paused"},
        ]
    )
    chunks = _collect(
        dispatcher=AgentDispatcher(client=client),
        messages=[{"role": "user", "content": "run"}],
        turn_id="t-chat-term",
        user_requested_agent=False,
        poll_timeout_s=0,
    )
    assert '"type": "pending_confirmation"' in "".join(chunks)
```

Append to `tests/test_chat_openhands_binding.py`:

```python
def test_chat_stream_passes_user_requested_agent_into_governed_stream():
    source = Path("routes/chat_routes.py").read_text(encoding="utf-8")
    call = source.split("async for chunk in stream_governed_agent(", 1)[1].split("):", 1)[0]
    assert "user_requested_agent=user_requested_agent" in call
    assert "workspace_grants=" in call


def test_index_has_no_engineering_admin_controls():
    html = Path("static/index.html").read_text(encoding="utf-8")
    lowered = html.lower()
    assert "add agent" not in lowered
    assert "add workflow" not in lowered
    assert "agent-canvas-frame" not in html
```

The last test may live in `tests/test_agent_ui_js.py` instead if that file already loads `_HTML`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker run --rm -e PYTHONDONTWRITEBYTECODE=1 -e PYTHON_DOTENV_DISABLED=1 -v /Users/maxholden/odysseus:/app -w /app --entrypoint python odysseus-odysseus:latest -m pytest -q tests/agents/test_legacy_bridge.py tests/test_chat_openhands_binding.py tests/test_agent_ui_js.py --tb=short`

Expected: FAIL (kwargs unused; confirmations still emitted; source asserts miss)

- [ ] **Step 3: Implement**

In `stream_governed_agent`, import Task 1 helpers. When building `DispatchRequest`:

```python
    writable = human_workspace_writable(
        user_requested_agent=bool(kwargs.get("user_requested_agent")),
    )
    grants = workspace_grants_for_turn(
        user_requested_agent=bool(kwargs.get("user_requested_agent")),
        requested=tuple(kwargs.get("workspace_grants") or ()),
    )
    ref = dispatcher.dispatch(
        DispatchRequest(
            request_id=request_id,
            archetype=str(kwargs.get("archetype") or "chat"),
            payload={"text": _user_text(messages)},
            conversation_id=conversation_id,
            agent_profile_id=profile,
            workspace_grants=grants,
        )
    )
```

When handling `ActionEvent`, emit `pending_confirmation` only if `writable` or the action is not human-workspace mutating:

```python
            if event.get("kind") == "ActionEvent" and eid and eid not in emitted_pending:
                tool_name = event.get("tool_name") or event.get("tool")
                if writable or not is_human_workspace_mutating_action(
                    str(tool_name) if tool_name is not None else None
                ):
                    emitted_pending.add(eid)
                    yield "data: " + json.dumps({
                        "type": "pending_confirmation",
                        "event_id": eid,
                    }) + "\n\n"
```

Leave `test_pending_confirmation_emitted_once_per_event_id` green: it does not pass `user_requested_agent`, so `writable` is False… **stop.** Default without the kwarg must not break existing Agent tests that omit it. Use:

```python
    if "user_requested_agent" in kwargs:
        writable = human_workspace_writable(
            user_requested_agent=bool(kwargs.get("user_requested_agent")),
        )
    else:
        writable = True
```

Chat/Agent `chat_stream` always passes `user_requested_agent` explicitly.

In `chat_stream`:

- Do not auto-bind a message-path workspace unless `user_requested_agent` is true. The current block that sets `workspace` from `_resolve_workspace_from_message_path` and auto-escalates must not grant writes on a Chat toggle. Keep intent auto-escalation for tools (notes/search) as it exists; it must not set `user_requested_agent`.
- Pass into `stream_governed_agent`:

```python
                        user_requested_agent=user_requested_agent,
                        workspace=(workspace or None) if user_requested_agent else None,
                        workspace_grants=workspace_grants_for_turn(
                            user_requested_agent=user_requested_agent,
                            requested=((workspace,) if workspace else ()),
                        ),
```

- Do not copy sandbox files into the owner tree on auto-escalation (there is no such copier today; do not add one).
- Do not add Canvas engineering screens.

- [ ] **Step 4: Run tests**

Run: `docker run --rm -e PYTHONDONTWRITEBYTECODE=1 -e PYTHON_DOTENV_DISABLED=1 -v /Users/maxholden/odysseus:/app -w /app --entrypoint python odysseus-odysseus:latest -m pytest -q tests/agents/test_legacy_bridge.py tests/agents/test_workspace_privilege.py tests/agents/test_dispatcher.py tests/test_chat_openhands_binding.py tests/test_agent_ui_js.py --tb=short`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add services/agents/legacy_bridge.py routes/chat_routes.py tests/agents/test_legacy_bridge.py tests/test_chat_openhands_binding.py tests/test_agent_ui_js.py
git commit -m "feat: deny human-workspace writes in Chat mode"
```
