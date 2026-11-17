import asyncio
import json
import time

from src.deep_research import DeepResearcher
from src.research_navigator import ResearchAction, ResearchNavigator, ResearchPage


class _ActionNavigator(ResearchNavigator):
    def __init__(self):
        super().__init__()
        self.searches = []
        self.fetches = []
        self.browser_reads = []

    async def search(self, query: str, *, count: int = 10):
        self.searches.append(query)
        return [{"url": "https://example.com/search-hit", "title": "Search Hit"}]

    async def fetch(self, url: str, *, timeout: int = 10, max_bytes: int | None = None) -> ResearchPage:
        self.fetches.append(url)
        return ResearchPage(
            url=url,
            title="Fetched",
            content=f"Fetched evidence from {url}",
            success=True,
            retrieval="fetch",
        )

    async def browser_read(self, url: str, *, timeout: int = 45) -> ResearchPage:
        self.browser_reads.append(url)
        return ResearchPage(
            url=url,
            title="Browser",
            content=f"Browser evidence from {url}",
            success=True,
            retrieval="browser",
        )


class _MixedSearchNavigator(_ActionNavigator):
    async def search(self, query: str, *, count: int = 10):
        self.searches.append(query)
        return [
            {
                "url": "https://affiliate.example.com/top-tools",
                "title": "Top Tools Review",
                "snippet": "Commercial roundup.",
            },
            {
                "url": "https://docs.example.com/tool/release-notes",
                "title": "Official release notes",
                "snippet": "Official documentation.",
            },
            {
                "url": "https://docs.example.com/tool/changelog",
                "title": "Official changelog",
                "snippet": "Official documentation.",
            },
            {
                "url": "https://github.com/acme/tool",
                "title": "Project repository",
                "snippet": "Source repository.",
            },
        ]


class _DirectAndSearchNavigator(_ActionNavigator):
    async def search(self, query: str, *, count: int = 10):
        self.searches.append(query)
        return [{
            "url": "https://docs.example.com/search-result",
            "title": "Official search result",
            "snippet": "Official documentation.",
        }]


def _researcher(nav: ResearchNavigator) -> DeepResearcher:
    researcher = DeepResearcher.__new__(DeepResearcher)
    researcher.navigator = nav
    researcher.search_provider_override = None
    researcher.queries_used = set()
    researcher.urls_fetched = set()
    researcher.analyzed_urls = []
    researcher.providers_used = []
    researcher.action_trace = []
    researcher.navigation_trace = []
    researcher.session_id = "research-test"
    researcher.max_urls_per_round = 3
    researcher.extraction_concurrency = 2
    researcher.max_content_chars = 15000
    researcher.max_report_tokens = 4096
    researcher.extraction_timeout = 30
    researcher.query_timeout = 30
    researcher.synthesis_window = 10
    researcher.research_plan = "Use primary sources."
    researcher._cancelled = False
    researcher._start_time = time.time()
    researcher.max_time = 999
    researcher.min_rounds = 2
    researcher.max_rounds = 8
    researcher._progress = None
    return researcher


async def _extract_json(messages, **kwargs):
    return json.dumps({
        "rational": "useful",
        "summary": "Useful finding.",
        "evidence": messages[1]["content"],
    })


def test_planned_web_fetch_reads_specific_url_without_search(monkeypatch):
    monkeypatch.setattr("src.settings.get_setting", lambda key, default=None: True)
    nav = _ActionNavigator()
    researcher = _researcher(nav)
    researcher._llm = _extract_json

    actions = asyncio.run(researcher._plan_research_actions(
        "What changed?",
        "",
        1,
    ))

    # _extract_json is not action JSON, so planning falls back empty.
    assert actions == []

    actions = researcher._normalize_research_actions(
        [ResearchAction("web_fetch", {"url": "https://example.com/official"})],
        max_actions=3,
    )
    findings = asyncio.run(researcher._execute_research_actions(actions, "What changed?"))

    assert nav.searches == []
    assert nav.fetches == ["https://example.com/official"]
    assert findings[0]["retrieval"] == "fetch"


def test_planned_browser_read_uses_browser_before_fetch(monkeypatch):
    monkeypatch.setattr("src.settings.get_setting", lambda key, default=None: True)
    nav = _ActionNavigator()
    researcher = _researcher(nav)
    researcher._llm = _extract_json

    actions = researcher._normalize_research_actions(
        [ResearchAction("browser_read", {"url": "https://example.com/app"})],
        max_actions=3,
    )
    findings = asyncio.run(researcher._execute_research_actions(actions, "What changed?"))

    assert nav.fetches == []
    assert nav.browser_reads == ["https://example.com/app"]
    assert findings[0]["retrieval"] == "browser"
    assert researcher.analyzed_urls[0]["requested_by"] == "browser_read"


def test_planned_browser_snapshot_uses_browser_read_with_trace(monkeypatch):
    monkeypatch.setattr("src.settings.get_setting", lambda key, default=None: True)
    nav = _ActionNavigator()
    researcher = _researcher(nav)
    researcher._llm = _extract_json

    actions = researcher._normalize_research_actions(
        [ResearchAction("browser_snapshot", {"url": "https://example.com/rendered"})],
        max_actions=3,
    )
    researcher._record_action_plan(1, actions)
    findings = asyncio.run(researcher._execute_research_actions(actions, "What changed?"))

    assert actions == [
        ResearchAction(
            "browser_read",
            {"url": "https://example.com/rendered", "requested_by": "browser_snapshot"},
        )
    ]
    assert nav.fetches == []
    assert nav.browser_reads == ["https://example.com/rendered"]
    assert findings[0]["retrieval"] == "browser"
    assert researcher.analyzed_urls[0]["requested_by"] == "browser_snapshot"
    assert researcher.action_trace == [{
        "round": 1,
        "source": "planner",
        "tool": "browser_read",
        "url": "https://example.com/rendered",
        "requested_by": "browser_snapshot",
    }]


def test_actual_private_browser_snapshot_is_supported_as_bounded_read(monkeypatch):
    monkeypatch.setattr("src.settings.get_setting", lambda key, default=None: True)
    nav = _ActionNavigator()
    researcher = _researcher(nav)
    researcher._llm = _extract_json

    actions = researcher._normalize_research_actions(
        [ResearchAction("private_browser", {
            "action": "snapshot",
            "url": "https://example.com/rendered",
        })],
        max_actions=3,
    )
    researcher._record_action_plan(1, actions)
    findings = asyncio.run(researcher._execute_research_actions(actions, "What changed?"))

    assert actions == [
        ResearchAction(
            "browser_read",
            {
                "url": "https://example.com/rendered",
                "requested_by": "private_browser.snapshot",
            },
        )
    ]
    assert nav.fetches == []
    assert nav.browser_reads == ["https://example.com/rendered"]
    assert findings[0]["retrieval"] == "browser"
    assert researcher.action_trace == [{
        "round": 1,
        "source": "planner",
        "tool": "browser_read",
        "url": "https://example.com/rendered",
        "requested_by": "private_browser.snapshot",
    }]


def test_action_normalization_accepts_common_argument_aliases(monkeypatch):
    monkeypatch.setattr("src.settings.get_setting", lambda key, default=None: True)
    researcher = _researcher(_ActionNavigator())

    actions = researcher._normalize_research_actions(
        [
            ResearchAction("web_search", {"q": "official docs"}),
            ResearchAction("web_fetch", {"href": "https://example.com/docs"}),
            ResearchAction("browser_read", {"link": "https://example.com/app"}),
        ],
        max_actions=3,
    )

    assert actions == [
        ResearchAction("web_search", {"query": "official docs"}),
        ResearchAction("web_fetch", {"url": "https://example.com/docs"}),
        ResearchAction("browser_read", {"url": "https://example.com/app"}),
    ]


def test_private_browser_click_is_rejected_in_research_mode(monkeypatch):
    monkeypatch.setattr("src.settings.get_setting", lambda key, default=None: True)
    researcher = _researcher(_ActionNavigator())
    rejected = []

    actions = researcher._normalize_research_actions(
        [ResearchAction("private_browser", {
            "action": "click",
            "selector": "button[type=submit]",
            "url": "https://example.com/form",
        })],
        max_actions=3,
        rejected=rejected,
    )

    assert actions == []
    assert rejected == [{
        "tool": "private_browser",
        "query": "",
        "url": "https://example.com/form",
        "reason": "unsupported private_browser action: click",
    }]


def test_explicit_urls_seed_first_round_reads_before_search(monkeypatch):
    monkeypatch.setattr("src.settings.get_setting", lambda key, default=None: True)
    nav = _ActionNavigator()
    researcher = _researcher(nav)
    researcher._llm = _extract_json

    actions = researcher._merge_seed_actions(
        researcher._explicit_url_actions("Research https://example.com/docs, then verify it"),
        [ResearchAction("web_search", {"query": "example docs verification"})],
        max_actions=4,
    )
    findings = asyncio.run(researcher._execute_research_actions(actions, "What changed?"))

    assert nav.fetches[0] == "https://example.com/docs"
    assert nav.searches == ["example docs verification"]
    assert any(f["url"] == "https://example.com/docs" for f in findings)
    assert researcher.analyzed_urls[0]["requested_by"] == "explicit_url"


def test_search_hits_prefer_primary_sources_and_host_diversity(monkeypatch):
    monkeypatch.setattr("src.settings.get_setting", lambda key, default=None: True)
    nav = _MixedSearchNavigator()
    researcher = _researcher(nav)
    researcher.max_urls_per_round = 2
    researcher._llm = _extract_json

    findings = asyncio.run(researcher._execute_research_actions(
        [ResearchAction("web_search", {"query": "tool release notes"})],
        "What changed?",
    ))

    assert [f["url"] for f in findings] == [
        "https://docs.example.com/tool/release-notes",
        "https://github.com/acme/tool",
    ]
    assert nav.fetches == [
        "https://docs.example.com/tool/release-notes",
        "https://github.com/acme/tool",
    ]
    assert researcher.analyzed_urls[0]["source_kind"] == "primary"
    assert researcher.analyzed_urls[1]["source_kind"] == "primary"


def test_direct_reads_do_not_consume_search_fetch_budget(monkeypatch):
    monkeypatch.setattr("src.settings.get_setting", lambda key, default=None: True)
    nav = _DirectAndSearchNavigator()
    researcher = _researcher(nav)
    researcher.max_urls_per_round = 1
    researcher._llm = _extract_json

    findings = asyncio.run(researcher._execute_research_actions(
        [
            ResearchAction("web_fetch", {"url": "https://example.com/user-link"}),
            ResearchAction("web_search", {"query": "official docs"}),
        ],
        "What changed?",
    ))

    assert [f["url"] for f in findings] == [
        "https://example.com/user-link",
        "https://docs.example.com/search-result",
    ]
    assert nav.fetches == [
        "https://example.com/user-link",
        "https://docs.example.com/search-result",
    ]
    assert researcher.analyzed_urls[0]["requested_by"] == "web_fetch"
    assert researcher.analyzed_urls[1]["requested_by"] == "web_search"


def test_explicit_url_extraction_dedupes_and_strips_sentence_punctuation():
    researcher = _researcher(_ActionNavigator())

    actions = researcher._explicit_url_actions(
        "Read https://example.com/docs, https://example.com/docs and https://docs.example.com/page)."
    )

    assert actions == [
        ResearchAction("web_fetch", {"url": "https://example.com/docs", "requested_by": "explicit_url"}),
        ResearchAction("web_fetch", {"url": "https://docs.example.com/page", "requested_by": "explicit_url"}),
    ]


def test_action_planning_returns_model_chosen_actions(monkeypatch):
    monkeypatch.setattr("src.settings.get_setting", lambda key, default=None: True)
    nav = _ActionNavigator()
    researcher = _researcher(nav)
    seen_prompt = {}

    async def _planner(messages, **kwargs):
        seen_prompt["text"] = messages[0]["content"]
        return json.dumps({
            "actions": [
                {"tool": "web_search", "query": "official release notes"},
                {"tool": "web_fetch", "url": "https://example.com/docs"},
            ]
        })

    researcher._llm = _planner

    actions = asyncio.run(researcher._plan_research_actions("What changed?", "", 1))

    assert [(a.tool, a.args) for a in actions] == [
        ("web_search", {"query": "official release notes"}),
        ("web_fetch", {"url": "https://example.com/docs"}),
    ]
    assert "Evidence/source state:" in seen_prompt["text"]
    assert "Structured source coverage JSON:" in seen_prompt["text"]
    assert "No sources gathered yet" in seen_prompt["text"]
    assert "never use the meta request itself as the search query" in seen_prompt["text"]


def test_action_planning_rejects_meta_only_search_query(monkeypatch):
    monkeypatch.setattr("src.settings.get_setting", lambda key, default=None: True)
    nav = _ActionNavigator()
    researcher = _researcher(nav)

    async def _planner(messages, **kwargs):
        return json.dumps({
            "actions": [
                {"tool": "web_search", "query": "can you search"},
                {"tool": "web_search", "query": "Gustav III Swedish king dancing masquerade ball"},
            ]
        })

    researcher._llm = _planner

    actions = asyncio.run(researcher._plan_research_actions(
        "Which Swedish king was most associated with dancing?",
        "",
        1,
    ))

    assert actions == [
        ResearchAction("web_search", {"query": "Gustav III Swedish king dancing masquerade ball"})
    ]
    assert researcher.action_trace == [
        {
            "round": 1,
            "source": "planner",
            "tool": "web_search",
            "query": "Gustav III Swedish king dancing masquerade ball",
        },
        {
            "round": 1,
            "source": "planner",
            "status": "skipped",
            "tool": "web_search",
            "query": "can you search",
            "reason": "meta search request",
        },
    ]


def test_generate_queries_rejects_meta_only_queries():
    nav = _ActionNavigator()
    researcher = _researcher(nav)

    async def _planner(messages, **kwargs):
        return json.dumps(["can you search", "Gustav III Swedish king dancing"])

    researcher._llm = _planner

    queries = asyncio.run(researcher._generate_queries(
        "Which Swedish king was most associated with dancing?",
        "",
        1,
    ))

    assert queries == ["Gustav III Swedish king dancing"]


def test_search_result_prioritization_drops_generic_hits_when_topic_hits_exist():
    researcher = _researcher(_ActionNavigator())
    results = researcher._prioritize_search_results([
        {"url": "https://dictionary.example/best", "title": "Best Definition"},
        {
            "url": "https://example.com/local-ai",
            "title": "Local AI hardware guide",
            "snippet": "GPU memory and computer requirements for local models",
        },
    ], limit=10, question="Best computer to run local AI")

    assert [item["url"] for item in results] == ["https://example.com/local-ai"]


def test_action_planning_sees_recent_navigation_trace(monkeypatch):
    monkeypatch.setattr("src.settings.get_setting", lambda key, default=None: True)
    nav = _ActionNavigator()
    researcher = _researcher(nav)
    researcher.navigation_trace = [{
        "tool": "web_search",
        "query": "bad old query",
        "status": "no_results",
        "results": 0,
    }]
    seen_prompt = {}

    async def _planner(messages, **kwargs):
        seen_prompt["text"] = messages[0]["content"]
        return json.dumps({"actions": [{"tool": "web_search", "query": "better query"}]})

    researcher._llm = _planner

    actions = asyncio.run(researcher._plan_research_actions("What changed?", "", 2))

    assert actions == [ResearchAction("web_search", {"query": "better query"})]
    assert "Recent navigation observations:" in seen_prompt["text"]
    assert 'web_search "bad old query" -> no_results; 0 result(s)' in seen_prompt["text"]


def test_research_stats_include_browser_reads():
    nav = _ActionNavigator()
    nav.browser_fetches = 2
    researcher = _researcher(nav)
    researcher.round_count = 1
    researcher.llm_model = "test-model"
    researcher.category = None

    stats = researcher.get_stats()

    assert stats["Browser reads"] == 2


def test_navigation_trace_records_search_and_fetch_outcomes(monkeypatch):
    monkeypatch.setattr("src.settings.get_setting", lambda key, default=None: True)
    nav = _ActionNavigator()
    researcher = _researcher(nav)
    researcher._llm = _extract_json

    findings = asyncio.run(researcher._execute_research_actions(
        [ResearchAction("web_search", {"query": "release notes"})],
        "What changed?",
    ))

    assert findings
    assert researcher.navigation_trace[0] == {
        "tool": "web_search",
        "status": "ok",
        "query": "release notes",
        "results": 1,
    }
    assert researcher.navigation_trace[-1]["tool"] == "web_fetch"
    assert researcher.navigation_trace[-1]["status"] == "ok"
    assert researcher.navigation_trace[-1]["source_score"] >= 0


def test_navigation_trace_records_browser_reads():
    nav = _ActionNavigator()
    researcher = _researcher(nav)
    researcher._llm = _extract_json

    findings = asyncio.run(researcher._execute_research_actions(
        [ResearchAction("browser_read", {"url": "https://example.com/app"})],
        "What changed?",
    ))

    assert findings
    assert researcher.navigation_trace[-1]["tool"] == "browser_read"
    assert researcher.navigation_trace[-1]["retrieval"] == "browser"


def test_navigation_trace_is_bounded_and_summarized():
    researcher = _researcher(_ActionNavigator())

    for i in range(90):
        researcher._record_navigation("web_search", query=f"query {i}", status="ok", results=i)

    assert len(researcher.navigation_trace) == 80
    assert researcher.navigation_trace[0]["query"] == "query 10"
    summary = researcher._navigation_trace_summary(limit=2)
    assert '"query 88" -> ok; 88 result(s)' in summary
    assert '"query 89" -> ok; 89 result(s)' in summary


def test_synthesis_prompt_includes_source_state_and_navigation_trace():
    researcher = _researcher(_ActionNavigator())
    researcher.navigation_trace = [{
        "tool": "web_fetch",
        "url": "https://example.com/source",
        "status": "ok",
        "source_kind": "primary",
        "source_score": 82,
    }]
    researcher.findings = [{
        "url": "https://example.com/source",
        "title": "Source",
        "summary": "Useful evidence.",
        "source_kind": "primary",
        "source_score": 82,
    }]
    seen_prompt = {}

    async def _capture(messages, **kwargs):
        seen_prompt["text"] = messages[0]["content"]
        return "updated report"

    researcher._llm = _capture

    result = asyncio.run(researcher._synthesize(
        "What changed?",
        researcher.findings,
        "current report",
    ))

    assert result == "updated report"
    assert "Evidence/source state:" in seen_prompt["text"]
    assert "Structured source coverage JSON:" in seen_prompt["text"]
    assert "Recent navigation observations:" in seen_prompt["text"]
    assert '"primary_or_official": 1' in seen_prompt["text"]
    assert "web_fetch https://example.com/source -> ok; primary score 82" in seen_prompt["text"]
    assert "avoid overconfident conclusions" in seen_prompt["text"]


def test_final_report_prompt_includes_source_state_and_navigation_trace():
    researcher = _researcher(_ActionNavigator())
    researcher.category = None
    researcher.max_report_tokens = 4096
    researcher.navigation_trace = [{
        "tool": "web_search",
        "query": "official docs",
        "status": "ok",
        "results": 4,
    }]
    researcher.findings = [{
        "url": "https://docs.example.com",
        "title": "Docs",
        "summary": "Official evidence.",
        "source_kind": "official",
        "source_score": 90,
    }]
    seen_prompt = {}

    async def _capture(messages, **kwargs):
        seen_prompt["text"] = messages[0]["content"]
        return "final report"

    researcher._llm = _capture

    result = asyncio.run(researcher._final_report("What changed?", "evidence report"))

    assert result == "final report"
    assert "Evidence/source state:" in seen_prompt["text"]
    assert "Structured source coverage JSON:" in seen_prompt["text"]
    assert "Recent navigation observations:" in seen_prompt["text"]
    assert '"primary_or_official": 1' in seen_prompt["text"]
    assert 'web_search "official docs" -> ok; 4 result(s)' in seen_prompt["text"]
    assert "Prefer primary/official evidence" in seen_prompt["text"]


def test_source_state_summary_reports_mix_and_gaps():
    researcher = _researcher(_ActionNavigator())
    researcher.analyzed_urls = [
        {"url": "https://example-review-site.com/best-tools", "title": "Best Tools"},
    ]
    researcher.findings = [
        {
            "url": "https://example-review-site.com/best-tools",
            "title": "Best Tools",
            "summary": "Commercial roundup.",
            "source_kind": "commercial",
            "source_score": 40,
        }
    ]

    summary = researcher._source_state_summary()

    assert "Source mix: commercial=1" in summary
    assert "primary/official evidence missing" in summary
    assert "commercial/listicle evidence needs verification" in summary


def test_source_coverage_returns_machine_readable_counts():
    researcher = _researcher(_ActionNavigator())
    researcher.analyzed_urls = [
        {"url": "https://docs.example.com/guide", "title": "Guide", "retrieval": "browser"},
        {"url": "https://review.example.com/best", "title": "Best"},
    ]
    researcher.findings = [
        {
            "url": "https://docs.example.com/guide",
            "title": "Guide",
            "summary": "Official documentation.",
            "retrieval": "browser",
            "source_kind": "primary",
            "source_score": 87,
        },
        {
            "url": "https://review.example.com/best",
            "title": "Best",
            "summary": "Commercial roundup.",
            "source_kind": "commercial",
            "source_score": 40,
        },
    ]

    coverage = researcher._source_coverage()

    assert coverage["sources_analyzed"] == 2
    assert coverage["useful_findings"] == 2
    assert coverage["unique_urls"] == 2
    assert coverage["source_mix"] == {"primary": 1, "commercial": 1}
    assert coverage["primary_or_official"] == 1
    assert coverage["browser_reads"] == 1
    assert coverage["best_source_score"] == 87
    assert coverage["gaps"] == ["source diversity still thin"]


def test_depth_gate_blocks_early_stop_on_thin_evidence():
    researcher = _researcher(_ActionNavigator())
    researcher.max_rounds = 5
    researcher.findings = [{
        "url": "https://example.com/a",
        "title": "A",
        "summary": "One useful source.",
        "source_kind": "secondary",
        "source_score": 55,
    }]
    researcher.analyzed_urls = [{"url": "https://example.com/a", "title": "A"}]

    assert researcher._needs_more_evidence_before_stop(2) == "fewer than two useful findings"


def test_depth_gate_allows_stop_at_max_rounds_even_if_evidence_is_thin():
    researcher = _researcher(_ActionNavigator())
    researcher.max_rounds = 2
    researcher.findings = []
    researcher.analyzed_urls = []

    assert researcher._needs_more_evidence_before_stop(2) == ""


def test_stop_prompt_includes_structured_source_coverage():
    researcher = _researcher(_ActionNavigator())
    researcher.max_rounds = 8
    researcher.findings = [{
        "url": "https://docs.example.com",
        "title": "Docs",
        "summary": "Official evidence.",
        "source_kind": "official",
        "source_score": 90,
    }]
    researcher.analyzed_urls = [{"url": "https://docs.example.com", "title": "Docs"}]
    seen_prompt = {}

    async def _capture(messages, **kwargs):
        seen_prompt["text"] = messages[0]["content"]
        return "YES — enough."

    researcher._llm = _capture

    assert asyncio.run(researcher._should_stop("What changed?", "report", 3)) is True
    assert "Structured source coverage JSON:" in seen_prompt["text"]
    assert '"primary_or_official": 1' in seen_prompt["text"]
