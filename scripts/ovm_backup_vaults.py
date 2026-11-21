"""OVM-M1 Encrypted Vault Backup, Integrity Verification & Restore Subsystem.

Backs up encrypted KDBX 4.x vault files atomically with SHA-256 manifests.
Never decrypts vaults during backup, never writes to G:\\, and never replaces
an existing valid backup if staging/verification fails.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _guard_not_g_drive(path: Path) -> None:
    if str(Path(path).resolve()).upper().startswith("G:"):
        raise ValueError(f"G:\\ is strictly forbidden for writes: {path}")


def backup_encrypted_vaults(
    vault_files: Iterable[Path],
    *,
    backup_root: Path,
    simulate_interrupt: bool = False,
) -> dict:
    """Create a timestamped, SHA-256-verified encrypted backup via atomic directory staging."""
    backup_root = Path(backup_root).resolve()
    _guard_not_g_drive(backup_root)
    backup_root.mkdir(parents=True, exist_ok=True)

    sources = [Path(v).resolve() for v in vault_files]
    for src in sources:
        if not src.exists():
            raise FileNotFoundError(f"Vault source missing: {src}")

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    final_dir = backup_root / f"ovm_backup_{ts}"
    stage_dir = Path(tempfile.mkdtemp(prefix=".ovm_stage_", dir=str(backup_root)))

    manifest_entries = []
    try:
        for src in sources:
            src_hash = sha256_file(src)
            staged_file = stage_dir / src.name
            shutil.copy2(src, staged_file)

            if simulate_interrupt:
                raise IOError("Simulated backup interruption before verification/commit.")

            staged_hash = sha256_file(staged_file)
            if staged_hash != src_hash:
                raise IOError(f"Backup hash mismatch for {src.name}: {staged_hash} != {src_hash}")

            # Verify KDBX4 header magic bytes
            magic = staged_file.read_bytes()[:8]
            if magic != b"\x03\xd9\xa2\x9a\x67\xfb\x4b\xb5":
                raise ValueError(f"Invalid KDBX4 header in staged backup: {src.name}")

            manifest_entries.append({
                "filename": src.name,
                "size_bytes": staged_file.stat().st_size,
                "sha256": staged_hash,
            })

        manifest = {
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "encrypted_only": True,
            "vault_count": len(manifest_entries),
            "files": manifest_entries,
        }
        (stage_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        # Atomic commit: rename complete verified staging folder to final timestamped backup folder
        os.replace(str(stage_dir), str(final_dir))
        return {"backup_dir": str(final_dir), "manifest": manifest}
    finally:
        if stage_dir.exists():
            shutil.rmtree(stage_dir, ignore_errors=True)


def verify_and_restore_backup(
    backup_dir: Path,
    *,
    restore_dir: Path,
) -> list[Path]:
    """Verify SHA-256 manifest of an encrypted backup and restore encrypted KDBX files."""
    bdir = Path(backup_dir).resolve()
    rdir = Path(restore_dir).resolve()
    _guard_not_g_drive(rdir)

    manifest_path = bdir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Backup manifest missing: {manifest_path}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    rdir.mkdir(parents=True, exist_ok=True)

    restored: list[Path] = []
    for entry in manifest["files"]:
        src_backup = bdir / entry["filename"]
        if not src_backup.exists():
            raise FileNotFoundError(f"Backup file missing: {src_backup}")
        actual_hash = sha256_file(src_backup)
        if actual_hash != entry["sha256"]:
            raise ValueError(f"CORRUPTED BACKUP DETECTED for {entry['filename']}: expected {entry['sha256']}, got {actual_hash}")

        dst = rdir / entry["filename"]
        shutil.copy2(src_backup, dst)
        assert sha256_file(dst) == entry["sha256"]
        restored.append(dst)

    return restored
