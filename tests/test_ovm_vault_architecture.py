"""Isolated OVM-M1 tests for KDBX 4.x vault separation, backup/restore, and conflict detection."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pykeepass.exceptions import CredentialsError

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.ovm_backup_vaults import backup_encrypted_vaults, verify_and_restore_backup
from scripts.ovm_vault_manager import (
    create_empty_kdbx4_vault,
    inspect_vault_header,
    open_vault_readonly,
    save_vault_atomic_with_conflict_check,
)


def test_independent_personal_and_business_kdbx4_vaults(tmp_path: Path) -> None:
    p_path = tmp_path / "vault_personal.kdbx"
    b_path = tmp_path / "vault_business.kdbx"

    p_pass = "Synthetic-Personal-Key-2026!"
    b_pass = "Synthetic-Business-Key-2026!"

    p_meta = create_empty_kdbx4_vault(p_path, master_password=p_pass, title="Personal", description="Test Personal")
    b_meta = create_empty_kdbx4_vault(b_path, master_password=b_pass, title="Business", description="Test Business")

    # Different salts/keys ensure distinct ciphertexts even for empty vaults
    assert p_meta.sha256 != b_meta.sha256

    p_hdr = inspect_vault_header(p_path)
    b_hdr = inspect_vault_header(b_path)
    assert p_hdr["kdbx_signature_valid"] is True and p_hdr["format_version"].startswith("4.")
    assert b_hdr["kdbx_signature_valid"] is True and b_hdr["format_version"].startswith("4.")

    # Personal cannot unlock Business; Business cannot unlock Personal; wrong password fails
    with pytest.raises(CredentialsError):
        open_vault_readonly(b_path, master_password=p_pass)
    with pytest.raises(CredentialsError):
        open_vault_readonly(p_path, master_password=b_pass)
    with pytest.raises(CredentialsError):
        open_vault_readonly(p_path, master_password="Wrong-Password-Attempt-123")

    # Valid unlock shows 0 real entries
    kp_p, _ = open_vault_readonly(p_path, master_password=p_pass)
    assert len(kp_p.entries) == 0


def test_encrypted_backup_restore_corruption_and_interruption(tmp_path: Path) -> None:
    live_dir = tmp_path / "live"
    backup_root = tmp_path / "backups"
    restore_dir = tmp_path / "restored"

    p_path = live_dir / "vault_personal.kdbx"
    b_path = live_dir / "vault_business.kdbx"
    p_pass = "Synthetic-Personal-Backup-99!"
    b_pass = "Synthetic-Business-Backup-88!"

    create_empty_kdbx4_vault(p_path, master_password=p_pass, title="Personal", description="P")
    create_empty_kdbx4_vault(b_path, master_password=b_pass, title="Business", description="B")

    # Add a synthetic non-secret entry to verify full round-trip restore
    kp_p, meta_p = open_vault_readonly(p_path, master_password=p_pass)
    kp_p.add_entry(kp_p.root_group, "Synthetic Test Item", "synthetic_user", "synthetic_test_only_123")
    save_vault_atomic_with_conflict_check(kp_p, p_path, expected_before=meta_p)

    # 1. Valid encrypted backup + restore
    res = backup_encrypted_vaults([p_path, b_path], backup_root=backup_root)
    bdir = Path(res["backup_dir"])
    restored = verify_and_restore_backup(bdir, restore_dir=restore_dir)
    assert len(restored) == 2

    kp_restored, _ = open_vault_readonly(restore_dir / "vault_personal.kdbx", master_password=p_pass)
    assert len(kp_restored.entries) == 1
    assert kp_restored.entries[0].title == "Synthetic Test Item"

    # 2. Interrupted backup leaves zero partial directories behind
    before_dirs = list(backup_root.iterdir())
    with pytest.raises(IOError, match="Simulated backup interruption"):
        backup_encrypted_vaults([p_path, b_path], backup_root=backup_root, simulate_interrupt=True)
    after_dirs = list(backup_root.iterdir())
    assert before_dirs == after_dirs

    # 3. Corrupted backup is detected via SHA-256 manifest before restore
    corrupt_target = bdir / "vault_personal.kdbx"
    data = bytearray(corrupt_target.read_bytes())
    data[-1] ^= 0xFF
    corrupt_target.write_bytes(bytes(data))

    with pytest.raises(ValueError, match="CORRUPTED BACKUP DETECTED"):
        verify_and_restore_backup(bdir, restore_dir=tmp_path / "restored_corrupt")


def test_concurrent_update_conflict_guard(tmp_path: Path) -> None:
    p_path = tmp_path / "vault_personal.kdbx"
    p_pass = "Synthetic-Conflict-Test-2026!"
    create_empty_kdbx4_vault(p_path, master_password=p_pass, title="Personal", description="P")

    # Simulate two concurrent clients opening the same vault version
    client_a, meta_a = open_vault_readonly(p_path, master_password=p_pass)
    client_b, meta_b = open_vault_readonly(p_path, master_password=p_pass)

    # Client A saves first -> succeeds
    client_a.add_entry(client_a.root_group, "Update A", "user_a", "pass_a_synthetic")
    save_vault_atomic_with_conflict_check(client_a, p_path, expected_before=meta_a)

    # Client B tries to save stale state -> blocked with RuntimeError (prevents silent overwrite)
    client_b.add_entry(client_b.root_group, "Update B", "user_b", "pass_b_synthetic")
    with pytest.raises(RuntimeError, match="Concurrent vault modification detected"):
        save_vault_atomic_with_conflict_check(client_b, p_path, expected_before=meta_b)
