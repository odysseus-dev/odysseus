# services/research/service.py
"""Research service — deep research with LLM-in-the-loop."""

from dataclasses import dataclass, field
from typing import List, Optional, Callable

from .research_handler import ResearchHandler


@dataclass
class ResearchSource:
    """A source found during research."""

    url: str
    title: str
    snippet: str
    relevance: float = 0.0


@dataclass
class ResearchResult:
    """Result of a deep research query."""

    query: str
    summary: str
    sources: List[ResearchSource] = field(default_factory=list)
    sections: List[str] = field(default_factory=list)
    tokens_used: int = 0
    duration_seconds: float = 0.0


class ResearchService:
    """
    Deep research service.

    Usage:
        service = ResearchService()
        result = await service.research("quantum computing advances 2024")
        print(result.summary)
    """

    def __init__(self):
        self.handler = ResearchHandler()
        self._active: dict = {}

    async def research(
        self,
        topic: str,
        llm_endpoint: str,
        llm_model: str,
        max_time: int = 300,
        on_progress: Optional[Callable[[dict], None]] = None,
    ) -> ResearchResult:
        """
        Perform deep research on a topic.

        Args:
            topic: Research topic/question
            llm_endpoint: LLM API endpoint
            llm_model: Model to use
            max_time: Maximum time in seconds
            on_progress: Optional progress callback

        Returns:
            ResearchResult with findings
        """
        import time

        start = time.time()

        result = await self.handler.call_research_service(
            topic,
            llm_endpoint,
            llm_model,
            max_time=max_time,
            progress_callback=on_progress,
        )

        duration = time.time() - start

        # Parse result into structured format
        sources = [
            ResearchSource(
                url=s.get("url", ""),
                title=s.get("title", ""),
                snippet=s.get("snippet", ""),
                relevance=s.get("relevance", 0.0),
            )
            for s in result.get("sources", [])
        ]

        return ResearchResult(
            query=topic,
            summary=result.get("summary", result.get("answer", "")),
            sources=sources,
            sections=result.get("sections", []),
            tokens_used=result.get("tokens_used", 0),
            duration_seconds=duration,
        )

    def start_background(
        self,
        session_id: str,
        topic: str,
        llm_endpoint: str,
        llm_model: str,
        max_time: int = 300,
    ) -> dict:
        """Start research in background. Returns task info."""
        return self.handler.start_research(
            session_id, topic, llm_endpoint, llm_model, max_time
        )

    def get_status(self, session_id: str) -> Optional[dict]:
        """Get status of background research."""
        return self.handler.get_status(session_id)

    def cancel(self, session_id: str) -> bool:
        """Cancel background research."""
        return self.handler.cancel_research(session_id)
