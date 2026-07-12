"""Checkpoint writer — fork agent pattern for memory persistence.

Can be spawned as a subagent to persist state to checkpoint files.
"""
from __future__ import annotations

import asyncio
import logging
from typing import List, Optional
from src.agent.memory_persist import MemoryStore, CheckpointStore, NotesStore, TaskProgressStore

logger = logging.getLogger(__name__)


class CheckpointWriter:
    """Writes checkpoint files for memory persistence.
    
    Can be used directly or spawned as a fork agent.
    """
    def __init__(self, base_dir: str) -> None:
        self.base_dir = base_dir
        self.checkpoint_store = CheckpointStore(base_dir)
        self.memory_store = MemoryStore(base_dir)
        self.notes_store = NotesStore(base_dir)
        self.task_store = TaskProgressStore(base_dir)

    def write_checkpoint(self, active_intent: str = "", next_action: str = "", directives: str = "", task_tree: str = "", current_work: str = "", files_and_code: str = "", discovered_knowledge: str = "", errors_and_fixes: str = "", live_resources: str = "", design_decisions: str = "", open_notes: str = "") -> None:
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

    def write_memory(self, project_context: str = "", rules: Optional[List[str]] = None, architecture_decisions: str = "", discovered_knowledge: str = "") -> None:
        content = self.memory_store.read()
        if project_context:
            content = content.replace("_(What is this project?)_", project_context)
        if rules:
            rules_text = "\n".join(f"- {r}" for r in rules)
            content = content.replace("_Hard constraints from user._", rules_text)
        if architecture_decisions:
            content = content.replace("_Major design choices._", architecture_decisions)
        if discovered_knowledge:
            content = content.replace("_Cross-task facts._", discovered_knowledge)
        self.memory_store.write(content)

    def write_note(self, content: str) -> None:
        self.notes_store.append(content)

    def write_task_progress(self, task_id: str, content: str) -> None:
        self.task_store.write_progress(task_id, content)

    def rebuild_context(self) -> str:
        parts = []
        checkpoint = self.checkpoint_store.read()
        if checkpoint.strip():
            parts.append("## Session checkpoint\n" + checkpoint)
        memory = self.memory_store.read()
        if memory.strip():
            parts.append("## Project memory\n" + memory)
        notes = self.notes_store.read()
        if notes.strip():
            parts.append("## Session notes\n" + notes)
        tasks = self.task_store.list_tasks()
        for task_id in tasks:
            progress = self.task_store.read_progress(task_id)
            if progress.strip():
                parts.append(f"## Task {task_id} progress\n" + progress)
        return "\n\n---\n\n".join(parts)

    def render_for_prompt(self) -> str:
        active_intent = self.checkpoint_store.get_section("active_intent")
        if not active_intent:
            return ""
        return f"## Checkpoint context\nA previous checkpoint was saved. Key state:\n- Active intent: {active_intent[:200]}\n- Next action: {self.checkpoint_store.get_section('next_action')[:200]}\nUse `rebuild_context` tool to restore full context if needed."


async def run_checkpoint_writer(
    base_dir: str,
    context: str,
    session_id: str = "",
    owner: str = "",
) -> str:
    """Run checkpoint writer as a fork agent.
    
    This function can be called to spawn a checkpoint writer as a subagent.
    It reads the context, extracts key information, and writes to checkpoint files.
    
    Args:
        base_dir: Base directory for checkpoint files
        context: The conversation context to extract from
        session_id: Session ID for the checkpoint
        owner: Owner of the session
        
    Returns:
        Summary of what was written
    """
    writer = CheckpointWriter(base_dir)
    
    # Extract key information from context
    # In a real implementation, this would use an LLM to extract structured data
    # For now, we'll do simple extraction
    
    # Find the last user message as active intent
    active_intent = ""
    lines = context.split("\n")
    for line in reversed(lines):
        if line.strip() and not line.startswith("#"):
            active_intent = line.strip()[:500]
            break
    
    # Write checkpoint
    writer.write_checkpoint(
        active_intent=active_intent,
        next_action="Continue from last checkpoint",
        current_work=f"Checkpoint written for session {session_id}",
    )
    
    # Write a note
    writer.write_note(f"Checkpoint auto-written for session {session_id}")
    
    logger.info(f"Checkpoint writer completed for session {session_id}")
    return f"Checkpoint written for session {session_id}"


async def spawn_checkpoint_writer(
    base_dir: str,
    context: str,
    session_id: str = "",
    owner: str = "",
    background: bool = True,
) -> str:
    """Spawn a checkpoint writer as a background task.
    
    Args:
        base_dir: Base directory for checkpoint files
        context: The conversation context to extract from
        session_id: Session ID for the checkpoint
        owner: Owner of the session
        background: Whether to run in background
        
    Returns:
        Actor ID for tracking
    """
    from src.agent.actor import Actor, ActorMode, ActorRegistry, ActorStatus
    
    registry = ActorRegistry.get_instance()
    actor_id = registry.allocate_id("checkpoint-writer")
    
    actor = Actor(
        id=actor_id,
        session_id=session_id,
        mode=ActorMode.SUBAGENT,
        background=background,
    )
    registry.register(actor)
    registry.update_status(actor_id, ActorStatus.RUNNING)
    
    async def _run():
        try:
            result = await run_checkpoint_writer(base_dir, context, session_id, owner)
            actor.result = result
            registry.update_status(actor_id, ActorStatus.IDLE, outcome=__import__("src.agent.actor", fromlist=["ActorOutcome"]).ActorOutcome.SUCCESS)
        except Exception as e:
            actor.error = str(e)
            registry.update_status(actor_id, ActorStatus.IDLE, outcome=__import__("src.agent.actor", fromlist=["ActorOutcome"]).ActorOutcome.FAILURE, error=str(e))
    
    if background:
        asyncio.create_task(_run())
    else:
        await _run()
    
    return actor_id
