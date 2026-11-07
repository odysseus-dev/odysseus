"""
Authentication module — multi-user password hashing, session tokens, config persistence.
Config stored in data/auth.json. Uses bcrypt directly.
"""

import enum
import json
import os
import secrets
import threading
import time
import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Optional, Dict, Any, List

# POSIX-only: fcntl provides inter-process file locking used by
# _interprocess_auth_lock.  On native Windows it doesn't exist, so
# we fall back to intra-process-only serialisation (single-worker
# deployments are the norm there, and OIDC defaults to off).
try:
    import fcntl
    HAS_FCNTL = True
except ImportError:
    HAS_FCNTL = False
    fcntl = None  # type: ignore[assignment]

import bcrypt
import pyotp

logger = logging.getLogger(__name__)


from core.atomic_io import atomic_write_json as _atomic_write_json  # noqa: E402

DEFAULT_PRIVILEGES = {
    "can_use_agent": True,
    "can_use_browser": True,
    "can_use_bash": False,
    "can_use_documents": True,
    "can_use_research": True,
    "can_generate_images": True,
    "can_manage_memory": True,
    "max_messages_per_day": 0,
    "allowed_models": [],
    "allowed_models_restricted": False,
    # Explicit "block every model" sentinel. An empty `allowed_models` list is
    # ambiguous — it's also what gets sent when the admin clicks "[All]" — so
    # we need a dedicated flag to express "this user may use no models at all"
    # distinctly from "this user has no restriction".
    "block_all_models": False,
}

# Admins get everything
ADMIN_PRIVILEGES = {k: (True if isinstance(v, bool) else (0 if isinstance(v, int) else [])) for k, v in DEFAULT_PRIVILEGES.items()}
ADMIN_PRIVILEGES["allowed_models_restricted"] = False
# Admins must never be blocked from using models — the generic dict
# comprehension above flips every boolean default to True, which would be
# backwards for this sentinel.
ADMIN_PRIVILEGES["block_all_models"] = False

from src.constants import AUTH_FILE, PASSWORD_MIN_LENGTH, USERNAME_MAX_LENGTH
from src.owner_identity import RESERVED_AUTH_USERNAMES
DEFAULT_AUTH_PATH = AUTH_FILE
TOKEN_TTL = 60 * 60 * 24 * 7  # 7 days

# Usernames the auth + middleware layer reserves for request sentinels and
# internal storage owners; they must never belong to a real login account.
# "internal-tool" is the most dangerous because `core.middleware.require_admin`
# treats it as the in-process tool loopback. "api" collides with bearer-token
# attribution. "demo"/"system" are synthetic owners already special-cased by
# scheduler/assistant/research paths. The Default/Local owner is a storage
# bucket for explicit auth-disabled no-login mode, not a login username.
RESERVED_USERNAMES = frozenset(RESERVED_AUTH_USERNAMES)


def username_error(username: str | None) -> Optional[str]:
    """Why a new username is not acceptable, or None when it is.

    Admin routes carry the name as a URL path segment, where "/" never
    reaches the handler, so such an account could not be managed afterwards.
    """
    name = (username or "").strip()
    if not name:
        return "Username is required"
    if len(name) > USERNAME_MAX_LENGTH:
        return f"Username must be at most {USERNAME_MAX_LENGTH} characters"
    if "/" in name or "\\" in name or any(not c.isprintable() for c in name):
        return "Username must not contain slashes or control characters"
    if name in (".", ".."):
        return "Username is not allowed"
    return None

# Intra-process mutex that serialises all auth.json mutations within the same
# Python process.  fcntl.flock (used by _interprocess_auth_lock) only blocks
# *other* processes — two threads in the same process calling flock(LOCK_EX)
# on the same file both succeed immediately.  This lock closes that gap so the
# critical section is serialised across both threads and workers.
#
# RLock (reentrant) so a mutation method that acquires the inter-process lock
# can safely call another mutation method that also acquires it (e.g. setup()
# calling create_user()).
_auth_intraprocess_lock = threading.RLock()


def normalize_known_username(users: Dict[str, Any], username: str | None) -> Optional[str]:
    """Return a normalized username only when it exists in the auth user map."""
    key = str(username or "").strip().lower()
    if not key or key not in users:
        return None
    return key


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))


class SetAdminResult(enum.Enum):
    """Outcome of AuthManager.set_admin, so callers can map each case to a
    precise response instead of guessing from a bare bool."""
    OK = "ok"
    USER_NOT_FOUND = "user_not_found"
    NOT_AUTHORIZED = "not_authorized"   # requester is not an admin
    LAST_ADMIN = "last_admin"           # would remove the last remaining admin


# Session-lookup states for the shared token resolver (_authenticate_token).
# _ORPHAN means the token names a user missing from this worker's *current*
# auth snapshot — not trusted until the snapshot has been re-read.
_SESSION_VALID = "valid"
_SESSION_ORPHAN = "orphan"
_SESSION_EXPIRED = "expired"
_SESSION_MISSING = "missing"


class AuthManager:
    """Manages multi-user password + session-token auth system."""

    def __init__(self, auth_path: str = DEFAULT_AUTH_PATH):
        self.auth_path = auth_path
        self._sessions_path = os.path.join(os.path.dirname(auth_path), "sessions.json")
        self._config: Dict[str, Any] = {}
        self._sessions: Dict[str, Dict[str, Any]] = {}  # token -> {username, expiry}
        # Guards mutations of self._sessions and the on-disk sessions.json.
        # Validate/create/revoke run concurrently from the FastAPI threadpool.
        self._sessions_lock = threading.RLock()
        # Guards all mutations of self._config and the on-disk auth.json so
        # concurrent create/delete/rename/privilege operations don't interleave
        # and corrupt the user database.
        self._config_lock = threading.Lock()
        # Path for the inter-process file lock (fcntl.flock).  Shared across
        # all uvicorn workers so first-admin bootstrap and auth.json mutations
        # are serialised across processes, not just threads within one worker.
        self._ipc_lock_path = auth_path + ".lock"
        # mtime of sessions.json at last load — lets validate_token cheaply
        # detect sessions written by other uvicorn workers (see
        # _reload_sessions_if_changed).
        self._sessions_mtime_ns = -1
        # Tokens present in sessions.json at the last disk sync.  Used to
        # distinguish "revoked by another worker" (was on disk, now gone —
        # drop it) from "issued locally moments ago, racing its own save"
        # (never seen on disk — keep it).
        self._disk_tokens: set = set()
        # Tokens this worker revoked whose removal may not yet be visible
        # on disk.  A disk sync must never re-add these; pruned once the
        # on-disk file no longer contains them.
        self._revoked_tokens: set = set()
        # Tokens whose username this worker itself renamed since the last
        # disk sync (filled by rename_user, cleared by _save_sessions for
        # the tokens it writes).  A disk entry still carrying the old
        # username must not undo a local rename during a merge; every
        # other username mismatch yields to disk.
        self._renamed_since_sync: set = set()
        # Thread-local depth of _interprocess_auth_lock: _save records the
        # auth.json signature only while that lock is held, and clears it
        # otherwise (an unlocked write leaves the snapshot unconfirmed).
        self._ipc_auth_local = threading.local()
        # Signature (st_mtime_ns, st_ino, st_size) of the auth.json whose
        # content self._config mirrors, or None while the snapshot is
        # unconfirmed.  Recorded by _load/_save (so a worker's own writes do
        # not trigger a re-read) and consulted by _refresh_users_if_changed.
        self._auth_signature: Optional[tuple] = None
        self._load()
        self._load_sessions()
        self._migrate_single_user()
        self._drop_reserved_loaded_users()
        self._migrate_legacy_admin_role()

    def _load(self):
        self._auth_signature = None
        try:
            if os.path.exists(self.auth_path):
                # Contains password hashes — restrict pre-existing files
                # written before the 0600 policy.
                try:
                    os.chmod(self.auth_path, 0o600)
                except OSError:
                    pass
                with open(self.auth_path, "r", encoding="utf-8") as f:
                    self._config = json.load(f)
                    stat_result = os.fstat(f.fileno())
                # Normalize all stored usernames to lowercase so they match
                # the .strip().lower() applied at login/verify time. Fixes
                # "Invalid credentials" when auth.json was written with
                # mixed-case keys (e.g. via manual edit or a future migration).
                if "users" in self._config:
                    self._config["users"] = {
                        k.strip().lower(): v
                        for k, v in self._config["users"].items()
                    }
                self._auth_signature = (
                    stat_result.st_mtime_ns, stat_result.st_ino, stat_result.st_size
                )
                logger.info("Auth config loaded")
            else:
                self._config = {}
                logger.info("No auth config found — first-run setup required")
        except Exception as e:
            logger.error(f"Failed to load auth config: {e}")
            self._config = {}

    def _auth_file_signature(self) -> Optional[tuple]:
        """Signature (st_mtime_ns, st_ino, st_size) of auth.json, or None
        when it cannot be stat'ed.  An overwrite via atomic_write_json
        changes at least the inode, so the signature cannot go stale on a
        rewrite that lands inside one filesystem-timestamp tick."""
        try:
            stat_result = os.stat(self.auth_path)
        except OSError:
            return None
        return (stat_result.st_mtime_ns, stat_result.st_ino, stat_result.st_size)

    def _refresh_users_if_changed(self) -> bool:
        """Hot-reload self._config when another worker rewrote auth.json.

        Each uvicorn worker keeps its own in-memory user snapshot, and the
        mutation paths only reload under the inter-process auth lock.  The
        token validators therefore need the freshest snapshot they can get:
        without this, a session imported from sessions.json for an account
        created on another worker after this one started would fail the
        orphan check and be persisted as a revocation tombstone (review P1
        on PR #5752).

        Gated without a lock on the auth.json signature; steady-state cost
        is one os.stat.  On change: take _config_lock, open the file and
        fstat the open descriptor so the recorded signature matches the
        content actually parsed, and parse into a temporary — self._config
        is swapped only on success.  Keys are normalized exactly as _load
        does, and RESERVED_USERNAMES plus empty names are dropped in memory
        so a hand-edited auth.json cannot admit a request sentinel through
        this hot path (the drop is never written back to disk; the mutators
        own the file).

        On a read/parse failure or a missing file the last good snapshot is
        kept, the signature is left unrecorded (the next validation retries),
        and False is returned: the caller must then treat "user absent" as
        unconfirmed rather than proven.  Returns True only when the current
        auth.json is what self._config reflects.

        Caller must not hold _config_lock (non-reentrant) or _sessions_lock
        (lock order is _interprocess_auth_lock -> _config_lock ->
        _sessions_lock).
        """
        signature = self._auth_file_signature()
        if signature is not None and signature == self._auth_signature:
            return True
        if signature is None and not os.path.exists(self.auth_path):
            # auth.json is simply absent: a quiet unconfirmed state, not
            # an error to log per validation.  No lock taken; the last
            # good snapshot is kept and the caller fails closed.
            logger.debug("Auth config file missing — keeping last-good user snapshot")
            return False
        with self._config_lock:
            # Another thread on this worker may have refreshed already.
            signature = self._auth_file_signature()
            if signature is not None and signature == self._auth_signature:
                return True
            try:
                with open(self.auth_path, "r", encoding="utf-8") as f:
                    stat_result = os.fstat(f.fileno())
                    config = json.load(f)
            except Exception as e:
                logger.error(f"Failed to refresh auth config: {e}")
                return False
            if not isinstance(config, dict):
                logger.error("Failed to refresh auth config: not a JSON object")
                return False
            users = config.get("users")
            if users is not None and not isinstance(users, dict):
                logger.error("Failed to refresh auth config: 'users' is not an object")
                return False
            if users is not None:
                normalized = {}
                for key, data in users.items():
                    name = str(key or "").strip().lower()
                    if not name or name in RESERVED_USERNAMES:
                        continue
                    normalized[name] = data
                config["users"] = normalized
            self._config = config
            self._auth_signature = (
                stat_result.st_mtime_ns, stat_result.st_ino, stat_result.st_size
            )
            logger.info("Auth config refreshed from disk")
            return True

    def _load_sessions(self):
        """Load persisted session tokens from disk, pruning expired ones."""
        try:
            if os.path.exists(self._sessions_path):
                # Session tokens are bearer credentials — never leave the
                # file readable by other local users (same policy as
                # data/app.db, #4420).
                try:
                    os.chmod(self._sessions_path, 0o600)
                except OSError:
                    pass
                self._sessions_mtime_ns = os.stat(self._sessions_path).st_mtime_ns
                with open(self._sessions_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                now = time.time()
                self._sessions = {k: v for k, v in data.items() if v.get("expiry", 0) > now}
                self._disk_tokens = set(data)
                pruned = len(data) - len(self._sessions)
                if pruned > 0:
                    self._save_sessions()
                logger.info(f"Loaded {len(self._sessions)} session(s) from disk")
        except Exception as e:
            logger.error(f"Failed to load sessions: {e}")
            self._sessions = {}

    def _reload_sessions_if_changed(self):
        """Sync session state written by other uvicorn workers.

        The OIDC callback (or a password login/logout) may run on one
        worker while the browser's next request lands on another; each
        worker loads sessions.json only at startup, so cross-worker
        issuance and revocation would otherwise be invisible.  Called on
        every token validation: when the file's mtime has changed since
        the last sync, re-read it and

        - add unknown unexpired tokens (issued by another worker), and
        - drop in-memory tokens that were on disk at the last sync but
          are gone now (revoked by another worker).

        A token never yet seen on disk is kept — it was issued locally
        moments ago and may be racing its own _save_sessions.  The mtime
        gate keeps the steady-state cost at one os.stat per validation,
        not a JSON parse.
        """
        try:
            stat = os.stat(self._sessions_path)
        except OSError:
            return
        with self._sessions_lock:
            if stat.st_mtime_ns == self._sessions_mtime_ns:
                return
            self._read_and_apply_disk_sessions(stat.st_mtime_ns)

    def _force_reload_sessions(self):
        """Reload sessions.json bypassing the mtime gate.

        Used by the token validators' second chance (see
        _authenticate_token): a rewrite by another worker can land within
        the same filesystem-timestamp tick as this worker's last sync, in
        which case the mtime gate alone would miss it.  The gate is not
        advanced here: the next gated reload re-reads and reconciles, so a
        rewrite racing this parse cannot be swallowed.
        """
        with self._sessions_lock:
            self._read_and_apply_disk_sessions(None)

    def _read_and_apply_disk_sessions(self, gate_mtime_ns: Optional[int]):
        """Read sessions.json and merge it in.  Caller must hold
        ``_sessions_lock``.  ``gate_mtime_ns`` is the stat taken before
        reading, recorded on success, or None for an ungated (force) read
        that must leave the gate untouched.
        """
        try:
            with open(self._sessions_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            logger.error(f"Failed to reload sessions: {e}")
            return
        if not isinstance(data, dict):
            return
        if gate_mtime_ns is not None:
            self._sessions_mtime_ns = gate_mtime_ns
        self._apply_disk_sessions(data)

    def _apply_disk_sessions(self, data: Dict[str, Any]) -> None:
        """Merge parsed sessions.json content into memory.

        Caller must hold ``_sessions_lock``.  Adds unknown unexpired
        tokens (unless this worker revoked them and the removal hasn't
        reached disk yet), drops tokens revoked by other workers, and
        refreshes the disk-snapshot bookkeeping.

        A token held both in memory and on disk under *different*
        usernames takes the disk username — another worker (often the
        one that renamed the account) has already written the truth to
        disk.  The exception is a token in ``_renamed_since_sync``: this
        worker renamed it itself and has not written that rename out
        yet, so its newer in-memory name wins the merge.  The rule does
        not consult the user snapshot: a lagging snapshot can still hold
        the *old* name as a known user, which used to make a stale
        worker write the pre-rename username back and tombstone the
        token (review P1 r4347312512 finding 1).
        """
        now = time.time()
        for tok, sess in data.items():
            if (
                tok not in self._sessions
                and tok not in self._revoked_tokens
                and isinstance(sess, dict)
                and sess.get("expiry", 0) > now
            ):
                self._sessions[tok] = sess
                continue
            mem = self._sessions.get(tok)
            if isinstance(mem, dict) and isinstance(sess, dict):
                mem_user = str(mem.get("username") or "").strip().lower()
                disk_user = str(sess.get("username") or "").strip().lower()
                if (
                    mem_user != disk_user
                    and tok not in self._renamed_since_sync
                ):
                    self._sessions[tok] = sess
        revoked_elsewhere = [
            tok for tok in self._sessions
            if tok not in data and tok in self._disk_tokens
        ]
        for tok in revoked_elsewhere:
            self._sessions.pop(tok, None)
        self._disk_tokens = set(data)
        # A tombstone is only needed while the token is still on disk.
        self._revoked_tokens &= self._disk_tokens

    @contextmanager
    def _interprocess_sessions_lock(self):
        """Serialise sessions.json read-merge-write cycles across uvicorn
        workers.  Separate lock file from the auth.json IPC lock so a
        session save can never deadlock a caller already holding the auth
        lock (flock is not re-entrant across file descriptors)."""
        if not HAS_FCNTL:
            yield
            return
        fd = os.open(self._sessions_path + ".lock", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _save_sessions(self):
        """Persist session tokens to disk (atomic, merge-on-write).

        Merges the current on-disk state before writing, under an
        inter-process flock — a plain overwrite would clobber sessions
        issued by other workers since this worker's last sync (lost
        update).  Tombstones in ``_revoked_tokens`` keep just-revoked
        tokens from being re-merged and resurrected.
        """
        try:
            with self._interprocess_sessions_lock(), self._sessions_lock:
                try:
                    with open(self._sessions_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if isinstance(data, dict):
                        self._apply_disk_sessions(data)
                except OSError:
                    pass  # first save — no file yet
                except Exception as e:
                    logger.error(f"Failed to merge sessions before save: {e}")
                snapshot = dict(self._sessions)
                _atomic_write_json(self._sessions_path, snapshot, mode=0o600)
                self._disk_tokens = set(snapshot)
                self._revoked_tokens &= self._disk_tokens
                # Disk now reflects this worker's renames; release the
                # pending-rename guard for the tokens just written.
                self._renamed_since_sync -= set(snapshot)
        except Exception as e:
            logger.error(f"Failed to save sessions: {e}")

    def _migrate_single_user(self):
        """Migrate old single-user format to multi-user format."""
        if "password_hash" in self._config and "users" not in self._config:
            old_user = str(self._config.get("username", "admin") or "admin").strip().lower()
            if old_user in RESERVED_USERNAMES:
                logger.warning(
                    "Migrating legacy single-user reserved username '%s' to 'admin'",
                    old_user,
                )
                old_user = "admin"
            old_hash = self._config["password_hash"]
            with self._config_lock:
                self._config = {
                    "users": {
                        old_user: {
                            "password_hash": old_hash,
                            "created": time.time(),
                            "is_admin": True,
                        }
                    }
                }
                self._save()
            logger.info(f"Migrated single-user auth to multi-user (admin: {old_user})")

    def _drop_reserved_loaded_users(self):
        """Fail closed for legacy/manual auth rows that collide with sentinels."""
        users = self._config.get("users")
        if not isinstance(users, dict):
            return
        normalized = {}
        removed = []
        for username, data in users.items():
            key = str(username or "").strip().lower()
            if not key:
                continue
            if key in RESERVED_USERNAMES:
                removed.append(key)
                continue
            normalized[key] = data
        if removed or normalized != users:
            with self._config_lock:
                self._config["users"] = normalized
                self._save()
        if removed:
            logger.warning(
                "Removed reserved username(s) from auth config: %s",
                ", ".join(sorted(set(removed))),
            )

    def _migrate_legacy_admin_role(self):
        """Normalize setup.py's old role='admin' marker to is_admin=True."""
        changed = False
        for username, user in self.users.items():
            if user.get("role") == "admin" and "is_admin" not in user:
                user["is_admin"] = True
                changed = True
                logger.info(f"Migrated legacy admin role for '{username}'")
        if changed:
            self._save()

    def _save(self):
        # Password hashes — owner-only, same policy as sessions.json.
        _atomic_write_json(self.auth_path, self._config, indent=2, mode=0o600)
        # Record the signature of the file just replaced so this worker's
        # own write does not trigger a re-read on the next refresh — but
        # only while the caller holds the inter-process auth lock.  A
        # write from an unlocked path (e.g. reset_user_password holding
        # only _config_lock) can race a concurrent worker's locked write,
        # so its snapshot is never confirmed: clear the signature and the
        # next refresh re-reads auth.json.  Fail-closed, never fail-open.
        if getattr(self._ipc_auth_local, "depth", 0) > 0:
            self._auth_signature = self._auth_file_signature()
        else:
            self._auth_signature = None

    @property
    def users(self) -> Dict[str, Any]:
        return self._config.get("users", {})

    @property
    def signup_enabled(self) -> bool:
        return self._config.get("signup_enabled", False)

    @signup_enabled.setter
    def signup_enabled(self, value: bool):
        with self._interprocess_auth_lock(), self._config_lock:
            self._load()
            self._config["signup_enabled"] = value
            self._save()

    @property
    def is_configured(self) -> bool:
        return len(self.users) > 0

    def policy(self) -> dict:
        """Return public auth policy constants for the frontend."""
        return {
            "password_min_length": PASSWORD_MIN_LENGTH,
            "reserved_usernames": sorted(RESERVED_USERNAMES),
            "signup_enabled": self.signup_enabled,
            "session_days": TOKEN_TTL // 86400,
        }

    # ------------------------------------------------------------------
    # Account management
    # ------------------------------------------------------------------

    @contextmanager
    def _interprocess_auth_lock(self):
        """Acquire an exclusive lock on auth.json — serialised across both
        threads (intra-process) and workers/processes (inter-process).

        The module-level threading.Lock serialises threads within the same
        Python process.  fcntl.flock serialises across different processes
        (uvicorn workers).  The kernel releases flock automatically when
        the process exits, so a crash cannot leave a stale lock.

        On platforms without fcntl (native Windows), this degrades to
        intra-process-only serialisation.  OIDC defaults to off and
        single-worker deployments are the norm there, so the degraded
        mode is safe for most Windows use cases.
        """
        with _auth_intraprocess_lock:
            if not HAS_FCNTL:
                self._enter_auth_lock_depth()
                try:
                    yield
                finally:
                    self._exit_auth_lock_depth()
                return
            # Open in read-write mode; create the lock file if it doesn't exist.
            fd = os.open(self._ipc_lock_path, os.O_CREAT | os.O_RDWR, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                self._enter_auth_lock_depth()
                try:
                    yield
                finally:
                    self._exit_auth_lock_depth()
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)

    def _enter_auth_lock_depth(self) -> None:
        # Called only after both the intra-process and inter-process auth
        # locks are held by this thread.  _save uses the depth to decide
        # whether an auth.json write is a locked (confirmed) write.
        self._ipc_auth_local.depth = getattr(self._ipc_auth_local, "depth", 0) + 1

    def _exit_auth_lock_depth(self) -> None:
        self._ipc_auth_local.depth = getattr(self._ipc_auth_local, "depth", 0) - 1

    def setup(self, username: str, password: str) -> bool:
        """First-run admin setup. Only works if no users exist."""
        username = username.strip().lower()
        with self._interprocess_auth_lock(), self._config_lock:
            # Reload from disk so we see what another worker may have
            # written since our last _load().
            self._load()
            if self.is_configured:
                return False
            # _create_user_locked assumes the interprocess lock is already
            # held, avoiding a nested fcntl.flock deadlock (flock is not
            # reentrant across different file descriptors).
            return self._create_user_locked(username, password, is_admin=True)

    def create_user(self, username: str, password: str, is_admin: bool = False) -> bool:
        """Create a new user account.

        Serialised across workers via the shared inter-process lock so a
        concurrent OIDC admin sync cannot lose a newly-created user.
        """
        username = username.strip().lower()
        if not username:
            return False
        if username in RESERVED_USERNAMES:
            logger.warning("Refused to create reserved username '%s'", username)
            return False
        with self._interprocess_auth_lock(), self._config_lock:
            self._load()
            return self._create_user_locked(username, password, is_admin)

    def _create_user_locked(self, username: str, password: str, is_admin: bool) -> bool:
        """Internal helper — caller must hold _interprocess_auth_lock
        and _config_lock.  Does not reload (caller did that)."""
        username = username.strip().lower()
        if username in RESERVED_USERNAMES:
            logger.warning("Refused to create reserved username '%s'", username)
            return False
        if username in self._config.get("users", {}):
            return False
        if "users" not in self._config:
            self._config["users"] = {}
        self._config["users"][username] = {
            "password_hash": _hash_password(password),
            "created": time.time(),
            "is_admin": is_admin,
            "privileges": dict(ADMIN_PRIVILEGES if is_admin else DEFAULT_PRIVILEGES),
        }
        self._save()
        logger.info(f"Created user '{username}' (admin={is_admin})")
        return True

    def get_user_by_oidc(self, sub: str, issuer: str) -> Optional[str]:
        """Find a username by OIDC (sub, issuer) pair. Returns None if no match."""
        for username, data in self.users.items():
            if data.get("oidc_sub") == sub and data.get("oidc_issuer") == issuer:
                return username
        return None

    def create_user_oidc(self, username: str, sub: str, issuer: str, email: str = "",
                         is_admin: bool = False) -> Optional[str]:
        """Create a passwordless user linked to an OIDC identity.

        Returns the final username (may differ from *username* if a local
        password user already owns that name), or ``None`` when creation
        fails (e.g. all candidate usernames collide with different OIDC
        identities).

        OIDC users have no password hash — they can only authenticate
        through the OIDC flow. An existing OIDC user with the same
        (sub, issuer) is returned as-is (idempotent).

        When OIDC is the only auth path (no password admin exists) or
        OIDC_ADMIN_GROUPS is unset, the first OIDC user becomes admin
        by default to prevent zero-admin lockout.  Set
        OIDC_FIRST_USER_IS_ADMIN=false to disable this bootstrap.
        """
        username = username.strip().lower()
        name_error = username_error(username)
        if name_error:
            logger.warning(
                "Refused OIDC user with invalid username %r: %s", username, name_error,
            )
            return None
        if username in RESERVED_USERNAMES:
            logger.warning("Refused OIDC user with reserved username '%s'", username)
            return None

        with self._interprocess_auth_lock(), self._config_lock:
            # Reload from disk so we see what another process (or the
            # local-setup path) may have written since our last _load().
            self._load()

            if "users" not in self._config:
                self._config["users"] = {}
            users = self._config["users"]

            # Idempotent: same identity already exists (inside lock so
            # two concurrent callbacks for the same OIDC identity cannot
            # both observe an empty user map and create duplicate entries).
            for uname, data in users.items():
                if data.get("oidc_sub") == sub and data.get("oidc_issuer") == issuer:
                    return uname

            # Bootstrap: if no users exist yet, OIDC_ADMIN_GROUPS is
            # unset, and OIDC_FIRST_USER_IS_ADMIN isn't explicitly false,
            # make the first OIDC user an admin.  The check is inside the
            # inter-process + process-local locks so two workers (or a
            # concurrent local setup) cannot both observe an empty user
            # map and both persist as admin.
            if not is_admin:
                first_user_admin = os.getenv("OIDC_FIRST_USER_IS_ADMIN", "true").lower() != "false"
                oidc_admin_groups = os.getenv("OIDC_ADMIN_GROUPS", "").strip()
                if first_user_admin and not users and not oidc_admin_groups:
                    is_admin = True
                    logger.info(
                        "First OIDC user '%s' promoted to admin (bootstrap, "
                        "no OIDC_ADMIN_GROUPS configured). "
                        "Set OIDC_FIRST_USER_IS_ADMIN=false to opt out.",
                        username,
                    )

            # If the requested username is taken by a *different* identity
            # (another OIDC user or a local password user), find a free
            # slot by appending a numeric suffix.
            base = username
            candidate = username
            suffix = 1
            while candidate in users:
                suffix += 1
                # The suffix must also keep the name <= USERNAME_MAX_LENGTH,
                # else the account would fail username_error: truncate the
                # base so base + suffix always fits.
                base = username[: USERNAME_MAX_LENGTH - len(str(suffix))]
                candidate = f"{base}{suffix}"
                if suffix > 100:  # safety valve
                    logger.error("OIDC username collision loop for '%s'", username)
                    return None

            users[candidate] = {
                "password_hash": None,
                "created": time.time(),
                "is_admin": is_admin,
                "privileges": dict(ADMIN_PRIVILEGES if is_admin else DEFAULT_PRIVILEGES),
                "oidc_sub": sub,
                "oidc_issuer": issuer,
                "oidc_email": email,
            }
            self._save()

        logger.info(
            "Created OIDC user '%s' (sub=%s issuer=%s admin=%s)",
            candidate, sub, issuer, is_admin,
        )
        return candidate

    def is_oidc_user(self, username: str) -> bool:
        """Return True when *username* was created via OIDC (has no password)."""
        user = self.users.get(username.strip().lower(), {})
        return bool(user.get("oidc_sub"))

    def set_oidc_user_admin(self, username: str, is_admin: bool) -> bool:
        """Set (or clear) admin status for an OIDC user.

        Called on every OIDC login so admin follows the IdP's group
        membership.  Returns ``False`` if the user doesn't exist or is
        not an OIDC user (password-account admins must be managed manually).

        Serialised across workers via the shared inter-process lock so a
        stale in-memory snapshot cannot overwrite users concurrently
        created by another worker.
        """
        username = username.strip().lower()
        with self._interprocess_auth_lock(), self._config_lock:
            # Reload from disk so we see what another process may have
            # written since our last _load() — e.g. a concurrent
            # create_user_oidc() on a different worker.
            self._load()
            user = self._config.get("users", {}).get(username, {})
            if not user.get("oidc_sub"):
                return False  # not an OIDC user (or removed) — don't touch
            if user.get("is_admin") == is_admin:
                return True   # no change needed
            # Refuse to demote the only remaining administrator. Group
            # membership changes must not silently make the instance
            # unadministrable; password admins count as recovery admins.
            if user.get("is_admin") and not is_admin:
                admin_count = sum(
                    1 for data in self._config.get("users", {}).values()
                    if data.get("is_admin")
                )
                if admin_count <= 1:
                    logger.warning(
                        "Refusing to demote last admin '%s' during OIDC sync",
                        username,
                    )
                    return False
            # Token creation is admin-only and a demoted user can neither
            # list nor revoke their tokens, so purge them with the role
            # (dev #6832). Done before the flag flips: if the token store
            # fails the exception propagates and the user is still an admin.
            if user.get("is_admin") and not is_admin:
                from core.database import get_db_session, ApiToken
                with get_db_session() as db:
                    removed_tokens = db.query(ApiToken).filter(ApiToken.owner == username).delete()
                if removed_tokens:
                    logger.info(
                        f"Revoked {removed_tokens} API token(s) owned by demoted OIDC admin '{username}'"
                    )
            self._config["users"][username]["is_admin"] = is_admin
            if is_admin:
                self._config["users"][username]["privileges"] = dict(ADMIN_PRIVILEGES)
            else:
                self._config["users"][username]["privileges"] = dict(DEFAULT_PRIVILEGES)
            self._save()
        logger.info(
            "OIDC user '%s' admin=%s (synced from IdP group membership)",
            username, is_admin,
        )
        return True

    def check_oidc_totp(self, username: str) -> bool:
        """Return True when *username* has TOTP enabled AND is an OIDC user.

        Reloads auth.json from disk under the inter-process lock so a
        manually-edited or externally-mutated config is visible.
        Callers must invoke this via ``asyncio.to_thread()`` — file
        locking inside the critical section blocks the calling thread.

        This is defense-in-depth: normal OIDC users cannot enable local
        TOTP (route guards prevent it), but an externally-edited auth.json or
        a pre-OIDC legacy account could have both ``oidc_sub`` and
        ``totp_enabled`` set.
        """
        username = username.strip().lower()
        with self._interprocess_auth_lock(), self._config_lock:
            self._load()
            user = self._config.get("users", {}).get(username, {})
            if not user.get("oidc_sub"):
                return False  # not an OIDC user
            return bool(user.get("totp_enabled"))

    def delete_user(self, username: str, requesting_user: str) -> bool:
        """Delete a user. Only admins can delete, and can't delete themselves.

        SECURITY: also revoke every active session token belonging to this
        user so any open browser tab they have gets kicked back to /login
        on the next request. Without this the user kept full access until
        their cookie expired naturally (default ~30 days).
        """
        username = username.strip().lower()
        with self._interprocess_auth_lock(), self._config_lock:
            self._load()
            if username not in self.users:
                return False
            if username == requesting_user:
                return False
            if not self.users.get(requesting_user, {}).get("is_admin"):
                return False
            # Revoke API bearer tokens before removing the auth row. The bearer
            # path authenticates from ApiToken rows and does not require the
            # owner to still exist, so a successful delete must not leave active
            # rows behind. If the token store is unavailable, fail closed and
            # keep the user/session state intact so the admin can retry.
            try:
                from core.database import get_db_session, ApiToken
                with get_db_session() as db:
                    removed_tokens = db.query(ApiToken).filter(ApiToken.owner == username).delete()
                if removed_tokens:
                    logger.info(
                        f"Revoked {removed_tokens} API token(s) owned by deleted user '{username}'"
                    )
            except Exception:
                logger.warning(f"Failed to revoke API tokens for deleted user '{username}'")
                return False
            del self._config["users"][username]
            self._save()
        # Purge all sessions belonging to this user. validate_token doesn't
        # cross-check `self.users`, so without this step a deleted user's
        # cookie keeps authenticating.
        revoked = 0
        with self._sessions_lock:
            to_drop = [tok for tok, sess in self._sessions.items()
                       if (sess or {}).get("username") == username]
            for tok in to_drop:
                self._sessions.pop(tok, None)
                self._revoked_tokens.add(tok)
                revoked += 1
        if revoked:
            self._save_sessions()
        logger.info(f"Deleted user '{username}' (by {requesting_user}); revoked {revoked} active session(s)")
        return True

    def rename_user(self, old_username: str, new_username: str, requesting_user: str) -> bool:
        """Rename a user in auth config and active sessions. Admin only."""
        old_username = old_username.strip().lower()
        new_username = new_username.strip().lower()
        requesting_user = (requesting_user or "").strip().lower()
        if not old_username or not new_username:
            return False
        if new_username in RESERVED_USERNAMES:
            logger.warning("Refused to rename '%s' into reserved username '%s'", old_username, new_username)
            return False
        with self._interprocess_auth_lock(), self._config_lock:
            self._load()
            if old_username not in self.users:
                return False
            if new_username in self.users:
                return False
            if not self.users.get(requesting_user, {}).get("is_admin"):
                return False
            self._config.setdefault("users", {})[new_username] = self._config["users"].pop(old_username)
            self._save()

        renamed_sessions = 0
        # Pull sessions written by other workers in first (ungated, so a
        # rewrite inside one mtime tick is not missed): the rename must also
        # cover tokens this worker has not yet seen in memory.
        self._force_reload_sessions()
        with self._sessions_lock:
            for tok, sess in self._sessions.items():
                sess_user = str((sess or {}).get("username") or "").strip().lower()
                if sess_user == old_username:
                    sess["username"] = new_username
                    # Protect this worker's newer name from a stale disk
                    # entry (old username) until the rename is written.
                    self._renamed_since_sync.add(tok)
                    renamed_sessions += 1
        if renamed_sessions:
            self._save_sessions()
        logger.info(
            "Renamed user '%s' -> '%s' (by %s); updated %d active session(s)",
            old_username, new_username, requesting_user, renamed_sessions,
        )
        return True

    def is_admin(self, username: str) -> bool:
        return self.users.get(username, {}).get("is_admin", False)

    def list_users(self) -> List[Dict[str, Any]]:
        result = []
        for u, d in self.users.items():
            entry = {
                "username": u,
                "is_admin": d.get("is_admin", False),
                "privileges": self.get_privileges(u),
            }
            if d.get("oidc_sub"):
                entry["oidc"] = True
                entry["oidc_issuer"] = d.get("oidc_issuer", "")
                entry["oidc_email"] = d.get("oidc_email", "")
            result.append(entry)
        return result

    def get_privileges(self, username: str) -> Dict[str, Any]:
        """Get privileges for a user. Admins get all privileges."""
        user = self.users.get(username, {})
        if user.get("is_admin"):
            return dict(ADMIN_PRIVILEGES)
        # Merge stored privileges with defaults (in case new privileges were added)
        stored = user.get("privileges", {})
        return {**DEFAULT_PRIVILEGES, **stored}

    def set_privileges(self, username: str, privileges: Dict[str, Any]) -> bool:
        """Update privileges for a user. Can't modify admin privileges."""
        username = username.strip().lower()
        with self._interprocess_auth_lock(), self._config_lock:
            self._load()
            if username not in self.users:
                return False
            if self.users[username].get("is_admin"):
                return False  # admins always have full access
            # Only allow known privilege keys
            current = self.get_privileges(username)
            for k, v in privileges.items():
                if k in DEFAULT_PRIVILEGES:
                    default = DEFAULT_PRIVILEGES[k]
                    if isinstance(default, bool):
                        ok = isinstance(v, bool)
                    elif isinstance(default, int):
                        ok = isinstance(v, int) and not isinstance(v, bool)
                    else:
                        ok = isinstance(v, list)
                    if not ok:
                        raise ValueError(f"Invalid value for privilege '{k}'")
                    current[k] = v
            self._config["users"][username]["privileges"] = current
            self._save()
        logger.info(f"Updated privileges for '{username}': {current}")
        return True

    def set_admin(self, username: str, is_admin: bool,
                  requesting_user: str) -> SetAdminResult:
        """Promote/demote an existing user to/from admin. Admin only.

        Refuses to remove the last remaining admin so the instance can never
        be locked out of admin access; self-demotion is allowed as long as
        another admin remains. Admin status is re-checked live on every
        request, so unlike delete/rename no session or token revocation is
        needed — a demoted admin simply fails the next is_admin() gate.

        Promotion stashes the user's current privilege map and demotion
        restores it, so a temporary admin stint can't silently broaden a
        user's non-admin access; users without a stash (created as admin,
        or promoted before stashing existed) demote to DEFAULT_PRIVILEGES.

        Counting admins and flipping the flag happen in one critical section
        so two concurrent demotions can't race the admin count to zero.
        """
        username = (username or "").strip().lower()
        requesting_user = (requesting_user or "").strip().lower()
        is_admin = bool(is_admin)
        with self._interprocess_auth_lock(), self._config_lock:
            self._load()
            target = self._config.get("users", {}).get(username)
            if target is None:
                return SetAdminResult.USER_NOT_FOUND
            if not self.users.get(requesting_user, {}).get("is_admin"):
                return SetAdminResult.NOT_AUTHORIZED
            currently_admin = bool(target.get("is_admin"))
            if currently_admin == is_admin:
                return SetAdminResult.OK  # no-op; leave privileges untouched
            if currently_admin and not is_admin:
                admin_count = sum(1 for d in self.users.values() if d.get("is_admin"))
                if admin_count <= 1:
                    return SetAdminResult.LAST_ADMIN
                # Token creation is admin-only and a demoted user can neither
                # list nor revoke their tokens, so purge them with the role.
                # Done before the flag flips: if the token store fails the
                # exception propagates and the user is still an admin.
                from core.database import get_db_session, ApiToken
                with get_db_session() as db:
                    removed_tokens = db.query(ApiToken).filter(ApiToken.owner == username).delete()
                if removed_tokens:
                    logger.info(
                        f"Revoked {removed_tokens} API token(s) owned by demoted admin '{username}'"
                    )
            # Write order matters for lock-free readers: get_privileges()
            # reads without _config_lock and trusts is_admin, so the admin
            # flag must be flipped while the stored map is safe to expose —
            # before writing admin privileges on promote, after restoring
            # the pre-admin map on demote.
            if is_admin:
                target["is_admin"] = True
                # Stash the pre-admin map so a later demotion can restore it.
                # While is_admin is set the stored map is inert: get_privileges
                # short-circuits to ADMIN_PRIVILEGES and set_privileges refuses
                # admins, so only set_admin ever touches the stash.
                target["privileges_before_admin"] = dict(
                    target.get("privileges") or DEFAULT_PRIVILEGES
                )
                target["privileges"] = dict(ADMIN_PRIVILEGES)
            else:
                # Restore the stashed pre-admin map. Fall back to defaults for
                # users created as admins (their stored map is ADMIN_PRIVILEGES,
                # which must not leak past demotion — e.g. can_use_bash) and
                # for admins promoted before the stash existed.
                target["privileges"] = dict(
                    target.pop("privileges_before_admin", None)
                    or DEFAULT_PRIVILEGES
                )
                target["is_admin"] = False
            self._save()
        logger.info("Set is_admin=%s for '%s' (by '%s')", is_admin, username, requesting_user)
        return SetAdminResult.OK

    def reset_user_password(self, username: str, new_password: str, requesting_user: str) -> bool:
        """Allow an admin to reset a non-admin account and revoke its sessions."""
        username = username.strip().lower()
        with self._config_lock:
            target = self.users.get(username)
            if not self.is_admin(requesting_user) or not target or target.get("is_admin"):
                return False
            if target.get("oidc_sub"):
                # OIDC account: writing a hash would open a password login
                # that bypasses the IdP. Credentials must go through the IdP.
                return False
            self._config["users"][username]["password_hash"] = _hash_password(new_password)
            self._save()
            self.revoke_user_sessions(username)
        logger.info("Password reset for '%s' by '%s'", username, requesting_user)
        return True

    def change_password(self, username: str, current_password: str, new_password: str) -> bool:
        username = username.strip().lower()
        with self._interprocess_auth_lock(), self._config_lock:
            self._load()
            if username not in self.users:
                return False
            pw_hash = self.users[username].get("password_hash")
            if pw_hash is None:
                return False  # OIDC-only user — password changes must go through the IdP
            if not _verify_password(current_password, pw_hash):
                return False
            self._config["users"][username]["password_hash"] = _hash_password(new_password)
            self._save()
        return True

    # ------------------------------------------------------------------
    # TOTP two-factor authentication
    # ------------------------------------------------------------------

    def totp_enabled(self, username: str) -> bool:
        """Check if 2FA is enabled for a user."""
        user = self.users.get(username.strip().lower(), {})
        return bool(user.get("totp_enabled"))

    def totp_generate_secret(self, username: str) -> Optional[str]:
        """Generate a new TOTP secret for a user. Returns the secret (not yet enabled)."""
        username = username.strip().lower()
        secret = pyotp.random_base32()
        with self._interprocess_auth_lock(), self._config_lock:
            self._load()
            if username not in self.users:
                return None
            self._config["users"][username]["totp_secret_pending"] = secret
            self._save()
        return secret

    def totp_get_provisioning_uri(self, username: str, secret: str) -> str:
        """Get the otpauth:// URI for QR code generation."""
        totp = pyotp.TOTP(secret)
        return totp.provisioning_uri(name=username, issuer_name="Odysseus")

    def totp_confirm_enable(self, username: str, code: str) -> bool:
        """Verify a TOTP code against the pending secret, then enable 2FA."""
        username = username.strip().lower()
        with self._interprocess_auth_lock(), self._config_lock:
            self._load()
            user = self._config.get("users", {}).get(username, {})
            secret = user.get("totp_secret_pending")
            if not secret:
                return False
            totp = pyotp.TOTP(secret)
            if not totp.verify(code, valid_window=1):
                return False
            # Enable 2FA
            self._config["users"][username]["totp_secret"] = secret
            self._config["users"][username]["totp_enabled"] = True
            self._config["users"][username].pop("totp_secret_pending", None)
            # Generate backup codes
            backup = [secrets.token_hex(4) for _ in range(8)]
            self._config["users"][username]["totp_backup_codes"] = backup
            self._save()
        logger.info(f"2FA enabled for '{username}'")
        return True

    def totp_verify(self, username: str, code: str) -> bool:
        """Verify a TOTP code for login."""
        username = username.strip().lower()
        user = self.users.get(username, {})
        if not user.get("totp_enabled"):
            return True  # 2FA not enabled, always pass
        secret = user.get("totp_secret")
        if not secret:
            # 2FA is enabled but no secret is stored (corrupt/partially-written
            # auth.json). Fail closed — returning True here bypassed the second
            # factor entirely.
            return False
        # Check backup codes first
        backup = user.get("totp_backup_codes", [])
        if code in backup:
            with self._interprocess_auth_lock(), self._config_lock:
                self._load()
                latest_backup = self._config.get("users", {}).get(username, {}).get("totp_backup_codes", [])
                if code in latest_backup:
                    latest_backup.remove(code)
                    self._config["users"][username]["totp_backup_codes"] = latest_backup
                    self._save()
                    logger.info(f"Backup code used for '{username}' ({len(latest_backup)} remaining)")
                    return True
                return False
        totp = pyotp.TOTP(secret)
        return totp.verify(code, valid_window=1)

    def totp_disable(self, username: str, password: str) -> bool:
        """Disable 2FA for a user. Requires password confirmation."""
        username = username.strip().lower()
        if not self.verify_password(username, password):
            return False
        with self._interprocess_auth_lock(), self._config_lock:
            self._load()
            if username not in self.users:
                return False
            self._config["users"][username].pop("totp_secret", None)
            self._config["users"][username].pop("totp_secret_pending", None)
            self._config["users"][username].pop("totp_backup_codes", None)
            self._config["users"][username]["totp_enabled"] = False
            self._save()
        logger.info(f"2FA disabled for '{username}'")
        return True

    # ------------------------------------------------------------------
    # Login / logout / session tokens
    # ------------------------------------------------------------------

    def verify_password(self, username: str, password: str) -> bool:
        username = username.strip().lower()
        if username not in self.users:
            return False
        pw_hash = self.users[username].get("password_hash")
        if pw_hash is None:
            return False  # OIDC-only user — no password set
        return _verify_password(password, pw_hash)

    def create_session(self, username: str, password: str) -> Optional[str]:
        """Verify credentials and return a session token, or None."""
        username = username.strip().lower()
        if not self.verify_password(username, password):
            return None
        return self.create_session_trusted(username)

    def create_session_trusted(self, username: str) -> Optional[str]:
        """Issue a session token for an already-verified user.
        Call only after verify_password (and TOTP if enabled) have passed."""
        username = username.strip().lower()
        token = secrets.token_hex(32)
        with self._config_lock:
            if username not in self.users:
                logger.warning("Refused to issue session for missing user '%s'", username)
                return None
            with self._sessions_lock:
                self._sessions[token] = {
                    "username": username,
                    "expiry": time.time() + TOKEN_TTL,
                }
        self._save_sessions()
        return token

    def validate_token(self, token: Optional[str]) -> bool:
        if not token:
            return False
        # Sync the user snapshot from other workers (signature-gated)
        # before the orphan check below can be trusted; see
        # _refresh_users_if_changed.
        self._refresh_users_if_changed()
        return self._authenticate_token(token) is not None

    def get_username_for_token(self, token: Optional[str]) -> Optional[str]:
        """Return the username associated with a valid token."""
        if not token:
            return None
        # Same snapshot refresh as validate_token — the two are called
        # back-to-back on every authenticated request (app.py middleware).
        self._refresh_users_if_changed()
        return self._authenticate_token(token)

    def _authenticate_token(self, token: str) -> Optional[str]:
        """Shared body of validate_token / get_username_for_token: reload
        sessions (mtime-gated), then look the token up.

        If the token's username is unknown to this worker, do not trust the
        verdict: an account created on another worker after this one booted
        (the review P1 on PR #5752 — worker B imported A's cookie from
        sessions.json while still holding a pre-creation auth.json snapshot)
        would otherwise be revoked as a deleted user, and the tombstone would
        make worker A reject its own session too.  Leave _sessions_lock,
        refresh the users snapshot, force an ungated session reload, and look
        up again.  A revocation (tombstone + save) is persisted only for a
        user confirmed absent from a successfully-read auth.json; when the
        snapshot is unconfirmed the request simply fails without revoking.
        Expiry behaviour is identical on every path.

        Caller must not hold _config_lock or _sessions_lock.
        """
        self._reload_sessions_if_changed()
        state, username = self._lookup_session(token)
        if state == _SESSION_VALID:
            return username
        if state == _SESSION_EXPIRED:
            self._save_sessions()
            return None
        if state == _SESSION_MISSING:
            return None
        # _SESSION_ORPHAN: re-read both files and look the token up again.
        snapshot_confirmed = self._refresh_users_if_changed()
        self._force_reload_sessions()
        state, username = self._lookup_session(token)
        if state == _SESSION_VALID:
            return username
        if state == _SESSION_EXPIRED:
            self._save_sessions()
            return None
        if state == _SESSION_ORPHAN and snapshot_confirmed:
            # SECURITY: the user record has since been removed (admin
            # deleted them while their cookie was still valid) — drop the
            # session so the next request kicks them out instead of
            # silently authenticating against a non-existent account.
            with self._sessions_lock:
                self._sessions.pop(token, None)
                self._revoked_tokens.add(token)
            self._save_sessions()
        return None

    def _lookup_session(self, token: str) -> tuple:
        """Classify one token against this worker's session map.

        Returns a ``(state, username)`` pair; see the _SESSION_* constants.
        Expired entries are dropped from memory (the caller must persist
        the change).  _ORPHAN means only that the username is missing from
        this worker's *current* auth snapshot — a stale snapshot must not
        be treated as proof of deletion.
        """
        with self._sessions_lock:
            session = self._sessions.get(token)
            if session is None:
                return _SESSION_MISSING, None
            if time.time() > session["expiry"]:
                self._sessions.pop(token, None)
                return _SESSION_EXPIRED, None
            username = session.get("username")
            if username not in self.users:
                return _SESSION_ORPHAN, None
            return _SESSION_VALID, username

    def revoke_token(self, token: str):
        with self._sessions_lock:
            self._sessions.pop(token, None)
            self._revoked_tokens.add(token)
        self._save_sessions()

    def revoke_user_sessions(self, username: str, except_token: Optional[str] = None) -> int:
        """Revoke active browser sessions for a user, optionally preserving one."""
        username = username.strip().lower()
        revoked = 0
        with self._sessions_lock:
            to_drop = [
                token for token, session in self._sessions.items()
                if token != except_token and (session or {}).get("username") == username
            ]
            for token in to_drop:
                self._sessions.pop(token, None)
                self._revoked_tokens.add(token)
                revoked += 1
        # Save outside _sessions_lock: _save_sessions acquires the
        # inter-process flock before _sessions_lock, and taking them in
        # the opposite order here could deadlock two threads.
        if revoked:
            self._save_sessions()
        return revoked

    def status(self, token: Optional[str]) -> Dict[str, Any]:
        username = self.get_username_for_token(token)
        authenticated = username is not None
        result = {
            "configured": self.is_configured,
            "authenticated": authenticated,
            "username": username,
            "is_admin": self.is_admin(username) if username else False,
        }
        if authenticated:
            result["privileges"] = self.get_privileges(username)
        return result
