"""Shared loader for CLI scripts under scripts/."""
import importlib.machinery
import importlib.util
from pathlib import Path


_SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"


def load_script(script_name):
    """Load a script from scripts/ by name and return it as a module.

    The module name is derived from the script name (hyphens become underscores,
    with a _cli suffix) giving each script a stable, unique import identity.

    Any sys.modules stubs the script needs at import time must be injected via
    monkeypatch before calling this function.
    """
    module_name = script_name.replace("-", "_") + "_cli"
    path = _SCRIPTS_DIR / script_name
    loader = importlib.machinery.SourceFileLoader(module_name, str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module
