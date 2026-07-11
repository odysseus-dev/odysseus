"""
state_machine.py — Task and Project State Machine

Required Artifact: state-machine

Defines valid state transitions for both projects and individual tasks
in the Factory Service. Provides validation functions that enforce
the state machine rules described in the architecture contract.

## Task State Machine

    ┌──────────┐
    │  pending │ ◄──────┐
    └────┬─────┘        │
         │               │
         ▼               │
    ┌──────────┐        │
    │  running │         │
    └────┬─────┘        │
         │               │
    ┌────┴────┐         │
    │         │          │
    ▼         ▼          │
  ┌──────┐ ┌──────┐     │
  │compl.│ │failed│─────┘
  └──────┘ └──────┘

Transitions:
  - pending  → running   (task is dispatched)
  - running  → completed (task finishes successfully)
  - running  → failed    (task errors)
  - failed   → pending   (retry task)
  - completed → pending  (re-execute; invalidates downstream)

## Project State Machine

  planning → ready → running → completed
                              → failed
                              → cancelled
  completed → ready   (reset)
  failed    → ready   (reset)
  cancelled → ready   (reset)

Usage:
    from backend.state_machine import (
        validate_task_transition, validate_project_transition,
        TASK_TRANSITIONS, PROJECT_TRANSITIONS
    )
"""

from typing import Dict, Set, Tuple

# ---------------------------------------------------------------------------
# Task State Machine
# ---------------------------------------------------------------------------

# Valid transitions: current_state → set of allowed next states
TASK_TRANSITIONS: Dict[str, Set[str]] = {
    'pending':   {'running'},
    'running':   {'completed', 'failed'},
    'completed': {'pending'},    # re-execute invalidates downstream
    'failed':    {'pending'},    # retry
}

# All valid task states
TASK_STATES = {'pending', 'running', 'completed', 'failed'}


def validate_task_transition(
    current_status: str,
    new_status: str,
    task_id: int = 0,
    task_title: str = ""
) -> None:
    """
    Validate a task state transition.

    Args:
        current_status: The current state of the task.
        new_status: The desired next state.
        task_id: Task identifier (for error messages).
        task_title: Task title (for error messages).

    Raises:
        ValueError: If the transition is not valid according to the
                    state machine rules.
    """
    if current_status not in TASK_STATES:
        raise ValueError(
            f"Invalid current task status '{current_status}'. "
            f"Must be one of {sorted(TASK_STATES)}."
        )

    if new_status not in TASK_STATES:
        raise ValueError(
            f"Invalid target task status '{new_status}'. "
            f"Must be one of {sorted(TASK_STATES)}."
        )

    allowed = TASK_TRANSITIONS.get(current_status, set())
    if new_status not in allowed:
        label = f" (Task #{task_id} '{task_title}')" if task_id else ""
        raise ValueError(
            f"Invalid task state transition: "
            f"'{current_status}' → '{new_status}'{label}. "
            f"Allowed transitions from '{current_status}': "
            f"{sorted(allowed)}"
        )


# ---------------------------------------------------------------------------
# Project State Machine
# ---------------------------------------------------------------------------

PROJECT_TRANSITIONS: Dict[str, Set[str]] = {
    'planning':  {'ready', 'failed'},
    'ready':     {'running', 'failed'},
    'running':   {'completed', 'failed', 'cancelled'},
    'completed': {'ready'},
    'failed':    {'ready'},
    'cancelled': {'ready'},
}

PROJECT_STATES = {'planning', 'ready', 'running', 'completed', 'failed', 'cancelled'}


def validate_project_transition(
    current_status: str,
    new_status: str,
    project_id: int = 0
) -> None:
    """
    Validate a project state transition.

    Args:
        current_status: The current state of the project.
        new_status: The desired next state.
        project_id: Project identifier (for error messages).

    Raises:
        ValueError: If the transition is not valid according to the
                    state machine rules.
    """
    if current_status not in PROJECT_STATES:
        raise ValueError(
            f"Invalid current project status '{current_status}'. "
            f"Must be one of {sorted(PROJECT_STATES)}."
        )

    if new_status not in PROJECT_STATES:
        raise ValueError(
            f"Invalid target project status '{new_status}'. "
            f"Must be one of {sorted(PROJECT_STATES)}."
        )

    allowed = PROJECT_TRANSITIONS.get(current_status, set())
    if new_status not in allowed:
        label = f" (Project #{project_id})" if project_id else ""
        raise ValueError(
            f"Invalid project state transition: "
            f"'{current_status}' → '{new_status}'{label}. "
            f"Allowed transitions from '{current_status}': "
            f"{sorted(allowed)}"
        )
