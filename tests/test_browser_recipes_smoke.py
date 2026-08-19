"""
Minimal smoke verification for the browser recipe adapter.

Validates adapter helper logic and orchestrator flow without launching
Chromium.
"""

import asyncio

import pytest

from src.browser_recipes.google_meet import (
    _looks_like_caption,
    _score_caption_region,
    MeetAdapter,
)
from src.browser_recipes.linkedin import LinkedInAdapter, _parse_relative_ms, _chat_id_from_href
from src.browser_recipes.orchestrator import BrowserRecipeOrchestrator
from src.browser_recipes.schemas import LifecycleEvent, RecipeState


def test_chrome_noise_filters():
    assert _looks_like_caption("arrow_downward") is False
    assert _looks_like_caption("mic_off") is False
    assert _looks_like_caption("Copy link") is False
    assert _looks_like_caption("Make a massive improvement on the rollout plan") is True


class _MockElement:
    def __init__(self, count_map):
        self._count_map = count_map

    def query_selector_all(self, selector):
        if selector == "img[alt]":
            return [self] * self._count_map.get("img_alt", 0)
        if selector == "[data-self-name]":
            return [self] * self._count_map.get("self_name", 0)
        if selector == "span":
            return [self] * self._count_map.get("span", 0)
        return []

    def get_attribute(self, name):
        if name == "alt":
            return self._count_map.get("alt")
        if name == "data-self-name":
            return self._count_map.get("self-name")
        return None


def test_score_caption_region():
    ranked = _score_caption_region(_MockElement({"span": 3, "img_alt": 1, "alt": "Alice"}))
    assert ranked >= 1


def test_parse_relative_time():
    ms = _parse_relative_ms("2h")
    assert ms is not None and ms > 0
    assert _parse_relative_ms("xyz") is None
    assert _parse_relative_ms("") is None


def test_chat_id_from_href():
    assert _chat_id_from_href("/messages/thread/123-abc-456/") == "123-abc-456"
    assert _chat_id_from_href(None) is None
    assert _chat_id_from_href("/about") is None


class _Sink:
    def __init__(self):
        self.events = []

    def __call__(self, event: LifecycleEvent):
        self.events.append(event)


@pytest.mark.asyncio
async def test_orchestrator_runs_and_emits_once():
    async def factory(sink, state: RecipeState):
        sink(LifecycleEvent(kind="tick", payload={"i": 1}, ts_ms=1, adapter="test"))
        await asyncio.sleep(0)

    sink = _Sink()
    orch = BrowserRecipeOrchestrator()
    orch.bind_sink(sink)
    orch.register("test", factory)
    await orch.start()
    await asyncio.sleep(0.05)
    await orch.stop()
    assert any(ev.kind == "tick" for ev in sink.events)


@pytest.mark.asyncio
async def test_orchestrator_clean_start_stop():
    calls = []

    async def factory(sink, state: RecipeState):
        calls.append(1)
        sink(LifecycleEvent(kind="tick", payload={"c": len(calls)}, ts_ms=len(calls), adapter="ok"))
        await asyncio.sleep(0)

    sink = _Sink()
    orch = BrowserRecipeOrchestrator()
    orch.bind_sink(sink)
    orch.register("ok", factory)
    await orch.start()
    await asyncio.sleep(0.05)
    await orch.stop()
    assert any(ev.kind == "tick" for ev in sink.events)


def test_linkedin_adapter_does_not_start_on_non_messaging_page():
    adapter = LinkedInAdapter(None)
