"""Force stdlib ``calendar`` when the app package ``/app/calendar`` shadows it.

Agents: Odysseus ships ``calendar/`` under WORKDIR ``/app``. Importing httpx or
requests then binds the app package (no ``timegm``) and crashes. Call
``prefer_stdlib_calendar`` before those imports in ``app.py`` and the model-job
worker. Safe to call more than once.
"""

from __future__ import annotations

import importlib.util
import os
import sys


def prefer_stdlib_calendar() -> None:
    """Load stdlib ``calendar.py`` into ``sys.modules['calendar']``."""
    stdlib_file = os.path.join(os.path.dirname(os.__file__), "calendar.py")
    spec = importlib.util.spec_from_file_location("calendar", stdlib_file)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"stdlib calendar missing at {stdlib_file}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["calendar"] = module
    spec.loader.exec_module(module)
    if not hasattr(module, "timegm"):
        raise RuntimeError("stdlib calendar.timegm missing after prefer")
