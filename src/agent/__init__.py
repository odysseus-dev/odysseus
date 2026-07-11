"""Odysseus agent loop package — modular rewrite based on MiMo-Code patterns."""
from __future__ import annotations

from src.agent.loop_detector import LoopDetector, RecoveryLevel, StableSignature
from src.agent.recovery import RecoveryPrompts, IntentSupervisor
from src.agent.prompt_builder import PromptBuilder, PromptSection
from src.agent.checkpoint import ContextManager, CompactionResult

__all__ = [
    "LoopDetector", "RecoveryLevel", "StableSignature",
    "RecoveryPrompts", "IntentSupervisor",
    "PromptBuilder", "PromptSection",
    "ContextManager", "CompactionResult",
]
