"""Compare: a blind comparison streams both sides and reveals them on the vote.

Two model ids on the one stub provider is what makes this checkable
without a second endpoint: each returns a reply naming itself, so the
reveal can be matched against which text arrived on which side.
"""
from __future__ import annotations

import json

from tests.smoke.stub_provider import MODEL_PRIMARY, MODEL_SECONDARY, reply_for

COMPARE_PATH = "/api/compare"
CHAT_STREAM_PATH = "/api/chat_stream"

PROMPT = "Smoke check: compare two replies."


def _stream_text(client, session_id: str) -> str:
    deltas = []
    with client.stream("POST", CHAT_STREAM_PATH,
                       json={"message": PROMPT, "session": session_id}) as response:
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
    return "".join(deltas)


def test_a_blind_comparison_streams_and_reveals(client, stub_endpoint):
    started = client.post(f"{COMPARE_PATH}/start", data={
        "prompt": PROMPT,
        "model_a": MODEL_PRIMARY,
        "model_b": MODEL_SECONDARY,
        "endpoint_a_id": stub_endpoint,
        "endpoint_b_id": stub_endpoint,
        "is_blind": "true",
    })
    assert started.status_code == 200, started.text
    comparison = started.json()
    comparison_id = comparison["id"]

    # Blind: the start response must not say which model is on which side.
    assert not comparison.get("model_left"), comparison
    assert not comparison.get("model_right"), comparison

    left = _stream_text(client, comparison["session_left"])
    right = _stream_text(client, comparison["session_right"])
    assert {left, right} == {reply_for(MODEL_PRIMARY), reply_for(MODEL_SECONDARY)}, (left, right)

    voted = client.post(f"{COMPARE_PATH}/{comparison_id}/vote", data={"winner": "left"})
    assert voted.status_code == 200, voted.text
    revealed = voted.json().get("revealed") or {}
    assert revealed.get("left") in (MODEL_PRIMARY, MODEL_SECONDARY), voted.text
    assert reply_for(revealed["left"]) == left, (revealed, left)
    assert reply_for(revealed["right"]) == right, (revealed, right)

    history = client.get(f"{COMPARE_PATH}/history")
    assert history.status_code == 200, history.text
    entries = [row for row in history.json() if row.get("id") == comparison_id]
    assert entries, history.text
    assert entries[0].get("winner"), entries[0]
