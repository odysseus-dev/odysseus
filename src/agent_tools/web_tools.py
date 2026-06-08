import asyncio
import json
from typing import Dict, Any

from src.constants import MAX_OUTPUT_CHARS

class WebSearchTool:
    async def execute(self, content: str, ctx: dict) -> dict:
        from src.search import comprehensive_web_search
        raw = content.strip()
        query = raw
        time_filter = None
        max_pages = 5
        if raw.startswith("{"):
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict) and "query" in parsed:
                    query = str(parsed.get("query", "")).strip()
                    tf = parsed.get("time_filter") or parsed.get("freshness")
                    if isinstance(tf, str) and tf.lower() in ("day", "week", "month", "year"):
                        time_filter = tf.lower()
                    mp = parsed.get("max_pages")
                    if isinstance(mp, int) and 1 <= mp <= 10:
                        max_pages = mp
            except json.JSONDecodeError:
                pass
        if not query:
            query = raw.split("\n")[0].strip()
        if time_filter is None:
            q_lc = query.lower()
            if any(kw in q_lc for kw in ("today", "latest", "breaking", "this morning", "right now", "currently")):
                time_filter = "day"
            elif any(kw in q_lc for kw in ("this week", "past week", "recent news", "last few days")):
                time_filter = "week"
            elif any(kw in q_lc for kw in ("this month", "past month")):
                time_filter = "month"
            elif " news" in q_lc or q_lc.startswith("news ") or q_lc.endswith(" news"):
                time_filter = "week"
        loop = asyncio.get_running_loop()
        text, sources = await asyncio.wait_for(
            loop.run_in_executor(
                None,
                lambda: comprehensive_web_search(
                    query,
                    max_pages=max_pages,
                    time_filter=time_filter,
                    return_sources=True,
                ),
            ),
            timeout=30,
        )
        output = text[:MAX_OUTPUT_CHARS] if len(text) > MAX_OUTPUT_CHARS else text
        if sources:
            output += "\n\n<!-- SOURCES:" + json.dumps(sources) + " -->"
        return {"output": output, "exit_code": 0}

class WebFetchTool:
    async def execute(self, content: str, ctx: dict) -> dict:
        from src.search.content import fetch_webpage_content
        raw = content.strip()
        url = ""
        if raw.startswith("{"):
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    url = str(parsed.get("url") or "").strip()
            except json.JSONDecodeError:
                url = ""
        if not url:
            url = raw.split("\n")[0].strip()
        if not url or url.startswith("{") or any(c in url for c in (" ", "\t", "\n")):
            return {"error": "web_fetch: provide a single URL or domain, e.g. example.com", "exit_code": 1}
        low = url.lower()
        if "://" in low and not low.startswith(("http://", "https://")):
            return {"error": f"web_fetch: unsupported URL scheme (only http/https): {url[:80]}", "exit_code": 1}
        if not low.startswith(("http://", "https://")):
            url = "https://" + url
        loop = asyncio.get_running_loop()
        try:
            result = await asyncio.wait_for(
                loop.run_in_executor(None, lambda: fetch_webpage_content(url, timeout=10)),
                timeout=30,
            )
        except asyncio.TimeoutError:
            return {"error": f"web_fetch: timed out fetching {url}", "exit_code": 1}
        except Exception as e:
            return {"error": f"web_fetch: {url}: {e}", "exit_code": 1}
        err = result.get("error")
        text = (result.get("content") or "").strip()
        title = result.get("title") or ""

        if not text:
            if err:
                return {"error": f"web_fetch: {url}: {err}", "exit_code": 1}
            return {"error": f"web_fetch: {url}: no readable text content (not HTML, or the page needs JS/login)", "exit_code": 1}

        header = (f"# {title}\n" if title else "") + f"Source: {url}\n\n"
        output = header + text
        if len(output) > MAX_OUTPUT_CHARS:
            output = output[:MAX_OUTPUT_CHARS] + "\n\n[...truncated]"
        return {"output": output, "exit_code": 0}
