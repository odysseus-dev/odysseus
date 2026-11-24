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
import contextlib
from pathlib import Path
from typing import Optional, Dict, Any, List, Callable

_TOKEN_CACHE_INVALIDATOR: Optional[Callable[[], None]] = None


def register_token_cache_invalidator(fn: Callable[[], None]) -> None:
    global _TOKEN_CACHE_INVALIDATOR
    _TOKEN_CACHE_INVALIDATOR = fn


def invalidate_token_cache() -> None:
    if _TOKEN_CACHE_INVALIDATOR is not None:
        try:
            _TOKEN_CACHE_INVALIDATOR()
        except Exception:
            pass
    try:
        from core.paths import DATA_DIR
        dirty_path = os.path.join(DATA_DIR, "tokens.dirty")
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(dirty_path, "a+b") as f:
            f.write(b"1\n")
    except Exception:
        pass


def delete_api_tokens_for_user(db, username: str) -> int:
    """Purge all API tokens owned by `username` using case- and whitespace-insensitive matching."""
    norm_name = (username or "").strip().lower()
    if not norm_name:
        return 0
    from core.database import ApiToken
    from sqlalchemy import func, or_
    if hasattr(getattr(ApiToken, "owner", None), "__clause_element__"):
        count = db.query(ApiToken).filter(
            or_(
                ApiToken.owner == norm_name,
                func.lower(func.trim(ApiToken.owner)) == norm_name,
            )
        ).delete(synchronize_session=False)
    else:
        # Mocked or simple attribute in unit tests
        count = db.query(ApiToken).filter(ApiToken.owner == norm_name).delete(synchronize_session=False)
    invalidate_token_cache()
    return count

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
    # Share a note with another account. Listed so require_privilege, which
    # treats unknown keys as allowed, has something an admin can switch off.
    "can_share_notes": True,
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


_DEFAULT_AUTH_MANAGER: Optional["AuthManager"] = None
_REAL_AUTH_MANAGER = None


def get_auth_manager(auth_path: str = DEFAULT_AUTH_PATH) -> "AuthManager":
    global _DEFAULT_AUTH_MANAGER
    if _DEFAULT_AUTH_MANAGER is None:
        _DEFAULT_AUTH_MANAGER = AuthManager(auth_path)
    return _DEFAULT_AUTH_MANAGER


class _UserLifecycleLock:
    """Read-write lock for coordinating account deletion and in-flight mutations for a specific user."""
    def __init__(self):
        self._lock = threading.Lock()
        self._readers_ok = threading.Condition(self._lock)
        self._writers_ok = threading.Condition(self._lock)
        self._readers = 0
        self._writers = 0
        self._writer_waiting = 0

    @contextlib.contextmanager
    def read_lock(self):
        with self._lock:
            while self._writers > 0 or self._writer_waiting > 0:
                self._readers_ok.wait()
            self._readers += 1
        try:
            yield
        finally:
            with self._lock:
                self._readers -= 1
                if self._readers == 0:
                    self._writers_ok.notify()

    @contextlib.contextmanager
    def write_lock(self):
        with self._lock:
            self._writer_waiting += 1
            while self._readers > 0 or self._writers > 0:
                self._writers_ok.wait()
            self._writer_waiting -= 1
            self._writers = 1
        try:
            yield
        finally:
            with self._lock:
                self._writers = 0
                if self._writer_waiting > 0:
                    self._writers_ok.notify()
                else:
                    self._readers_ok.notify_all()


class AuthManager:
    """Manages multi-user password + session-token auth system."""

    def __init__(self, auth_path: str = DEFAULT_AUTH_PATH):
        global _DEFAULT_AUTH_MANAGER
        _DEFAULT_AUTH_MANAGER = self
        self.auth_path = auth_path
        self._last_loaded_mtime: float = 0.0
        self._last_loaded_size: int = 0
        self._sessions_path = os.path.join(os.path.dirname(auth_path), "sessions.json")
        self._config: Dict[str, Any] = {}
        self._sessions: Dict[str, Dict[str, Any]] = {}  # token -> {username, expiry}
        # Guards mutations of self._sessions and the on-disk sessions.json.
        # Validate/create/revoke run concurrently from the FastAPI threadpool.
        self._sessions_lock = threading.RLock()
        # Guards all mutations of self._config and the on-disk auth.json so
        # concurrent create/delete/rename/privilege operations don't interleave
        # and corrupt the user database.
        self._config_lock = threading.RLock()
        # Guards the first-run setup check-and-write so concurrent requests
        # cannot both observe is_configured==False and both create admin accounts.
        self._setup_lock = threading.Lock()
        self._cross_process_lock_depth = 0
        self._lifecycle_locks: Dict[str, _UserLifecycleLock] = {}
        self._lifecycle_meta_lock = threading.Lock()
        self._load()
        self._load_sessions()
        self._migrate_single_user()
        self._drop_reserved_loaded_users()
        self._migrate_legacy_admin_role()

    def _get_user_lifecycle_lock(self, username: str) -> _UserLifecycleLock:
        norm = (username or "").strip().lower()
        with self._lifecycle_meta_lock:
            if norm not in self._lifecycle_locks:
                self._lifecycle_locks[norm] = _UserLifecycleLock()
            return self._lifecycle_locks[norm]

    @contextlib.contextmanager
    def account_deletion_guard(self, username: str):
        """Acquire exclusive write lock for username, blocking concurrent mutation commits."""
        norm = (username or "").strip().lower()
        if not norm:
            yield
            return
        lock = self._get_user_lifecycle_lock(norm)
        with lock.write_lock():
            yield

    @contextlib.contextmanager
    def account_mutation_guard(self, actor: Optional[str] = None, owner: Optional[str] = None, db=None):
        """Acquire shared read lock for actor and owner, validating active status before yielding to commit."""
        users_to_lock = sorted(set(filter(None, [
            (actor or "").strip().lower(),
            (owner or "").strip().lower(),
        ])))

        @contextlib.contextmanager
        def _acquire_all(users):
            if not users:
                yield
                return
            u = users[0]
            lock = self._get_user_lifecycle_lock(u)
            with lock.read_lock():
                with _acquire_all(users[1:]):
                    yield

        with _acquire_all(users_to_lock):
            if not self._check_active_fail_closed(actor, owner, db=db):
                return
            yield

    def _check_active_fail_closed(self, actor: Optional[str], owner: Optional[str], db=None) -> bool:
        norm_actor = (actor or "").strip().lower()
        norm_owner = (owner or "").strip().lower()
        try:
            if not getattr(self, "is_configured", False):
                return True
            if norm_actor and not self.is_user_active(norm_actor):
                if db:
                    db.rollback()
                from core.note_writes import NotePermissionRevoked
                raise NotePermissionRevoked(f"Account '{norm_actor}' is inactive or pending deletion", status_code=403)
            if norm_owner and not self.is_user_active(norm_owner):
                if db:
                    db.rollback()
                from core.note_writes import NotePermissionRevoked
                raise NotePermissionRevoked(f"Account '{norm_owner}' is inactive or pending deletion", status_code=403)
            return True
        except Exception as exc:
            from core.note_writes import NotePermissionRevoked
            if isinstance(exc, NotePermissionRevoked):
                raise
            if db:
                try:
                    db.rollback()
                except Exception:
                    pass
            logger.error("Authentication storage failure during lifecycle verification: %s", exc)
            raise NotePermissionRevoked("Authentication service unavailable; operation aborted", status_code=503) from exc

    def _ensure_fresh_config(self):
        """Reload auth config from disk if modified by another process. Fails closed on error."""
        try:
            try:
                stat = os.stat(self.auth_path)
            except FileNotFoundError:
                return
            mtime = stat.st_mtime
            size = stat.st_size
            if getattr(self, "_last_loaded_mtime", 0.0) != mtime or getattr(self, "_last_loaded_size", 0) != size:
                self._load()
        except Exception as e:
            logger.error("Failed to verify or refresh authentication configuration: %s", e)
            raise RuntimeError(f"Unable to verify fresh authentication configuration: {e}") from e

    @contextlib.contextmanager
    def _cross_process_lock(self):
        """Acquire in-process lock and cross-process file lock, ensuring fresh config from disk. Fails closed."""
        with self._config_lock:
            if self._cross_process_lock_depth > 0:
                self._cross_process_lock_depth += 1
                try:
                    yield
                finally:
                    self._cross_process_lock_depth -= 1
                return

            lock_path = os.path.abspath(str(self.auth_path)) + ".lock"
            try:
                os.makedirs(os.path.dirname(lock_path) or ".", exist_ok=True)
                lock_handle = open(lock_path, "a+b")
                if os.name != "nt":
                    import fcntl
                    fcntl.flock(lock_handle, fcntl.LOCK_EX)
            except Exception as e:
                logger.error("Failed to acquire authentication process lock (%s): %s", lock_path, e)
                raise RuntimeError(f"Could not acquire authentication process lock: {e}") from e

            self._cross_process_lock_depth = 1
            try:
                self._ensure_fresh_config()
                yield
            finally:
                self._cross_process_lock_depth = 0
                try:
                    if os.name != "nt":
                        import fcntl
                        fcntl.flock(lock_handle, fcntl.LOCK_UN)
                finally:
                    lock_handle.close()

    def _load(self):
        try:
            if os.path.exists(self.auth_path):
                try:
                    stat = os.stat(self.auth_path)
                    self._last_loaded_mtime = stat.st_mtime
                    self._last_loaded_size = stat.st_size
                except Exception as e:
                    raise RuntimeError(f"Failed to inspect auth file status: {e}") from e
                with open(self.auth_path, "r", encoding="utf-8") as f:
                    self._config = json.load(f)
                # Normalize all stored usernames to lowercase so they match
                # the .strip().lower() applied at login/verify time. Fixes
                # "Invalid credentials" when auth.json was written with
                # mixed-case keys (e.g. via manual edit or a future migration).
                if "users" in self._config:
                    self._config["users"] = {
                        k.strip().lower(): v
                        for k, v in self._config["users"].items()
                    }
                logger.info("Auth config loaded")
            else:
                self._config = {}
                self._last_loaded_mtime = 0.0
                self._last_loaded_size = 0
                logger.info("No auth config found — first-run setup required")
        except Exception as e:
            logger.error(f"Failed to load auth config: {e}")
            self._config = {}

    def _load_sessions(self):
        """Load persisted session tokens from disk, pruning expired ones."""
        try:
            if os.path.exists(self._sessions_path):
                with open(self._sessions_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                now = time.time()
                self._sessions = {k: v for k, v in data.items() if v.get("expiry", 0) > now}
                pruned = len(data) - len(self._sessions)
                if pruned > 0:
                    self._save_sessions()
                logger.info(f"Loaded {len(self._sessions)} session(s) from disk")
        except Exception as e:
            logger.error(f"Failed to load sessions: {e}")
            self._sessions = {}

    def _save_sessions(self):
        """Persist session tokens to disk (atomic, lock-guarded)."""
        try:
            with self._sessions_lock:
                snapshot = dict(self._sessions)
            _atomic_write_json(self._sessions_path, snapshot)
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
        _atomic_write_json(self.auth_path, self._config, indent=2)
        try:
            if os.path.exists(self.auth_path):
                stat = os.stat(self.auth_path)
                self._last_loaded_mtime = stat.st_mtime
                self._last_loaded_size = stat.st_size
        except Exception:
            pass

    @property
    def users(self) -> Dict[str, Any]:
        self._ensure_fresh_config()
        return self._config.get("users", {})

    @property
    def signup_enabled(self) -> bool:
        self._ensure_fresh_config()
        return self._config.get("signup_enabled", False)

    @signup_enabled.setter
    def signup_enabled(self, value: bool):
        with self._cross_process_lock():
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

    @property
    def deleting_users(self) -> Dict[str, Any]:
        """Return a copy of the accounts currently undergoing deletion."""
        with self._config_lock:
            self._ensure_fresh_config()
            return dict(self._config.get("deleting", {}))

    def is_user_active(self, username: str | None) -> bool:
        """True if username exists in users and is not undergoing deletion."""
        norm = (username or "").strip().lower()
        if not norm:
            return True
        with self._config_lock:
            self._ensure_fresh_config()
            return norm in self.users and norm not in self._config.get("deleting", {})

    def is_username_available(self, username: str | None) -> bool:
        """True if username is not taken by an active user or pending deletion."""
        norm = (username or "").strip().lower()
        if not norm:
            return False
        with self._config_lock:
            self._ensure_fresh_config()
            return norm not in self.users and norm not in self._config.get("deleting", {})

    # ------------------------------------------------------------------
    # Account management
    # ------------------------------------------------------------------

    def setup(self, username: str, password: str) -> bool:
        """First-run admin setup. Only works if no users exist."""
        with self._setup_lock, self._cross_process_lock():
            if self.is_configured:
                return False
            return self.create_user(username, password, is_admin=True, clean_orphaned=True)

    def create_user(
        self,
        username: str,
        password: str,
        is_admin: bool = False,
        clean_orphaned: bool = False,
    ) -> bool:
        """Create a new user account."""
        username = username.strip().lower()
        if not username:
            return False
        if username in RESERVED_USERNAMES:
            logger.warning("Refused to create reserved username '%s'", username)
            return False
        with self._cross_process_lock():
            if username in self.users or username in self._config.get("deleting", {}):
                # Active or pending-deletion account: duplicate creation attempt never touches or deletes active data
                return False

            if clean_orphaned:
                try:
                    from core.database import get_db_session
                    with get_db_session() as db:
                        delete_api_tokens_for_user(db, username)
                    invalidate_token_cache()
                except Exception as exc:
                    logger.error("Failed to clean up orphaned tokens for '%s'; creation aborted: %s", username, exc)
                    return False

                try:
                    import sys
                    cdb_mod = sys.modules.get("core.database")
                    if cdb_mod is not None and hasattr(cdb_mod, "Note"):
                        from core.notes_sharing import purge_notes_owned_by
                        purge_notes_owned_by(username)
                except Exception as exc:
                    logger.error("Failed to clean up orphaned notes for '%s'; creation aborted: %s", username, exc)
                    return False

            if "users" not in self._config:
                self._config["users"] = {}
            self._config["users"][username] = {
                "password_hash": _hash_password(password),
                "created": time.time(),
                "is_admin": is_admin,
                "privileges": dict(ADMIN_PRIVILEGES if is_admin else DEFAULT_PRIVILEGES),
            }
            try:
                self._save()
            except Exception:
                self._config["users"].pop(username, None)
                raise
        logger.info(f"Created user '{username}' (admin={is_admin})")
        return True

    def can_delete_user(self, username: str, requesting_user: str) -> bool:
        """Whether `delete_user(username, requesting_user)` would be allowed.

        The same checks `delete_user` makes before touching anything, so a
        caller can run work that must happen first (removing the account's
        notes) only for a deletion that is going ahead.
        """
        username = (username or "").strip().lower()
        with self._config_lock:
            return (
                username in self.users
                and username != requesting_user
                and bool(self.users.get(requesting_user, {}).get("is_admin"))
            )

    def delete_user(self, username: str, requesting_user: str, db=None) -> bool:
        """Delete a user. Only admins can delete, and can't delete themselves.

        SECURITY: also revoke every active session token belonging to this
        user so any open browser tab they have gets kicked back to /login
        on the next request. Without this the user kept full access until
        their cookie expired naturally (default ~30 days).
        """
        username = username.strip().lower()
        with self._cross_process_lock():
            if username not in self.users:
                return False
            if username == requesting_user:
                return False
            if not self.users.get(requesting_user, {}).get("is_admin"):
                return False
            # Revoke API bearer tokens before moving the auth row. The bearer
            # path authenticates from ApiToken rows and does not require the
            # owner to still exist, so a successful delete must not leave active
            # rows behind. If the token store is unavailable, fail closed and
            # keep the user/session state intact so the admin can retry.
            try:
                if db is not None:
                    removed_tokens = delete_api_tokens_for_user(db, username)
                else:
                    from core.database import get_db_session
                    with get_db_session() as own_db:
                        removed_tokens = delete_api_tokens_for_user(own_db, username)
                if removed_tokens:
                    logger.info(
                        f"Revoked {removed_tokens} API token(s) owned by deleted user '{username}'"
                    )
                invalidate_token_cache()
            except Exception:
                logger.warning(f"Failed to revoke API tokens for deleted user '{username}'")
                return False

            user_data = self._config.get("users", {}).pop(username, None)
            if user_data is None:
                user_data = self._config.get("deleting", {}).get(username, {})
            if "deleting" not in self._config:
                self._config["deleting"] = {}
            self._config["deleting"][username] = user_data

            try:
                self._save()
            except Exception:
                if username in self._config.get("deleting", {}):
                    self._config["deleting"].pop(username, None)
                if user_data:
                    self._config.setdefault("users", {})[username] = user_data
                raise
        # Purge all sessions belonging to this user. validate_token doesn't
        # cross-check `self.users`, so without this step a deleted user's
        # cookie keeps authenticating.
        revoked = 0
        with self._sessions_lock:
            to_drop = [tok for tok, sess in self._sessions.items()
                       if (sess or {}).get("username") == username]
            for tok in to_drop:
                self._sessions.pop(tok, None)
                revoked += 1
        if revoked:
            self._save_sessions()
        logger.info(f"Marked user '{username}' for deletion (by {requesting_user}); revoked {revoked} active session(s)")
        return True

    def finish_delete_user(self, username: str) -> bool:
        """Mark account deletion completely finished and free the username."""
        username = username.strip().lower()
        with self._cross_process_lock():
            deleting = self._config.get("deleting")
            if not isinstance(deleting, dict) or username not in deleting:
                return True
            backup_entry = deleting[username]
            deleting.pop(username, None)
            try:
                self._save()
                return True
            except Exception as e:
                deleting[username] = backup_entry
                logger.error(f"Failed to save auth config after finishing deletion for '{username}': {e}")
                raise

    def restore_user(self, username: str, user_data: Optional[dict] = None) -> bool:
        """Restore a user record after a downstream failure during deletion."""
        username = username.strip().lower()
        with self._cross_process_lock():
            if "users" not in self._config:
                self._config["users"] = {}
            if username in self._config["users"]:
                # Already active: never overwrite!
                return True
            if not user_data and "deleting" in self._config:
                user_data = self._config["deleting"].get(username)
            if user_data is not None:
                self._config["users"][username] = user_data
            backup_deleting = None
            if "deleting" in self._config:
                backup_deleting = self._config["deleting"].pop(username, None)
            try:
                self._save()
                return True
            except Exception as e:
                self._config["users"].pop(username, None)
                if backup_deleting is not None:
                    self._config.setdefault("deleting", {})[username] = backup_deleting
                logger.error(f"Failed to restore user '{username}' after deletion failure: {e}")
                raise

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
        with self._cross_process_lock():
            if old_username not in self.users:
                return False
            if new_username in self.users:
                return False
            if not self.users.get(requesting_user, {}).get("is_admin"):
                return False
            self._config.setdefault("users", {})[new_username] = self._config["users"].pop(old_username)
            self._save()

        renamed_sessions = 0
        with self._sessions_lock:
            for sess in self._sessions.values():
                sess_user = str((sess or {}).get("username") or "").strip().lower()
                if sess_user == old_username:
                    sess["username"] = new_username
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
        return [
            {"username": u, "is_admin": d.get("is_admin", False), "privileges": self.get_privileges(u)}
            for u, d in self.users.items()
        ]

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
        with self._config_lock:
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
        with self._config_lock:
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
                from core.database import get_db_session
                with get_db_session() as db:
                    removed_tokens = delete_api_tokens_for_user(db, username)
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
            self._config["users"][username]["password_hash"] = _hash_password(new_password)
            self._save()
            self.revoke_user_sessions(username)
        logger.info("Password reset for '%s' by '%s'", username, requesting_user)
        return True

    def change_password(self, username: str, current_password: str, new_password: str) -> bool:
        username = username.strip().lower()
        if username not in self.users:
            return False
        if not _verify_password(current_password, self.users[username]["password_hash"]):
            return False
        with self._config_lock:
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
        if username not in self.users:
            return None
        secret = pyotp.random_base32()
        with self._config_lock:
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
        user = self.users.get(username, {})
        secret = user.get("totp_secret_pending")
        if not secret:
            return False
        totp = pyotp.TOTP(secret)
        if not totp.verify(code, valid_window=1):
            return False
        # Enable 2FA
        with self._config_lock:
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
            with self._config_lock:
                backup.remove(code)
                self._config["users"][username]["totp_backup_codes"] = backup
                self._save()
            logger.info(f"Backup code used for '{username}' ({len(backup)} remaining)")
            return True
        totp = pyotp.TOTP(secret)
        return totp.verify(code, valid_window=1)

    def totp_disable(self, username: str, password: str) -> bool:
        """Disable 2FA for a user. Requires password confirmation."""
        username = username.strip().lower()
        if not self.verify_password(username, password):
            return False
        with self._config_lock:
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
        return _verify_password(password, self.users[username]["password_hash"])

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
        expired = False
        deleted_user = False
        with self._sessions_lock:
            session = self._sessions.get(token)
            if session is None:
                return False
            if time.time() > session["expiry"]:
                self._sessions.pop(token, None)
                expired = True
            else:
                # SECURITY: if the user record has since been removed (admin
                # deleted them while their cookie was still valid), drop the
                # session so the next request kicks them out instead of
                # silently authenticating against a non-existent account.
                if session.get("username") not in self.users:
                    self._sessions.pop(token, None)
                    deleted_user = True
        if expired or deleted_user:
            self._save_sessions()
            return False
        return True

    def get_username_for_token(self, token: Optional[str]) -> Optional[str]:
        """Return the username associated with a valid token."""
        if not token:
            return None
        expired = False
        deleted_user = False
        with self._sessions_lock:
            session = self._sessions.get(token)
            if session is None:
                return None
            if time.time() > session["expiry"]:
                self._sessions.pop(token, None)
                expired = True
            else:
                _u = session["username"]
                # SECURITY: orphan check — same rationale as validate_token.
                if _u not in self.users:
                    self._sessions.pop(token, None)
                    deleted_user = True
                else:
                    return _u
        if expired or deleted_user:
            self._save_sessions()
        return None

    def revoke_token(self, token: str):
        with self._sessions_lock:
            self._sessions.pop(token, None)
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
                revoked += 1
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


_REAL_AUTH_MANAGER = AuthManager
