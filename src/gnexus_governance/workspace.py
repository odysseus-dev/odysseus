from pathlib import Path
import os

DEFAULT_WORKSPACE_ROOT = r"C:\Users\iamcy\CymaticsDev"


def normalize_path(value: str) -> str:
    return str(Path(value).expanduser().resolve())


def is_inside(path: str, root: str) -> bool:
    try:
        p = Path(path).expanduser().resolve()
        r = Path(root).expanduser().resolve()
        return str(p).lower() == str(r).lower() or str(p).lower().startswith(str(r).lower() + os.sep)
    except Exception:
        return False


def classify_workspace_path(path: str, workspace_root: str = DEFAULT_WORKSPACE_ROOT) -> dict:
    p = normalize_path(path)
    inside = is_inside(p, workspace_root)
    sensitive_names = [".env", "auth.json", "app.db", "id_rsa", "id_ed25519"]
    lower = p.lower()
    sensitive = any(name.lower() in lower for name in sensitive_names) or lower.endswith((".pem", ".key", ".sqlite", ".sqlite3"))
    return {
        "path": p,
        "workspaceRoot": normalize_path(workspace_root),
        "insideWorkspace": inside,
        "sensitive": sensitive,
        "readDecision": "APPROVAL_REQUIRED" if sensitive else ("ALLOW" if inside else "BLOCK"),
        "writeDecision": "APPROVAL_REQUIRED" if inside and not sensitive else "BLOCK_OR_ESCALATE"
    }
