"""
ExecutionGuard: Security scanner for commands, packages, and operations.
Risk levels: LOW (auto-execute), MEDIUM (user approval required), HIGH (blocked).
"""
import re
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_HIGH_RISK_PATTERNS = [
    re.compile(r"rm\s+-rf\s+/(?!workspace)", re.IGNORECASE),
    re.compile(r"/etc/(?:passwd|shadow|sudoers|crontab)", re.IGNORECASE),
    re.compile(r"C:\\Windows\\System32", re.IGNORECASE),
    re.compile(r"chmod\s+(?:777|4755|6755|a\+s)", re.IGNORECASE),
    re.compile(r"sudo\s+(?:su|bash|sh|-i|-s)", re.IGNORECASE),
    re.compile(r"privilege.*escal|escal.*privilege", re.IGNORECASE),
    re.compile(r"\-\-privileged", re.IGNORECASE),
    re.compile(r"nsenter|chroot\s+/(?!workspace)", re.IGNORECASE),
    re.compile(r"docker\s+run.*--cap-add\s+ALL", re.IGNORECASE),
    re.compile(r"mkfs\.|dd\s+if=/dev/(?!null|zero|urandom)", re.IGNORECASE),
    re.compile(r"fork\s*bomb|\:\(\)\s*\{\s*:\|:", re.IGNORECASE),
    re.compile(r"base64\s+-d.*\|\s*(?:bash|sh|python)", re.IGNORECASE),
]

_MEDIUM_RISK_PATTERNS = [
    re.compile(r"nmcli.*connect|netsh.*wlan.*connect", re.IGNORECASE),
    re.compile(r"apt(?:-get)?\s+install|pip\s+install|npm\s+install", re.IGNORECASE),
    re.compile(r"iptables|ufw\s+(?:allow|deny|enable)", re.IGNORECASE),
    re.compile(r"docker\s+(?:run|exec|create|pull)", re.IGNORECASE),
    re.compile(r"systemctl\s+(?:start|stop|restart|enable)", re.IGNORECASE),
    re.compile(r"crontab\s+-[el]", re.IGNORECASE),
    re.compile(r"curl.*\|\s*(?:bash|sh)|wget.*\|\s*(?:bash|sh)", re.IGNORECASE),
    re.compile(r"ssh\s+.*-i\s|scp\s", re.IGNORECASE),
    re.compile(r"pkill|killall|kill\s+-9", re.IGNORECASE),
    re.compile(r"useradd|userdel|passwd\s+\w", re.IGNORECASE),
]

_MEDIUM_PERMISSIONS = {"NET_ADMIN", "HardwareBridgeAccess", "DeviceMapping", "MANAGE_WORKSPACES"}
_HIGH_PERMISSIONS = {"SYS_ADMIN", "SYS_PTRACE", "ALL", "CAP_ALL"}


def classify_command(command: str) -> dict:
    """
    Classify a shell command by risk level.
    Returns: {risk: "LOW"|"MEDIUM"|"HIGH", reason: str, blocked: bool}
    """
    for pattern in _HIGH_RISK_PATTERNS:
        if pattern.search(command):
            return {
                "risk": "HIGH",
                "reason": f"Blocked dangerous pattern detected",
                "blocked": True,
            }
    for pattern in _MEDIUM_RISK_PATTERNS:
        if pattern.search(command):
            return {
                "risk": "MEDIUM",
                "reason": "Operation requires user approval",
                "blocked": False,
            }
    return {"risk": "LOW", "reason": "Routine operation", "blocked": False}


def scan_package(manifest: dict, pkg_path: str | None = None) -> dict:
    """
    Validate a package manifest and optionally scan backend source files.
    Returns: {risk: str, reason: str, blocked: bool, warnings: list}
    """
    warnings = []
    permissions = manifest.get("requiredPermissions", [])

    for perm in permissions:
        if perm in _HIGH_PERMISSIONS:
            return {
                "risk": "HIGH",
                "reason": f"Dangerous permission requested: {perm}",
                "blocked": True,
                "warnings": warnings,
            }

    has_medium = any(p in _MEDIUM_PERMISSIONS for p in permissions)

    if pkg_path:
        entry = manifest.get("entryPoint", "")
        if entry.endswith(".py"):
            backend_file = Path(pkg_path) / entry
            if backend_file.exists():
                try:
                    code = backend_file.read_text(errors="ignore")
                    for pattern in _HIGH_RISK_PATTERNS:
                        if pattern.search(code):
                            return {
                                "risk": "HIGH",
                                "reason": f"Dangerous code pattern in backend: {entry}",
                                "blocked": True,
                                "warnings": warnings,
                            }
                    for pattern in _MEDIUM_RISK_PATTERNS:
                        if pattern.search(code):
                            warnings.append(f"Sensitive operation pattern in {entry}")
                            has_medium = True
                except Exception as e:
                    warnings.append(f"Could not scan {entry}: {e}")

    if has_medium:
        return {
            "risk": "MEDIUM",
            "reason": "Package requests elevated permissions",
            "blocked": False,
            "warnings": warnings,
        }

    return {"risk": "LOW", "reason": "Package appears safe", "blocked": False, "warnings": warnings}


def check_docker_operation(operation: str, target_labels: dict, requester_owner: str) -> dict:
    """
    Validate Docker operations to prevent cross-workspace interference.
    Prevents one agent from stopping/destroying another agent's workspace.
    """
    ws_owner = target_labels.get("odysseus.owner")
    ws_protected = target_labels.get("odysseus.protected") == "true"
    destructive = operation in ("destroy", "stop", "restart", "remove")

    if ws_protected and ws_owner and ws_owner != requester_owner and destructive:
        return {
            "risk": "HIGH",
            "reason": f"Cannot {operation} a protected workspace owned by '{ws_owner}'",
            "blocked": True,
        }

    if destructive:
        return {"risk": "MEDIUM", "reason": f"Destructive Docker operation: {operation}", "blocked": False}

    return {"risk": "LOW", "reason": "Non-destructive operation", "blocked": False}
