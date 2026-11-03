"""Offline benchmark evidence auditor for Odysseus C5 / E00 baseline reconstruction.

Classifies historical evaluation artifacts using a strict evidence scheme.
Never labels an artifact VERIFIED merely because its hash was calculated.
VERIFIED requires an independently provided expected hash that matches.

Separates artifact file integrity from campaign prerequisite completeness.
A prerequisite is only complete when independently established expected sets
are verified. Archive summaries and aggregate statistics do not prove complete
task oracles or detailed latency records.

Usage:
    python3 scripts/audit_c5_benchmark_evidence.py MANIFEST ROOT [--output FILE] [--summary FILE] [--deterministic]

Exit codes:
    0 - all artifacts VERIFIED or OBSERVED_ONLY
    1 - any MISSING, MISMATCH, or UNVERIFIABLE artifacts
    2 - INVALID manifest, preflight destination conflict, publication error, or fatal error
"""
from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
import posixpath
import re
import stat
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

# ---------------------------------------------------------------------------
# Classification constants
# ---------------------------------------------------------------------------

VERIFIED = "VERIFIED"
OBSERVED_ONLY = "OBSERVED_ONLY"
MISSING = "MISSING"
MISMATCH = "MISMATCH"
INVALID = "INVALID"
UNVERIFIABLE = "UNVERIFIABLE"
PARTIAL = "PARTIAL"
INCOMPLETE = "INCOMPLETE"
UNKNOWN = "UNKNOWN"

ALL_STATUSES = (VERIFIED, OBSERVED_ONLY, MISSING, MISMATCH, INVALID, UNVERIFIABLE)

# Map artifact types to reproducibility prerequisite keys.
_TYPE_TO_PREREQ: dict[str, str] = {
    "model_artifact": "model_artifact_hash",
    "source_revision": "source_checkpoint_revision",
    "quantization_config": "quantization_info",
    "backend_binary": "backend_executable_identity",
    "chat_template": "chat_template_identity",
    "run_config": "context_and_decoding_config",
    "task_fixture": "task_fixture_identities",
    "oracle_definition": "expected_outcomes_and_oracles",
    "oracle_summary": "expected_outcomes_and_oracles",
    "trajectory": "raw_trajectories",
    "result_record": "result_and_latency_records",
    "result_summary": "result_and_latency_records",
    "aggregate_report": "result_and_latency_records",
}

_PREREQ_KEYS = (
    "backend_executable_identity",
    "chat_template_identity",
    "context_and_decoding_config",
    "expected_outcomes_and_oracles",
    "model_artifact_hash",
    "quantization_info",
    "raw_trajectories",
    "result_and_latency_records",
    "source_checkpoint_revision",
    "task_fixture_identities",
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _valid_sha256(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, str):
        return False
    return bool(_SHA256_RE.match(value))


# ---------------------------------------------------------------------------
# Path safety
# ---------------------------------------------------------------------------

def _is_safe_relative_path(raw: Any) -> bool:
    """Return True only if raw is a non-empty, non-absolute relative path with no traversal."""
    if isinstance(raw, bool) or not isinstance(raw, str):
        return False
    if "\0" in raw:
        return False
    try:
        raw.encode("utf-8")
        os.fsencode(raw)
    except (UnicodeError, ValueError):
        return False
    clean = raw.strip()
    if not clean or os.path.isabs(clean):
        return False
    try:
        p = PurePosixPath(clean)
    except Exception:
        return False
    # Reject dot-only paths and zero-component normalizations ('.', './', '././')
    if not p.parts or not p.name or p.name in (".", ".."):
        return False
    if ".." in p.parts or "." in p.parts:
        return False
    if clean.endswith(("/.", "/..")) or clean in (".", ".."):
        return False
    # Reject backslash paths that could escape on Windows hosts.
    if "\\" in clean:
        return False
    return True


def _normalize_relative_path(raw: Any) -> str | None:
    """Return normalized POSIX relative path string if safe, else None.

    Normalizes redundant dot components and leading './' using posixpath.normpath
    without following symlinks or escaping the root boundary.
    """
    if not _is_safe_relative_path(raw):
        return None
    try:
        norm = posixpath.normpath(str(raw).strip())
    except (UnicodeError, ValueError):
        return None
    if not _is_safe_relative_path(norm):
        return None
    return norm


# ---------------------------------------------------------------------------
# Secure File Descriptor Traversal and Hashing (O_NOFOLLOW / Descriptor-Relative)
# ---------------------------------------------------------------------------

def _open_dir_securely(path: Path) -> int:
    """Securely open directory path descriptor-by-descriptor from root anchor with O_NOFOLLOW.

    Anchors traversal at the filesystem root descriptor.
    Rejects any path component (including the root itself) that is a symbolic link.
    Caller owns the returned file descriptor and must close it.
    """
    try:
        abs_p = path.absolute()
        anchor = abs_p.anchor
        parts = abs_p.relative_to(anchor).parts
    except (ValueError, UnicodeError) as exc:
        raise OSError(f"malformed directory path: {exc}") from exc

    try:
        fd = os.open(anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    except (ValueError, UnicodeError) as exc:
        raise OSError(f"malformed root anchor: {exc}") from exc
    open_fds: list[int] = [fd]
    try:
        curr = fd
        for part in parts:
            try:
                st = os.lstat(part, dir_fd=curr)
            except (ValueError, UnicodeError) as exc:
                raise OSError(f"malformed path component {part!r}: {exc}") from exc
            if stat.S_ISLNK(st.st_mode):
                raise OSError(f"symlink path component rejected by O_NOFOLLOW: {part!r}")
            if not stat.S_ISDIR(st.st_mode):
                raise OSError(f"path component is not a directory: {part!r}")
            try:
                next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW, dir_fd=curr)
            except (ValueError, UnicodeError) as exc:
                raise OSError(f"malformed path component {part!r}: {exc}") from exc
            open_fds.append(next_fd)
            curr = next_fd

        open_fds.remove(curr)
        return curr
    finally:
        for f in open_fds:
            try:
                os.close(f)
            except OSError:
                pass


def _open_or_create_dir_securely(path: Path) -> int:
    """Traverse and create path components descriptor-by-descriptor with O_NOFOLLOW.

    Anchors traversal at the filesystem root descriptor.
    Rejects any intermediate or final directory component that is a symbolic link.
    Never reopens directory by full pathname.
    Caller owns the returned file descriptor and must close it.
    """
    try:
        abs_p = path.absolute()
        anchor = abs_p.anchor
        parts = abs_p.relative_to(anchor).parts
    except (ValueError, UnicodeError) as exc:
        raise OSError(f"malformed directory path: {exc}") from exc

    try:
        fd = os.open(anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    except (ValueError, UnicodeError) as exc:
        raise OSError(f"malformed root anchor: {exc}") from exc
    open_fds: list[int] = [fd]
    try:
        curr = fd
        for part in parts:
            try:
                try:
                    st = os.lstat(part, dir_fd=curr)
                except FileNotFoundError:
                    try:
                        os.mkdir(part, 0o755, dir_fd=curr)
                    except FileExistsError:
                        pass
                    st = os.lstat(part, dir_fd=curr)
            except (ValueError, UnicodeError) as exc:
                raise OSError(f"malformed path component {part!r}: {exc}") from exc

            if stat.S_ISLNK(st.st_mode):
                raise OSError(f"symlink directory component rejected by O_NOFOLLOW: {part!r}")
            if not stat.S_ISDIR(st.st_mode):
                raise OSError(f"path component is not a directory: {part!r}")

            try:
                next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW, dir_fd=curr)
            except (ValueError, UnicodeError) as exc:
                raise OSError(f"malformed path component {part!r}: {exc}") from exc
            open_fds.append(next_fd)
            curr = next_fd

        open_fds.remove(curr)
        return curr
    finally:
        for f in open_fds:
            try:
                os.close(f)
            except OSError:
                pass


class ArtifactOpenResult(tuple):
    """4-tuple (status_code, observed_sha256, size_bytes, note) carrying file_id."""
    file_id: str | None = None


def _open_and_hash_artifact(root: Path, raw_path: str) -> tuple[str, str | None, int | None, str | None]:
    """Securely open artifact within root using descriptor-relative traversal and O_NOFOLLOW.

    Enforces root boundary at point of access and protects against symlink TOCTOU replacement.
    All symlinks within root and root itself are rejected as UNVERIFIABLE.

    Returns:
        (status_code, observed_sha256, size_bytes, note)
        status_code is one of: 'PRESENT', 'MISSING', 'UNVERIFIABLE', 'INVALID'
    """
    if not _is_safe_relative_path(raw_path):
        return INVALID, None, None, f"path has no valid filename component: {raw_path!r}"

    try:
        root_fd = _open_dir_securely(root)
    except FileNotFoundError:
        return MISSING, None, None, f"root directory not found: {root}"
    except (OSError, ValueError, UnicodeError) as exc:
        return UNVERIFIABLE, None, None, f"root directory is invalid or a symlink: {exc}"

    open_fds: list[int] = [root_fd]
    try:
        try:
            parts = list(PurePosixPath(raw_path.strip()).parts)
        except (ValueError, UnicodeError):
            return INVALID, None, None, f"path has no valid filename component: {raw_path!r}"
        if not parts or not parts[-1] or parts[-1] in (".", ".."):
            return INVALID, None, None, f"path has no valid filename component: {raw_path!r}"
        curr_fd = root_fd

        # Traverse directory components securely
        for part in parts[:-1]:
            try:
                st = os.lstat(part, dir_fd=curr_fd)
                if stat.S_ISLNK(st.st_mode):
                    return UNVERIFIABLE, None, None, f"symlink directory component rejected by O_NOFOLLOW: {part!r}"
                if not stat.S_ISDIR(st.st_mode):
                    return UNVERIFIABLE, None, None, f"path component is not a directory: {part!r}"
                next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW, dir_fd=curr_fd)
                open_fds.append(next_fd)
                curr_fd = next_fd
            except FileNotFoundError:
                return MISSING, None, None, f"directory component not found: {part!r}"
            except (ValueError, UnicodeError):
                return INVALID, None, None, f"malformed directory component rejected: {part!r}"
            except OSError as exc:
                if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                    return UNVERIFIABLE, None, None, f"symlink directory component rejected: {part!r}"
                return UNVERIFIABLE, None, None, f"filesystem error accessing component {part!r}: {exc}"

        # Access final artifact file securely
        filename = parts[-1]
        try:
            st = os.lstat(filename, dir_fd=curr_fd)
            if stat.S_ISLNK(st.st_mode):
                return UNVERIFIABLE, None, None, f"symlink artifact rejected by O_NOFOLLOW: {filename!r}"
            if not stat.S_ISREG(st.st_mode):
                return UNVERIFIABLE, None, None, f"artifact is not a regular file: {filename!r}"
            file_fd = os.open(filename, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW, dir_fd=curr_fd)
            open_fds.append(file_fd)
        except FileNotFoundError:
            return MISSING, None, None, f"artifact not found: {filename!r}"
        except (ValueError, UnicodeError):
            return INVALID, None, None, f"malformed artifact filename rejected: {filename!r}"
        except OSError as exc:
            if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                return UNVERIFIABLE, None, None, f"symlink artifact rejected: {filename!r}"
            return UNVERIFIABLE, None, None, f"filesystem error opening artifact {filename!r}: {exc}"

        # Stat opened file descriptor directly
        try:
            fst = os.fstat(file_fd)
            if not stat.S_ISREG(fst.st_mode):
                return UNVERIFIABLE, None, None, f"opened descriptor is not a regular file: {filename!r}"
            size_bytes = fst.st_size
            file_id = f"{fst.st_dev}:{fst.st_ino}"
            h = hashlib.sha256()
            read_bytes = 0
            while True:
                chunk = os.read(file_fd, 65536)
                if not chunk:
                    break
                read_bytes += len(chunk)
                h.update(chunk)
            res = ArtifactOpenResult(("PRESENT", h.hexdigest(), read_bytes, None))
            res.file_id = file_id
            return res
        except OSError as exc:
            return UNVERIFIABLE, None, None, f"filesystem error reading artifact: {exc}"
    finally:
        for fd in reversed(open_fds):
            try:
                os.close(fd)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Manifest and Expected-Sets Configuration Validation
# ---------------------------------------------------------------------------

def _validate_expected_sets_structure(esets: Any, label: str = "expected_sets") -> list[str]:
    """Validate expected_sets mapping structure and types. Return list of error strings."""
    errors: list[str] = []
    if esets is None:
        errors.append(f"{label} cannot be null")
        return errors
    if not isinstance(esets, dict):
        errors.append(f"{label} must be a JSON object, got {type(esets).__name__}")
        return errors
    if not esets and label == "trusted_expected_sets":
        errors.append(f"{label} cannot be an empty object")
        return errors

    for pkey, pdef in esets.items():
        if not isinstance(pkey, str) or pkey not in _PREREQ_KEYS:
            errors.append(f"unrecognized or unsupported prerequisite category in {label}: {pkey!r}")
            continue
        if not isinstance(pdef, dict):
            errors.append(f"entry for category {pkey!r} in {label} must be a JSON object, got {type(pdef).__name__}")
            continue

        unsupported_fields = set(pdef.keys()) - {
            "expected_paths",
            "expected_count",
            "provenance",
            "independent_oracle",
            "complete",
            "expected_set_complete",
        }
        if unsupported_fields:
            errors.append(f"unsupported field(s) in category {pkey!r}: {sorted(unsupported_fields)}")

        has_paths = "expected_paths" in pdef
        has_count = "expected_count" in pdef
        if not has_paths and not has_count:
            errors.append(f"entry for category {pkey!r} in {label} must specify 'expected_paths' or 'expected_count'")

        if has_paths:
            ep = pdef["expected_paths"]
            if not isinstance(ep, list):
                errors.append(f"expected_paths for category {pkey!r} in {label} must be a list of strings")
            elif len(ep) == 0:
                errors.append(f"expected_paths for category {pkey!r} in {label} cannot be empty")
            else:
                seen = set()
                for item in ep:
                    if isinstance(item, bool) or not isinstance(item, str):
                        errors.append(f"invalid item in expected_paths for {pkey!r}: {item!r} (must be string)")
                    elif not _is_safe_relative_path(item):
                        errors.append(f"unsafe or invalid path in expected_paths for {pkey!r}: {item!r}")
                    else:
                        norm = _normalize_relative_path(item)
                        if norm in seen:
                            errors.append(f"duplicate path in expected_paths for {pkey!r}: {item!r}")
                        else:
                            seen.add(norm)

        if has_count:
            ec = pdef["expected_count"]
            if isinstance(ec, bool) or not isinstance(ec, int) or ec <= 0:
                errors.append(f"expected_count for category {pkey!r} in {label} must be a positive integer")

        if has_paths and has_count and isinstance(pdef.get("expected_paths"), list) and isinstance(pdef.get("expected_count"), int) and not isinstance(pdef.get("expected_count"), bool):
            if len(pdef["expected_paths"]) != pdef["expected_count"]:
                errors.append(f"conflicting definitions in category {pkey!r}: len(expected_paths)={len(pdef['expected_paths'])} != expected_count={pdef['expected_count']}")

        if "provenance" in pdef:
            prov = pdef["provenance"]
            if prov is not None and (isinstance(prov, bool) or not isinstance(prov, str) or not prov.strip()):
                errors.append(f"invalid provenance for category {pkey!r} in {label}: {prov!r} (must be non-empty string)")

        if "independent_oracle" in pdef:
            io = pdef["independent_oracle"]
            if not isinstance(io, bool):
                errors.append(f"independent_oracle for category {pkey!r} in {label} must be a boolean")

        if "complete" in pdef and not isinstance(pdef["complete"], bool):
            errors.append(f"complete flag for category {pkey!r} in {label} must be a boolean")

        if "expected_set_complete" in pdef and not isinstance(pdef["expected_set_complete"], bool):
            errors.append(f"expected_set_complete flag for category {pkey!r} in {label} must be a boolean")

    return errors


def _validate_manifest(data: Any) -> list[str]:
    """Return list of error strings; empty means valid."""
    errors: list[str] = []
    if not isinstance(data, dict):
        errors.append("manifest must be a JSON object")
        return errors
    sv = data.get("schema_version")
    if type(sv) is not int or sv != 1:
        errors.append(f"unsupported schema_version: {sv!r} (expected 1)")
    cid = data.get("campaign_id")
    if isinstance(cid, bool) or not isinstance(cid, str) or not cid.strip():
        errors.append("campaign_id must be a non-empty string")
    arts = data.get("artifacts")
    if not isinstance(arts, list):
        errors.append("artifacts must be a JSON array")
    if "expected_sets" in data:
        errors.extend(_validate_expected_sets_structure(data["expected_sets"], label="expected_sets"))
    return errors


# ---------------------------------------------------------------------------
# Single artifact audit
# ---------------------------------------------------------------------------

def _audit_artifact(entry: Any, root: Path) -> dict[str, Any]:
    """Audit one artifact entry against the filesystem root."""
    if not isinstance(entry, dict):
        return {
            "path": str(entry),
            "type": None,
            "status": INVALID,
            "present": False,
            "observed_sha256": None,
            "expected_sha256": None,
            "hash_match": False,
            "size_bytes": None,
            "provenance": None,
            "notes": f"artifact entry must be a JSON object, got {type(entry).__name__}",
        }

    if "path" not in entry:
        return {
            "path": "",
            "type": entry.get("type") if isinstance(entry.get("type"), str) else None,
            "status": INVALID,
            "present": False,
            "observed_sha256": None,
            "expected_sha256": None,
            "hash_match": False,
            "size_bytes": None,
            "provenance": entry.get("provenance") if isinstance(entry.get("provenance"), str) else None,
            "notes": "missing required 'path' field in artifact entry",
        }

    raw_path = entry["path"]
    artifact_type = entry.get("type") if isinstance(entry.get("type"), str) else None
    provenance = entry.get("provenance") if isinstance(entry.get("provenance"), str) else None
    notes_in = entry.get("notes") if isinstance(entry.get("notes"), str) else None
    mandatory = entry.get("mandatory", True) if not (entry.get("mandatory") is False or entry.get("optional") is True) else False
    is_summary = bool(entry.get("is_summary") or entry.get("scope") in ("summary", "aggregate"))

    # Validate path type before any operations
    if isinstance(raw_path, bool) or not isinstance(raw_path, str):
        return {
            "path": str(raw_path),
            "type": artifact_type,
            "status": INVALID,
            "present": False,
            "observed_sha256": None,
            "expected_sha256": None,
            "hash_match": False,
            "size_bytes": None,
            "provenance": provenance,
            "mandatory": mandatory,
            "is_summary": is_summary,
            "notes": f"artifact path must be a string, got {type(raw_path).__name__}",
        }

    # Validate path safety
    if not _is_safe_relative_path(raw_path):
        return {
            "path": raw_path,
            "type": artifact_type,
            "status": INVALID,
            "present": False,
            "observed_sha256": None,
            "expected_sha256": None,
            "hash_match": False,
            "size_bytes": None,
            "provenance": provenance,
            "mandatory": mandatory,
            "is_summary": is_summary,
            "notes": f"unsafe path rejected (absolute, traversal, or empty): {raw_path!r}",
        }

    # Validate expected hash format
    expected_hash: str | None = None
    if "expected_sha256" in entry and entry["expected_sha256"] is not None:
        raw_hash = entry["expected_sha256"]
        if isinstance(raw_hash, bool) or not isinstance(raw_hash, str):
            return {
                "path": raw_path,
                "type": artifact_type,
                "status": INVALID,
                "present": False,
                "observed_sha256": None,
                "expected_sha256": str(raw_hash),
                "hash_match": False,
                "size_bytes": None,
                "provenance": provenance,
                "mandatory": mandatory,
                "is_summary": is_summary,
                "notes": f"expected_sha256 must be a string, got {type(raw_hash).__name__}",
            }
        if not _valid_sha256(raw_hash):
            return {
                "path": raw_path,
                "type": artifact_type,
                "status": INVALID,
                "present": False,
                "observed_sha256": None,
                "expected_sha256": raw_hash,
                "hash_match": False,
                "size_bytes": None,
                "provenance": provenance,
                "mandatory": mandatory,
                "is_summary": is_summary,
                "notes": f"expected_sha256 is not a valid 64-hex-char SHA-256: {raw_hash!r}",
            }
        expected_hash = raw_hash

    # Securely open and hash the artifact
    open_res = _open_and_hash_artifact(root, raw_path)
    open_status, observed_hash, size_bytes, err_note = open_res
    file_id = getattr(open_res, "file_id", None)
    norm_path = _normalize_relative_path(raw_path)

    if open_status == "MISSING":
        return {
            "path": raw_path,
            "normalized_path": norm_path,
            "file_id": None,
            "type": artifact_type,
            "status": MISSING,
            "present": False,
            "observed_sha256": None,
            "expected_sha256": expected_hash,
            "hash_match": False,
            "size_bytes": None,
            "provenance": provenance,
            "mandatory": mandatory,
            "is_summary": is_summary,
            "notes": err_note or notes_in,
        }

    if open_status == "UNVERIFIABLE":
        return {
            "path": raw_path,
            "normalized_path": norm_path,
            "file_id": None,
            "type": artifact_type,
            "status": UNVERIFIABLE,
            "present": None,
            "observed_sha256": None,
            "expected_sha256": expected_hash,
            "hash_match": False,
            "size_bytes": None,
            "provenance": provenance,
            "mandatory": mandatory,
            "is_summary": is_summary,
            "notes": err_note or notes_in,
        }

    if open_status == "INVALID":
        return {
            "path": raw_path,
            "normalized_path": norm_path,
            "file_id": None,
            "type": artifact_type,
            "status": INVALID,
            "present": True,
            "observed_sha256": None,
            "expected_sha256": expected_hash,
            "hash_match": False,
            "size_bytes": None,
            "provenance": provenance,
            "mandatory": mandatory,
            "is_summary": is_summary,
            "notes": err_note or notes_in,
        }

    # Artifact is present regular file
    if expected_hash is None:
        status = OBSERVED_ONLY
        hash_match = False
        note = "no independent expected hash provided; observed hash recorded but not verified"
    elif observed_hash == expected_hash:
        status = VERIFIED
        hash_match = True
        note = notes_in
    else:
        status = MISMATCH
        hash_match = False
        note = "observed SHA-256 does not match independently provided expected hash"

    res = {
        "path": raw_path,
        "normalized_path": norm_path,
        "file_id": file_id,
        "type": artifact_type,
        "status": status,
        "present": True,
        "observed_sha256": observed_hash,
        "expected_sha256": expected_hash,
        "hash_match": hash_match,
        "size_bytes": size_bytes,
        "provenance": provenance,
        "mandatory": mandatory,
        "is_summary": is_summary,
        "notes": note,
    }
    # Preserve explicit expected_set properties when present
    for k in ("expected_set_id", "expected_set_provenance", "expected_set_complete", "independent_oracle"):
        if k in entry:
            res[k] = entry[k]
    return res


# ---------------------------------------------------------------------------
# Reproducibility prerequisites conservative completeness model
# ---------------------------------------------------------------------------

def _build_prereqs(
    artifact_results: list[dict[str, Any]],
    expected_sets_def: Any = None,
    trusted_sources: list[str] | set[str] | None = None,
    trusted_expected_sets: dict[str, Any] | None = None,
) -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
    """Derive reproducibility prerequisite completeness and artifact integrity.

    Separates artifact checksum integrity from campaign completeness.
    A prerequisite is complete only when sufficient evidence establishes that its
    full required set is present, valid, and supported by independent provenance.

    - Manifest-provided assertions (e.g. independent_oracle: true, fabricated URL)
      are never treated as independent proof without a separately trusted source.
    - Summary documents do not prove task-level oracle validity.
    - Aggregate statistics do not prove complete latency records.
    - Unverified self-assertions of completeness are rejected as UNVERIFIED_CLAIM.
    - Unverified declared expected sets report UNVERIFIED, producing PARTIAL, not VERIFIED.
    """
    # Normalize trusted_sources
    trusted_src_set: set[str] = set()
    if trusted_sources:
        if isinstance(trusted_sources, (list, tuple, set)):
            trusted_src_set = {str(s).strip() for s in trusted_sources if s and isinstance(s, str)}
        elif isinstance(trusted_sources, str):
            trusted_src_set = {trusted_sources.strip()}

    external_sets_mode = False
    active_expected_sets: dict[str, Any] = {}
    if trusted_expected_sets is not None:
        if isinstance(trusted_expected_sets, dict):
            active_expected_sets = trusted_expected_sets
            external_sets_mode = True
        else:
            active_expected_sets = {}
    elif isinstance(expected_sets_def, dict):
        active_expected_sets = expected_sets_def
    elif expected_sets_def is None:
        active_expected_sets = {}
    else:
        # expected_sets_def was provided but is not a dict (e.g. [] or "string")
        prereqs = {k: INVALID for k in sorted(_PREREQ_KEYS)}
        details = {
            k: {
                "status": INVALID,
                "artifact_integrity": INVALID,
                "expected_set_status": "INVALID",
                "expected_set_source": "none",
                "prerequisite_complete": False,
                "blocking": [],
                "missing_or_unverified": [],
                "evidence_provenance": "none",
                "notes": f"expected_sets definition must be a JSON object, got {type(expected_sets_def).__name__}",
            }
            for k in sorted(_PREREQ_KEYS)
        }
        return prereqs, details

    by_prereq: dict[str, list[dict[str, Any]]] = {k: [] for k in _PREREQ_KEYS}
    for art in artifact_results:
        atype = art.get("type")
        pkey = _TYPE_TO_PREREQ.get(atype or "", "")
        if pkey:
            by_prereq[pkey].append(art)

    prereqs: dict[str, str] = {}
    details: dict[str, dict[str, Any]] = {}

    for pkey in sorted(_PREREQ_KEYS):
        arts = by_prereq[pkey]
        es_info = active_expected_sets.get(pkey)

        if es_info is not None and not isinstance(es_info, dict):
            prereqs[pkey] = INVALID
            details[pkey] = {
                "status": INVALID,
                "artifact_integrity": INVALID,
                "expected_set_status": "INVALID",
                "expected_set_source": "externally_verified" if external_sets_mode else "declared",
                "prerequisite_complete": False,
                "blocking": [],
                "missing_or_unverified": [],
                "evidence_provenance": "none",
                "notes": f"expected_sets entry for {pkey!r} must be a JSON object, got {type(es_info).__name__}",
            }
            continue

        if not arts and not es_info:
            prereqs[pkey] = UNKNOWN
            details[pkey] = {
                "status": UNKNOWN,
                "artifact_integrity": UNKNOWN,
                "expected_set_status": UNKNOWN,
                "expected_set_source": "none",
                "prerequisite_complete": False,
                "blocking": [],
                "missing_or_unverified": [],
                "evidence_provenance": "none",
                "notes": "no artifacts or expected set declared for this prerequisite",
            }
            continue

        mandatory = [a for a in arts if a.get("mandatory", True) is not False]
        optional = [a for a in arts if a.get("mandatory") is False]

        # 1. Determine artifact integrity for the cohort
        statuses = [a.get("status", UNKNOWN) for a in arts]
        if not arts:
            artifact_integrity = UNKNOWN
        elif any(s == MISMATCH for s in statuses):
            artifact_integrity = MISMATCH
        elif any(s == INVALID for s in statuses):
            artifact_integrity = INVALID
        elif any(s == UNVERIFIABLE for s in statuses):
            artifact_integrity = UNVERIFIABLE
        elif any(s == MISSING for s in (a.get("status") for a in mandatory)):
            artifact_integrity = INCOMPLETE
        elif all(s == VERIFIED for s in statuses):
            artifact_integrity = VERIFIED
        elif all(s == OBSERVED_ONLY for s in statuses):
            artifact_integrity = OBSERVED_ONLY
        elif any(s == VERIFIED for s in statuses) and any(s == OBSERVED_ONLY for s in statuses):
            artifact_integrity = PARTIAL
        else:
            artifact_integrity = INCOMPLETE

        # 2. Validate expected set definition structure
        es_invalid = False
        es_invalid_reason = ""
        exp_paths_list: list[str] | None = None
        exp_count_val: int | None = None

        if es_info:
            if "expected_paths" in es_info:
                ep = es_info["expected_paths"]
                if not isinstance(ep, list):
                    es_invalid = True
                    es_invalid_reason = f"expected_paths for {pkey} must be a list"
                elif len(ep) == 0:
                    es_invalid = True
                    es_invalid_reason = f"expected_paths for {pkey} cannot be empty"
                else:
                    seen = set()
                    for item in ep:
                        if isinstance(item, bool) or not isinstance(item, str):
                            es_invalid = True
                            es_invalid_reason = f"invalid item in expected_paths for {pkey}: {item!r} (must be string)"
                            break
                        if not _is_safe_relative_path(item):
                            es_invalid = True
                            es_invalid_reason = f"unsafe or invalid path in expected_paths for {pkey}: {item!r}"
                            break
                        norm = _normalize_relative_path(item)
                        if norm in seen:
                            es_invalid = True
                            es_invalid_reason = f"duplicate path in expected_paths for {pkey}: {item!r}"
                            break
                        seen.add(norm)
                    if not es_invalid:
                        exp_paths_list = ep

            if not es_invalid and "expected_count" in es_info:
                ec = es_info["expected_count"]
                if isinstance(ec, bool) or not isinstance(ec, int) or ec <= 0:
                    es_invalid = True
                    es_invalid_reason = f"expected_count for {pkey} must be a positive integer"
                else:
                    exp_count_val = ec

        if es_invalid:
            prereqs[pkey] = INVALID
            details[pkey] = {
                "status": INVALID,
                "artifact_integrity": artifact_integrity if artifact_integrity != VERIFIED else INVALID,
                "expected_set_status": "INVALID",
                "expected_set_source": "externally_verified" if external_sets_mode else "declared",
                "prerequisite_complete": False,
                "blocking": [],
                "missing_or_unverified": [],
                "evidence_provenance": "none",
                "notes": es_invalid_reason,
            }
            continue

        # 3. Detect duplicate logical and physical records within category
        seen_norm_paths: dict[str, str] = {}
        seen_file_ids: dict[str, str] = {}
        duplicate_records: list[dict[str, str]] = []
        distinct_verified_norm_paths: set[str] = set()
        distinct_verified_file_ids: set[str] = set()

        for a in arts:
            raw_p = str(a.get("path") or "")
            norm_p = a.get("normalized_path") or _normalize_relative_path(raw_p)
            fid = a.get("file_id")
            is_dup = False
            dup_reason = ""

            if norm_p is not None:
                if norm_p in seen_norm_paths:
                    is_dup = True
                    orig_p = seen_norm_paths[norm_p]
                    if orig_p == raw_p:
                        dup_reason = f"exact duplicate artifact path {raw_p!r}"
                    else:
                        dup_reason = f"normalized path alias {raw_p!r} duplicates {orig_p!r}"
                else:
                    seen_norm_paths[norm_p] = raw_p

            if not is_dup and fid is not None:
                if fid in seen_file_ids:
                    is_dup = True
                    orig_p = seen_file_ids[fid]
                    dup_reason = f"physical file alias {raw_p!r} hardlinks {orig_p!r}"
                else:
                    seen_file_ids[fid] = raw_p

            if is_dup:
                duplicate_records.append({
                    "path": raw_p,
                    "reason": dup_reason,
                })
            else:
                if a.get("status") == VERIFIED:
                    if norm_p:
                        distinct_verified_norm_paths.add(norm_p)
                    if fid:
                        distinct_verified_file_ids.add(fid)

        distinct_verified_count = (
            min(len(distinct_verified_norm_paths), len(distinct_verified_file_ids))
            if distinct_verified_file_ids
            else len(distinct_verified_norm_paths)
        )

        expected_set_status = UNKNOWN
        expected_set_source = "none"
        prerequisite_complete = False
        evidence_provenance = "none"
        notes = None

        if es_info:
            prov = es_info.get("provenance")
            self_claim = bool(es_info.get("complete") or es_info.get("expected_set_complete"))

            # Determine whether the verification source is separately trusted
            is_trusted_source = False
            if external_sets_mode:
                is_trusted_source = True
                expected_set_source = "externally_verified"
                evidence_provenance = str(prov or "external_trusted_expected_sets")
            elif prov and isinstance(prov, str) and prov.strip() in trusted_src_set:
                is_trusted_source = True
                expected_set_source = "externally_verified"
                evidence_provenance = prov.strip()
            else:
                expected_set_source = "declared"
                evidence_provenance = str(prov or "manifest_declared")

            if self_claim and not is_trusted_source:
                expected_set_status = "UNVERIFIED_CLAIM"
                notes = "completeness self-asserted in manifest without separately trusted verification source"
            elif not is_trusted_source:
                expected_set_status = "UNVERIFIED"
                notes = "expected set declared in manifest lacks separately trusted verification source"
            else:
                # Separately trusted verification source exists
                if exp_paths_list is not None:
                    norm_exp_paths = set()
                    for p in exp_paths_list:
                        np = _normalize_relative_path(p)
                        if np:
                            norm_exp_paths.add(np)

                    if (
                        norm_exp_paths
                        and norm_exp_paths.issubset(distinct_verified_norm_paths)
                        and all(a.get("status") == VERIFIED for a in arts)
                        and not duplicate_records
                    ):
                        expected_set_status = "COMPLETE"
                        prerequisite_complete = True
                    else:
                        expected_set_status = "INCOMPLETE"
                        prerequisite_complete = False
                elif exp_count_val is not None:
                    if (
                        distinct_verified_count >= exp_count_val
                        and len(arts) == distinct_verified_count
                        and all(a.get("status") == VERIFIED for a in arts)
                        and not duplicate_records
                    ):
                        expected_set_status = "COMPLETE"
                        prerequisite_complete = True
                    else:
                        expected_set_status = "INCOMPLETE"
                        prerequisite_complete = False
                else:
                    expected_set_status = "UNKNOWN"
        else:
            expected_set_status = "UNKNOWN"
            expected_set_source = "none"

        if duplicate_records:
            dup_msg = f"duplicate artifact records detected: {'; '.join(d['reason'] for d in duplicate_records)}"
            notes = f"{notes}; {dup_msg}" if notes else dup_msg

        # 4. Special scientific domain boundaries
        # Requirement 8: Generic summary documents do not prove task-level oracle validity
        if pkey == "expected_outcomes_and_oracles" and arts:
            all_summaries = all(
                a.get("is_summary") is True
                or a.get("type") in ("oracle_summary", "campaign_manifest")
                or "freeze-manifest" in a.get("path", "").lower()
                for a in arts
            )
            if all_summaries:
                expected_set_status = "SUMMARY_ONLY"
                prerequisite_complete = False
                notes = "summary document does not prove task-level oracle validity"

        # Requirement 9: Aggregate statistics do not prove complete latency records
        if pkey == "result_and_latency_records" and arts:
            all_aggregates = all(
                a.get("is_summary") is True
                or a.get("type") in ("result_summary", "aggregate_report", "campaign_manifest")
                or "report" in a.get("path", "").lower()
                or "inventory" in a.get("path", "").lower()
                or "status" in a.get("path", "").lower()
                for a in arts
            )
            if all_aggregates:
                expected_set_status = "AGGREGATE_ONLY"
                prerequisite_complete = False
                notes = "aggregate statistics do not prove complete latency records"

        # 5. Compute missing or unverified artifacts
        missing_or_unverified: list[str] = [
            a.get("path", "")
            for a in mandatory
            if a.get("status") != VERIFIED
        ]
        if exp_paths_list:
            for p in exp_paths_list:
                np = _normalize_relative_path(p)
                if np and np not in distinct_verified_norm_paths and p not in missing_or_unverified:
                    missing_or_unverified.append(p)

        # 6. Determine top-level aggregate status string
        if prerequisite_complete and artifact_integrity == VERIFIED:
            status = VERIFIED
        elif artifact_integrity == MISMATCH:
            status = MISMATCH
        elif artifact_integrity == INVALID:
            status = INVALID
        elif artifact_integrity == UNVERIFIABLE:
            status = UNVERIFIABLE
        elif artifact_integrity == INCOMPLETE or expected_set_status == "INCOMPLETE":
            status = INCOMPLETE
        elif expected_set_status in ("SUMMARY_ONLY", "AGGREGATE_ONLY"):
            status = OBSERVED_ONLY
        elif artifact_integrity == OBSERVED_ONLY:
            status = OBSERVED_ONLY
        elif artifact_integrity == VERIFIED and not prerequisite_complete:
            # Verified files present, but expected-set completeness is not verified
            status = PARTIAL
        elif artifact_integrity == PARTIAL:
            status = PARTIAL
        else:
            status = UNKNOWN

        prereqs[pkey] = status
        details[pkey] = {
            "status": status,
            "artifact_integrity": artifact_integrity,
            "expected_set_status": expected_set_status,
            "expected_set_source": expected_set_source,
            "prerequisite_complete": prerequisite_complete,
            "distinct_verified_count": distinct_verified_count,
            "duplicate_artifacts": [d["reason"] for d in duplicate_records],
            "blocking": sorted(str(p) for p in missing_or_unverified),
            "missing_or_unverified": sorted(str(p) for p in missing_or_unverified),
            "evidence_provenance": evidence_provenance,
            "notes": notes,
        }

    return prereqs, details


# ---------------------------------------------------------------------------
# Output Publication & Safety (TOCTOU and Descriptor Anchoring)
# ---------------------------------------------------------------------------

def _collect_protected_targets(
    manifest_path: str,
    root_path: str,
    artifact_results: list[dict[str, Any]],
    extra_input_paths: list[str | Path | None] | None = None,
) -> tuple[dict[Path, str], dict[tuple[int, int], str]]:
    """Collect resolved paths and file identities of protected inputs and manifest."""
    protected_paths: dict[Path, str] = {}
    protected_inodes: dict[tuple[int, int], str] = {}

    try:
        root = Path(root_path).resolve()
    except (ValueError, UnicodeError, OSError):
        root = Path(root_path)

    try:
        mpath = Path(manifest_path).resolve()
        protected_paths[mpath] = "input manifest"
        if mpath.exists():
            try:
                st = mpath.stat()
                protected_inodes[(st.st_dev, st.st_ino)] = "input manifest"
            except OSError:
                pass
    except (ValueError, UnicodeError, OSError):
        pass

    for art in artifact_results:
        raw = art.get("path")
        if isinstance(raw, str) and _is_safe_relative_path(raw):
            try:
                art_full = (root / raw).resolve()
                label = f"audited artifact '{raw}'"
                protected_paths[art_full] = label
                if art_full.exists():
                    try:
                        st = art_full.stat()
                        protected_inodes[(st.st_dev, st.st_ino)] = label
                    except OSError:
                        pass
            except (ValueError, UnicodeError, OSError):
                pass

    if extra_input_paths:
        for p in extra_input_paths:
            if p is not None and str(p).strip():
                try:
                    p_obj = Path(p).resolve()
                    label = f"trusted input file '{p}'"
                    protected_paths[p_obj] = label
                    if p_obj.exists():
                        try:
                            st = p_obj.stat()
                            protected_inodes[(st.st_dev, st.st_ino)] = label
                        except OSError:
                            pass
                except (ValueError, UnicodeError, OSError):
                    pass

    return protected_paths, protected_inodes


def _validate_output_destinations(
    manifest_path: str,
    root_path: str,
    output_path: str | None,
    summary_path: str | None,
    artifact_results: list[dict[str, Any]],
    extra_input_paths: list[str | Path | None] | None = None,
) -> list[str]:
    """Validate that output and summary destinations do not overwrite input evidence or manifest."""
    errors: list[str] = []
    if not output_path and not summary_path:
        return errors

    # Conflict between output and summary
    if output_path and summary_path:
        try:
            out_p = Path(output_path).resolve()
            sum_p = Path(summary_path).resolve()
            if out_p == sum_p:
                errors.append(f"output and summary cannot target the same destination: {output_path}")
            elif out_p.exists() and sum_p.exists():
                try:
                    if os.path.samefile(out_p, sum_p):
                        errors.append("output and summary target the same file (alias detected)")
                except OSError:
                    pass
        except (ValueError, UnicodeError, OSError):
            pass

    protected_paths, protected_inodes = _collect_protected_targets(
        manifest_path,
        root_path,
        artifact_results,
        extra_input_paths=extra_input_paths,
    )

    dests_to_check = []
    if output_path:
        dests_to_check.append(("output", Path(output_path)))
    if summary_path:
        dests_to_check.append(("summary", Path(summary_path)))

    for label, dest in dests_to_check:
        try:
            res_dest = dest.resolve()
            if res_dest in protected_paths:
                errors.append(f"{label} destination targets protected {protected_paths[res_dest]}: {dest}")
            if dest.exists() or dest.is_symlink():
                try:
                    if dest.is_symlink():
                        link_target = dest.resolve()
                        if link_target in protected_paths:
                            errors.append(f"{label} destination symlink targets protected {protected_paths[link_target]}: {dest}")
                    dst_st = dest.stat()
                    ident = (dst_st.st_dev, dst_st.st_ino)
                    if ident in protected_inodes:
                        errors.append(f"{label} destination hardlink/alias targets protected {protected_inodes[ident]}: {dest}")
                except OSError:
                    pass
        except (ValueError, UnicodeError, OSError) as exc:
            errors.append(f"{label} destination path is invalid: {dest}")

    return errors


def _publish_file_securely(
    dest_path_str: str,
    content_str: str,
    protected_paths: dict[Path, str],
    protected_inodes: dict[tuple[int, int], str],
) -> None:
    """Publish content atomically and securely, protecting against symlink substitution.

    1. Checks parent path components without resolving symlinks; rejects symlinked directories.
    2. Opens parent directory descriptor with O_NOFOLLOW.
    3. Verifies destination is not a symlink or hardlink pointing to protected targets.
    4. Creates temporary file exclusively with O_EXCL and O_NOFOLLOW in the parent directory.
    5. Writes and flushes complete content.
    6. Atomically replaces destination using descriptor-anchored renameat (os.replace).
    7. Cleans up temporary files and descriptors on success and failure.
    """
    raw_p = Path(dest_path_str)
    abs_p = raw_p.absolute()
    parent = abs_p.parent

    # Traverse/create parent directory components descriptor-by-descriptor with O_NOFOLLOW
    # Never reopen parent through full pathname!
    parent_fd = _open_or_create_dir_securely(parent)
    base_name = abs_p.name
    temp_name = f".tmp_{uuid.uuid4().hex}_{base_name}"
    temp_fd: int | None = None

    try:
        # Check destination in parent directory before creating temp file
        try:
            st = os.lstat(base_name, dir_fd=parent_fd)
            ident = (st.st_dev, st.st_ino)
            if ident in protected_inodes:
                raise OSError(f"Destination is protected alias: {dest_path_str}")
            if stat.S_ISLNK(st.st_mode):
                target = (parent / os.readlink(base_name, dir_fd=parent_fd)).resolve()
                if target in protected_paths or (st.st_dev, st.st_ino) in protected_inodes:
                    raise OSError(f"Destination symlink targets protected path: {dest_path_str}")
        except FileNotFoundError:
            pass
        except (ValueError, UnicodeError) as exc:
            raise OSError(f"Invalid destination filename: {exc}") from exc

        # Create temporary file exclusively in parent directory
        temp_fd = os.open(
            temp_name,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o644,
            dir_fd=parent_fd,
        )
        data = content_str.encode("utf-8", errors="backslashreplace")
        total = 0
        while total < len(data):
            n = os.write(temp_fd, data[total:])
            total += n
        os.fsync(temp_fd)
        os.close(temp_fd)
        temp_fd = None

        # Verify destination before atomic replace
        try:
            st = os.lstat(base_name, dir_fd=parent_fd)
            ident = (st.st_dev, st.st_ino)
            if ident in protected_inodes:
                raise OSError(f"Destination changed to protected file: {dest_path_str}")
            if stat.S_ISLNK(st.st_mode):
                target = (parent / os.readlink(base_name, dir_fd=parent_fd)).resolve()
                if target in protected_paths:
                    raise OSError(f"Destination changed to symlink targeting protected path: {dest_path_str}")
        except FileNotFoundError:
            pass
        except (ValueError, UnicodeError) as exc:
            raise OSError(f"Invalid destination filename: {exc}") from exc

        # Atomic rename anchored to directory descriptor
        os.replace(temp_name, base_name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
    finally:
        if temp_fd is not None:
            try:
                os.close(temp_fd)
            except OSError:
                pass
        try:
            os.unlink(temp_name, dir_fd=parent_fd)
        except OSError:
            pass
        os.close(parent_fd)


# ---------------------------------------------------------------------------
# Core audit function
# ---------------------------------------------------------------------------

def audit(
    manifest_path: str,
    root_path: str,
    deterministic: bool = False,
    trusted_sources: list[str] | set[str] | None = None,
    trusted_expected_sets: dict[str, Any] | str | Path | None = None,
) -> dict[str, Any]:
    """Run the full evidence audit and return a result dict.

    This function never raises; all errors are captured in the returned dict.
    It is safe to call repeatedly without side effects.
    """
    if deterministic:
        audited_at = "1970-01-01T00:00:00Z"
    else:
        audited_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    base: dict[str, Any] = {
        "schema_version": 1,
        "audited_at": audited_at,
        "timestamp_kind": "deterministic_placeholder" if deterministic else "live_execution",
        "campaign_id": None,
        "root": str(root_path),
        "manifest_path": str(manifest_path),
        "totals": {s: 0 for s in ALL_STATUSES} | {"total": 0},
        "artifacts": [],
        "reproducibility_prerequisites": {k: UNKNOWN for k in sorted(_PREREQ_KEYS)},
        "reproducibility_details": {},
        "errors": [],
        "manifest_valid": False,
        "configuration_valid": True,
    }

    try:
        root = Path(root_path).absolute()
        mpath = Path(manifest_path)
    except (ValueError, UnicodeError, OSError) as exc:
        base["errors"].append(f"invalid path: {exc}")
        return base

    base["root"] = str(root)
    base["manifest_path"] = str(mpath)

    # Load manifest
    try:
        raw = mpath.read_bytes()
    except (OSError, PermissionError, ValueError, UnicodeError) as exc:
        base["errors"].append(f"cannot read manifest: {exc}")
        return base

    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        base["errors"].append(f"manifest JSON parse error: {exc}")
        return base

    # Validate manifest structure
    validation_errors = _validate_manifest(data)
    if validation_errors:
        base["errors"].extend(validation_errors)
        base["campaign_id"] = data.get("campaign_id") if isinstance(data, dict) and not isinstance(data.get("campaign_id"), bool) else None
        return base

    base["manifest_valid"] = True
    base["campaign_id"] = data["campaign_id"]

    # Audit each artifact
    artifact_entries = data.get("artifacts", [])
    results: list[dict[str, Any]] = []
    for entry in artifact_entries:
        result = _audit_artifact(entry, root)
        results.append(result)

    # Deterministic stable sort by path string
    results.sort(key=lambda r: str(r.get("path") if r.get("path") is not None else ""))

    # Compute totals
    totals: dict[str, int] = {s: 0 for s in ALL_STATUSES}
    totals["total"] = len(results)
    for r in results:
        s = r.get("status", INVALID)
        if s in totals:
            totals[s] += 1

    loaded_trusted_sets: dict[str, Any] | None = None
    configuration_valid = True
    if trusted_expected_sets is not None:
        if isinstance(trusted_expected_sets, (str, Path)):
            try:
                with open(str(trusted_expected_sets), "rb") as f:
                    loaded_trusted_sets = json.load(f)
            except Exception as exc:
                base["errors"].append(f"cannot load trusted_expected_sets: {exc}")
                configuration_valid = False
        elif isinstance(trusted_expected_sets, dict):
            loaded_trusted_sets = trusted_expected_sets
        else:
            base["errors"].append(f"trusted_expected_sets must be a path or dict, got {type(trusted_expected_sets).__name__}")
            configuration_valid = False

        if configuration_valid:
            struct_errors = _validate_expected_sets_structure(loaded_trusted_sets, label="trusted_expected_sets")
            if struct_errors:
                base["errors"].extend(struct_errors)
                configuration_valid = False

    base["configuration_valid"] = configuration_valid
    if not configuration_valid:
        prereqs = {k: INVALID for k in sorted(_PREREQ_KEYS)}
        details = {k: {"status": INVALID, "notes": "invalid trusted_expected_sets configuration"} for k in sorted(_PREREQ_KEYS)}
    else:
        prereqs, details = _build_prereqs(
            results,
            data.get("expected_sets", {}),
            trusted_sources=trusted_sources,
            trusted_expected_sets=loaded_trusted_sets,
        )

    base["artifacts"] = results
    base["totals"] = totals
    base["reproducibility_prerequisites"] = prereqs
    base["reproducibility_details"] = details
    return base


# ---------------------------------------------------------------------------
# Markdown summary
# ---------------------------------------------------------------------------

def _markdown_display_value(value: Any) -> str:
    """Make controls and invalid Unicode visible before code-span quoting."""
    raw = str(value).encode("utf-8", errors="backslashreplace").decode("utf-8")
    safe = []
    for char in raw:
        if char == "\n":
            safe.append(r"\n")
        elif char == "\r":
            safe.append(r"\r")
        elif char == "\t":
            safe.append(r"\t")
        elif ord(char) < 32 or ord(char) == 127:
            safe.append(f"\\x{ord(char):02x}")
        elif (ord(char) in (0x061C, 0x200E, 0x200F)
              or 0x202A <= ord(char) <= 0x202E
              or 0x2066 <= ord(char) <= 0x2069):
            safe.append(f"\\u{ord(char):04x}")
        elif char in ("\u0085", "\u2028", "\u2029"):
            safe.append(f"\\u{ord(char):04x}")
        else:
            safe.append(char)
    return "".join(safe)


def _markdown_code(value: Any, *, table_cell: bool = False) -> str:
    """Quote literal text using a native GFM code span, not inline HTML."""
    text = _markdown_display_value(value)
    if not text:
        return ""
    if table_cell:
        # GFM splits cells before parsing code spans and removes this one escape.
        text = text.replace("|", r"\|")
    # cmark-gfm limits delimiter lengths. Keep spans short and emit input
    # backticks as literal entities outside them, never as Markdown delimiters.
    spans = []
    for part in text.split("`"):
        # One padding space is stripped by GFM, except for all-space spans.
        padding = " " if part.strip(" ") else ""
        spans.append(f"`{padding}{part}{padding}`" if part else "")
    return "&#96;".join(spans)


def _markdown_text(value: Any, *, table_cell: bool = False) -> str:
    return _markdown_code(value, table_cell=table_cell)


def _markdown_summary(result: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# Benchmark Evidence Audit Summary")
    lines.append("")
    lines.append(f"- **Campaign**: {_markdown_code(result.get('campaign_id', 'unknown'))}")
    lines.append(f"- **Audited at**: {result.get('audited_at', '')}")
    if result.get("timestamp_kind") == "deterministic_placeholder":
        lines.append("- **Timestamp note**: Fixed reproducibility placeholder (1970-01-01T00:00:00Z)")
    lines.append(f"- **Root**: {_markdown_code(result.get('root', ''))}")
    lines.append(f"- **Manifest valid**: {result.get('manifest_valid', False)}")
    lines.append("")

    t = result.get("totals", {})
    lines.append("## Totals")
    lines.append("")
    lines.append("| Status | Count |")
    lines.append("|---|---|")
    lines.append(f"| Total | {t.get('total', 0)} |")
    for s in ALL_STATUSES:
        lines.append(f"| {s} | {t.get(s, 0)} |")
    lines.append("")

    errors = result.get("errors", [])
    if errors:
        lines.append("## Errors")
        lines.append("")
        for e in errors:
            lines.append(f"- {_markdown_text(e)}")
        lines.append("")

    arts = result.get("artifacts", [])
    if arts:
        lines.append("## Artifacts")
        lines.append("")
        lines.append("| Path | Type | Status | Present | Size (bytes) |")
        lines.append("|---|---|---|---|---|")
        for a in arts:
            path = a.get("path", "")
            display_path = _markdown_code(path, table_cell=True)
            atype = a.get("type") or ""
            status = a.get("status", "")
            present = "yes" if a.get("present") else ("no" if a.get("present") is False else "?")
            size = str(a.get("size_bytes")) if a.get("size_bytes") is not None else "-"
            lines.append(f"| {display_path} | {_markdown_text(atype, table_cell=True)} | **{status}** | {present} | {size} |")
        lines.append("")

    prereqs = result.get("reproducibility_prerequisites", {})
    details = result.get("reproducibility_details", {})
    if prereqs:
        lines.append("## Reproducibility Prerequisites")
        lines.append("")
        lines.append("| Prerequisite | Prerequisite Status | Artifact Integrity | Expected Set | Complete | Notes |")
        lines.append("|---|---|---|---|---|---|")
        for k, v in sorted(prereqs.items()):
            det = details.get(k, {})
            integrity = det.get("artifact_integrity", "UNKNOWN")
            es_status = det.get("expected_set_status", "UNKNOWN")
            comp = "yes" if det.get("prerequisite_complete") else "no"
            notes = det.get("notes") or ""
            lines.append(f"| {k} | **{v}** | {integrity} | {es_status} | {comp} | {_markdown_text(notes, table_cell=True)} |")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("manifest", help="Path to the artifact manifest JSON file")
    parser.add_argument("root", help="Designated filesystem root for artifact lookup")
    parser.add_argument(
        "--output", "-o", metavar="FILE",
        help="Write JSON output to FILE instead of stdout",
    )
    parser.add_argument(
        "--summary", "-s", metavar="FILE",
        help="Also write a Markdown summary to FILE",
    )
    parser.add_argument(
        "--deterministic", "-d", action="store_true",
        help="Use a fixed timestamp (1970-01-01T00:00:00Z) as reproducibility placeholder",
    )
    parser.add_argument(
        "--trusted-source", "-t", action="append", default=[],
        help="Designate an external trusted verification source identifier or URL",
    )
    parser.add_argument(
        "--trusted-expected-sets", metavar="FILE",
        help="Path to an independently established trusted expected-sets JSON file",
    )
    args = parser.parse_args(argv)

    result = audit(
        args.manifest,
        args.root,
        deterministic=args.deterministic,
        trusted_sources=args.trusted_source,
        trusted_expected_sets=args.trusted_expected_sets,
    )

    extra_inputs = [args.trusted_expected_sets] if args.trusted_expected_sets else None

    # Preflight output destination validation
    preflight_errors = _validate_output_destinations(
        args.manifest,
        args.root,
        args.output,
        args.summary,
        result.get("artifacts", []),
        extra_input_paths=extra_inputs,
    )
    if preflight_errors:
        # Input paths may contain credentials. Do not echo validation details to stderr.
        print("preflight error: unsafe or conflicting output destination", file=sys.stderr)
        return 2

    if not result.get("manifest_valid") or result.get("configuration_valid") is False or result.get("errors"):
        # The structured audit result retains diagnostics for trusted callers.
        # Never log its untrusted error strings, paths, or configuration values.
        if not result.get("manifest_valid"):
            print("audit error: manifest is unreadable or invalid", file=sys.stderr)
        elif result.get("configuration_valid") is False:
            print("audit error: invalid trusted expected-sets configuration", file=sys.stderr)
        else:
            print("audit error: invalid audit input", file=sys.stderr)
        return 2

    output_json = json.dumps(result, indent=2, sort_keys=False) + "\n"
    summary_md = _markdown_summary(result) + "\n" if args.summary else ""

    # Atomic publication using descriptor-anchored safe writer
    protected_paths, protected_inodes = _collect_protected_targets(
        args.manifest,
        args.root,
        result.get("artifacts", []),
        extra_input_paths=extra_inputs,
    )

    if args.output:
        try:
            _publish_file_securely(args.output, output_json, protected_paths, protected_inodes)
        except OSError:
            print("publication error: unable to write output", file=sys.stderr)
            return 2
    else:
        print(output_json, end="")

    if args.summary:
        try:
            _publish_file_securely(args.summary, summary_md, protected_paths, protected_inodes)
        except OSError:
            print("publication error: unable to write summary", file=sys.stderr)
            return 2

    # Determine exit code
    totals = result.get("totals", {})
    if totals.get(INVALID, 0) > 0:
        return 2
    prereqs = result.get("reproducibility_prerequisites", {})
    if any(status == INVALID for status in prereqs.values()):
        return 2
    if any(totals.get(s, 0) > 0 for s in (MISSING, MISMATCH, UNVERIFIABLE)):
        return 1
    if any(status in (INCOMPLETE, MISMATCH, UNVERIFIABLE) for status in prereqs.values()):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
