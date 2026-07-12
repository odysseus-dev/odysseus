# Subagent/Actor System + Memory Persistence — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use compose:subagent (recommended) or compose:execute to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port MiMo-Code's subagent/actor system and memory persistence to Odysseus, enabling child agent loops with isolated contexts, structured result protocols, and file-based memory.

**Architecture:** Four new modules in `src/agent/` (actor.py, spawn.py, inbox.py, memory_persist.py). The actor system manages lifecycle and communication. Spawn mechanics run child agent loops. Inbox provides message passing. Memory persistence stores structured state in markdown files.

**Tech Stack:** Python 3.9+ (with `from __future__ import annotations`), asyncio, existing FastAPI infrastructure. No new dependencies.

---

## File Structure

| File | Responsibility |
|------|---------------|
| `src/agent/actor.py` | Actor dataclass, ActorRegistry, lifecycle management |
| `src/agent/spawn.py` | Subagent/peer spawning, context inheritance, return format |
| `src/agent/inbox.py` | Actor-to-actor message passing, notifications |
| `src/agent/memory_persist.py` | MEMORY.md, checkpoint.md, notes.md, tasks/<id>/progress.md |
| `tests/test_actor.py` | Unit tests for actor system |
| `tests/test_spawn.py` | Unit tests for spawning |
| `tests/test_inbox.py` | Unit tests for inbox |
| `tests/test_memory_persist.py` | Unit tests for memory persistence |

---

### Task 1: Actor/Subagent system

**Covers:** [S1] Actor lifecycle, registry, status tracking

**Files:**
- Create: `src/agent/actor.py`
- Create: `tests/test_actor.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_actor.py
"""Tests for src/agent/actor.py"""
from __future__ import annotations
import asyncio
import pytest
from src.agent.actor import (
    Actor,
    ActorMode,
    ActorStatus,
    ActorOutcome,
    ActorRegistry,
)


def test_actor_creation():
    actor = Actor(
        id="explore-1",
        session_id="sess-123",
        mode=ActorMode.SUBAGENT,
    )
    assert actor.id == "explore-1"
    assert actor.session_id == "sess-123"
    assert actor.mode == ActorMode.SUBAGENT
    assert actor.status == ActorStatus.PENDING
    assert actor.parent_id is None


def test_actor_modes():
    assert ActorMode.SUBAGENT.value == "subagent"
    assert ActorMode.PEER.value == "peer"


def test_actor_statuses():
    assert ActorStatus.PENDING.value == "pending"
    assert ActorStatus.RUNNING.value == "running"
    assert ActorStatus.IDLE.value == "idle"


def test_actor_outcomes():
    assert ActorOutcome.SUCCESS.value == "success"
    assert ActorOutcome.FAILURE.value == "failure"
    assert ActorOutcome.CANCELLED.value == "cancelled"


def test_registry_register():
    reg = ActorRegistry()
    actor = Actor(id="explore-1", session_id="s1", mode=ActorMode.SUBAGENT)
    reg.register(actor)
    assert reg.get("explore-1") is actor


def test_registry_list_by_session():
    reg = ActorRegistry()
    reg.register(Actor(id="a1", session_id="s1", mode=ActorMode.SUBAGENT))
    reg.register(Actor(id="a2", session_id="s1", mode=ActorMode.PEER))
    reg.register(Actor(id="a3", session_id="s2", mode=ActorMode.SUBAGENT))
    actors = reg.list_by_session("s1")
    assert len(actors) == 2


def test_registry_list_by_parent():
    reg = ActorRegistry()
    reg.register(Actor(id="main", session_id="s1", mode=ActorMode.SUBAGENT))
    reg.register(Actor(id="explore-1", session_id="s1", mode=ActorMode.SUBAGENT, parent_id="main"))
    reg.register(Actor(id="explore-2", session_id="s1", mode=ActorMode.SUBAGENT, parent_id="main"))
    children = reg.list_by_parent("main")
    assert len(children) == 2


def test_registry_allocate_id():
    reg = ActorRegistry()
    id1 = reg.allocate_id("explore")
    id2 = reg.allocate_id("explore")
    assert id1 == "explore-1"
    assert id2 == "explore-2"


def test_registry_allocate_id_different_types():
    reg = ActorRegistry()
    id1 = reg.allocate_id("explore")
    id2 = reg.allocate_id("general")
    assert id1 == "explore-1"
    assert id2 == "general-1"


def test_registry_update_status():
    reg = ActorRegistry()
    actor = Actor(id="a1", session_id="s1", mode=ActorMode.SUBAGENT)
    reg.register(actor)
    reg.update_status("a1", ActorStatus.RUNNING)
    assert reg.get("a1").status == ActorStatus.RUNNING


def test_registry_update_outcome():
    reg = ActorRegistry()
    actor = Actor(id="a1", session_id="s1", mode=ActorMode.SUBAGENT)
    reg.register(actor)
    reg.update_status("a1", ActorStatus.IDLE, outcome=ActorOutcome.SUCCESS)
    assert reg.get("a1").outcome == ActorOutcome.SUCCESS


def test_registry_list_active():
    reg = ActorRegistry()
    reg.register(Actor(id="a1", session_id="s1", mode=ActorMode.SUBAGENT, status=ActorStatus.RUNNING))
    reg.register(Actor(id="a2", session_id="s1", mode=ActorMode.SUBAGENT, status=ActorStatus.IDLE))
    active = reg.list_active()
    assert len(active) == 1
    assert active[0].id == "a1"


def test_registry_render_for_agent():
    reg = ActorRegistry()
    reg.register(Actor(id="explore-1", session_id="s1", mode=ActorMode.SUBAGENT, status=ActorStatus.RUNNING))
    reg.register(Actor(id="general-1", session_id="s1", mode=ActorMode.SUBAGENT, status=ActorStatus.IDLE, outcome=ActorOutcome.SUCCESS))
    text = reg.render_for_agent()
    assert "explore-1" in text
    assert "running" in text.lower()


@pytest.mark.asyncio
async def test_actor_wait_timeout():
    reg = ActorRegistry()
    actor = Actor(id="a1", session_id="s1", mode=ActorMode.SUBAGENT)
    reg.register(actor)
    with pytest.raises(asyncio.TimeoutError):
        await reg.wait("a1", timeout=0.1)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_actor.py -v --noconftest`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement actor.py**

```python
# src/agent/actor.py
"""Actor/Subagent system — lifecycle management and registry.

Ported from MiMo-Code patterns:
- Actor dataclass with id, session, mode, status, outcome
- ActorRegistry for tracking all actors
- Status lifecycle: pending -> running -> idle (with outcome)
- Two modes: subagent (same session) and peer (new session)
- Render for agent prompt injection
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class ActorMode(Enum):
    SUBAGENT = "subagent"  # Shares parent session
    PEER = "peer"          # New child session


class ActorStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    IDLE = "idle"


class ActorOutcome(Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    CANCELLED = "cancelled"


@dataclass
class Actor:
    """An actor (subagent or peer) in the system."""
    id: str
    session_id: str
    mode: ActorMode
    status: ActorStatus = ActorStatus.PENDING
    outcome: Optional[ActorOutcome] = None
    parent_id: Optional[str] = None
    tool_allowlist: Optional[set] = None
    context_mode: str = "none"  # none, state, full
    background: bool = False
    error: Optional[str] = None
    turn_count: int = 0
    _waiters: List[asyncio.Future] = field(default_factory=list, repr=False)

    @property
    def is_active(self) -> bool:
        return self.status in (ActorStatus.PENDING, ActorStatus.RUNNING)


class ActorRegistry:
    """Registry tracking all actors."""

    def __init__(self) -> None:
        self._actors: Dict[str, Actor] = {}
        self._counters: Dict[str, int] = {}

    def register(self, actor: Actor) -> None:
        self._actors[actor.id] = actor
        logger.info(f"Actor registered: {actor.id} (mode={actor.mode.value}, session={actor.session_id})")

    def get(self, id: str) -> Optional[Actor]:
        return self._actors.get(id)

    def list_by_session(self, session_id: str) -> List[Actor]:
        return [a for a in self._actors.values() if a.session_id == session_id]

    def list_by_parent(self, parent_id: str) -> List[Actor]:
        return [a for a in self._actors.values() if a.parent_id == parent_id]

    def list_active(self) -> List[Actor]:
        return [a for a in self._actors.values() if a.is_active]

    def allocate_id(self, agent_type: str) -> str:
        count = self._counters.get(agent_type, 0) + 1
        self._counters[agent_type] = count
        return f"{agent_type}-{count}"

    def update_status(
        self,
        id: str,
        status: ActorStatus,
        outcome: Optional[ActorOutcome] = None,
        error: Optional[str] = None,
    ) -> None:
        actor = self._actors.get(id)
        if not actor:
            return
        actor.status = status
        if outcome:
            actor.outcome = outcome
        if error:
            actor.error = error
        if status == ActorStatus.RUNNING:
            actor.turn_count += 1
        # Notify waiters
        if status == ActorStatus.IDLE:
            for fut in actor._waiters:
                if not fut.done():
                    fut.set_result(actor)
            actor._waiters.clear()
        logger.info(f"Actor {id} status: {status.value}" + (f" outcome={outcome.value}" if outcome else ""))

    async def wait(self, id: str, timeout: float = 600.0) -> Actor:
        """Wait for an actor to reach idle state."""
        actor = self._actors.get(id)
        if not actor:
            raise ValueError(f"Actor {id} not found")
        if actor.status == ActorStatus.IDLE:
            return actor
        fut = asyncio.get_event_loop().create_future()
        actor._waiters.append(fut)
        return await asyncio.wait_for(fut, timeout=timeout)

    def cancel(self, id: str) -> bool:
        """Cancel an actor."""
        actor = self._actors.get(id)
        if not actor or not actor.is_active:
            return False
        self.update_status(id, ActorStatus.IDLE, outcome=ActorOutcome.CANCELLED)
        return True

    def render_for_agent(self) -> str:
        """Render active actors as text for agent prompt injection."""
        active = self.list_active()
        if not active:
            return ""
        lines = ["## Active actors"]
        for a in active:
            outcome_str = f" ({a.outcome.value})" if a.outcome else ""
            lines.append(f"- `{a.id}` — {a.mode.value}, {a.status.value}{outcome_str}")
        return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_actor.py -v --noconftest`
Expected: All 14 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/agent/actor.py tests/test_actor.py
git commit -m "feat(agent): add actor/subagent system with lifecycle management and registry"
```

---

### Task 2: Spawning mechanics

**Covers:** [S2] Subagent/peer spawning, context inheritance, return format

**Files:**
- Create: `src/agent/spawn.py`
- Create: `tests/test_spawn.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_spawn.py
"""Tests for src/agent/spawn.py"""
from __future__ import annotations
import pytest
from src.agent.spawn import (
    ReturnFormat,
    parse_return_header,
    ContextMode,
    SpawnConfig,
)


def test_return_format_parse_success():
    text = """**Status**: success
**Summary**: Found all relevant files

- src/main.py: entry point
- src/utils.py: helper functions

**Files touched**: src/main.py, src/utils.py
**Findings worth promoting**: Main entry point uses FastAPI"""
    result = parse_return_header(text)
    assert result.status == "success"
    assert result.summary == "Found all relevant files"


def test_return_format_parse_failed():
    text = """**Status**: failed
**Summary**: Could not connect to database"""
    result = parse_return_header(text)
    assert result.status == "failed"
    assert result.summary == "Could not connect to database"


def test_return_format_parse_partial():
    text = """**Status**: partial
**Summary**: Fixed 3 of 5 bugs"""
    result = parse_return_header(text)
    assert result.status == "partial"


def test_return_format_parse_blocked():
    text = """**Status**: blocked
**Summary**: Waiting for user input"""
    result = parse_return_header(text)
    assert result.status == "blocked"


def test_return_format_parse_no_header():
    text = "Just a normal response without headers."
    result = parse_return_header(text)
    assert result.status is None
    assert result.summary is None
    assert result.body == text


def test_return_format_parse_malformed():
    text = "**Status**: invalid_status\n**Summary**: test"
    result = parse_return_header(text)
    assert result.status is None


def test_context_mode_values():
    assert ContextMode.NONE.value == "none"
    assert ContextMode.STATE.value == "state"
    assert ContextMode.FULL.value == "full"


def test_spawn_config_defaults():
    config = SpawnConfig(
        agent_type="explore",
        task="Find all Python files",
        session_id="s1",
    )
    assert config.agent_type == "explore"
    assert config.mode.value == "subagent"
    assert config.context_mode.value == "none"
    assert config.background is False
    assert config.timeout == 600.0


def test_spawn_config_with_allowlist():
    config = SpawnConfig(
        agent_type="explore",
        task="Search the codebase",
        session_id="s1",
        tool_allowlist={"read_file", "glob", "grep"},
    )
    assert config.tool_allowlist == {"read_file", "glob", "grep"}


def test_spawn_config_peer_mode():
    config = SpawnConfig(
        agent_type="general",
        task="Implement feature",
        session_id="s1",
        mode="peer",
        context_mode="full",
    )
    assert config.mode.value == "peer"
    assert config.context_mode.value == "full"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_spawn.py -v --noconftest`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement spawn.py**

```python
# src/agent/spawn.py
"""Spawning mechanics — subagent/peer creation, context inheritance, return format.

Ported from MiMo-Code patterns:
- Two modes: subagent (same session) and peer (new session)
- Context inheritance: none, state, full
- Return format protocol for structured results
- Tool filtering by permissions and allowlist
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Set

logger = logging.getLogger(__name__)


class ContextMode(Enum):
    NONE = "none"
    STATE = "state"
    FULL = "full"


@dataclass
class ReturnFormat:
    """Parsed return header from a subagent."""
    status: Optional[str] = None  # success, partial, failed, blocked
    summary: Optional[str] = None
    body: str = ""


_STATUS_RE = re.compile(r"\*\*Status\*\*:\s*(success|partial|failed|blocked)", re.IGNORECASE)
_SUMMARY_RE = re.compile(r"\*\*Summary\*\*:\s*(.+)", re.IGNORECASE)


def parse_return_header(text: str) -> ReturnFormat:
    """Parse the structured return format from a subagent response.
    
    Expected format:
        **Status**: success | partial | failed | blocked
        **Summary**: <one sentence>
        
        [deliverable body]
        
        **Files touched**: <paths>
        **Findings worth promoting**: <bullets>
    """
    status_match = _STATUS_RE.search(text)
    summary_match = _SUMMARY_RE.search(text)
    
    if not status_match:
        return ReturnFormat(body=text)
    
    status = status_match.group(1).lower()
    summary = summary_match.group(1).strip() if summary_match else None
    
    # Extract body (everything after the header block)
    body = text
    if status_match:
        # Find end of header block (first blank line after status)
        header_end = status_match.end()
        blank_match = re.search(r"\n\s*\n", text[header_end:])
        if blank_match:
            body = text[header_end + blank_match.end():].strip()
        else:
            body = text[header_end:].strip()
    
    return ReturnFormat(status=status, summary=summary, body=body)


@dataclass
class SpawnConfig:
    """Configuration for spawning a subagent."""
    agent_type: str  # explore, general, build, plan, etc.
    task: str
    session_id: str
    mode: str = "subagent"  # subagent or peer
    context_mode: str = "none"  # none, state, full
    tool_allowlist: Optional[Set[str]] = None
    background: bool = False
    timeout: float = 600.0
    parent_id: Optional[str] = None
    workspace: Optional[str] = None

    @property
    def mode_enum(self):
        from src.agent.actor import ActorMode
        return ActorMode.SUBAGENT if self.mode == "subagent" else ActorMode.PEER

    @property
    def context_mode_enum(self):
        return ContextMode(self.context_mode)


# Return format instruction appended to non-specialized subagent tasks
RETURN_FORMAT_INSTRUCTION = """

## Return format (required)

**Status**: success | partial | failed | blocked
**Summary**: <one sentence describing what happened>

[deliverable body]

**Files touched**: <comma-separated paths or "(none)">
**Findings worth promoting**: <bullet list, or "(none)">
"""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_spawn.py -v --noconftest`
Expected: All 10 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/agent/spawn.py tests/test_spawn.py
git commit -m "feat(agent): add spawning mechanics with return format protocol"
```

---

### Task 3: Inbox system

**Covers:** [S3] Actor-to-actor message passing, notifications

**Files:**
- Create: `src/agent/inbox.py`
- Create: `tests/test_inbox.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_inbox.py
"""Tests for src/agent/inbox.py"""
from __future__ import annotations
import asyncio
import pytest
from src.agent.inbox import Inbox, InboxMessage


def test_inbox_message_creation():
    msg = InboxMessage(
        sender_id="explore-1",
        receiver_id="main",
        content="Found 5 Python files in src/",
        type="actor_notification",
    )
    assert msg.sender_id == "explore-1"
    assert msg.receiver_id == "main"
    assert msg.type == "actor_notification"


def test_inbox_send_and_receive():
    inbox = Inbox()
    msg = InboxMessage(
        sender_id="explore-1",
        receiver_id="main",
        content="Task complete",
        type="actor_notification",
    )
    inbox.send(msg)
    messages = inbox.receive("main")
    assert len(messages) == 1
    assert messages[0].content == "Task complete"


def test_inbox_receive_clears():
    inbox = Inbox()
    inbox.send(InboxMessage(sender_id="a", receiver_id="b", content="msg1", type="text"))
    inbox.receive("b")
    messages = inbox.receive("b")
    assert len(messages) == 0


def test_inbox_receive_filtered():
    inbox = Inbox()
    inbox.send(InboxMessage(sender_id="a", receiver_id="b", content="msg1", type="text"))
    inbox.send(InboxMessage(sender_id="c", receiver_id="b", content="msg2", type="actor_notification"))
    messages = inbox.receive("b", type_filter="actor_notification")
    assert len(messages) == 1
    assert messages[0].content == "msg2"


def test_inbox_send_notification():
    inbox = Inbox()
    inbox.send_notification(
        sender_id="explore-1",
        receiver_id="main",
        status="success",
        summary="Found all files",
    )
    messages = inbox.receive("main")
    assert len(messages) == 1
    assert "success" in messages[0].content.lower()
    assert "found all files" in messages[0].content.lower()


def test_inbox_multiple_receivers():
    inbox = Inbox()
    inbox.send(InboxMessage(sender_id="a", receiver_id="b", content="to b", type="text"))
    inbox.send(InboxMessage(sender_id="a", receiver_id="c", content="to c", type="text"))
    inbox.send(InboxMessage(sender_id="a", receiver_id="b", content="to b again", type="text"))
    
    b_msgs = inbox.receive("b")
    c_msgs = inbox.receive("c")
    assert len(b_msgs) == 2
    assert len(c_msgs) == 1


def test_inbox_is_empty():
    inbox = Inbox()
    assert inbox.is_empty("main") is True
    inbox.send(InboxMessage(sender_id="a", receiver_id="main", content="msg", type="text"))
    assert inbox.is_empty("main") is False


def test_inbox_pending_count():
    inbox = Inbox()
    assert inbox.pending_count("main") == 0
    inbox.send(InboxMessage(sender_id="a", receiver_id="main", content="msg1", type="text"))
    inbox.send(InboxMessage(sender_id="a", receiver_id="main", content="msg2", type="text"))
    assert inbox.pending_count("main") == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_inbox.py -v --noconftest`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement inbox.py**

```python
# src/agent/inbox.py
"""Actor communication — inbox for message passing between actors.

Ported from MiMo-Code patterns:
- Inbox service for actor-to-actor messaging
- Typed messages (text, actor_notification, etc.)
- Auto-notification on actor completion
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class InboxMessage:
    """A message in the actor inbox."""
    sender_id: str
    receiver_id: str
    content: str
    type: str = "text"  # text, actor_notification, etc.


class Inbox:
    """Inbox for actor-to-actor communication."""
    
    def __init__(self) -> None:
        self._messages: Dict[str, List[InboxMessage]] = defaultdict(list)

    def send(self, message: InboxMessage) -> None:
        """Send a message to an actor's inbox."""
        self._messages[message.receiver_id].append(message)
        logger.debug(f"Inbox: {message.sender_id} -> {message.receiver_id} ({message.type})")

    def receive(
        self,
        actor_id: str,
        type_filter: Optional[str] = None,
    ) -> List[InboxMessage]:
        """Receive and clear messages for an actor.
        
        Args:
            actor_id: The actor to receive messages for
            type_filter: If provided, only return messages of this type
        """
        messages = self._messages.get(actor_id, [])
        if type_filter:
            messages = [m for m in messages if m.type == type_filter]
        # Clear received messages
        self._messages[actor_id] = [
            m for m in self._messages.get(actor_id, [])
            if m.type != type_filter if type_filter else False
        ]
        return messages

    def send_notification(
        self,
        sender_id: str,
        receiver_id: str,
        status: str,
        summary: str,
        body: str = "",
        error: Optional[str] = None,
    ) -> None:
        """Send a structured actor completion notification."""
        content = f"**Status**: {status}\n**Summary**: {summary}"
        if body:
            content += f"\n\n{body}"
        if error:
            content += f"\n\n**Error**: {error}"
        
        self.send(InboxMessage(
            sender_id=sender_id,
            receiver_id=receiver_id,
            content=content,
            type="actor_notification",
        ))

    def is_empty(self, actor_id: str) -> bool:
        return len(self._messages.get(actor_id, [])) == 0

    def pending_count(self, actor_id: str) -> int:
        return len(self._messages.get(actor_id, []))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_inbox.py -v --noconftest`
Expected: All 9 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/agent/inbox.py tests/test_inbox.py
git commit -m "feat(agent): add inbox system for actor-to-actor communication"
```

---

### Task 4: Memory persistence

**Covers:** [S4] File-based memory persistence (MEMORY.md, checkpoint.md, notes.md)

**Files:**
- Create: `src/agent/memory_persist.py`
- Create: `tests/test_memory_persist.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_memory_persist.py
"""Tests for src/agent/memory_persist.py"""
from __future__ import annotations
import os
import tempfile
import pytest
from src.agent.memory_persist import (
    MemoryStore,
    CheckpointStore,
    NotesStore,
    TaskProgressStore,
)


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield d


def test_memory_store_read_write(tmp_dir):
    store = MemoryStore(tmp_dir)
    store.write("## Rules\n- Use Python 3.9+")
    content = store.read()
    assert "Rules" in content
    assert "Python 3.9+" in content


def test_memory_store_append(tmp_dir):
    store = MemoryStore(tmp_dir)
    store.write("## Rules\n- Rule 1")
    store.append("## Architecture\n- FastAPI backend")
    content = store.read()
    assert "Rule 1" in content
    assert "FastAPI backend" in content


def test_memory_store_initial_content(tmp_dir):
    store = MemoryStore(tmp_dir)
    content = store.read()
    assert "Project memory" in content


def test_checkpoint_store_sections(tmp_dir):
    store = CheckpointStore(tmp_dir)
    store.update_section("active_intent", "User wants to fix the bug")
    store.update_section("next_action", "Run tests")
    content = store.read()
    assert "User wants to fix the bug" in content
    assert "Run tests" in content


def test_checkpoint_store_all_sections(tmp_dir):
    store = CheckpointStore(tmp_dir)
    sections = store.list_sections()
    assert "active_intent" in sections
    assert "next_action" in sections
    assert "directives" in sections
    assert "task_tree" in sections
    assert "current_work" in sections
    assert "files_and_code" in sections
    assert "discovered_knowledge" in sections
    assert "errors_and_fixes" in sections
    assert "live_resources" in sections
    assert "design_decisions" in sections
    assert "open_notes" in sections


def test_checkpoint_store_get_section(tmp_dir):
    store = CheckpointStore(tmp_dir)
    store.update_section("active_intent", "Fix the bug")
    content = store.get_section("active_intent")
    assert "Fix the bug" in content


def test_notes_store_append(tmp_dir):
    store = NotesStore(tmp_dir)
    store.append("Important finding about the architecture")
    store.append("Another note about deployment")
    content = store.read()
    assert "Important finding" in content
    assert "Another note" in content


def test_notes_store_format(tmp_dir):
    store = NotesStore(tmp_dir)
    store.append("Test note")
    content = store.read()
    assert "##" in content  # Should have timestamp headers


def test_task_progress_store(tmp_dir):
    store = TaskProgressStore(tmp_dir)
    store.write_progress("T1", "Implemented feature X, tests passing")
    content = store.read_progress("T1")
    assert "Implemented feature X" in content


def test_task_progress_store_multiple(tmp_dir):
    store = TaskProgressStore(tmp_dir)
    store.write_progress("T1", "Task 1 progress")
    store.write_progress("T2", "Task 2 progress")
    assert "Task 1" in store.read_progress("T1")
    assert "Task 2" in store.read_progress("T2")


def test_task_progress_list(tmp_dir):
    store = TaskProgressStore(tmp_dir)
    store.write_progress("T1", "progress 1")
    store.write_progress("T2", "progress 2")
    tasks = store.list_tasks()
    assert "T1" in tasks
    assert "T2" in tasks
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_memory_persist.py -v --noconftest`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement memory_persist.py**

```python
# src/agent/memory_persist.py
"""Memory persistence — file-based memory system.

Ported from MiMo-Code patterns:
- MEMORY.md: project-level persistent knowledge
- checkpoint.md: session-level structured state (11 sections)
- notes.md: agent scratchpad
- tasks/<id>/progress.md: per-task journal
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import List, Optional


class _FileStore:
    """Base class for file-based storage."""
    
    def __init__(self, base_dir: str, filename: str) -> None:
        self._path = os.path.join(base_dir, filename)
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
    
    def read(self) -> str:
        if os.path.exists(self._path):
            with open(self._path, "r", encoding="utf-8") as f:
                return f.read()
        return ""
    
    def write(self, content: str) -> None:
        with open(self._path, "w", encoding="utf-8") as f:
            f.write(content)
    
    def append(self, content: str) -> None:
        existing = self.read()
        sep = "\n\n" if existing.strip() else ""
        self.write(existing + sep + content)


class MemoryStore(_FileStore):
    """Project-level persistent memory (MEMORY.md)."""
    
    INITIAL_CONTENT = """# Project memory
_Durable project-level knowledge. Persists across all sessions in this project._

## Project context
_(What is this project? What are its key characteristics?)_

## Rules
_Hard constraints from user that every session must respect._

## Architecture decisions
_Major design choices with rationale._

## Discovered durable knowledge
_Cross-task facts that survive sessions._
"""
    
    def __init__(self, base_dir: str) -> None:
        super().__init__(base_dir, "MEMORY.md")
        if not os.path.exists(self._path):
            self.write(self.INITIAL_CONTENT)


class CheckpointStore:
    """Session-level checkpoint (checkpoint.md) with 11 sections."""
    
    SECTIONS = [
        "active_intent",
        "next_action",
        "directives",
        "task_tree",
        "current_work",
        "files_and_code",
        "discovered_knowledge",
        "errors_and_fixes",
        "live_resources",
        "design_decisions",
        "open_notes",
    ]
    
    SECTION_HEADERS = {
        "active_intent": "## §1 Active intent",
        "next_action": "## §2 Next concrete action",
        "directives": "## §3 Directives",
        "task_tree": "## §4 Task tree",
        "current_work": "## §5 Current work",
        "files_and_code": "## §6 Files and code sections",
        "discovered_knowledge": "## §7 Discovered knowledge",
        "errors_and_fixes": "## §8 Errors and fixes",
        "live_resources": "## §9 Live resources",
        "design_decisions": "## §10 Design decisions",
        "open_notes": "## §11 Open notes",
    }
    
    def __init__(self, base_dir: str) -> None:
        self._path = os.path.join(base_dir, "checkpoint.md")
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        self._sections: dict[str, str] = {s: "" for s in self.SECTIONS}
        if os.path.exists(self._path):
            self._load()
    
    def _load(self) -> None:
        content = self.read()
        current_section = None
        current_lines = []
        for line in content.split("\n"):
            for section, header in self.SECTION_HEADERS.items():
                if line.startswith(header):
                    if current_section:
                        self._sections[current_section] = "\n".join(current_lines).strip()
                    current_section = section
                    current_lines = []
                    break
            else:
                current_lines.append(line)
        if current_section:
            self._sections[current_section] = "\n".join(current_lines).strip()
    
    def read(self) -> str:
        if os.path.exists(self._path):
            with open(self._path, "r", encoding="utf-8") as f:
                return f.read()
        return ""
    
    def list_sections(self) -> List[str]:
        return list(self.SECTIONS)
    
    def get_section(self, section: str) -> str:
        return self._sections.get(section, "")
    
    def update_section(self, section: str, content: str) -> None:
        self._sections[section] = content
        self._save()
    
    def _save(self) -> None:
        lines = ["# Session checkpoint", ""]
        for section in self.SECTIONS:
            header = self.SECTION_HEADERS[section]
            lines.append(header)
            lines.append("")
            lines.append(self._sections.get(section, ""))
            lines.append("")
        with open(self._path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))


class NotesStore(_FileStore):
    """Agent scratchpad (notes.md)."""
    
    def __init__(self, base_dir: str) -> None:
        super().__init__(base_dir, "notes.md")
    
    def append(self, content: str) -> None:
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        entry = f"## [turn · {timestamp}]\n{content}"
        super().append(entry)


class TaskProgressStore:
    """Per-task progress journals (tasks/<id>/progress.md)."""
    
    def __init__(self, base_dir: str) -> None:
        self._base_dir = os.path.join(base_dir, "tasks")
        os.makedirs(self._base_dir, exist_ok=True)
    
    def _task_dir(self, task_id: str) -> str:
        d = os.path.join(self._base_dir, task_id)
        os.makedirs(d, exist_ok=True)
        return d
    
    def write_progress(self, task_id: str, content: str) -> None:
        path = os.path.join(self._task_dir(task_id), "progress.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"# Task {task_id} Progress\n\n{content}")
    
    def read_progress(self, task_id: str) -> str:
        path = os.path.join(self._task_dir(task_id), "progress.md")
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        return ""
    
    def list_tasks(self) -> List[str]:
        if not os.path.exists(self._base_dir):
            return []
        return [
            d for d in os.listdir(self._base_dir)
            if os.path.isdir(os.path.join(self._base_dir, d))
        ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_memory_persist.py -v --noconftest`
Expected: All 12 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/agent/memory_persist.py tests/test_memory_persist.py
git commit -m "feat(agent): add memory persistence with MEMORY.md, checkpoint.md, notes.md"
```

---

### Task 5: Integration + deployment

**Covers:** [S5] Package exports, Docker deploy, verification

**Files:**
- Modify: `src/agent/__init__.py`

- [ ] **Step 1: Update agent package exports**

```python
# src/agent/__init__.py
"""Odysseus agent loop package — modular rewrite based on MiMo-Code patterns."""
from __future__ import annotations

# Loop detection
from src.agent.loop_detector import LoopDetector, RecoveryLevel, StableSignature
# Recovery
from src.agent.recovery import RecoveryPrompts, IntentSupervisor
# Prompt building
from src.agent.prompt_builder import PromptBuilder, PromptSection
# Context management
from src.agent.checkpoint import ContextManager, CompactionResult
# Tool framework
from src.agent.tool import Tool, ToolResult, ToolContext, RecoverableError, ToolInfo
# Permissions
from src.agent.permission import Action, Rule, Ruleset, evaluate, AGENT_PERMISSIONS
# Registry
from src.agent.tool_registry import ToolRegistry
# Actor/Actor system
from src.agent.actor import Actor, ActorMode, ActorStatus, ActorOutcome, ActorRegistry
# Spawning
from src.agent.spawn import SpawnConfig, ReturnFormat, parse_return_header, RETURN_FORMAT_INSTRUCTION
# Communication
from src.agent.inbox import Inbox, InboxMessage
# Memory persistence
from src.agent.memory_persist import MemoryStore, CheckpointStore, NotesStore, TaskProgressStore

__all__ = [
    # Loop detection
    "LoopDetector", "RecoveryLevel", "StableSignature",
    # Recovery
    "RecoveryPrompts", "IntentSupervisor",
    # Prompt building
    "PromptBuilder", "PromptSection",
    # Context management
    "ContextManager", "CompactionResult",
    # Tool framework
    "Tool", "ToolResult", "ToolContext", "RecoverableError", "ToolInfo",
    # Permissions
    "Action", "Rule", "Ruleset", "evaluate", "AGENT_PERMISSIONS",
    # Registry
    "ToolRegistry",
    # Actor system
    "Actor", "ActorMode", "ActorStatus", "ActorOutcome", "ActorRegistry",
    # Spawning
    "SpawnConfig", "ReturnFormat", "parse_return_header", "RETURN_FORMAT_INSTRUCTION",
    # Communication
    "Inbox", "InboxMessage",
    # Memory persistence
    "MemoryStore", "CheckpointStore", "NotesStore", "TaskProgressStore",
]
```

- [ ] **Step 2: Run ALL agent module tests**

Run: `python -m pytest tests/test_loop_detector.py tests/test_recovery.py tests/test_prompt_builder.py tests/test_checkpoint.py tests/test_tool.py tests/test_permission.py tests/test_tool_registry.py tests/test_actor.py tests/test_spawn.py tests/test_inbox.py tests/test_memory_persist.py -v --noconftest`
Expected: All tests PASS

- [ ] **Step 3: Verify imports**

Run: `python -c "from src.agent import Actor, ActorRegistry, SpawnConfig, Inbox, MemoryStore; print('All imports OK')"`

- [ ] **Step 4: Deploy to Docker**

```bash
echo "rt67we45" | sudo -S docker cp src/agent/. odysseus-odysseus-1:/app/src/agent/
echo "rt67we45" | sudo -S docker exec odysseus-odysseus-1 python -c "from src.agent import Actor, ActorRegistry, SpawnConfig, Inbox, MemoryStore; print('Docker OK')"
```

- [ ] **Step 5: Commit**

```bash
git add src/agent/__init__.py
git commit -m "feat(agent): complete Phase 3 — subagent/actor system, inbox, memory persistence"
```
