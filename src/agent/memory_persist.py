"""Memory persistence — file-based memory system."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import List


class _FileStore:
    def __init__(self, base_dir: str, filename: str) -> None:
        self._path = os.path.join(base_dir, filename)
        os.makedirs(os.path.dirname(self._path), exist_ok=True)

    def read(self) -> str:
        if os.path.exists(self._path):
            with open(self._path, "r", encoding="utf-8") as f:
                return f.read()
        return ""

    def write(self, content: str) -> None:
        with open(self._path, "w", encoding="utf-8") as f:
            f.write(content)

    def append(self, content: str) -> None:
        existing = self.read()
        sep = "\n\n" if existing.strip() else ""
        self.write(existing + sep + content)


class MemoryStore(_FileStore):
    INITIAL_CONTENT = """# Project memory
_Durable project-level knowledge._

## Project context
_(What is this project?)_

## Rules
_Hard constraints from user._

## Architecture decisions
_Major design choices._

## Discovered durable knowledge
_Cross-task facts._
"""

    def __init__(self, base_dir: str) -> None:
        super().__init__(base_dir, "MEMORY.md")
        if not os.path.exists(self._path):
            self.write(self.INITIAL_CONTENT)


class CheckpointStore:
    SECTIONS = [
        "active_intent", "next_action", "directives", "task_tree",
        "current_work", "files_and_code", "discovered_knowledge",
        "errors_and_fixes", "live_resources", "design_decisions", "open_notes",
    ]

    SECTION_HEADERS = {
        "active_intent": "## §1 Active intent",
        "next_action": "## §2 Next concrete action",
        "directives": "## §3 Directives",
        "task_tree": "## §4 Task tree",
        "current_work": "## §5 Current work",
        "files_and_code": "## §6 Files and code sections",
        "discovered_knowledge": "## §7 Discovered knowledge",
        "errors_and_fixes": "## §8 Errors and fixes",
        "live_resources": "## §9 Live resources",
        "design_decisions": "## §10 Design decisions",
        "open_notes": "## §11 Open notes",
    }

    def __init__(self, base_dir: str) -> None:
        self._path = os.path.join(base_dir, "checkpoint.md")
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        self._sections: dict[str, str] = {s: "" for s in self.SECTIONS}
        if os.path.exists(self._path):
            self._load()

    def _load(self) -> None:
        content = self.read()
        current_section = None
        current_lines = []
        for line in content.split("\n"):
            for section, header in self.SECTION_HEADERS.items():
                if line.startswith(header):
                    if current_section:
                        self._sections[current_section] = "\n".join(current_lines).strip()
                    current_section = section
                    current_lines = []
                    break
            else:
                current_lines.append(line)
        if current_section:
            self._sections[current_section] = "\n".join(current_lines).strip()

    def read(self) -> str:
        if os.path.exists(self._path):
            with open(self._path, "r", encoding="utf-8") as f:
                return f.read()
        return ""

    def list_sections(self) -> List[str]:
        return list(self.SECTIONS)

    def get_section(self, section: str) -> str:
        return self._sections.get(section, "")

    def update_section(self, section: str, content: str) -> None:
        self._sections[section] = content
        self._save()

    def _save(self) -> None:
        lines = ["# Session checkpoint", ""]
        for section in self.SECTIONS:
            header = self.SECTION_HEADERS[section]
            lines.append(header)
            lines.append("")
            lines.append(self._sections.get(section, ""))
            lines.append("")
        with open(self._path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))


class NotesStore(_FileStore):
    def __init__(self, base_dir: str) -> None:
        super().__init__(base_dir, "notes.md")

    def append(self, content: str) -> None:
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        entry = f"## [turn · {timestamp}]\n{content}"
        super().append(entry)


class TaskProgressStore:
    def __init__(self, base_dir: str) -> None:
        self._base_dir = os.path.join(base_dir, "tasks")
        os.makedirs(self._base_dir, exist_ok=True)

    def _task_dir(self, task_id: str) -> str:
        d = os.path.join(self._base_dir, task_id)
        os.makedirs(d, exist_ok=True)
        return d

    def write_progress(self, task_id: str, content: str) -> None:
        path = os.path.join(self._task_dir(task_id), "progress.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"# Task {task_id} Progress\n\n{content}")

    def read_progress(self, task_id: str) -> str:
        path = os.path.join(self._task_dir(task_id), "progress.md")
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        return ""

    def list_tasks(self) -> List[str]:
        if not os.path.exists(self._base_dir):
            return []
        return [d for d in os.listdir(self._base_dir) if os.path.isdir(os.path.join(self._base_dir, d))]
