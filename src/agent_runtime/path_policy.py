"""Existing sensitive-path policy shared by tools and evidence observation.

This is a deny predicate, not an authorization grant or a workspace scope.
"""
import os

_SENSITIVE_BASENAMES: set[str] = {
    ".ssh", ".gnupg", ".gitconfig",
    ".bashrc", ".bash_profile", ".bash_logout",
    ".zshrc", ".zprofile", ".zshenv",
    ".profile", ".tcshrc", ".cshrc", ".env", ".netrc",
}
_SENSITIVE_FILE_PATTERNS: tuple[str, ...] = (
    "authorized_keys", "id_rsa", "id_ed25519", "id_ecdsa",
    "known_hosts", "auth.json", "app.db", "settings.json",
)
_SENSITIVE_BASENAMES_CF = frozenset(b.casefold() for b in _SENSITIVE_BASENAMES)
_SENSITIVE_FILE_PATTERNS_CF = frozenset(p.casefold() for p in _SENSITIVE_FILE_PATTERNS)


def _is_sensitive_path(resolved: str) -> bool:
    # Case folding is required even on POSIX: default macOS volumes are
    # case insensitive but os.path.normcase there does not fold path names.
    parts = [p.casefold() for p in resolved.split(os.sep)]
    filename = parts[-1] if parts else ""
    return any(part in _SENSITIVE_BASENAMES_CF for part in parts) or filename in _SENSITIVE_FILE_PATTERNS_CF
