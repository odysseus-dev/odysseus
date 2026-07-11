"""
kahn_algorithm.py — Kahn's Algorithm for Topological Sort and Cycle Detection

Required Artifact: kahn-algorithm

This module provides pure-Python implementations of Kahn's algorithm for:
1. Topological ordering of directed acyclic graphs (DAGs)
2. Cycle detection in directed graphs
3. Execution layer grouping (tasks that can run in parallel)
4. Dependency validation (dangling references, self-loops)

The algorithm operates on an adjacency list representation:
    tasks: list[dict] with keys 'id' (int) and 'dependencies' (list[int])

Complexity: O(V + E) where V = node count, E = edge count.

Usage:
    from backend.kahn_algorithm import (
        topological_order, detect_cycle, execution_layers,
        validate_dependencies, CycleError
    )
"""

from typing import List, Optional, Dict, Set
from collections import deque, defaultdict


class CycleError(ValueError):
    """
    Raised when a cycle is detected in the dependency graph.

    Attributes:
        cycle_path: The sequence of node ids forming the detected cycle.
        message: Human-readable description.
    """
    def __init__(self, cycle_path: List[int], message: Optional[str] = None):
        self.cycle_path = cycle_path
        if message is None:
            message = (
                f"Cycle detected in task dependency graph: "
                f"{' → '.join(str(n) for n in cycle_path)}"
            )
        super().__init__(message)


def _build_adjacency(tasks: List[dict]) -> tuple:
    """
    Build adjacency list and in-degree map from task list.

    Args:
        tasks: List of dicts with 'id' (int) and 'dependencies' (list[int]).

    Returns:
        Tuple of (adj: dict[int, list[int]], in_degree: dict[int, int],
                  node_ids: set[int], dep_map: dict[int, list[int]]).
    """
    adj: Dict[int, List[int]] = defaultdict(list)
    in_degree: Dict[int, int] = defaultdict(int)
    node_ids: Set[int] = set()
    # dep_map stores original dependencies for each node (reverse of adj)
    dep_map: Dict[int, List[int]] = defaultdict(list)

    for task in tasks:
        node_id = task['id']
        deps = task.get('dependencies', [])
        node_ids.add(node_id)
        dep_map[node_id] = list(deps)

        for dep_id in deps:
            # Edge dep_id → node_id means dep_id must run before node_id
            adj[dep_id].append(node_id)
            in_degree[node_id] += 1
            node_ids.add(dep_id)

        # Ensure every node has an entry in in_degree
        if node_id not in in_degree:
            in_degree[node_id] = 0

    return adj, in_degree, node_ids, dep_map


def validate_dependencies(tasks: List[dict]) -> None:
    """
    Validate that all dependency references point to existing nodes.

    Checks for:
    - Self-loops (a node depending on itself)
    - Dangling references (dep on non-existent node id)

    Args:
        tasks: List of task dicts with 'id' and 'dependencies'.

    Raises:
        ValueError: If validation fails with a descriptive message.
    """
    node_ids = {t['id'] for t in tasks}

    for task in tasks:
        tid = task['id']
        for dep in task.get('dependencies', []):
            if dep == tid:
                raise ValueError(
                    f"Self-loop detected: Task {tid} depends on itself."
                )
            if dep not in node_ids:
                raise ValueError(
                    f"Dangling dependency: Task {tid} depends on "
                    f"non-existent task {dep}."
                )


def detect_cycle(tasks: List[dict]) -> Optional[List[int]]:
    """
    Detect a cycle in the dependency graph using Kahn's algorithm.

    Returns the first cycle found as a list of node ids, or None if the
    graph is acyclic.

    Args:
        tasks: List of task dicts with 'id' and 'dependencies'.

    Returns:
        List of node ids forming a cycle, or None if acyclic.
    """
    adj, in_degree, node_ids, dep_map = _build_adjacency(tasks)

    # Seed queue with zero-in-degree nodes
    queue = deque([n for n in node_ids if in_degree.get(n, 0) == 0])
    sorted_count = 0

    # Track removed edges for cycle reconstruction
    temp_in_degree = dict(in_degree)

    while queue:
        node = queue.popleft()
        sorted_count += 1
        for neighbor in adj.get(node, []):
            temp_in_degree[neighbor] -= 1
            if temp_in_degree[neighbor] == 0:
                queue.append(neighbor)

    if sorted_count < len(node_ids):
        # A cycle exists. Find it by following dependencies from any
        # remaining (non-zero in-degree) node.
        remaining = {n for n in node_ids if temp_in_degree.get(n, 0) > 0}

        if remaining:
            # Pick a start node and follow dependency edges to reconstruct cycle
            start = next(iter(remaining))
            visited: Dict[int, int] = {}  # node → step index
            path: List[int] = []
            current = start

            while current not in visited:
                visited[current] = len(path)
                path.append(current)
                # Find a predecessor (a node that depends on current)
                # by looking at dep_map
                predecessors = [d for d in remaining if current in adj.get(d, [])]
                if not predecessors:
                    # Try any edge from current
                    predecessors = adj.get(current, [])
                    if not predecessors:
                        break
                current = predecessors[0] if predecessors else next(iter(remaining))

            if current in visited:
                # Found a cycle: slice from first occurrence
                cycle_start = visited[current]
                cycle_path = path[cycle_start:] + [current]
                return cycle_path

        # Fallback: return remaining nodes
        return list(remaining)

    return None


def topological_order(tasks: List[dict]) -> List[int]:
    """
    Compute a topological ordering of tasks using Kahn's algorithm.

    Args:
        tasks: List of task dicts with 'id' and 'dependencies'.

    Returns:
        List of task ids in valid execution order (dependencies first).

    Raises:
        CycleError: If the dependency graph contains a cycle.
        ValueError: If dependencies reference non-existent tasks.
    """
    validate_dependencies(tasks)

    cycle_path = detect_cycle(tasks)
    if cycle_path is not None:
        raise CycleError(cycle_path)

    adj, in_degree, node_ids, _ = _build_adjacency(tasks)

    # Seed queue with zero-in-degree nodes, sorting for deterministic order
    queue = deque(sorted([n for n in node_ids if in_degree.get(n, 0) == 0]))
    result: List[int] = []
    temp_in_degree = dict(in_degree)

    while queue:
        node = queue.popleft()
        result.append(node)
        for neighbor in sorted(adj.get(node, [])):
            temp_in_degree[neighbor] -= 1
            if temp_in_degree[neighbor] == 0:
                queue.append(neighbor)

    return result


def execution_layers(tasks: List[dict]) -> List[List[int]]:
    """
    Group tasks into execution layers where all dependencies of layer N
    are satisfied by layers 0 through N-1.

    This is useful for parallel execution planning: tasks in the same layer
    can run concurrently.

    Args:
        tasks: List of task dicts with 'id' and 'dependencies'.

    Returns:
        List of layers, where each layer is a list of task ids.
        Layer 0 contains tasks with no dependencies.

    Raises:
        CycleError: If the dependency graph contains a cycle.
        ValueError: If dependencies reference non-existent tasks.
    """
    validate_dependencies(tasks)

    adj, in_degree, node_ids, _ = _build_adjacency(tasks)

    layers: List[List[int]] = []
    # Track which nodes have been placed
    placed: Set[int] = set()

    while len(placed) < len(node_ids):
        # Find all nodes whose dependencies are all placed
        current_layer = []
        for n in node_ids:
            if n in placed:
                continue
            deps = [t.get('dependencies', []) for t in tasks if t['id'] == n]
            deps = deps[0] if deps else []
            if all(d in placed for d in deps):
                current_layer.append(n)

        if not current_layer:
            # No progress — must be a cycle
            cycle = detect_cycle(tasks)
            if cycle:
                raise CycleError(cycle)
            # Shouldn't happen if validate already passed, but safety check
            remaining = node_ids - placed
            raise CycleError(list(remaining))

        layers.append(sorted(current_layer))
        placed.update(current_layer)

    return layers


def is_acyclic(tasks: List[dict]) -> bool:
    """
    Convenience function: returns True if the dependency graph has no cycles.

    Args:
        tasks: List of task dicts with 'id' and 'dependencies'.

    Returns:
        True if the graph is a valid DAG, False otherwise.
    """
    return detect_cycle(tasks) is None
