"""manifest_schema.py — JSON Schema-style validator for OXS ``extension.json``.

This module validates the ``extension.json`` manifest that ships with every
OXS extension. It is deliberately self-contained (no ``jsonschema`` package
dependency) so PR 1 stays zero-new-dep. Validation is **fail-closed** — any
deviation from the schema is a hard install error.

The schema is encoded as ``MANIFEST_SCHEMA`` below. Treat it as the canonical
contract; ``validate_manifest()`` is the single entry point.
"""

from __future__ import annotations

import re
from typing import Any

MANIFEST_FILENAME = "extension.json"

# PEP 440 version range: comma-separated clauses; each is "spec" or two specs
# joined by comparison operators. We accept the common subset that operators
# actually use, which is enough for compatibility checks without pulling in
# ``packaging``.
_PEP440_RANGE = re.compile(
    r"^\s*(?:>=\s*\d+(?:\.\d+){0,3}[A-Za-z0-9._+\-]*"
    r"|<=\s*\d+(?:\.\d+){0,3}[A-Za-z0-9._+\-]*"
    r"|>\s*\d+(?:\.\d+){0,3}[A-Za-z0-9._+\-]*"
    r"|<\s*\d+(?:\.\d+){0,3}[A-Za-z0-9._+\-]*"
    r"|==\s*\d+(?:\.\d+){0,3}[A-Za-z0-9._+\-]*"
    r"|!=\s*\d+(?:\.\d+){0,3}[A-Za-z0-9._+\-]*"
    r"|~=\s*\d+(?:\.\d+){0,3}[A-Za-z0-9._+\-]*"
    r"|\d+(?:\.\d+){0,3}[A-Za-z0-9._+\-]*"
    r")(?:\s*,\s*(?:>=\s*|<=\s*|>\s*|<\s*|==\s*|!=\s*|~=\s*)?\s*"
    r"\d+(?:\.\d+){0,3}[A-Za-z0-9._+\-]*)*\s*$"
)

# Extension id: lowercase, 1-64 chars, starts with letter or digit, allows
# [a-z0-9_-]. This is intentionally strict — collision risk with file paths
# and DB rows is real, so we keep it boring.
_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

# Semver: MAJOR.MINOR.PATCH, optional pre-release, optional build.
_SEMVER_PATTERN = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$"
)

# ``module:callable``. Module is dotted path; callable is a plain identifier.
# We do not permit ``module.submodule: attr`` — too easy to write ambiguity.
_ENTRY_POINT_PATTERN = re.compile(r"^([A-Za-z_][\w.]*):([A-Za-z_][\w]*)$")

# File hashes are recorded as ``sha256:<hex 64>``. We accept uppercase hex
# too; canonicalization happens in security.sha256_of_*.
_SHA256_HASH_PATTERN = re.compile(r"^sha256:([0-9a-fA-F]{64})$")

# Capability names. Centralized so other modules can import them and stay in
# sync with what the manifest validator accepts.
SAFE_CAPABILITIES = frozenset({
    "data",
    "routes",
    "tools",
    "webhooks_in",
    "webhooks_out",
    "mcp_tools",
    "ui_sidebar",
    "ui_panel",
    "ui_slash",
    "ui_settings",
    "static",
    "events_sub",
    "events_fire",
    "task_actions",
    "filesystem_plugin",
    "settings_read",
})

MEDIUM_CAPABILITIES = frozenset({
    "network_outbound",
    "filesystem_data_subpath",
})

PRIVILEGED_CAPABILITIES = frozenset({
    "shell_command",
    "admin_routes",
    "manage_other_extensions",
    "network_outbound_private",
    "modify_core_settings",
})

ALL_CAPABILITIES = SAFE_CAPABILITIES | MEDIUM_CAPABILITIES | PRIVILEGED_CAPABILITIES

# Lifecycle hook names. ``on_startup`` / ``on_shutdown`` are reserved for PR 2.
VALID_HOOKS = frozenset({
    "on_install",
    "on_enable",
    "on_disable",
    "on_uninstall",
    "on_startup",
    "on_shutdown",
})


class OXSManifestError(ValueError):
    """Manifest validation failed.

    Carries a list of human-readable error messages so the install UI can
    show every problem at once instead of one-at-a-time.
    """

    def __init__(self, errors: list[str]):
        self.errors = errors
        if not errors:
            errors = ["unknown manifest validation failure"]
        super().__init__("; ".join(errors))


# ── Field validators ───────────────────────────────────────────────────────


def _is_str(x: Any) -> bool:
    return isinstance(x, str)


def _is_list_of_str(x: Any) -> bool:
    return isinstance(x, list) and all(isinstance(i, str) for i in x)


def _is_dict(x: Any) -> bool:
    return isinstance(x, dict)


def _require_str(data: dict, key: str, errs: list[str], *, min_len: int = 1, max_len: int = 256) -> None:
    val = data.get(key)
    if not _is_str(val) or len(val) < min_len or len(val) > max_len:
        errs.append(f"manifest.{key} must be a non-empty string ≤ {max_len} chars")


def _require_id(data: dict, key: str, errs: list[str]) -> None:
    val = data.get(key)
    if not _is_str(val) or not _ID_PATTERN.match(val):
        errs.append(
            f"manifest.{key} must match ^[a-z0-9][a-z0-9_-]{{0,63}}$ "
            f"(got {val!r})"
        )


def _require_semver(data: dict, key: str, errs: list[str]) -> None:
    val = data.get(key)
    if not _is_str(val) or not _SEMVER_PATTERN.match(val):
        errs.append(f"manifest.{key} must be a semver string (got {val!r})")


def _require_pep440_range(data: dict, key: str, errs: list[str]) -> None:
    val = data.get(key)
    if not _is_str(val) or not _PEP440_RANGE.match(val):
        errs.append(f"manifest.{key} must be a PEP 440 range (got {val!r})")


def _require_entry_point(data: dict, key: str, errs: list[str]) -> None:
    val = data.get(key)
    if not _is_str(val) or not _ENTRY_POINT_PATTERN.match(val):
        errs.append(
            f"manifest.{key} must look like 'module:callable' "
            f"(e.g. 'plugin:register', got {val!r})"
        )


def _require_url(data: dict, key: str, errs: list[str], *, schemes: list[str] | None = None) -> None:
    val = data.get(key)
    if not _is_str(val):
        return
    schemes = schemes or ["https:", "http:"]
    if not val:
        return
    if not any(val.startswith(f"{s}//") for s in schemes):
        errs.append(f"manifest.{key} must start with one of {schemes} (got {val!r})")
    return


def _require_author(data: dict, key: str, errs: list[str]) -> None:
    val = data.get(key)
    if not _is_dict(val):
        errs.append(f"manifest.{key} must be an object with name/url/email")
        return
    if not _is_str(val.get("name")) or not val["name"]:
        errs.append(f"manifest.{key}.name is required and must be a non-empty string")
    for opt in ("url", "email"):
        v = val.get(opt)
        if v is not None and not _is_str(v):
            errs.append(f"manifest.{key}.{opt} must be a string when present")


def _require_capabilities_block(caps: Any, errs: list[str]) -> None:
    if not _is_dict(caps):
        errs.append("manifest.capabilities must be an object")
        return

    # Every declared key must be a known capability; unknown ones are an
    # install error (forward-compat is a non-goal — we want authors to be
    # explicit when they upgrade the OXS core).
    declared = set(caps.keys())
    unknown = declared - ALL_CAPABILITIES
    for name in sorted(unknown):
        errs.append(f"manifest.capabilities.{name} is not a known capability")

    # Safe capabilities — must be either a boolean true or a structural dict.
    # We do not deep-validate shape here because each capability has its own
    # schema; PR 2 will tighten this. For PR 1, "present and non-empty" is
    # enough to start routing.
    for name in SAFE_CAPABILITIES:
        if name not in caps:
            continue
        val = caps[name]
        if not (val is True or _is_dict(val) or _is_list(val)):
            errs.append(
                f"manifest.capabilities.{name} must be true, an object, "
                f"or an array"
            )

    for name in MEDIUM_CAPABILITIES:
        if name not in caps:
            continue
        val = caps[name]
        if not _is_dict(val):
            errs.append(
                f"manifest.capabilities.{name} must be an object with scope "
                f"(medium capabilities require explicit allowlists in "
                f"manifest.permissions, not bare 'true')"
            )

    for name in PRIVILEGED_CAPABILITIES:
        # Privileged capabilities are declared in ``privileged_permissions``,
        # NOT in ``capabilities``. Surface a clear error if an author puts
        # them in the wrong place.
        if name in caps:
            errs.append(
                f"manifest.capabilities.{name} must be declared under "
                f"manifest.privileged_permissions, not manifest.capabilities"
            )


def _is_list(x: Any) -> bool:
    return isinstance(x, list)


def _require_permissions_block(perms: Any, errs: list[str]) -> None:
    """Validate the ``permissions`` allowlist block.

    Each permission key maps to either:
      - a list of strings (for ``network_outbound`` URL list,
        ``filesystem_data_subpath`` path list, ``events_sub``/``events_fire``
        event name list)
      - a string glob (for ``settings_read``)
    """
    if perms is None:
        return
    if not _is_dict(perms):
        errs.append("manifest.permissions must be an object when present")
        return

    valid_keys = {
        "network_outbound",
        "filesystem_data_subpath",
        "events_sub",
        "events_fire",
        "settings_read",
    }
    for key in perms.keys():
        if key not in valid_keys:
            errs.append(
                f"manifest.permissions.{key} is not a known permission key"
            )

    if "network_outbound" in perms:
        val = perms["network_outbound"]
        if not _is_list_of_str(val) or not val:
            errs.append(
                "manifest.permissions.network_outbound must be a non-empty "
                "list of URL prefix strings (e.g. ['https://api.example.com'])"
            )
        else:
            for url in val:
                if not (url.startswith("https://") or url.startswith("http://")):
                    errs.append(
                        f"manifest.permissions.network_outbound entry must "
                        f"start with http(s):// (got {url!r})"
                    )

    if "filesystem_data_subpath" in perms:
        val = perms["filesystem_data_subpath"]
        if not _is_list_of_str(val):
            errs.append(
                "manifest.permissions.filesystem_data_subpath must be a list "
                "of relative path strings"
            )
        else:
            for p in val:
                if p.startswith("/") or ".." in p.split("/"):
                    errs.append(
                        f"manifest.permissions.filesystem_data_subpath entry "
                        f"must be relative, no leading slash, no '..' "
                        f"(got {p!r})"
                    )

    if "events_sub" in perms and not _is_list_of_str(perms["events_sub"]):
        errs.append("manifest.permissions.events_sub must be a list of event names")
    if "events_fire" in perms and not _is_list_of_str(perms["events_fire"]):
        errs.append("manifest.permissions.events_fire must be a list of event names")
    if "settings_read" in perms and not _is_str(perms["settings_read"]):
        errs.append("manifest.permissions.settings_read must be a glob string")


def _require_privileged_block(priv: Any, errs: list[str]) -> None:
    """Validate ``privileged_permissions`` — all values must be booleans.

    No silent grants: every privileged capability either is true (operator
    explicitly opted in at install) or is false/absent (default deny).
    """
    if priv is None:
        return
    if not _is_dict(priv):
        errs.append("manifest.privileged_permissions must be an object when present")
        return

    valid_keys = PRIVILEGED_CAPABILITIES | {"__unknown__"}  # we filter below
    for key in priv.keys():
        if key not in PRIVILEGED_CAPABILITIES:
            errs.append(
                f"manifest.privileged_permissions.{key} is not a known "
                f"privileged capability"
            )
            continue
        if not isinstance(priv[key], bool):
            errs.append(
                f"manifest.privileged_permissions.{key} must be a boolean "
                f"(true to grant, false to deny)"
            )


def _require_file_hashes(hashes: Any, errs: list[str]) -> None:
    if hashes is None:
        return
    if not _is_dict(hashes):
        errs.append("manifest.file_hashes must be an object {relpath: 'sha256:...'}")
        return
    for relpath, h in hashes.items():
        if not _is_str(relpath) or "/" not in relpath and "\\" in relpath:
            errs.append(
                f"manifest.file_hashes keys must be forward-slash relative "
                f"paths (got {relpath!r})"
            )
        if not _is_str(h) or not _SHA256_HASH_PATTERN.match(h):
            errs.append(
                f"manifest.file_hashes[{relpath!r}] must be 'sha256:<64 hex chars>' "
                f"(got {h!r})"
            )


def _require_hooks(hooks: Any, errs: list[str]) -> None:
    if hooks is None:
        return
    if not _is_list_of_str(hooks):
        errs.append("manifest.hooks must be a list of hook names")
        return
    for h in hooks:
        if h not in VALID_HOOKS:
            errs.append(
                f"manifest.hooks entry {h!r} is not a known hook "
                f"(valid: {sorted(VALID_HOOKS)})"
            )


# ── Public entry point ─────────────────────────────────────────────────────


def validate_manifest(manifest: Any) -> dict:
    """Validate an ``extension.json`` payload. Returns the same dict on success.

    On failure raises :class:`OXSManifestError` with every problem in
    ``.errors`` so the operator sees the full set, not just the first.

    The input can be either:
      - a parsed ``dict`` (from ``json.load``)
      - a JSON string (raw file contents)
      - ``None`` or any non-dict (rejected)

    The manifest is returned untouched on success, so the caller can store it
    verbatim in the DB.
    """
    if isinstance(manifest, (bytes, bytearray)):
        try:
            manifest = manifest.decode("utf-8")
        except UnicodeDecodeError as e:
            raise OXSManifestError([f"manifest is not valid UTF-8: {e}"]) from None
    if isinstance(manifest, str):
        import json as _json
        try:
            manifest = _json.loads(manifest)
        except _json.JSONDecodeError as e:
            raise OXSManifestError([f"manifest is not valid JSON: {e.msg}"]) from None
    if not _is_dict(manifest):
        raise OXSManifestError(["manifest must be a JSON object"])

    errs: list[str] = []

    # ── Identity ─────────────────────────────────
    _require_id(manifest, "id", errs)
    _require_str(manifest, "name", errs)
    _require_semver(manifest, "version", errs)
    _require_pep440_range(manifest, "oxs_version", errs)

    # ── Metadata (optional) ──────────────────────
    if "python_deps" in manifest:
        deps = manifest["python_deps"]
        if not _is_list_of_str(deps):
            errs.append("manifest.python_deps must be a list of PEP 508 strings")
    _require_author(manifest, "author", errs) if "author" in manifest else None
    if "license" in manifest and not _is_str(manifest["license"]):
        errs.append("manifest.license must be a string")
    if "repository" in manifest:
        _require_url(manifest, "repository", errs)

    # ── Entry point (required) ────────────────────
    _require_entry_point(manifest, "entry_point", errs)

    # ── Hooks ─────────────────────────────────────
    _require_hooks(manifest.get("hooks"), errs)

    # ── Capabilities ─────────────────────────────
    _require_capabilities_block(manifest.get("capabilities", {}), errs)

    # ── Permissions ──────────────────────────────
    _require_permissions_block(manifest.get("permissions"), errs)

    # ── Privileged ───────────────────────────────
    _require_privileged_block(manifest.get("privileged_permissions"), errs)

    # ── File hashes ──────────────────────────────
    _require_file_hashes(manifest.get("file_hashes"), errs)

    # ── Schemas (light validation) ───────────────
    # We accept any dict and let the per-feature modules do deep validation
    # (settings_schema validates against jsonschema in PR 2; data_schema is
    # parsed by storage.py in PR 3). For PR 1 we only reject obviously wrong
    # types so authors get feedback early.
    for key in ("settings_schema", "data_schema"):
        if key in manifest and not _is_dict(manifest[key]):
            errs.append(f"manifest.{key} must be a JSON Schema (object)")

    if errs:
        raise OXSManifestError(errs)

    return manifest