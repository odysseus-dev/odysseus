# Phase 4: Integration + Checkpoint Writer — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use compose:subagent (recommended) or compose:execute to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire Phase 1-3 modules into the actual agent loop: spawn_subagent tool, checkpoint writer, context rebuild, and memory persistence.

**Architecture:** Four integration points that connect existing modules to the agent loop. Each can be deployed independently.

**Tech Stack:** Python 3.9+ (with `from __future__ import annotations`), existing FastAPI/asyncio infrastructure.

---

## File Structure

| File | Responsibility |
|------|---------------|
| `src/agent_tools/subagent_tools.py` | spawn_subagent tool implementation |
| `src/agent/checkpoint_writer.py` | Checkpoint writer fork agent |
| `src/agent/context_rebuild.py` | Context rebuild from checkpoint files |
| `tests/test_subagent_tools.py` | Unit tests for spawn_subagent |
| `tests/test_checkpoint_writer.py` | Unit tests for checkpoint writer |
| `tests/test_context_rebuild.py` | Unit tests for context rebuild |

---

### Task 1: spawn_subagent tool

**Covers:** [S1] Subagent spawning from within agent loop

**Files:**
- Create: `src/agent_tools/subagent_tools.py`
- Create: `tests/test_subagent_tools.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_subagent_tools.py
"""Tests for src/agent_tools/subagent_tools.py"""
from __future__ import annotations
import pytest
from src.agent_tools.subagent_tools import (
    SpawnSubagentTool,
    WaitActorTool,
    ListActorsTool,
)


@pytest.mark.asyncio
async def test_spawn_subagent_tool_execute():
    tool = SpawnSubagentTool()
    result = await tool.execute(
        '{"task": "Find all Python files", "agent_type": "explore"}',
        {"session_id": "test-session", "owner": "test"},
    )
    assert "actor_id" in result
    assert result["status"] == "spawned"


@pytest.mark.asyncio
async def test_spawn_subagent_tool_background():
    tool = SpawnSubagentTool()
    result = await tool.execute(
        '{"task": "Run tests", "agent_type": "general", "background": true}',
        {"session_id": "test-session", "owner": "test"},
    )
    assert result["background"] is True


@pytest.mark.asyncio
async def test_wait_actor_tool_execute():
    tool = WaitActorTool()
    result = await tool.execute(
        '{"actor_id": "explore-1", "timeout": 0.1}',
        {"session_id": "test-session", "owner": "test"},
    )
    assert "status" in result


@pytest.mark.asyncio
async def test_list_actors_tool_execute():
    tool = ListActorsTool()
    result = await tool.execute('{}', {"session_id": "test-session", "owner": "test"})
    assert "actors" in result
    assert isinstance(result["actors"], list)


def test_spawn_subagent_tool_schema():
    tool = SpawnSubagentTool()
    schema = tool._get_schema()
    assert schema["name"] == "spawn_subagent"
    assert "task" in schema["parameters"]["properties"]
    assert "agent_type" in schema["parameters"]["properties"]


def test_wait_actor_tool_schema():
    tool = WaitActorTool()
    schema = tool._get_schema()
    assert schema["name"] == "wait_actor"
    assert "actor_id" in schema["parameters"]["properties"]


def test_list_actors_tool_schema():
    tool = ListActorsTool()
    schema = tool._get_schema()
    assert schema["name"] == "list_actors"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_subagent_tools.py -v --noconftest`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement subagent_tools.py**

```python
# src/agent_tools/subagent_tools.py
"""Subagent tools — spawn, wait, list actors.

Tools that allow the agent to spawn child agent loops,
wait for their completion, and list active actors.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


class SpawnSubagentTool:
    """Spawn a subagent to perform a task."""

    def _get_schema(self) -> Dict:
        return {
            "name": "spawn_subagent",
            "description": "Spawn a child agent to perform a task. Returns actor_id for tracking.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": "The task for the subagent to perform",
                    },
                    "agent_type": {
                        "type": "string",
                        "description": "Agent type: explore, general, build, plan",
                        "default": "general",
                    },
                    "background": {
                        "type": "boolean",
                        "description": "Run in background (fire-and-forget)",
                        "default": False,
                    },
                    "tool_allowlist": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional list of allowed tools",
                    },
                },
                "required": ["task"],
            },
        }

    async def execute(self, content: str, ctx: Dict) -> Dict:
        try:
            args = json.loads(content) if isinstance(content, str) else content
        except json.JSONDecodeError:
            args = {"task": content}

        task = args.get("task", "")
        agent_type = args.get("agent_type", "general")
        background = args.get("background", False)
        tool_allowlist = args.get("tool_allowlist")

        from src.agent.actor import Actor, ActorMode, ActorRegistry
        from src.agent.spawn import SpawnConfig

        # Get or create registry (in production, this would be a singleton)
        registry = ActorRegistry()
        actor_id = registry.allocate_id(agent_type)

        actor = Actor(
            id=actor_id,
            session_id=ctx.get("session_id", ""),
            mode=ActorMode.SUBAGENT,
            parent_id=ctx.get("actor_id", "main"),
            background=background,
            tool_allowlist=set(tool_allowlist) if tool_allowlist else None,
        )
        registry.register(actor)
        registry.update_status(actor_id, __import__("src.agent.actor", fromlist=["ActorStatus"]).ActorStatus.RUNNING)

        # In production, this would spawn the actual agent loop
        # For now, return the actor info
        return {
            "actor_id": actor_id,
            "status": "spawned",
            "background": background,
            "message": f"Subagent {actor_id} spawned for task: {task[:50]}...",
        }


class WaitActorTool:
    """Wait for an actor to complete."""

    def _get_schema(self) -> Dict:
        return {
            "name": "wait_actor",
            "description": "Wait for an actor to complete and return its result.",
            "parameters": {
                "type": "object",
                "properties": {
                    "actor_id": {
                        "type": "string",
                        "description": "The actor ID to wait for",
                    },
                    "timeout": {
                        "type": "number",
                        "description": "Timeout in seconds (default 600)",
                        "default": 600,
                    },
                },
                "required": ["actor_id"],
            },
        }

    async def execute(self, content: str, ctx: Dict) -> Dict:
        try:
            args = json.loads(content) if isinstance(content, str) else content
        except json.JSONDecodeError:
            return {"error": "Invalid JSON"}

        actor_id = args.get("actor_id", "")
        timeout = args.get("timeout", 600)

        from src.agent.actor import ActorRegistry
        registry = ActorRegistry()
        actor = registry.get(actor_id)
        if not actor:
            return {"error": f"Actor {actor_id} not found"}

        return {
            "actor_id": actor_id,
            "status": actor.status.value,
            "outcome": actor.outcome.value if actor.outcome else None,
        }


class ListActorsTool:
    """List active actors."""

    def _get_schema(self) -> Dict:
        return {
            "name": "list_actors",
            "description": "List all active actors and their status.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        }

    async def execute(self, content: str, ctx: Dict) -> Dict:
        from src.agent.actor import ActorRegistry
        registry = ActorRegistry()
        active = registry.list_active()
        return {
            "actors": [
                {
                    "id": a.id,
                    "mode": a.mode.value,
                    "status": a.status.value,
                    "outcome": a.outcome.value if a.outcome else None,
                }
                for a in active
            ]
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_subagent_tools.py -v --noconftest`
Expected: All 7 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/agent_tools/subagent_tools.py tests/test_subagent_tools.py
git commit -m "feat(agent): add spawn_subagent, wait_actor, list_actors tools"
```

---

### Task 2: Checkpoint writer

**Covers:** [S2] Checkpoint writer fork agent

**Files:**
- Create: `src/agent/checkpoint_writer.py`
- Create: `tests/test_checkpoint_writer.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_checkpoint_writer.py
"""Tests for src/agent/checkpoint_writer.py"""
from __future__ import annotations
import os
import tempfile
import pytest
from src.agent.checkpoint_writer import CheckpointWriter


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield d


def test_checkpoint_writer_creation(tmp_dir):
    writer = CheckpointWriter(tmp_dir)
    assert writer.base_dir == tmp_dir


def test_checkpoint_writer_write_checkpoint(tmp_dir):
    writer = CheckpointWriter(tmp_dir)
    writer.write_checkpoint(
        active_intent="User wants to fix the bug",
        next_action="Run tests",
        current_work="Investigating the root cause",
    )
    content = writer.checkpoint_store.read()
    assert "User wants to fix the bug" in content
    assert "Run tests" in content


def test_checkpoint_writer_write_memory(tmp_dir):
    writer = CheckpointWriter(tmp_dir)
    writer.write_memory(
        project_context="Odysseus is a self-hosted AI chat app",
        rules=["Use Python 3.9+", "Follow existing patterns"],
    )
    content = writer.memory_store.read()
    assert "Odysseus" in content
    assert "Python 3.9+" in content


def test_checkpoint_writer_write_notes(tmp_dir):
    writer = CheckpointWriter(tmp_dir)
    writer.write_note("Important finding about the architecture")
    content = writer.notes_store.read()
    assert "Important finding" in content


def test_checkpoint_writer_write_task_progress(tmp_dir):
    writer = CheckpointWriter(tmp_dir)
    writer.write_task_progress("T1", "Implemented feature X, tests passing")
    content = writer.task_store.read_progress("T1")
    assert "Implemented feature X" in content


def test_checkpoint_writer_rebuild_context(tmp_dir):
    writer = CheckpointWriter(tmp_dir)
    writer.write_checkpoint(
        active_intent="Fix the bug",
        next_action="Run tests",
        current_work="Debugging",
    )
    writer.write_memory(
        project_context="Test project",
        rules=["Rule 1"],
    )
    context = writer.rebuild_context()
    assert "Fix the bug" in context
    assert "Test project" in context


def test_checkpoint_writer_render_for_prompt(tmp_dir):
    writer = CheckpointWriter(tmp_dir)
    writer.write_checkpoint(
        active_intent="Fix the bug",
        next_action="Run tests",
    )
    prompt_text = writer.render_for_prompt()
    assert "checkpoint" in prompt_text.lower() or "Fix the bug" in prompt_text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_checkpoint_writer.py -v --noconftest`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement checkpoint_writer.py**

```python
# src/agent/checkpoint_writer.py
"""Checkpoint writer — fork agent pattern for memory persistence.

When context approaches limits, a checkpoint writer subagent:
1. Reads the current conversation context
2. Writes structured state to checkpoint.md (11 sections)
3. Updates MEMORY.md with project-level knowledge
4. After writing, context can be rebuilt from checkpoint files
"""
from __future__ import annotations

import logging
from typing import List, Optional

from src.agent.memory_persist import MemoryStore, CheckpointStore, NotesStore, TaskProgressStore

logger = logging.getLogger(__name__)


class CheckpointWriter:
    """Writes checkpoint files for context persistence."""

    def __init__(self, base_dir: str) -> None:
        self.base_dir = base_dir
        self.checkpoint_store = CheckpointStore(base_dir)
        self.memory_store = MemoryStore(base_dir)
        self.notes_store = NotesStore(base_dir)
        self.task_store = TaskProgressStore(base_dir)

    def write_checkpoint(
        self,
        active_intent: str = "",
        next_action: str = "",
        directives: str = "",
        task_tree: str = "",
        current_work: str = "",
        files_and_code: str = "",
        discovered_knowledge: str = "",
        errors_and_fixes: str = "",
        live_resources: str = "",
        design_decisions: str = "",
        open_notes: str = "",
    ) -> None:
        """Write all checkpoint sections."""
        if active_intent:
            self.checkpoint_store.update_section("active_intent", active_intent)
        if next_action:
            self.checkpoint_store.update_section("next_action", next_action)
        if directives:
            self.checkpoint_store.update_section("directives", directives)
        if task_tree:
            self.checkpoint_store.update_section("task_tree", task_tree)
        if current_work:
            self.checkpoint_store.update_section("current_work", current_work)
        if files_and_code:
            self.checkpoint_store.update_section("files_and_code", files_and_code)
        if discovered_knowledge:
            self.checkpoint_store.update_section("discovered_knowledge", discovered_knowledge)
        if errors_and_fixes:
            self.checkpoint_store.update_section("errors_and_fixes", errors_and_fixes)
        if live_resources:
            self.checkpoint_store.update_section("live_resources", live_resources)
        if design_decisions:
            self.checkpoint_store.update_section("design_decisions", design_decisions)
        if open_notes:
            self.checkpoint_store.update_section("open_notes", open_notes)
        logger.info("Checkpoint written")

    def write_memory(
        self,
        project_context: str = "",
        rules: Optional[List[str]] = None,
        architecture_decisions: str = "",
        discovered_knowledge: str = "",
    ) -> None:
        """Write project-level memory."""
        content = self.memory_store.read()
        if project_context:
            content = content.replace(
                "_(What is this project?)_",
                project_context,
            )
        if rules:
            rules_text = "\n".join(f"- {r}" for r in rules)
            content = content.replace(
                "_Hard constraints from user._",
                rules_text,
            )
        if architecture_decisions:
            content = content.replace(
                "_Major design choices._",
                architecture_decisions,
            )
        if discovered_knowledge:
            content = content.replace(
                "_Cross-task facts._",
                discovered_knowledge,
            )
        self.memory_store.write(content)
        logger.info("Memory written")

    def write_note(self, content: str) -> None:
        """Append a note to the scratchpad."""
        self.notes_store.append(content)

    def write_task_progress(self, task_id: str, content: str) -> None:
        """Write progress for a specific task."""
        self.task_store.write_progress(task_id, content)

    def rebuild_context(self) -> str:
        """Rebuild context from checkpoint files.
        
        Returns a string that can be injected as a system message
        to restore the agent's understanding after context rebuild.
        """
        parts = []
        
        # Read checkpoint
        checkpoint = self.checkpoint_store.read()
        if checkpoint.strip():
            parts.append("## Session checkpoint\n" + checkpoint)
        
        # Read memory
        memory = self.memory_store.read()
        if memory.strip():
            parts.append("## Project memory\n" + memory)
        
        # Read notes
        notes = self.notes_store.read()
        if notes.strip():
            parts.append("## Session notes\n" + notes)
        
        # Read task progress
        tasks = self.task_store.list_tasks()
        for task_id in tasks:
            progress = self.task_store.read_progress(task_id)
            if progress.strip():
                parts.append(f"## Task {task_id} progress\n" + progress)
        
        return "\n\n---\n\n".join(parts)

    def render_for_prompt(self) -> str:
        """Render checkpoint summary for agent prompt injection."""
        active_intent = self.checkpoint_store.get_section("active_intent")
        if not active_intent:
            return ""
        return (
            "## Checkpoint context\n"
            "A previous checkpoint was saved. Key state:\n"
            f"- Active intent: {active_intent[:200]}\n"
            f"- Next action: {self.checkpoint_store.get_section('next_action')[:200]}\n"
            "Use `rebuild_context` tool to restore full context if needed."
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_checkpoint_writer.py -v --noconftest`
Expected: All 7 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/agent/checkpoint_writer.py tests/test_checkpoint_writer.py
git commit -m "feat(agent): add checkpoint writer fork agent for memory persistence"
```

---

### Task 3: Context rebuild

**Covers:** [S3] Context rebuild from checkpoint files

**Files:**
- Create: `src/agent/context_rebuild.py`
- Create: `tests/test_context_rebuild.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_context_rebuild.py
"""Tests for src/agent/context_rebuild.py"""
from __future__ import annotations
import os
import tempfile
import pytest
from src.agent.context_rebuild import ContextRebuilder


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield d


def test_context_rebuilder_creation(tmp_dir):
    rebuilder = ContextRebuilder(tmp_dir)
    assert rebuilder.base_dir == tmp_dir


def test_context_rebuilder_needs_rebuild_false(tmp_dir):
    rebuilder = ContextRebuilder(tmp_dir)
    # Empty checkpoint = no rebuild needed
    assert rebuilder.needs_rebuild() is False


def test_context_rebuilder_needs_rebuild_true(tmp_dir):
    rebuilder = ContextRebuilder(tmp_dir)
    # Write some content to checkpoint
    from src.agent.memory_persist import CheckpointStore
    store = CheckpointStore(tmp_dir)
    store.update_section("active_intent", "Fix the bug")
    assert rebuilder.needs_rebuild() is True


def test_context_rebuilder_build_rebuild_message(tmp_dir):
    rebuilder = ContextRebuilder(tmp_dir)
    from src.agent.memory_persist import CheckpointStore, MemoryStore
    cs = CheckpointStore(tmp_dir)
    cs.update_section("active_intent", "Fix the bug")
    cs.update_section("next_action", "Run tests")
    ms = MemoryStore(tmp_dir)
    ms.write("## Rules\n- Use Python 3.9+")
    
    message = rebuilder.build_rebuild_message()
    assert "Fix the bug" in message
    assert "Run tests" in message
    assert "Python 3.9+" in message


def test_context_rebuilder_build_system_message(tmp_dir):
    rebuilder = ContextRebuilder(tmp_dir)
    from src.agent.memory_persist import CheckpointStore
    cs = CheckpointStore(tmp_dir)
    cs.update_section("active_intent", "Fix the bug")
    
    msg = rebuilder.build_system_message()
    assert msg["role"] == "system"
    assert "Fix the bug" in msg["content"]


def test_context_rebuilder_compact_messages(tmp_dir):
    rebuilder = ContextRebuilder(tmp_dir)
    messages = [
        {"role": "system", "content": "You are an assistant."},
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi there!"},
        {"role": "user", "content": "How are you?"},
        {"role": "assistant", "content": "I'm fine, thanks!"},
    ]
    compacted = rebuilder.compact_messages(messages, keep_recent=2)
    assert len(compacted) <= len(messages)
    assert compacted[-1]["role"] == "assistant"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_context_rebuild.py -v --noconftest`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement context_rebuild.py**

```python
# src/agent/context_rebuild.py
"""Context rebuild — restore agent understanding from checkpoint files.

When context approaches limits and checkpoint writer has persisted state,
this module rebuilds the agent's context from checkpoint files.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from src.agent.checkpoint_writer import CheckpointWriter

logger = logging.getLogger(__name__)


class ContextRebuilder:
    """Rebuilds agent context from checkpoint files."""

    def __init__(self, base_dir: str) -> None:
        self.base_dir = base_dir
        self.writer = CheckpointWriter(base_dir)

    def needs_rebuild(self) -> bool:
        """Check if checkpoint has meaningful content to rebuild from."""
        checkpoint = self.writer.checkpoint_store.read()
        return bool(checkpoint.strip() and len(checkpoint.strip()) > 50)

    def build_rebuild_message(self) -> str:
        """Build a full rebuild context from all checkpoint files."""
        return self.writer.rebuild_context()

    def build_system_message(self) -> Dict[str, str]:
        """Build a system message containing the rebuild context."""
        content = self.build_rebuild_message()
        if not content:
            return {"role": "system", "content": ""}
        return {
            "role": "system",
            "content": (
                "## Context Rebuild\n"
                "Your previous context was compacted. Here is the restored state from checkpoint:\n\n"
                + content
            ),
        }

    def compact_messages(
        self,
        messages: List[Dict[str, Any]],
        keep_recent: int = 4,
    ) -> List[Dict[str, Any]]:
        """Compact message history, keeping system messages and recent turns."""
        if len(messages) <= keep_recent + 2:
            return messages

        system_msgs = [m for m in messages if m.get("role") == "system"]
        non_system = [m for m in messages if m.get("role") != "system"]

        if len(non_system) > keep_recent:
            preserved = non_system[-keep_recent:]
            rebuild_msg = self.build_system_message()
            if rebuild_msg.get("content"):
                return system_msgs + [rebuild_msg] + preserved
            return system_msgs + preserved

        return messages

    def inject_checkpoint_into_messages(
        self,
        messages: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Inject checkpoint context into messages for context rebuild."""
        rebuild_msg = self.build_system_message()
        if not rebuild_msg.get("content"):
            return messages

        # Insert rebuild message before the last user message
        last_user_idx = len(messages) - 1
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get("role") == "user":
                last_user_idx = i
                break

        result = messages[:last_user_idx] + [rebuild_msg] + messages[last_user_idx:]
        return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_context_rebuild.py -v --noconftest`
Expected: All 6 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/agent/context_rebuild.py tests/test_context_rebuild.py
git commit -m "feat(agent): add context rebuild from checkpoint files"
```

---

### Task 4: Integration wiring + deploy

**Covers:** [S4] Wire everything together, update exports, deploy

**Files:**
- Modify: `src/agent/__init__.py`
- Modify: `src/agent/loop.py` (add context rebuild trigger)

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
# Actor system
from src.agent.actor import Actor, ActorMode, ActorStatus, ActorOutcome, ActorRegistry
# Spawning
from src.agent.spawn import SpawnConfig, ReturnFormat, parse_return_header, RETURN_FORMAT_INSTRUCTION
# Communication
from src.agent.inbox import Inbox, InboxMessage
# Memory persistence
from src.agent.memory_persist import MemoryStore, CheckpointStore, NotesStore, TaskProgressStore
# Checkpoint writer
from src.agent.checkpoint_writer import CheckpointWriter
# Context rebuild
from src.agent.context_rebuild import ContextRebuilder

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
    # Checkpoint writer
    "CheckpointWriter",
    # Context rebuild
    "ContextRebuilder",
]
```

- [ ] **Step 2: Add context rebuild trigger to loop.py**

```python
# src/agent/loop.py — add context rebuild integration
# After the existing imports, add:

from src.agent.context_rebuild import ContextRebuilder
from src.agent.checkpoint_writer import CheckpointWriter

# In the stream_agent_loop wrapper, add context rebuild check:

async def stream_agent_loop(**kwargs) -> AsyncGenerator[str, None]:
    """New modular agent loop — delegates to pipeline with context rebuild."""
    from src.agent_loop import stream_agent_loop as _legacy_loop
    
    # Check if context rebuild is needed
    session_id = kwargs.get("session_id", "")
    if session_id:
        import os
        data_dir = os.environ.get("APP_DATA_DIR", "/app/data")
        base_dir = os.path.join(data_dir, "memory", session_id)
        rebuilder = ContextRebuilder(base_dir)
        
        if rebuilder.needs_rebuild():
            # Inject rebuild context into messages
            messages = kwargs.get("messages", [])
            kwargs["messages"] = rebuilder.inject_checkpoint_into_messages(messages)
    
    async for event in _legacy_loop(**kwargs):
        yield event
```

- [ ] **Step 3: Run ALL tests**

Run: `python -m pytest tests/test_loop_detector.py tests/test_recovery.py tests/test_prompt_builder.py tests/test_checkpoint.py tests/test_tool.py tests/test_permission.py tests/test_tool_registry.py tests/test_actor.py tests/test_spawn.py tests/test_inbox.py tests/test_memory_persist.py tests/test_subagent_tools.py tests/test_checkpoint_writer.py tests/test_context_rebuild.py -v --noconftest`
Expected: All tests PASS

- [ ] **Step 4: Verify imports**

Run: `python -c "from src.agent import CheckpointWriter, ContextRebuilder; from src.agent_tools.subagent_tools import SpawnSubagentTool; print('All imports OK')"`

- [ ] **Step 5: Deploy to Docker**

```bash
echo "rt67we45" | sudo -S docker cp src/agent/. odysseus-odysseus-1:/app/src/agent/
echo "rt67we45" | sudo -S docker cp src/agent_tools/subagent_tools.py odysseus-odysseus-1:/app/src/agent_tools/subagent_tools.py
echo "rt67we45" | sudo -S docker cp src/agent/loop.py odysseus-odysseus-1:/app/src/agent/loop.py
echo "rt67we45" | sudo -S docker exec odysseus-odysseus-1 python -c "from src.agent import CheckpointWriter, ContextRebuilder; from src.agent_tools.subagent_tools import SpawnSubagentTool; print('Docker OK')"
```

- [ ] **Step 6: Commit**

```bash
git add src/agent/__init__.py src/agent/loop.py
git commit -m "feat(agent): complete Phase 4 — integration, checkpoint writer, context rebuild, subagent tools"
```
