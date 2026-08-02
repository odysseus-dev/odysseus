#!/usr/bin/env python3
"""Build a deterministic, credential-screened corresponding-source archive."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path, PurePosixPath


INTEGRATION_FILES = (
    "PDV_INTEGRATION_BOUNDARY.md",
    "PDV_UPSTREAM_SNAPSHOT.json",
    "PDV_WINDOWS_NATIVE_BASELINE.md",
    "PDV_NATIVE_FEATURE_PROOF.md",
    "routes/pdv_routes.py",
    "mcp_servers/pdv_control_server.py",
    "src/pdv_provider_guard.py",
    "scripts/pdv_verify_native_windows_baseline.ps1",
    "scripts/pdv_windows_lifecycle.ps1",
    "scripts/pdv_initialize_adapter_key.ps1",
    "scripts/pdv_build_source_archive.py",
    "scripts/pdv_runtime_provider_probe.py",
    "tests/cli/test_pdv_windows_baseline.py",
    "tests/cli/test_pdv_source_archive.py",
    "tests/test_pdv_routes.py",
    "tests/test_pdv_mcp_bridge.py",
    "tests/test_pdv_provider_guard.py",
    "tests/test_pdv_native_integration_proof.py",
)
PROHIBITED_PARTS = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", "secrets"}
PROHIBITED_RUNTIME_PREFIXES = {("data", "pdv-integration-v1"), ("logs", "pdv-integration-v1")}
PROHIBITED_FILE_NAMES = {".env", "adapter.key", "id_ed25519", "id_rsa"}
PROHIBITED_FILE_SUFFIXES = (".env", ".key", ".p12", ".pem", ".pfx")
FIXED_TIME = (1980, 1, 1, 0, 0, 0)
SECRET_PATTERNS = (
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(rb"\b(?:sk-(?:proj-|live-)?[A-Za-z0-9_-]{24,}|gh[pousr]_[A-Za-z0-9]{30,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{30,})\b"),
    re.compile(rb"\body_[A-Za-z0-9_-]{24,}\b"),
    re.compile(rb"(?i)\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|client[_-]?secret|password)\b[\"']?\s*[:=]\s*[\"'][A-Za-z0-9+/_=-]{24,}[\"']"),
)
SOURCE_SUFFIXES = {
    ".bat", ".c", ".cc", ".cfg", ".cjs", ".cpp", ".css", ".cts", ".go", ".h", ".hpp", ".html", ".ini",
    ".java", ".js", ".json", ".jsx", ".lock", ".md", ".mjs", ".mts", ".ps1", ".py", ".rb", ".rs", ".sh",
    ".sql", ".svelte", ".toml", ".ts", ".tsx", ".txt", ".vue", ".xml", ".yaml", ".yml",
}
SOURCE_NAMES = {"dockerfile", "gemfile", "license", "makefile", "procfile", "rakefile"}


def _is_runtime_path(value: str) -> bool:
    parts = tuple(part.lower() for part in PurePosixPath(value.replace("\\", "/")).parts)
    return parts[:2] in PROHIBITED_RUNTIME_PREFIXES


def _is_untracked_runtime_path(value: str) -> bool:
    parts = tuple(part.lower() for part in PurePosixPath(value.replace("\\", "/")).parts)
    return bool(parts and parts[0] in {"data", "logs"})


def _safe_relative(root: Path, value: str) -> tuple[str, Path]:
    posix = PurePosixPath(value.replace("\\", "/"))
    lowered_parts = tuple(part.lower() for part in posix.parts)
    file_name = lowered_parts[-1] if lowered_parts else ""
    if (
        posix.is_absolute()
        or ".." in posix.parts
        or any(part in PROHIBITED_PARTS for part in lowered_parts)
        or _is_runtime_path(value)
        or file_name in PROHIBITED_FILE_NAMES
        or file_name.endswith(PROHIBITED_FILE_SUFFIXES)
    ):
        raise ValueError("source include escapes or enters a prohibited runtime directory")
    absolute = (root / Path(*posix.parts)).resolve()
    absolute.relative_to(root)
    return posix.as_posix(), absolute


def _git_paths(root: Path, *arguments: str) -> list[str]:
    output = subprocess.run(
        ["git", "-c", f"safe.directory={root.as_posix()}", "ls-files", *arguments, "-z"],
        cwd=root,
        check=True,
        capture_output=True,
    ).stdout
    return [item.decode("utf-8") for item in output.split(b"\0") if item]


def _contains_high_confidence_secret(content: bytes) -> bool:
    return any(pattern.search(content) for pattern in SECRET_PATTERNS)


def _is_untracked_source_candidate(root: Path, value: str) -> bool:
    path = root / Path(*PurePosixPath(value.replace("\\", "/")).parts)
    name = path.name.lower()
    return path.suffix.lower() in SOURCE_SUFFIXES or name in SOURCE_NAMES or (not path.suffix and path.is_file() and path.read_bytes()[:2] == b"#!")


def _source_tree_sha256(file_hashes: dict[str, str]) -> str:
    canonical = b"".join(
        name.encode("utf-8") + b"\0" + digest.encode("ascii") + b"\n"
        for name, digest in sorted(file_hashes.items())
    )
    return hashlib.sha256(canonical).hexdigest()


def build(repository_root: Path, output: Path, includes: list[str]) -> dict[str, object]:
    root = repository_root.resolve()
    output = output.resolve()
    output.relative_to(root)
    tracked = set(_git_paths(root, "--cached"))
    untracked = set(_git_paths(root, "--others", "--exclude-standard"))
    candidates = set(tracked)
    candidates.update(path for path in (*INTEGRATION_FILES, *includes) if (root / path).is_file())
    excluded_untracked = sorted(untracked - candidates)
    explicit_includes = {value.replace("\\", "/") for value in includes}
    files: dict[str, bytes] = {}
    excluded_untracked = [candidate.replace("\\", "/") for candidate in excluded_untracked if not _is_runtime_path(candidate)]
    protected_values: list[bytes] = []
    adapter_key_path = root / "data" / "pdv-integration-v1" / "adapter.key"
    if adapter_key_path.is_file():
        adapter_key = adapter_key_path.read_bytes().strip()
        if len(adapter_key) == 64 and re.fullmatch(rb"[a-f0-9]{64}", adapter_key):
            protected_values.append(adapter_key)
    for candidate in sorted(candidates):
        try:
            relative, absolute = _safe_relative(root, candidate)
        except ValueError:
            if candidate in tracked or candidate.replace("\\", "/") in explicit_includes:
                raise
            if not _is_runtime_path(candidate):
                excluded_untracked.append(candidate.replace("\\", "/"))
            continue
        if absolute.is_file():
            content = absolute.read_bytes()
            if _contains_high_confidence_secret(content) or any(secret in content for secret in protected_values):
                raise ValueError("source inventory contains high-confidence secret material")
            files[relative] = content
    if "LICENSE" not in files:
        raise ValueError("LICENSE is required in corresponding source")
    commit = subprocess.run(
        ["git", "-c", f"safe.directory={root.as_posix()}", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    branch = subprocess.run(
        ["git", "-c", f"safe.directory={root.as_posix()}", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    license_sha256 = hashlib.sha256(files["LICENSE"]).hexdigest()
    file_hashes = {name: hashlib.sha256(content).hexdigest() for name, content in sorted(files.items())}
    source_tree_sha256 = _source_tree_sha256(file_hashes)
    manifest = {
        "schemaVersion": 2,
        "canonicalRepository": "https://github.com/odysseus-dev/odysseus",
        "upstreamCommit": commit,
        "integrationBranch": branch,
        "license": "AGPL-3.0-or-later",
        "licenseSha256": license_sha256,
        "sourceInventoryMode": "tracked-plus-explicit-integration-files",
        "excludedUntrackedCount": len(excluded_untracked),
        "excludedUntrackedPathsSha256": hashlib.sha256("\n".join(sorted(excluded_untracked)).encode("utf-8")).hexdigest(),
        "explicitIncludes": sorted(explicit_includes),
        "sourceTreeSha256": source_tree_sha256,
        "files": file_hashes,
    }
    files["CORRESPONDING_SOURCE_MANIFEST.json"] = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    with zipfile.ZipFile(temporary, "w") as archive:
        for name, content in sorted(files.items()):
            info = zipfile.ZipInfo(name, FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, content, compresslevel=9)
    temporary.replace(output)
    archive_bytes = output.read_bytes()
    receipt = {
        "schemaVersion": 2,
        "archiveSha256": hashlib.sha256(archive_bytes).hexdigest(),
        "archiveBytes": len(archive_bytes),
        "fileCount": len(files),
        "upstreamCommit": commit,
        "integrationBranch": branch,
        "licenseSha256": license_sha256,
        "sourceInventoryMode": "tracked-plus-explicit-integration-files",
        "explicitIncludes": sorted(explicit_includes),
        "sourceTreeSha256": source_tree_sha256,
        "excludedUntrackedCount": len(excluded_untracked),
        "excludedUntrackedPathsSha256": hashlib.sha256("\n".join(sorted(excluded_untracked)).encode("utf-8")).hexdigest(),
        "sourceEndpoint": "/api/pdv/source/archive",
    }
    output.with_suffix(output.suffix + ".json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return receipt


def _invalidate_failed_output(repository_root: Path, output_path: Path) -> bool:
    root = repository_root.resolve()
    output = output_path.resolve()
    try:
        output.relative_to(root)
    except ValueError:
        return False
    success = True
    # Invalidate the small verification sidecar first. The archive itself may be
    # locked by a Windows streaming response, but without the sidecar it cannot
    # pass the authenticated route's integrity check.
    for stale in (output.with_suffix(output.suffix + ".json"), output.with_suffix(output.suffix + ".tmp"), output):
        try:
            stale.unlink(missing_ok=True)
        except OSError:
            success = False
    return success


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--include", action="append", default=[])
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        receipt = build(Path(args.repository_root), Path(args.output), args.include)
    except Exception as error:
        _invalidate_failed_output(Path(args.repository_root), Path(args.output))
        if args.json:
            print(json.dumps({"ok": False, "error": type(error).__name__}))
        else:
            print(f"source archive build failed: {type(error).__name__}", file=sys.stderr)
        return 1
    report = {"ok": True, **receipt}
    print(json.dumps(report) if args.json else json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
