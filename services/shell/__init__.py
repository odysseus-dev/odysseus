# services/shell/__init__.py
"""Shell service — safe command execution."""

from .service import ShellResult, ShellService

__all__ = ["ShellService", "ShellResult"]
