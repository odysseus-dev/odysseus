"""Plugin manifest JSON Schema validation.

Fail-closed: any non-conforming manifest is rejected with a clear error
rather than half-loaded.
"""

import json
import os
from pathlib import Path
from typing import Any

# Load bundled schema at import time so failures are loud on startup.
_SCHEMA_PATH = Path(__file__).parent.parent / "odysseus-plugin.schema.json"
_SCHEMA: dict[str, Any] | None = None


def _load_schema() -> dict[str, Any]:
    global _SCHEMA
    if _SCHEMA is not None:
        return _SCHEMA
    with open(_SCHEMA_PATH, "r", encoding="utf-8") as f:
        _SCHEMA = json.load(f)
    return _SCHEMA


class PluginValidationError(Exception):
    """Raised when a plugin manifest fails schema validation."""

    pass


def validate_manifest(manifest: dict[str, Any]) -> None:
    """Validate a plugin manifest against the bundled JSON Schema.

    Raises:
        PluginValidationError: If the manifest is missing required fields,
            contains invalid values, or declares unknown capabilities.
    """
    schema = _load_schema()
    required = schema.get("required", [])
    missing = [f for f in required if f not in manifest]
    if missing:
        raise PluginValidationError(f"Missing required fields: {', '.join(missing)}")

    # Name: kebab-case
    name = manifest.get("name", "")
    if not name or not isinstance(name, str):
        raise PluginValidationError("'name' must be a non-empty string")
    # Simple kebab-case check
    if not all(c.isalnum() or c == "-" for c in name):
        raise PluginValidationError("'name' must be kebab-case (alphanumeric and hyphens only)")

    # Version: SemVer-ish
    version = manifest.get("version", "")
    if not version or not isinstance(version, str):
        raise PluginValidationError("'version' must be a non-empty string")
    parts = version.split("-")[0].split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        raise PluginValidationError("'version' must be a valid semantic version (e.g. '1.0.0')")

    # entry_point
    entry_point = manifest.get("entry_point", "")
    if not entry_point or not isinstance(entry_point, str):
        raise PluginValidationError("'entry_point' must be a non-empty string")
    if ":" not in entry_point and "." not in entry_point:
        raise PluginValidationError(
            "'entry_point' must be a dotted import path (e.g. 'my_plugin:register')"
        )

    # odysseus_compat
    compat = manifest.get("odysseus_compat", "")
    if not compat or not isinstance(compat, str):
        raise PluginValidationError("'odysseus_compat' must be a non-empty version range string")

    # description
    if not manifest.get("description") or not isinstance(manifest["description"], str):
        raise PluginValidationError("'description' must be a non-empty string")

    # author
    if not manifest.get("author") or not isinstance(manifest["author"], str):
        raise PluginValidationError("'author' must be a non-empty string")

    # capabilities
    caps = manifest.get("capabilities", [])
    if not isinstance(caps, list) or not caps:
        raise PluginValidationError("'capabilities' must be a non-empty list")
    allowed = set(schema["properties"]["capabilities"]["items"]["enum"])
    unknown = [c for c in caps if c not in allowed]
    if unknown:
        raise PluginValidationError(f"Unknown capabilities: {', '.join(unknown)}")

    # Optional fields type checks
    if "frontend" in manifest and not isinstance(manifest["frontend"], str):
        raise PluginValidationError("'frontend' must be a string")
    if "styles" in manifest:
        styles = manifest["styles"]
        if not isinstance(styles, list) or not all(isinstance(s, str) for s in styles):
            raise PluginValidationError("'styles' must be a list of strings")
    if "homepage" in manifest:
        if not isinstance(manifest["homepage"], str):
            raise PluginValidationError("'homepage' must be a string")
    if "repository" in manifest:
        if not isinstance(manifest["repository"], str):
            raise PluginValidationError("'repository' must be a string")
    if "license" in manifest:
        if not isinstance(manifest["license"], str):
            raise PluginValidationError("'license' must be a string")
