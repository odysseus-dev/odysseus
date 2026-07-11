"""
swarm — Odysseus Swarm Intelligence Framework.

Hierarchical multi-agent architecture where specialised worker agents
collaborate under a master agent's coordination.

Public API::

    from src.swarm import SwarmManager, SwarmDefinition, BUILTIN_SWARMS

    manager = SwarmManager()
    async for event in manager.execute(swarm=..., user_query=..., **kwargs):
        ...
"""

from src.swarm.swarm_types import (
    SwarmRole,
    SwarmDefinition,
    SwarmTask,
    SwarmExecution,
    TaskStatus,
    ExecutionStatus,
)
from src.swarm.swarm_memory import SwarmMemory
from src.swarm.swarm_worker import run_worker
from src.swarm.swarm_manager import SwarmManager
from src.swarm.swarm_definitions import BUILTIN_SWARMS, get_builtin_swarm, list_builtin_swarms

__all__ = [
    "SwarmRole",
    "SwarmDefinition",
    "SwarmTask",
    "SwarmExecution",
    "TaskStatus",
    "ExecutionStatus",
    "SwarmMemory",
    "SwarmManager",
    "run_worker",
    "BUILTIN_SWARMS",
    "get_builtin_swarm",
    "list_builtin_swarms",
]
