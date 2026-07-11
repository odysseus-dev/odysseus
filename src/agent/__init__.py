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

__all__ = [
    "LoopDetector", "RecoveryLevel", "StableSignature",
    "RecoveryPrompts", "IntentSupervisor",
    "PromptBuilder", "PromptSection",
    "ContextManager", "CompactionResult",
    "Tool", "ToolResult", "ToolContext", "RecoverableError", "ToolInfo",
    "Action", "Rule", "Ruleset", "evaluate", "AGENT_PERMISSIONS",
    "ToolRegistry",
    "Actor", "ActorMode", "ActorStatus", "ActorOutcome", "ActorRegistry",
    "SpawnConfig", "ReturnFormat", "parse_return_header", "RETURN_FORMAT_INSTRUCTION",
    "Inbox", "InboxMessage",
    "MemoryStore", "CheckpointStore", "NotesStore", "TaskProgressStore",
]
