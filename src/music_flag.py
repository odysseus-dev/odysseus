"""Whether the optional music module is enabled.

The module is off unless a library root is configured, so a stock install never
imports the music service (mpv, the local library, the download helpers) nor
mounts its routes. ``ODYSSEUS_MUSIC_ENABLED`` overrides the default either way:

* ``1/true/yes/on`` enables it even without a library root (an empty library
  still lets the UI start and show "no tracks").
* ``0/false/no/off`` disables it even when a root is configured.
"""
from __future__ import annotations

import os

_TRUTHY = {"1", "true", "yes", "on"}
_FALSY = {"0", "false", "no", "off"}


def music_enabled() -> bool:
    raw = os.environ.get("ODYSSEUS_MUSIC_ENABLED", "").strip().lower()
    if raw in _FALSY:
        return False
    if raw in _TRUTHY:
        return True
    return bool(os.environ.get("ODYSSEUS_MUSIC_ROOT", "").strip())
