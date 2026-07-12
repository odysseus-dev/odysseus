import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes import misumi_routes
from services.memory.skills import SkillsManager
from src import endpoint_resolver, llm_core, seed_order_context
from src.misumi_memory import MisumiMemory


PERSONAS = """\
personas:
  aoteru:
    role: head-human-interfacer
    consults: [kurisu, misato, ichigo, giorno, erwin]
    routing: {intents: [coordinate]}
  lelouch:
    role: operator
    consults: [aoteru]
    routing: {intents: [implementation, deployment]}
  kurisu:
    role: archivist
    consults: [aoteru]
    routing: {intents: [evidence]}
  misato:
    role: caretaker
    consults: [aoteru]
    routing: {intents: [routine]}
  ichigo:
    role: guardian
    consults: [aoteru]
    routing: {intents: [hardware]}
  giorno:
    role: creative-spawner
    consults: [aoteru]
    routing: {intents: [experiment]}
  erwin:
    role: deputy-general
    consults: [aoteru]
    routing: {intents: [risk]}
"""


def _client(tmp_path: Path, monkeypatch, llm_call, *, consult="true"):
    household = tmp_path / "household"
    household.mkdir()
    root = tmp_path / "seed"
    persona_path = root / "config" / "personas.yaml"
    persona_path.parent.mkdir(parents=True)
    persona_path.write_text(PERSONAS, encoding="utf-8")
    memory_root = tmp_path / "memory"

    monkeypatch.setenv("MISUMI_HOUSEHOLD_ROOT", str(household))
    monkeypatch.setenv("MISUMI_EVENT_LOG", str(tmp_path / "events.jsonl"))
    monkeypatch.setenv("MISUMI_CONSULT", consult)
    monkeypatch.setattr("src.persona_capabilities._resolve_seed_root", lambda: root)
    monkeypatch.setattr(seed_order_context, "build_seed_order_context", lambda: "SEED ORDER")
    monkeypatch.setattr(
        endpoint_resolver,
        "resolve_endpoint",
        lambda *args, **kwargs: ("http://model.test/v1/chat/completions", "model", {}),
    )
    monkeypatch.setattr(llm_core, "llm_call_async", llm_call)
    app = FastAPI()
    app.include_router(misumi_routes.setup_misumi_routes(
        SkillsManager(str(tmp_path / "skills")), memory_root=memory_root
    ))
    return TestClient(app), MisumiMemory(memory_root)


def _is_consult(messages):
    return any("Review Aoteru's plan" in str(message.get("content")) for message in messages)


def _consult_persona(messages):
    system = " ".join(str(message.get("content")) for message in messages if message.get("role") == "system")
    return next(name for name in ("kurisu", "lelouch", "misato", "ichigo", "giorno", "erwin") if f"You are {name}," in system)


def test_plan_consults_named_then_intent_and_records_linked_handoffs(tmp_path, monkeypatch):
    calls = []

    async def llm_call(url, model, messages, **kwargs):
        calls.append((messages, kwargs))
        if not _is_consult(messages):
            return "Kurisu should preserve the evidence before rollout."
        persona = _consult_persona(messages)
        return {"kurisu": "Record the assumptions. Then review evidence.", "lelouch": "Stage the implementation. Then verify it."}[persona]

    client, memory = _client(tmp_path, monkeypatch, llm_call)
    body = client.post("/misumi/respond", json={"prompt": "Plan the deployment approach", "persona": "aoteru"}).json()

    assert body["consulted"] == [{"persona": "kurisu"}, {"persona": "lelouch"}]
    assert "\n\n— Kurisu: Record the assumptions." in body["text"]
    assert "\n\n— Lelouch: Stage the implementation." in body["text"]
    assert body["capsule_id"]
    assert len(body["handoff_ids"]) == 2
    assert [call[1]["max_tokens"] for call in calls] == [480, 240, 240]
    assert [call[1]["timeout"] for call in calls] == [25, 20, 20]
    capsules, _ = memory.capsules()
    handoffs, _ = memory.handoffs()
    assert capsules[0]["id"] == body["capsule_id"]
    assert capsules[0]["type"] == "decision"
    assert capsules[0]["source"] == "consultation"
    assert capsules[0]["persona_primary"] == "aoteru"
    assert {item["capsule_id"] for item in handoffs} == {body["capsule_id"]}
    assert {item["action"] for item in handoffs} == {"Record the assumptions.", "Stage the implementation."}


def test_five_named_candidates_are_capped_at_two(tmp_path, monkeypatch):
    consulted = []

    async def llm_call(url, model, messages, **kwargs):
        if not _is_consult(messages):
            return "Kurisu, Misato, Ichigo, Giorno, and Erwin should review this."
        persona = _consult_persona(messages)
        consulted.append(persona)
        return "Review the proposal."

    client, _ = _client(tmp_path, monkeypatch, llm_call)
    body = client.post("/misumi/respond", json={"prompt": "Consider the approach", "persona": "aoteru"}).json()

    assert consulted == ["kurisu", "misato"]
    assert body["consulted"] == [{"persona": "kurisu"}, {"persona": "misato"}]


def test_non_aoteru_never_consults(tmp_path, monkeypatch):
    calls = 0

    async def llm_call(url, model, messages, **kwargs):
        nonlocal calls
        calls += 1
        return "Primary reply."

    client, memory = _client(tmp_path, monkeypatch, llm_call)
    body = client.post("/misumi/respond", json={"prompt": "Plan the deployment approach", "persona": "lelouch"}).json()

    assert calls == 1
    assert body["consulted"] == []
    assert body["capsule_id"] is None
    assert memory.capsules() == ([], 0)


def test_kill_switch_preserves_legacy_response_and_writes_nothing(tmp_path, monkeypatch):
    async def llm_call(url, model, messages, **kwargs):
        return "Legacy reply."

    monkeypatch.setattr(misumi_routes.MisumiEventLog, "request_id", lambda self: "request-1")
    client, memory = _client(tmp_path, monkeypatch, llm_call, consult="false")
    body = client.post("/misumi/respond", json={"prompt": "Plan deployment", "persona": "aoteru"}).json()

    assert body == {
        "text": "Legacy reply.", "state": "speaking", "mood": "focused",
        "source": "odysseus", "persona": "aoteru", "who": "Aoteru",
        "audio_url": None, "voice": None, "tts_provider": None,
        "request_id": "request-1", "sources": [],
    }
    assert memory.capsules() == ([], 0)
    assert memory.handoffs() == ([], 0)


def test_failed_consult_is_logged_and_has_no_handoff(tmp_path, monkeypatch, caplog):
    async def llm_call(url, model, messages, **kwargs):
        if not _is_consult(messages):
            return "Kurisu should review the deployment."
        if _consult_persona(messages) == "kurisu":
            raise RuntimeError("consult unavailable")
        return "Stage the implementation."

    client, memory = _client(tmp_path, monkeypatch, llm_call)
    with caplog.at_level(logging.WARNING, logger=misumi_routes.__name__):
        body = client.post("/misumi/respond", json={"prompt": "Plan deployment", "persona": "aoteru"}).json()

    assert body["consulted"] == [{"persona": "lelouch"}]
    assert "— Kurisu:" not in body["text"]
    assert "consult unavailable" in caplog.text
    handoffs, _ = memory.handoffs()
    assert len(handoffs) == 1
    assert handoffs[0]["to_persona"] == "lelouch"


def test_degraded_primary_reply_never_consults(tmp_path, monkeypatch):
    async def unused_call(url, model, messages, **kwargs):
        raise AssertionError("consultation model must not be called")

    client, memory = _client(tmp_path, monkeypatch, unused_call)

    async def degraded(prompt, persona):
        return "Backend unavailable.", None, None

    monkeypatch.setattr(misumi_routes, "_model_reply", degraded)
    body = client.post("/misumi/respond", json={"prompt": "Plan deployment", "persona": "aoteru"}).json()

    assert body["text"] == "Backend unavailable."
    assert body["consulted"] == []
    assert memory.capsules() == ([], 0)


def test_forbidden_contribution_uses_neutral_handoff_action(tmp_path, monkeypatch):
    async def llm_call(url, model, messages, **kwargs):
        if not _is_consult(messages):
            return "Kurisu should review this."
        return "Email the landlord with the plan. Then wait."

    client, memory = _client(tmp_path, monkeypatch, llm_call)
    body = client.post("/misumi/respond", json={"prompt": "Plan the approach", "persona": "aoteru"}).json()

    assert len(body["handoff_ids"]) == 2
    handoffs, _ = memory.handoffs()
    assert {item["action"] for item in handoffs} == {"review the plan and contribute next steps"}


def test_consultation_keeps_all_legacy_response_fields(tmp_path, monkeypatch):
    async def llm_call(url, model, messages, **kwargs):
        return "Primary reply." if not _is_consult(messages) else "Review the plan."

    client, _ = _client(tmp_path, monkeypatch, llm_call)
    body = client.post("/misumi/respond", json={"prompt": "Plan deployment", "persona": "aoteru", "mood": "steady"}).json()

    assert {
        "text", "state", "mood", "source", "persona", "who", "audio_url",
        "voice", "tts_provider", "request_id", "sources",
    }.issubset(body)
    assert body["state"] == "speaking"
    assert body["mood"] == "steady"
    assert body["source"] == "odysseus"
