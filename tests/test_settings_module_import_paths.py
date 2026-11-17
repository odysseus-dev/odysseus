"""Relative imports in extracted settings modules must resolve on disk."""

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SETTINGS = ROOT / "static" / "js" / "settings"

_FROM_IMPORT = re.compile(r"""\bfrom\s+["']([^"']+)["']""")
_DYNAMIC_IMPORT = re.compile(r"""\bimport\s*\(\s*["']([^"']+)["']\s*\)""")
_SIDE_EFFECT_IMPORT = re.compile(
    r"""^\s*import\s+["']([^"']+)["']""",
    re.MULTILINE,
)


def _relative_specifiers(source: str) -> set[str]:
    specs = {
        *_FROM_IMPORT.findall(source),
        *_DYNAMIC_IMPORT.findall(source),
        *_SIDE_EFFECT_IMPORT.findall(source),
    }
    return {spec for spec in specs if spec.startswith(".")}


def test_settings_relative_imports_resolve() -> None:
    modules = sorted(SETTINGS.glob("*.js"))
    assert modules, "settings module directory unexpectedly empty"

    unresolved = []

    for module in modules:
        source = module.read_text(encoding="utf-8")

        for spec in sorted(_relative_specifiers(source)):
            clean = spec.split("?", 1)[0].split("#", 1)[0]
            target = (module.parent / clean).resolve()

            if not target.is_file():
                unresolved.append(
                    f"{module.relative_to(ROOT)}: {spec} -> "
                    f"{target.relative_to(ROOT)}"
                )

    assert not unresolved, (
        "relative imports from settings modules do not resolve:\n"
        + "\n".join(unresolved)
    )
