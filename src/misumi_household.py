"""Read-only adapter for the canonical household repository."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from pathlib import Path
from typing import Dict, Iterable, List, Optional


ALLOWED_SUFFIXES = {".md", ".txt", ".yaml", ".yml", ".json", ".csv", ".tsv"}
DOMAIN_PATHS = {
    "tasks": ("agent-tasks",),
    "food": ("household/food", "household/recipes"),
    "shopping": ("household/food/shopping-list.md",),
    "recipes": ("household/recipes",),
    "cleaning": ("household/cleaning",),
    "records": ("household/records",),
    "plants": ("household/plants",),
    "finances": ("household/finances",),
    "maintenance": (
        "household/maintenance",
        "agent-tasks/inbox",
        "agent-tasks/odysseus",
        "agent-tasks/misumi",
        "agent-tasks/review",
        "agent-tasks/blocked-human",
    ),
}

DOMAIN_TERMS = {
    "shopping": {"shopping", "grocery", "groceries", "buy"},
    "food": {"food", "stock", "inventory", "meal", "meals", "cook", "cooking"},
    "recipes": {"recipe", "recipes"},
    "cleaning": {"clean", "cleaning", "chore", "chores", "rota"},
    "records": {"record", "records", "album", "albums", "music", "play"},
    "plants": {"plant", "plants", "watering", "watered"},
    "finances": {"budget", "finance", "finances", "receipt", "subscription"},
    "maintenance": {"maintenance", "repair", "repairs", "broken", "urgent", "open-loop", "open-loops"},
    "tasks": {"task", "tasks", "blocked", "backlog"},
}


def infer_household_domain(query: str) -> Optional[str]:
    """Return the narrow canonical domain most explicitly named by a request."""
    terms = set(re.findall(r"[A-Za-z0-9_-]{2,}", (query or "").lower()))
    selected = None
    selected_score = 0
    for domain, indicators in DOMAIN_TERMS.items():
        score = len(terms.intersection(indicators))
        if score > selected_score:
            selected = domain
            selected_score = score
    return selected


def configured_household_root() -> Optional[Path]:
    value = (
        os.getenv("MISUMI_HOUSEHOLD_ROOT")
        or os.getenv("FLAT_KNOWLEDGEBASE_ROOT")
        or os.getenv("MISUMI_SOURCE_ROOT")
        or ""
    ).strip()
    if value:
        return Path(value).expanduser()
    home = Path.home()
    for candidate in (
        home / "Documents" / "flat-knowledgebase",
        home / "Documents" / "Claude" / "Projects" / "homeBase",
    ):
        if candidate.is_dir():
            return candidate
    return None


class HouseholdReadOnlyAdapter:
    """Path-confined reader with no mutation API."""

    def __init__(self, root: Optional[Path | str] = None):
        configured = Path(root).expanduser() if root else configured_household_root()
        self.root = configured.resolve() if configured else None

    @property
    def reachable(self) -> bool:
        return bool(self.root and self.root.is_dir())

    def _resolve(self, relative: str | Path) -> Path:
        if not self.root:
            raise FileNotFoundError("household repository is not configured")
        raw = str(relative or "").replace("\\", "/").strip().lstrip("/")
        if not raw or raw.startswith(".") or "/." in raw:
            raise ValueError("hidden or empty paths are not readable")
        candidate = (self.root / raw).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("path escapes household repository") from exc
        if candidate.suffix.lower() not in ALLOWED_SUFFIXES:
            raise ValueError("unsupported household file type")
        return candidate

    def domains(self) -> List[Dict[str, object]]:
        result = []
        for name, entries in DOMAIN_PATHS.items():
            present = False
            if self.root:
                present = any((self.root / entry).exists() for entry in entries)
            result.append({"id": name, "present": present, "paths": list(entries)})
        return result

    def iter_files(self, domain: Optional[str] = None) -> Iterable[Path]:
        if not self.root:
            return []
        entries = DOMAIN_PATHS.get(domain, ()) if domain else ("household", "agent-tasks")
        seen = set()
        files: List[Path] = []
        for entry in entries:
            base = (self.root / entry).resolve()
            try:
                base.relative_to(self.root)
            except ValueError:
                continue
            candidates = [base] if base.is_file() else base.rglob("*") if base.is_dir() else []
            for path in candidates:
                if not path.is_file() or path.suffix.lower() not in ALLOWED_SUFFIXES:
                    continue
                resolved = path.resolve()
                try:
                    resolved.relative_to(self.root)
                except ValueError:
                    continue
                if any(part.startswith(".") for part in resolved.relative_to(self.root).parts):
                    continue
                if resolved not in seen:
                    seen.add(resolved)
                    files.append(resolved)
        return sorted(files)

    def read(self, relative: str, start_line: int = 1, max_lines: int = 80) -> Dict[str, object]:
        path = self._resolve(relative)
        if not path.is_file():
            raise FileNotFoundError(relative)
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        start = max(1, int(start_line))
        count = max(1, min(int(max_lines), 200))
        selected = lines[start - 1:start - 1 + count]
        rel = path.relative_to(self.root).as_posix()
        return {
            "path": rel,
            "line_start": start,
            "line_end": start + len(selected) - 1 if selected else start,
            "text": "\n".join(selected),
        }

    def search(self, query: str, domain: Optional[str] = None, limit: int = 10) -> List[Dict[str, object]]:
        terms = [term.lower() for term in re.findall(r"[A-Za-z0-9_]{2,}", query or "")]
        stop = {
            "and", "answer", "are", "current", "currently", "data", "does", "exists", "explain",
            "fewer", "from", "have", "in", "is", "language", "on", "only", "plain", "reply", "that",
            "the", "there", "this", "to", "what", "when", "where", "which", "with", "words",
        }
        terms = [term for term in terms if term not in stop]
        if not terms:
            return []
        hits = []
        for path in self.iter_files(domain):
            rel = path.relative_to(self.root).as_posix()
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for number, line in enumerate(lines, 1):
                line_terms = set(term.lower() for term in re.findall(r"[A-Za-z0-9_]{2,}", line))
                score = sum(1 for term in terms if term in line_terms)
                if not score:
                    continue
                hits.append({"path": rel, "line": number, "snippet": line.strip()[:500], "score": score})
        hits.sort(key=lambda item: (-int(item["score"]), str(item["path"]), int(item["line"])))
        return hits[:max(1, min(int(limit), 50))]

    def git_state(self) -> Dict[str, object]:
        if not self.root:
            return {"available": False, "dirty": None, "error": "household repository is not configured"}
        try:
            result = subprocess.run(
                ["git", "-C", str(self.root), "status", "--short", "--branch"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            lines = [line for line in result.stdout.splitlines() if line]
            changes = [line for line in lines if not line.startswith("##")]
            return {
                "available": result.returncode == 0,
                "dirty": bool(changes) if result.returncode == 0 else None,
                "summary": lines[:30],
            }
        except Exception as exc:
            return {"available": False, "dirty": None, "error": str(exc)}

    def content_fingerprint(self) -> str:
        """Hash readable canonical content for non-mutation smoke tests."""
        digest = hashlib.sha256()
        for path in self.iter_files():
            digest.update(path.relative_to(self.root).as_posix().encode())
            digest.update(path.read_bytes())
        return digest.hexdigest()

    def status(self) -> Dict[str, object]:
        return {
            "reachable": self.reachable,
            "root": str(self.root) if self.root else None,
            "domains": self.domains(),
            "git": self.git_state(),
            "mode": "read_only",
            "writes_allowed": False,
        }
