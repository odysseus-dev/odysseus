from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class PolicyRule:
    agent: str
    penalty: float = 0.0
    max_retries: int = 3
    cooldown_until: Optional[float] = None


@dataclass
class Policies:
    max_depth: int = 5
    max_tokens: int = 8000
    max_retries: int = 3
    error_threshold: float = 0.2
    deep_plan_threshold: int = 6
    default_model: str = "cantique"

    agent_rules: Dict[str, PolicyRule] = field(default_factory=dict)

    def penalize_agent(self, agent_name: str):
        rule = self.agent_rules.setdefault(agent_name, PolicyRule(agent=agent_name))
        rule.penalty = min(rule.penalty + 0.25, 1.0)

    def reward_agent(self, agent_name: str):
        rule = self.agent_rules.setdefault(agent_name, PolicyRule(agent=agent_name))
        rule.penalty = max(rule.penalty - 0.1, 0.0)

    def describe(self) -> dict:
        return {
            "max_depth": self.max_depth,
            "max_tokens": self.max_tokens,
            "max_retries": self.max_retries,
            "error_threshold": self.error_threshold,
            "default_model": self.default_model,
            "agent_rules": {k: {"penalty": v.penalty, "max_retries": v.max_retries} for k, v in self.agent_rules.items()},
        }
