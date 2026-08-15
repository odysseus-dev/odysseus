"""Shared validation for privileged Bash execution paths."""

from src.constants import MAX_BASH_COMMAND_CHARS


def validate_bash_command(command: str) -> str:
    """Return an executable command unchanged, or raise ``ValueError``.

    Bash commands are intentionally arbitrary code for authorized admins. The
    checks here are transport/resource guards, not a misleading command
    allowlist: reject empty input, NUL bytes that cannot be represented in an
    argv entry, and unexpectedly large programs before any process or detached
    job file is created.
    """
    if not isinstance(command, str):
        raise ValueError("Bash command must be text")
    if not command.strip():
        raise ValueError("No command provided")
    if "\x00" in command:
        raise ValueError("Bash command contains a NUL byte")
    if len(command) > MAX_BASH_COMMAND_CHARS:
        raise ValueError(
            f"Bash command is too large ({len(command)} characters; "
            f"maximum {MAX_BASH_COMMAND_CHARS})"
        )
    return command
