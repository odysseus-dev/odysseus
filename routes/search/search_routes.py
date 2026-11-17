"""Search routes — /api/search/config GET, /api/search POST."""

import html
import json
import logging
from typing import Dict, Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse

import time

from services.search import get_search_config, comprehensive_web_search, PROVIDER_INFO
from services.search.core import _call_provider
from services.search.providers import _get_provider_key, _get_search_instance

logger = logging.getLogger(__name__)


async def _request_values(request: Request) -> Dict[str, Any]:
    """Accept JSON, form data, or query params for search endpoints.

    The browser UI posts FormData, while the agent's generic app_api tool
    posts JSON. FastAPI Form(...) rejects JSON with a 422 before our handler
    runs, which made the model think SearXNG was broken.
    """
    values: Dict[str, Any] = dict(request.query_params)
    content_type = (request.headers.get("content-type") or "").lower()
    try:
        if "application/json" in content_type:
            body = await request.json()
            if isinstance(body, dict):
                values.update(body)
        else:
            form = await request.form()
            values.update(dict(form))
    except Exception:
        pass
    return values


def setup_search_routes(config) -> APIRouter:
    router = APIRouter(tags=["search"])

    @router.get("/search/web", response_class=HTMLResponse)
    async def web_search_page(request: Request, q: str = Query("", min_length=0)) -> HTMLResponse:
        """Browser-facing search results page for clickable agent web_search rows."""
        # The site CSP allows inline script only with the per-request nonce
        # (SecurityHeadersMiddleware); without it this page's script never ran.
        nonce = html.escape(getattr(request.state, "csp_nonce", "") or "", quote=True)
        safe_q = str(q or "").strip()
        title = html.escape(safe_q or "Web search")
        # json.dumps leaves `<`, `>` and `&` alone, so q=</script><script ...>
        # would close this inline <script> and inject markup (reflected XSS).
        # Unicode-escape them; the JS string value is unchanged.
        q_json = (
            json.dumps(safe_q)
            .replace("<", "\\u003c")
            .replace(">", "\\u003e")
            .replace("&", "\\u0026")
        )
        page = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{title} - Odysseus Search</title>
  <style>
    :root {{ color-scheme: dark; --bg:#111; --fg:#eee; --muted:#999; --border:#333; --accent:#e05252; }}
    body {{ margin:0; font:14px/1.45 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; background:var(--bg); color:var(--fg); }}
    main {{ max-width:900px; margin:0 auto; padding:22px 18px 40px; }}
    form {{ display:flex; gap:8px; margin:0 0 16px; }}
    input {{ flex:1; min-width:0; height:34px; padding:0 10px; border:1px solid var(--border); border-radius:7px; background:#181818; color:var(--fg); font:inherit; }}
    button {{ height:34px; padding:0 13px; border:1px solid color-mix(in srgb,var(--accent) 45%,var(--border)); border-radius:7px; background:color-mix(in srgb,var(--accent) 14%,transparent); color:var(--fg); font:inherit; cursor:pointer; }}
    h1 {{ margin:0 0 14px; font-size:16px; font-weight:650; }}
    .status {{ color:var(--muted); font-size:12px; margin:8px 0 14px; }}
    .result {{ display:block; padding:11px 0; border-top:1px solid var(--border); text-decoration:none; color:inherit; }}
    .result-title {{ color:var(--fg); font-weight:650; }}
    .result-url {{ margin-top:3px; color:color-mix(in srgb,var(--accent) 78%,var(--fg)); font-size:12px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
    .result-snippet {{ margin-top:5px; color:color-mix(in srgb,var(--fg) 72%,transparent); font-size:13px; }}
  </style>
</head>
<body>
  <main>
    <h1>Web Search</h1>
    <form id="search-form">
      <input id="query" value="{html.escape(safe_q, quote=True)}" autocomplete="off">
      <button type="submit">Search</button>
    </form>
    <div class="status" id="status">Loading...</div>
    <div id="results"></div>
  </main>
  <script nonce="{nonce}">
    const initialQuery = {q_json};
    const input = document.getElementById('query');
    const statusEl = document.getElementById('status');
    const resultsEl = document.getElementById('results');
    function esc(value) {{
      return String(value || '').replace(/[&<>"']/g, ch => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[ch]));
    }}
    async function runSearch(query) {{
      query = String(query || '').trim();
      if (!query) {{ statusEl.textContent = 'Enter a search query.'; resultsEl.innerHTML = ''; return; }}
      statusEl.textContent = 'Searching...';
      resultsEl.innerHTML = '';
      const fd = new FormData();
      fd.append('query', query);
      const res = await fetch('/api/search', {{ method: 'POST', credentials: 'same-origin', body: fd }});
      const data = await res.json().catch(() => ({{}}));
      const sources = Array.isArray(data.sources) ? data.sources : [];
      if (!res.ok || data.error) {{
        statusEl.textContent = data.error || `Search failed (${{res.status}})`;
        return;
      }}
      statusEl.textContent = sources.length ? `${{sources.length}} results` : 'No results';
      resultsEl.innerHTML = sources.map(s => {{
        const rawUrl = String(s.url || s.link || '');
        // Result URLs come from third-party search results: only http(s) may become a link.
        const url = /^https?:[/][/]/i.test(rawUrl) ? rawUrl : '';
        const title = s.title || url || 'Untitled';
        const snippet = s.snippet || s.content || '';
        return `<a class="result" href="${{esc(url)}}" target="_blank" rel="noopener noreferrer">
          <div class="result-title">${{esc(title)}}</div>
          <div class="result-url">${{esc(url)}}</div>
          <div class="result-snippet">${{esc(snippet)}}</div>
        </a>`;
      }}).join('');
    }}
    document.getElementById('search-form').addEventListener('submit', ev => {{
      ev.preventDefault();
      const q = input.value.trim();
      const url = new URL(window.location.href);
      url.searchParams.set('q', q);
      history.replaceState(null, '', url);
      runSearch(q);
    }});
    runSearch(initialQuery);
  </script>
</body>
</html>"""
        return HTMLResponse(page)

    @router.get("/api/search/config")
    async def get_search_settings() -> Dict[str, Any]:
        return get_search_config()

    @router.post("/api/search")
    async def do_web_search(request: Request) -> Dict[str, Any]:
        """Standalone web search — returns context string + source list.

        Used by Compare mode to pre-search once and share results across panes.
        """
        values = await _request_values(request)
        query = str(values.get("query") or values.get("q") or "").strip()
        if not query:
            return {"context": "", "sources": [], "error": "query is required"}
        time_filter = values.get("time_filter") or values.get("freshness")
        if time_filter is not None:
            time_filter = str(time_filter).strip() or None
        try:
            context, sources = comprehensive_web_search(
                query, return_sources=True, time_filter=time_filter,
            )
            return {"context": context, "sources": sources}
        except Exception as e:
            logger.error(f"Standalone web search failed: {e}")
            return {"context": "", "sources": [], "error": str(e)}

    @router.get("/api/search/providers")
    async def list_search_providers():
        """Return available search providers with config status."""
        providers = []
        for pid, (label, needs_key, needs_url) in PROVIDER_INFO.items():
            if pid == "disabled":
                continue
            available = True
            if needs_key and not _get_provider_key(pid):
                available = False
            if needs_url and pid == "searxng" and not _get_search_instance():
                available = False
            providers.append({
                "id": pid,
                "label": label,
                "available": available,
            })
        return providers

    @router.post("/api/search/query")
    async def search_with_provider(request: Request) -> Dict[str, Any]:
        """Search using a specific provider. Used by compare search mode."""
        values = await _request_values(request)
        query = str(values.get("query") or values.get("q") or "").strip()
        provider = str(values.get("provider") or "").strip()
        try:
            count = int(values.get("count") or values.get("limit") or 10)
        except Exception:
            count = 10
        if not query:
            return {"results": [], "provider": provider, "error": "query is required"}
        if provider not in PROVIDER_INFO or provider == "disabled":
            return {"results": [], "provider": provider, "error": "Unknown provider"}
        t0 = time.time()
        try:
            results = _call_provider(provider, query, min(count, 20))
            elapsed = round(time.time() - t0, 2)
            return {"results": results, "provider": provider, "time": elapsed}
        except Exception as e:
            elapsed = round(time.time() - t0, 2)
            logger.error(f"Search provider {provider} failed: {e}")
            return {"results": [], "provider": provider, "time": elapsed, "error": str(e)}

    return router
