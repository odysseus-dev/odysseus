"""Multi-agent expert router: classification parsing, deterministic offline
fallback, preset registration, and the async routing entry point.

These tests are network-free: the only LLM call in ``classify_expert`` is
monkeypatched, and every other path is pure.
"""
import asyncio

import pytest

from src import expert_router as er
from src.expert_router import (
    DEFAULT_EXPERT,
    EXPERTS,
    ROUTER_PRESET_ID,
    classify_expert,
    expert_presets_for_picker,
    keyword_fallback_expert,
    parse_expert_decision,
)


# ── parse_expert_decision ─────────────────────────────────────────────────

def test_parse_clean_json():
    eid, reason = parse_expert_decision('{"expert": "programmer", "reason": "code"}')
    assert eid == "programmer"
    assert reason == "code"


def test_parse_fenced_json():
    raw = '```json\n{"expert": "ai_ml", "reason": "training"}\n```'
    eid, _ = parse_expert_decision(raw)
    assert eid == "ai_ml"


def test_parse_json_embedded_in_prose():
    raw = 'Sure! {"expert": "general"} is best.'
    eid, _ = parse_expert_decision(raw)
    assert eid == "general"


@pytest.mark.parametrize("raw", ["", "not json", "{}", '{"expert": "nope"}', "{broken"])
def test_parse_invalid_returns_none(raw):
    eid, _ = parse_expert_decision(raw)
    assert eid is None


# ── keyword_fallback_expert ───────────────────────────────────────────────

@pytest.mark.parametrize(
    "message,expected",
    [
        ("Why does my python function throw a syntax error?", "programmer"),
        ("How do I fine-tune an LLM with a small dataset?", "ai_ml"),
        ("How do I harden authentication against an injection vulnerability?", "security_defensive"),
        ("What's a good recipe for dinner?", "general"),
        ("", DEFAULT_EXPERT),
    ],
)
def test_keyword_fallback(message, expected):
    assert keyword_fallback_expert(message) == expected


# ── expert_presets_for_picker ─────────────────────────────────────────────

def test_picker_presets_well_formed():
    presets = expert_presets_for_picker()
    assert ROUTER_PRESET_ID in presets
    for eid in EXPERTS:
        key = f"expert_{eid}"
        assert key in presets
        entry = presets[key]
        for field in ("name", "temperature", "max_tokens", "system_prompt"):
            assert field in entry, f"{key} missing {field}"
        assert entry["system_prompt"].strip()


def test_presets_registered_as_builtin_defaults():
    from src.preset_manager import PresetManager

    assert ROUTER_PRESET_ID in PresetManager.DEFAULT_PRESETS
    for eid in EXPERTS:
        assert f"expert_{eid}" in PresetManager.DEFAULT_PRESETS


def test_no_offensive_security_expert():
    # The router is defensive-only by design; guard against regressions that
    # reintroduce an offensive/"uncensored" persona.
    blob = " ".join(
        (meta["name"] + " " + meta["system_prompt"]).lower()
        for meta in EXPERTS.values()
    )
    assert "uncensored" not in blob
    assert "offensive" not in blob


# ── classify_expert (async) ───────────────────────────────────────────────

def test_classify_uses_llm_decision(monkeypatch):
    async def fake_llm(*args, **kwargs):
        return '{"expert": "programmer", "reason": "code question"}'

    monkeypatch.setattr("src.llm_core.llm_call_async", fake_llm)
    eid, reason = asyncio.run(classify_expert("fix my bug", "http://x", "m"))
    assert eid == "programmer"
    assert reason == "code question"


def test_classify_falls_back_on_llm_error(monkeypatch):
    async def boom(*args, **kwargs):
        raise RuntimeError("no model")

    monkeypatch.setattr("src.llm_core.llm_call_async", boom)
    eid, reason = asyncio.run(
        classify_expert("fine-tune a transformer model", "http://x", "m")
    )
    assert eid == "ai_ml"
    assert reason == "keyword fallback"


def test_classify_empty_message_is_general():
    eid, _ = asyncio.run(classify_expert("   ", "http://x", "m"))
    assert eid == DEFAULT_EXPERT
