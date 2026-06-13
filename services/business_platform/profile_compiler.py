"""Métier catalog loader + profile compiler. Spec §4.

Native-skills-first: a catalog role's capabilities are REFERENCES to native
Odysseus skills (``data/skills/<category>/<name>/SKILL.md``); the catalog
carries only a thin identity ``soul``. Seed skills for the baseline office
staff live in ``seed_skills/`` (repo-tracked; ``data/`` is gitignored) and
are installed into the native skills dir only when missing — an operator's
edited native skill is never overwritten.

Compiler output formats come from the approved multiagent spec
(docs/superpowers/specs/2026-06-12-odysseus-multiagent-orchestration-design.md):
persona = ``personas/<name>/SOUL.md`` + ``meta.json {description}``;
agent = ``agents/<name>/agent.json {persona, tools, model}`` — extended here
with a forward-compatible ``skills`` list (runtime consumption is Plan 3).
"""
import json
import re
import shutil
from pathlib import Path
from typing import Optional

import yaml

from .envelope import GATED_CLASSES

SEED_SKILLS_DIR = Path(__file__).resolve().parent / "seed_skills"
CATALOGS_DIR = Path(__file__).resolve().parent / "catalogs"
GENERAL_OFFICE_CATALOG_PATH = CATALOGS_DIR / "general_office.yaml"

SURFACE_POLICIES = {"web_only", "web_first", "app_invite", "app_required"}


class CatalogError(ValueError):
    pass


# ---------------------------------------------------------------- skills ----

def _default_skills_dir() -> Path:
    from src.constants import SKILLS_DIR
    return Path(SKILLS_DIR)


def install_seed_skills(skills_dir: Optional[str | Path] = None,
                        owner: Optional[str] = None) -> dict:
    """Copy repo seed skills into the native skills dir, missing-only.

    Returns {"installed": [refs], "skipped": [refs]} where a ref is
    "<category>/<name>". Existing native skills are never touched.

    ``owner``: SkillsManager.load(owner=...) deliberately HIDES ownerless
    skills (strict ownership filter). Pass the owner the seeded skills
    should be visible to (e.g. the company's agent owner id) and it is
    stamped into the frontmatter on install. Without it, seeds are only
    reachable via load_all() / catalog skill-ref resolution.
    """
    dest_root = Path(skills_dir) if skills_dir else _default_skills_dir()
    installed, skipped = [], []
    for skill_md in sorted(SEED_SKILLS_DIR.glob("*/*/SKILL.md")):
        src_dir = skill_md.parent
        ref = f"{src_dir.parent.name}/{src_dir.name}"
        dest = dest_root / src_dir.parent.name / src_dir.name
        if (dest / "SKILL.md").exists():
            skipped.append(ref)
            continue
        shutil.copytree(src_dir, dest, dirs_exist_ok=True)
        if owner:
            # Frontmatter-injection guard: owner is interpolated into the
            # SKILL.md header, so a newline or YAML-structural char could
            # smuggle extra keys. Owner ids are "human:x" / "agent:x/y".
            if not re.fullmatch(r"[\w:@./-]+", owner):
                raise ValueError(f"invalid owner for skill stamping: {owner!r}")
            md = dest / "SKILL.md"
            text = md.read_text(encoding="utf-8")
            head, sep, body = text.partition("\n---\n")
            if sep and head.startswith("---"):
                md.write_text(f"{head}\nowner: {owner}{sep}{body}",
                              encoding="utf-8")
        installed.append(ref)
    return {"installed": installed, "skipped": skipped}


def _skill_resolvable(ref: str, skills_dir: Path) -> bool:
    """A 'category/name' ref resolves against native skills or repo seeds."""
    if "/" not in ref:
        return False
    return ((skills_dir / ref / "SKILL.md").exists()
            or (SEED_SKILLS_DIR / ref / "SKILL.md").exists())


# --------------------------------------------------------------- catalog ----

def load_catalog(path: str | Path,
                 skills_dir: Optional[str | Path] = None) -> dict:
    """Load + validate a métier catalog YAML. Raises CatalogError."""
    raw = Path(path).read_text(encoding="utf-8")
    cat = yaml.safe_load(raw)
    if not isinstance(cat, dict):
        raise CatalogError("catalog must be a YAML mapping")

    vertical = cat.get("vertical")
    if not vertical or not isinstance(vertical, str) \
            or not vertical.replace("_", "").isalnum() or vertical.lower() != vertical:
        raise CatalogError(f"invalid vertical {vertical!r} (want [a-z0-9_]+)")

    if cat.get("surface_policy") not in SURFACE_POLICIES:
        raise CatalogError(
            f"unknown surface_policy {cat.get('surface_policy')!r}; "
            f"want one of {sorted(SURFACE_POLICIES)}")

    gated = cat.get("gated_classes") or []
    unknown = set(gated) - GATED_CLASSES
    if unknown:
        raise CatalogError(f"unknown gated classes: {sorted(unknown)}")

    roles = cat.get("roles")
    if not isinstance(roles, dict) or not roles:
        raise CatalogError("catalog needs at least one entry in roles")

    front = cat.get("front_desk")
    if not isinstance(front, dict) or "*" not in front:
        raise CatalogError('front_desk needs a "*" catch-all route')
    for prefix, target in front.items():
        if target not in roles:
            raise CatalogError(
                f"front_desk route {prefix!r} targets unknown role {target!r}")

    sk_dir = Path(skills_dir) if skills_dir else _default_skills_dir()
    missing_skills = []
    for role, spec in roles.items():
        if not isinstance(spec, dict):
            raise CatalogError(f"role {role!r} must be a mapping")
        if not str(spec.get("soul", "")).strip():
            raise CatalogError(f"role {role!r} needs a non-empty soul")
        tools = spec.get("tools")
        if not isinstance(tools, list) or not tools:
            raise CatalogError(f"role {role!r} needs a non-empty tools list")
        for ref in spec.get("skills") or []:
            if not _skill_resolvable(ref, sk_dir):
                missing_skills.append(f"{role}: {ref}")
    if missing_skills:
        raise CatalogError(
            "unresolvable skill references (install the native skill or fix "
            "the ref): " + "; ".join(missing_skills))
    return cat


# -------------------------------------------------------------- compiler ----

def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def compile_profile(catalog: dict, base_dir: str | Path) -> dict:
    """Compile a validated catalog into multiagent artifacts under base_dir.

    Idempotent: same catalog -> same bytes. Returns a manifest of paths.
    """
    base = Path(base_dir)
    vertical = catalog["vertical"]
    manifest = {"vertical": vertical, "personas": [], "agents": [],
                "front_desk": None}

    for role in sorted(catalog["roles"]):
        spec = catalog["roles"][role]
        name = f"{vertical}-{role}"

        soul = str(spec["soul"]).rstrip() + "\n"
        _write(base / "personas" / name / "SOUL.md", soul)
        _write(base / "personas" / name / "meta.json",
               json.dumps({"description": spec.get("description", "")},
                          indent=2, sort_keys=True) + "\n")
        manifest["personas"].append(str(base / "personas" / name))

        agent = {
            "persona": name,
            "tools": list(spec["tools"]),
            "skills": list(spec.get("skills") or []),
            "model": None,
        }
        _write(base / "agents" / name / "agent.json",
               json.dumps(agent, indent=2, sort_keys=True) + "\n")
        manifest["agents"].append(str(base / "agents" / name))

    front = {
        "vertical": vertical,
        # Routes target COMPILED agent names (agents/<vertical>-<role>/) so a
        # consuming runtime can spawn them directly without re-deriving the
        # prefix; "roles" keeps the raw catalog mapping for reference.
        "front_desk": {prefix: f"{vertical}-{role}"
                       for prefix, role in catalog["front_desk"].items()},
        "roles": catalog["front_desk"],
        "gated_classes": sorted(catalog.get("gated_classes") or []),
        "surface_policy": catalog["surface_policy"],
    }
    fd_path = base / "front_desk.json"
    _write(fd_path, json.dumps(front, indent=2, sort_keys=True) + "\n")
    manifest["front_desk"] = str(fd_path)
    return manifest
