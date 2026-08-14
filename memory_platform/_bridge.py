"""_bridge.py — importable bridge for the hyphen-named CLI modules.

The platform's entry scripts use hyphenated filenames (drift-ledger.py,
sleep-time.py, lexicon-reconcile.py) which Python cannot `import` by name.
They are meant to be run as subprocesses. This bridge loads them by path so
other modules (and the Odysseus adapter) can also call their functions
programmatically.

Usage:
    from memory_platform import _bridge
    drift = _bridge.load("drift-ledger")   # -> module with snapshot()/check()
    result = drift.snapshot()
"""

import importlib.util
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))


def load(script_name):
    """Load a hyphen-named script by path as a module (cached)."""
    path = os.path.join(_HERE, f"{script_name}.py")
    mod_name = script_name.replace("-", "_")
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(mod_name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    try:
        spec.loader.exec_module(mod)
    except SystemExit:
        pass  # scripts call sys.exit() in __main__; harmless at import
    return mod
