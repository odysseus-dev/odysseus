import logging
import time

logger = logging.getLogger(__name__)


class ManageMemoryTool:
    async def execute(self, content: str, ctx: dict) -> dict:
        """Manage memories: list, add, edit, delete, search.

        Content format:
        Line 1: action (list|add|edit|delete|search)
        Line 2+: action-specific params

        Actions:
        list                    — list all memories (optional line 2: category filter)
        add                     — line 2: text, optional line 3: category (fact|event|contact|preference)
        edit                    — line 2: memory_id, line 3: new text
        delete                  — line 2: memory_id
        search                  — line 2: query"""
        owner = ctx.get("owner")
        from src.ai_interaction import _memory_manager, _memory_vector

        if not _memory_manager:
            return {"error": "Memory manager not available"}

        lines = content.strip().split("\n")
        if not lines:
            return {"error": "Need at least 1 line: action"}

        action = lines[0].strip().lower()

        if action == "list":
            category_filter = lines[1].strip().lower() if len(lines) > 1 and lines[1].strip() else None
            memories = _memory_manager.load(owner=owner)
            if category_filter:
                memories = [m for m in memories if m.get("category", "").lower() == category_filter]
            if not memories:
                return {"results": "No memories found" + (f" in category '{category_filter}'" if category_filter else "") + "."}
            result_lines = [f"Found {len(memories)} memory entries:\n"]
            for m in memories[:100]:
                cat = m.get("category", "fact")
                mid = m.get("id", "?")[:8]
                text = m.get("text", "")
                if len(text) > 150:
                    text = text[:150] + "..."
                result_lines.append(f"- [{cat}] `{mid}` — {text}")
            if len(memories) > 100:
                result_lines.append(f"... and {len(memories) - 100} more")
            return {"results": "\n".join(result_lines)}

        elif action == "add":
            if len(lines) < 2:
                return {"error": "Add needs line 2: memory text"}
            text = lines[1].strip()
            category = lines[2].strip().lower() if len(lines) > 2 and lines[2].strip() else "fact"
            if not text:
                return {"error": "Memory text cannot be empty"}

            entry = _memory_manager.add_entry(text, source="ai_agent", category=category, owner=owner)
            memories = _memory_manager.load_all()
            memories.append(entry)
            _memory_manager.save(memories)

             # Update vector index if available
            if _memory_vector and hasattr(_memory_vector, 'healthy') and _memory_vector.healthy:
                try:
                    _memory_vector.add(entry["id"], text)
                except Exception:
                    pass
            try:
                from src.event_bus import fire_event
                fire_event("memory_added", owner)
            except Exception:
                logger.debug("memory_added event dispatch failed", exc_info=True)

            return {"action": "add", "memory_id": entry["id"],
                    "results": f"Memory added: [{category}] {text}"}

        elif action == "edit":
            if len(lines) < 3:
                return {"error": "Edit needs line 2: memory_id, line 3: new text"}
            memory_id = lines[1].strip()
            new_text = lines[2].strip()
            if not new_text:
                return {"error": "New text cannot be empty"}

            memories = _memory_manager.load_all()
            found = False
            for m in memories:
                if m.get("id", "").startswith(memory_id):
                    # Verify ownership
                    if owner and m.get("owner") != owner:
                        return {"error": f"Memory '{memory_id}' not found"}
                    m["text"] = new_text
                    m["timestamp"] = int(time.time())
                    found = True
                    full_id = m["id"]
                    break
            if not found:
                return {"error": f"Memory '{memory_id}' not found"}
            _memory_manager.save(memories)

            # Update vector index
            if _memory_vector and hasattr(_memory_vector, 'healthy') and _memory_vector.healthy:
                try:
                    _memory_vector.add(full_id, new_text)
                except Exception:
                    pass

            return {"action": "edit", "memory_id": memory_id,
                    "results": f"Memory updated: {new_text}"}

        elif action == "delete":
            if len(lines) < 2:
                return {"error": "Delete needs line 2: memory_id"}
            memory_id = lines[1].strip()

            memories = _memory_manager.load_all()
            original_len = len(memories)
            full_id = None
            delete_id = None
            for m in memories:
                if m.get("id", "").startswith(memory_id):
                    if owner and m.get("owner") != owner:
                        return {"error": f"Memory '{memory_id}' not found"}
                    full_id = m["id"]
                    delete_id = m["id"]
                    break
            memories = [m for m in memories if m.get("id") != delete_id]
            if len(memories) == original_len:
                return {"error": f"Memory '{memory_id}' not found"}
            _memory_manager.save(memories)

            # Remove from vector index
            if _memory_vector and full_id and hasattr(_memory_vector, 'healthy') and _memory_vector.healthy:
                try:
                    _memory_vector.remove(full_id)
                except Exception:
                    pass

            return {"action": "delete", "memory_id": memory_id,
                    "results": f"Memory '{memory_id}' deleted"}

        elif action == "search":
            if len(lines) < 2:
                return {"error": "Search needs line 2: query"}
            query = lines[1].strip()
            memories = _memory_manager.load(owner=owner)

            if hasattr(_memory_manager, 'get_relevant_memories'):
                results = _memory_manager.get_relevant_memories(query, memories, threshold=0.05, max_items=20)
            else:
                # Fallback: simple text search
                query_lower = query.lower()
                results = [m for m in memories if query_lower in m.get("text", "").lower()][:20]

            if not results:
                return {"results": f"No memories found matching '{query}'."}
            result_lines = [f"Found {len(results)} matching memories:\n"]
            for m in results:
                cat = m.get("category", "fact")
                mid = m.get("id", "?")[:8]
                text = m.get("text", "")
                result_lines.append(f"- [{cat}] `{mid}` — {text}")
            return {"results": "\n".join(result_lines)}

        else:
            return {"error": f"Unknown action '{action}'. Use: list, add, edit, delete, search"}
