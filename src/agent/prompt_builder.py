"""Modular prompt assembly for the agent loop.

Extended PromptBuilder that handles both static and dynamic context:
- Static: preamble, tool sections, rules (built once, cached)
- Dynamic: datetime, document, skills, MCP, email, integrations (per-request)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


@dataclass
class PromptSection:
    """A section of the system prompt."""
    id: str
    content: str
    priority: int = 50
    trusted: bool = True
    enabled: bool = True


@dataclass
class PromptBuilder:
    """Assembles system prompt from modular sections.
    
    Supports both static sections (built once, cached) and dynamic context
    (per-request: datetime, document, skills, MCP, email, integrations).
    """
    _sections: List[PromptSection] = field(default_factory=list, init=False)
    _domain_rules: Dict[str, str] = field(default_factory=dict, init=False)
    _disabled_tools: Set[str] = field(default_factory=set, init=False)
    _relevant_tools: Optional[Set[str]] = field(default=None, init=False)
    _compact: bool = field(default=False, init=False)
    _needs_admin: bool = field(default=False, init=False)
    
    # Dynamic context (per-request)
    _datetime_message: Optional[Dict] = field(default=None, init=False)
    _doc_message: Optional[Dict] = field(default=None, init=False)
    _skills_message: Optional[Dict] = field(default=None, init=False)
    _mcp_desc_message: Optional[Dict] = field(default=None, init=False)
    _email_style_message: Optional[Dict] = field(default=None, init=False)
    _integ_message: Optional[Dict] = field(default=None, init=False)

    def add_section(self, section: PromptSection) -> None:
        """Add a static section to the prompt."""
        self._sections.append(section)

    def disable_tools(self, tools: Set[str]) -> None:
        """Disable specific tools from the prompt."""
        self._disabled_tools.update(tools)

    def set_relevant_tools(self, tools: Optional[Set[str]]) -> None:
        """Set the relevant tools for domain rule filtering."""
        self._relevant_tools = tools

    def set_compact(self, compact: bool = True) -> None:
        """Enable compact mode (one-liner tool list for API models)."""
        self._compact = compact

    def set_needs_admin(self, needs_admin: bool = True) -> None:
        """Mark that admin tools should be included."""
        self._needs_admin = needs_admin

    def add_domain_rule(self, domain: str, rule: str) -> None:
        """Add a domain-specific rule."""
        self._domain_rules[domain] = rule

    # ── Dynamic context methods ──

    def set_datetime(self, message: Dict) -> None:
        """Set the datetime context message."""
        self._datetime_message = message

    def set_document(self, message: Dict) -> None:
        """Set the active document context message."""
        self._doc_message = message

    def set_skills(self, message: Dict) -> None:
        """Set the skills context message."""
        self._skills_message = message

    def set_mcp_descriptions(self, message: Dict) -> None:
        """Set the MCP tool descriptions message."""
        self._mcp_desc_message = message

    def set_email_style(self, message: Dict) -> None:
        """Set the email writing style message."""
        self._email_style_message = message

    def set_integrations(self, message: Dict) -> None:
        """Set the integrations context message."""
        self._integ_message = message

    # ── Build methods ──

    def build(self) -> str:
        """Build the system prompt string (trusted sections only)."""
        system_prompt, _ = self.build_with_untrusted()
        return system_prompt

    def build_with_untrusted(self) -> tuple:
        """Build system prompt and separate untrusted messages.
        
        Returns:
            (system_prompt: str, untrusted_messages: List[Dict])
        """
        trusted = []
        untrusted = []

        # Sort sections by priority (highest first)
        for section in sorted(self._sections, key=lambda s: -s.priority):
            if not section.enabled:
                continue
            if section.id in self._disabled_tools or any(
                tool.startswith(section.id) for tool in self._disabled_tools
            ):
                continue
            if not section.trusted:
                untrusted.append({"role": "user", "content": section.content})
                continue
            trusted.append(section.content)

        # Add domain rules
        if self._relevant_tools is not None and self._domain_rules:
            for domain, rule in self._domain_rules.items():
                if self._is_domain_active(domain):
                    trusted.append(rule)

        # Build system prompt
        system_prompt = "\n\n".join(trusted)

        # Collect dynamic context messages
        dynamic = []
        if self._datetime_message:
            dynamic.append(self._datetime_message)
        if self._doc_message:
            dynamic.append(self._doc_message)
        if self._email_style_message:
            dynamic.append(self._email_style_message)
        if self._integ_message:
            dynamic.append(self._integ_message)
        if self._mcp_desc_message:
            dynamic.append(self._mcp_desc_message)
        if self._skills_message:
            dynamic.append(self._skills_message)

        return system_prompt, dynamic + untrusted

    def _is_domain_active(self, domain: str) -> bool:
        """Check if a domain's tools are in the relevant set."""
        if self._relevant_tools is None:
            return True
        return any(tool.startswith(domain) for tool in self._relevant_tools)

    def reset_dynamic(self) -> None:
        """Reset dynamic context for next request."""
        self._datetime_message = None
        self._doc_message = None
        self._skills_message = None
        self._mcp_desc_message = None
        self._email_style_message = None
        self._integ_message = None
