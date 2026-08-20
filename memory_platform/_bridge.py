"""_bridge.py — Import bridge for hyphen-named Python scripts.

Python cannot `import` modules with hyphens in their names. This bridge
loads them by path using importlib.util, making them programmatically
accessible without renaming the CLI scripts.
"""

import importlib.util
import os
import sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def import_hyphen_module(name):
    """Import a hyphen-named module from the memory_platform directory.

    Usage:
        drift_ledger = import_hyphen_module("drift-ledger")
        drift_ledger.check(...)
    """
    path = os.path.join(_SCRIPT_DIR, f"{name}.py")
    if not os.path.exists(path):
        raise FileNotFoundError(f"Module not found: {path}")
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod.__name__] = mod
    spec.loader.exec_module(mod)
    return mod
