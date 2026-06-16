"""Agent tool: ``manage_atlas`` — read/write access to the Atlas markdown vault.

Lets chat/agents work with the user's Obsidian-style vault: list notes, read a
note (with its resolved out-links + back-links), create/overwrite or append to a
note, search, fetch back-links, and render the link graph. All file access goes
through the SAME confinement + owner-scoping helpers the HTTP API uses
(``routes.atlas_routes``), so the agent can never read or write outside the
requesting user's vault.

Mirrors the shape of ``ManageDocumentTool`` (``document_tools.py``): a single
``execute(content, ctx)`` entry point that parses a JSON ``{"action": ...}``
blob, with ``ctx["owner"]`` carrying the resolved user (set up by
``_document_tool_dispatch`` in ``tool_execution.py``).
"""

from typing import Dict

from src.constants import MAX_READ_CHARS
from .document_tools import _parse_tool_args


class ManageAtlasTool:
    async def execute(self, content: str, ctx: dict) -> Dict:
        # Imported lazily so a cold import of the tool module doesn't drag in the
        # route layer (and its FastAPI deps) until the tool actually runs.
        from routes.atlas_routes import (
            AtlasPathError,
            safe_atlas_path,
            write_note,
            read_all_notes,
            invalidate_cache,
            notes_for_query,
            _note_meta,
        )
        from src.atlas_query import run_query
        from src.atlas_links import (
            parse_links,
            resolve_link,
            build_graph,
            backlinks as _backlinks,
            note_title,
        )

        owner = ctx.get("owner")

        try:
            args = _parse_tool_args(content)
        except ValueError:
            return {"error": "Invalid JSON arguments", "exit_code": 1}

        action = (args.get("action") or "list").lower()
        path = args.get("path") or args.get("note") or args.get("title")

        def _canon(p):
            """Resolve an agent-supplied note name to its real relpath.

            The agent typically passes a wikilink-style name ('Roadmap',
            'Ideas/AI') without the .md extension; resolve it the same way a
            [[link]] would resolve so read/append/delete/backlinks all hit the
            right file. Returns the relpath, or None if no such note exists.
            """
            return resolve_link(p, list(read_all_notes(owner).keys()))

        def _link(rel, label=None):
            """A chat-clickable note link: [label](#atlas-<url-encoded-path>).

            chatRenderer's anchor delegate routes #atlas-… links to the docked
            Atlas panel and pulse-highlights the note, so when the model says
            "found X" the user can click straight to it.
            """
            from urllib.parse import quote
            return f"[{label or rel}](#atlas-{quote(rel, safe='')})"

        try:
            if action == "list":
                notes = read_all_notes(owner)
                if not notes:
                    return {"response": "The Atlas vault is empty.", "notes": [], "exit_code": 0}
                items = [_note_meta(owner, rel, md) for rel, md in notes.items()]
                items.sort(key=lambda n: n["mtime"], reverse=True)
                q = (args.get("search") or "").strip().lower()
                if q:
                    items = [n for n in items
                             if q in n["path"].lower() or q in n["title"].lower()]
                items = items[: int(args.get("limit", 50))]
                lines = [f"- {_link(n['path'], n['title'])} (`{n['path']}`)"
                         + (f" #{' #'.join(n['tags'])}" if n["tags"] else "")
                         for n in items]
                return {
                    "response": f"{len(items)} note(s) in the vault:\n" + "\n".join(lines),
                    "notes": items,
                    "exit_code": 0,
                }

            if action in ("read", "view", "open", "get"):
                if not path:
                    return {"error": "Need 'path' (use action=list to find one)", "exit_code": 1}
                rel = _canon(path)
                if not rel:
                    return {"error": f"Note '{path}' not found", "exit_code": 1}
                abs_path = safe_atlas_path(owner, rel)
                md = abs_path.read_text(encoding="utf-8", errors="replace")
                all_notes = read_all_notes(owner)
                relpaths = list(all_notes.keys())
                parsed = parse_links(md)
                outlinks = [resolve_link(n, relpaths) for n in parsed["wikilinks"] + parsed["embeds"]]
                outlinks = [o for o in outlinks if o]
                preview_limit = int(args.get("limit", MAX_READ_CHARS))
                truncated = len(md) > preview_limit
                body = md[:preview_limit] + (f"\n... (truncated, {len(md)} chars)" if truncated else "")
                return {
                    "response": f"{_link(rel, note_title(rel, md))} (`{rel}`)\n\n{body}",
                    "note": {
                        "path": rel,
                        "title": note_title(rel, md),
                        "tags": parsed["tags"],
                        "outlinks": outlinks,
                        "backlinks": _backlinks(rel, all_notes),
                    },
                    "exit_code": 0,
                }

            if action in ("write", "create", "save", "update"):
                if not path:
                    return {"error": "Need 'path' to write a note", "exit_code": 1}
                rel = write_note(owner, path, args.get("content", "") or "")
                return {"response": f"Saved note {_link(rel)}.", "path": rel, "exit_code": 0}

            if action == "append":
                if not path:
                    return {"error": "Need 'path' to append to a note", "exit_code": 1}
                # Append to the existing note if one resolves; otherwise create
                # it at the literal path the agent gave.
                target = _canon(path) or path
                abs_path = safe_atlas_path(owner, target) if _canon(path) else None
                existing = abs_path.read_text(encoding="utf-8", errors="replace") if abs_path and abs_path.is_file() else ""
                addition = args.get("content", "") or ""
                joined = existing + ("\n" if existing and not existing.endswith("\n") else "") + addition
                rel = write_note(owner, target, joined)
                return {"response": f"Appended to {_link(rel)}.", "path": rel, "exit_code": 0}

            if action == "delete":
                if not path:
                    return {"error": "Need 'path' to delete a note", "exit_code": 1}
                rel = _canon(path)
                if not rel:
                    return {"error": f"Note '{path}' not found", "exit_code": 1}
                safe_atlas_path(owner, rel).unlink()
                invalidate_cache(owner)
                return {"response": f"Deleted `{rel}`.", "exit_code": 0}

            if action in ("search", "find"):
                q = (args.get("query") or args.get("search") or "").strip()
                if not q:
                    return {"error": "Need 'query' to search", "exit_code": 1}
                notes = read_all_notes(owner)
                hits = []
                tag_q = q[1:].lower() if q.startswith("#") else None
                for rel, md in notes.items():
                    if tag_q is not None:
                        tags = parse_links(md)["tags"]
                        if any(t.lower() == tag_q or t.lower().startswith(tag_q + "/") for t in tags):
                            hits.append(rel)
                    elif q.lower() in (rel + "\n" + md).lower():
                        hits.append(rel)
                if not hits:
                    return {"response": f"No notes match '{q}'.", "results": [], "exit_code": 0}
                return {
                    "response": f"{len(hits)} match(es) for '{q}':\n" + "\n".join(f"- {_link(h)}" for h in hits),
                    "results": hits,
                    "exit_code": 0,
                }

            if action == "backlinks":
                if not path:
                    return {"error": "Need 'path' for backlinks", "exit_code": 1}
                rel = _canon(path) or path
                links = _backlinks(rel, read_all_notes(owner))
                return {
                    "response": (f"{len(links)} note(s) link to `{rel}`:\n" + "\n".join(f"- `{b}`" for b in links))
                    if links else f"No notes link to `{rel}`.",
                    "backlinks": links,
                    "exit_code": 0,
                }

            if action == "graph":
                g = build_graph(read_all_notes(owner))
                return {
                    "response": f"Vault graph: {len(g['nodes'])} node(s), {len(g['links'])} link(s).",
                    "graph": g,
                    "exit_code": 0,
                }

            if action == "query":
                # Bases-style structured query (the same shape the UI builds and
                # the atlas MCP server accepts). Answers questions like "todo
                # sections where status is open".
                q = args.get("query") or args.get("where")
                if isinstance(q, str):
                    import json as _json
                    try:
                        q = _json.loads(q)
                    except (ValueError, TypeError):
                        return {"error": "query must be a JSON object", "exit_code": 1}
                if not isinstance(q, dict):
                    return {"error": "Need a 'query' object", "exit_code": 1}
                # Allow a bare filter list / where-block to be passed directly.
                if "where" not in q and ("filters" in q or "join" in q):
                    q = {"where": q}
                vault_notes = notes_for_query(owner)
                result = run_query(q, vault_notes)
                rows = result["rows"]
                if not rows:
                    # Help the model self-correct instead of guessing field names:
                    # surface the properties + tags that actually exist in the vault.
                    all_cols = run_query({"from": q.get("from", "notes")}, vault_notes)["columns"]
                    props = sorted(c for c in all_cols if c.startswith("prop."))
                    tags = sorted({t for n in vault_notes
                                   for t in parse_links(n["content"]).get("tags", [])})
                    hint = "No matching rows."
                    if props:
                        hint += f" Available properties: {', '.join(props)}."
                    if tags:
                        hint += f" Tags in vault: {', '.join('#' + t for t in tags[:20])}."
                    if not props and not tags:
                        hint += " No frontmatter properties or tags exist in the vault yet."
                    return {"response": hint, **result, "available_fields": props, "exit_code": 0}
                lines = [f"{result['count']} row(s) [{', '.join(result['columns'])}]:"]
                for row in rows[:30]:
                    cells = " · ".join(f"{c}={row.get(c)}" for c in result["columns"])
                    fp = row.get("file.path")
                    lines.append(f"- {_link(fp) if fp else '(row)'} {cells}")
                return {"response": "\n".join(lines), **result, "exit_code": 0}

            return {"error": f"Unknown action '{action}'", "exit_code": 1}

        except AtlasPathError as e:
            return {"error": f"Invalid path: {e}", "exit_code": 1}
        except Exception as e:  # noqa: BLE001 — surface tool errors to the agent
            return {"error": f"manage_atlas: {e}", "exit_code": 1}
