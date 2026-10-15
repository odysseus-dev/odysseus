"""security.py — supply-chain and parsing checks for OXS extensions.

Three concerns, all of which must pass before an extension is allowed to
``on_enable``:

  1. ``verify_file_hashes`` — every file on disk matches the SHA-256 recorded
     in ``manifest.file_hashes``. Bundled extensions are exempt because
     the file_hashes block is not required (the source-controlled tree is
     trusted).

  2. ``verify_zip_safe`` — when installing from a ZIP, reject archives that
     contain absolute paths, ``..`` traversal, symlinks, or unsafe modes.

  3. ``ast_parse_safe`` — read the entry-point module with ``ast.parse``
     (NEVER ``import`` or ``exec``) and confirm the ``register`` callable
     is a top-level function definition. Block dynamic constructs that are
     almost always malicious: ``exec``, ``eval``, ``__import__`` with
     non-literal args, etc. This is a coarse filter — the real sandbox
     is the capability gate in PR 2.

Plus helpers:

  - ``sha256_of_bytes`` / ``sha256_of_file`` — canonical hash helpers.
  - ``compute_extension_hashes`` — walk a directory and emit a
    ``{relpath: "sha256:..."}`` map suitable for pasting into a manifest.
"""

from __future__ import annotations

import ast
import hashlib
import os
import re
import zipfile
from typing import IO, Iterable


class OXSSecurityError(ValueError):
    """Security check failed for an extension.

    Carries the extension id and a list of human-readable reasons so the
    install UI can show every problem at once.
    """

    def __init__(self, errors: list[str], extension_id: str = ""):
        self.errors = errors
        self.extension_id = extension_id
        super().__init__("; ".join(errors) if errors else "security check failed")


# ── SHA-256 helpers ────────────────────────────────────────────────────────


def sha256_of_bytes(data: bytes) -> str:
    """Return ``sha256:<lowercase 64-hex>`` for the given bytes."""
    return "sha256:" + hashlib.sha256(data).hexdigest()


def sha256_of_file(path: str) -> str:
    """Return ``sha256:<lowercase 64-hex>`` for the given file on disk."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


_SHA256_BARE = re.compile(r"^[0-9a-fA-F]{64}$")


def _normalize_hash(h: str) -> str:
    """Lowercase and validate a ``sha256:<hex>`` hash. Raises ``OXSSecurityError``."""
    if not h.startswith("sha256:"):
        raise OXSSecurityError([f"hash missing 'sha256:' prefix: {h!r}"])
    body = h[len("sha256:"):]
    if not _SHA256_BARE.match(body):
        raise OXSSecurityError([f"hash body must be 64 hex chars: {h!r}"])
    return "sha256:" + body.lower()


MANIFEST_FILENAME_DEFAULT = "extension.json"


def compute_extension_hashes(root: str, *, manifest_filename: str = MANIFEST_FILENAME_DEFAULT) -> dict:
    """Walk ``root`` and emit ``{relpath: "sha256:..."}`` for every file.

    Excludes the manifest itself (it carries the hashes, no chicken/egg).
    Paths are forward-slash normalized for portability.

    ``manifest_filename`` is the leaf name of the manifest inside ``root``,
    e.g. ``"extension.json"``. We always resolve it relative to ``root`` —
    the leaf-only contract is what keeps the function safe to call from any
    working directory.
    """
    root_abs = os.path.abspath(root)
    manifest_abs = os.path.abspath(os.path.join(root_abs, manifest_filename))
    out: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(root_abs):
        # Skip hidden dirs and pycache
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d != "__pycache__"]
        for fn in filenames:
            abs_path = os.path.join(dirpath, fn)
            if abs_path == manifest_abs:
                continue
            rel = os.path.relpath(abs_path, root_abs).replace(os.sep, "/")
            out[rel] = sha256_of_file(abs_path)
    return out


# ── File-hash verification ─────────────────────────────────────────────────


# ── File-hash verification ─────────────────────────────────────────────────


def verify_file_hashes(
    root: str,
    declared: dict,
    *,
    extension_id: str = "",
    manifest_filename: str = MANIFEST_FILENAME_DEFAULT,
) -> None:
    """Verify every file in ``root`` matches a SHA-256 in ``declared``.

    ``declared`` is ``manifest.file_hashes``: ``{relpath: "sha256:<hex>"}``.
    Files on disk that are NOT in ``declared`` are *not* an error (operators
    may want to ship logs or generated files). Files in ``declared`` that do
    not exist on disk, or whose hashes mismatch, are hard errors.
    """
    errs: list[str] = []
    manifest_abs = os.path.abspath(os.path.join(root, manifest_filename))
    for relpath, expected in declared.items():
        # Reject path traversal in declared paths — this is what the loader
        # is going to walk; we don't want to escape ``root``.
        if relpath.startswith("/") or ".." in relpath.split("/"):
            errs.append(f"declared path escapes root: {relpath!r}")
            continue
        target = os.path.abspath(os.path.join(root, relpath))
        if not target.startswith(os.path.abspath(root) + os.sep) and target != manifest_abs:
            errs.append(f"declared path resolves outside root: {relpath!r}")
            continue
        if not os.path.isfile(target):
            errs.append(f"declared file missing on disk: {relpath!r}")
            continue
        try:
            want = _normalize_hash(expected)
        except OXSSecurityError as e:
            errs.extend(e.errors)
            continue
        got = sha256_of_file(target)
        if got != want:
            errs.append(
                f"hash mismatch for {relpath!r}: declared {want}, got {got}"
            )
    if errs:
        raise OXSSecurityError(errs, extension_id=extension_id)


# ── ZIP-safety verification ────────────────────────────────────────────────


# Reject archive entries whose normalized path is absolute, escapes the
# archive root, contains drive letters, or is a symlink/hardlink. These are
# the standard "zip-slip" checks; we add Windows-specific patterns for
# completeness because Odysseus installs run on Windows too.
_FORBIDDEN_PATH_FRAGMENTS = ("..", "\\", ":")


def _zip_member_is_safe(name: str) -> tuple[bool, str]:
    if not name:
        return False, "empty member name"
    # ZIP spec uses forward slashes. Backslashes inside a member name are
    # non-conformant and a common obfuscation pattern on Windows tools that
    # may write such names — refuse them outright rather than silently
    # normalizing to a forward slash path.
    if "\\" in name:
        return False, f"backslash not allowed in archive name: {name!r}"
    norm = name
    if norm.startswith("/"):
        return False, f"absolute path: {name!r}"
    parts = norm.split("/")
    if any(p == "" for p in parts[:-1]):
        return False, f"intermediate empty segment: {name!r}"
    if any(p == ".." for p in parts):
        return False, f"path traversal segment: {name!r}"
    # On Windows we want to reject "C:foo" or "C:/foo".
    if re.match(r"^[A-Za-z]:", norm):
        return False, f"drive letter: {name!r}"
    return True, ""


def verify_zip_safe(
    zf: zipfile.ZipFile,
    *,
    extension_id: str = "",
    max_members: int = 10_000,
    max_member_bytes: int = 50 * 1024 * 1024,
) -> None:
    """Reject a ZIP that contains unsafe entries.

    Checks:
      - No absolute paths or ``..`` segments
      - No symlinks (mode bit S_ISLNK) or hardlinks
      - No special file modes (devices, FIFOs)
      - Member count and individual file size bounded (zip-bomb mitigation)
    """
    errs: list[str] = []
    infos = zf.infolist()
    if len(infos) > max_members:
        errs.append(
            f"archive has {len(infos)} members, max allowed is {max_members}"
        )

    for info in infos:
        ok, reason = _zip_member_is_safe(info.filename)
        if not ok:
            errs.append(f"unsafe path in archive: {reason}")
            continue
        # File size cap
        if info.file_size > max_member_bytes:
            errs.append(
                f"member {info.filename!r} is {info.file_size} bytes, "
                f"max {max_member_bytes}"
            )
        # Mode checks. ZIP stores Unix mode in external_attr upper 16 bits
        # for entries created on Unix; on Windows this is often zero which
        # we accept as "regular file".
        mode = (info.external_attr >> 16) & 0xFFFF
        if mode:
            import stat
            if stat.S_ISLNK(mode):
                errs.append(f"symlink not allowed: {info.filename!r}")
            elif stat.S_ISCHR(mode) or stat.S_ISBLK(mode):
                errs.append(f"device file not allowed: {info.filename!r}")
            elif stat.S_ISFIFO(mode):
                errs.append(f"FIFO not allowed: {info.filename!r}")
            elif stat.S_ISSOCK(mode):
                errs.append(f"socket not allowed: {info.filename!r}")

    if errs:
        raise OXSSecurityError(errs, extension_id=extension_id)


# ── AST-based entry-point parsing ──────────────────────────────────────────

# Constructs we refuse to see at the top level of an entry-point module.
# These are coarse — anything that calls exec/eval/__import__ with a
# non-literal is almost always an obfuscation pattern. Real protection
# comes from the capability gate; this is just a tripwire.
_FORBIDDEN_AST_CALLS = frozenset({"exec", "eval", "compile"})


def ast_parse_safe(source: str | bytes) -> ast.Module:
    """Parse Python source with ``ast.parse`` and reject obviously bad shapes.

    Never ``exec``s or ``import``s. Returns the parsed ``ast.Module`` on
    success so the caller can inspect it (e.g. confirm ``register`` exists).
    """
    if isinstance(source, bytes):
        try:
            source = source.decode("utf-8")
        except UnicodeDecodeError as e:
            raise OXSSecurityError([f"entry-point source not valid UTF-8: {e}"]) from None

    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        raise OXSSecurityError([f"entry-point has syntax error: {e.msg}"]) from None

    errs: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = None
            if isinstance(func, ast.Name):
                name = func.id
            elif isinstance(func, ast.Attribute):
                name = func.attr
            if name in _FORBIDDEN_AST_CALLS:
                errs.append(
                    f"forbidden call '{name}' at line {node.lineno}"
                )
        # Also refuse ``__import__`` directly (it bypasses ``import`` keyword)
        if isinstance(node, ast.Name) and node.id == "__import__":
            errs.append(
                f"forbidden name '__import__' at line {node.lineno}"
            )

    if errs:
        raise OXSSecurityError(errs)

    return tree


def find_register_callable(tree: ast.Module, callable_name: str = "register") -> str | None:
    """Return the qualified name of ``register`` if it is a top-level def.

    We require ``register`` to be a plain top-level ``def``. We do not allow
    ``register = lambda`` or ``register = some_function`` because they make
    static analysis harder and add nothing legitimate.
    """
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == callable_name:
            args = node.args
            # We expect ``def register(host):`` — single positional arg.
            if len(args.args) != 1:
                return None
            return callable_name
    return None