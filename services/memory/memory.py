# services/memory/memory.py
"""Compatibility shim — the canonical MemoryManager lives in ``src/memory.py``.

This module used to hold a second, near-identical copy of ``MemoryManager`` that
had drifted from the ``src`` one (issue #49): the ``src`` copy gained a bullet
regex fix and ``uses`` usage-tracking (``increment_uses``), while this copy had a
``claim_ownerless`` migration helper the other lacked — so a fix could land in
one copy and silently miss the other.

``src/memory.py`` is now the single source of truth (with ``claim_ownerless``
ported into it), and this module simply re-exports it. Existing imports —
``from services.memory.memory import MemoryManager`` and ``from services.memory
import MemoryManager`` — keep working with no behavioural drift.
"""

from src.memory import MemoryManager

__all__ = ["MemoryManager"]
