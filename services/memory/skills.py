# services/memory/skills.py
"""Skills storage layer.

Skills live on disk as `data/skills/<category>/<name>/SKILL.md` files with
YAML frontmatter and a structured markdown body (When to Use / Procedure /
Pitfalls / Verification). See `skill_format.py` for the format.

Usage counters (`uses`, `last_used`) live in a sidecar
`data/skills/_usage.json` keyed by owner plus skill name so the SKILL.md
content doesn't churn on every retrieval.

Ownership: skills declare `owner: <username>` in frontmatter. Single-user
deployments can leave that blank.

This module also retains a JSON fallback for any legacy `data/skills.json`
entries — they're surfaced as read-only `Skill` objects so old data still
loads while a user migrates them to disk.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Dict, Iterable, List, Optional

from .skill_format import Skill, slugify

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Token / similarity helpers (kept for the relevance fallback)
# ---------------------------------------------------------------------------

def _tokenize(text: str) -> set:
    return {w.strip('.,!?";:()[]') for w in (text or "").lower().split() if len(w) > 1}


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _to_float(x, default: float = 0.0) -> float:
    """Coerce a possibly hand-edited frontmatter value to float without
    raising — a blank or non-numeric `confidence:` in a SKILL.md must not
    blow up retrieval or eviction."""
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# SkillsManager
# ---------------------------------------------------------------------------


class SkillsManager:
    """Read/write SKILL.md files under <data_dir>/skills/."""

    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        self.skills_root = os.path.join(data_dir, "skills")
        self.usage_file = os.path.join(self.skills_root, "_usage.json")
        self.legacy_file = os.path.join(data_dir, "skills.json")  # back-compat
        os.makedirs(self.skills_root, exist_ok=True)

    # ----------------------------------------------------------------------
    # Path helpers
    # ----------------------------------------------------------------------

    def _skill_dir(self, category: str, name: str) -> str:
        cat = slugify(category or "general", fallback="general")
        nm = slugify(name, fallback="skill")
        return os.path.join(self.skills_root, cat, nm)

    def _skill_file(self, category: str, name: str) -> str:
        return os.path.join(self._skill_dir(category, name), "SKILL.md")

    # ----------------------------------------------------------------------
    # Usage sidecar
    # ----------------------------------------------------------------------

    def _load_usage(self) -> Dict[str, Dict]:
        if not os.path.exists(self.usage_file):
            return {}
        try:
            with open(self.usage_file, encoding="utf-8") as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
        except Exception:
            return {}

    def _save_usage(self, usage: Dict[str, Dict]) -> None:
        try:
            from core.atomic_io import atomic_write_json
            atomic_write_json(self.usage_file, usage, indent=2)
        except Exception:
            tmp = self.usage_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(usage, f, indent=2)
            os.replace(tmp, self.usage_file)

    @staticmethod
    def _usage_key(name: str, owner: Optional[str] = None) -> str:
        # Skill names are not globally unique once multiple owners are present.
        # Keep the usage sidecar keyed the same way the skill file is scoped.
        return f"{owner}::{name}" if owner else name

    def _usage_entry(self, usage: Dict[str, Dict], name: str, owner: Optional[str] = None) -> Dict:
        key = self._usage_key(name, owner)
        entry = usage.get(key)
        if isinstance(entry, dict):
            return entry
        return {}

    def set_audit(self, name: str, verdict: str, by_teacher: bool = False,
                  worker_model: str = "", teacher_model: str = "",
                  owner: Optional[str] = None) -> None:
        """Record the last test/audit result for a skill in the usage sidecar
        (so it surfaces in load() without touching SKILL.md). Drives the
        'verified' check + teacher mark on the card."""
        import time as _t
        usage = self._load_usage()
        key = self._usage_key(name, owner)
        e = usage.setdefault(key, {"uses": 0, "last_used": None})
        e["audit_verdict"] = verdict
        e["audit_by_teacher"] = bool(by_teacher)
        if worker_model:
            e["audit_worker_model"] = worker_model
        if teacher_model:
            e["audit_teacher_model"] = teacher_model
        e["audited_at"] = _t.time()
        self._save_usage(usage)

    def set_necessity(self, name: str, necessary: bool,
                      redundant_with=None, reason: str = "",
                      owner: Optional[str] = None) -> None:
        """Record the advisory 'is this skill necessary?' judgment in the usage
        sidecar. Surfaced on the card as a flag; never acts on the skill."""
        usage = self._load_usage()
        key = self._usage_key(name, owner)
        e = usage.setdefault(key, {"uses": 0, "last_used": None})
        e["necessity"] = {
            "necessary": bool(necessary),
            "redundant_with": list(redundant_with or []),
            "reason": str(reason or ""),
        }
        self._save_usage(usage)

    # ----------------------------------------------------------------------
    # Disk scan
    # ----------------------------------------------------------------------

    def _iter_skill_files(self) -> Iterable[str]:
        if not os.path.isdir(self.skills_root):
            return
        for root, _dirs, files in os.walk(self.skills_root, followlinks=False):
            if "SKILL.md" in files:
                yield os.path.join(root, "SKILL.md")

    def _read_skill(self, path: str) -> Optional[Skill]:
        try:
            with open(path, encoding="utf-8") as f:
                text = f.read()
            return Skill.from_markdown(text, path=path)
        except Exception as e:
            logger.warning(f"Failed to parse {path}: {e}")
            return None

    def _write_skill(self, sk: Skill) -> str:
        path = self._skill_file(sk.category or "general", sk.name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        from core.atomic_io import atomic_write_text
        atomic_write_text(path, sk.to_markdown())
        sk.path = path
        return path

    def backfill_owner(self, primary_owner: str, valid_owners: Optional[set[str]] = None) -> int:
        """Assign legacy/unclaimed skill files to the primary owner.

        Skills are disk-backed, so the DB legacy-owner migration cannot fix
        them. If strict owner filtering is enabled and SKILL.md files have no
        owner or an owner from a deleted/test account, the UI appears empty even
        though files still exist. This mirrors the DB legacy-owner sweep.
        """
        primary_owner = (primary_owner or "").strip()
        if not primary_owner:
            return 0
        valid_owners = set(valid_owners or [])
        changed = 0
        for path in self._iter_skill_files():
            sk = self._read_skill(path)
            if not sk:
                continue
            owner = (sk.owner or "").strip()
            if owner == primary_owner:
                continue
            if owner and owner in valid_owners:
                continue
            sk.owner = primary_owner
            try:
                self._write_skill(sk)
                changed += 1
            except Exception as e:
                logger.warning("Failed to backfill owner for skill %s: %s", sk.name, e)
        return changed

    # ----------------------------------------------------------------------
    # Public API — keeps the old method names so callers don't break
    # ----------------------------------------------------------------------

    def load_all(self) -> List[Dict]:
        """Return every skill as a plain dict, plus any legacy JSON entries."""
        usage = self._load_usage()
        out: List[Dict] = []
        seen_names: set[str] = set()
        for path in self._iter_skill_files():
            sk = self._read_skill(path)
            if not sk:
                continue
            d = sk.to_dict()
            u = self._usage_entry(usage, sk.name, sk.owner)
            d["uses"] = int(u.get("uses", 0))
            d["last_used"] = u.get("last_used")
            d["audit_verdict"] = u.get("audit_verdict")
            d["audit_by_teacher"] = bool(u.get("audit_by_teacher"))
            d["audit_worker_model"] = u.get("audit_worker_model")
            d["audit_teacher_model"] = u.get("audit_teacher_model")
            d["audited_at"] = u.get("audited_at")
            d["necessity"] = u.get("necessity")
            out.append(d)
            seen_names.add(sk.name)
        # Legacy JSON entries — surfaced as draft, not editable from new flow
        if os.path.exists(self.legacy_file):
            try:
                with open(self.legacy_file, encoding="utf-8") as f:
                    legacy = json.load(f)
                if isinstance(legacy, list):
                    for row in legacy:
                        if not isinstance(row, dict):
                            continue
                        name = slugify(row.get("title") or row.get("id") or "skill")
                        if name in seen_names:
                            continue
                        out.append({
                            "id": row.get("id") or name,
                            "name": name,
                            "description": row.get("title", ""),
                            "version": "0.0.1",
                            "category": "legacy",
                            "tags": row.get("tags") or [],
                            "status": row.get("status") or "draft",
                            "confidence": row.get("confidence", 0.5),
                            "source": row.get("source", "imported"),
                            "owner": row.get("owner"),
                            "when_to_use": row.get("problem", ""),
                            "procedure": row.get("steps") or [],
                            "pitfalls": [],
                            "verification": [],
                            "body_extra": row.get("solution", ""),
                            "title": row.get("title", ""),
                            "problem": row.get("problem", ""),
                            "solution": row.get("solution", ""),
                            "steps": row.get("steps") or [],
                            "uses": row.get("uses", 0),
                            "last_used": row.get("last_used"),
                            "_legacy": True,
                        })
            except Exception:
                pass
        return out

    def load(self, owner: Optional[str] = None) -> List[Dict]:
        entries = self.load_all()
        if owner is None:
            return entries
        # SECURITY: strict ownership filter. The previous predicate also
        # included skills with NO owner field (`not s.get("owner")`), which
        # leaked legacy / un-stamped skills to every authenticated user.
        # Hide them now; the owner needs to be backfilled on disk if those
        # skills should be visible to a specific user.
        return [s for s in entries if s.get("owner") == owner]

    # ----------------------------------------------------------------------
    # CRUD — disk-backed
    # ----------------------------------------------------------------------

    def add_skill(
        self,
        title: str = "",
        problem: str = "",
        solution: str = "",
        steps: Optional[List[str]] = None,
        tags: Optional[List[str]] = None,
        source: str = "learned",
        teacher_model: Optional[str] = None,
        confidence: float = 0.8,
        session_id: Optional[str] = None,
        owner: Optional[str] = None,
        # New-schema fields (optional; fall back to old shape if absent)
        name: Optional[str] = None,
        description: Optional[str] = None,
        category: str = "general",
        when_to_use: Optional[str] = None,
        procedure: Optional[List[str]] = None,
        pitfalls: Optional[List[str]] = None,
        verification: Optional[List[str]] = None,
        platforms: Optional[List[str]] = None,
        requires_toolsets: Optional[List[str]] = None,
        fallback_for_toolsets: Optional[List[str]] = None,
        status: str = "draft",
        version: str = "1.0.0",
    ) -> Dict:
        # Normalize name
        nm = slugify(name or title or description or "skill")

        # Free dedup-at-creation (always, no API): for LLM-authored skills,
        # skip if a near-identical skill already exists (Jaccard over
        # name+description+when_to_use+procedure). User-authored skills are
        # never auto-skipped — a human asked for it. The every-X AI audit
        # handles the fuzzier near-duplicates this cheap check won't catch.
        _all = self.load_all()
        _dedup_pool = _all if owner is None else [s for s in _all if s.get("owner") == owner]
        if source != "user":
            cand = _tokenize(" ".join([
                nm, (description or title or ""),
                (when_to_use if when_to_use is not None else (problem or "")),
                " ".join(procedure if procedure is not None else (steps or [])),
            ]))
            if cand:
                for s in _dedup_pool:
                    ex = _tokenize(" ".join([
                        s.get("name", ""), s.get("description", ""),
                        s.get("when_to_use", ""),
                        " ".join(s.get("procedure", []) or []),
                    ]))
                    if _jaccard(cand, ex) >= 0.82:
                        # Near-identical — don't grow the library; bump the
                        # existing skill's usage and return it so the caller
                        # knows it already exists.
                        try:
                            self.record_use(s["name"], owner=s.get("owner"))
                        except Exception:
                            pass
                        return {**s, "_deduped": True, "_duplicate_of": s.get("name")}

        # Avoid clobbering an existing skill with the same name
        existing = {s["name"] for s in _all}
        base = nm
        i = 2
        while nm in existing:
            nm = f"{base}-{i}"
            i += 1

        sk = Skill(
            name=nm,
            description=(description or title or "").strip(),
            version=version,
            category=category or "general",
            tags=list(tags or []),
            platforms=list(platforms or []),
            requires_toolsets=list(requires_toolsets or []),
            fallback_for_toolsets=list(fallback_for_toolsets or []),
            status=status or "draft",
            confidence=float(confidence),
            source=source,
            teacher_model=teacher_model,
            owner=owner,
            when_to_use=(when_to_use if when_to_use is not None else (problem or "")),
            procedure=list(procedure if procedure is not None else (steps or [])),
            pitfalls=list(pitfalls or []),
            verification=list(verification or []),
            body_extra=(solution if solution and not procedure else ""),
        )
        self._write_skill(sk)

        return sk.to_dict()

    def update_skill(self, skill_id: str, updates: Dict, owner: Optional[str] = None) -> bool:
        """`skill_id` is the slug name. Allows updating any field plus
        renames if `name` changes (file is moved on disk).

        The call is owner-scoped: it matches a skill on disk only if
        `skill.owner == owner` (string compare; both empty-string and
        None mean "ownerless"). When `owner is None` (the default), the
        call only matches skills whose own `owner` field is empty —
        callers that want to edit an owned skill must pass the matching
        owner explicitly. This prevents a caller with one owner from
        mutating a file owned by another user that happens to share
        the same slug across category directories. The `owner` key in
        `updates` is also ignored — ownership is not an editable field
        via this path; rename or admin tooling is required for that.
        """
        for path in self._iter_skill_files():
            sk = self._read_skill(path)
            if not sk or sk.name != skill_id:
                continue
            if (sk.owner or "") != (owner or ""):
                continue

            old_dir = os.path.dirname(path)

            scalar_keys = (
                "description", "version", "category", "status", "confidence",
                "source", "teacher_model", "when_to_use",
                "body_extra",
            )
            for k in scalar_keys:
                if k in updates:
                    setattr(sk, k, updates[k])
            list_keys = ("tags", "procedure", "pitfalls", "verification",
                         "platforms", "requires_toolsets", "fallback_for_toolsets")
            for k in list_keys:
                if k in updates:
                    setattr(sk, k, list(updates[k] or []))

            # Old-schema field aliases
            if "title" in updates and "description" not in updates:
                sk.description = updates["title"]
            if "problem" in updates and "when_to_use" not in updates:
                sk.when_to_use = updates["problem"]
            if "solution" in updates and "body_extra" not in updates and not sk.procedure:
                sk.body_extra = updates["solution"]
            if "steps" in updates and "procedure" not in updates:
                sk.procedure = list(updates["steps"] or [])

            # Rename
            new_name = slugify(updates.get("name") or sk.name)
            if new_name != sk.name:
                sk.name = new_name

            # Write to potentially new path
            new_path = self._skill_file(sk.category, sk.name)
            if new_path != path:
                # Move the whole skill directory if rename or recategorize
                new_dir = os.path.dirname(new_path)
                if os.path.isdir(new_dir):
                    logger.warning(f"Skill rename target exists: {new_dir}")
                    return False
                os.makedirs(os.path.dirname(new_dir), exist_ok=True)
                os.rename(old_dir, new_dir)
                # Also rename usage key
                usage = self._load_usage()
                old_usage_key = self._usage_key(skill_id, sk.owner)
                if old_usage_key in usage:
                    usage[self._usage_key(sk.name, sk.owner)] = usage.pop(old_usage_key)
                    self._save_usage(usage)
            self._write_skill(sk)
            return True
        return False

    def delete_skill(self, skill_id: str, owner: Optional[str] = None) -> bool:
        for path in self._iter_skill_files():
            sk = self._read_skill(path)
            if not sk or sk.name != skill_id:
                continue
            if (sk.owner or "") != (owner or ""):
                continue
            skill_dir = os.path.dirname(path)
            try:
                # Remove the whole skill dir
                for root, dirs, files in os.walk(skill_dir, topdown=False):
                    for f in files:
                        os.remove(os.path.join(root, f))
                    for d in dirs:
                        os.rmdir(os.path.join(root, d))
                os.rmdir(skill_dir)
            except Exception as e:
                logger.warning(f"Failed to remove skill dir {skill_dir}: {e}")
                return False
            usage = self._load_usage()
            usage_key = self._usage_key(skill_id, sk.owner)
            if usage_key in usage:
                del usage[usage_key]
                self._save_usage(usage)
            return True
        return False

    def record_use(self, skill_id: str, owner: Optional[str] = None) -> None:
        usage = self._load_usage()
        key = self._usage_key(skill_id, owner)
        entry = usage.setdefault(key, {"uses": 0, "last_used": None})
        entry["uses"] = int(entry.get("uses", 0)) + 1
        entry["last_used"] = int(time.time())
        self._save_usage(usage)

    # ----------------------------------------------------------------------
    # Reading a single skill (used by the skill_view tool)
    # ----------------------------------------------------------------------

    def read_skill_md(self, name: str, owner: Optional[str] = None) -> Optional[str]:
        for path in self._iter_skill_files():
            sk = self._read_skill(path)
            if not sk or sk.name != name:
                continue
            if (sk.owner or "") != (owner or ""):
                continue
            try:
                with open(path, encoding="utf-8") as f:
                    return f.read()
            except Exception:
                return None
        return None

    def read_skill_reference(self, name: str, ref_path: str, owner: Optional[str] = None) -> Optional[str]:
        """Read a sub-file under the skill's directory (references/, etc).
        Refuses path traversal."""
        for path in self._iter_skill_files():
            sk = self._read_skill(path)
            if not sk or sk.name != name:
                continue
            if (sk.owner or "") != (owner or ""):
                continue
            base = os.path.realpath(os.path.dirname(path))
            target = os.path.realpath(os.path.join(base, ref_path))
            if os.path.commonpath([base, target]) != base or target == os.path.dirname(path):
                return None
            if not os.path.isfile(target):
                return None
            try:
                with open(target, encoding="utf-8") as f:
                    return f.read()
            except Exception:
                return None
        return None

    # ----------------------------------------------------------------------
    # Index — the lightweight summary injected into the system prompt
    # ----------------------------------------------------------------------

    def index_for(
        self,
        owner: Optional[str] = None,
        *,
        active_toolsets: Optional[List[str]] = None,
        platform: Optional[str] = None,
    ) -> List[Dict]:
        """Return the `[{name, description, category, status}]` list the
        agent sees in its system prompt.

        Includes:
          - All published skills.
          - Drafts written by the teacher-escalation loop
            (`source == "teacher-escalation"`). The whole point of
            the teacher loop is for the student to find the new
            procedure on the very next turn — waiting for a manual
            publish click defeats the loop.

        Excludes user-created drafts (status=draft, source != teacher-
        escalation) — those are work-in-progress and pollute the
        prompt with half-finished procedures.
        """
        active_toolsets = active_toolsets or []
        out = []
        for s in self.load(owner=owner):
            status = s.get("status")
            # Published + None (pre-status legacy) always included.
            # Drafts only if the teacher wrote them.
            if status not in ("published", None):
                if status == "draft" and s.get("source") == "teacher-escalation":
                    pass  # let it through
                else:
                    continue
            # Platform gating
            if platform and s.get("platforms") and platform not in s["platforms"]:
                continue
            # requires_toolsets: hide unless every required toolset is active
            req = s.get("requires_toolsets") or []
            if req and not all(t in active_toolsets for t in req):
                continue
            # fallback_for_toolsets: hide when any of those toolsets is active
            fb = s.get("fallback_for_toolsets") or []
            if fb and any(t in active_toolsets for t in fb):
                continue
            out.append({
                "name": s["name"],
                "description": s.get("description") or s.get("title", ""),
                "category": s.get("category", "general"),
                "status": status or "published",
            })
        out.sort(key=lambda x: (x["category"], x["name"]))
        return out

    # ----------------------------------------------------------------------
    # Relevance search (kept for the existing /api/skills/search endpoint
    # and the `manage_skills` action="search"). Now operates on the new
    # field set.
    # ----------------------------------------------------------------------

    def get_relevant_skills(
        self,
        query: str,
        skills: Optional[List[Dict]] = None,
        threshold: float = 0.3,
        max_items: int = 5,
        min_confidence: float = 0.0,
    ) -> List[Dict]:
        if skills is None:
            skills = self.load_all()
        if not skills or not query.strip():
            return []
        # Consider published AND draft skills for relevance retrieval.
        # The teacher-escalation loop writes new skills as drafts; the
        # whole point is for the student to find them on the next try
        # without a manual publish click. The UI flags teacher-written
        # entries with a 🎓 badge so users can demote / delete bad
        # ones when they spot them.
        skills = [s for s in skills if s.get("status") in ("published", "draft")]
        # Confidence gate (used by prompt-injection, NOT by search): a DRAFT
        # skill must clear the bar to be injected. Published skills are already
        # vetted, so they always qualify. Missing confidence = treat as 1.0
        # (legacy skills shouldn't silently vanish). 0 disables the gate.
        if min_confidence > 0:
            def _passes(s):
                if s.get("status") == "published":
                    return True
                # Teacher-escalation drafts are auto-written from a (possibly
                # untrusted) trace and injected as authoritative guidance, so they
                # must EARN injection with an explicit, parseable confidence that
                # clears the bar — fail closed on a missing/garbage value instead
                # of treating it as 1.0. Hand-authored legacy drafts keep the
                # lenient "unset → keep" behavior so they don't silently vanish.
                if s.get("source") == "teacher-escalation":
                    c = s.get("confidence")
                    if c is None:
                        return False
                    return _to_float(c, 0.0) >= min_confidence  # unparseable → fail closed
                c = s.get("confidence")
                if c is None:
                    return True  # unset → don't filter (legacy)
                return _to_float(c, 1.0) >= min_confidence  # unparseable → pass
            skills = [s for s in skills if _passes(s)]
        if not skills:
            return []

        query_tokens = _tokenize(query)
        scored = []
        for sk in skills:
            text = " ".join([
                sk.get("name", ""),
                sk.get("description", ""),
                sk.get("when_to_use", ""),
                " ".join(sk.get("tags", []) or []),
                " ".join(sk.get("procedure", []) or []),
            ])
            score = _jaccard(query_tokens, _tokenize(text))
            for tag in sk.get("tags", []) or []:
                # Match tags as whole tokens, not substrings: `tag in query`
                # boosted e.g. a "ai" tag for any query containing "email".
                tag_tokens = _tokenize(tag)
                if tag_tokens and tag_tokens <= query_tokens:
                    score = max(score, 0.3) * 1.3
            if query.lower() in (sk.get("description") or "").lower():
                score = max(score, 0.6)
            score *= 1.0 + _to_float(sk.get("confidence"), 0.5) * 0.1
            if sk.get("uses", 0) > 0:
                score *= 1.05
            if score >= threshold:
                scored.append((score, sk))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [sk for _, sk in scored[:max_items]]
