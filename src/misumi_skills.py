"""First-party Misumi skill catalog and external-skill security review."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional

from services.memory.skill_format import Skill
from src.constants import BASE_DIR
from src.misumi_policy import normalize_persona, persona_record


SEED_ROOT = Path(BASE_DIR) / "skills" / "misumi"
SUSPICIOUS_PATTERNS: Mapping[str, re.Pattern[str]] = {
    "pipe-to-shell": re.compile(r"\b(?:curl|wget)\b[^\n|]{0,300}\|\s*(?:sh|bash|zsh)\b", re.I),
    "powershell-download-cradle": re.compile(r"(?:invoke-webrequest|iwr|downloadstring|downloadfile|new-object\s+net\.webclient)", re.I),
    "credential-access": re.compile(r"(?:credential|password|secret|api[_ -]?key|token)[^\n]{0,80}(?:read|open|dump|steal|collect)", re.I),
    "browser-cookie-access": re.compile(r"(?:cookies?|login data).{0,80}(?:chrome|browser|sqlite)", re.I),
    "private-key-access": re.compile(r"(?:id_rsa|id_ed25519|\.ssh|private key|wallet)", re.I),
    "obfuscated-base64": re.compile(r"(?:base64\s+-d|b64decode|frombase64string)", re.I),
    "hidden-network-call": re.compile(r"(?:requests\.(?:get|post)|httpx\.|urllib\.request|fetch\()[^\n]{0,160}", re.I),
    "universal-trigger": re.compile(r"(?:always use this skill|use for every|all requests|every task)", re.I),
    "filesystem-mutation": re.compile(r"(?:rm\s+-rf|remove-item|shutil\.rmtree|write_text\(|write_bytes\(|set-content|add-content)", re.I),
    "package-side-effect": re.compile(r"(?:pip|npm|winget|apt(?:-get)?|brew)\s+install", re.I),
}


def seed_catalog() -> List[Dict[str, object]]:
    skills: List[Dict[str, object]] = []
    if not SEED_ROOT.is_dir():
        return skills
    for path in sorted(SEED_ROOT.glob("*/*/SKILL.md")):
        try:
            skill = Skill.from_markdown(path.read_text(encoding="utf-8"), path=str(path))
        except Exception:
            continue
        persona = path.relative_to(SEED_ROOT).parts[0]
        item = skill.to_dict()
        item.update({"persona": persona, "first_party": True, "path": str(path)})
        skills.append(item)
    return skills


def skills_for_persona(persona: object, installed: Optional[Iterable[Dict[str, object]]] = None) -> List[Dict[str, object]]:
    name = normalize_persona(persona)
    categories = set(persona_record(name).get("allowed_skill_categories") or [])
    combined = list(seed_catalog())
    for skill in installed or []:
        item = dict(skill)
        item.setdefault("first_party", False)
        combined.append(item)
    visible = []
    seen = set()
    for skill in combined:
        category = str(skill.get("category") or "general")
        if skill.get("persona") not in (None, name) and skill.get("first_party"):
            continue
        if category not in categories:
            continue
        key = str(skill.get("name") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        visible.append(skill)
    return visible


def security_review_files(files: Mapping[str, str]) -> Dict[str, object]:
    flags = []
    for path, content in files.items():
        text = str(content or "")
        for rule, pattern in SUSPICIOUS_PATTERNS.items():
            match = pattern.search(text)
            if match:
                flags.append({"rule": rule, "path": str(path), "excerpt": match.group(0)[:160]})
    rules = sorted({flag["rule"] for flag in flags})
    risk = "high" if any(rule in rules for rule in ("pipe-to-shell", "credential-access", "private-key-access", "filesystem-mutation")) else "review" if flags else "low"
    return {
        "risk": risk,
        "flagged": bool(flags),
        "flags": flags,
        "publishable": False,
        "required_status": "draft",
        "scripts_executed": False,
    }


def installed_skill_files(skill: Dict[str, object]) -> Dict[str, str]:
    path_value = skill.get("path")
    if not path_value:
        return {}
    skill_path = Path(str(path_value)).resolve()
    root = skill_path.parent
    files: Dict[str, str] = {}
    if not root.is_dir():
        return files
    for path in root.rglob("*"):
        if not path.is_file() or path.stat().st_size > 400_000:
            continue
        try:
            files[path.relative_to(root).as_posix()] = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
    return files


def seed_first_party_skills(skills_manager, owner: Optional[str]) -> int:
    """Install missing versioned first-party skills for one configured owner."""
    existing = {item.get("name") for item in skills_manager.load(owner=owner)}
    added = 0
    for item in seed_catalog():
        if item.get("name") in existing:
            continue
        skills_manager.add_skill(
            name=str(item.get("name")),
            description=str(item.get("description") or ""),
            category=str(item.get("category") or "general"),
            tags=list(item.get("tags") or []),
            when_to_use=str(item.get("when_to_use") or ""),
            procedure=list(item.get("procedure") or []),
            pitfalls=list(item.get("pitfalls") or []),
            verification=list(item.get("verification") or []),
            status="published",
            confidence=1.0,
            source="first-party",
            owner=owner,
        )
        added += 1
    return added
