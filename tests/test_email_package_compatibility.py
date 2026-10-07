from __future__ import annotations

import ast
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
CANONICAL = (
    ROOT / "routes" / "email" / "email_helpers.py",
    ROOT / "routes" / "email" / "email_pollers.py",
    ROOT / "routes" / "email" / "email_routes.py",
)

LEGACY_NAMES = {
    "routes.email_helpers",
    "routes.email_pollers",
    "routes.email_routes",
}


def _imported_module_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)

    return names


def test_canonical_email_package_does_not_import_legacy_shims() -> None:
    offenders: list[str] = []

    for path in CANONICAL:
        imports = _imported_module_names(path)
        legacy = sorted(imports & LEGACY_NAMES)
        if legacy:
            offenders.append(f"{path.relative_to(ROOT)}: {legacy}")

    assert not offenders, "\n".join(offenders)


def test_legacy_email_modules_alias_canonical_module_objects() -> None:
    code = r'''
import importlib
import sys

pairs = (
    ("routes.email_helpers", "routes.email.email_helpers"),
    ("routes.email_pollers", "routes.email.email_pollers"),
    ("routes.email_routes", "routes.email.email_routes"),
)

for legacy_name, canonical_name in pairs:
    legacy = importlib.import_module(legacy_name)
    canonical = importlib.import_module(canonical_name)

    assert legacy is canonical, (legacy_name, canonical_name)
    assert sys.modules[legacy_name] is canonical
    assert sys.modules[canonical_name] is canonical

    marker = object()
    legacy._compat_identity_probe = marker
    assert canonical._compat_identity_probe is marker
    del canonical._compat_identity_probe
'''

    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHON_DOTENV_DISABLED"] = "1"
    env["ODYSSEUS_INPROCESS_POLLERS"] = "0"

    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )

    assert result.returncode == 0, (
        f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
    )
