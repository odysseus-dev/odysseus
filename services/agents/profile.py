"""Persona/agent profile loading + owner-id derivation. Multiagent slice-1.

Spec: docs/superpowers/specs/2026-06-12-odysseus-multiagent-orchestration-design.md.

- Persona = reusable identity: ``data/personas/<name>/SOUL.md``
  (+ optional ``meta.json {description}``). No tools, no memory, inert.
- Agent = binding: ``data/agents/<name>/agent.json``
  ``{persona, tools, model}`` (+ optional ``skills`` from the platform
  profile compiler).
- Owner id is DERIVED at spawn, never stored:
  ``agent:{human or "local"}/{name}`` — internal-only identity, disjoint
  from human usernames by prefix.
"""
import json
import re
from pathlib import Path
from typing import Optional


class ProfileError(ValueError):
    pass


def _data_dir(data_dir: Optional[str | Path]) -> Path:
    if data_dir:
        return Path(data_dir)
    from src.constants import DATA_DIR
    return Path(DATA_DIR)


def load_persona(name: str, data_dir: Optional[str | Path] = None) -> dict:
    """Load a persona: {"name", "soul", "description"}."""
    root = _data_dir(data_dir) / "personas" / name
    soul_path = root / "SOUL.md"
    if not soul_path.is_file():
        raise ProfileError(f"persona {name!r} not found ({soul_path})")
    description = ""
    meta_path = root / "meta.json"
    if meta_path.is_file():
        try:
            description = json.loads(meta_path.read_text(encoding="utf-8")) \
                              .get("description", "")
        except (ValueError, AttributeError):
            description = ""
    return {"name": name,
            "soul": soul_path.read_text(encoding="utf-8"),
            "description": description}


def load_agent(name: str, data_dir: Optional[str | Path] = None) -> dict:
    """Load an agent binding: {"name", "persona", "tools", "skills", "model"}."""
    path = _data_dir(data_dir) / "agents" / name / "agent.json"
    if not path.is_file():
        raise ProfileError(f"agent {name!r} not found ({path})")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as e:
        raise ProfileError(f"agent {name!r}: invalid agent.json: {e}")
    if not isinstance(raw, dict) or not raw.get("persona"):
        raise ProfileError(f"agent {name!r}: agent.json needs a persona")
    tools = raw.get("tools")
    if not isinstance(tools, list):
        raise ProfileError(f"agent {name!r}: agent.json needs a tools list")
    return {"name": name, "persona": raw["persona"], "tools": list(tools),
            "skills": list(raw.get("skills") or []),
            "model": raw.get("model")}


_OWNER_NAME_RE = re.compile(r"[\w.-]+")


def derive_owner(human: Optional[str], agent_name: str) -> str:
    """``agent:{human or "local"}/{agent_name}`` — derived, never stored.

    The human owner is inherited at spawn and never elevated; deriving from
    an already-derived agent id is refused so nested spawns cannot mint
    identities under a different or synthetic human.
    """
    human = human or "local"
    if human.startswith("agent:"):
        raise ProfileError(
            f"cannot derive an owner from agent id {human!r}; "
            "pass the inheriting HUMAN owner")
    if not _OWNER_NAME_RE.fullmatch(agent_name.replace("/", "")):
        raise ProfileError(f"invalid agent name {agent_name!r}")
    return f"agent:{human}/{agent_name}"


def resolve_binding(entry: dict, human: Optional[str],
                    data_dir: Optional[str | Path] = None) -> dict:
    """Resolve one spawn_agent entry to a concrete binding.

    Entry references a stored agent (``{"agent": name}``) OR an inline
    binding (``{"persona": name, "tools": [...]}``). Returns
    {"name", "owner", "soul", "description", "tools", "skills", "model",
    "task", "persist"}.
    """
    if not isinstance(entry, dict):
        raise ProfileError("spawn entry must be a mapping")
    task = (entry.get("task") or "").strip()
    if not task:
        raise ProfileError("spawn entry needs a non-empty task")

    if entry.get("agent"):
        agent = load_agent(entry["agent"], data_dir=data_dir)
        persona = load_persona(agent["persona"], data_dir=data_dir)
        name, tools = agent["name"], agent["tools"]
        skills, model = agent["skills"], agent["model"]
    elif entry.get("persona"):
        persona = load_persona(entry["persona"], data_dir=data_dir)
        name = entry["persona"]
        tools = entry.get("tools")
        if not isinstance(tools, list) or not tools:
            raise ProfileError(
                "inline binding needs a non-empty tools list")
        skills, model = list(entry.get("skills") or []), entry.get("model")
    else:
        raise ProfileError(
            "spawn entry needs either a stored 'agent' name or an inline "
            "'persona' binding")

    return {
        "name": name,
        "owner": derive_owner(human, name),
        "soul": persona["soul"],
        "description": persona["description"],
        "tools": list(tools),
        "skills": skills,
        "model": model,
        "task": task,
        "persist": bool(entry.get("persist", False)),
    }
