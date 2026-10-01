"""Tests for ResearchHandler.synthesize_query topic/fallback selection.

Deep research asks clarifying questions first. When the user answers with a
bare affirmation ("yes", "ok", "go ahead"), that follow-up must not become the
research topic — we fall back to the original substantive ask. A short but
meaningful answer ("UK", "C++", "Rust") is a real topic and must be preserved.
"""
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.models import ChatMessage, Session
from src.research_handler import ResearchHandler


def _session(history):
    return Session(
        id="s1", name="t", endpoint_url="http://local.test", model="m",
        history=[ChatMessage(role, content) for role, content in history],
    )


@pytest.fixture
def handler():
    return ResearchHandler()


def _raise(*args, **kwargs):
    raise RuntimeError("synthesis unavailable")


@pytest.mark.asyncio
async def test_bare_yes_falls_back_to_original_ask(handler, monkeypatch):
    # original ask + assistant clarification + user "yes" => original ask
    monkeypatch.setattr("src.research_handler.submit_model_job", _raise)
    sess = _session([
        ("user", "What is the best electric car for a cold climate?"),
        ("assistant", "Happy to research that — should I go ahead?"),
    ])
    result = await handler.synthesize_query(sess, "yes", "http://local.test", "m")
    assert result == "What is the best electric car for a cold climate?"


@pytest.mark.asyncio
async def test_continuation_phrase_falls_back_to_original_ask(handler, monkeypatch):
    monkeypatch.setattr("src.research_handler.submit_model_job", _raise)
    sess = _session([
        ("user", "Summarize recent advances in fusion energy."),
        ("assistant", "Want me to go ahead and research this?"),
    ])
    result = await handler.synthesize_query(sess, "Go ahead!", "http://local.test", "m")
    assert result == "Summarize recent advances in fusion energy."


@pytest.mark.asyncio
async def test_short_country_answer_is_kept(handler, monkeypatch):
    # original ask + assistant asks "which country?" + user "UK" => "UK"
    monkeypatch.setattr("src.research_handler.submit_model_job", _raise)
    sess = _session([
        ("user", "Compare national healthcare systems."),
        ("assistant", "Which country should I focus on?"),
    ])
    result = await handler.synthesize_query(sess, "UK", "http://local.test", "m")
    assert result == "UK"


@pytest.mark.asyncio
async def test_short_language_answer_is_kept(handler, monkeypatch):
    # original ask + assistant asks "which language?" + user "C++" => "C++"
    monkeypatch.setattr("src.research_handler.submit_model_job", _raise)
    sess = _session([
        ("user", "Find the fastest sorting library."),
        ("assistant", "Which language are you targeting?"),
    ])
    result = await handler.synthesize_query(sess, "C++", "http://local.test", "m")
    assert result == "C++"


@pytest.mark.asyncio
async def test_short_only_substantive_message_is_kept(handler):
    # A short answer that is the only substantive message must not be swallowed.
    sess = _session([("user", "Rust")])
    result = await handler.synthesize_query(sess, "Rust", "http://local.test", "m")
    assert result == "Rust"


@pytest.mark.asyncio
async def test_multiword_followup_uses_synthesis(handler, monkeypatch):
    # A normal multi-word follow-up still flows through query synthesis untouched.
    synthesized = "Best long-range EV for cold climates with fast charging"

    def _synth(*args, **kwargs):
        return SimpleNamespace(output={"text": synthesized})

    # Replacement is submit_model_job. Creating the name here so the
    # test fails on behavior (still llm_call_async) rather than AttributeError.
    monkeypatch.setattr("src.research_handler.submit_model_job", _synth, raising=False)
    sess = _session([
        ("user", "What is the best electric car for a cold climate?"),
        ("assistant", "Any constraints on range or charging?"),
    ])
    result = await handler.synthesize_query(
        sess, "focus on long range and fast charging", "http://local.test", "m",
    )
    assert result == synthesized


def test_research_plan_and_query_are_model_jobs_not_llm_call():
    source = Path("src/research_handler.py").read_text(encoding="utf-8")
    query = source.split("async def synthesize_query", 1)[1].split(
        "async def generate_plan", 1
    )[0]
    plan = source.split("async def generate_plan", 1)[1].split(
        "def rename_owner", 1
    )[0]
    assert "submit_model_job" in query
    assert "llm_call_async" not in query
    assert "submit_model_job" in plan
    assert "llm_call_async" not in plan


def test_agentic_research_uses_governed_openhands_not_deep_researcher_llm():
    source = Path("src/research_handler.py").read_text(encoding="utf-8")
    agentic = source.split("async def call_research_service", 1)[1].split(
        "async def _fallback_research", 1
    )[0]
    assert "stream_governed_agent(" in agentic
    assert "DeepResearcher" not in agentic
    assert "llm_call_async" not in agentic
    assert "_probe_endpoint" not in agentic
