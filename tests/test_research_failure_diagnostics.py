"""Research failures preserve their actual stage through task and disk APIs."""

import asyncio
import json
import logging
from types import SimpleNamespace

import httpx
import pytest

from src.deep_research import DeepResearcher
from src import research_handler as handler_module
from src import llm_core
from src.research_handler import ResearchHandler, _research_failure_fields
from services.search.analytics import ProviderUnavailableError
from services.search import core as search_core, providers
from routes.research import research_routes


@pytest.fixture(autouse=True)
def no_live_browser(monkeypatch):
    async def no_browser(*args, **kwargs):
        return None

    monkeypatch.setattr(DeepResearcher, "_browser_fallback", no_browser)


def _researcher(monkeypatch, *, max_empty_rounds=1, **kwargs):
    options = {"max_rounds": 2, **kwargs}
    if max_empty_rounds is not None:
        options["max_empty_rounds"] = max_empty_rounds
    researcher = DeepResearcher(
        llm_endpoint="https://model.test/v1/chat/completions", llm_model="fixture-model",
        **options,
    )

    async def plan(question):
        return "Fixture plan"

    async def category(question, research_plan=""):
        return None

    async def actions(*args):
        return []

    async def queries(question, report, round_num):
        researcher.queries_used.add("fixture query")
        return ["fixture query"]

    monkeypatch.setattr(researcher, "_create_plan", plan)
    monkeypatch.setattr(researcher, "_classify_category", category)
    monkeypatch.setattr(researcher, "_generate_queries", queries)
    monkeypatch.setattr(researcher, "_plan_research_actions", actions)
    monkeypatch.setattr(providers, "_get_search_settings", lambda: {"search_provider": "searxng"})
    monkeypatch.setattr(search_core, "_build_provider_chain", lambda _: ["searxng", "duckduckgo"])
    return researcher


def test_blocked_search_is_classified_before_any_extraction(monkeypatch):
    events = []
    researcher = _researcher(monkeypatch, progress_callback=events.append)
    calls = []

    def blocked(provider, query, count):
        calls.append(provider)
        raise ProviderUnavailableError(f"{provider} requires CAPTCHA")

    async def unexpected_extract(*args):
        pytest.fail("No page can be extracted when search returned no URL")

    monkeypatch.setattr(search_core, "_call_provider", blocked)
    monkeypatch.setattr(researcher, "_fetch_and_extract", unexpected_extract)

    report = asyncio.run(researcher.research("fixture question"))

    assert calls == ["searxng", "duckduckgo"]
    assert researcher.failure_stage == "search"
    assert "CAPTCHA" in researcher.failure_message
    assert "no pages" in researcher.failure_message
    assert "Search unavailable" in report
    assert events[-1]["failure_stage"] == "search"
    assert not researcher.urls_fetched


@pytest.mark.parametrize("exit_mode", ["one_round", "time_limit"])
def test_early_exit_preserves_search_errors_with_default_empty_threshold(monkeypatch, exit_mode):
    researcher = _researcher(
        monkeypatch, max_empty_rounds=None, max_rounds=1 if exit_mode == "one_round" else 2,
    )
    assert researcher.max_empty_rounds == 2

    def blocked(*args):
        raise ProviderUnavailableError("SearXNG CAPTCHA")

    monkeypatch.setattr(search_core, "_call_provider", blocked)
    if exit_mode == "time_limit":
        checks = 0

        def time_exceeded():
            nonlocal checks
            checks += 1
            return checks >= 3

        monkeypatch.setattr(researcher, "_time_exceeded", time_exceeded)

    report = asyncio.run(researcher.research("fixture question"))

    assert researcher.failure_stage == "search"
    assert "CAPTCHA" in researcher.failure_message
    assert "CAPTCHA" in report
    assert researcher.round_count <= 2


def test_pages_without_usable_evidence_are_not_called_search_failure(monkeypatch):
    researcher = _researcher(monkeypatch)
    monkeypatch.setattr(search_core, "_call_provider", lambda *args: [
        {"url": "https://source.test/page", "title": "Fixture page"},
    ])

    async def inaccessible_page(*args):
        return None

    monkeypatch.setattr(researcher, "_fetch_and_extract", inaccessible_page)

    report = asyncio.run(researcher.research("fixture question"))

    assert researcher.failure_stage == "extraction"
    assert "found pages" in researcher.failure_message
    assert "Search unavailable" not in report
    assert researcher.urls_fetched == {"https://source.test/page"}


def test_empty_model_extraction_is_not_counted_as_a_finding(monkeypatch):
    researcher = _researcher(monkeypatch)
    monkeypatch.setattr(search_core, "_call_provider", lambda *args: [
        {"url": "https://source.test/page", "title": "Fixture page"},
    ])
    monkeypatch.setattr("src.search.content.fetch_webpage_content", lambda *args, **kwargs: {
        "success": True, "content": "A public fixture page about knowledge distillation.",
    })

    async def empty_output(*args, **kwargs):
        return ""

    monkeypatch.setattr(researcher, "_llm", empty_output)

    asyncio.run(researcher.research("fixture question"))

    assert researcher.findings == []
    assert researcher.failure_stage == "extraction"


@pytest.mark.parametrize("content,finish_reason", [
    ("", "stop"),
    ('{"summary":"unfinished', "length"),
])
async def test_incomplete_llm_output_cannot_become_research_evidence(monkeypatch, content, finish_reason):
    researcher = _researcher(monkeypatch)
    monkeypatch.setattr(search_core, "_call_provider", lambda *args: [
        {"url": "https://source.test/page", "title": "Fixture page"},
    ])
    monkeypatch.setattr("src.search.content.fetch_webpage_content", lambda *args, **kwargs: {
        "success": True, "content": "A public fixture page about knowledge distillation.",
    })
    monkeypatch.setattr(llm_core, "_response_cache", {})
    monkeypatch.setattr(llm_core, "_response_model_cache", {})
    requests = []

    def upstream(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{
            "message": {"content": content, "reasoning_content": "Unfinished model analysis"},
            "finish_reason": finish_reason,
        }]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        monkeypatch.setattr(llm_core, "_get_http_client", lambda: client)
        report = await researcher.research("fixture question")

    assert len(requests) == 1
    assert requests[0]["max_tokens"] == 2048
    assert researcher.findings == []
    assert researcher.failure_stage == "extraction"
    assert "Unfinished model analysis" not in report
    assert not llm_core._response_cache


async def test_completed_llm_extraction_keeps_final_evidence(monkeypatch):
    researcher = _researcher(monkeypatch)
    monkeypatch.setattr("src.search.content.fetch_webpage_content", lambda *args, **kwargs: {
        "success": True, "content": "A public fixture page about knowledge distillation.",
    })
    monkeypatch.setattr(llm_core, "_response_cache", {})
    monkeypatch.setattr(llm_core, "_response_model_cache", {})

    def upstream(request):
        return httpx.Response(200, json={"choices": [{
            "message": {
                "content": json.dumps({
                    "summary": "Knowledge distillation trains a student using a teacher's outputs.",
                    "evidence": "A smaller student can learn from a larger teacher model.",
                }),
                "reasoning_content": "Internal analysis of the page",
            },
            "finish_reason": "stop",
        }]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        monkeypatch.setattr(llm_core, "_get_http_client", lambda: client)
        finding = await researcher._fetch_and_extract(
            "https://source.test/page", "fixture question", "Fixture page",
        )

    assert finding["summary"].startswith("Knowledge distillation trains")
    assert "Internal analysis" not in finding["evidence"]
    assert finding["url"] == "https://source.test/page"


async def test_research_uses_nonthinking_for_small_steps_and_default_for_reports(monkeypatch):
    researcher = DeepResearcher(
        llm_endpoint="http://localhost:8081/v1", llm_model="Qwen3.5-9B",
    )
    # Exercise every LLM helper independently of the small-model fast path.
    researcher.simple_research_mode = False
    monkeypatch.setattr("src.search.content.fetch_webpage_content", lambda *args, **kwargs: {
        "success": True, "content": "A public fixture page about knowledge distillation.",
    })
    monkeypatch.setattr(llm_core, "_response_cache", {})
    monkeypatch.setattr(llm_core, "_response_model_cache", {})
    monkeypatch.setattr("src.model_context._configured_endpoint_kind", lambda _: None)
    date_context = "Today's date is October 05, 2026 (2026-10-05). Assess publication dates relative to today."
    monkeypatch.setattr("src.deep_research.current_date_context", lambda: date_context)
    responses = [
        json.dumps({"sub_questions": ["What is distillation?"]}),
        "general",
        json.dumps(["knowledge distillation"]),
        json.dumps({"actions": [{"tool": "web_search", "query": "teacher student distillation"}]}),
        json.dumps({"summary": "A student learns from teacher outputs.", "evidence": "Fixture evidence."}),
        "YES — sufficient evidence.",
        "A synthesized report based on the fixture evidence.",
        "A detailed final report. " + "Evidence " * 400,
    ]
    requests = []

    def upstream(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{
            "message": {"content": responses.pop(0)}, "finish_reason": "stop",
        }]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        monkeypatch.setattr(llm_core, "_get_http_client", lambda: client)
        await researcher._create_plan("fixture question")
        await researcher._classify_category("fixture question")
        await researcher._generate_queries("fixture question", "", 1)
        monkeypatch.setattr("src.settings.get_setting", lambda key, default=None: default)
        actions = await researcher._plan_research_actions("fixture question", "", 1)
        assert actions[0].args["query"] == "teacher student distillation"
        finding = await researcher._fetch_and_extract("https://source.test", "fixture question", "Fixture")
        await researcher._should_stop("fixture question", "report", 2)
        report = await researcher._synthesize("fixture question", [finding], "")
        await researcher._final_report("fixture question", report)

    assert len(requests) == 8
    assert all(payload["chat_template_kwargs"] == {"enable_thinking": False} for payload in requests[:6])
    assert all("chat_template_kwargs" not in payload for payload in requests[6:])
    # The real transport sees trusted date grounding at every step, including
    # stop, synthesis, and final report, which previously used a stale model year.
    assert all(payload["messages"][0] == {"role": "system", "content": date_context} for payload in requests)
    extraction_messages = requests[4]["messages"]
    assert "public fixture page" not in extraction_messages[0]["content"]
    assert extraction_messages[-1]["role"] == "user"
    assert "UNTRUSTED_SOURCE_DATA" in extraction_messages[-1]["content"]
    assert "public fixture page" in extraction_messages[-1]["content"]
    assert '"relevant": false' in extraction_messages[1]["content"]
    assert requests[5]["messages"][1]["content"].startswith(date_context)
    assert "3-8 content words" in requests[2]["messages"][1]["content"]
    assert "Do not invent hardware minimums" in requests[6]["messages"][1]["content"]
    assert "Do not invent hardware minimums" in requests[7]["messages"][1]["content"]


@pytest.mark.parametrize("response", [
    "{}",
    json.dumps({"relevant": False, "summary": "A physics detector specification.", "evidence": "Detector dimensions."}),
    json.dumps({"relevant": "false", "summary": "A physics detector specification.", "evidence": "Detector dimensions."}),
    json.dumps({"relevant": True, "summary": "", "evidence": ""}),
    json.dumps({"summary": "   ", "evidence": "Fixture evidence."}),
    json.dumps({"summary": "Fixture summary.", "evidence": ""}),
    json.dumps({"summary": "The source document is completely irrelevant to the research goal.", "evidence": "ATLAS detector performance."}),
    json.dumps({"summary": "The source concerns astrophysics, with zero overlap with the topic of LLM distillation.", "evidence": "Neutrino detection."}),
    "The provided text concerns gravitational waves, making it irrelevant to the specified research goal.",
    "No relevant information could be extracted from this page.",
])
async def test_irrelevant_or_empty_extraction_is_not_research_evidence(monkeypatch, response):
    researcher = DeepResearcher(llm_endpoint="http://local.test/v1", llm_model="fixture")
    monkeypatch.setattr("src.search.content.fetch_webpage_content", lambda *args, **kwargs: {
        "success": True, "content": "A public source fixture.",
    })

    async def output(*args, **kwargs):
        return response

    monkeypatch.setattr(researcher, "_llm", output)
    assert await researcher._fetch_and_extract("https://source.test", "LLM distillation", "Fixture") is None


@pytest.mark.parametrize("response", [
    json.dumps({"relevant": True, "summary": "A student learns from a teacher.", "evidence": "Teacher-generated examples train the student."}),
    json.dumps({"summary": "A student learns from a teacher.", "evidence": "Teacher-generated examples train the student."}),
    json.dumps({"relevant": True, "summary": "The evaluation dataset has zero overlap with the training examples, avoiding test contamination.", "evidence": "The train and evaluation sets are disjoint."}),
    json.dumps({"relevant": True, "summary": "The source gives no information regarding training times, so the duration is unknown.", "evidence": "Training times are not reported."}),
    "Teacher-generated examples train a student model, which learns from the teacher's outputs.",
])
async def test_useful_extraction_keeps_explicit_and_legacy_formats(monkeypatch, response):
    researcher = DeepResearcher(llm_endpoint="http://local.test/v1", llm_model="fixture")
    monkeypatch.setattr("src.search.content.fetch_webpage_content", lambda *args, **kwargs: {
        "success": True, "content": "A public source fixture.",
    })

    async def output(*args, **kwargs):
        return response

    monkeypatch.setattr(researcher, "_llm", output)
    finding = await researcher._fetch_and_extract("https://source.test", "LLM distillation", "Fixture")
    assert finding["url"] == "https://source.test"
    assert finding["summary"]
    assert finding["evidence"]


def test_working_fallback_does_not_leave_failure_metadata(monkeypatch):
    researcher = _researcher(monkeypatch)

    def fallback(provider, query, count):
        if provider == "searxng":
            raise ProviderUnavailableError("CAPTCHA")
        return [{"url": "https://source.test/page"}]

    monkeypatch.setattr(search_core, "_call_provider", fallback)

    assert asyncio.run(researcher._search("fixture")) == [{"url": "https://source.test/page"}]
    assert researcher.providers_used == ["duckduckgo"]
    assert researcher.failure_stage == researcher.failure_message == ""
    assert researcher._search_errors == []


@pytest.mark.parametrize("path", ["small_model_queries", "standard_queries", "action_planner"])
async def test_active_planners_receive_short_queries_and_named_source_guidance(monkeypatch, path):
    researcher = DeepResearcher(llm_endpoint="http://localhost:8081/v1", llm_model="Qwen3.5-9B")
    researcher.simple_research_mode = path == "small_model_queries"
    monkeypatch.setattr("src.settings.get_setting", lambda key, default=None: default)
    monkeypatch.setattr(llm_core, "_response_cache", {})
    monkeypatch.setattr(llm_core, "_response_model_cache", {})
    requests = []
    query = "Hugging Face PEFT LoRA documentation"
    output = {"actions": [{"tool": "web_search", "query": query}]} if path == "action_planner" else [query]

    def upstream(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{
            "message": {"content": json.dumps(output)}, "finish_reason": "stop",
        }]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        monkeypatch.setattr(llm_core, "_get_http_client", lambda: client)
        question = "Explain LoRA using the Hugging Face PEFT documentation and original paper."
        if path == "action_planner":
            actions = await researcher._plan_research_actions(question, "", 1)
            assert actions[0].tool == "web_search"
            assert actions[0].args["query"] == query
        else:
            assert await researcher._generate_queries(question, "", 1) == [query]

    assert len(requests) == 1
    assert requests[0]["chat_template_kwargs"] == {"enable_thinking": False}
    prompt = requests[0]["messages"][-1]["content"]
    assert question in prompt
    assert "3-8 content words, not full sentences" in prompt
    assert "explicitly named sources" in prompt
    assert "exact project or paper name" in prompt
    assert "Do not add a year unless the question explicitly needs" in prompt
    assert "Do not introduce unrequested hardware" in prompt


def test_explicit_legacy_search_error_is_inferred_without_source_guessing():
    legacy = {
        "sources": [],
        "raw_report": "**Search unavailable** — Web search failed after 2 rounds. Error: no results from searxng\n\nCheck settings.",
    }
    assert _research_failure_fields(legacy) == {
        "failure_stage": "search",
        "failure_message": "Web search failed after 2 rounds. Error: no results from searxng",
    }
    assert _research_failure_fields({"sources": [], "raw_report": "A brief report"}) == {
        "failure_stage": "", "failure_message": "",
    }
    assert _research_failure_fields({**legacy, "sources": [{"url": "https://source.test"}]}) == {
        "failure_stage": "", "failure_message": "",
    }


def _handler(tmp_path, monkeypatch):
    monkeypatch.setattr(handler_module, "RESEARCH_DATA_DIR", tmp_path)
    monkeypatch.setattr(research_routes, "DEEP_RESEARCH_DIR", str(tmp_path))
    handler = ResearchHandler.__new__(ResearchHandler)
    handler._active_tasks = {}
    handler._legacy_engine = None
    return handler


def _route(router, path, method):
    return next(route.endpoint for route in router.routes if route.path == path and method in route.methods)


def test_saved_failure_survives_status_library_and_result_peek(tmp_path, monkeypatch):
    handler = _handler(tmp_path, monkeypatch)
    entry = {
        "query": "Fixture question", "status": "done", "result": "Search unavailable",
        "raw_report": "Search unavailable", "started_at": 1, "owner": "alice",
        "stats": {"Duration": "2s", "Rounds": 1},
        "failure_stage": "search", "failure_message": "SearXNG requires CAPTCHA",
    }
    handler._save_result("fixture-run", entry)
    persisted = json.loads((tmp_path / "fixture-run.json").read_text())
    assert persisted["status"] == "done"
    assert persisted["failure_message"] == entry["failure_message"]
    assert handler.get_status("fixture-run")["failure_stage"] == "search"

    router = research_routes.setup_research_routes(handler)
    request = SimpleNamespace(state=SimpleNamespace(current_user="alice"))
    library = asyncio.run(_route(router, "/api/research/library", "GET")(
        request=request, search=None, sort="recent", limit=20, archived=False,
    ))
    result = asyncio.run(_route(router, "/api/research/result-peek/{session_id}", "POST")(
        session_id="fixture-run", request=request,
    ))
    assert library["research"][0]["failure_stage"] == "search"
    assert result["failure_message"] == entry["failure_message"]


def test_failed_research_is_not_logged_as_success(monkeypatch, caplog):
    researcher = _researcher(monkeypatch)
    monkeypatch.setattr(search_core, "_call_provider", lambda *args: [])
    monkeypatch.setattr("src.deep_research.DeepResearcher", lambda **kwargs: researcher)

    async def probe(*args, **kwargs):
        return None

    monkeypatch.setattr(ResearchHandler, "_probe_endpoint", staticmethod(probe))
    monkeypatch.setattr("src.settings.get_setting", lambda key, default=None: default)
    handler = ResearchHandler.__new__(ResearchHandler)
    handler._legacy_engine = None
    entry = {}

    with caplog.at_level(logging.INFO, logger="src.research_handler"):
        asyncio.run(handler.call_research_service(
            "fixture question", "https://model.test/v1", "fixture-model", _task_entry=entry,
        ))

    assert entry["failure_stage"] == "search"
    assert "finished without evidence" in caplog.text
    assert "completed successfully" not in caplog.text
