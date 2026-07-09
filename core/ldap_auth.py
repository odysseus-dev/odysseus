"""LDAP / FreeIPA authentication helpers.

The module is intentionally isolated from ``core.auth`` so local password
auth keeps working when LDAP is not configured, misconfigured, or when the
optional ldap3 dependency is unavailable.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
import logging
import os
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set

logger = logging.getLogger(__name__)


class LdapAuthError(Exception):
    """Base LDAP auth failure."""


class LdapConfigError(LdapAuthError):
    """LDAP is enabled but required settings are missing."""


@dataclass(frozen=True)
class LdapConfig:
    enabled: bool = False
    server_uri: str = ""
    bind_dn: str = ""
    bind_password: str = ""
    user_base_dn: str = ""
    user_filter: str = "(|(uid={username})(sAMAccountName={username})(userPrincipalName={username})(mail={username}))"
    user_name_attribute: str = "uid"
    display_name_attribute: str = "cn"
    email_attribute: str = "mail"
    group_base_dn: str = ""
    group_filter: str = "(|(member={user_dn})(memberUid={username}))"
    allowed_groups: Sequence[str] = ()
    admin_groups: Sequence[str] = ()
    start_tls: bool = False
    connect_timeout: float = 5.0

    @classmethod
    def from_env(cls) -> "LdapConfig":
        return cls(
            enabled=_env_bool("ODYSSEUS_LDAP_ENABLED", False),
            server_uri=os.getenv("ODYSSEUS_LDAP_SERVER_URI", "").strip(),
            bind_dn=os.getenv("ODYSSEUS_LDAP_BIND_DN", "").strip(),
            bind_password=os.getenv("ODYSSEUS_LDAP_BIND_PASSWORD", ""),
            user_base_dn=os.getenv("ODYSSEUS_LDAP_USER_BASE_DN", "").strip(),
            user_filter=os.getenv(
                "ODYSSEUS_LDAP_USER_FILTER",
                cls.user_filter,
            ).strip(),
            user_name_attribute=os.getenv("ODYSSEUS_LDAP_USER_NAME_ATTRIBUTE", "uid").strip() or "uid",
            display_name_attribute=os.getenv("ODYSSEUS_LDAP_DISPLAY_NAME_ATTRIBUTE", "cn").strip() or "cn",
            email_attribute=os.getenv("ODYSSEUS_LDAP_EMAIL_ATTRIBUTE", "mail").strip() or "mail",
            group_base_dn=os.getenv("ODYSSEUS_LDAP_GROUP_BASE_DN", "").strip(),
            group_filter=os.getenv("ODYSSEUS_LDAP_GROUP_FILTER", cls.group_filter).strip(),
            allowed_groups=_split_env("ODYSSEUS_LDAP_ALLOWED_GROUPS"),
            admin_groups=_split_env("ODYSSEUS_LDAP_ADMIN_GROUPS"),
            start_tls=_env_bool("ODYSSEUS_LDAP_START_TLS", False),
            connect_timeout=_env_float("ODYSSEUS_LDAP_CONNECT_TIMEOUT", 5.0),
        )

    def validate(self) -> None:
        if not self.enabled:
            return
        missing = []
        if not self.server_uri:
            missing.append("ODYSSEUS_LDAP_SERVER_URI")
        if not self.user_base_dn:
            missing.append("ODYSSEUS_LDAP_USER_BASE_DN")
        if missing:
            raise LdapConfigError("Missing LDAP setting(s): " + ", ".join(missing))

    @classmethod
    def from_mapping(
        cls,
        settings: Optional[Mapping[str, Any]],
        *,
        env_fallback: bool = True,
    ) -> "LdapConfig":
        """Build LDAP config from auth.json settings, using env as fallback."""
        base = cls.from_env() if env_fallback else cls()
        if not isinstance(settings, Mapping):
            return base
        data = {field.name: getattr(base, field.name) for field in fields(cls)}
        defaults = cls()
        for name in data:
            if name not in settings or settings[name] is None:
                continue
            value = settings[name]
            if name in {"enabled", "start_tls"}:
                data[name] = _coerce_bool(value, bool(data[name]))
            elif name in {"allowed_groups", "admin_groups"}:
                data[name] = _split_setting(value)
            elif name == "connect_timeout":
                data[name] = _coerce_float(value, float(data[name] or defaults.connect_timeout))
            elif name == "bind_password":
                data[name] = str(value)
            else:
                text = str(value).strip()
                if not text and name in {
                    "user_filter",
                    "user_name_attribute",
                    "display_name_attribute",
                    "email_attribute",
                    "group_filter",
                }:
                    text = str(getattr(defaults, name))
                data[name] = text
        return cls(**data)


@dataclass(frozen=True)
class LdapLoginResult:
    username: str
    user_dn: str
    display_name: str = ""
    email: str = ""
    groups: Sequence[str] = ()
    is_admin: bool = False


def ldap_enabled(config: Optional[LdapConfig] = None) -> bool:
    return (config or LdapConfig.from_env()).enabled


def sanitize_ldap_settings(
    settings: Optional[Mapping[str, Any]],
    existing: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Return auth.json-safe LDAP settings without leaking absent secrets."""
    current = dict(existing or {}) if isinstance(existing, Mapping) else {}
    out: Dict[str, Any] = {
        field.name: current[field.name]
        for field in fields(LdapConfig)
        if field.name in current
    }
    if not isinstance(settings, Mapping):
        return out

    defaults = LdapConfig()
    clear_password = _coerce_bool(settings.get("clear_bind_password"), False)
    for field in fields(LdapConfig):
        name = field.name
        if name not in settings or settings[name] is None:
            continue
        value = settings[name]
        if name in {"enabled", "start_tls"}:
            out[name] = _coerce_bool(value, bool(getattr(defaults, name)))
        elif name in {"allowed_groups", "admin_groups"}:
            out[name] = list(_split_setting(value))
        elif name == "connect_timeout":
            out[name] = _coerce_float(value, defaults.connect_timeout)
        elif name == "bind_password":
            password = str(value)
            if clear_password:
                out[name] = ""
            elif password:
                out[name] = password
        else:
            text = str(value).strip()
            if not text and name in {
                "user_filter",
                "user_name_attribute",
                "display_name_attribute",
                "email_attribute",
                "group_filter",
            }:
                text = str(getattr(defaults, name))
            out[name] = text

    if clear_password and "bind_password" not in settings:
        out["bind_password"] = ""
    return out


def public_ldap_settings(config: Optional[LdapConfig] = None) -> Dict[str, Any]:
    """Return LDAP settings safe to expose to admin UI clients."""
    config = config or LdapConfig.from_env()
    data: Dict[str, Any] = {}
    for field in fields(LdapConfig):
        value = getattr(config, field.name)
        if field.name in {"allowed_groups", "admin_groups"}:
            value = list(value)
        data[field.name] = value
    data["bind_password_configured"] = bool(config.bind_password)
    data["bind_password"] = ""
    return data


def diagnose_ldap(
    username: str,
    password: str,
    config: Optional[LdapConfig] = None,
) -> Dict[str, Any]:
    """Run an LDAP login test and return admin-friendly diagnostics."""
    config = config or LdapConfig.from_env()
    result: Dict[str, Any] = {
        "ok": False,
        "stage": "disabled",
        "detail": "LDAP is disabled",
        "host": config.server_uri,
        "username": "",
        "user_dn": "",
        "groups": [],
        "is_admin": False,
        "in_allowed_group": False,
    }
    if not config.enabled:
        return result
    try:
        config.validate()
    except LdapConfigError as exc:
        result.update(stage="configuration", detail=str(exc))
        return result
    try:
        login = authenticate_ldap(username, password, config)
    except LdapConfigError as exc:
        result.update(stage="configuration", detail=str(exc))
        return result
    except LdapAuthError as exc:
        result.update(stage="connection", detail=str(exc))
        return result
    except Exception as exc:
        logger.warning("LDAP diagnostic failed: %s", exc)
        result.update(stage="error", detail=str(exc))
        return result
    if login is None:
        result.update(
            stage="authentication",
            detail="Invalid credentials, no unique user match, or LDAP group policy denied access",
        )
        return result
    result.update(
        ok=True,
        stage="authenticated",
        detail="LDAP login succeeded",
        username=login.username,
        user_dn=login.user_dn,
        groups=list(login.groups),
        is_admin=login.is_admin,
        in_allowed_group=not config.allowed_groups or group_set_matches(login.groups, config.allowed_groups),
    )
    return result


def authenticate_ldap(username: str, password: str, config: Optional[LdapConfig] = None) -> Optional[LdapLoginResult]:
    """Authenticate a user against LDAP and return normalized directory data.

    Returns ``None`` for bad credentials, no matching user, group lockout, or
    disabled LDAP. Raises ``LdapConfigError`` for an enabled-but-invalid config.
    """
    config = config or LdapConfig.from_env()
    if not config.enabled:
        return None
    config.validate()
    username = (username or "").strip()
    if not username or not password:
        return None

    try:
        ldap3 = _ldap3()
        escape_filter_chars = _ldap_escape()
    except ImportError as exc:
        logger.error("LDAP login requested but ldap3 is not installed")
        raise LdapConfigError("LDAP support requires the ldap3 package") from exc

    server = ldap3.Server(config.server_uri, get_info=ldap3.NONE, connect_timeout=config.connect_timeout)
    search_conn = _bind_connection(ldap3, server, config, config.bind_dn, config.bind_password)
    try:
        escaped_username = escape_filter_chars(username)
        user_filter = config.user_filter.format(username=escaped_username)
        attributes = _unique_attributes(
            [
                config.user_name_attribute,
                config.display_name_attribute,
                config.email_attribute,
                "memberOf",
            ]
        )
        ok = search_conn.search(
            search_base=config.user_base_dn,
            search_filter=user_filter,
            search_scope=ldap3.SUBTREE,
            attributes=attributes,
            size_limit=2,
        )
        entries = list(search_conn.entries or []) if ok else []
        if len(entries) != 1:
            if len(entries) > 1:
                logger.warning("LDAP login refused for '%s': user filter returned multiple entries", username)
            return None

        entry = entries[0]
        user_dn = str(entry.entry_dn)
        user_value = _first_attr(entry, config.user_name_attribute) or username
        normalized_username = user_value.strip().lower()
        if not normalized_username:
            return None

        user_conn = _bind_connection(ldap3, server, config, user_dn, password)
        user_conn.unbind()

        groups = _collect_groups(
            ldap3=ldap3,
            conn=search_conn,
            config=config,
            user_dn=user_dn,
            username=normalized_username,
            entry=entry,
        )
        if config.allowed_groups and not group_set_matches(groups, config.allowed_groups):
            logger.info("LDAP login refused for '%s': not in an allowed group", normalized_username)
            return None
        is_admin = bool(config.admin_groups and group_set_matches(groups, config.admin_groups))
        return LdapLoginResult(
            username=normalized_username,
            user_dn=user_dn,
            display_name=_first_attr(entry, config.display_name_attribute),
            email=_first_attr(entry, config.email_attribute),
            groups=tuple(sorted(groups)),
            is_admin=is_admin,
        )
    finally:
        search_conn.unbind()


def group_set_matches(groups: Iterable[str], configured_groups: Iterable[str]) -> bool:
    """Return true when any LDAP group matches a configured DN or group name."""
    available = {_normalize_group_token(g) for g in groups if str(g or "").strip()}
    desired = {_normalize_group_token(g) for g in configured_groups if str(g or "").strip()}
    return bool(available & desired)


def group_aliases(group: str) -> Set[str]:
    """Return matching aliases for a group DN/name.

    Supports full DN config (``cn=admins,cn=groups,...``) and short group-name
    config (``admins``), which is common for FreeIPA home-lab setups.
    """
    raw = str(group or "").strip()
    if not raw:
        return set()
    aliases = {_normalize_group_token(raw)}
    first_rdn = raw.split(",", 1)[0].strip()
    if "=" in first_rdn:
        aliases.add(_normalize_group_token(first_rdn.split("=", 1)[1]))
    return {a for a in aliases if a}


def _collect_groups(*, ldap3: Any, conn: Any, config: LdapConfig, user_dn: str, username: str, entry: Any) -> Set[str]:
    groups: Set[str] = set()
    for member_of in _list_attr(entry, "memberOf"):
        groups.update(group_aliases(member_of))

    if config.group_base_dn:
        try:
            escape_filter_chars = _ldap_escape()
            group_filter = config.group_filter.format(
                user_dn=escape_filter_chars(user_dn),
                username=escape_filter_chars(username),
            )
            ok = conn.search(
                search_base=config.group_base_dn,
                search_filter=group_filter,
                search_scope=ldap3.SUBTREE,
                attributes=["cn", "memberOf"],
            )
            if ok:
                for group_entry in conn.entries or []:
                    groups.update(group_aliases(str(group_entry.entry_dn)))
                    for cn in _list_attr(group_entry, "cn"):
                        groups.update(group_aliases(cn))
        except Exception as exc:
            logger.warning("LDAP group lookup failed for '%s': %s", username, exc)
    return groups


def _bind_connection(ldap3: Any, server: Any, config: LdapConfig, user: str = "", password: str = "") -> Any:
    conn = ldap3.Connection(
        server,
        user=user or None,
        password=password or None,
        receive_timeout=config.connect_timeout,
        raise_exceptions=False,
    )
    if not conn.open():
        raise LdapAuthError("LDAP server connection failed")
    if config.start_tls and not conn.start_tls():
        conn.unbind()
        raise LdapAuthError("LDAP StartTLS failed")
    if not conn.bind():
        conn.unbind()
        raise LdapAuthError("LDAP bind failed")
    return conn


def _first_attr(entry: Any, name: str) -> str:
    values = _list_attr(entry, name)
    return values[0] if values else ""


def _list_attr(entry: Any, name: str) -> List[str]:
    if not name:
        return []
    try:
        attr = getattr(entry, name)
    except Exception:
        return []
    try:
        values = attr.values
    except Exception:
        try:
            value = attr.value
        except Exception:
            return []
        values = [value]
    if values is None:
        return []
    if not isinstance(values, (list, tuple, set)):
        values = [values]
    return [str(v).strip() for v in values if str(v or "").strip()]


def _unique_attributes(values: Iterable[str]) -> List[str]:
    seen = set()
    out = []
    for value in values:
        key = str(value or "").strip()
        if key and key not in seen:
            out.append(key)
            seen.add(key)
    return out


def _normalize_group_token(value: str) -> str:
    return str(value or "").strip().lower()


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return _coerce_bool(value, default)


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    return _coerce_float(value, default)


def _coerce_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def _coerce_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _split_env(name: str) -> Sequence[str]:
    value = os.getenv(name, "")
    return _split_setting(value)


def _split_setting(value: Any) -> Sequence[str]:
    if isinstance(value, (list, tuple, set)):
        return tuple(str(part).strip() for part in value if str(part or "").strip())
    text = str(value or "").strip()
    if not text:
        return ()
    if ";" in text or "\n" in text:
        return tuple(part.strip() for part in text.replace("\n", ";").split(";") if part.strip())
    if "=" in text:
        return (text,)
    return tuple(part.strip() for part in text.split(",") if part.strip())


def _ldap3() -> Any:
    import ldap3  # type: ignore

    return ldap3


def _ldap_escape():
    from ldap3.utils.conv import escape_filter_chars  # type: ignore

    return escape_filter_chars
