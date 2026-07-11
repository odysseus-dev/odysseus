"""Modular prompt assembly for the agent loop."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

logger = logging.getLogger(__name__)


@dataclass
class PromptSection:
    id: str
    content: str
    priority: int = 50
    trusted: bool = True
    enabled: bool = True


@dataclass
class PromptBuilder:
    _sections: List[PromptSection] = field(default_factory=list, init=False)
    _domain_rules: Dict[str, str] = field(default_factory=dict, init=False)
    _disabled_tools: Set[str] = field(default_factory=set, init=False)
    _relevant_tools: Optional[Set[str]] = field(default=None, init=False)
    _compact: bool = field(default=False, init=False)
    _needs_admin: bool = field(default=False, init=False)

    def add_section(self, section: PromptSection) -> None:
        self._sections.append(section)

    def disable_tools(self, tools: Set[str]) -> None:
        self._disabled_tools.update(tools)

    def set_relevant_tools(self, tools: Optional[Set[str]]) -> None:
        self._relevant_tools = tools

    def set_compact(self, compact: bool) -> None:
        self._compact = compact

    def set_needs_admin(self, needs_admin: bool) -> None:
        self._needs_admin = needs_admin

    def add_domain_rule(self, domain: str, rule: str) -> None:
        self._domain_rules[domain] = rule

    def build(self) -> str:
        system_prompt, _ = self.build_with_untrusted()
        return system_prompt

    def build_with_untrusted(self) -> tuple:
        trusted = []
        untrusted = []
        for section in sorted(self._sections, key=lambda s: -s.priority):
            if not section.enabled:
                continue
            if section.id in self._disabled_tools or any(tool.startswith(section.id) for tool in self._disabled_tools):
                continue
            if not section.trusted:
                untrusted.append({"role": "user", "content": section.content})
                continue
            trusted.append(section.content)
        if self._relevant_tools is not None and self._domain_rules:
            for domain, rule in self._domain_rules.items():
                if self._is_domain_active(domain):
                    trusted.append(rule)
        system_prompt = "\n\n".join(trusted)
        return system_prompt, untrusted

    def _is_domain_active(self, domain: str) -> bool:
        if self._relevant_tools is None:
            return True
        return any(tool.startswith(domain) for tool in self._relevant_tools)