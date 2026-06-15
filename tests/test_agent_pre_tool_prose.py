"""Issue #3992 — the agent must not persist pre-tool prose as a final answer.

When a single assistant round emits prose AND an executable tool call, the
prose precedes the real tool result, so it is *ungrounded*: the grounded answer
only arrives in a later round after the tool output is fed back. `stream_agent_loop`
used to append that pre-tool prose to `round_texts` unconditionally
(`src/agent_loop.py`), and `round_texts` is exactly what `chatRenderer.js`'s
agentic multi-bubble reconstruction replays on reload — so a guessed/fabricated
answer rendered as its own bubble right next to the real, tool-grounded answer.

The fix quarantines the visible pre-tool prose for any round that resolved to an
executable tool call, while preserving `<think>` blocks (so the thinking section
still renders on reload). Rounds with no tool call keep their prose unchanged.

These tests drive the real `stream_agent_loop` end-to-end with a mocked LLM
stream (mirroring tests/test_fenced_example_not_executed_for_native_models.py)
and assert on the `round_texts` carried in the final metrics event — the same
artifact the reporter's fake-stream probe inspected.
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


def _round_texts(events):
    for e in events:
        if e.get("type") == "metrics":
            return e.get("data", {}).get("round_texts")
    return None


def _patch_common(monkeypatch, exec_calls):
    monkeypatch.setattr(al, "get_setting", lambda key, default=None: default, raising=False)
    monkeypatch.setattr(al, "get_mcp_manager", lambda: None, raising=False)
    monkeypatch.setattr(al, "estimate_tokens", lambda *a, **k: 10, raising=False)

    async def _fake_exec(block, *a, **k):
        exec_calls.append(block)
        return ("bash", {"output": "Real A and Real B", "exit_code": 0})
    monkeypatch.setattr(al, "execute_tool_block", _fake_exec, raising=False)


def _run_loop(monkeypatch, model, round1_deltas, round2_text="The real inbox has 2 emails: Real A and Real B.",
              max_rounds=3, endpoint_url="http://192.168.1.50:8000/v1"):
    """Drive stream_agent_loop: round 1 streams `round1_deltas`, round 2 (and
    later) streams a plain grounded answer with no tool call so the loop ends.
    Defaults to a non-native model/host so the fenced tool channel is active."""
    call_count = {"n": 0}

    async def _fake_stream(_candidates, messages, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            for d in round1_deltas:
                yield f'data: {json.dumps({"delta": d})}\n\n'
            yield "data: [DONE]\n\n"
        else:
            yield f'data: {json.dumps({"delta": round2_text})}\n\n'
            yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    gen = al.stream_agent_loop(
        endpoint_url, model,
        [{"role": "user", "content": "List my latest emails."}],
        max_rounds=max_rounds,
        relevant_tools={"bash"},
    )
    return _events(_collect(gen))


# ---------------------------------------------------------------------------
# 1. Pre-tool prose (a fabricated answer) emitted in the same round as an
#    executable tool call must NOT survive in round_texts; the later
#    tool-grounded answer must.
# ---------------------------------------------------------------------------
def test_pre_tool_prose_not_persisted_when_round_has_tool_call(monkeypatch):
    exec_calls = []
    _patch_common(monkeypatch, exec_calls)
    round1 = (
        "Here are your latest emails:\n\n"
        "| From | Subject |\n|---|---|\n| Alice | FAKE-GUESSED-ROW |\n\n"
        "```bash\necho fetch-inbox\n```"
    )
    events = _run_loop(monkeypatch, "llama-2-7b-chat", [round1])

    assert len(exec_calls) == 1, f"the fenced tool call should have executed: {exec_calls}"
    rt = _round_texts(events)
    assert rt is not None, "metrics should carry round_texts when tools ran"
    # The fabricated pre-tool answer must not be persisted in any round slot.
    assert all("FAKE-GUESSED-ROW" not in t for t in rt), rt
    # The real, tool-grounded answer survives.
    assert any("Real A and Real B" in t for t in rt), rt


# ---------------------------------------------------------------------------
# 2. <think> reasoning in a quarantined tool round is preserved (so the
#    thinking section still renders on reload), but the ungrounded prose is not.
# ---------------------------------------------------------------------------
def test_think_blocks_preserved_in_quarantined_tool_round(monkeypatch):
    exec_calls = []
    _patch_common(monkeypatch, exec_calls)
    round1 = (
        "<think>I should fetch the inbox before answering.</think>"
        "Your inbox has FAKE-GUESSED-ANSWER.\n\n"
        "```bash\necho fetch-inbox\n```"
    )
    events = _run_loop(monkeypatch, "llama-2-7b-chat", [round1])

    assert len(exec_calls) == 1, exec_calls
    rt = _round_texts(events)
    assert rt is not None and rt, rt
    first = rt[0]
    assert "I should fetch the inbox" in first, f"think block should be kept: {first!r}"
    assert "FAKE-GUESSED-ANSWER" not in first, f"ungrounded prose should be dropped: {first!r}"


# ---------------------------------------------------------------------------
# 2b. Multiple/interleaved <think> blocks in a quarantined tool round: every
#     think block is kept, and every stretch of ungrounded prose between/around
#     them is dropped.
# ---------------------------------------------------------------------------
def test_interleaved_think_blocks_all_kept_prose_all_dropped(monkeypatch):
    exec_calls = []
    _patch_common(monkeypatch, exec_calls)
    round1 = (
        "<think>First, decide.</think>"
        "FAKE-PROSE-A in the middle.\n"
        "<think>Now fetch.</think>"
        "FAKE-PROSE-B at the end.\n\n"
        "```bash\necho fetch-inbox\n```"
    )
    events = _run_loop(monkeypatch, "llama-2-7b-chat", [round1])

    assert len(exec_calls) == 1, exec_calls
    rt = _round_texts(events)
    assert rt is not None and rt, rt
    first = rt[0]
    assert "First, decide." in first and "Now fetch." in first, f"all think blocks kept: {first!r}"
    assert "FAKE-PROSE-A" not in first and "FAKE-PROSE-B" not in first, f"all prose dropped: {first!r}"


# ---------------------------------------------------------------------------
# 3. A round with no tool call keeps its prose verbatim (regression): the
#    grounded answer that arrives after the tool result is preserved in full.
# ---------------------------------------------------------------------------
def test_prose_kept_in_round_without_tool_call(monkeypatch):
    exec_calls = []
    _patch_common(monkeypatch, exec_calls)
    round1 = "Let me look that up.\n\n```bash\necho fetch-inbox\n```"
    events = _run_loop(
        monkeypatch, "llama-2-7b-chat", [round1],
        round2_text="Grounded answer: KEEP-THIS-FULL-TEXT from the real result.",
    )

    assert len(exec_calls) == 1, exec_calls
    rt = _round_texts(events)
    assert rt is not None, rt
    assert any("KEEP-THIS-FULL-TEXT" in t for t in rt), rt


# ---------------------------------------------------------------------------
# 4. Terminal ask_user round (RaresKeY review on PR #4015): ask_user ENDS the
#    turn, so its round is terminal — there is no later grounded round to
#    supersede the prose. The blanket tool-round quarantine wrongly blanked it,
#    and reload reconstruction reads round_texts, so the question disappeared on
#    reload. The terminal round's question (and visible prose) must survive in
#    round_texts.
# ---------------------------------------------------------------------------
def test_terminal_ask_user_question_survives_in_round_texts(monkeypatch):
    exec_calls = []

    monkeypatch.setattr(al, "get_setting", lambda key, default=None: default, raising=False)
    monkeypatch.setattr(al, "get_mcp_manager", lambda: None, raising=False)
    monkeypatch.setattr(al, "estimate_tokens", lambda *a, **k: 10, raising=False)

    async def _fake_exec(block, *a, **k):
        exec_calls.append(block)
        if getattr(block, "tool_type", None) == "ask_user":
            return "ask_user: which inbox", {
                "ask_user": {
                    "question": "ASK-QUESTION-KEEPME which inbox should I check?",
                    "options": [{"label": "Work"}, {"label": "Personal"}],
                    "multi": False,
                },
                "output": "Asked the user. Awaiting their selection.",
                "exit_code": 0,
            }
        return ("bash", {"output": "ignored", "exit_code": 0})

    monkeypatch.setattr(al, "execute_tool_block", _fake_exec, raising=False)

    round1 = (
        "I can do that, but I need one detail first.\n\n"
        '```ask_user\n{"question": "ASK-QUESTION-KEEPME which inbox should I check?", '
        '"options": [{"label": "Work"}, {"label": "Personal"}]}\n```'
    )
    events = _run_loop(monkeypatch, "llama-2-7b-chat", [round1])

    assert len(exec_calls) == 1, f"ask_user should have executed once: {exec_calls}"
    rt = _round_texts(events)
    assert rt is not None and rt, f"metrics should carry round_texts: {rt}"
    # The terminal ask_user question must persist so it renders on reload.
    assert any("ASK-QUESTION-KEEPME" in t for t in rt), f"ask_user question lost on reload: {rt}"


def _clean_response(events):
    for e in events:
        if e.get("type") == "metrics":
            return e.get("data", {}).get("clean_response")
    return None


def _accumulated_deltas(events):
    """Mirror the chat route: concatenate every non-thinking delta the way
    routes/chat_routes.py builds the saved `full_response`."""
    out = ""
    for e in events:
        if "delta" in e and not e.get("thinking"):
            out += e["delta"]
    return out


# ---------------------------------------------------------------------------
# 5. Gap A (RaresKeY review on PR #4015): quarantining only round_texts is not
#    enough — the chat route saves the delta-accumulated full_response as the
#    canonical assistant message, which is replayed to the model and fed to
#    export/webhook/extraction. That saved body must ALSO drop the fabricated
#    pre-tool prose while keeping the grounded answer. agent_loop now emits a
#    sanitized `clean_response` in the metrics event for the route to save.
# ---------------------------------------------------------------------------
def test_clean_response_excludes_pre_tool_prose_for_saved_body(monkeypatch):
    exec_calls = []
    _patch_common(monkeypatch, exec_calls)
    round1 = (
        "Here are your latest emails:\n\n"
        "| From | Subject |\n|---|---|\n| Alice | FAKE-GUESSED-ROW |\n\n"
        "```bash\necho fetch-inbox\n```"
    )
    events = _run_loop(monkeypatch, "llama-2-7b-chat", [round1])

    assert len(exec_calls) == 1, exec_calls
    # The raw delta accumulation (what the route used to save) DOES carry the
    # fabricated answer — this is the bug surface.
    assert "FAKE-GUESSED-ROW" in _accumulated_deltas(events)
    # The sanitized body the route will actually save must NOT.
    clean = _clean_response(events)
    assert clean is not None, "metrics should carry clean_response on an agentic turn"
    assert "FAKE-GUESSED-ROW" not in clean, f"saved body still has fabricated prose: {clean!r}"
    assert "Real A and Real B" in clean, f"grounded answer missing from saved body: {clean!r}"


# ---------------------------------------------------------------------------
# 5b. Gap A — grace synthesis: when the model burns its budget and writes no
#     final prose, agent_loop synthesizes the answer (force-answer path) and
#     adds it only to full_response, NOT round_texts. clean_response must still
#     include that synthesized answer (else join(round_texts) would lose it).
# ---------------------------------------------------------------------------
def test_clean_response_keeps_grace_synthesis_answer(monkeypatch):
    exec_calls = []

    monkeypatch.setattr(al, "get_setting", lambda key, default=None: default, raising=False)
    monkeypatch.setattr(al, "get_mcp_manager", lambda: None, raising=False)
    monkeypatch.setattr(al, "estimate_tokens", lambda *a, **k: 10, raising=False)

    async def _fake_exec(block, *a, **k):
        exec_calls.append(block)
        return ("bash", {"output": "raw data rows", "exit_code": 0})
    monkeypatch.setattr(al, "execute_tool_block", _fake_exec, raising=False)

    async def _fake_synth(*a, **k):
        return "SYNTH-FINAL-ANSWER assembled from the gathered data."
    monkeypatch.setattr("src.llm_core.llm_call_async", _fake_synth, raising=False)

    # Every round emits the SAME tool call and no final prose. After enough
    # identical no-progress rounds the loop-breaker trips and forces one
    # tool-free round, where the grace synthesis salvages a final answer.
    _step_delta = "```bash\necho step\n```"

    async def _fake_stream(_candidates, messages, **kwargs):
        yield f'data: {json.dumps({"delta": _step_delta})}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)
    gen = al.stream_agent_loop(
        "http://192.168.1.50:8000/v1", "llama-2-7b-chat",
        [{"role": "user", "content": "Summarize my inbox."}],
        max_rounds=8, relevant_tools={"bash"},
    )
    events = _events(_collect(gen))
    clean = _clean_response(events)
    assert clean is not None, "metrics should carry clean_response on an agentic turn"
    assert "SYNTH-FINAL-ANSWER" in clean, f"grace-synthesized answer missing: {clean!r}"


def _route_saved_body(events):
    """Mirror routes/chat_routes.py agent-mode save accumulation, delegating the
    final body choice to the real production helper: accumulate full_response
    from visible deltas, capture clean_response from the metrics event, then
    accumulate the visible deltas that arrive AFTER it (the inline teacher
    takeover) — these are absent from clean_response."""
    from routes.chat_helpers import select_saved_body
    full, clean, post, teacher_clean, pre_raw = "", None, "", None, None
    outer_seen = False
    for e in events:
        if e.get("type") == "metrics":
            md_clean = e.get("data", {}).get("clean_response")
            if e.get("teacher"):
                # Nested teacher metrics — don't clobber the outer clean body.
                if md_clean:
                    teacher_clean = md_clean
            else:
                # Mirror the route: an empty sanitized body ("") is a real value
                # (finding #5); only an absent key (None) keeps the prior clean.
                if md_clean is not None:
                    clean = md_clean
                outer_seen = True
                pre_raw = full
        elif "delta" in e and not e.get("thinking"):
            full += e["delta"]
            # Mirror the route guard: only deltas after the outer metrics event
            # are post-answer (teacher takeover).
            if outer_seen:
                post += e["delta"]
    return select_saved_body(
        clean, post, full, teacher_clean=teacher_clean, pre_metrics_raw=pre_raw
    )


# ---------------------------------------------------------------------------
# 6. Gap C (RaresKeY review on PR #4015): an inline teacher takeover streams a
#    visible correction AFTER the metrics event, so it is not in clean_response.
#    The saved body must still carry the teacher's answer (it is the real final
#    assistant output) while keeping the pre-tool prose excluded.
# ---------------------------------------------------------------------------
def test_teacher_takeover_survives_in_saved_body(monkeypatch):
    exec_calls = []
    _patch_common(monkeypatch, exec_calls)

    async def _fake_teacher(*a, **k):
        yield 'data: ' + json.dumps(
            {"delta": "TEACHER-FIX-USE-THIS — the real total is 43."}
        ) + '\n\n'

    monkeypatch.setattr(
        "src.teacher_escalation.run_teacher_inline", _fake_teacher, raising=False
    )

    round1 = (
        "Quick guess: FAKE-PRE-TOOL total is 999.\n\n"
        "```bash\necho compute\n```"
    )
    events = _run_loop(monkeypatch, "llama-2-7b-chat", [round1])

    # The teacher delta is emitted after the metrics event (post-answer takeover).
    assert any("TEACHER-FIX-USE-THIS" in e.get("delta", "") for e in events), \
        "teacher takeover should stream as a visible delta"

    body = _route_saved_body(events)
    assert "TEACHER-FIX-USE-THIS" in body, f"teacher takeover lost from saved body: {body!r}"
    assert "FAKE-PRE-TOOL" not in body, f"pre-tool prose leaked into saved body: {body!r}"


# ---------------------------------------------------------------------------
# 5c. Empty sanitized turn (RaresKeY review on PR #4015, finding #5): a
#     tool-using round emits ONLY ungrounded pre-tool prose and the turn then
#     ends with no grounded final answer, so the sanitized body is "". agent_loop
#     must STILL emit clean_response (as "") so the route can distinguish
#     "sanitized to empty" from "no sanitized value" — otherwise the route's
#     fallback persists the raw pre-tool prose. The saved body must be empty,
#     NOT the fabricated pre-tool answer.
# ---------------------------------------------------------------------------
def test_empty_sanitized_tool_turn_saves_empty_not_raw_pretool_prose(monkeypatch):
    exec_calls = []
    _patch_common(monkeypatch, exec_calls)
    round1 = "FAKE-ONLY-PRETOOL answer: 999.\n\n```bash\necho compute\n```"
    # The follow-up round produces no final prose, so nothing grounded is written.
    events = _run_loop(monkeypatch, "llama-2-7b-chat", [round1], round2_text="")

    assert len(exec_calls) == 1, exec_calls
    # The raw delta accumulation still carries the fabricated pre-tool answer.
    assert "FAKE-ONLY-PRETOOL" in _accumulated_deltas(events)
    # agent_loop must emit clean_response even though it sanitized to empty.
    clean = _clean_response(events)
    assert clean == "", f"empty-sanitized tool turn must emit clean_response='', got {clean!r}"
    # The body the route persists must be the (empty) sanitized body, not raw.
    body = _route_saved_body(events)
    assert "FAKE-ONLY-PRETOOL" not in body, f"raw pre-tool prose leaked into saved body: {body!r}"
    assert body == "", f"saved body should be empty, got {body!r}"


def _route_outer_metrics(events):
    """Mirror the route's last_metrics selection: only the outer (non-teacher)
    metrics event is persisted; a nested teacher metrics must not replace it."""
    last = None
    for e in events:
        if e.get("type") == "metrics" and not e.get("teacher"):
            last = e.get("data", {})
    return last


# ---------------------------------------------------------------------------
# 7. Gap D (RaresKeY review on PR #4015, finding #4): when the inline teacher
#    takeover ITSELF runs a tool-backed turn, its nested stream_agent_loop emits
#    its OWN metrics event carrying clean_response, forwarded with teacher=True.
#    The outer route must NOT let that nested metrics clobber the student's
#    sanitized body or the outer turn's metrics — and must use the teacher's
#    sanitized clean_response as the teacher segment, not the raw deltas (which
#    carry the teacher's own pre-tool prose). Saved body = student answer +
#    teacher grounded answer, each once, with no pre-tool prose from either.
# ---------------------------------------------------------------------------
def test_tool_using_teacher_takeover_keeps_both_sanitized_segments(monkeypatch):
    exec_calls = []
    _patch_common(monkeypatch, exec_calls)

    async def _fake_teacher(*a, **k):
        # Mirrors run_teacher_inline forwarding a tool-using teacher run: the
        # teacher streams its own pre-tool prose, then emits its nested metrics
        # with a sanitized clean_response — all tagged teacher=True
        # (src/teacher_escalation.py sets payload["teacher"] = True on each).
        yield 'data: ' + json.dumps(
            {"delta": "TEACHER-PRETOOL guess: 111.", "teacher": True}
        ) + '\n\n'
        yield 'data: ' + json.dumps(
            {"type": "metrics", "teacher": True,
             "data": {"clean_response": "TEACHER-GROUNDED real total is 43."}}
        ) + '\n\n'

    monkeypatch.setattr(
        "src.teacher_escalation.run_teacher_inline", _fake_teacher, raising=False
    )

    round1 = "STUDENT-PRETOOL guess: 999.\n\n```bash\necho compute\n```"
    events = _run_loop(
        monkeypatch, "llama-2-7b-chat", [round1],
        round2_text="STUDENT-GROUNDED answer: Real A and Real B.",
    )

    body = _route_saved_body(events)
    # Student's sanitized grounded answer survives (not clobbered by the nested
    # teacher metrics).
    assert "STUDENT-GROUNDED" in body, f"student answer dropped: {body!r}"
    # Teacher's sanitized grounded answer present, exactly once (no duplication).
    assert body.count("TEACHER-GROUNDED") == 1, \
        f"teacher answer missing/duplicated: {body!r}"
    # No pre-tool prose from EITHER student or teacher.
    assert "STUDENT-PRETOOL" not in body, f"student pre-tool prose leaked: {body!r}"
    assert "TEACHER-PRETOOL" not in body, f"teacher pre-tool prose leaked: {body!r}"

    # The persisted metrics describe the OUTER turn, not the nested teacher run.
    outer = _route_outer_metrics(events)
    assert outer is not None and "round_texts" in outer, \
        f"outer-turn metrics lost to nested teacher metrics: {outer!r}"


# ---------------------------------------------------------------------------
# 8. Gap D, no-tool student variant (fresh-context review of finding #4): a
#    student that answers WITHOUT tools emits no clean_response, so _clean_body
#    is None. A tool-using teacher takeover must still contribute its sanitized
#    answer (not its raw pre-tool prose), and the student's raw answer is kept.
# ---------------------------------------------------------------------------
def test_no_tool_student_then_tool_using_teacher_excludes_teacher_pretool(monkeypatch):
    exec_calls = []
    _patch_common(monkeypatch, exec_calls)

    async def _fake_teacher(*a, **k):
        yield 'data: ' + json.dumps(
            {"delta": "TEACHER-PRETOOL guess: 111.", "teacher": True}
        ) + '\n\n'
        yield 'data: ' + json.dumps(
            {"type": "metrics", "teacher": True,
             "data": {"clean_response": "TEACHER-GROUNDED real total is 43."}}
        ) + '\n\n'

    monkeypatch.setattr(
        "src.teacher_escalation.run_teacher_inline", _fake_teacher, raising=False
    )

    # Student gives up with no tool call → one round, no clean_response.
    events = _run_loop(
        monkeypatch, "llama-2-7b-chat", ["STUDENT-NOTOOL I cannot compute that."]
    )

    body = _route_saved_body(events)
    assert "STUDENT-NOTOOL" in body, f"student raw answer dropped: {body!r}"
    assert body.count("TEACHER-GROUNDED") == 1, \
        f"teacher answer missing/duplicated: {body!r}"
    assert "TEACHER-PRETOOL" not in body, f"teacher pre-tool prose leaked: {body!r}"
