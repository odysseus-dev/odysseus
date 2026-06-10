import logging

logger = logging.getLogger(__name__)


class ManageRagTool:
    async def execute(self, content: str, ctx: dict) -> dict:
        from src.ai_interaction import _rag_manager, _personal_docs_manager

        lines = content.strip().split("\n")
        if not lines:
            return {"error": "No action specified"}
        action = lines[0].strip().lower()

        if action == "list":
            if not _personal_docs_manager:
                return {"results": "Personal docs manager not available. RAG may not be configured."}
            try:
                files = []
                if hasattr(_personal_docs_manager, 'index'):
                    files = _personal_docs_manager.index or []
                dirs = []
                if hasattr(_personal_docs_manager, 'get_indexed_directories'):
                    dirs = _personal_docs_manager.get_indexed_directories()

                result_lines = []
                if dirs:
                    result_lines.append(f"**Indexed directories ({len(dirs)}):**")
                    for d in dirs:
                        result_lines.append(f"  - `{d}`")
                if files:
                    result_lines.append(f"\n**Indexed files ({len(files)}):**")
                    for f in files[:50]:
                        name = f.get("name", str(f)) if isinstance(f, dict) else str(f)
                        result_lines.append(f"  - {name}")
                    if len(files) > 50:
                        result_lines.append(f"  ... and {len(files) - 50} more")

                if not result_lines:
                    return {"results": "No files or directories indexed in RAG."}
                return {"results": "\n".join(result_lines)}
            except Exception as e:
                return {"error": str(e)}

        elif action == "add_directory":
            if len(lines) < 2:
                return {"error": "add_directory needs line 2: directory path"}
            directory = lines[1].strip()

            import os
            directory = os.path.expanduser(directory)
            if not os.path.isdir(directory):
                return {"error": f"Directory not found: {directory}"}

            if not _rag_manager:
                return {"error": "RAG manager not available"}

            try:
                result = _rag_manager.index_personal_documents(directory)
                indexed = result.get("indexed", 0) if isinstance(result, dict) else 0
                return {"action": "add_directory", "directory": directory,
                        "results": f"Directory '{directory}' added to RAG index ({indexed} files indexed)"}
            except Exception as e:
                return {"error": f"Failed to index directory: {e}"}

        elif action == "remove_directory":
            if len(lines) < 2:
                return {"error": "remove_directory needs line 2: directory path"}
            directory = lines[1].strip()

            if not _personal_docs_manager:
                return {"error": "Personal docs manager not available"}

            try:
                if hasattr(_personal_docs_manager, 'remove_directory'):
                    _personal_docs_manager.remove_directory(directory)
                return {"action": "remove_directory", "directory": directory,
                        "results": f"Directory '{directory}' removed from RAG index"}
            except Exception as e:
                return {"error": f"Failed to remove directory: {e}"}

        else:
            return {"error": f"Unknown action '{action}'. Use: list, add_directory, remove_directory"}
