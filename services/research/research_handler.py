# src/research_handler.py
"""Handler for research service integration with expandable UI support.

Uses the IterResearch-style DeepResearcher (LLM-in-the-loop) as the primary
engine, falling back to the legacy ResearchOrchestrator or basic web search
if needed.

Includes a task registry so research survives page refreshes and can be cancelled.
"""
import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Optional, Dict

from src.research_utils import is_low_quality
from src.constants import DEEP_RESEARCH_DIR

logger = logging.getLogger(__name__)

RESEARCH_DATA_DIR = Path(DEEP_RESEARCH_DIR)


class ResearchHandler:
    """Handles research service operations with iterative deep research."""

    def __init__(self):
        self._legacy_engine = None
        self._active_tasks: Dict[str, dict] = {}
        self._initialize_legacy_engine()
        RESEARCH_DATA_DIR.mkdir(parents=True, exist_ok=True)

    def _initialize_legacy_engine(self):
        """Initialize the legacy research engine as a fallback."""
        try:
            from research_engine import ResearchOrchestrator, Config
            config = Config(max_searches=12, max_content_per_page=15000)
            self._legacy_engine = ResearchOrchestrator(config)
            logger.info("Legacy ResearchOrchestrator initialized (fallback)")
        except ImportError:
            logger.info("Legacy research_engine.py not found — DeepResearcher only")
            self._legacy_engine = None
        except Exception as e:
            logger.warning(f"Legacy research engine init failed: {e}")
            self._legacy_engine = None

    # ------------------------------------------------------------------
    # Task registry — background research with persistence
    # ------------------------------------------------------------------

    def start_research(
        self,
        session_id: str,
        query: str,
        llm_endpoint: str,
        llm_model: str,
        max_time: int = 300,
        llm_headers: dict = None,
    ) -> dict:
        """Start research as a background task. Returns task info dict."""
        # Cancel any existing research for this session
        if session_id in self._active_tasks:
            existing = self._active_tasks[session_id]
            if existing.get("status") == "running":
                self.cancel_research(session_id)

        entry = {
            "task": None,
            "researcher": None,
            "query": query,
            "status": "running",
            "progress": {},
            "result": None,
            "started_at": time.time(),
        }
        self._active_tasks[session_id] = entry

        def on_progress(event):
            entry["progress"] = event

        async def _run():
            try:
                result = await self.call_research_service(
                    query, llm_endpoint, llm_model,
                    max_time=max_time,
                    progress_callback=on_progress,
                    _task_entry=entry,
                    llm_headers=llm_headers,
                )
                entry["result"] = result
                entry["status"] = "done"
                self._save_result(session_id, entry)
            except asyncio.CancelledError:
                entry["status"] = "cancelled"
                raise
            except Exception as e:
                logger.error(f"Background research failed: {e}", exc_info=True)
                entry["result"] = str(e)
                entry["status"] = "error"

        task = asyncio.create_task(_run())
        entry["task"] = task
        return {"session_id": session_id, "status": "running", "query": query}

    def get_status(self, session_id: str) -> Optional[dict]:
        """Get current research status for a session."""
        if session_id in self._active_tasks:
            entry = self._active_tasks[session_id]
            return {
                "status": entry["status"],
                "progress": entry["progress"],
                "query": entry["query"],
                "started_at": entry["started_at"],
            }
        # Check disk for completed research
        path = RESEARCH_DATA_DIR / f"{session_id}.json"
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                return {
                    "status": data.get("status", "done"),
                    "progress": {},
                    "query": data.get("query", ""),
                    "started_at": data.get("started_at", 0),
                }
            except Exception:
                pass
        return None

    def cancel_research(self, session_id: str) -> bool:
        """Cancel running research for a session."""
        if session_id not in self._active_tasks:
            return False
        entry = self._active_tasks[session_id]
        if entry["status"] != "running":
            return False
        researcher = entry.get("researcher")
        if researcher:
            researcher.cancel()
        task = entry.get("task")
        if task and not task.done():
            task.cancel()
        entry["status"] = "cancelled"
        return True

    def get_result(self, session_id: str) -> Optional[str]:
        """Get the completed research result."""
        if session_id in self._active_tasks:
            entry = self._active_tasks[session_id]
            if entry["status"] in ("done", "error", "cancelled"):
                return entry.get("result")
        # Check disk
        path = RESEARCH_DATA_DIR / f"{session_id}.json"
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                return data.get("result")
            except Exception:
                pass
        return None

    def get_sources(self, session_id: str) -> Optional[list]:
        """Get deduplicated source list from research findings."""
        # Check in-memory first
        if session_id in self._active_tasks:
            entry = self._active_tasks[session_id]
            if entry.get("sources"):
                return entry["sources"]
            researcher = entry.get("researcher")
            if researcher and researcher.findings:
                return self._extract_sources(researcher.findings)
        # Check disk
        path = RESEARCH_DATA_DIR / f"{session_id}.json"
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                return data.get("sources")
            except Exception:
                pass
        return None

    @staticmethod
    def _extract_sources(findings: list) -> list:
        """Extract deduplicated [{url, title}] from findings, filtering low-quality ones."""
        seen = set()
        sources = []
        for f in findings:
            url = f.get("url", "")
            title = f.get("title", "") or url
            summary = f.get("summary", "") or f.get("evidence", "")
            if url and url not in seen and not is_low_quality(summary):
                seen.add(url)
                sources.append({"url": url, "title": title})
        return sources

    def clear_result(self, session_id: str):
        """Remove persisted result after it's been consumed."""
        self._active_tasks.pop(session_id, None)
        path = RESEARCH_DATA_DIR / f"{session_id}.json"
        if path.exists():
            try:
                path.unlink()
            except Exception:
                pass

    def _save_result(self, session_id: str, entry: dict):
        """Persist completed research result to disk."""
        try:
            # Extract and cache sources
            sources = []
            researcher = entry.get("researcher")
            if researcher and researcher.findings:
                sources = self._extract_sources(researcher.findings)
            entry["sources"] = sources

            path = RESEARCH_DATA_DIR / f"{session_id}.json"
            data = {
                "query": entry["query"],
                "status": entry["status"],
                "result": entry["result"],
                "sources": sources,
                "started_at": entry["started_at"],
                "completed_at": time.time(),
            }
            path.write_text(json.dumps(data), encoding="utf-8")
            logger.info(f"Research result saved to {path}")
        except Exception as e:
            logger.error(f"Failed to save research result: {e}")

    async def call_research_service(
        self,
        query: str,
        llm_endpoint: str,
        llm_model: str,
        max_time: int = 300,
        progress_callback=None,
        _task_entry: dict = None,
        llm_headers: dict = None,
    ) -> str:
        """
        Run iterative deep research using the LLM-in-the-loop DeepResearcher.

        Args:
            query: Research question
            llm_endpoint: LLM endpoint URL for chat completions
            llm_model: Model name/ID
            max_time: Maximum research time in seconds (default 5 minutes)
            _task_entry: Internal - registry entry to store researcher ref

        Returns:
            Formatted research report with expandable section and summary
        """
        logger.info("Starting IterResearch Deep Research")
        logger.info(f"Query: {query}")
        logger.info(f"LLM: {llm_endpoint} / {llm_model}")
        logger.info(f"Max time: {max_time}s")

        try:
            from src.deep_research import DeepResearcher
            from src.settings import get_setting

            researcher = DeepResearcher(
                llm_endpoint=llm_endpoint,
                llm_model=llm_model,
                llm_headers=llm_headers,
                max_rounds=8,
                max_time=max_time,
                max_report_tokens=int(get_setting("research_max_tokens", 8192)),
                progress_callback=progress_callback,
            )
            if _task_entry is not None:
                _task_entry["researcher"] = researcher

            start_time = time.time()
            report = await researcher.research(query)
            elapsed = time.time() - start_time

            stats = researcher.get_stats()
            logger.info("IterResearch completed successfully")
            for key, value in stats.items():
                logger.info(f"  {key}: {value}")

            return self._format_research_report(
                query, report, stats, elapsed,
                findings=researcher.findings,
                evolving_report=researcher.evolving_report,
                analyzed_urls=getattr(researcher, "analyzed_urls", None),
            )

        except Exception as e:
            logger.error(f"DeepResearcher failed: {e}", exc_info=True)
            return await self._fallback_research(query, llm_endpoint, llm_model, max_time, str(e))

    async def _fallback_research(
        self, query: str, llm_endpoint: str, llm_model: str,
        max_time: int, primary_error: str,
    ) -> str:
        """Fall back to legacy engine, then to basic web search."""
        # Try legacy orchestrator
        if self._legacy_engine:
            try:
                import asyncio
                logger.info("Falling back to legacy ResearchOrchestrator...")
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(
                    None, self._legacy_engine.start_research, query, max_time
                )
                stats = self._get_legacy_stats()
                elapsed = float(stats.get("Duration", "0").rstrip("s") or 0)
                return self._format_research_report(query, result, stats, elapsed)
            except Exception as e:
                logger.error(f"Legacy engine also failed: {e}")

        # Fall back to basic web search
        return self._handle_research_failure(query, primary_error)

    def _get_legacy_stats(self) -> dict:
        """Get statistics from the legacy research engine."""
        if not self._legacy_engine:
            return {}
        try:
            tracker = self._legacy_engine.progress_tracker
            return {
                "Findings": len(self._legacy_engine.findings),
                "Sources": len(self._legacy_engine.source_reports),
                "Searches": tracker.counters['searches_executed'],
                "URLs": tracker.counters['urls_processed'],
            }
        except Exception:
            return {}

    def _format_research_report(
        self, query: str, full_report: str, stats: dict, elapsed: float,
        findings: Optional[list] = None, evolving_report: Optional[str] = None,
        analyzed_urls: Optional[list] = None,
    ) -> str:
        """Format research report with sources list and expandable raw findings."""
        summary_lines = [
            f"**Duration:** {elapsed:.1f}s",
            f"**Rounds:** {stats.get('Rounds', stats.get('Findings', '?'))}",
            f"**Queries:** {stats.get('Queries', stats.get('Searches', '?'))}",
            f"**URLs Analyzed:** {stats.get('URLs', '?')}",
        ]
        summary_text = " | ".join(summary_lines)

        # Build sources list with clickable links. Keep the curated Sources
        # section filtered for citation quality, but also list every unique URL
        # the research run inspected so the "URLs Analyzed" count is auditable.
        sources_section = ""
        analyzed_urls_section = ""
        url_items = analyzed_urls if analyzed_urls is not None else findings
        if findings or url_items:
            seen_urls = set()
            source_lines = []
            analyzed_seen = set()
            analyzed_lines = []
            for f in findings or []:
                url = f.get("url", "")
                title = f.get("title", "") or url
                summary = f.get("summary", "") or f.get("evidence", "")
                if url and url not in seen_urls and not is_low_quality(summary):
                    seen_urls.add(url)
                    source_lines.append(f"- [{title}]({url})")
            for item in url_items or []:
                url = item.get("url", "")
                title = item.get("title", "") or url
                if url and url not in analyzed_seen:
                    analyzed_seen.add(url)
                    analyzed_lines.append(f"{len(analyzed_lines) + 1}. [{title}]({url})")
            if source_lines:
                sources_section = "\n### Sources\n\n" + "\n".join(source_lines) + "\n"
            if analyzed_lines:
                analyzed_urls_section = "\n### Analyzed URLs\n\n" + "\n".join(analyzed_lines) + "\n"

        # Build raw findings section (individual extractions per source)
        raw_findings_section = ""
        if findings:
            parts = []
            for i, f in enumerate(findings, 1):
                url = f.get("url", "")
                title = f.get("title", "") or "Untitled"
                summary = f.get("summary", "")
                evidence = f.get("evidence", "")
                content = summary if summary else (evidence[:2000] if evidence else "(no content)")
                parts.append(f"**{i}. [{title}]({url})**\n\n{content}")
            raw_findings_section = "\n\n".join(parts)

        # Build expandable collected info section
        collected_section = ""
        if evolving_report or raw_findings_section:
            collected_section = "\n<details>\n<summary><strong>Raw collected findings ({} sources)</strong></summary>\n\n".format(
                len(findings) if findings else 0
            )
            if raw_findings_section:
                collected_section += raw_findings_section + "\n"
            collected_section += "\n</details>\n"

        formatted = f"""---

## Research Summary

{summary_text}

---

{full_report}

{sources_section}
{analyzed_urls_section}
{collected_section}
---

**The AI has analyzed all research findings above. Ask me anything about: "{query}"**
"""
        return formatted

    def _format_error_response(self, error_msg: str, query: str) -> str:
        """Format error response in a user-friendly way."""
        return f"""## Research Engine Unavailable

**Query:** {query}

**Error:** {error_msg}

**Please check:**
1. LLM endpoint is reachable
2. SearXNG is running at the configured instance
3. Application logs for detailed error information

**Troubleshooting:**
- Test basic search: Try the web search toggle first
- Check search config: `/api/search/config`
- Review logs for initialization errors
"""

    def _handle_research_failure(self, query: str, error: str) -> str:
        """Handle research failure with fallback to basic search."""
        try:
            logger.info("Attempting fallback to basic web search...")
            from src.search import comprehensive_web_search

            search_result = comprehensive_web_search(query)

            return f"""## Research Failed - Basic Search Fallback

**Query:** {query}

**Error:** {error}

**Note:** The deep research engine encountered an error. Here are basic search results instead:

---

### Basic Web Search Results

{search_result}

---

**To fix deep research:**
1. Check that your LLM endpoint and search provider are properly configured
2. Verify network connectivity
3. Review application logs for detailed error information

Try the web search toggle for simpler queries, or fix the research engine for comprehensive analysis.
"""

        except Exception as e2:
            logger.error(f"Fallback search also failed: {e2}", exc_info=True)
            return f"""## Complete Research Failure

**Primary Error:** {error}
**Fallback Error:** {str(e2)}

**Please check:**
1. Search provider configuration in Settings -> Search Settings
2. Network connectivity to search APIs
3. Application logs for detailed error information
4. That SearXNG is running (if using SearXNG)

**Debug Info:**
- Search config endpoint: `/api/search/config`
- Test basic search toggle with a simple query first
"""
