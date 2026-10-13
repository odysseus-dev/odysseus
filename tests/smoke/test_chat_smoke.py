"""Chat: a turn against the stub provider comes back rendered and saved.

The buffered and the streamed path are both checked because the UI uses
the streamed one and the agent's own loop uses the buffered one, and a
decomposition can break either alone.
"""
from __future__ import annotations

import json

from tests.smoke.stub_provider import MODEL_PRIMARY, reply_for

CHAT_PATH = "/api/chat"
CHAT_STREAM_PATH = "/api/chat_stream"
HISTORY_PATH = "/api/history"

PROMPT = "Smoke check: reply with anything."


def test_buffered_turn_returns_the_provider_reply(client, chat_session, stub_provider):
    response = client.post(CHAT_PATH, json={"message": PROMPT, "session": chat_session})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body.get("response") == reply_for(MODEL_PRIMARY), body
    assert body.get("model") == MODEL_PRIMARY, body
    # The app prefaces the turn with its own date/time context block, so
    # the prompt is contained in what the provider saw rather than equal
    # to it.
    assert any(PROMPT in seen for seen in stub_provider.recorder.prompts()), (
        "the prompt never reached the provider, so the reply came from "
        "somewhere other than the model path"
    )


def test_streamed_turn_emits_the_reply_and_saves_the_message(client, chat_session):
    deltas, saved = [], []
    with client.stream("POST", CHAT_STREAM_PATH,
                       json={"message": PROMPT, "session": chat_session}) as response:
        assert response.status_code == 200
        for line in response.iter_lines():
            if not line.startswith("data: "):
                continue
            payload = line[len("data: "):].strip()
            if payload == "[DONE]":
                break
            try:
                event = json.loads(payload)
            except ValueError:
                continue
            if "delta" in event:
                deltas.append(str(event["delta"]))
            if event.get("type") == "message_saved":
                saved.append(event.get("id"))

    assert "".join(deltas) == reply_for(MODEL_PRIMARY), deltas
    assert saved and saved[0], "the stream never reported the assistant turn as saved"


def test_the_turn_is_in_the_session_history(client, chat_session):
    client.post(CHAT_PATH, json={"message": PROMPT, "session": chat_session})
    response = client.get(f"{HISTORY_PATH}/{chat_session}")
    assert response.status_code == 200, response.text
    messages = response.json().get("history") or []
    rendered = [str(m.get("content") or "") for m in messages]
    assert any(PROMPT in text for text in rendered), rendered
    assert any(reply_for(MODEL_PRIMARY) in text for text in rendered), rendered
