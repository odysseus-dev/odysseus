import re

MUTATION_PATTERNS = [
    r"\bdel\b",
    r"\brm\b",
    r"Remove-Item",
    r"\bmove\b",
    r"Move-Item",
    r"\bcopy\b",
    r"Copy-Item",
    r"git\s+add",
    r"git\s+commit",
    r"git\s+reset",
    r"git\s+checkout",
    r"npm\s+install",
    r"pnpm\s+install",
    r"pip\s+install",
    r"docker\b",
    r"curl\b",
    r"Invoke-WebRequest",
    r"Invoke-RestMethod"
]

BLOCK_PATTERNS = [
    r"format\b",
    r"cipher\s+/w",
    r"shutdown\b",
    r"Restart-Computer",
    r"Stop-Computer"
]


def classify_shell_command(command: str) -> dict:
    text = command or ""
    for pat in BLOCK_PATTERNS:
        if re.search(pat, text, re.IGNORECASE):
            return {"decision": "BLOCK", "risk": "critical", "reason": "blocked_pattern", "pattern": pat}
    for pat in MUTATION_PATTERNS:
        if re.search(pat, text, re.IGNORECASE):
            return {"decision": "APPROVAL_REQUIRED", "risk": "high", "reason": "mutation_pattern", "pattern": pat}
    return {"decision": "ALLOW_READ_ONLY", "risk": "low", "reason": "no_mutation_pattern"}


def classify_file_write(path: str) -> dict:
    return {
        "decision": "APPROVAL_REQUIRED",
        "risk": "critical",
        "reason": "file_write_requires_diff_first_human_approval",
        "path": path
    }
