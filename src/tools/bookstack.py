"""BookStack-domain tool implementations.

Provides manage_bookstack tool for searching, reading, creating, and updating
BookStack pages through the API proxy.
"""
import json
import logging
import httpx
from typing import Dict, Optional

from src.tools._common import _parse_tool_args

logger = logging.getLogger(__name__)


def _get_bookstack_config():
    """Get BookStack URL and token from settings."""
    from src.settings import load_settings
    settings = load_settings()
    url = settings.get("bookstack_url", "").rstrip("/")
    token = settings.get("bookstack_token", "")
    return url, token


def _get_headers():
    """Build headers for BookStack API requests."""
    _, token = _get_bookstack_config()
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Token {token}"
    return headers


async def _api_request(method: str, endpoint: str, data: dict = None) -> dict:
    """Make an API request to BookStack."""
    base_url, _ = _get_bookstack_config()
    if not base_url:
        return {"error": "BookStack URL not configured"}

    url = f"{base_url}/api/{endpoint}"
    headers = _get_headers()

    async with httpx.AsyncClient(timeout=30) as client:
        try:
            if method == "GET":
                resp = await client.get(url, headers=headers, params=data)
            elif method == "POST":
                resp = await client.post(url, headers=headers, json=data)
            elif method == "PUT":
                resp = await client.put(url, headers=headers, json=data)
            elif method == "DELETE":
                resp = await client.delete(url, headers=headers)
            else:
                return {"error": f"Unsupported method: {method}"}

            if resp.status_code >= 400:
                return {"error": f"HTTP {resp.status_code}: {resp.text[:500]}"}

            if resp.status_code == 204:
                return {"ok": True}

            return resp.json()
        except httpx.ConnectError:
            return {"error": f"Cannot connect to BookStack at {base_url}"}
        except Exception as e:
            return {"error": str(e)}


async def do_manage_bookstack(content: str, owner: Optional[str] = None) -> Dict:
    """Handle manage_bookstack tool calls.

    Actions:
      - search: Search for content
      - list_shelves: List all shelves
      - list_books: List books (optionally filtered by shelf)
      - get_book: Get book with contents tree
      - list_pages: List pages (optionally filtered by book/chapter)
      - get_page: Get page content
      - create_page: Create a new page
      - update_page: Update an existing page
      - delete_page: Delete a page
      - export_page: Export page as markdown
    """
    try:
        args = _parse_tool_args(content)
    except ValueError:
        return {"error": "Invalid JSON arguments", "exit_code": 1}

    action = (args.get("action") or "search").replace("-", "_").strip().lower()

    # Action aliases
    _ACTION_ALIASES = {
        "list": "list_shelves",
        "search_pages": "search",
        "read_page": "get_page",
        "read_book": "get_book",
    }
    action = _ACTION_ALIASES.get(action, action)

    try:
        if action == "search":
            query = args.get("query", args.get("q", ""))
            if not query:
                return {"error": "Search query required", "exit_code": 1}
            result = await _api_request("GET", f"search?query={query}&count={args.get('count', 20)}")
            if "error" in result:
                return {"error": result["error"], "exit_code": 1}
            items = result.get("data", [])
            output = f"Found {result.get('total', len(items))} results:\n\n"
            for item in items[:10]:
                item_type = item.get("type", "unknown")
                name = item.get("name", "Untitled")
                output += f"- [{item_type}] {name}\n"
            return {"response": output, "exit_code": 0}

        elif action == "list_shelves":
            result = await _api_request("GET", "shelves")
            if "error" in result:
                return {"error": result["error"], "exit_code": 1}
            shelves = result.get("data", [])
            output = f"Shelves ({len(shelves)}):\n\n"
            for s in shelves:
                output += f"- {s['name']} (id: {s['id']})\n"
            return {"response": output, "exit_code": 0}

        elif action == "list_books":
            shelf_id = args.get("shelf_id")
            endpoint = "books"
            if shelf_id:
                endpoint += f"?filter[shelf_id:eq]={shelf_id}"
            result = await _api_request("GET", endpoint)
            if "error" in result:
                return {"error": result["error"], "exit_code": 1}
            books = result.get("data", [])
            output = f"Books ({len(books)}):\n\n"
            for b in books:
                output += f"- {b['name']} (id: {b['id']})\n"
            return {"response": output, "exit_code": 0}

        elif action == "get_book":
            book_id = args.get("book_id") or args.get("id")
            if not book_id:
                return {"error": "book_id required", "exit_code": 1}
            result = await _api_request("GET", f"books/{book_id}")
            if "error" in result:
                return {"error": result["error"], "exit_code": 1}
            output = f"Book: {result.get('name', 'Untitled')}\n"
            output += f"Description: {result.get('description', '')}\n\n"
            contents = result.get("contents", [])
            for item in contents:
                indent = "  " if item.get("type") == "page" else ""
                output += f"{indent}- [{item.get('type')}] {item.get('name', 'Untitled')} (id: {item.get('id')})\n"
            return {"response": output, "exit_code": 0}

        elif action == "list_pages":
            book_id = args.get("book_id")
            chapter_id = args.get("chapter_id")
            endpoint = "pages"
            params = []
            if book_id:
                params.append(f"filter[book_id:eq]={book_id}")
            if chapter_id:
                params.append(f"filter[chapter_id:eq]={chapter_id}")
            if params:
                endpoint += "?" + "&".join(params)
            result = await _api_request("GET", endpoint)
            if "error" in result:
                return {"error": result["error"], "exit_code": 1}
            pages = result.get("data", [])
            output = f"Pages ({len(pages)}):\n\n"
            for p in pages:
                output += f"- {p['name']} (id: {p['id']}, book_id: {p.get('book_id')})\n"
            return {"response": output, "exit_code": 0}

        elif action == "get_page":
            page_id = args.get("page_id") or args.get("id")
            if not page_id:
                return {"error": "page_id required", "exit_code": 1}
            result = await _api_request("GET", f"pages/{page_id}")
            if "error" in result:
                return {"error": result["error"], "exit_code": 1}
            output = f"Page: {result.get('name', 'Untitled')}\n"
            output += f"Book ID: {result.get('book_id')}\n"
            output += f"Updated: {result.get('updated_at')}\n\n"
            # Return markdown if available, otherwise HTML
            content_text = result.get("markdown") or result.get("html", "")
            if len(content_text) > 3000:
                content_text = content_text[:3000] + "\n\n... (truncated)"
            output += content_text
            return {"response": output, "exit_code": 0}

        elif action == "create_page":
            book_id = args.get("book_id")
            chapter_id = args.get("chapter_id")
            name = args.get("name")
            page_content = args.get("content") or args.get("html") or args.get("markdown", "")

            if not name:
                return {"error": "Page name required", "exit_code": 1}
            if not book_id and not chapter_id:
                return {"error": "book_id or chapter_id required", "exit_code": 1}

            payload = {"name": name}
            if book_id:
                payload["book_id"] = book_id
            if chapter_id:
                payload["chapter_id"] = chapter_id

            # Determine content type
            if args.get("markdown") or (page_content and not page_content.strip().startswith("<")):
                payload["markdown"] = page_content
            else:
                payload["html"] = page_content

            if args.get("tags"):
                payload["tags"] = [{"name": t} for t in args["tags"] if isinstance(t, str)]

            result = await _api_request("POST", "pages", payload)
            if "error" in result:
                return {"error": result["error"], "exit_code": 1}
            return {"response": f"Created page '{result.get('name')}' (id: {result.get('id')})", "exit_code": 0, "page_id": result.get("id")}

        elif action == "update_page":
            page_id = args.get("page_id") or args.get("id")
            if not page_id:
                return {"error": "page_id required", "exit_code": 1}

            payload = {}
            if args.get("name"):
                payload["name"] = args["name"]
            if args.get("content") or args.get("html"):
                payload["html"] = args.get("content") or args["html"]
            if args.get("markdown"):
                payload["markdown"] = args["markdown"]
            if args.get("tags"):
                payload["tags"] = [{"name": t} for t in args["tags"] if isinstance(t, str)]

            if not payload:
                return {"error": "No fields to update", "exit_code": 1}

            result = await _api_request("PUT", f"pages/{page_id}", payload)
            if "error" in result:
                return {"error": result["error"], "exit_code": 1}
            return {"response": f"Updated page {page_id}", "exit_code": 0}

        elif action == "delete_page":
            page_id = args.get("page_id") or args.get("id")
            if not page_id:
                return {"error": "page_id required", "exit_code": 1}

            result = await _api_request("DELETE", f"pages/{page_id}")
            if "error" in result:
                return {"error": result["error"], "exit_code": 1}
            return {"response": f"Deleted page {page_id}", "exit_code": 0}

        elif action == "export_page":
            page_id = args.get("page_id") or args.get("id")
            if not page_id:
                return {"error": "page_id required", "exit_code": 1}

            base_url, _ = _get_bookstack_config()
            url = f"{base_url}/api/pages/{page_id}/export/markdown"
            headers = _get_headers()

            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(url, headers=headers)
                if resp.status_code == 200:
                    return {"response": resp.text, "exit_code": 0}
                else:
                    return {"error": f"Export failed: HTTP {resp.status_code}", "exit_code": 1}

        else:
            return {"error": f"Unknown action: {action}. Valid: search, list_shelves, list_books, get_book, list_pages, get_page, create_page, update_page, delete_page, export_page", "exit_code": 1}

    except Exception as e:
        logger.error(f"BookStack tool error: {e}", exc_info=True)
        return {"error": str(e), "exit_code": 1}
