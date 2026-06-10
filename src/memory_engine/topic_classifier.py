"""Topic classification — heuristic by default, LLM opt-in.

Heuristic mode uses Jaccard similarity + keyword overlap for fast,
no-LLM branching.  When enabled via settings, an LLM call produces
{action, parent_id, topic_name} JSON.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Dict, List, Optional, Set

from src.topic_analyzer import TOPIC_KEYWORDS

logger = logging.getLogger(__name__)


def _tokenize(text: str) -> Set[str]:
    return set(word.strip(".,!?\"';:()[]") for word in text.lower().split())


def _jaccard(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    ta = _tokenize(a)
    tb = _tokenize(b)
    if not ta and not tb:
        return 1.0
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _extract_keywords(text: str) -> Set[str]:
    words = set()
    lower = text.lower()
    for topic, kws in TOPIC_KEYWORDS.items():
        for kw in kws:
            if kw in lower:
                words.add(kw)
    return words


class HeuristicClassifier:
    """Fast, local topic branching using Jaccard + keyword overlap."""

    def __init__(self, branch_threshold: float = 0.4):
        self.branch_threshold = branch_threshold

    def classify(
        self,
        text: str,
        current_topic: Dict[str, Any],
        sibling_topics: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Return classification dict: {action, parent_id, topic_name}.

        action is 'continue' (stay on current topic) or 'branch' (new topic).
        """
        current_id = current_topic.get("id")
        current_name = current_topic.get("topic_name", "")
        current_summary = current_topic.get("summary", "")
        current_kw = set(current_topic.get("keywords", []))

        topic_text = f"{current_name} {current_summary}"
        sim = _jaccard(text, topic_text)

        msg_kw = _extract_keywords(text)
        if msg_kw and current_kw:
            overlap = len(msg_kw & current_kw) / max(len(msg_kw), len(current_kw))
            sim = max(sim, overlap)

        if sim >= self.branch_threshold:
            return {
                "action": "continue",
                "parent_id": current_topic.get("parent_id"),
                "topic_name": current_name,
            }

        # Try siblings
        parent_id = current_topic.get("parent_id")
        best_match = None
        best_score = self.branch_threshold
        for sib in sibling_topics:
            if sib.get("id") == current_id:
                continue
            sib_text = f"{sib.get('topic_name', '')} {sib.get('summary', '')}"
            sib_sim = _jaccard(text, sib_text)
            sib_kw = set(sib.get("keywords", []))
            if msg_kw and sib_kw:
                overlap = len(msg_kw & sib_kw) / max(len(msg_kw), len(sib_kw))
                sib_sim = max(sib_sim, overlap)
            if sib_sim > best_score:
                best_score = sib_sim
                best_match = sib.get("id")

        if best_match:
            matched = next(s for s in sibling_topics if s.get("id") == best_match)
            return {
                "action": "continue",
                "parent_id": matched.get("parent_id"),
                "topic_name": matched.get("topic_name", ""),
            }

        # Branch into new topic
        return {
            "action": "branch",
            "parent_id": parent_id,
            "topic_name": self._generate_name(text),
        }

    @staticmethod
    def _generate_name(text: str) -> str:
        matched = _extract_keywords(text)
        if matched:
            return sorted(matched)[0].capitalize()
        words = text.split()[:3]
        return " ".join(words).capitalize() or "New Topic"


class TopicClassifier:
    """Unified classifier: heuristic default, LLM opt-in."""

    def __init__(
        self,
        *,
        use_llm: bool = False,
        llm_caller: Optional[Callable[..., Any]] = None,
        branch_threshold: float = 0.4,
    ):
        self.use_llm = use_llm
        self.llm_caller = llm_caller
        self.heuristic = HeuristicClassifier(branch_threshold=branch_threshold)

    async def classify(
        self,
        text: str,
        current_topic: Dict[str, Any],
        sibling_topics: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        if self.use_llm and self.llm_caller:
            try:
                return await self._llm_classify(text, current_topic, sibling_topics)
            except Exception as e:
                logger.warning("LLM classification failed, falling back to heuristic: %s", e)
        return self.heuristic.classify(text, current_topic, sibling_topics)

    async def _llm_classify(
        self,
        text: str,
        current_topic: Dict[str, Any],
        sibling_topics: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        if not self.llm_caller:
            raise RuntimeError("llm_caller not configured")

        prompt = self._build_prompt(text, current_topic, sibling_topics)
        response = await self.llm_caller(prompt)

        # Expect JSON response
        try:
            if isinstance(response, str):
                data = json.loads(response)
            elif isinstance(response, dict):
                data = response
            else:
                raise ValueError("Unexpected LLM response type")
        except json.JSONDecodeError:
            # Try to extract JSON from markdown fences
            import re
            m = re.search(r"```(?:json)?\s*([\s\S]+?)```", response)
            if m:
                data = json.loads(m.group(1))
            else:
                raise

        action = data.get("action", "continue")
        parent_id = data.get("parent_id", current_topic.get("parent_id"))
        topic_name = data.get("topic_name", current_topic.get("topic_name", ""))

        return {
            "action": action,
            "parent_id": parent_id,
            "topic_name": topic_name,
        }

    @staticmethod
    def _build_prompt(
        text: str,
        current_topic: Dict[str, Any],
        sibling_topics: List[Dict[str, Any]],
    ) -> str:
        siblings = "\n".join(
            f"- {s.get('topic_name')}: {s.get('summary', '')}" for s in sibling_topics[:5]
        )
        prompt = (
            "You are a topic classifier for a conversation memory system.\n"
            "Given the new message and the current conversation context, decide:\n"
            "1. Should this message continue the CURRENT topic?\n"
            "2. Or should it branch to a NEW topic?\n\n"
            f"CURRENT TOPIC: {current_topic.get('topic_name')}\n"
            f"Summary: {current_topic.get('summary', 'N/A')}\n\n"
            f"SIBLING TOPICS:\n{siblings or '(none)'}\n\n"
            f"NEW MESSAGE:\n{text}\n\n"
            "Respond with JSON only:\n"
            '{"action": "continue" | "branch", "parent_id": "...", "topic_name": "..."}'
        )
        return prompt
