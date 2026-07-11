"""Local, append-only passive memory for Misumi Phase A."""

from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from src.constants import DATA_DIR


PERSONAS = (
    "kurisu", "aoteru", "lelouch", "ichigo", "ginko", "sanji", "l",
    "jin", "misato", "giorno", "erwin",
)
CAPSULE_TYPES = {
    "observation", "decision", "inventory", "blocker", "preference",
    "open_loop", "experiment_result", "note",
}
CAPSULE_STATUSES = {"open", "confirmed", "routed", "closed"}
HANDOFF_STATUSES = {"pending", "resolved"}
FORBIDDEN_HANDOFF_ACTIONS = re.compile(
    r"\b(send|email|calendar|notify|webhook|pay|buy|transfer|message|post|tweet|call)\b",
    re.IGNORECASE,
)

ROUTING_KEYWORDS = {
    "kurisu": ("raw", "capture", "uncertain", "uncertainty", "evidence", "source"),
    "aoteru": ("route", "routing", "coherence", "meta-task", "coordinate"),
    "lelouch": ("implement", "implementation", "deploy", "wire up code", "ship", "code"),
    "ichigo": ("hardware", "solder", "wiring", "wired", "safety", "fix", "maintenance", "mpu6050", "gy-521", "interface box", "offline"),
    "ginko": ("sensor", "plant", "humidity", "damp", "environment"),
    "sanji": ("food", "recipe", "shopping", "cook", "ingredient", "acid"),
    "l": ("budget", "receipt", "subscription", "cost", "bank"),
    "jin": ("music", "record", "gig", "vinyl"),
    "misato": ("routine", "rota", "cleaning", "capacity"),
    "giorno": ("experiment", "pilot", "trial"),
    "erwin": ("priority", "risk", "deadline", "cost-of-delay"),
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _new_id(prefix: str) -> str:
    stamp = int(datetime.now(timezone.utc).timestamp())
    return f"{prefix}-{stamp}-{uuid.uuid4().hex[:6]}"


def _normalise_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def summarize(raw: str) -> Tuple[str, float]:
    """Return a conservative extractive summary and deterministic confidence."""
    clean = _normalise_whitespace(raw)
    sentence = re.split(r"(?<=[.!?])\s+", clean, maxsplit=1)[0]
    if len(sentence) > 140:
        sentence = sentence[:140].rstrip()
    lower = clean.lower()
    confidence = 0.6 if "remember" in lower or "we decided" in lower else 0.4
    return sentence, confidence


def classify(raw: str) -> str:
    lower = _normalise_whitespace(raw).lower()
    if "remember" in lower:
        return "note"
    if "this worked but" in lower or "worked but needed" in lower:
        return "experiment_result"
    if any(phrase in lower for phrase in ("doesn't work", "broken", "offline", "blocked", "failed")):
        return "blocker"
    if any(phrase in lower for phrase in ("still need to", "now for", "todo", "next step", "all wired up, now")):
        return "open_loop"
    if "i bought" in lower or "ordered" in lower or re.search(r"\b\d+\s+[A-Za-z]*\d[A-Za-z0-9-]*", raw):
        return "inventory"
    if "we decided" in lower or "decided to" in lower:
        return "decision"
    if any(phrase in lower for phrase in ("prefer", "i like", "always use")):
        return "preference"
    return "observation"


def route(raw: str, entities: Iterable[str] = ()) -> Tuple[str, Optional[str]]:
    haystack = " ".join((raw, *[str(entity) for entity in entities])).lower()
    scores = {
        persona: sum(1 for keyword in keywords if keyword in haystack)
        for persona, keywords in ROUTING_KEYWORDS.items()
    }
    best = max(scores.values(), default=0)
    if best == 0:
        return "kurisu", None
    ranked = sorted(
        (persona for persona, score in scores.items() if score > 0),
        key=lambda persona: (-scores[persona], 0 if persona == "kurisu" else 1, PERSONAS.index(persona)),
    )
    primary = ranked[0]
    secondary = ranked[1] if len(ranked) > 1 else None
    return primary, secondary


def detect_open_loop(raw: str, capsule_type: str) -> bool:
    lower = _normalise_whitespace(raw).lower()
    phrases = (
        "all wired up, now for implementation", "still need to", "doesn't work",
        "blocked", "i bought", "remember", "worked but", "we decided",
    )
    return capsule_type in {"open_loop", "blocker"} or any(phrase in lower for phrase in phrases)


def _loop_text(raw: str, capsule_type: str) -> str:
    lower = _normalise_whitespace(raw).lower()
    if "i bought" in lower:
        return "Put away or verify the purchased item."
    if "remember" in lower:
        return "Review and confirm the remembered note."
    if "we decided" in lower:
        return "Apply the recorded decision."
    if "this worked but" in lower or "worked but needed" in lower:
        return "Review the remaining experiment adjustment."
    if capsule_type == "blocker" or any(term in lower for term in ("doesn't work", "blocked", "offline", "broken", "failed")):
        return "Investigate and resolve the blocker."
    if "now for" in lower:
        return _normalise_whitespace(raw)[lower.index("now for") + len("now for"):].strip(" .") or "Complete the next implementation step."
    if "still need to" in lower:
        return _normalise_whitespace(raw)[lower.index("still need to") + len("still need to"):].strip(" .") or "Complete the remaining action."
    return "Complete the captured open loop."


class MisumiMemory:
    """Append-only JSONL stores with latest-record folding."""

    def __init__(
        self,
        root: Optional[Path | str] = None,
        model_refiner: Optional[Callable[[Dict[str, object]], Dict[str, object]]] = None,
    ):
        self.root = Path(root) if root is not None else Path(DATA_DIR) / "misumi" / "memory"
        self.model_refiner = model_refiner

    def _path(self, name: str) -> Path:
        return self.root / f"{name}.jsonl"

    def _append(self, name: str, record: Dict[str, object]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        with self._path(name).open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
            handle.flush()

    def _fold(self, name: str) -> Tuple[List[Dict[str, object]], int]:
        path = self._path(name)
        if not path.is_file():
            return [], 0
        folded: Dict[str, Dict[str, object]] = {}
        corrupt = 0
        with path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    record = json.loads(line)
                    if not isinstance(record, dict) or not isinstance(record.get("id"), str):
                        raise ValueError("invalid record")
                    folded[record["id"]] = record
                except (json.JSONDecodeError, ValueError, TypeError):
                    corrupt += 1
        return list(folded.values()), corrupt

    @staticmethod
    def _persona(value: object) -> str:
        persona = str(value or "").strip().lower()
        if persona not in PERSONAS:
            raise ValueError(f"Unknown persona: {value}")
        return persona

    def capture(
        self,
        text: str,
        *,
        source: str = "chat",
        capsule_type: Optional[str] = None,
        persona: Optional[str] = None,
        entities: Optional[Iterable[str]] = None,
        next_action: Optional[str] = None,
        meta: Optional[Dict[str, object]] = None,
    ) -> Dict[str, object]:
        if not isinstance(text, str) or not text.strip():
            raise ValueError("text must be non-empty")
        selected_type = str(capsule_type or classify(text)).strip().lower()
        if selected_type not in CAPSULE_TYPES:
            raise ValueError(f"Unknown capsule type: {selected_type}")
        entity_list = [str(item) for item in (entities or [])]
        inferred_primary, inferred_secondary = route(text, entity_list)
        primary = self._persona(persona) if persona is not None else inferred_primary
        secondary = inferred_secondary if inferred_secondary != primary else None
        summary, confidence = summarize(text)
        now = utc_now()
        record: Dict[str, object] = {
            "id": _new_id("cap"), "created": now, "updated": now,
            "raw_text": text, "summary": summary, "type": selected_type,
            "confidence": confidence, "source": str(source or "chat"),
            "persona_primary": primary, "persona_secondary": secondary,
            "entities": entity_list, "next_action": next_action,
            "status": "open", "human_confirmed": False, "meta": dict(meta or {}),
        }
        if "remember" in text.lower():
            record["meta"]["human_confirmation_suggested"] = True
        if self.model_refiner:
            refined = self.model_refiner(dict(record)) or {}
            if isinstance(refined.get("summary"), str) and refined["summary"].strip():
                record["summary"] = refined["summary"].strip()
            if refined.get("persona_primary") is not None:
                record["persona_primary"] = self._persona(refined["persona_primary"])
            if "persona_secondary" in refined:
                record["persona_secondary"] = (
                    self._persona(refined["persona_secondary"])
                    if refined["persona_secondary"] is not None else None
                )
            if record["persona_secondary"] == record["persona_primary"]:
                record["persona_secondary"] = None
        self._append("capsules", record)
        if detect_open_loop(text, selected_type):
            loop = {
                "id": _new_id("loop"), "capsule_id": record["id"],
                "text": next_action or _loop_text(text, selected_type), "owner": record["persona_primary"],
                "created": now, "updated": now, "status": "open",
            }
            self._append("open_loops", loop)
        return record

    def capsules(self) -> Tuple[List[Dict[str, object]], int]:
        return self._fold("capsules")

    def loops(self) -> Tuple[List[Dict[str, object]], int]:
        records, corrupt = self._fold("open_loops")
        try:
            stale_hours = max(0.0, float(os.getenv("MISUMI_MEMORY_STALE_HOURS", "72")))
        except ValueError:
            stale_hours = 72.0
        now = datetime.now(timezone.utc)
        result = []
        for item in records:
            copy = dict(item)
            try:
                updated = datetime.fromisoformat(str(item["updated"]).replace("Z", "+00:00"))
                copy["stale"] = item.get("status") == "open" and (now - updated).total_seconds() > stale_hours * 3600
            except (KeyError, TypeError, ValueError):
                copy["stale"] = False
            result.append(copy)
        return result, corrupt

    def handoffs(self) -> Tuple[List[Dict[str, object]], int]:
        return self._fold("handoffs")

    def _latest(self, store: str, record_id: str) -> Dict[str, object]:
        records, _ = self._fold(store)
        found = next((item for item in records if item.get("id") == record_id), None)
        if not found:
            raise KeyError(record_id)
        return found

    def update_capsule(self, capsule_id: str, **changes: object) -> Dict[str, object]:
        record = dict(self._latest("capsules", capsule_id))
        record.update(changes)
        record["updated"] = utc_now()
        if record.get("status") not in CAPSULE_STATUSES:
            raise ValueError("Invalid capsule status")
        self._append("capsules", record)
        return record

    def confirm(self, capsule_id: str) -> Dict[str, object]:
        return self.update_capsule(capsule_id, human_confirmed=True, status="confirmed")

    def reroute(self, capsule_id: str, primary: str, secondary: Optional[str] = None) -> Dict[str, object]:
        selected_primary = self._persona(primary)
        selected_secondary = self._persona(secondary) if secondary is not None else None
        if selected_secondary == selected_primary:
            raise ValueError("Primary and secondary personas must differ")
        routed = self.update_capsule(
            capsule_id, persona_primary=selected_primary,
            persona_secondary=selected_secondary, status="routed",
        )
        loops, _ = self._fold("open_loops")
        now = utc_now()
        for loop in loops:
            if loop.get("capsule_id") == capsule_id and loop.get("status") == "open":
                changed = dict(loop)
                changed.update({"owner": selected_primary, "updated": now})
                self._append("open_loops", changed)
        return routed

    def close(self, capsule_id: str, resolution: Optional[str] = None) -> Dict[str, object]:
        record = self._latest("capsules", capsule_id)
        meta = dict(record.get("meta") or {})
        if resolution:
            meta["resolution"] = resolution
        closed = self.update_capsule(capsule_id, status="closed", meta=meta)
        loops, _ = self._fold("open_loops")
        now = utc_now()
        for loop in loops:
            if loop.get("capsule_id") == capsule_id and loop.get("status") == "open":
                changed = dict(loop)
                changed.update({"status": "closed", "updated": now})
                self._append("open_loops", changed)
        return closed

    def create_handoff(
        self, from_persona: str, to_persona: str, action: str,
        capsule_id: Optional[str] = None, note: Optional[str] = None,
    ) -> Dict[str, object]:
        source = self._persona(from_persona)
        target = self._persona(to_persona)
        action_text = str(action or "").strip()
        if not action_text:
            raise ValueError("action must be non-empty")
        if len(action_text) > 240:
            raise ValueError("action must be short")
        if FORBIDDEN_HANDOFF_ACTIONS.search(action_text):
            raise ValueError("handoff action requests a forbidden outbound side effect")
        if capsule_id is not None:
            self._latest("capsules", capsule_id)
        now = utc_now()
        record = {
            "id": _new_id("handoff"), "from_persona": source, "to_persona": target,
            "capsule_id": capsule_id, "action": action_text, "note": str(note or ""),
            "created": now, "updated": now, "status": "pending",
        }
        self._append("handoffs", record)
        return record

    def resolve_handoff(self, handoff_id: str) -> Dict[str, object]:
        record = dict(self._latest("handoffs", handoff_id))
        record.update({"status": "resolved", "updated": utc_now()})
        self._append("handoffs", record)
        return record

    def glance(self) -> Dict[str, object]:
        capsules, capsule_corrupt = self.capsules()
        loops, loop_corrupt = self.loops()
        handoffs, handoff_corrupt = self.handoffs()
        newest = max(capsules, key=lambda item: str(item.get("created", "")), default=None)
        open_loops = [item for item in loops if item.get("status") == "open"]
        open_loops.sort(key=lambda item: (not bool(item.get("stale")), str(item.get("created", ""))))
        pending = sorted(
            (item for item in handoffs if item.get("status") == "pending"),
            key=lambda item: str(item.get("created", "")),
        )
        top_loop = open_loops[0] if open_loops else None
        next_action = top_loop.get("text") if top_loop else pending[0].get("action") if pending else None
        owner = top_loop.get("owner") if top_loop else pending[0].get("to_persona") if pending else None
        return {
            "newest_capture": newest,
            "inbox_count": sum(1 for item in capsules if item.get("status") == "open" and not item.get("human_confirmed")),
            "open_loop_count": len(open_loops),
            "stale_loop_count": sum(1 for item in open_loops if item.get("stale")),
            "pending_handoff_count": len(pending),
            "top_open_loop": top_loop,
            "next_recommended_action": next_action,
            "responsible_persona": owner,
            "writes_allowed": False,
            "corrupt_lines": capsule_corrupt + loop_corrupt + handoff_corrupt,
        }
