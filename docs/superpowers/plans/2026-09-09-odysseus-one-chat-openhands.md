# Odysseus One-Chat OpenHands Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the existing Odysseus composer the only interactive chat, backed by OpenHands Agent Server, with Canvas as a same-conversation link.

**Architecture:** Persist `openhands_conversation_id` and `agent_profile_id` on the Odysseus session. Each send dispatches a new turn through `AgentDispatcher` / `OpenHandsClient.create_or_resume`, then `stream_governed_agent` polls Agent Server events and yields Odysseus SSE until the run is idle. The Gate 10 side panel is deleted; Approve / Cancel / Resume / Canvas bind on the chat bar.

**Tech Stack:** Flask/FastAPI Odysseus, `services/agents/*`, Agent Server 1.45 conversation API, existing chat SSE (`delta`, `type`), vanilla JS in `static/js/agents.js`.

## Global Constraints

- Odysseus chat is the only interactive product surface; OpenHands Agent Server is the only interactive execution backend.
- Agent Canvas remains a separate engineering UI; Odysseus embeds no Canvas iframe and does not port Canvas features.
- Do not start 9router; do not restyle, relocate, disable, or rewire the model selector (`#model-picker-wrap`).
- Hermes is not a selectable agent type.
- Do not delete `src/agent_loop.py` or `execute_tool_block` (Task 18 remains held).
- Remove `#agent-platform-panel` and do not unhide anything for `?agents=1`.
- Do not change scheduled/background Automation ownership.
- Do not treat Odysseus session id as an OpenHands conversation id.
- Dispatch `request_id` is per turn, not the session id (session-scoped idempotency would swallow the second send).
- Local tests: `docker run --rm -e PYTHONDONTWRITEBYTECODE=1 -e PYTHON_DOTENV_DISABLED=1 -v /Users/maxholden/odysseus:/app -w /app --entrypoint python odysseus-odysseus:latest -m pytest -q <paths> --tb=short`
- Commit messages: `feat:` / `test:` / `fix:` on this branch. Do not push. Do not edit the parent cutover design/plan bodies.

---

### Task 1: Session binding store

**Files:**
- Create: `services/agents/session_binding.py`
- Create: `tests/agents/test_session_binding.py`
- Modify: `core/database.py` (`Session` columns + `_migrate_add_openhands_binding` called next to `_migrate_add_crew_member_id()` at line 2134)

**Interfaces:**
- Consumes: nothing from later tasks
- Produces:
  - `SessionBinding(conversation_id: str | None, agent_profile_id: str)`
  - `ALLOWED_AGENT_PROFILES = ("odysseus", "opencode")`
  - `DEFAULT_AGENT_PROFILE = "odysseus"`
  - `MemoryBindingStore.get(session_id: str) -> SessionBinding`
  - `MemoryBindingStore.put(session_id: str, binding: SessionBinding) -> None`
  - `normalize_agent_profile_id(value: str | None) -> str` (`hermes` and unknown → `odysseus`)

- [ ] **Step 1: Write the failing test**

```python
from tests.agents._import_agents import ensure_agents_package

ensure_agents_package()

from services.agents.session_binding import (
    DEFAULT_AGENT_PROFILE,
    MemoryBindingStore,
    SessionBinding,
    normalize_agent_profile_id,
)


def test_new_session_has_no_conversation_and_native_profile():
    store = MemoryBindingStore()
    binding = store.get("sess-1")
    assert binding.conversation_id is None
    assert binding.agent_profile_id == DEFAULT_AGENT_PROFILE


def test_put_round_trips_conversation_and_profile():
    store = MemoryBindingStore()
    store.put("sess-1", SessionBinding(conversation_id="conv-1", agent_profile_id="opencode"))
    assert store.get("sess-1") == SessionBinding("conv-1", "opencode")


def test_hermes_is_not_a_selectable_profile():
    assert normalize_agent_profile_id("hermes") == "odysseus"
    assert normalize_agent_profile_id("opencode") == "opencode"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker run --rm -e PYTHONDONTWRITEBYTECODE=1 -e PYTHON_DOTENV_DISABLED=1 -v /Users/maxholden/odysseus:/app -w /app --entrypoint python odysseus-odysseus:latest -m pytest -q tests/agents/test_session_binding.py --tb=short`

Expected: FAIL collecting or importing `session_binding`.

- [ ] **Step 3: Write minimal implementation**

```python
# services/agents/session_binding.py
from __future__ import annotations

from dataclasses import dataclass

ALLOWED_AGENT_PROFILES = ("odysseus", "opencode")
DEFAULT_AGENT_PROFILE = "odysseus"


@dataclass(frozen=True)
class SessionBinding:
    conversation_id: str | None
    agent_profile_id: str


def normalize_agent_profile_id(value: str | None) -> str:
    if value in ALLOWED_AGENT_PROFILES:
        return value
    return DEFAULT_AGENT_PROFILE


class MemoryBindingStore:
    def __init__(self) -> None:
        self._rows: dict[str, SessionBinding] = {}

    def get(self, session_id: str) -> SessionBinding:
        return self._rows.get(
            session_id,
            SessionBinding(None, DEFAULT_AGENT_PROFILE),
        )

    def put(self, session_id: str, binding: SessionBinding) -> None:
        self._rows[session_id] = SessionBinding(
            binding.conversation_id,
            normalize_agent_profile_id(binding.agent_profile_id),
        )
```

Add on `core/database.py` `Session`:

```python
openhands_conversation_id = Column(String, nullable=True)
agent_profile_id = Column(String, nullable=True)
```

Add `_migrate_add_openhands_binding` using the same `PRAGMA table_info(sessions)` / `ALTER TABLE` pattern as `_migrate_add_crew_member_id`, and call it from the existing migrate runner beside that function.

- [ ] **Step 4: Run test to verify it passes**

Run: same docker pytest command as Step 2.

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/agents/session_binding.py tests/agents/test_session_binding.py core/database.py
git commit -m "feat: persist OpenHands conversation binding on sessions"
```

---

### Task 2: Per-turn dispatch with agent profile

**Files:**
- Modify: `services/agents/dispatcher.py`
- Modify: `services/agents/openhands_client.py`
- Modify: `tests/agents/test_dispatcher.py`
- Modify: `tests/agents/test_openhands_client.py`

**Interfaces:**
- Consumes: `normalize_agent_profile_id` from Task 1
- Produces:
  - `DispatchRequest.agent_profile_id: str = "odysseus"`
  - `OpenHandsClient.create_or_resume(..., agent_profile_id: str = "odysseus")`
  - Create payload includes `"agent_profile_id": agent_profile_id` when profile is `opencode`; native uses current settings create (no Hermes)
  - `autotitle: False` remains on create

- [ ] **Step 1: Write the failing tests**

In `tests/agents/test_dispatcher.py`:

```python
def test_dispatch_forwards_agent_profile_id():
    client = FakeClient()
    dispatcher = AgentDispatcher(client=client)
    dispatcher.dispatch(
        DispatchRequest(
            request_id="turn-2",
            archetype="chat",
            payload={"text": "hi"},
            conversation_id="conv-keep",
            agent_profile_id="opencode",
        )
    )
    assert client.agent_server_calls[0]["agent_profile_id"] == "opencode"
```

In `tests/agents/test_openhands_client.py` `test_create_sends_workspace_and_initial_message`, add:

```python
assert body["autotitle"] is False
```

New test:

```python
def test_create_sends_opencode_profile_id():
    transport = RecordingTransport()
    client = OpenHandsClient(transport=transport, agent_server_base="http://agent-server")
    client.create_or_resume(
        conversation_id=None,
        message="hi",
        request_id="o1",
        profile_revision=1,
        archetype_version=1,
        agent_profile_id="opencode",
    )
    body = transport.calls[1][2]
    assert body["agent_profile_id"] == "opencode"
    assert body["autotitle"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker run --rm -e PYTHONDONTWRITEBYTECODE=1 -e PYTHON_DOTENV_DISABLED=1 -v /Users/maxholden/odysseus:/app -w /app --entrypoint python odysseus-odysseus:latest -m pytest -q tests/agents/test_dispatcher.py tests/agents/test_openhands_client.py --tb=short`

Expected: FAIL on missing `agent_profile_id`.

- [ ] **Step 3: Write minimal implementation**

Add `agent_profile_id: str = "odysseus"` to `DispatchRequest`. Pass it through `dispatch()` into `create_or_resume`.

In `create_or_resume`, accept `agent_profile_id: str = "odysseus"`. On create only, if `agent_profile_id == "opencode"`, set `body["agent_profile_id"]` to the OpenCode profile id used live (`opencode` string is enough if Canvas registered that id; if create 422s on unknown id, pass the stored Canvas profile UUID via env `OPENHANDS_OPENCODE_PROFILE_ID` and default `"opencode"`). Do not send `hermes`. Keep `autotitle: False`.

- [ ] **Step 4: Run tests to verify they pass**

Run: same command as Step 2.

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/agents/dispatcher.py services/agents/openhands_client.py tests/agents/test_dispatcher.py tests/agents/test_openhands_client.py
git commit -m "feat: pass agent type into OpenHands create"
```

---

### Task 3: Stream projected events until idle

**Files:**
- Modify: `services/agents/legacy_bridge.py`
- Create: `tests/agents/test_legacy_bridge.py`

**Interfaces:**
- Consumes: `AgentDispatcher.dispatch`, `OpenHandsClient.conversation_events`
- Produces: `stream_governed_agent` yields, in order:
  1. `data: {"type":"execution","execution_id":...,"conversation_id":...,"rebound":bool}\n\n`
  2. zero or more `data: {"delta": "<assistant text>"}\n\n` from new `MessageEvent`s with `source=agent`
  3. `data: {"type":"pending_confirmation","event_id":...}\n\n` when the latest unmatched `ActionEvent` exists
  4. `data: [DONE]\n\n` when conversation status is `finished`, `paused`, `completed`, `failed`, or `cancelled`, or after one poll if the fake client has no more events
- `conversation_id` kwarg is used only when it is an OpenHands id (non-empty and not equal to `session_id`)
- `request_id` defaults to `kwargs["turn_id"]` or a new `uuid4` hex, never bare `session_id`
- `agent_profile_id` forwarded to `DispatchRequest`
- If `conversation_id` is set and `agent_profile_id` differs from `kwargs.get("bound_agent_profile_id")`, dispatch with `conversation_id=None` and set `rebound=True`

- [ ] **Step 1: Write the failing tests**

```python
import asyncio
from tests.agents._import_agents import ensure_agents_package

ensure_agents_package()

from services.agents.dispatcher import AgentDispatcher
from services.agents.legacy_bridge import stream_governed_agent


class ScriptedClient:
    def __init__(self, events):
        self.events = events
        self.calls = []

    def create_or_resume(self, **kwargs):
        self.calls.append(kwargs)
        cid = kwargs.get("conversation_id") or "conv-new"
        return type("R", (), {"conversation_id": cid, "execution_id": f"agent-server:{cid}"})()

    def conversation_events(self, conversation_id):
        return list(self.events)


def _collect(**kwargs):
    return asyncio.run(_alist(kwargs))


async def _alist(kwargs):
    return [chunk async for chunk in stream_governed_agent(**kwargs)]


def test_second_turn_reuses_openhands_id_not_session_id():
    client = ScriptedClient([])
    dispatcher = AgentDispatcher(client=client)
    _collect(
        dispatcher=dispatcher,
        messages=[{"role": "user", "content": "hi"}],
        session_id="ody-session",
        conversation_id="conv-keep",
        turn_id="turn-2",
        agent_profile_id="odysseus",
        bound_agent_profile_id="odysseus",
    )
    assert client.calls[0]["conversation_id"] == "conv-keep"
    assert client.calls[0]["request_id"] == "turn-2"


def test_stream_emits_execution_delta_and_done():
    client = ScriptedClient(
        [
            {
                "id": "m1",
                "kind": "MessageEvent",
                "source": "agent",
                "content": [{"type": "text", "text": "pong"}],
            },
            {"id": "s1", "kind": "ConversationStateUpdate", "status": "finished"},
        ]
    )
    chunks = _collect(
        dispatcher=AgentDispatcher(client=client),
        messages=[{"role": "user", "content": "ping"}],
        turn_id="t1",
    )
    joined = "".join(chunks)
    assert '"type": "execution"' in joined
    assert '"delta": "pong"' in joined
    assert chunks[-1] == "data: [DONE]\n\n"


def test_profile_change_rebinds_new_conversation():
    client = ScriptedClient([])
    chunks = _collect(
        dispatcher=AgentDispatcher(client=client),
        messages=[{"role": "user", "content": "hi"}],
        conversation_id="conv-old",
        agent_profile_id="opencode",
        bound_agent_profile_id="odysseus",
        turn_id="t3",
    )
    assert client.calls[0]["conversation_id"] is None
    assert '"rebound": true' in "".join(chunks)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker run --rm -e PYTHONDONTWRITEBYTECODE=1 -e PYTHON_DOTENV_DISABLED=1 -v /Users/maxholden/odysseus:/app -w /app --entrypoint python odysseus-odysseus:latest -m pytest -q tests/agents/test_legacy_bridge.py --tb=short`

Expected: FAIL (`conversation_id` is session id or stream ends without `delta`).

- [ ] **Step 3: Write minimal implementation**

Replace `stream_governed_agent` body:

```python
import uuid

_IDLE = {"finished", "paused", "completed", "failed", "cancelled"}


def _assistant_text(event: dict) -> str:
    if event.get("kind") != "MessageEvent" or event.get("source") == "user":
        return ""
    parts = event.get("content") or event.get("text") or ""
    if isinstance(parts, str):
        return parts
    return "".join(
        block.get("text") or ""
        for block in parts
        if isinstance(block, dict)
    )


async def stream_governed_agent(...) -> AsyncIterator[str]:
    dispatcher = kwargs.pop("dispatcher", None) or AgentDispatcher()
    session_id = kwargs.get("session_id")
    requested = kwargs.get("conversation_id")
    conversation_id = requested if requested and requested != session_id else None
    profile = str(kwargs.get("agent_profile_id") or "odysseus")
    bound_profile = kwargs.get("bound_agent_profile_id")
    rebound = bool(conversation_id and bound_profile and bound_profile != profile)
    if rebound:
        conversation_id = None
    request_id = str(kwargs.get("turn_id") or kwargs.get("request_id") or uuid.uuid4().hex)
    ref = dispatcher.dispatch(
        DispatchRequest(
            request_id=request_id,
            archetype=str(kwargs.get("archetype") or "chat"),
            payload={"text": _user_text(messages)},
            conversation_id=conversation_id,
            agent_profile_id=profile,
        )
    )
    yield "data: " + json.dumps({
        "type": "execution",
        "execution_id": ref.automation_execution_id,
        "conversation_id": ref.conversation_id,
        "rebound": rebound,
    }) + "\n\n"
    if rebound:
        yield "data: " + json.dumps({
            "type": "delta",
            "delta": "Started a new OpenHands conversation because the agent type changed.",
        }) + "\n\n"
        yield "data: " + json.dumps({
            "delta": "Started a new OpenHands conversation because the agent type changed.",
        }) + "\n\n"
    seen: set[str] = set()
    idle = False
    pending_id = None
    for _ in range(20):
        events = dispatcher.client.conversation_events(ref.conversation_id)
        for event in events:
            eid = str(event.get("id") or "")
            if eid and eid in seen:
                continue
            if eid:
                seen.add(eid)
            text = _assistant_text(event)
            if text:
                yield "data: " + json.dumps({"delta": text}) + "\n\n"
            if event.get("kind") == "ActionEvent":
                pending_id = event.get("id")
            status = str(event.get("status") or "")
            if event.get("kind") == "ConversationStateUpdate" and status.lower() in _IDLE:
                idle = True
        if pending_id:
            yield "data: " + json.dumps({
                "type": "pending_confirmation",
                "event_id": pending_id,
            }) + "\n\n"
        if idle or not events:
            break
    yield "data: [DONE]\n\n"
```

Use only one rebound user-visible SSE (the `delta` form the chat route already persists). Drop the unused `type: delta` duplicate if you emit `{"delta": ...}` only.

Do not call `stream_agent_loop`.

- [ ] **Step 4: Run tests to verify they pass**

Run: same command as Step 2.

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/agents/legacy_bridge.py tests/agents/test_legacy_bridge.py
git commit -m "feat: stream OpenHands events into the product chat SSE"
```

---

### Task 4: Chat route binds the session

**Files:**
- Modify: `routes/chat_routes.py` (the `stream_governed_agent(...)` call near line 2336)
- Create: `tests/test_chat_openhands_binding.py`
- Modify: `core/session_manager.py` or the session persist path used after chat SSE `execution` events — only if `history_session` / DB session cannot already accept new attributes

**Interfaces:**
- Consumes: `SessionBinding`, `MemoryBindingStore` pattern; production uses SQL session columns from Task 1
- Produces: chat send passes `conversation_id=sess.openhands_conversation_id`, `agent_profile_id`, `bound_agent_profile_id`, `turn_id=uuid4().hex`; on `type==execution` persist `openhands_conversation_id` and `agent_profile_id` onto that session

- [ ] **Step 1: Write the failing test**

```python
"""Chat send must bind OpenHands conversation ids, never the Odysseus session id."""

from pathlib import Path


def test_chat_route_passes_binding_kwargs():
    source = Path("routes/chat_routes.py").read_text(encoding="utf-8")
    assert "openhands_conversation_id" in source
    assert "turn_id" in source
    assert "agent_profile_id" in source
    assert "bound_agent_profile_id" in source
```

Also add a function-level unit test if a small helper is extracted:

```python
# services/agents/session_binding.py
def conversation_kwarg(session_id: str, binding: SessionBinding) -> str | None:
    if binding.conversation_id and binding.conversation_id != session_id:
        return binding.conversation_id
    return None
```

Test that helper in `tests/agents/test_session_binding.py`.

- [ ] **Step 2: Run test to verify it fails**

Run: `docker run --rm -e PYTHONDONTWRITEBYTECODE=1 -e PYTHON_DOTENV_DISABLED=1 -v /Users/maxholden/odysseus:/app -w /app --entrypoint python odysseus-odysseus:latest -m pytest -q tests/test_chat_openhands_binding.py tests/agents/test_session_binding.py --tb=short`

Expected: FAIL (`openhands_conversation_id` missing from chat_routes).

- [ ] **Step 3: Write minimal implementation**

In `routes/chat_routes.py` before `async for chunk in stream_governed_agent(`:

```python
import uuid
from services.agents.session_binding import (
    DEFAULT_AGENT_PROFILE,
    conversation_kwarg,
    normalize_agent_profile_id,
)

_bound_cid = getattr(sess, "openhands_conversation_id", None)
_bound_profile = normalize_agent_profile_id(getattr(sess, "agent_profile_id", None))
_requested_profile = normalize_agent_profile_id(
    (ctx.raw if hasattr(ctx, "raw") else None)
    or None
)
```

Read `agent_profile_id` from the existing chat POST JSON body (add `body.get("agent_profile_id")` where the route already reads `session` / `message`). Do not read or write model-picker fields.

Add kwargs:

```python
conversation_id=conversation_kwarg(session, type("B", (), {
    "conversation_id": _bound_cid,
    "agent_profile_id": _bound_profile,
})()),
turn_id=uuid.uuid4().hex,
agent_profile_id=_requested_profile or _bound_profile,
bound_agent_profile_id=_bound_profile,
archetype="chat",
```

In the existing chunk parser (the `data: ` JSON branch), when `data.get("type") == "execution"`:

```python
_cid = data.get("conversation_id")
if _cid:
    sess.openhands_conversation_id = _cid
    sess.agent_profile_id = _requested_profile or _bound_profile
    # persist with the same SessionLocal/update path this function already uses for tokens
```

If the in-memory `sess` object has no attributes, set them and add the two columns to the SQL UPDATE this function already performs. Do not invent a second session table.

- [ ] **Step 4: Run tests to verify they pass**

Run: same command as Step 2, plus `tests/test_foreground_model_routing.py` if you touched the `stream_governed_agent` call signature in a way that could break the fake stream (kwargs-only is safe).

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add routes/chat_routes.py services/agents/session_binding.py tests/test_chat_openhands_binding.py tests/agents/test_session_binding.py
git commit -m "feat: bind Odysseus sessions to OpenHands conversations"
```

---

### Task 5: Chat-bar controls; delete the side panel

**Files:**
- Modify: `static/index.html` (remove `#agent-platform-panel` ~2550–2565; add controls in `.chat-input-right` before the send button, after the Agent/Chat toggle)
- Modify: `static/js/agents.js`
- Modify: `tests/test_agent_ui_js.py`
- Do not modify `#model-picker-wrap` / `#model-picker-btn` / `#model-picker-menu`

**Interfaces:**
- Consumes: `approveAgent`, `cancelAgent`, `resumeAgent`, `canvasUrl`, `renderAgentStatus` (keep these exports)
- Produces: `initAgentPlatform()` binds `#agent-chat-controls`; no `launchAgent` click handler; no `?agents` query gate
- Chat POST body includes `agent_profile_id` from `#agent-type-select`

- [ ] **Step 1: Write the failing tests**

Replace/extend `tests/test_agent_ui_js.py`:

```python
def test_index_has_no_side_panel():
    assert 'id="agent-platform-panel"' not in _HTML
    assert "agent-canvas-frame" not in _HTML
    assert 'id="agent-chat-controls"' in _HTML
    assert 'data-agent-approve' in _HTML
    assert 'data-agent-cancel' in _HTML
    assert 'data-agent-resume' in _HTML
    assert 'data-agent-canvas' in _HTML
    assert 'id="agent-type-select"' in _HTML
    assert "hermes" not in _HTML.lower() or "hermes" not in _HTML.split('id="agent-type-select"')[1][:400]


def test_model_picker_markup_unchanged():
    assert 'id="model-picker-wrap"' in _HTML
    assert 'id="model-picker-btn"' in _HTML
    assert 'id="model-picker-label"' in _HTML


def test_agents_js_has_no_query_gate_or_second_composer():
    assert "URLSearchParams" not in _JS or "agents" not in _JS
    assert "data-agent-input" not in _JS
    assert "data-agent-launch" not in _JS
    assert "initAgentPlatform" in _JS
```

If `URLSearchParams` remains for other reasons, assert `.has('agents')` is absent instead.

- [ ] **Step 2: Run test to verify it fails**

Run: `docker run --rm -e PYTHONDONTWRITEBYTECODE=1 -e PYTHON_DOTENV_DISABLED=1 -v /Users/maxholden/odysseus:/app -w /app --entrypoint python odysseus-odysseus:latest -m pytest -q tests/test_agent_ui_js.py --tb=short`

Expected: FAIL (panel still present).

- [ ] **Step 3: Write minimal implementation**

Delete the entire `<section id="agent-platform-panel" ...></section>`.

Inside `.chat-input-right`, after the mode toggle, insert:

```html
<div id="agent-chat-controls">
  <label class="sr-only" for="agent-type-select">Agent type</label>
  <select id="agent-type-select" aria-label="Agent type">
    <option value="odysseus">Native</option>
    <option value="opencode">OpenCode</option>
  </select>
  <span data-agent-status hidden>idle</span>
  <button type="button" data-agent-approve hidden>Approve</button>
  <button type="button" data-agent-cancel hidden>Cancel</button>
  <button type="button" data-agent-resume hidden>Resume</button>
  <a data-agent-canvas hidden rel="noopener noreferrer" target="_blank">Open in Canvas</a>
</div>
```

Reuse existing button classes (`input-icon-btn` / `mode-toggle-btn`) so it matches the bar. Do not add a second textarea.

Rewrite `initAgentPlatform` to use `#agent-chat-controls`:

```javascript
export function initAgentPlatform() {
  const root = document.getElementById('agent-chat-controls');
  if (!root) return;
  const canvas = root.querySelector('[data-agent-canvas]');
  const approve = root.querySelector('[data-agent-approve]');
  const cancel = root.querySelector('[data-agent-cancel]');
  const resume = root.querySelector('[data-agent-resume]');
  if (canvas) {
    canvas.setAttribute('rel', 'noopener noreferrer');
    canvas.setAttribute('target', '_blank');
  }
  window.__odysseusBindAgentExecution = function bind(execution) {
    if (!execution) return;
    if (execution.execution_id) root.dataset.executionId = execution.execution_id;
    if (canvas && execution.conversation_id) {
      canvas.hidden = false;
      canvas.setAttribute('href', canvasUrl(execution.conversation_id, window.OPENHANDS_CANVAS_URL || ''));
    }
    if (execution.pending_confirmation) {
      if (approve) approve.hidden = false;
    }
    if (cancel) cancel.hidden = !execution.execution_id;
    if (resume) resume.hidden = execution.status !== 'paused';
  };
  approve?.addEventListener('click', async () => {
    const id = root.dataset.executionId;
    if (id) await approveAgent(id, {});
  });
  cancel?.addEventListener('click', async () => {
    const id = root.dataset.executionId;
    if (id) await cancelAgent(id);
  });
  resume?.addEventListener('click', async () => {
    const id = root.dataset.executionId;
    if (id) await resumeAgent(id);
  });
}
```

In `static/js/chatStream.js` (or the existing SSE handler that already parses `data:` JSON), on `type === 'execution'` or `type === 'pending_confirmation'` call `window.__odysseusBindAgentExecution`. If chatStream is the wrong file, hook the same place `chat_routes` `delta` is consumed in JS — search `JSON.parse` + `delta` in `static/js/chatStream.js` / `static/js/chat.js`.

In the existing chat POST body builder, add `agent_profile_id: document.getElementById('agent-type-select')?.value || 'odysseus'`. Do not change model-picker JS.

Set `window.OPENHANDS_CANVAS_URL` from the same env-backed value already used by `AgentHttp` if a small inline in `index.html` already exposes config; otherwise default `''` and let `canvasUrl` join what `/api/agents/executions` would have used. Prefer reading `canvas_url` off the execution SSE if you add that field in Task 3 (`canvas_url` optional on the execution event).

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker run --rm -e PYTHONDONTWRITEBYTECODE=1 -e PYTHON_DOTENV_DISABLED=1 -v /Users/maxholden/odysseus:/app -w /app --entrypoint python odysseus-odysseus:latest -m pytest -q tests/test_agent_ui_js.py tests/test_agent_routes_openhands.py --tb=short`

Expected: PASS. Approve-without-pending remains `409`.

- [ ] **Step 5: Commit**

```bash
git add static/index.html static/js/agents.js static/js/chatStream.js static/js/chat.js tests/test_agent_ui_js.py
git commit -m "feat: move OpenHands controls onto the Odysseus chat bar"
```

---

### Task 6: Copy live files and verify the one composer

**Files:** none new. Live container bind is `/app` only if you `docker cp` as in Gate 10 leftovers.

- [ ] **Step 1: Copy runtime files into `odysseus-odysseus-1` if the image is not bind-mounted**

```bash
for f in \
  services/agents/session_binding.py \
  services/agents/dispatcher.py \
  services/agents/openhands_client.py \
  services/agents/legacy_bridge.py \
  routes/chat_routes.py \
  core/database.py \
  static/index.html \
  static/js/agents.js \
  static/js/chatStream.js \
  static/js/chat.js
 do
  [ -f /Users/maxholden/odysseus/$f ] && docker cp /Users/maxholden/odysseus/$f odysseus-odysseus-1:/app/$f
 done
docker restart odysseus-odysseus-1
```

- [ ] **Step 2: Health + UI checks**

```bash
curl -fsS http://127.0.0.1:7001/api/health
```

In the browser: open Odysseus (not Canvas). Confirm one composer, Agent/Chat toggle still present, model picker unchanged, Native/OpenCode select present, no top-right OpenHands panel, no second textarea. Send a message; transcript should receive assistant text from OpenHands; Canvas link appears for that conversation. `?agents=1` must not resurrect a panel.

- [ ] **Step 3: Commit only if Step 2 forced a fix**

No commit if verification only. If a fix landed, commit that fix with `fix:` and re-verify.

---

## Spec coverage

| Spec section | Task |
|---|---|
| 5.1 one composer / no Launch | 5 |
| 5.2 chat type = existing Agent/Chat | 4 (existing `disabled_tools` / mode), 5 (toggle stays) |
| 5.3 agent type Native/OpenCode; no Hermes | 1, 2, 5 |
| 5.3 profile switch rebind + note | 3 |
| 5.4 model selector unmodified | 5 test + global constraint |
| 5.5 bar controls; delete panel; ignore `?agents=1` | 5 |
| 5.6 one transcript | 3, 4, 5 |
| 6 binding + product stream | 1, 3, 4 |
| 7 errors | 3 (unreachable via existing chat errors), approve 409 already in `agent_routes` |
| 8 tests | each task |
| 9 held work | global constraints; no Task 18 / 9router / Canvas work |

## Placeholder / type check

- `SessionBinding`, `DispatchRequest.agent_profile_id`, `turn_id`, `conversation_kwarg`, `initAgentPlatform` / `#agent-chat-controls` names are consistent across tasks.
- No TBD/TODO left in steps.
