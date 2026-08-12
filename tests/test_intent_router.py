import asyncio
import json
import time
from pathlib import Path

import numpy as np
import pytest

from src.intent_router import (
    ActiveIntentSelection,
    INTENT_DEFINITIONS,
    IntentDefinition,
    IntentRoute,
    IntentRouter,
    IntentScore,
    active_constraint_disabled_tools,
    classify_intent_route,
    extract_intent_constraints,
    get_intent_router_mode,
    select_active_intents,
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
    assert get_intent_router_mode("active") == "active"
    assert get_intent_router_mode("unexpected") == "off"


def _active_route(*intents, constraints=(), source="semantic"):
    return IntentRoute(
        top_intents=tuple(intents),
        constraints=tuple(constraints),
        source=source,
        rollout_mode="active",
    )


def test_active_selection_preserves_multiple_high_confidence_tool_intents():
    route = _active_route(
        IntentScore("workspace.modify", 0.91, True, ("files",)),
        IntentScore("workspace.test", 0.82, True, ("files",)),
        IntentScore("chat.explain", 0.30, False, ()),
    )

    selected = select_active_intents(route, min_score=0.60, min_margin=0.08)

    assert isinstance(selected, ActiveIntentSelection)
    assert [intent.name for intent in selected.intents] == [
        "workspace.modify",
        "workspace.test",
    ]
    assert selected.domains == ("files",)
    assert selected.needs_tools is True
    assert selected.reason == "selected"


def test_active_selection_rejects_explanation_conflict_and_low_confidence():
    explanation = _active_route(
        IntentScore("chat.explain", 0.80, False, ()),
        IntentScore("system.execute", 0.77, True, ("files",)),
    )
    low_confidence = _active_route(
        IntentScore("email.read", 0.50, True, ("email",)),
    )

    assert select_active_intents(
        explanation, min_score=0.60, min_margin=0.08
    ).reason == "conflicting"
    assert select_active_intents(
        low_confidence, min_score=0.60, min_margin=0.08
    ).reason == "low_confidence"


def test_active_selection_obeys_negative_constraints():
    route = _active_route(
        IntentScore("web.search", 0.94, True, ("web",)),
        IntentScore("git.publish", 0.90, True, ("files",)),
        constraints=("web.no_browse", "git.no_push"),
    )

    selected = select_active_intents(route, min_score=0.60, min_margin=0.08)

    assert selected.needs_tools is False
    assert selected.reason == "constrained"
    assert selected.domains == ()
    assert active_constraint_disabled_tools(route) >= {
        "web_search",
        "web_fetch",
        "builtin_browser",
        "trigger_research",
        "manage_research",
    }


def test_active_read_only_constraint_uses_fail_closed_mutator_policy():
    route = _active_route(constraints=("workspace.read_only",))

    disabled = active_constraint_disabled_tools(route)

    assert disabled >= {
        "bash",
        "python",
        "write_file",
        "edit_file",
        "apply_patch",
        "create_document",
        "manage_notes",
        "manage_calendar",
        "manage_tasks",
        "send_email",
        "manage_settings",
        "ui_control",
    }


def test_shadow_constraints_do_not_change_tool_policy():
    route = IntentRoute(
        constraints=("web.no_browse",),
        source="semantic",
        rollout_mode="shadow",
    )

    assert active_constraint_disabled_tools(route) == set()
    assert select_active_intents(route).reason == "inactive"


@pytest.mark.parametrize("source", ["timeout", "unavailable", "empty"])
def test_active_selection_falls_back_when_semantic_route_is_unavailable(source):
    route = _active_route(
        IntentScore("email.read", 0.99, True, ("email",)),
        source=source,
    )

    selected = select_active_intents(route)

    assert selected.needs_tools is False
    assert selected.reason == source


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
