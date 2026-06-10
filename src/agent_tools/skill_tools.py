import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)


def _skill_dump(sk) -> Dict:
    return {
        "name": sk.name,
        "description": sk.description,
        "version": sk.version,
        "category": sk.category,
        "tags": sk.tags,
        "platforms": sk.platforms,
        "requires_toolsets": sk.requires_toolsets,
        "fallback_for_toolsets": sk.fallback_for_toolsets,
        "status": sk.status,
        "confidence": sk.confidence,
        "source": sk.source,
        "teacher_model": sk.teacher_model,
        "owner": sk.owner,
        "when_to_use": sk.when_to_use,
        "procedure": sk.procedure,
        "pitfalls": sk.pitfalls,
        "verification": sk.verification,
        "body_extra": sk.body_extra,
    }


class ManageSkillsTool:
    async def execute(self, content: str, ctx: dict) -> dict:
        owner = ctx.get("owner")
        from src.tool_implementations import _parse_tool_args

        try:
            args = _parse_tool_args(content)
        except ValueError:
            return {"error": "Invalid JSON arguments", "exit_code": 1}

        action = (args.get("action") or "").lower()
        from services.memory.skills import SkillsManager
        from services.memory.skill_format import Skill, slugify
        from src.constants import DATA_DIR
        sm = SkillsManager(DATA_DIR)

        name = (args.get("name") or args.get("skill_id") or "").strip()

        if action in ("list", "index", ""):
            all_skills = sm.load(owner=owner)
            if not all_skills:
                return {"results": "No skills yet. Create one with action='add'."}
            published = [s for s in all_skills if s.get("status") == "published"]
            drafts = [s for s in all_skills if s.get("status") == "draft"]
            lines = []
            if published:
                lines.append("## Published")
                for s in sorted(published, key=lambda x: x["name"]):
                    lines.append(f"- **{s['name']}** ({s.get('category','general')}): {s.get('description','')}")
            if drafts:
                lines.append("\n## Drafts")
                for s in sorted(drafts, key=lambda x: x["name"]):
                    lines.append(f"- **{s['name']}** [draft]: {s.get('description','')}")
            return {"results": "\n".join(lines) if lines else "No skills yet."}

        if action == "view":
            if not name:
                return {"error": "name is required for view", "exit_code": 1}
            md = sm.read_skill_md(name, owner=owner)
            if md is None:
                return {"error": f"Skill {name!r} not found", "exit_code": 1}
            return {"results": md}

        if action == "view_ref":
            if not name:
                return {"error": "name is required for view_ref", "exit_code": 1}
            ref = (args.get("path") or "").strip()
            if not ref:
                return {"error": "path is required for view_ref", "exit_code": 1}
            text = sm.read_skill_reference(name, ref, owner=owner)
            if text is None:
                return {"error": f"Reference {ref!r} not found under {name!r}", "exit_code": 1}
            return {"results": text}

        if action == "add":
            if not name:
                return {"error": "name is required for add", "exit_code": 1}
            proc = args.get("procedure")
            if proc is None:
                proc = args.get("steps") or []
            if not proc and not args.get("body_extra") and not args.get("solution"):
                return {"error": "procedure (or solution body) is required", "exit_code": 1}
            _status_arg = args.get("status")
            if not _status_arg:
                try:
                    from routes.prefs_routes import _load_for_user as _load_prefs
                    _prefs = _load_prefs(owner) or {}
                    _status_arg = "published" if _prefs.get("auto_approve_skills", True) else "draft"
                except Exception:
                    _status_arg = "draft"
            entry = sm.add_skill(
                name=args.get("name"),
                description=(args.get("description") or args.get("title") or "").strip(),
                category=args.get("category") or "general",
                tags=args.get("tags") or [],
                platforms=args.get("platforms") or [],
                requires_toolsets=args.get("requires_toolsets") or [],
                fallback_for_toolsets=args.get("fallback_for_toolsets") or [],
                when_to_use=(args.get("when_to_use") if args.get("when_to_use") is not None
                             else args.get("problem", "")),
                procedure=proc,
                pitfalls=args.get("pitfalls") or [],
                verification=args.get("verification") or [],
                status=_status_arg,
                version=args.get("version") or "1.0.0",
                confidence=args.get("confidence", 0.8),
                source=args.get("source", "learned"),
                teacher_model=args.get("teacher_model"),
                owner=owner,
                title=args.get("title", ""),
                problem=args.get("problem", ""),
                solution=args.get("solution", ""),
                steps=args.get("steps") or [],
            )
            if entry.get("_deduped"):
                return {"results": (
                    f"A near-identical skill already exists: `{entry['name']}` — not creating "
                    f"a duplicate. View or edit it with action='view', name='{entry['name']}'."
                )}
            try:
                from src.event_bus import fire_event
                fire_event("skill_added", owner)
            except Exception:
                logger.debug("skill_added event dispatch failed", exc_info=True)
            verify_hint = ""
            if entry.get("status") == "draft":
                verify_hint = (
                    "\n\nThis skill is a DRAFT. Run through the procedure once to verify, "
                    f"then publish with action='publish', name='{entry['name']}'."
                )
            return {"results": f"Created skill `{entry['name']}` — {entry.get('description','')}{verify_hint}"}

        if action == "edit":
            if not name:
                return {"error": "name is required for edit", "exit_code": 1}
            new_content = args.get("content")
            if not isinstance(new_content, str) or not new_content.strip():
                return {"error": "content (full SKILL.md) is required for edit", "exit_code": 1}
            try:
                sk_new = Skill.from_markdown(new_content)
            except Exception as e:
                return {"error": f"Could not parse content as SKILL.md: {e}", "exit_code": 1}
            sk_new.name = slugify(sk_new.name or name)
            existing = sm.load(owner=owner)
            match = next((s for s in existing if s.get("name") == name), None)
            if not match:
                return {"error": f"Skill {name!r} not found", "exit_code": 1}
            if not sk_new.owner:
                sk_new.owner = match.get("owner") or owner
            ok = sm.update_skill(name, _skill_dump(sk_new), owner=owner)
            return {"results": f"Edited skill `{sk_new.name}`."} if ok else {"error": "Update failed", "exit_code": 1}

        if action == "patch":
            if not name:
                return {"error": "name is required for patch", "exit_code": 1}
            old = args.get("old_string")
            new_str = args.get("new_string", "")
            if not isinstance(old, str) or not old:
                return {"error": "old_string is required and must be non-empty", "exit_code": 1}
            md = sm.read_skill_md(name, owner=owner)
            if md is None:
                return {"error": f"Skill {name!r} not found", "exit_code": 1}
            count = md.count(old)
            if count == 0:
                return {"error": "old_string not found in SKILL.md", "exit_code": 1}
            if count > 1:
                return {"error": f"old_string is ambiguous (appears {count} times). Make it more specific.", "exit_code": 1}
            new_md = md.replace(old, new_str, 1)
            try:
                sk_new = Skill.from_markdown(new_md)
            except Exception as e:
                return {"error": f"Patched content is not valid SKILL.md: {e}", "exit_code": 1}
            sk_new.name = slugify(sk_new.name or name)
            ok = sm.update_skill(name, _skill_dump(sk_new), owner=owner)
            return {"results": f"Patched skill `{sk_new.name}`."} if ok else {"error": "Patch update failed", "exit_code": 1}

        if action == "publish":
            if not name:
                return {"error": "name is required for publish", "exit_code": 1}
            all_skills = sm.load(owner=owner)
            match = next((s for s in all_skills if s.get("name") == name), None)
            if not match:
                return {"error": f"Skill {name!r} not found", "exit_code": 1}
            updates = {"status": "published"}
            if args.get("confidence") is not None:
                updates["confidence"] = max(0.0, min(1.0, float(args["confidence"])))
            sm.update_skill(name, updates, owner=owner)
            return {"results": f"✅ Published `{name}`. It now appears in the skills index for future turns."}

        if action == "delete":
            if not name:
                return {"error": "name is required for delete", "exit_code": 1}
            ok = sm.delete_skill(name, owner=owner)
            return {"results": f"Deleted skill `{name}`."} if ok else {"error": f"Skill {name!r} not found", "exit_code": 1}

        if action == "search":
            query = (args.get("query") or "").strip()
            if not query:
                return {"error": "query is required for search", "exit_code": 1}
            results = sm.get_relevant_skills(query, sm.load(owner=owner), max_items=5)
            if not results:
                return {"results": "No matching skills found."}
            lines = []
            for sk in results:
                proc = sk.get("procedure") or sk.get("steps") or []
                steps_str = " → ".join(proc[:5])
                lines.append(f"**{sk['name']}**: {sk.get('description','')}\n  When: {sk.get('when_to_use','')}\n  Steps: {steps_str}")
            return {"results": "\n\n".join(lines)}

        return {
            "error": (
                f"Unknown action: {action!r}. "
                "Use one of: list, view, view_ref, add, edit, patch, publish, delete, search."
            ),
            "exit_code": 1,
        }
