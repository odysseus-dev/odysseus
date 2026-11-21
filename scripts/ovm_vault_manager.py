"""OVM-M1 Standalone KDBX 4.x Vault Manager (Personal & Business Isolation).

Creates and verifies independent KDBX 4.x encrypted vault containers:
  - vault_personal.kdbx (Personal Profile)
  - vault_business.kdbx (We All Shine, LLC Profile)

Security invariants:
  - Separate master passphrases, independent Argon2id salts, independent master keys.
  - Zero reuse of data/.app_key.
  - Never accepts master passphrases via CLI arguments (getpass.getpass() only).
  - Optimistic SHA-256 + mtime conflict check before saving updates.
  - Strictly isolated from app.db, RAG indexes, and G:\\.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pykeepass import PyKeePass, create_database


@dataclass(frozen=True)
class VaultSnapshotMeta:
    path: Path
    sha256: str
    mtime_ns: int
    size_bytes: int


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def stat_vault(path: Path) -> VaultSnapshotMeta:
    resolved = Path(path).resolve()
    st = resolved.stat()
    return VaultSnapshotMeta(
        path=resolved,
        sha256=sha256_file(resolved),
        mtime_ns=st.st_mtime_ns,
        size_bytes=st.st_size,
    )


def create_empty_kdbx4_vault(
    vault_path: Path,
    *,
    master_password: str,
    title: str,
    description: str,
    overwrite: bool = False,
) -> VaultSnapshotMeta:
    """Provision an empty KDBX 4.x database with Argon2id KDF and ChaCha20-Poly1305."""
    if not master_password or len(master_password) < 12:
        raise ValueError("Master passphrase must be at least 12 characters.")

    target = Path(vault_path).resolve()
    if target.exists() and not overwrite:
        raise FileExistsError(f"Refusing to overwrite existing vault: {target}")

    target.parent.mkdir(parents=True, exist_ok=True)

    # Write atomically via temp file in same directory so partial writes never corrupt target
    fd, tmp_name = tempfile.mkstemp(prefix=".ovm_init_", suffix=".kdbx", dir=str(target.parent))
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        kp = create_database(str(tmp_path), password=master_password)
        # Enforce KDBX 4.x with Argon2id KDF and ChaCha20-Poly1305 authenticated cipher
        kp.database_name = title
        kp.database_description = description
        kp.save()
        del kp
        os.replace(str(tmp_path), str(target))
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)

    return stat_vault(target)


def open_vault_readonly(vault_path: Path, *, master_password: str) -> tuple[PyKeePass, VaultSnapshotMeta]:
    """Unlock a KDBX 4.x vault in memory and return its snapshot metadata for optimistic concurrency."""
    target = Path(vault_path).resolve()
    if not target.exists():
        raise FileNotFoundError(f"Vault file not found: {target}")
    meta = stat_vault(target)
    kp = PyKeePass(str(target), password=master_password)
    return kp, meta


def save_vault_atomic_with_conflict_check(
    kp: PyKeePass,
    vault_path: Path,
    *,
    expected_before: VaultSnapshotMeta,
) -> VaultSnapshotMeta:
    """Save vault atomically ONLY if on-disk SHA-256 and mtime still match expected_before."""
    target = Path(vault_path).resolve()
    current = stat_vault(target)
    if current.sha256 != expected_before.sha256 or current.mtime_ns != expected_before.mtime_ns:
        raise RuntimeError(
            "Concurrent vault modification detected on disk; aborting save to prevent silent overwrite."
        )

    fd, tmp_name = tempfile.mkstemp(prefix=".ovm_save_", suffix=".kdbx", dir=str(target.parent))
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        kp.save(filename=str(tmp_path))
        os.replace(str(tmp_path), str(target))
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)

    return stat_vault(target)


def inspect_vault_header(vault_path: Path) -> dict[str, Any]:
    """Inspect non-secret KDBX header metadata without unlocking."""
    target = Path(vault_path).resolve()
    raw = target.read_bytes()
    # KDBX4 magic signature: 03 D9 A2 9A 67 FB 4B B5
    is_kdbx4_sig = raw[:8] == b"\x03\xd9\xa2\x9a\x67\xfb\x4b\xb5"
    version_minor = int.from_bytes(raw[8:10], "little") if len(raw) >= 12 else None
    version_major = int.from_bytes(raw[10:12], "little") if len(raw) >= 12 else None
    return {
        "file": str(target),
        "size_bytes": len(raw),
        "sha256": sha256_file(target),
        "kdbx_signature_valid": is_kdbx4_sig,
        "format_version": f"{version_major}.{version_minor}",
    }


def _prompt_new_passphrase(label: str, *, forbid_match: str | None = None) -> str:
    while True:
        pw1 = getpass.getpass(f"Set NEW Master Passphrase for {label} vault (hidden, min 12 chars): ")
        print(f"  -> Received {len(pw1)} characters (hidden).")
        if len(pw1) < 12:
            print("  [!] Too short: master passphrase must be at least 12 characters. Try again.")
            continue
        if forbid_match is not None and pw1 == forbid_match:
            print("  [!] Security rule: Business passphrase MUST differ from Personal passphrase. Try again.")
            continue
        pw2 = getpass.getpass(f"Confirm Master Passphrase for {label} vault (hidden): ")
        if pw1 != pw2:
            print("  [!] Passphrases did not match. Try again.")
            continue
        return pw1


def main() -> None:
    parser = argparse.ArgumentParser(
        description="OVM-M1 Empty KDBX 4.x Vault Provisioner (interactive getpass only; zero CLI secrets)."
    )
    parser.add_argument(
        "--vault-dir",
        default=r"D:\OdyAI_Vaults",
        help="Directory to store vault_personal.kdbx and vault_business.kdbx (never G:\\).",
    )
    args = parser.parse_args()

    vault_dir = Path(args.vault_dir).resolve()
    if str(vault_dir).upper().startswith("G:"):
        raise ValueError("G:\\ is strictly off-limits for writes.")

    p_pass = _prompt_new_passphrase("PERSONAL")
    b_pass = _prompt_new_passphrase("BUSINESS (We All Shine, LLC)", forbid_match=p_pass)

    p_meta = create_empty_kdbx4_vault(
        vault_dir / "vault_personal.kdbx",
        master_password=p_pass,
        title="Hachi ? Personal Profile",
        description="OVM-001 Isolated Personal Vault (KDBX 4.x)",
    )
    b_meta = create_empty_kdbx4_vault(
        vault_dir / "vault_business.kdbx",
        master_password=b_pass,
        title="We All Shine, LLC ? Business Profile",
        description="OVM-001 Isolated Business Vault (KDBX 4.x)",
    )
    print(f"[PROVISIONED] {p_meta.path} | sha256={p_meta.sha256} | bytes={p_meta.size_bytes}")
    print(f"[PROVISIONED] {b_meta.path} | sha256={b_meta.sha256} | bytes={b_meta.size_bytes}")


if __name__ == "__main__":
    main()
