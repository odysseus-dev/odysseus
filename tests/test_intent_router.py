import asyncio
import json
import time
from pathlib import Path

import numpy as np
import pytest

from src.intent_router import (
    INTENT_DEFINITIONS,
    IntentDefinition,
    IntentRoute,
    IntentRouter,
    classify_intent_route,
    extract_intent_constraints,
    get_intent_router_mode,
)


class MappingEncoder:
    def __init__(self, vectors):
        self.vectors = vectors
        self.calls = []

    def encode(self, texts, normalize_embeddings=True):
        self.calls.append(list(texts))
        return np.asarray([self.vectors[text] for text in texts], dtype="float32")


TEST_DEFINITIONS = (
    IntentDefinition("chat.explain", False, (), ("explain prototype",)),
    IntentDefinition("workspace.modify", True, ("files",), ("modify prototype",)),
    IntentDefinition("workspace.test", True, ("files",), ("test prototype",)),
)


def test_router_returns_ranked_multi_label_route_and_reuses_prototypes():
    vectors = {
        "explain prototype": [1.0, 0.0, 0.0],
        "modify prototype": [0.0, 1.0, 0.0],
        "test prototype": [0.0, 0.0, 1.0],
        "fix and test": [0.0, 0.8, 0.6],
        "explain only": [1.0, 0.0, 0.0],
    }
    encoder = MappingEncoder(vectors)
    factory_calls = 0

    def factory():
        nonlocal factory_calls
        factory_calls += 1
        return encoder

    router = IntentRouter(
        encoder_factory=factory,
        definitions=TEST_DEFINITIONS,
        min_score=0.5,
    )

    route = router.classify("fix and test", top_k=2)
    assert [item.name for item in route.top_intents] == [
        "workspace.modify",
        "workspace.test",
    ]
    assert route.domains == ("files",)
    assert route.needs_tools is True
    assert route.source == "semantic"

    explain_route = router.classify("explain only")
    assert explain_route.top_intents[0].name == "chat.explain"
    assert explain_route.needs_tools is False
    assert factory_calls == 1
    assert encoder.calls[0] == [
        "explain prototype",
        "modify prototype",
        "test prototype",
    ]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Fix it, but don't push anything", ("git.no_push",)),
        ("Prepare the branch, but no publishing", ("git.no_push",)),
        ("Review this without committing", ("git.no_commit",)),
        ("Summarize it but do not browse", ("web.no_browse",)),
        ("Inspect this read-only", ("workspace.read_only",)),
        ("Explain it without running anything", ("system.no_execute",)),
        ("Push, commit, browse, and run it", ()),
    ],
)
def test_negative_constraints_are_separate_from_intent(text, expected):
    assert extract_intent_constraints(text) == expected


def test_router_failure_is_structured_and_does_not_raise():
    class BrokenEncoder:
        def encode(self, texts, normalize_embeddings=True):
            raise RuntimeError("model unavailable")

    route = IntentRouter(
        encoder_factory=BrokenEncoder,
        definitions=TEST_DEFINITIONS,
    ).classify("fix the code")

    assert route.source == "unavailable"
    assert route.error == "RuntimeError"
    assert route.top_intents == ()


def test_router_rejects_invalid_encoder_shape_as_unavailable():
    class InvalidEncoder:
        def encode(self, texts, normalize_embeddings=True):
            return np.asarray([1.0, 2.0], dtype="float32")

    route = IntentRouter(
        encoder_factory=InvalidEncoder,
        definitions=TEST_DEFINITIONS,
    ).classify("fix the code")

    assert route.source == "unavailable"
    assert route.error == "ValueError"


def test_log_fields_never_include_prompt_text():
    secret_prompt = "private prompt marker 817263"
    route = IntentRoute(source="semantic", constraints=("git.no_push",))

    assert secret_prompt not in json.dumps(route.log_fields())
    assert "text" not in route.log_fields()


def test_log_fields_can_compare_semantic_and_deterministic_routes():
    route = IntentRoute(
        domains=("documents", "web"),
        needs_tools=True,
        source="semantic",
    )

    fields = route.log_fields(
        deterministic_needs_tools=False,
        deterministic_domains=("files",),
    )

    assert fields["comparison"] == {
        "deterministic_needs_tools": False,
        "needs_tools_disagrees": True,
        "deterministic_domains": ["files"],
        "semantic_only_domains": ["documents", "web"],
        "deterministic_only_domains": ["files"],
    }


def test_mode_defaults_invalid_values_to_off(monkeypatch):
    monkeypatch.delenv("ODYSSEUS_INTENT_ROUTER_MODE", raising=False)
    assert get_intent_router_mode() == "off"
    assert get_intent_router_mode("SHADOW") == "shadow"
    assert get_intent_router_mode("active") == "off"


@pytest.mark.asyncio
async def test_async_classifier_does_not_initialize_router_when_disabled():
    class UnexpectedRouter:
        def classify(self, text, top_k=3):
            raise AssertionError("disabled mode must not classify")

    route = await classify_intent_route(
        "fix the code", router=UnexpectedRouter(), mode="off"
    )
    assert route.source == "disabled"
    assert route.rollout_mode == "off"


@pytest.mark.asyncio
async def test_async_classifier_bounds_request_wait_and_keeps_constraints():
    class SlowRouter:
        def classify(self, text, top_k=3):
            time.sleep(0.05)
            return IntentRoute(source="semantic")

    route = await classify_intent_route(
        "fix it but don't push", router=SlowRouter(), mode="shadow", timeout_ms=1
    )

    assert route.source == "timeout"
    assert route.rollout_mode == "shadow"
    assert route.error == "TimeoutError"
    assert route.constraints == ("git.no_push",)

    busy_route = await classify_intent_route(
        "inspect another request", router=SlowRouter(), mode="shadow", timeout_ms=10
    )
    assert busy_route.source == "busy"
    assert busy_route.rollout_mode == "shadow"
    assert busy_route.error == "IntentRouterBusy"
    await asyncio.sleep(0.06)


def test_evaluation_corpus_covers_every_label_and_routing_edge():
    corpus_path = Path(__file__).parent / "fixtures" / "intent_router_cases.json"
    cases = json.loads(corpus_path.read_text(encoding="utf-8"))
    expected_labels = {
        label for case in cases for label in case.get("expected_intents", [])
    }
    defined_labels = {definition.name for definition in INTENT_DEFINITIONS}
    all_constraints = {
        constraint for case in cases for constraint in case.get("constraints", [])
    }

    assert expected_labels == defined_labels
    assert all_constraints == {
        "git.no_push",
        "git.no_commit",
        "web.no_browse",
        "workspace.read_only",
        "system.no_execute",
    }
    assert any(len(case["expected_intents"]) > 1 for case in cases)
    assert any(not case["expected_intents"] for case in cases)
    assert any(any(ord(char) > 127 for char in case["text"]) for case in cases)
