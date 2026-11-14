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
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from src.constants import APP_KEY_FILE

logger = logging.getLogger(__name__)

_KEY_PATH = Path(APP_KEY_FILE)
_PREFIX = "enc:"
_fernet: Fernet | None = None

# Serialises first-use key creation so two threads in the same process
# cannot race on the shared temp-file path inside _load_or_create_key().
_key_creation_lock = threading.Lock()


def _load_or_create_key() -> bytes:
    # Fast path: key already exists on disk.
    if _KEY_PATH.exists():
        return _KEY_PATH.read_bytes()

    # Slow path: create the key atomically.  On a fresh multi-worker
    # deployment two workers may race here.  We write the complete key
    # to a temp file, fsync it, then use os.link to make it visible at
    # the final path in a single atomic step.  If os.link fails because
    # another worker already created the final file, we read the
    # winner's key.  This avoids the O_CREAT-then-write window where a
    # racing reader can see an empty or partial key file.
    key = Fernet.generate_key()
    _KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = _KEY_PATH.parent / f".app_key.tmp.{os.getpid()}"
    try:
        # Use os.open + os.fdopen so we can set 0o600 at creation time
        # (plain open() applies umask, typically giving 0o644).
        tmp_fd = os.open(str(tmp_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(tmp_fd, "wb") as f:
            f.write(key)
            f.flush()
            os.fsync(f.fileno())
        # Atomic: either the link succeeds and the complete key is
        # visible, or FileExistsError means another worker won the
        # race and we read its (complete) key.  No reader ever sees
        # a partial file.
        try:
            os.link(tmp_path, _KEY_PATH)
        except FileExistsError:
            logger.info("App key already created by another worker — reusing")
            return _KEY_PATH.read_bytes()
        logger.info(f"Generated new app key at {_KEY_PATH}")
        return key
    finally:
        # Best-effort cleanup of the temp file — not critical if it
        # lingers (a stale tmp uses negligible space).
        try:
            tmp_path.unlink()
        except OSError:
            pass


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        with _key_creation_lock:
            # Double-check inside the lock — another thread may have
            # finished creation while we were waiting.
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
