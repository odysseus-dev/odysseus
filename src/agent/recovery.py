"""Recovery prompts and intent supervisor for the agent loop."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Set

from src.agent.loop_detector import RecoveryLevel


class RecoveryPrompts:
    """Static recovery prompt templates with progressive severity."""

    @staticmethod
    def text_loop(level: RecoveryLevel) -> str:
        if level == RecoveryLevel.MILD:
            return (
                "You appear to be repeating similar text across rounds. "
                "Take a different approach: summarize what you've found so far, "
                "then either proceed to the next step or give your best answer "
                "from the information already gathered. Do not repeat the same "
                "phrasing or reasoning."
            )
        elif level == RecoveryLevel.STRONG:
            return (
                "STOP. You are stuck in a text repetition loop. You have already "
                "stated the same thing multiple times. End this turn NOW by either: "
                "(a) giving your final answer based on information already gathered, "
                "or (b) stating in ONE sentence what is blocking you. Do not "
                "restate plans, do not re-explain context, do not use tools."
            )
        return ""

    @staticmethod
    def stall(level: RecoveryLevel) -> str:
        if level == RecoveryLevel.STRONG:
            return (
                "You're executing the same tool calls repeatedly without making "
                "progress or writing any text. STOP calling tools. Write your "
                "best answer now from the information already gathered, or state "
                "plainly what is blocking you."
            )
        elif level == RecoveryLevel.MILD:
            return (
                "The same tool calls are being repeated. Try a different approach "
                "or write an answer from what you've already found."
            )
        return ""

    @staticmethod
    def runaway(tool_name: str) -> str:
        return (
            f"You have called `{tool_name}` with identical arguments many times "
            f"in a row. This is a runaway loop. STOP calling tools immediately. "
            f"Write your best answer from the information already gathered."
        )

    @staticmethod
    def force_answer(disabled_tools: Optional[List[str]] = None) -> str:
        msg = (
            "You're repeating tool calls without converging. STOP calling "
            "tools and end the turn one of two ways: (a) write your best "
            "final answer NOW from the information already gathered, or "
            "(b) if you're genuinely blocked, say plainly what's blocking "
            "you in a sentence or two."
        )
        if disabled_tools:
            names = ", ".join(disabled_tools)
            msg += f" ({names} is currently disabled — say so if you needed it.)"
        return msg

    @staticmethod
    def intent_without_action(matched_phrase: str, cookbook_hint: str = "") -> str:
        msg = (
            f'You just wrote: "{matched_phrase}" — but ended the '
            "turn without making the actual tool call. The user can "
            "see you announced the action but didn't run it, which "
            "is the most frustrating thing you can do. "
            "DO IT NOW: emit the actual function call this turn. "
        )
        if cookbook_hint:
            msg += cookbook_hint + " "
        msg += (
            "If you decided not to do it after all, say so plainly in "
            "one sentence instead of restating the plan."
        )
        return msg


@dataclass
class IntentSupervisor:
    """Catches 'I will check...' followed by no tool call."""
    max_nudges: int = 2
    _nudge_count: int = field(default=0, init=False)

    _INTENT_RE: re.Pattern = field(default_factory=lambda: re.compile(
        r"(?:^|\n)\s*(?:let me|i'?ll|i will|i need to|we need to|need to|"
        r"i should|we should|i must|we must|going to|let's)\s+"
        r"(?:tail|check|investigate|look at|see|read|fetch|inspect|"
        r"verify|diagnose|examine|debug|capture|grab|pull|view|run|call|"
        r"trigger|launch|start|kick off|stop|kill|restart|adopt|serve|"
        r"register|adopt|list|search|find|query|hit|ping|test|use|perform|do)"
        r"\b[^.\n]{0,140}",
        re.IGNORECASE,
    ))

    _THINK_RE: re.Pattern = field(default_factory=lambda: re.compile(
        r"<think>.*?</think>", re.DOTALL | re.IGNORECASE
    ))

    def detect(self, text: str) -> bool:
        cleaned = self._THINK_RE.sub("", text or "").strip()
        if len(cleaned) >= 400:
            return False
        if "```" in cleaned:
            return False
        return bool(self._INTENT_RE.search(cleaned))

    def should_nudge(self) -> bool:
        return self._nudge_count < self.max_nudges

    def nudge(self) -> None:
        self._nudge_count += 1

    def reset(self) -> None:
        self._nudge_count = 0
