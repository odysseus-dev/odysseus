"""Generic LDAP login backend (FreeIPA, 389-ds, OpenLDAP, Active Directory, ...).

An admin can enable "LDAP login" under Settings → Users and set the
directory's domain (e.g. "example.com"). Users then authenticate with their
LDAP credentials instead of (or in addition to) a locally managed password —
we attempt an LDAP simple bind as the user against the directory and treat a
successful bind as proof of a valid password. Optionally, membership in a
"required group" gates access, and membership in a separate "admin group"
grants admin status automatically.

Requires the optional `ldap3` package (see requirements-optional.txt). When
absent, `authenticate()` raises a clear RuntimeError instead of silently
failing every login, so the admin gets a useful error in the server log /
API response rather than a mysterious "Invalid credentials".
"""

import logging
import ssl

logger = logging.getLogger(__name__)

LDAP_MISSING = (
    "LDAP login requires the ldap3 package. Install optional dependencies "
    "with `pip install -r requirements-optional.txt`."
)


class LDAPAccessDenied(Exception):
    """Valid credentials, but not a member of the required group."""


class LDAPAuthResult:
    """Outcome of a successful LDAP bind. Truthy so callers can keep writing
    `if result:` like a plain bool; `is_admin` reflects membership in the
    configured admin group (False when no admin group is configured)."""

    def __init__(self, is_admin: bool = False):
        self.is_admin = is_admin

    def __bool__(self) -> bool:
        return True


def domain_to_base_dn(domain: str) -> str:
    """Convert a DNS domain ('example.com') into an LDAP base DN
    ('dc=example,dc=com')."""
    parts = [p for p in domain.strip().lower().split(".") if p]
    return ",".join(f"dc={p}" for p in parts)


def user_dn(username: str, domain: str, user_dn_template: str = "") -> str:
    """Build the user bind DN for `username` in `domain`.

    `user_dn_template` lets the admin override the RDN shape for directories
    that don't use FreeIPA/389-ds's default layout (use "{username}" and
    "{base_dn}" placeholders, e.g. "cn={username},ou=people,{base_dn}" for a
    typical OpenLDAP tree, or "{username}@{domain}" style UPNs for Active
    Directory via a custom template). Defaults to the FreeIPA/389-ds layout.
    """
    base_dn = domain_to_base_dn(domain)
    template = user_dn_template.strip() or "uid={username},cn=users,cn=accounts,{base_dn}"
    return template.format(username=username, base_dn=base_dn, domain=domain)


def group_dn(group: str, domain: str, group_dn_template: str = "") -> str:
    """Build the group entry DN for `group` in `domain`. Same override
    mechanism as `user_dn`; defaults to the FreeIPA/389-ds layout."""
    base_dn = domain_to_base_dn(domain)
    template = group_dn_template.strip() or "cn={group},cn=groups,cn=accounts,{base_dn}"
    return template.format(group=group, base_dn=base_dn, domain=domain)


def _is_group_member(conn, bind_dn: str, username: str, group: str,
                      domain: str, group_dn_template: str):
    """Look up `group` and check whether `bind_dn`/`username` is a member.
    Supports both DN-valued `member` (groupOfNames/FreeIPA) and bare-name
    `memberUid` (posixGroup) membership attributes. Returns a tuple
    `(is_member, detail)` — `detail` explains a miss (group not found vs.
    found-but-not-a-member) for diagnostics."""
    target = group_dn(group, domain, group_dn_template)
    if not conn.search(target, "(objectClass=*)", attributes=["member", "memberUid"]):
        return False, f"group entry '{target}' not found ({conn.result.get('description', 'search failed')})"
    if not conn.entries:
        return False, f"group entry '{target}' not found"
    attrs = conn.entries[0].entry_attributes_as_dict
    members = [str(m) for m in attrs.get("member", [])]
    member_uids = [str(m) for m in attrs.get("memberUid", [])]
    is_member = bind_dn in members or username in member_uids
    detail = "member" if is_member else f"not a member of '{target}'"
    return is_member, detail


def _attempt(
    username: str,
    password: str,
    *,
    domain: str,
    server: str = "",
    user_dn_template: str = "",
    use_ssl: bool = True,
    verify_cert: bool = True,
    timeout: float = 5.0,
    required_group: str = "",
    group_dn_template: str = "",
    admin_group: str = "",
) -> dict:
    """Core LDAP bind + group-check logic shared by `authenticate()` (real
    login — returns a narrow bool/exception so nothing sensitive leaks to an
    unauthenticated caller) and `test_login()` (admin-only diagnostics —
    returns this full dict as-is). Keys:
      stage:   'config' | 'connect' | 'bind' | 'required_group' |
               'admin_group' | 'success'
      ok:      bool — True only for 'success'
      detail:  human-readable explanation of the stage's outcome
      host, bind_dn: the values actually used, so a wrong Domain/Server/DN
               template shows up immediately instead of being guessed at
      is_admin, in_required_group: bool | None (None = not checked)
    """
    result = {
        "stage": "config", "ok": False, "detail": "",
        "host": "", "bind_dn": "",
        "is_admin": False, "in_required_group": None,
    }
    try:
        import ldap3
        from ldap3.core.exceptions import LDAPException
    except ImportError as exc:
        raise RuntimeError(LDAP_MISSING) from exc

    domain = (domain or "").strip()
    username = (username or "").strip()
    if not domain or not username or not password:
        result["detail"] = "Missing domain, username, or password"
        return result

    host = (server or domain).strip()
    bind_dn = user_dn(username, domain, user_dn_template)
    result["host"] = host
    result["bind_dn"] = bind_dn

    tls = None
    if use_ssl:
        tls = ldap3.Tls(validate=ssl.CERT_REQUIRED if verify_cert else ssl.CERT_NONE)

    try:
        result["stage"] = "connect"
        server_obj = ldap3.Server(host, use_ssl=use_ssl, tls=tls, connect_timeout=timeout)
        conn = ldap3.Connection(
            server_obj,
            user=bind_dn,
            password=password,
            # ldap3 packs receive_timeout into a C "long" via struct.pack('LL', ...),
            # so it must be a whole number of seconds — a float (e.g. 5.0) raises
            # "struct.error: required argument is not an integer".
            receive_timeout=max(1, int(round(timeout))),
        )
        result["stage"] = "bind"
        if not conn.bind():
            desc = (conn.result or {}).get("description", "bind failed")
            msg = (conn.result or {}).get("message", "")
            result["detail"] = f"{desc}: {msg}".strip(": ")
            return result
        try:
            required_group = (required_group or "").strip()
            if required_group:
                result["stage"] = "required_group"
                is_member, detail = _is_group_member(
                    conn, bind_dn, username, required_group, domain, group_dn_template
                )
                result["in_required_group"] = is_member
                if not is_member:
                    result["detail"] = detail
                    return result

            admin_group = (admin_group or "").strip()
            if admin_group:
                result["stage"] = "admin_group"
                is_member, detail = _is_group_member(
                    conn, bind_dn, username, admin_group, domain, group_dn_template
                )
                result["is_admin"] = is_member

            result["stage"] = "success"
            result["ok"] = True
            result["detail"] = "Bind succeeded" + (
                " (admin group member)" if result["is_admin"] else ""
            )
            return result
        finally:
            conn.unbind()
    except LDAPException as e:
        result["detail"] = f"LDAP error at stage '{result['stage']}': {e}"
        return result
    except Exception as e:  # network errors, timeouts, etc.
        result["detail"] = f"Connection error at stage '{result['stage']}' (host={host}): {e}"
        return result


def authenticate(
    username: str,
    password: str,
    *,
    domain: str,
    server: str = "",
    user_dn_template: str = "",
    use_ssl: bool = True,
    verify_cert: bool = True,
    timeout: float = 5.0,
    required_group: str = "",
    group_dn_template: str = "",
    admin_group: str = "",
):
    """Attempt an LDAP simple bind as `username` against the directory for
    `domain`. Returns an `LDAPAuthResult` (truthy) when the bind succeeds and,
    if `required_group` is set, `username` is a member of it; `result.is_admin`
    is True when `admin_group` is set and `username` is a member of it too.
    Returns `False` for a plain bad-password/unreachable-server bind failure.
    Raises `LDAPAccessDenied` (not a plain `False`) when the credentials are
    valid but the required-group check fails, so callers can tell "wrong
    password" and "not authorized" apart. Never raises for bad credentials,
    an unreachable server, or any other LDAP-level failure — only for a
    missing `ldap3` dependency or missing required arguments.

    Detailed failure reasons are logged server-side (see `test_login()` for
    an admin-facing equivalent) — they are intentionally not returned here so
    an unauthenticated login attempt can't fingerprint the directory.
    """
    result = _attempt(
        username, password,
        domain=domain, server=server, user_dn_template=user_dn_template,
        use_ssl=use_ssl, verify_cert=verify_cert, timeout=timeout,
        required_group=required_group, group_dn_template=group_dn_template,
        admin_group=admin_group,
    )
    if result["stage"] == "required_group" and not result["ok"]:
        logger.info("LDAP auth: '%s' valid credentials but %s", username, result["detail"])
        raise LDAPAccessDenied()
    if not result["ok"]:
        logger.warning("LDAP auth failed for '%s' at stage '%s': %s",
                        username, result["stage"], result["detail"])
        return False
    return LDAPAuthResult(is_admin=result["is_admin"])


def test_login(username: str, password: str, **settings) -> dict:
    """Admin-only diagnostic: run the same bind + group-check logic as
    `authenticate()` but return the full stage-by-stage result instead of
    collapsing it to a bool, so an admin can see exactly where a login
    attempt fails (wrong host, wrong bind DN / domain, bad password, or a
    missing group membership) without digging through server logs."""
    return _attempt(username, password, **settings)
