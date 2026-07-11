"""Loop detection: n-gram text repetition, stable tool-call signatures, stall/runaway detection.

Ported from MiMo-Code patterns:
- stableStringify for key-order-independent tool call comparison
- N-gram text repetition detection with progressive recovery levels
- Stall detector for repeated identical tool calls with no text output
- Runaway detector for absurd repetition counts
"""
from __future__ import annotations

import collections
import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Deque, Dict, List, Optional, Sequence, Set


class RecoveryLevel(Enum):
    NONE = "none"
    MILD = "mild"
    STRONG = "strong"


@dataclass(frozen=True, slots=True)
class StableSignature:
    """Key-order-independent tool call signature for loop detection."""
    tool_type: str
    args_hash: str

    @classmethod
    def from_tool_call(cls, tool_type: str, raw_args: str) -> StableSignature:
        try:
            parsed = json.loads(raw_args) if raw_args else {}
            normalized = json.dumps(parsed, sort_keys=True, separators=(",", ":"))
        except (json.JSONDecodeError, TypeError):
            normalized = (raw_args or "").strip()
        return cls(tool_type=tool_type, args_hash=normalized)

    def __str__(self) -> str:
        return f"{self.tool_type}:{self.args_hash[:120]}"


@dataclass
class LoopDetector:
    """Stateful loop detector that tracks text patterns and tool call signatures."""
    max_rounds: int = 12
    stall_threshold: int = 4
    runaway_threshold: int = 15

    _round_count: int = field(default=0, init=False)
    _recent_call_sigs: Deque[str] = field(default_factory=lambda: collections.deque(maxlen=6), init=False)
    _call_freq: collections.Counter = field(default_factory=collections.Counter, init=False)
    _stuck_rounds: int = field(default=0, init=False)
    _text_history: Deque[str] = field(default_factory=lambda: collections.deque(maxlen=10), init=False)
    _force_answer: bool = field(default=False, init=False)

    @property
    def round_count(self) -> int:
        return self._round_count

    @property
    def force_answer(self) -> bool:
        return self._force_answer

    def record_round(self, text: str = "", tool_calls: Optional[Sequence[StableSignature]] = None) -> None:
        self._round_count += 1
        self._text_history.append(text or "")
        if tool_calls:
            sig_str = "|".join(str(s) for s in sorted(tool_calls, key=lambda s: str(s)))
            self._recent_call_sigs.append(sig_str)
            for tc in tool_calls:
                self._call_freq[str(tc)] += 1
            is_repeat = sig_str in list(self._recent_call_sigs)[:-1]
            real_text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.DOTALL | re.IGNORECASE).strip()
            if is_repeat and not real_text:
                self._stuck_rounds += 1
            else:
                self._stuck_rounds = 0
        else:
            self._stuck_rounds = 0

    def check_text_loop(self) -> RecoveryLevel:
        if len(self._text_history) < 3:
            return RecoveryLevel.NONE
        recent = list(self._text_history)[-3:]
        cleaned = [re.sub(r"<think>.*?</think>", "", t, flags=re.DOTALL | re.IGNORECASE).strip() for t in recent]
        def ngrams(text: str, n: int = 3) -> Set[str]:
            words = text.split()
            if len(words) < n:
                return set()
            return {" ".join(words[i:i+n]) for i in range(len(words) - n + 1)}
        prev_grams = ngrams(cleaned[0])
        curr_grams = ngrams(cleaned[-1])
        if not prev_grams or not curr_grams:
            return RecoveryLevel.NONE
        overlap = len(prev_grams & curr_grams) / max(len(prev_grams), 1)
        if overlap > 0.7:
            return RecoveryLevel.STRONG
        elif overlap > 0.4:
            return RecoveryLevel.MILD
        return RecoveryLevel.NONE

    def check_stall(self) -> RecoveryLevel:
        if self._stuck_rounds >= self.stall_threshold:
            return RecoveryLevel.STRONG
        elif self._stuck_rounds >= max(2, self.stall_threshold // 2):
            return RecoveryLevel.MILD
        return RecoveryLevel.NONE

    def is_runaway(self) -> bool:
        for sig, count in self._call_freq.items():
            if count >= self.runaway_threshold:
                self._force_answer = True
                return True
        return False

    def should_force_answer(self) -> bool:
        if self._force_answer:
            return True
        if self._stuck_rounds >= self.stall_threshold:
            self._force_answer = True
            return True
        return False

    def should_stop(self) -> bool:
        return self._round_count >= self.max_rounds

    def reset(self) -> None:
        self._round_count = 0
        self._recent_call_sigs.clear()
        self._call_freq.clear()
        self._stuck_rounds = 0
        self._text_history.clear()
        self._force_answer = False

    def get_disabled_tools_for_recovery(self) -> Optional[List[str]]:
        if not self._force_answer:
            return None
        disabled = []
        for sig, count in self._call_freq.items():
            if count >= 3:
                tool_name = sig.split(":", 1)[0] if ":" in sig else sig
                if tool_name in ("web_search", "bash"):
                    disabled.append(tool_name)
        return disabled or None
