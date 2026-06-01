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
import re
import time
from pathlib import Path
from typing import Optional, Dict

from src.research_utils import strip_thinking, is_low_quality

logger = logging.getLogger(__name__)

RESEARCH_DATA_DIR = Path("data/deep_research")


def _bounded_int(value, *, default: int, minimum: int, maximum: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, n))


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
    # Query synthesis & planning
    # ------------------------------------------------------------------

    async def synthesize_query(
        self, sess, latest_message: str,
        llm_endpoint: str, llm_model: str, llm_headers: dict = None,
    ) -> str:
        """Synthesize the conversation into a single focused research query.

        Reads the session history and latest message to produce a clear,
        specific research question that captures the user's full intent.
        Falls back to the latest message if synthesis fails.
        """
        # Build conversation context from history
        history = getattr(sess, 'history', [])
        if len(history) <= 1:
            return latest_message  # No conversation to synthesize

        # Take last 6 messages max for context
        recent = history[-6:]
        convo = "\n".join(
            f"{'User' if m.role == 'user' else 'Assistant'}: {m.content[:500]}"
            for m in recent if m.content
        )
        convo += f"\nUser: {latest_message}"

        try:
            from src.llm_core import llm_call_async

            response = await llm_call_async(
                url=llm_endpoint,
                model=llm_model,
                messages=[{"role": "user", "content":
                    "Read this conversation and write a single, specific research query that captures "
                    "what the user wants to know. Include all relevant context, constraints, and preferences "
                    "they mentioned. Output ONLY the research query — nothing else.\n\n"
                    f"Conversation:\n{convo}"
                }],
                temperature=0.1,
                max_tokens=200,
                headers=llm_headers,
                timeout=15,
                max_retries=1,
            )
            query = strip_thinking(response).strip().strip('"\'')
            if query and len(query) > 5:
                return query
        except Exception as e:
            logger.warning(f"Query synthesis failed: {e}")

        return latest_message  # Fallback

    async def generate_plan(
        self, query: str, llm_endpoint: str, llm_model: str, llm_headers: dict = None,
    ) -> Optional[dict]:
        """Generate a research plan for user review before starting research."""
        try:
            from src.deep_research import RESEARCH_PLAN_PROMPT
            from src.llm_core import llm_call_async

            prompt = RESEARCH_PLAN_PROMPT.format(question=query)
            response = await llm_call_async(
                url=llm_endpoint,
                model=llm_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=1024,
                headers=llm_headers,
                timeout=30,
                max_retries=1,
            )
            response = strip_thinking(response)

            # Try to parse structured plan
            import json as _json
            parsed = None
            try:
                # Try to extract JSON from response
                _clean = response.strip()
                if _clean.startswith("```"):
                    _clean = re.sub(r'^```(?:json)?\s*', '', _clean)
                    _clean = re.sub(r'\s*```$', '', _clean)
                import re as _re
                _match = _re.search(r'\{[\s\S]*\}', _clean)
                if _match:
                    parsed = _json.loads(_match.group())
            except Exception:
                pass

            return {
                "sub_questions": parsed.get("sub_questions", []) if parsed else [],
                "key_topics": parsed.get("key_topics", []) if parsed else [],
                "success_criteria": parsed.get("success_criteria", "") if parsed else "",
                "raw": response,
            }
        except Exception as e:
            logger.warning(f"Research plan generation failed: {e}")
            return None

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
        hard_timeout: int = 600,
        llm_headers: dict = None,
        on_complete: callable = None,
        prior_report: str = "",
        prior_findings: list = None,
        prior_urls: set = None,
        max_rounds: int = 20,
        search_provider: str = None,
        category: str = None,
        extraction_timeout: int = None,
        extraction_concurrency: int = None,
        owner: str = "",
    ) -> dict:
        """Start research as a background task. Returns task info dict.

        max_rounds is the safety cap; the AI's _should_stop decision (after
        min_rounds) terminates the loop earlier in normal operation.
        """
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
            "category": category,
            # SECURITY: track ownership so all reads / saves can filter by user.
            "owner": owner or "",
        }
        self._active_tasks[session_id] = entry

        def on_progress(event):
            entry["progress"] = event

        _completed = False

        def _guarded_complete(*args, **kwargs):
            nonlocal _completed
            if _completed:
                return
            _completed = True
            if on_complete:
                on_complete(*args, **kwargs)

        async def _run():
            # Hard wall-clock timeout — saves partial results if an LLM call hangs
            # hard_timeout passed from start_research()
            try:
                result = await asyncio.wait_for(
                    self.call_research_service(
                        query, llm_endpoint, llm_model,
                        max_time=max_time,
                        progress_callback=on_progress,
                        _task_entry=entry,
                        llm_headers=llm_headers,
                        prior_report=prior_report,
                        prior_findings=prior_findings,
                        prior_urls=prior_urls,
                        max_rounds=max_rounds,
                        search_provider=search_provider,
                        category=category,
                        extraction_timeout=extraction_timeout,
                        extraction_concurrency=extraction_concurrency,
                    ),
                    timeout=hard_timeout,
                )
                entry["result"] = result
                entry["status"] = "done"
                self._save_result(session_id, entry)
                # Persist to DB via callback (ensures result survives even if SSE disconnected)
                try:
                    sources = entry.get("sources", [])
                    researcher = entry.get("researcher")
                    findings = self._extract_raw_findings(researcher.findings) if researcher and researcher.findings else []
                    _guarded_complete(session_id, result, sources, findings)
                except Exception as cb_err:
                    logger.error(f"on_complete callback failed: {cb_err}")
            except asyncio.TimeoutError:
                logger.error(f"Research hard timeout ({hard_timeout}s) for session {session_id}")
                entry["status"] = "error"
                # If we have partial results, save what we have
                researcher = entry.get("researcher")
                if researcher and researcher.evolving_report:
                    entry["result"] = self._format_research_report(
                        query, researcher.evolving_report,
                        researcher.get_stats(), hard_timeout,
                    )
                    entry["status"] = "done"
                    self._save_result(session_id, entry)
                    try:
                        sources = self._extract_sources(researcher.findings) if researcher.findings else []
                        findings = self._extract_raw_findings(researcher.findings) if researcher.findings else []
                        _guarded_complete(session_id, entry["result"], sources, findings)
                    except Exception as e:
                        logger.warning(f"on_complete callback failed in timeout branch: {e}")
                else:
                    entry["result"] = f"Research timed out after {hard_timeout}s. The model may be too slow for deep research."
                on_progress({"phase": "error", "message": f"Research timed out after {hard_timeout}s"})
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
        avg = self.get_avg_duration()
        if session_id in self._active_tasks:
            entry = self._active_tasks[session_id]
            result = {
                "status": entry["status"],
                "progress": entry["progress"],
                "query": entry["query"],
                "started_at": entry["started_at"],
            }
            if avg is not None:
                result["avg_duration"] = round(avg, 1)
            return result
        # Check disk for completed research (skip consumed results)
        path = RESEARCH_DATA_DIR / f"{session_id}.json"
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if data.get("consumed"):
                    return None
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
        # Check disk (skip consumed results)
        path = RESEARCH_DATA_DIR / f"{session_id}.json"
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if data.get("consumed"):
                    return None
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

    def get_raw_findings(self, session_id: str) -> Optional[list]:
        """Get raw per-source findings for display."""
        if session_id in self._active_tasks:
            entry = self._active_tasks[session_id]
            researcher = entry.get("researcher")
            if researcher and researcher.findings:
                return self._extract_raw_findings(researcher.findings)
        # Check disk
        path = RESEARCH_DATA_DIR / f"{session_id}.json"
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                return data.get("raw_findings")
            except Exception as e:
                logger.warning(f"Failed to read raw findings for {session_id}: {e}")
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
                entry = {"url": url, "title": title}
                og_img = f.get("og_image", "")
                if og_img:
                    entry["image"] = og_img
                sources.append(entry)
        return sources

    @staticmethod
    def _extract_raw_findings(findings: list) -> list:
        """Extract [{url, title, summary}] for per-source findings display, filtering junk."""
        try:
            items = []
            for f in findings:
                url = f.get("url", "")
                title = f.get("title", "") or "Untitled"
                summary = f.get("summary", "")
                evidence = f.get("evidence", "")
                content = summary if summary else (evidence[:2000] if evidence else "")
                if url and content and not is_low_quality(content):
                    items.append({"url": url, "title": title, "summary": content})
            return items
        except Exception as e:
            logger.warning(f"Failed to extract raw findings: {e}")
            return []

    def get_avg_duration(self) -> Optional[float]:
        """Compute average research duration from completed results on disk."""
        durations = []
        try:
            for p in RESEARCH_DATA_DIR.glob("*.json"):
                try:
                    data = json.loads(p.read_text(encoding="utf-8"))
                    if data.get("status") == "done":
                        started = data.get("started_at", 0)
                        completed = data.get("completed_at", 0)
                        if started and completed and completed > started:
                            durations.append(completed - started)
                except Exception:
                    continue
        except Exception:
            pass
        if durations:
            return sum(durations) / len(durations)
        return None

    def clear_result(self, session_id: str):
        """Mark result as consumed so it won't be re-rendered on refresh.

        Keeps the JSON on disk so visual reports can be generated later.
        """
        self._active_tasks.pop(session_id, None)
        path = RESEARCH_DATA_DIR / f"{session_id}.json"
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                data["consumed"] = True
                path.write_text(json.dumps(data), encoding="utf-8")
            except Exception:
                pass

    def _save_result(self, session_id: str, entry: dict):
        """Persist completed research result to disk."""
        try:
            # Extract and cache sources + raw findings
            sources = []
            raw_findings = []
            researcher = entry.get("researcher")
            if researcher and researcher.findings:
                sources = self._extract_sources(researcher.findings)
                raw_findings = self._extract_raw_findings(researcher.findings)
            entry["sources"] = sources

            path = RESEARCH_DATA_DIR / f"{session_id}.json"
            data = {
                "query": entry["query"],
                "status": entry["status"],
                "result": entry["result"],
                "raw_report": entry.get("raw_report", ""),
                "sources": sources,
                "raw_findings": raw_findings,
                "stats": entry.get("stats"),
                "category": entry.get("category"),
                "started_at": entry["started_at"],
                "completed_at": time.time(),
                # SECURITY: stamp owner so route handlers can filter by user.
                "owner": entry.get("owner", ""),
            }
            path.write_text(json.dumps(data), encoding="utf-8")
            logger.info(f"Research result saved to {path}")
            try:
                from src.event_bus import fire_event
                fire_event("research_completed", entry.get("owner") or None)
            except Exception:
                logger.debug("research_completed event dispatch failed", exc_info=True)
        except Exception as e:
            logger.error(f"Failed to save research result: {e}")

    def _get_session_json(self, session_id: str) -> Optional[dict]:
        """Load the saved research JSON for a session, if it exists."""
        path = RESEARCH_DATA_DIR / f"{session_id}.json"
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return None

    def get_report_html(self, session_id: str) -> Optional[str]:
        """Generate the visual HTML report for a session (always fresh from JSON)."""
        json_path = RESEARCH_DATA_DIR / f"{session_id}.json"
        if not json_path.exists():
            logger.warning(f"No JSON found for visual report: {json_path}")
            return None

        try:
            from src.visual_report import generate_visual_report

            data = json.loads(json_path.read_text(encoding="utf-8"))
            report_md = data.get("raw_report") or data.get("result", "")
            html_content = generate_visual_report(
                question=data.get("query", ""),
                report_markdown=report_md,
                sources=data.get("sources"),
                stats=data.get("stats"),
                category=data.get("category"),
                session_id=session_id,
                hidden_images=data.get("hidden_images") or [],
            )
            logger.info(f"Visual report generated for {session_id}")
            return html_content
        except Exception as e:
            logger.error(f"Failed to generate visual report: {e}")
            return None

    def hide_image(self, session_id: str, image_url: str) -> bool:
        """Add image_url to the persisted hidden_images list for a research."""
        path = RESEARCH_DATA_DIR / f"{session_id}.json"
        if not path.exists():
            return False
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            hidden = data.get("hidden_images") or []
            if image_url not in hidden:
                hidden.append(image_url)
                data["hidden_images"] = hidden
                path.write_text(json.dumps(data), encoding="utf-8")
                logger.info(f"Hid image {image_url[:80]} for research {session_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to hide image: {e}")
            return False

    def unhide_all_images(self, session_id: str) -> bool:
        """Clear the hidden_images list for a research."""
        path = RESEARCH_DATA_DIR / f"{session_id}.json"
        if not path.exists():
            return False
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            data["hidden_images"] = []
            path.write_text(json.dumps(data), encoding="utf-8")
            logger.info(f"Cleared hidden_images for research {session_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to unhide images: {e}")
            return False

    @staticmethod
    async def _probe_endpoint(endpoint: str, model: str, headers: dict = None):
        """Quick probe to verify the LLM endpoint/model responds before research."""
        from src.llm_core import llm_call_async
        try:
            logger.info(f"Probing {model} at {endpoint} (has_auth={bool(headers and 'Authorization' in (headers or {}))})")
            await llm_call_async(
                url=endpoint,
                model=model,
                messages=[{"role": "user", "content": "hi"}],
                temperature=0,
                max_tokens=5,
                headers=headers,
                timeout=15,
                max_retries=1,
            )
            logger.info(f"Endpoint probe OK: {model}")
        except Exception as e:
            logger.error(f"Probe failed for {model}: {e}")
            err = str(e)
            if "401" in err or "API key" in err or "Unauthorized" in err:
                raise RuntimeError(
                    f"Model '{model}' requires an API key. Check your endpoint configuration."
                ) from e
            raise RuntimeError(
                f"Cannot reach model '{model}' — check that the endpoint is running and accessible."
            ) from e

    async def call_research_service(
        self,
        query: str,
        llm_endpoint: str,
        llm_model: str,
        max_time: int = 300,
        progress_callback=None,
        _task_entry: dict = None,
        llm_headers: dict = None,
        prior_report: str = "",
        prior_findings: list = None,
        prior_urls: set = None,
        max_rounds: int = 20,
        search_provider: str = None,
        category: str = None,
        extraction_timeout: int = None,
        extraction_concurrency: int = None,
    ) -> str:
        """
        Run iterative deep research using the LLM-in-the-loop DeepResearcher.

        Args:
            query: Research question
            llm_endpoint: LLM endpoint URL for chat completions
            llm_model: Model name/ID
            max_time: Maximum research time in seconds (default 5 minutes)
            _task_entry: Internal - registry entry to store researcher ref
            prior_report: Previous report to continue from.
            prior_findings: Previous findings to build on.
            prior_urls: URLs already visited (won't re-fetch).

        Returns:
            Formatted research report with expandable section and summary
        """
        is_continuation = bool(prior_report)
        logger.info(f"{'Continuing' if is_continuation else 'Starting'} IterResearch Deep Research")
        logger.info(f"Query: {query}")
        logger.info(f"LLM: {llm_endpoint} / {llm_model}")
        logger.info(f"Max time: {max_time}s")
        if is_continuation:
            logger.info(f"Prior: {len(prior_findings or [])} findings, {len(prior_urls or set())} URLs")

        # Probe the endpoint before committing to a long research run
        if progress_callback:
            progress_callback({"phase": "probing", "model": llm_model})
        await self._probe_endpoint(llm_endpoint, llm_model, llm_headers)

        try:
            from src.deep_research import DeepResearcher

            from src.settings import get_setting
            _max_report_tokens = int(get_setting("research_max_tokens", 16384))
            _extraction_timeout = _bounded_int(
                extraction_timeout if extraction_timeout is not None else get_setting("research_extraction_timeout_seconds", 90),
                default=90,
                minimum=15,
                maximum=600,
            )
            _extraction_concurrency = _bounded_int(
                extraction_concurrency if extraction_concurrency is not None else get_setting("research_extraction_concurrency", 3),
                default=3,
                minimum=1,
                maximum=12,
            )

            researcher = DeepResearcher(
                llm_endpoint=llm_endpoint,
                llm_model=llm_model,
                llm_headers=llm_headers,
                max_rounds=max_rounds,
                min_rounds=min(3, max_rounds),
                max_time=max_time,
                max_report_tokens=_max_report_tokens,
                extraction_timeout=_extraction_timeout,
                extraction_concurrency=_extraction_concurrency,
                progress_callback=progress_callback,
                search_provider=search_provider,
                category=category,
            )
            if _task_entry is not None:
                _task_entry["researcher"] = researcher

            start_time = time.time()
            report = await researcher.research(
                query,
                prior_report=prior_report,
                prior_findings=prior_findings,
                prior_urls=prior_urls,
            )
            elapsed = time.time() - start_time

            stats = researcher.get_stats()
            logger.info("IterResearch completed successfully")
            for key, value in stats.items():
                logger.info(f"  {key}: {value}")

            # Store raw report and stats for visual report generation
            if _task_entry is not None:
                _task_entry["raw_report"] = strip_thinking(report)
                _task_entry["stats"] = stats

            return self._format_research_report(query, report, stats, elapsed)

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
    ) -> str:
        """Format research report (markdown only — sources/findings handled by frontend)."""
        full_report = strip_thinking(full_report)
        summary_lines = [
            f"**Duration:** {elapsed:.1f}s",
            f"**Rounds:** {stats.get('Rounds', stats.get('Findings', '?'))}",
            f"**Queries:** {stats.get('Queries', stats.get('Searches', '?'))}",
            f"**URLs Analyzed:** {stats.get('URLs', '?')}",
        ]
        summary_text = " | ".join(summary_lines)

        formatted = f"""---

## Research Summary

{summary_text}

---

{full_report}
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
