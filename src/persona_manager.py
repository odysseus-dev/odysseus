"""
persona_manager.py

Core management for "Personas" (Sub-Agents / Agent Personas).

A Persona is a specialized agent configuration that can have:
- Different tool access (subset or superset of main agent)
- Different skills/memory namespace
- Different default model + temperature
- Custom system prompt / personality
- Specific purpose (Researcher, Coder, Email Assistant, etc.)

This enables the main agent (and users) to delegate tasks to specialized sub-agents.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from src.constants import PERSONAS_DIR, DATA_DIR

logger = logging.getLogger(__name__)

# Default tools that every persona should have access to (safety net)
CORE_TOOLS = {
    "manage_memory",
    "manage_notes",
    "manage_tasks",
    "ui_control",
    "list_sessions",
    "create_session",
}

# Tools that are considered "powerful" and should be explicitly granted
POWER_TOOLS = {
    "bash", "python", "write_file", "edit_file", "manage_documents",
    "manage_skills", "send_email", "bulk_email", "manage_calendar",
    "serve_model", "download_model", "generate_image"
}


@dataclass
class Persona:
    """Represents a single specialized agent persona."""
    name: str                          # unique slug, e.g. "researcher", "coder"
    display_name: str                  # human friendly: "Research Specialist"
    description: str = ""
    category: str = "general"          # research, coding, personal, creative, etc.

    # Model configuration
    model: Optional[str] = None
    temperature: float = 0.4
    max_tokens: int = 0

    # Capabilities
    allowed_tools: List[str] = field(default_factory=list)
    allowed_skills: List[str] = field(default_factory=list)   # skill names or categories
    memory_namespace: Optional[str] = None                    # separate memory bucket

    # Personality / behavior
    system_prompt_addition: str = ""   # extra instructions appended to main prompt
    personality: str = ""              # short description of how this persona behaves

    # Metadata
    source: str = "user"               # user | learned | imported
    status: str = "active"             # active | disabled
    created: str = ""
    updated: str = ""
    uses: int = 0
    last_used: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return {k: v for k, v in d.items() if v not in (None, "", [], {})}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Persona":
        data.setdefault("allowed_tools", [])
        data.setdefault("allowed_skills", [])
        data.setdefault("temperature", 0.4)
        data.setdefault("status", "active")
        return cls(**data)


class PersonaManager:
    """Handles CRUD and retrieval of personas."""

    def __init__(self, data_dir: Optional[str] = None):
        self.data_dir = data_dir or DATA_DIR
        self.personas_dir = os.path.join(self.data_dir, "personas")
        self.index_file = os.path.join(self.personas_dir, "index.json")
        os.makedirs(self.personas_dir, exist_ok=True)
        self._ensure_index()

    def _ensure_index(self):
        if not os.path.exists(self.index_file):
            with open(self.index_file, "w", encoding="utf-8") as f:
                json.dump({"personas": {}}, f, indent=2)

    def _load_index(self) -> Dict[str, Any]:
        try:
            with open(self.index_file, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"personas": {}}

    def _save_index(self, data: Dict[str, Any]):
        with open(self.index_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    # ------------------------------------------------------------------ #
    # CRUD
    # ------------------------------------------------------------------ #
    def list_personas(self, include_disabled: bool = False) -> List[Persona]:
        idx = self._load_index()
        result = []
        for name, meta in idx.get("personas", {}).items():
            if not include_disabled and meta.get("status") == "disabled":
                continue
            p = Persona.from_dict(meta)
            result.append(p)
        result.sort(key=lambda p: (p.last_used or "", p.name), reverse=True)
        return result

    def get_persona(self, name: str) -> Optional[Persona]:
        idx = self._load_index()
        meta = idx.get("personas", {}).get(name)
        if not meta:
            return None
        return Persona.from_dict(meta)

    def save_persona(self, persona: Persona, actor: str = "user") -> Persona:
        now = datetime.utcnow().isoformat() + "Z"
        if not persona.created:
            persona.created = now
        persona.updated = now

        idx = self._load_index()
        idx.setdefault("personas", {})[persona.name] = persona.to_dict()
        self._save_index(idx)

        logger.info(f"[personas] saved persona '{persona.name}' (actor={actor})")
        return persona

    def delete_persona(self, name: str) -> bool:
        idx = self._load_index()
        if name in idx.get("personas", {}):
            del idx["personas"][name]
            self._save_index(idx)
            logger.info(f"[personas] deleted persona '{name}'")
            return True
        return False

    # ------------------------------------------------------------------ #
    # Convenience
    # ------------------------------------------------------------------ #
    def get_or_create_default_personas(self) -> List[Persona]:
        """Seed a few useful default personas if none exist."""
        existing = {p.name for p in self.list_personas(include_disabled=True)}
        created = []

        defaults = [
            Persona(
                name="researcher",
                display_name="Research Specialist",
                description="Deep research, web investigation, synthesis, and report writing.",
                category="research",
                allowed_tools=["web_search", "web_fetch", "read_file", "create_document", "manage_memory"],
                system_prompt_addition="You are a meticulous researcher. Always cite sources when possible and structure your findings clearly.",
                personality="Careful, thorough, evidence-based.",
            ),
            Persona(
                name="coder",
                display_name="Coding Agent",
                description="Software engineering tasks: reading, writing, editing code, debugging, git operations.",
                category="coding",
                allowed_tools=["bash", "python", "read_file", "write_file", "edit_file", "grep", "glob", "ls"],
                system_prompt_addition="You are an expert software engineer. Prefer clean, minimal, well-commented code. Explain your changes.",
                personality="Pragmatic, precise, security-conscious.",
            ),
            Persona(
                name="assistant",
                display_name="Personal Assistant",
                description="Email, calendar, notes, tasks, reminders, and daily coordination.",
                category="personal",
                allowed_tools=["manage_notes", "manage_tasks", "manage_calendar", "list_emails", "read_email", "send_email"],
                system_prompt_addition="You are a reliable personal assistant. Be concise, proactive, and respect user time.",
                personality="Helpful, organized, respectful of boundaries.",
            ),
        ]

        for p in defaults:
            if p.name not in existing:
                self.save_persona(p, actor="system")
                created.append(p)
        return created

    def get_effective_tools(self, persona_name: Optional[str], base_tools: Set[str]) -> Set[str]:
        """Return the actual set of tools a persona should be allowed to use."""
        if not persona_name:
            return base_tools

        p = self.get_persona(persona_name)
        if not p or not p.allowed_tools:
            return base_tools

        allowed = set(p.allowed_tools)
        return base_tools & allowed
