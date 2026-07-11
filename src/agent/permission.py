"""Permission system — rule-based allow/deny/ask with pattern matching."""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional, Set


class Action(Enum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


@dataclass(frozen=True)
class Rule:
    permission: str
    pattern: str
    action: Action


Ruleset = List[Rule]


FORCED_ASK: Set[str] = {
    "bash_delete",
}


def evaluate(permission: str, pattern: str, ruleset: Ruleset) -> Rule:
    matching = [
        rule for rule in ruleset
        if fnmatch.fnmatch(permission, rule.permission)
        and fnmatch.fnmatch(pattern, rule.pattern)
    ]
    if matching:
        return matching[-1]
    permission_rules = [
        rule for rule in ruleset
        if fnmatch.fnmatch(permission, rule.permission)
    ]
    if permission_rules:
        return Rule(permission=permission, pattern=pattern, action=Action.ALLOW)
    return Rule(permission=permission, pattern="*", action=Action.ASK)


def merge_rulesets(*rulesets: Ruleset) -> Ruleset:
    result = []
    for rs in rulesets:
        result.extend(rs)
    return result


def disabled_tools(tools: List[str], ruleset: Ruleset) -> Set[str]:
    result = set()
    for tool in tools:
        rule = evaluate(tool, "*", ruleset)
        if rule.action == Action.DENY:
            result.add(tool)
    return result


AGENT_PERMISSIONS: dict[str, Ruleset] = {
    "build": [
        Rule(permission="*", pattern="*", action=Action.ALLOW),
    ],
    "plan": [
        Rule(permission="*", pattern="*", action=Action.ALLOW),
        Rule(permission="write_file", pattern="*", action=Action.DENY),
        Rule(permission="edit_file", pattern="*", action=Action.DENY),
        Rule(permission="bash", pattern="*", action=Action.DENY),
    ],
    "explore": [
        Rule(permission="*", pattern="*", action=Action.DENY),
        Rule(permission="read_file", pattern="*", action=Action.ALLOW),
        Rule(permission="ls", pattern="*", action=Action.ALLOW),
        Rule(permission="glob", pattern="*", action=Action.ALLOW),
        Rule(permission="grep", pattern="*", action=Action.ALLOW),
        Rule(permission="web_search", pattern="*", action=Action.ALLOW),
        Rule(permission="web_fetch", pattern="*", action=Action.ALLOW),
    ],
    "compose": [
        Rule(permission="*", pattern="*", action=Action.ALLOW),
    ],
    "general": [
        Rule(permission="*", pattern="*", action=Action.ALLOW),
        Rule(permission="manage_session", pattern="*", action=Action.DENY),
    ],
}
