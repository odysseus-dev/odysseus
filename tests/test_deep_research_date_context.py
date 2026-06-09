"""Regression tests for issue #1341 — deep research used the model's
training-cutoff year (e.g. "best Python tutorials 2025") because the
query-generation and planning prompts never told the LLM the current date.

The chat/agent path already injects "Today is ..." (src/agent_loop.py); deep
research had no equivalent. These tests pin that the current year now reaches
the LLM at both the planning and query-generation steps, without needing a live
LLM or DB.
"""
import asyncio
from datetime import datetime

from src.deep_research import (
    DeepResearcher,
    current_date_context,
    RESEARCH_PLAN_PROMPT,
)


def _this_year() -> str:
    return datetime.now().astimezone().strftime("%Y")


def test_current_date_context_names_the_real_year():
    ctx = current_date_context()
    assert _this_year() in ctx
    # It must actively steer the model away from training-data years.
    assert "training data" in ctx.lower()


def test_generate_queries_prompt_carries_the_current_year():
    # Build without the heavy __init__; _generate_queries only needs these.
    r = DeepResearcher.__new__(DeepResearcher)
    r.research_plan = ""
    r.queries_used = set()

    seen = {}

    async def _fake_llm(messages, **kwargs):
        seen["prompt"] = messages[0]["content"]
        return '["python tutorials", "python guides"]'

    r._llm = _fake_llm

    queries = asyncio.run(r._generate_queries("best python tutorials", "", 1))

    assert queries  # sanity: the JSON array parsed
    # The fix: the real current year is in the prompt the LLM actually sees.
    assert _this_year() in seen["prompt"]


def test_plan_prompt_carries_the_current_year():
    r = DeepResearcher.__new__(DeepResearcher)

    seen = {}

    async def _fake_llm(messages, **kwargs):
        seen["prompt"] = messages[0]["content"]
        return "{}"

    r._llm = _fake_llm

    asyncio.run(r._create_plan("what changed this year"))

    assert _this_year() in seen["prompt"]
    # The base template itself stays year-agnostic; the year comes from the
    # prepended context, proving the wiring (not a hard-coded prompt edit).
    assert _this_year() not in RESEARCH_PLAN_PROMPT
