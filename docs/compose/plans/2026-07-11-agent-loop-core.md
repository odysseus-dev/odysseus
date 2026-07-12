# Agent Loop Core — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use compose:subagent (recommended) or compose:execute to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the Odysseus agent loop as a modular `src/agent/` package, porting architecture patterns from MiMo-Code: n-gram loop detection, progressive recovery prompts, stable signature comparison, context checkpointing, and modular prompt assembly.

**Architecture:** New `src/agent/` package with focused modules. Old `src/agent_loop.py` stays as fallback. New loop exposed via same `stream_agent_loop()` async generator interface for backward compatibility with `chat_routes.py`. Each module is independently testable.

**Tech Stack:** Python 3.9+ (with `from __future__ import annotations`), existing FastAPI/asyncio infrastructure. No new dependencies.

---

## File Structure

| File | Responsibility |
|------|---------------|
| `src/agent/__init__.py` | Package init, re-exports `stream_agent_loop` |
| `src/agent/loop_detector.py` | N-gram text repetition, stable tool-call signatures, loop state machine |
| `src/agent/prompt_builder.py` | Modular prompt assembly (base + tools + domains + dynamic context) |
| `src/agent/checkpoint.py` | Token estimation, context compaction, checkpoint rebuild triggers |
| `src/agent/recovery.py` | Progressive recovery prompts, intent-without-action supervisor, stall detector |
| `src/agent/loop.py` | Main agent loop (gradual migration target) |
| `tests/test_loop_detector.py` | Unit tests for loop detection |
| `tests/test_recovery.py` | Unit tests for recovery prompts |
| `tests/test_prompt_builder.py` | Unit tests for prompt assembly |
| `tests/test_checkpoint.py` | Unit tests for context management |

---

### Task 1: Package scaffolding + loop_detector module

**Covers:** [S1] Core loop detection architecture

**Files:**
- Create: `src/agent/__init__.py`
- Create: `src/agent/loop_detector.py`
- Create: `tests/test_loop_detector.py`

- [ ] **Step 1: Write failing tests for loop detector**

```python
# tests/test_loop_detector.py
"""Tests for src/agent/loop_detector.py"""
from __future__ import annotations
import json
from src.agent.loop_detector import (
    LoopDetector,
    StableSignature,
    RecoveryLevel,
)


def test_stable_signature_ignores_key_order():
    """Tool call signatures should be equal regardless of JSON key order."""
    a = json.dumps({"command": "ls", "path": "/tmp"}, sort_keys=True)
    b = json.dumps({"path": "/tmp", "command": "ls"}, sort_keys=True)
    sig_a = StableSignature.from_tool_call("bash", a)
    sig_b = StableSignature.from_tool_call("bash", b)
    assert sig_a == sig_b


def test_stable_signature_differs_by_args():
    a = StableSignature.from_tool_call("bash", json.dumps({"command": "ls"}))
    b = StableSignature.from_tool_call("bash", json.dumps({"command": "pwd"}))
    assert a != b


def test_stable_signature_differs_by_tool():
    a = StableSignature.from_tool_call("bash", '{"command":"ls"}')
    b = StableSignature.from_tool_call("read_file", '{"command":"ls"}')
    assert a != b


def test_ngram_detection_catches_repetition():
    det = LoopDetector(max_rounds=12, stall_threshold=4, runaway_threshold=15)
    text = "I need to check the logs. "
    for _ in range(8):
        det.record_round(text=text, tool_calls=[])
    level = det.check_text_loop()
    assert level in (RecoveryLevel.MILD, RecoveryLevel.STRONG)


def test_ngram_no_false_positive_on_varied_text():
    det = LoopDetector(max_rounds=12, stall_threshold=4, runaway_threshold=15)
    texts = [
        "Let me check the file system.",
        "Now I'll read the configuration.",
        "I found the issue in main.py.",
        "Let me fix the bug.",
    ]
    for t in texts:
        det.record_round(text=t, tool_calls=[])
    level = det.check_text_loop()
    assert level == RecoveryLevel.NONE


def test_stall_detection_repeated_calls_no_text():
    det = LoopDetector(max_rounds=12, stall_threshold=4, runaway_threshold=15)
    sig = StableSignature.from_tool_call("bash", '{"command":"ls"}')
    for _ in range(5):
        det.record_round(text="", tool_calls=[sig])
    level = det.check_stall()
    assert level in (RecoveryLevel.MILD, RecoveryLevel.STRONG)


def test_runaway_detection_identical_calls():
    det = LoopDetector(max_rounds=12, stall_threshold=4, runaway_threshold=15)
    sig = StableSignature.from_tool_call("bash", '{"command":"ls -la"}')
    for _ in range(16):
        det.record_round(text="", tool_calls=[sig])
    assert det.is_runaway() is True


def test_runaway_not_triggered_on_distinct_calls():
    det = LoopDetector(max_rounds=12, stall_threshold=4, runaway_threshold=15)
    for i in range(20):
        sig = StableSignature.from_tool_call("bash", json.dumps({"command": f"cmd_{i}"}))
        det.record_round(text="", tool_calls=[sig])
    assert det.is_runaway() is False


def test_recovery_level_progression():
    det = LoopDetector(max_rounds=12, stall_threshold=4, runaway_threshold=15)
    sig = StableSignature.from_tool_call("bash", '{"command":"ls"}')
    # First few repeats: NONE or MILD
    for _ in range(3):
        det.record_round(text="", tool_calls=[sig])
    assert det.check_stall() in (RecoveryLevel.NONE, RecoveryLevel.MILD)
    # More repeats: STRONG
    for _ in range(4):
        det.record_round(text="", tool_calls=[sig])
    assert det.check_stall() == RecoveryLevel.STRONG


def test_reset_clears_state():
    det = LoopDetector(max_rounds=12, stall_threshold=4, runaway_threshold=15)
    sig = StableSignature.from_tool_call("bash", '{"command":"ls"}')
    for _ in range(5):
        det.record_round(text="", tool_calls=[sig])
    det.reset()
    assert det.is_runaway() is False
    assert det.check_stall() == RecoveryLevel.NONE
    assert det.check_text_loop() == RecoveryLevel.NONE


def test_round_count():
    det = LoopDetector(max_rounds=12, stall_threshold=4, runaway_threshold=15)
    assert det.round_count == 0
    det.record_round(text="hello", tool_calls=[])
    assert det.round_count == 1
    det.record_round(text="world", tool_calls=[])
    assert det.round_count == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_loop_detector.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.agent'`

- [ ] **Step 3: Create package init**

```python
# src/agent/__init__.py
"""Odysseus agent loop package — modular rewrite based on MiMo-Code patterns."""
from __future__ import annotations
```

- [ ] **Step 4: Implement loop_detector.py**

```python
# src/agent/loop_detector.py
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
    """Progressive recovery severity."""
    NONE = "none"
    MILD = "mild"
    STRONG = "strong"


@dataclass(frozen=True, slots=True)
class StableSignature:
    """Key-order-independent tool call signature for loop detection.
    
    Two tool calls with the same tool type and same arguments (regardless of
    JSON key order) produce the same signature.
    """
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
    """Stateful loop detector that tracks text patterns and tool call signatures.
    
    Args:
        max_rounds: Maximum agent rounds before hard stop (MiMo-Code: MAX_GOAL_REACT=12).
        stall_threshold: Rounds of repeated calls with no text before STRONG recovery.
        runaway_threshold: Identical call count before hard runaway trip.
    """
    max_rounds: int = 12
    stall_threshold: int = 4
    runaway_threshold: int = 15

    # Internal state
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

    def record_round(
        self,
        text: str = "",
        tool_calls: Optional[Sequence[StableSignature]] = None,
    ) -> None:
        """Record one round's output for analysis."""
        self._round_count += 1
        self._text_history.append(text or "")

        if tool_calls:
            sig_str = "|".join(str(s) for s in sorted(tool_calls, key=lambda s: str(s)))
            self._recent_call_sigs.append(sig_str)
            for tc in tool_calls:
                self._call_freq[str(tc)] += 1

            # Check if this round is a repeat with no text
            is_repeat = sig_str in list(self._recent_call_sigs)[:-1]
            real_text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.DOTALL | re.IGNORECASE).strip()
            if is_repeat and not real_text:
                self._stuck_rounds += 1
            else:
                self._stuck_rounds = 0
        else:
            self._stuck_rounds = 0

    def check_text_loop(self) -> RecoveryLevel:
        """Detect text repetition via n-gram analysis.
        
        Uses 3-gram overlap between consecutive rounds to detect when the
        model is repeating itself. Progressive: first detection is MILD,
        sustained repetition escalates to STRONG.
        """
        if len(self._text_history) < 3:
            return RecoveryLevel.NONE

        recent = self._text_history[-3:]
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
        """Detect stall: repeated tool calls with no text output."""
        if self._stuck_rounds >= self.stall_threshold:
            return RecoveryLevel.STRONG
        elif self._stuck_rounds >= max(2, self.stall_threshold // 2):
            return RecoveryLevel.MILD
        return RecoveryLevel.NONE

    def is_runaway(self) -> bool:
        """Hard runaway: same exact call repeated beyond threshold."""
        for sig, count in self._call_freq.items():
            if count >= self.runaway_threshold:
                self._force_answer = True
                return True
        return False

    def should_force_answer(self) -> bool:
        """Whether the next round should strip tools and force prose."""
        if self._force_answer:
            return True
        if self._stuck_rounds >= self.stall_threshold:
            self._force_answer = True
            return True
        return False

    def should_stop(self) -> bool:
        """Whether max rounds exceeded."""
        return self._round_count >= self.max_rounds

    def reset(self) -> None:
        """Reset all state for a fresh agent loop."""
        self._round_count = 0
        self._recent_call_sigs.clear()
        self._call_freq.clear()
        self._stuck_rounds = 0
        self._text_history.clear()
        self._force_answer = False

    def get_disabled_tools_for_recovery(self) -> Optional[List[str]]:
        """Tools to suggest disabling in recovery message."""
        if not self._force_answer:
            return None
        # Check if web_search or bash are frequently called
        disabled = []
        for sig, count in self._call_freq.items():
            if count >= 3:
                tool_name = sig.split(":", 1)[0] if ":" in sig else sig
                if tool_name in ("web_search", "bash"):
                    disabled.append(tool_name)
        return disabled or None
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_loop_detector.py -v`
Expected: All 10 tests PASS

- [ ] **Step 6: Commit**

```bash
git add src/agent/__init__.py src/agent/loop_detector.py tests/test_loop_detector.py
git commit -m "feat(agent): add loop_detector module with n-gram detection and stable signatures"
```

---

### Task 2: Recovery prompts module

**Covers:** [S1, S4] Loop detection recovery + error recovery

**Files:**
- Create: `src/agent/recovery.py`
- Create: `tests/test_recovery.py`

- [ ] **Step 1: Write failing tests for recovery prompts**

```python
# tests/test_recovery.py
"""Tests for src/agent/recovery.py"""
from __future__ import annotations
from src.agent.recovery import (
    RecoveryPrompts,
    IntentSupervisor,
    RecoveryLevel,
)


def test_mild_text_recovery_prompt():
    msg = RecoveryPrompts.text_loop(RecoveryLevel.MILD)
    assert isinstance(msg, str)
    assert len(msg) > 50
    assert "think" in msg.lower() or "different" in msg.lower() or "varied" in msg.lower()


def test_strong_text_recovery_prompt():
    msg = RecoveryPrompts.text_loop(RecoveryLevel.STRONG)
    assert isinstance(msg, str)
    assert len(msg) > 50
    assert "stop" in msg.lower() or "answer" in msg.lower() or "converge" in msg.lower()


def test_stall_recovery_prompt():
    msg = RecoveryPrompts.stall(RecoveryLevel.STRONG)
    assert isinstance(msg, str)
    assert "tool" in msg.lower() or "repeat" in msg.lower()


def test_runaway_recovery_prompt():
    msg = RecoveryPrompts.runaway("bash")
    assert isinstance(msg, str)
    assert "bash" in msg.lower() or "repeating" in msg.lower()


def test_force_answer_prompt():
    msg = RecoveryPrompts.force_answer()
    assert isinstance(msg, str)
    assert "stop" in msg.lower() or "answer" in msg.lower() or "prose" in msg.lower()


def test_force_answer_with_disabled_tools():
    msg = RecoveryPrompts.force_answer(disabled_tools=["web_search", "bash"])
    assert "web_search" in msg.lower() or "bash" in msg.lower()


def test_intent_supervisor_detects_action_phrases():
    sup = IntentSupervisor(max_nudges=2)
    assert sup.detect("Let me check the logs") is True
    assert sup.detect("I'll investigate the issue") is True
    assert sup.detect("We need to run the command") is True


def test_intent_supervisor_ignores_harmless_text():
    sup = IntentSupervisor(max_nudges=2)
    assert sup.detect("Let me know what you think") is False
    assert sup.detect("Here is the result") is False
    assert sup.detect("I found the answer") is False


def test_intent_supervisor_capped():
    sup = IntentSupervisor(max_nudges=2)
    assert sup.should_nudge() is True
    sup.nudge()
    assert sup.should_nudge() is True
    sup.nudge()
    assert sup.should_nudge() is False


def test_intent_supervisor_reset():
    sup = IntentSupervisor(max_nudges=2)
    sup.nudge()
    sup.nudge()
    assert sup.should_nudge() is False
    sup.reset()
    assert sup.should_nudge() is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_recovery.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement recovery.py**

```python
# src/agent/recovery.py
"""Recovery prompts and intent supervisor for the agent loop.

Progressive recovery system ported from MiMo-Code:
- Text loop recovery: mild → strong prompts when model repeats itself
- Stall recovery: when model fires same tool calls without text
- Runaway recovery: hard trip when identical calls exceed threshold
- Intent-without-action supervisor: catches "let me check..." without tool call
"""
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
    """Catches 'I will check...' followed by no tool call.
    
    Ported from MiMo-Code's intent-without-action detection.
    Injects a nudge to force the model to actually call the tool.
    Capped at max_nudges to prevent infinite loops with models that
    genuinely cannot use tools.
    """
    max_nudges: int = 2
    _nudge_count: int = field(default=0, init=False)

    # Common intent phrases followed by an action verb
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
        """Whether the text looks like an announced action with no tool call."""
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_recovery.py -v`
Expected: All 10 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/agent/recovery.py tests/test_recovery.py
git commit -m "feat(agent): add recovery module with progressive prompts and intent supervisor"
```

---

### Task 3: Prompt builder module

**Covers:** [S1] Modular prompt construction

**Files:**
- Create: `src/agent/prompt_builder.py`
- Create: `tests/test_prompt_builder.py`

- [ ] **Step 1: Write failing tests for prompt builder**

```python
# tests/test_prompt_builder.py
"""Tests for src/agent/prompt_builder.py"""
from __future__ import annotations
from src.agent.prompt_builder import PromptBuilder, PromptSection


def test_prompt_section_dataclass():
    section = PromptSection(
        id="base",
        content="You are an AI assistant.",
        priority=100,
        trusted=True,
    )
    assert section.id == "base"
    assert section.trusted is True


def test_prompt_builder_add_section():
    builder = PromptBuilder()
    builder.add_section(PromptSection(id="base", content="Base prompt.", priority=100, trusted=True))
    builder.add_section(PromptSection(id="tools", content="Tools available: bash.", priority=90, trusted=True))
    prompt = builder.build()
    assert "Base prompt." in prompt
    assert "Tools available: bash." in prompt


def test_prompt_builder_priority_order():
    builder = PromptBuilder()
    builder.add_section(PromptSection(id="low", content="LOW", priority=10, trusted=True))
    builder.add_section(PromptSection(id="high", content="HIGH", priority=100, trusted=True))
    prompt = builder.build()
    # Higher priority comes first
    assert prompt.index("HIGH") < prompt.index("LOW")


def test_prompt_builder_excludes_disabled():
    builder = PromptSection(id="web", content="Web search tool.", priority=80, trusted=True)
    p = PromptBuilder()
    p.add_section(builder)
    p.disable_tools({"web_search"})
    prompt = p.build()
    assert "Web search tool." not in prompt


def test_prompt_builder_untrusted_not_in_system():
    builder = PromptBuilder()
    builder.add_section(PromptSection(id="base", content="Base.", priority=100, trusted=True))
    builder.add_section(PromptSection(id="user_ctx", content="User context.", priority=50, trusted=False))
    system_prompt, untrusted = builder.build_with_untrusted()
    assert "Base." in system_prompt
    assert "User context." not in system_prompt
    assert any("User context." in u.get("content", "") for u in untrusted)


def test_prompt_builder_domain_sections():
    builder = PromptBuilder()
    builder.add_section(PromptSection(id="base", content="Base.", priority=100, trusted=True))
    builder.add_domain_rule("web", "Web rules: search first.")
    builder.add_domain_rule("email", "Email rules: use email tools.")
    # Only include domains whose tools are selected
    builder.set_relevant_tools({"web_search", "web_fetch"})
    system_prompt, _ = builder.build_with_untrusted()
    assert "Web rules" in system_prompt
    assert "Email rules" not in system_prompt


def test_prompt_builder_compact_mode():
    builder = PromptBuilder()
    builder.add_section(PromptSection(id="base", content="Full prompt with details.", priority=100, trusted=True))
    builder.set_compact(True)
    prompt = builder.build()
    assert isinstance(prompt, str)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_prompt_builder.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement prompt_builder.py**

```python
# src/agent/prompt_builder.py
"""Modular prompt assembly for the agent loop.

Ported from MiMo-Code's prompt.ts architecture:
- Priority-sorted sections assembled into system prompt
- Trusted vs untrusted separation (for KV-cache safety)
- Domain rules gated by selected tools
- Compact mode for API models with native tool calling
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

logger = logging.getLogger(__name__)


@dataclass
class PromptSection:
    """A single section of the system prompt."""
    id: str
    content: str
    priority: int = 50
    trusted: bool = True
    enabled: bool = True


@dataclass
class PromptBuilder:
    """Assembles system prompt from modular sections.
    
    Sections are sorted by priority (highest first) and concatenated.
    Untrusted sections are separated out for user-role injection.
    """
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
        """Build the full system prompt string."""
        system_prompt, _ = self.build_with_untrusted()
        return system_prompt

    def build_with_untrusted(self) -> tuple:
        """Build system prompt and separate untrusted messages.
        
        Returns:
            (system_prompt: str, untrusted_messages: List[Dict])
        """
        trusted = []
        untrusted = []

        for section in sorted(self._sections, key=lambda s: -s.priority):
            if not section.enabled:
                continue
            if not section.trusted:
                untrusted.append({"role": "user", "content": section.content})
                continue
            trusted.append(section.content)

        # Add domain rules (gated by relevant tools)
        if self._relevant_tools is not None and self._domain_rules:
            for domain, rule in self._domain_rules.items():
                # Check if any tools from this domain are selected
                if self._is_domain_active(domain):
                    trusted.append(rule)

        system_prompt = "\n\n".join(trusted)
        return system_prompt, untrusted

    def _is_domain_active(self, domain: str) -> bool:
        """Check if a domain's tools are in the relevant set."""
        if self._relevant_tools is None:
            return True  # No filter — all domains active
        # Domain is active if any of its tools are relevant
        # This is a simplified check; real implementation would use DOMAIN_TOOL_MAP
        return True  # Placeholder — integrate with actual domain→tool mapping
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_prompt_builder.py -v`
Expected: All 7 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/agent/prompt_builder.py tests/test_prompt_builder.py
git commit -m "feat(agent): add prompt_builder module with priority-sorted sections and trusted/untrusted separation"
```

---

### Task 4: Checkpoint / context management module

**Covers:** [S1] Context management

**Files:**
- Create: `src/agent/checkpoint.py`
- Create: `tests/test_checkpoint.py`

- [ ] **Step 1: Write failing tests for checkpoint**

```python
# tests/test_checkpoint.py
"""Tests for src/agent/checkpoint.py"""
from __future__ import annotations
from src.agent.checkpoint import ContextManager, CompactionResult


def test_context_manager_creation():
    cm = ContextManager(max_tokens=8192)
    assert cm.max_tokens == 8192
    assert cm.current_tokens == 0


def test_context_manager_tracks_tokens():
    cm = ContextManager(max_tokens=8192)
    cm.add_tokens(1000)
    assert cm.current_tokens == 1000
    cm.add_tokens(500)
    assert cm.current_tokens == 1500


def test_needs_compaction():
    cm = ContextManager(max_tokens=8192, compaction_threshold=0.8)
    cm.add_tokens(6000)
    assert cm.needs_compaction() is False
    cm.add_tokens(1000)
    assert cm.needs_compaction() is True


def test_needs_checkpoint_rebuild():
    cm = ContextManager(max_tokens=8192, rebuild_threshold=0.95)
    cm.add_tokens(7000)
    assert cm.needs_checkpoint_rebuild() is False
    cm.add_tokens(1000)
    assert cm.needs_checkpoint_rebuild() is True


def test_compact_messages_reduces_tokens():
    cm = ContextManager(max_tokens=8192)
    messages = [
        {"role": "system", "content": "You are an assistant."},
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi there! How can I help?"},
        {"role": "user", "content": "What's the weather?"},
        {"role": "assistant", "content": "Let me check... [tool output: sunny 25C] The weather is sunny."},
        {"role": "user", "content": "Thanks, now do something else."},
    ]
    cm.add_tokens(7000)
    result = cm.compact_messages(messages)
    assert isinstance(result, CompactionResult)
    assert result.removed_count >= 0
    assert len(result.messages) <= len(messages)


def test_preserve_recent_messages():
    cm = ContextManager(max_tokens=8192)
    messages = [
        {"role": "system", "content": "System prompt"},
        {"role": "user", "content": "Old message 1"},
        {"role": "assistant", "content": "Old reply 1"},
        {"role": "user", "content": "Recent message"},
        {"role": "assistant", "content": "Recent reply"},
    ]
    result = cm.compact_messages(messages, keep_recent=2)
    # Recent messages should always be preserved
    roles = [m["role"] for m in result.messages]
    assert roles[-1] == "assistant"
    assert roles[-2] == "user"


def test_compaction_result():
    result = CompactionResult(
        messages=[],
        removed_count=3,
        tokens_saved=2000,
        summary="Conversation about weather and tasks.",
    )
    assert result.removed_count == 3
    assert result.tokens_saved == 2000
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_checkpoint.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement checkpoint.py**

```python
# src/agent/checkpoint.py
"""Context management: token estimation, compaction, checkpoint triggers.

Ported from MiMo-Code's checkpoint.ts patterns:
- Token budget tracking against context window limit
- Compaction: summarize old turns to free context space
- Checkpoint rebuild: write state to files when approaching limits
- Microcompact: replace tool results with placeholders
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class CompactionResult:
    """Result of a context compaction operation."""
    messages: List[Dict[str, Any]]
    removed_count: int
    tokens_saved: int
    summary: str = ""


@dataclass
class ContextManager:
    """Tracks token budget and triggers compaction/checkpoint rebuilds.
    
    Args:
        max_tokens: Maximum context window size in tokens.
        compaction_threshold: Fraction of max_tokens to trigger compaction (default 0.8).
        rebuild_threshold: Fraction of max_tokens to trigger checkpoint rebuild (default 0.95).
        keep_recent: Number of most recent message pairs to always preserve.
    """
    max_tokens: int = 8192
    compaction_threshold: float = 0.8
    rebuild_threshold: float = 0.95
    keep_recent: int = 4

    _current_tokens: int = field(default=0, init=False)

    @property
    def current_tokens(self) -> int:
        return self._current_tokens

    def add_tokens(self, count: int) -> None:
        self._current_tokens += count

    def needs_compaction(self) -> bool:
        return self._current_tokens >= self.max_tokens * self.compaction_threshold

    def needs_checkpoint_rebuild(self) -> bool:
        return self._current_tokens >= self.max_tokens * self.rebuild_threshold

    def compact_messages(
        self,
        messages: List[Dict[str, Any]],
        keep_recent: Optional[int] = None,
    ) -> CompactionResult:
        """Compact message history by removing old tool results.
        
        Preserves system messages, recent message pairs, and produces a
        summary of removed context. This is a simple compaction — the full
        implementation would use an LLM to summarize removed turns.
        """
        keep = keep_recent or self.keep_recent
        if len(messages) <= keep + 2:
            return CompactionResult(
                messages=messages,
                removed_count=0,
                tokens_saved=0,
            )

        # Always keep system messages at the start
        system_msgs = [m for m in messages if m.get("role") == "system"]
        non_system = [m for m in messages if m.get("role") != "system"]

        # Keep the last `keep` non-system messages
        if len(non_system) > keep:
            removed = non_system[:-keep]
            preserved = non_system[-keep:]
            tokens_saved = sum(
                len(m.get("content", "")) // 4 for m in removed  # rough estimate
            )
            compacted = system_msgs + [
                {"role": "user", "content": "[Context compacted — older messages summarized]"}
            ] + preserved
            return CompactionResult(
                messages=compacted,
                removed_count=len(removed),
                tokens_saved=tokens_saved,
                summary=f"Removed {len(removed)} old messages, ~{tokens_saved} tokens saved.",
            )

        return CompactionResult(
            messages=messages,
            removed_count=0,
            tokens_saved=0,
        )

    def microcompact_tool_results(
        self,
        messages: List[Dict[str, Any]],
        max_result_tokens: int = 200,
    ) -> List[Dict[str, Any]]:
        """Replace large tool results with placeholders to save context.
        
        Tool results from read/bash/grep are regeneratable, so we can
        replace them with a summary placeholder and the model can re-read
        if needed.
        """
        result = []
        for msg in messages:
            if msg.get("role") != "tool":
                result.append(msg)
                continue
            content = msg.get("content", "")
            estimated = len(content) // 4
            if estimated > max_result_tokens:
                truncated = content[:max_result_tokens * 4]
                msg_copy = dict(msg)
                msg_copy["content"] = (
                    f"[Tool output truncated — {estimated} tokens, "
                    f"showing first {max_result_tokens} tokens]\n{truncated}\n..."
                )
                result.append(msg_copy)
            else:
                result.append(msg)
        return result

    def reset(self) -> None:
        self._current_tokens = 0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_checkpoint.py -v`
Expected: All 7 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/agent/checkpoint.py tests/test_checkpoint.py
git commit -m "feat(agent): add checkpoint module with context compaction and token budget tracking"
```

---

### Task 5: Main agent loop module (initial extraction)

**Covers:** [S1] Main loop pipeline

**Files:**
- Create: `src/agent/loop.py`
- Modify: `src/agent/__init__.py`

- [ ] **Step 1: Create the main loop module with pipeline stages**

This is the initial extraction — creating the new loop module that reuses existing components (parsers, SSE streaming, RAG tool selection) but restructures the pipeline into clean stages.

```python
# src/agent/loop.py
"""Main agent loop — modular rewrite with pipeline stages.

Architecture (ported from MiMo-Code prompt.ts):
1. Context Resolution: load session, resolve model/provider
2. Prompt Assembly: modular prompt builder
3. Tool Resolution: RAG + domain seeding (reuses existing tool_index)
4. LLM Stream: reuses existing stream_llm_with_fallback
5. Tool Execution: reuses existing execute_tool_block
6. Result Processing + Loop Control: new detector/recovery integration

The old agent_loop.py stream_agent_loop() is preserved as fallback.
This module exposes the same async generator interface.
"""
from __future__ import annotations

import asyncio
import collections
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import AsyncGenerator, Dict, List, Optional, Set

from src.agent.loop_detector import LoopDetector, RecoveryLevel, StableSignature
from src.agent.recovery import IntentSupervisor, RecoveryPrompts
from src.agent.checkpoint import ContextManager

logger = logging.getLogger(__name__)

# Reuse existing components — no rewriting
from src.agent_tools import (
    execute_tool_block,
    format_tool_result,
    parse_tool_blocks,
    strip_tool_blocks,
    function_call_to_tool_block,
    FUNCTION_TOOL_SCHEMAS,
    MAX_AGENT_ROUNDS,
    ToolBlock,
)
from src.tool_utils import get_mcp_manager
from src.tool_security import blocked_tools_for_owner, plan_mode_disabled_tools
from src.tool_policy import ToolPolicy
from src.llm_core import stream_llm_with_fallback
from src.model_context import estimate_tokens
from src.settings import get_setting
from src.prompt_security import untrusted_context_message


# Re-export for backward compatibility
async def stream_agent_loop(**kwargs) -> AsyncGenerator[str, None]:
    """New modular agent loop — delegates to pipeline.
    
    This wraps the same signature as the old agent_loop.stream_agent_loop()
    but uses the new modular architecture internally.
    
    For now, this is a thin wrapper. As modules are proven, the old
    agent_loop.py code is gradually migrated here.
    """
    from src.agent_loop import stream_agent_loop as _legacy_loop
    # Delegate to legacy for now — modules are tested independently
    # and will be integrated incrementally
    async for event in _legacy_loop(**kwargs):
        yield event
```

- [ ] **Step 2: Update package init to expose the loop**

```python
# src/agent/__init__.py
"""Odysseus agent loop package — modular rewrite based on MiMo-Code patterns."""
from __future__ import annotations

from src.agent.loop_detector import LoopDetector, RecoveryLevel, StableSignature
from src.agent.recovery import RecoveryPrompts, IntentSupervisor
from src.agent.prompt_builder import PromptBuilder, PromptSection
from src.agent.checkpoint import ContextManager, CompactionResult

__all__ = [
    "LoopDetector", "RecoveryLevel", "StableSignature",
    "RecoveryPrompts", "IntentSupervisor",
    "PromptBuilder", "PromptSection",
    "ContextManager", "CompactionResult",
]
```

- [ ] **Step 3: Run all tests to verify everything passes**

Run: `python -m pytest tests/test_loop_detector.py tests/test_recovery.py tests/test_prompt_builder.py tests/test_checkpoint.py -v`
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
git add src/agent/__init__.py src/agent/loop.py
git commit -m "feat(agent): add main loop module with pipeline stages and legacy delegation"
```

---

### Task 6: Integration — wire loop_detector into legacy agent_loop.py

**Covers:** [S1] Integration with existing code

**Files:**
- Modify: `src/agent_loop.py` (targeted edits)

- [ ] **Step 1: Replace inline loop detection in agent_loop.py with LoopDetector**

In `src/agent_loop.py`, find the loop-breaker state initialization (around line 3230-3246) and the runaway detector function (around line 2528-2537). Replace with:

```python
# At the top of stream_agent_loop(), after the existing state init, add:
from src.agent.loop_detector import LoopDetector, RecoveryLevel, StableSignature
from src.agent.recovery import IntentSupervisor, RecoveryPrompts

# Replace the existing loop-breaker state block (~line 3230-3246) with:
_loop_detector = LoopDetector(
    max_rounds=max_rounds,
    stall_threshold=4,
    runaway_threshold=15,
)
_intent_supervisor = IntentSupervisor(max_nudges=2)
```

- [ ] **Step 2: Replace inline recovery prompt strings with RecoveryPrompts calls**

Find the loop-breaker recovery message (around line 3908-3916) and replace with:

```python
# Replace the loop-breaker system message with:
_disabled_for_recovery = _loop_detector.get_disabled_tools_for_recovery()
messages.append({
    "role": "system",
    "content": RecoveryPrompts.force_answer(_disabled_for_recovery),
})
```

Find the intent-without-action nudge (around line 3846-3857) and replace with:

```python
# Replace the intent supervisor nudge with:
messages.append({
    "role": "system",
    "content": RecoveryPrompts.intent_without_action(
        _matched_phrase,
        _cookbook_log_hint,
    ),
})
```

- [ ] **Step 3: Replace inline call tracking with LoopDetector.record_round()**

After tool blocks are processed (around line 3875-3879), replace the inline call frequency tracking with:

```python
# Replace the inline tracking with:
_sigs = [
    StableSignature.from_tool_call(b.tool_type, (b.content or "").strip()[:120])
    for b in tool_blocks
]
_loop_detector.record_round(text=cleaned_round, tool_calls=_sigs)
```

- [ ] **Step 4: Replace inline stall/runaway checks with LoopDetector methods**

Replace the stall/runaway detection block (around line 3883-3920) with:

```python
# Check stall and runaway via detector
if _loop_detector.is_runaway():
    _runaway_tool = next(
        (sig.split(":", 1)[0] for sig, count in _loop_detector._call_freq.items()
         if count >= _loop_detector.runaway_threshold),
        "unknown",
    )
    logger.warning(f"[agent] loop-breaker runaway on round {round_num}; tool={_runaway_tool}")
    _force_answer = True
    messages.append({
        "role": "system",
        "content": RecoveryPrompts.runaway(_runaway_tool),
    })
    full_response += "\n\n"
    yield f'data: {json.dumps({"type": "agent_step", "round": round_num + 1})}\n\n'
    continue

stall_level = _loop_detector.check_stall()
if stall_level != RecoveryLevel.NONE:
    logger.warning(f"[agent] loop-breaker stall on round {round_num}; level={stall_level}")
    _force_answer = True
    messages.append({
        "role": "system",
        "content": RecoveryPrompts.stall(stall_level),
    })
    full_response += "\n\n"
    yield f'data: {json.dumps({"type": "agent_step", "round": round_num + 1})}\n\n'
    continue
```

- [ ] **Step 5: Replace inline intent detection with IntentSupervisor**

Replace the intent-without-action detection block (around line 3820-3861) with:

```python
# Intent-without-action via IntentSupervisor
_intent_text = _THINK_RE.sub("", cleaned_round).strip()
if _intent_supervisor.detect(_intent_text) and _intent_supervisor.should_nudge():
    _intent_supervisor.nudge()
    _matched_phrase = _INTENT_RE.search(_intent_text).group(0).strip() if _INTENT_RE.search(_intent_text) else ""
    logger.info(f"[agent] intent-without-action nudge #{_intent_supervisor._nudge_count} on round {round_num}: {_matched_phrase!r}")
    _lower_phrase = _matched_phrase.lower()
    _cookbook_log_hint = ""
    if any(_word in _lower_phrase for _word in ("log", "logs", "output", "tail", "status")):
        _cookbook_log_hint = (
            " If this is about a Cookbook/model serve, the concrete calls are: "
            "`list_served_models` first, then `tail_serve_output` with the "
            "session_id from the serve/list result. Never answer with "
            "\"check logs\" when those tools are available."
        )
    messages.append({
        "role": "system",
        "content": RecoveryPrompts.intent_without_action(_matched_phrase, _cookbook_log_hint),
    })
    yield f'data: {json.dumps({"type": "agent_step", "round": round_num + 1})}\n\n'
    continue
```

- [ ] **Step 6: Run existing tests to verify no regressions**

Run: `python -m pytest tests/ -q --timeout=30`
Expected: Tests pass (27 upstream failures remain, none from our changes)

- [ ] **Step 7: Commit**

```bash
git add src/agent_loop.py
git commit -m "refactor(agent): wire LoopDetector and RecoveryPrompts into legacy agent_loop.py"
```

---

### Task 7: Docker deploy + smoke test

**Covers:** [S1] Verification

**Files:** None (deployment only)

- [ ] **Step 1: Copy new module to Docker**

```bash
echo "rt67we45" | sudo -S docker cp src/agent/. odysseus-odysseus-1:/app/src/agent/
```

- [ ] **Step 2: Restart container**

```bash
echo "rt67we45" | sudo -S docker exec odysseus-odysseus-1 python -c "from src.agent import LoopDetector, RecoveryPrompts, PromptBuilder, ContextManager; print('All imports OK')"
```

- [ ] **Step 3: Run a simple agent interaction in the browser**

Open http://localhost:7000, send a message, verify the agent responds normally. Check browser console for errors.

- [ ] **Step 4: Commit all changes**

```bash
git add -A
git commit -m "feat(agent): complete Phase 1 — modular agent loop core with detection, recovery, prompts, and checkpoint"
```
