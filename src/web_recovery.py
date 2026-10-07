"""Execution budget for short web lookups, independent of model prose."""
from dataclasses import dataclass, field
import re


@dataclass
class WebRecoveryBudget:
    searches: set[str] = field(default_factory=set)
    fetches: int = 0
    browsers: int = 0

    def admit(self, tool: str, query: str = "") -> bool:
        if tool == "web_search":
            key = " ".join(sorted(re.findall(r"\w+", query.casefold())))
            if not key or key in self.searches or len(self.searches) >= 2:
                return False
            self.searches.add(key)
        elif tool == "web_fetch":
            if self.fetches >= 1:
                return False
            self.fetches += 1
        elif tool == "private_browser":
            # Browser is the third stage; permit a bounded open/read/follow-up
            # sequence rather than treating navigation alone as a full visit.
            if self.browsers >= 3 or len(self.searches) < 2:
                return False
            self.browsers += 1
        return True

    def instruction(self) -> str:
        if len(self.searches) < 2:
            return (
                "If the evidence does not answer the question, use one materially different "
                "web_search query. Choose the query yourself from the conversation."
            )
        if self.browsers < 3:
            return (
                "The two-search budget is exhausted. If evidence is still insufficient, "
                "inspect a promising source with web_fetch or use private_browser for "
                "rendered browsing. Respect tool permissions; do not invent source content."
            )
        return "The web recovery budget is exhausted. Answer from available evidence and state any gaps."
