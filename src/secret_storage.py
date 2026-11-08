"""
secret_storage.py

Fernet-based symmetric encryption for secrets stored in the SQLite DB
(IMAP / SMTP passwords today; safe to extend). The key lives at
`data/.app_key`, mode 0o600, generated on first call. `data/` is
gitignored so the key never ships with the repo.

Threat model: protects against SQLite-file exfiltration (stolen
backup, leaked container layer, sibling-tenant read). Does **not**
protect against a process compromise — anyone who can read this
module's memory or the key file has plaintext.

Encrypted values carry an `enc:` prefix so the migration is
idempotent: passing an already-encrypted value to `encrypt()` is a
no-op; passing a plaintext value to `decrypt()` returns it
unchanged. That lets legacy rows coexist with new ones until a
single migration pass rewrites them.
"""

import os
import logging
import threading
import time
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from src.constants import APP_KEY_FILE

logger = logging.getLogger(__name__)

_KEY_PATH = Path(APP_KEY_FILE)
_PREFIX = "enc:"
_fernet: Fernet | None = None
# Serializes first-start key creation and the lazy Fernet below. Two callers
# that both saw "no key yet" used to generate and write two different keys;
# whatever was encrypted with the overwritten one decrypted to "" after a
# restart. Re-entrant because _get_fernet holds it around the key load.
_KEY_LOCK = threading.RLock()


def _read_existing_key() -> bytes:
    # Another process may have created the file and not written the key yet.
    for _ in range(40):
        key = _KEY_PATH.read_bytes()
        if key:
            return key
        time.sleep(0.05)
    raise RuntimeError(f"App key file {_KEY_PATH} is empty")


def _load_or_create_key() -> bytes:
    with _KEY_LOCK:
        if _KEY_PATH.exists():
            return _read_existing_key()
        _KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
        key = Fernet.generate_key()
        # O_EXCL: only one creator wins, across processes too; a loser adopts
        # the winner's key instead of overwriting it. Mode 0o600 from creation,
        # not via a later chmod (Windows ignores it; the profile dir is
        # ACL-restricted).
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        try:
            fd = os.open(_KEY_PATH, flags, 0o600)
        except FileExistsError:
            return _read_existing_key()
        with os.fdopen(fd, "wb") as f:
            f.write(key)
            f.flush()
            os.fsync(f.fileno())
        logger.info(f"Generated new app key at {_KEY_PATH}")
        return key


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        with _KEY_LOCK:
            if _fernet is None:
                _fernet = Fernet(_load_or_create_key())
    return _fernet


def encrypt(plaintext: str) -> str:
    """Encrypt a string. Empty input passes through. Already-encrypted
    values pass through unchanged so re-encrypting is a no-op."""
    if not plaintext:
        return plaintext or ""
    if plaintext.startswith(_PREFIX):
        return plaintext
    token = _get_fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")
    return _PREFIX + token


def decrypt(value: str) -> str:
    """Decrypt an `enc:`-prefixed value. Plaintext (legacy) passes
    through unchanged. Returns "" on decryption failure so a corrupt
    or rotated-key row degrades to "unconfigured" rather than 500."""
    if not value:
        return value or ""
    if not value.startswith(_PREFIX):
        return value
    try:
        return _get_fernet().decrypt(value[len(_PREFIX):].encode("ascii")).decode("utf-8")
    except InvalidToken:
        logger.error("Failed to decrypt stored secret — wrong key or corrupt token")
        return ""
    except Exception as e:
        logger.error(f"Decrypt failure: {e}")
        return ""


def is_encrypted(value: str) -> bool:
    return bool(value) and value.startswith(_PREFIX)
