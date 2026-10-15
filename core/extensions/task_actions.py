"""task_actions.py — register OXS extension action types with the scheduler.

The existing ``src.builtin_actions.BUILTIN_ACTIONS`` is a flat dict keyed
by action name. To register an extension action, we namespace the
action name as ``oxs_<extension_id>__<action_name>`` and append a wrapper
to ``BUILTIN_ACTIONS``. The wrapper dispatches to the extension's
declared handler and forwards the prompt/owner as kwargs.

Wrappers are removed on disable. Namespacing prevents collisions with
core actions and with other extensions.

The extension provides:
  - ``name``        e.g. ``w2a_classify``
  - ``schema``      a JSON Schema describing prompt fields (recorded for
    reference; not yet wired into TDD-style UI validation — PR 5 surface)
  - ``handler``     ``async def handler(owner, **kwargs) -> (str, bool)``

We also expose ``BUILTIN_ACTIONS_INFO`` style entries so the admin UI
can list extension actions. The registry populates this on enable and
clears on disable.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


def _action_key(extension_id: str, action_name: str) -> str:
    """Build the namespaced action key."""
    return f"oxs_{extension_id}__{action_name}"


async def _wrapper(handler: Callable[..., Awaitable[Any]], **kwargs) -> tuple[str, bool]:
    """Adapt an extension handler to the (text, success) contract."""
    try:
        result = await handler(**kwargs)
    except Exception:
        logger.exception("OXS action handler raised")
        return ("", False)
    if isinstance(result, tuple) and len(result) == 2:
        return result
    # Allow handlers that return just True/False or None
    if result is True or result is None:
        return ("", True)
    if result is False:
        return ("", False)
    # Anything else: stringify and treat as success.
    return (str(result), True)


def install_action(extension_id: str, action_name: str, handler: Callable, schema: dict) -> str:
    """Register ``handler`` under a namespaced key in ``BUILTIN_ACTIONS``.

    Returns the namespaced key. The wrapper is async; the scheduler
    awaits it.
    """
    from src.builtin_actions import BUILTIN_ACTIONS, BUILTIN_ACTION_INFO

    key = _action_key(extension_id, action_name)
    async def _adapter(**kw):
        # The scheduler calls BUILTIN_ACTIONS[name](owner=..., prompt=...)
        # We forward every kwarg except those we already handle.
        return await _wrapper(handler, **kw)
    BUILTIN_ACTIONS[key] = _adapter
    BUILTIN_ACTION_INFO[key] = {
        "name": action_name,
        "source": "extension",
        "extension_id": extension_id,
        "schema": schema,
    }
    return key


def uninstall_action(extension_id: str, action_name: str) -> None:
    """Remove a previously-installed action."""
    from src.builtin_actions import BUILTIN_ACTIONS, BUILTIN_ACTION_INFO
    key = _action_key(extension_id, action_name)
    BUILTIN_ACTIONS.pop(key, None)
    BUILTIN_ACTION_INFO.pop(key, None)


def uninstall_all_actions(extension_id: str) -> int:
    """Remove every action namespaced under ``extension_id``.

    Returns the number removed.
    """
    from src.builtin_actions import BUILTIN_ACTIONS, BUILTIN_ACTION_INFO
    prefix = f"oxs_{extension_id}__"
    removed = 0
    for key in list(BUILTIN_ACTIONS.keys()):
        if key.startswith(prefix):
            del BUILTIN_ACTIONS[key]
            del BUILTIN_ACTION_INFO[key]
            removed += 1
    return removed


def list_actions_for(extension_id: str) -> list[str]:
    """Return action names registered by ``extension_id`` (without the prefix)."""
    from src.builtin_actions import BUILTIN_ACTIONS
    prefix = f"oxs_{extension_id}__"
    return sorted(
        key[len(prefix):]
        for key in BUILTIN_ACTIONS.keys()
        if key.startswith(prefix)
    )


__all__ = [
    "install_action",
    "uninstall_action",
    "uninstall_all_actions",
    "list_actions_for",
]