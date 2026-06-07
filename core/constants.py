# core/constants.py
"""Backward-compatible shim — the single source of truth is src/constants.py.

Historically there were two copies of this module (this one lagged behind at
APP_VERSION 0.9.1 and was missing the consolidated tool-output constants). To
kill the drift, this now simply re-exports everything from src.constants so
there is exactly one place that defines paths and reads ODYSSEUS_DATA_DIR.
internal_api_base() also lives in src.constants now and is re-exported here so
existing `from core.constants import internal_api_base` callers keep working.
"""
from src.constants import *  # noqa: F401,F403
from src.constants import internal_api_base  # noqa: F401  (explicit: functions aren't covered by some linters' * checks)
