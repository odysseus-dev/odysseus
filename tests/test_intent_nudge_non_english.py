"""Issue #3668 — the intent-without-action supervisor (`_INTENT_RE` in
src/agent_loop.py) only recognizes English intent phrasings ("let me …",
"I'll …"). When the agent converses in another language, the model announces
its next action in that language (e.g. Swedish "Låt mig kolla loggarna"),
emits no tool call, the regex doesn't match, and the loop exits via the
"no tools — done" path: the agent stalls silently mid-task instead of getting
the "you said you would X — call the actual tool now" nudge.

These tests drive the real `stream_agent_loop` with a fake LLM stream (same
harness shape as tests/test_fenced_example_not_executed_for_native_models.py).
The observable contract: a short announce-only round must trigger the nudge,
which forces a second LLM round. Without the nudge the loop breaks after one
round. We assert on the number of LLM rounds and on the `agent_step` event the
nudge emits.

The non-English cases fail against current dev and pass with the fix; the
English and guard cases pin existing behavior so the fix cannot regress it.
"""
import asyncio
import json

import src.agent_loop as al


def _collect(gen):
    async def _run():
        return [c async for c in gen]
    return asyncio.run(_run())


def _events(chunks):
    out = []
    for c in chunks:
        if c.startswith("data: ") and not c.startswith("data: [DONE]"):
            try:
                out.append(json.loads(c[6:]))
            except Exception:
                pass
    return out


def _patch_common(monkeypatch):
    monkeypatch.setattr(al, "get_setting", lambda key, default=None: default, raising=False)
    monkeypatch.setattr(al, "get_mcp_manager", lambda: None, raising=False)
    monkeypatch.setattr(al, "estimate_tokens", lambda *a, **k: 10, raising=False)

    async def _fake_exec(block, *a, **k):
        return ("bash", {"output": "ok", "exit_code": 0})
    monkeypatch.setattr(al, "execute_tool_block", _fake_exec, raising=False)


def _run_announce_only_round(monkeypatch, announce_text, user_text):
    """Round 1 streams `announce_text` with no tool calls; any later round
    answers plainly. Returns (number of LLM rounds, parsed SSE events)."""
    call_count = {"n": 0}

    async def _fake_stream(_candidates, messages, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            yield f'data: {json.dumps({"delta": announce_text})}\n\n'
        else:
            yield f'data: {json.dumps({"delta": "All done, here is your answer."})}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    gen = al.stream_agent_loop(
        "https://api.openai.com/v1", "gpt-4o",
        [{"role": "user", "content": user_text}],
        max_rounds=4,
        relevant_tools={"bash"},
    )
    events = _events(_collect(gen))
    return call_count["n"], events


# ---------------------------------------------------------------------------
# Existing behavior pin: English announce-only round gets the nudge.
# ---------------------------------------------------------------------------
def test_english_announce_only_round_is_nudged(monkeypatch):
    _patch_common(monkeypatch)
    rounds, events = _run_announce_only_round(
        monkeypatch,
        "Let me check the logs to see the error.",
        "The build fails, please investigate.",
    )
    assert rounds == 2, "English intent phrase must trigger the nudge and a second round"
    assert any(e.get("type") == "agent_step" for e in events), events


# ---------------------------------------------------------------------------
# Issue #3668 repro: the same announce-only stall in other languages must be
# nudged too. Each of these fails on current dev (loop ends after 1 round).
# ---------------------------------------------------------------------------
def test_swedish_announce_only_round_is_nudged(monkeypatch):
    _patch_common(monkeypatch)
    rounds, events = _run_announce_only_round(
        monkeypatch,
        "Låt mig kolla loggarna för att se felet.",
        "Bygget misslyckas, kan du undersöka?",
    )
    assert rounds == 2, "Swedish intent phrase must trigger the nudge and a second round"
    assert any(e.get("type") == "agent_step" for e in events), events


def test_norwegian_announce_only_round_is_nudged(monkeypatch):
    _patch_common(monkeypatch)
    rounds, events = _run_announce_only_round(
        monkeypatch,
        "La meg sjekke loggene for å se feilen.",
        "Bygget feiler, kan du undersøke?",
    )
    assert rounds == 2, "Norwegian intent phrase must trigger the nudge and a second round"
    assert any(e.get("type") == "agent_step" for e in events), events


def test_german_announce_only_round_is_nudged(monkeypatch):
    _patch_common(monkeypatch)
    rounds, events = _run_announce_only_round(
        monkeypatch,
        "Lass mich die Logs prüfen, um den Fehler zu sehen.",
        "Der Build schlägt fehl, bitte untersuchen.",
    )
    assert rounds == 2, "German intent phrase must trigger the nudge and a second round"
    assert any(e.get("type") == "agent_step" for e in events), events


def test_spanish_announce_only_round_is_nudged(monkeypatch):
    _patch_common(monkeypatch)
    rounds, events = _run_announce_only_round(
        monkeypatch,
        "Déjame revisar los registros para ver el error.",
        "La compilación falla, por favor investiga.",
    )
    assert rounds == 2, "Spanish intent phrase must trigger the nudge and a second round"
    assert any(e.get("type") == "agent_step" for e in events), events


def test_french_announce_only_round_is_nudged(monkeypatch):
    _patch_common(monkeypatch)
    rounds, events = _run_announce_only_round(
        monkeypatch,
        "Laisse-moi vérifier les journaux pour voir l'erreur.",
        "La compilation échoue, peux-tu enquêter ?",
    )
    assert rounds == 2, "French intent phrase must trigger the nudge and a second round"
    assert any(e.get("type") == "agent_step" for e in events), events


# ---------------------------------------------------------------------------
# Guards: harmless phrasings must NOT be nudged — neither the English
# "let me know" escape nor its non-English equivalents, nor long answers.
# ---------------------------------------------------------------------------
def test_let_me_know_is_not_nudged(monkeypatch):
    _patch_common(monkeypatch)
    rounds, _ = _run_announce_only_round(
        monkeypatch,
        "That's everything.\nLet me know what you think.",
        "Thanks for the help.",
    )
    assert rounds == 1, "'let me know' must not trigger the nudge"


def test_swedish_let_me_know_equivalent_is_not_nudged(monkeypatch):
    _patch_common(monkeypatch)
    rounds, _ = _run_announce_only_round(
        monkeypatch,
        "Det var allt.\nLåt mig veta vad du tycker.",
        "Tack för hjälpen.",
    )
    assert rounds == 1, "Swedish 'låt mig veta' must not trigger the nudge"


def test_long_answer_containing_intent_phrase_is_not_nudged(monkeypatch):
    _patch_common(monkeypatch)
    long_answer = (
        "Låt mig kolla loggarna är vad jag normalt skulle säga, men här är "
        "istället en full genomgång av felet och hur du löser det själv. "
        + "Detaljer. " * 60
    )
    rounds, _ = _run_announce_only_round(
        monkeypatch,
        long_answer,
        "Bygget misslyckas, kan du undersöka?",
    )
    assert rounds == 1, "long answers (>=400 chars) must never be nudged"
