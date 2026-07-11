"""Odysseus agent loop package — modular rewrite based on MiMo-Code patterns."""
from __future__ import annotations

from src.agent.loop_detector import LoopDetector, RecoveryLevel, StableSignature
from src.agent.recovery import RecoveryPrompts, IntentSupervisor
from src.agent.prompt_builder import PromptBuilder, PromptSection
from src.agent.checkpoint import ContextManager, CompactionResult
from src.agent.tool import Tool, ToolResult, ToolContext, RecoverableError, ToolInfo
from src.agent.permission import Action, Rule, Ruleset, evaluate, AGENT_PERMISSIONS
from src.agent.tool_registry import ToolRegistry

__all__ = [
    "LoopDetector", "RecoveryLevel", "StableSignature",
    "RecoveryPrompts", "IntentSupervisor",
    "PromptBuilder", "PromptSection",
    "ContextManager", "CompactionResult",
    "Tool", "ToolResult", "ToolContext", "RecoverableError", "ToolInfo",
    "Action", "Rule", "Ruleset", "evaluate", "AGENT_PERMISSIONS",
    "ToolRegistry",
]
