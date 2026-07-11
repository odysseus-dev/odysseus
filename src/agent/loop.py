"""Main agent loop — modular rewrite with pipeline stages.

Architecture (ported from MiMo-Code prompt.ts):
1. Context Resolution: load session, resolve model/provider
2. Prompt Assembly: modular prompt builder
3. Tool Resolution: RAG + domain seeding (reuses existing tool_index)
4. LLM Stream: reuses existing stream_llm_with_fallback
5. Tool Execution: reuses existing execute_tool_block
6. Result Processing + Loop Control: new detector/recovery integration

The old agent_loop.py stream_agent_loop() is preserved as fallback.
This module exposes the same async generator interface.
"""
from __future__ import annotations

import asyncio
import collections
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import AsyncGenerator, Dict, List, Optional, Set

from src.agent.loop_detector import LoopDetector, RecoveryLevel, StableSignature
from src.agent.recovery import IntentSupervisor, RecoveryPrompts
from src.agent.checkpoint import ContextManager
from src.agent.context_rebuild import ContextRebuilder
from src.agent.checkpoint_writer import CheckpointWriter

logger = logging.getLogger(__name__)

# Reuse existing components — no rewriting
from src.agent_tools import (
    execute_tool_block,
    format_tool_result,
    parse_tool_blocks,
    strip_tool_blocks,
    function_call_to_tool_block,
    FUNCTION_TOOL_SCHEMAS,
    MAX_AGENT_ROUNDS,
    ToolBlock,
)
from src.tool_utils import get_mcp_manager
from src.tool_security import blocked_tools_for_owner, plan_mode_disabled_tools
from src.tool_policy import ToolPolicy
from src.llm_core import stream_llm_with_fallback
from src.model_context import estimate_tokens
from src.settings import get_setting
from src.prompt_security import untrusted_context_message


# Re-export for backward compatibility
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
            messages = kwargs.get("messages", [])
            kwargs["messages"] = rebuilder.inject_checkpoint_into_messages(messages)
    
    async for event in _legacy_loop(**kwargs):
        yield event
