"""
factory_dag — DAG utilities for the Odysseus factory system.

Thin wrapper around factory_kahn that provides the public API used by
factory_service and the route handlers.
"""

from typing import List, Dict, Optional
from .factory_kahn import (
    CycleError,
    validate_dependencies,
    detect_cycle,
    topological_order,
    execution_layers,
    is_acyclic,
)


__all__ = [
    "CycleError",
    "validate_dependencies",
    "detect_cycle",
    "topological_order",
    "execution_layers",
    "is_acyclic",
]
