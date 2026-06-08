"""Issue #3222 — native function-calling models (GPT/Claude/Grok/Qwen3/DeepSeek-V,
etc.) must not have ordinary illustrative Markdown fences in their prose
(```bash, ```python, ```json examples written for the user to read) executed
as real tool calls just because the textual fallback parser matches them.

`_resolve_tool_blocks` in src/agent_loop.py picks native `tool_calls` when the
model emits them, and otherwise used to fall back unconditionally to
`parse_tool_blocks(round_response)` (the fenced-block textual parser). For a
native model that produced no real tool_calls — e.g. a "guide-only" turn where
the model writes an example command for the user to copy — that fallback used
to treat the example fence as an executable action, causing accidental command
execution and multi-round loops.

The fix: for native function-calling models (`_is_api_model=True`) that emitted
no native tool_calls, skip the textual fenced-block fallback entirely — these
models have a reliable structured channel and a bare fence in their prose is
display text, not an attempted call. Non-native / textual-only models keep the
fallback unchanged, since fenced blocks are their *only* tool channel.

These tests drive the real `stream_agent_loop` (not just source-text regex
assertions) end-to-end with a mocked LLM stream, and assert on whether
`execute_tool_block` actually gets invoked.
"""
import asyncio
import json

import src.agent_loop as al


def _collect(gen):
    async def _run():
        return [c async for c in gen]
    return asyncio.run(_run())


def _types(chunks):
    out = []
    for c in chunks:
        if c.startswith("data: ") and not c.startswith("data: [DONE]"):
            try:
                out.append(json.loads(c[6:]))
            except Exception:
                pass
    return out


def _patch_common(monkeypatch, exec_calls):
    # Skip RAG/tool-index, MCP, and settings lookups; keep the real loop body,
    # _resolve_tool_blocks, and parse_tool_blocks intact.
    monkeypatch.setattr(al, "get_setting", lambda key, default=None: default, raising=False)
    monkeypatch.setattr(al, "get_mcp_manager", lambda: None, raising=False)
    monkeypatch.setattr(al, "estimate_tokens", lambda *a, **k: 10, raising=False)

    async def _fake_exec(block, *a, **k):
        exec_calls.append(block)
        return ("bash", {"output": "ok", "exit_code": 0})
    monkeypatch.setattr(al, "execute_tool_block", _fake_exec, raising=False)


def _run_loop(monkeypatch, model, deltas, native_calls=None, max_rounds=2, endpoint_url=None):
    """Drive stream_agent_loop with a fake LLM stream.

    `deltas` is a list of text chunks streamed for round 1 (and reused for any
    further round). `native_calls`, if given, is emitted as a native
    `tool_calls` event alongside the round-1 text.
    """
    call_count = {"n": 0}

    async def _fake_stream(_candidates, messages, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            for d in deltas:
                yield f'data: {json.dumps({"delta": d})}\n\n'
            if native_calls:
                yield f'data: {json.dumps({"type": "tool_calls", "calls": native_calls})}\n\n'
            yield "data: [DONE]\n\n"
        else:
            # Subsequent rounds: just answer plainly so the loop terminates.
            yield f'data: {json.dumps({"delta": "All done, here is your answer."})}\n\n'
            yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    gen = al.stream_agent_loop(
        endpoint_url or "https://api.openai.com/v1", model,
        [{"role": "user", "content": "Do not run anything yet, just show me an example."}],
        max_rounds=max_rounds,
        relevant_tools={"bash"},
    )
    return _types(_collect(gen))


# ---------------------------------------------------------------------------
# 1. Native model, illustrative ```bash fence, NO native tool_calls
#    -> must NOT be executed.
# ---------------------------------------------------------------------------
def test_native_model_illustrative_bash_fence_not_executed(monkeypatch):
    exec_calls = []
    _patch_common(monkeypatch, exec_calls)
    guide_only = (
        "Here is the command you would run locally:\n\n"
        "```bash\nnpm run plan:articles\n```\n\n"
        "Just paste that into your terminal — I'm not running it for you."
    )
    events = _run_loop(monkeypatch, "gpt-4o", [guide_only])
    assert exec_calls == [], f"illustrative fence should not be executed, but got: {exec_calls}"
    # No tool-call/action events should be emitted for this round either.
    assert not any(e.get("type") == "tool_call" for e in events), events


# ---------------------------------------------------------------------------
# 2. Native model that DOES emit a real native tool_calls entry
#    -> that call IS resolved/executed normally (untouched native path).
# ---------------------------------------------------------------------------
def test_native_model_real_native_tool_call_is_executed(monkeypatch):
    exec_calls = []
    _patch_common(monkeypatch, exec_calls)
    native_calls = [{"name": "bash", "arguments": json.dumps({"command": "echo hi"})}]
    events = _run_loop(
        monkeypatch, "gpt-4o",
        ["Sure, let me check that for you."],
        native_calls=native_calls,
        max_rounds=2,
    )
    assert len(exec_calls) == 1, f"expected the native tool call to execute, got: {exec_calls}"
    assert exec_calls[0].tool_type == "bash"
    assert "echo hi" in exec_calls[0].content


# ---------------------------------------------------------------------------
# 3. Non-native / textual-only model using the legitimate fenced format it
#    depends on -> still correctly parsed and executed (regression check).
# ---------------------------------------------------------------------------
def test_non_native_model_fenced_tool_call_still_executed(monkeypatch):
    exec_calls = []
    _patch_common(monkeypatch, exec_calls)
    # Neither this model name nor this endpoint host match any of the
    # native-capable keyword/host checks, so _is_api_model resolves to False
    # and the model must rely on the textual fenced-block convention to
    # invoke tools at all.
    events = _run_loop(
        monkeypatch, "llama-2-7b-chat",
        ["```bash\necho hi\n```"],
        max_rounds=2,
        endpoint_url="http://192.168.1.50:8000/v1",
    )
    assert len(exec_calls) == 1, f"non-native model's fenced tool call should still execute: {exec_calls}"
    assert exec_calls[0].tool_type == "bash"
    assert "echo hi" in exec_calls[0].content


# ---------------------------------------------------------------------------
# 4. The exact illustrative-fence shape from issue #3222's repro (```bash +
#    ```json guide-only examples) run through the real resolution path for a
#    native model -> confirm zero tool actions resolved.
# ---------------------------------------------------------------------------
def test_issue_3222_repro_guide_only_response_resolves_no_tool_actions(monkeypatch):
    exec_calls = []
    _patch_common(monkeypatch, exec_calls)
    repro = (
        "Here is the command you would run locally:\n\n"
        "```bash\nnpm run plan:articles\n```\n\n"
        "And here is an example config shape:\n\n"
        "```json\n"
        "{\n"
        '  "script": "npm run plan:articles",\n'
        '  "mode": "guide-only"\n'
        "}\n"
        "```\n"
    )
    events = _run_loop(monkeypatch, "grok-4", [repro])
    assert exec_calls == [], f"guide-only example fences must resolve to zero tool actions: {exec_calls}"


# ---------------------------------------------------------------------------
# Direct unit coverage of _resolve_tool_blocks itself (the real seam the fix
# lives in), complementing the end-to-end checks above.
# ---------------------------------------------------------------------------
def test_resolve_tool_blocks_skips_textual_fallback_for_native_models_with_no_native_calls():
    guide_only = "```bash\nnpm run plan:articles\n```\n```json\n{\"a\": 1}\n```"
    blocks, used_native = al._resolve_tool_blocks(guide_only, [], round_num=1, is_api_model=True)
    assert blocks == []
    assert used_native is False


def test_resolve_tool_blocks_keeps_textual_fallback_for_non_native_models():
    text = "```bash\necho hi\n```"
    blocks, used_native = al._resolve_tool_blocks(text, [], round_num=1, is_api_model=False)
    assert len(blocks) == 1
    assert blocks[0].tool_type == "bash"
    assert used_native is False


def test_resolve_tool_blocks_native_path_untouched_when_native_calls_present():
    native_calls = [{"name": "bash", "arguments": json.dumps({"command": "echo hi"})}]
    blocks, used_native = al._resolve_tool_blocks("some prose", native_calls, round_num=1, is_api_model=True)
    assert used_native is True
    assert len(blocks) == 1
    assert blocks[0].tool_type == "bash"


# ---------------------------------------------------------------------------
# Booyaka101's review on #3356: short-circuiting the *whole* parser for native
# models (`tool_blocks = [] if is_api_model else parse_tool_blocks(...)`) also
# silently dropped explicit [TOOL_CALL]/<invoke>/<tool_code>/DSML markup that
# leaked into content as text — a real regression for e.g. DeepSeek-V falling
# back to DSML when it can't emit structured tool_calls. The fix gates ONLY
# the fenced-code pattern (via `skip_fenced=`) so Patterns 2-5 stay active.
# ---------------------------------------------------------------------------
from src.tool_parsing import parse_tool_blocks, strip_tool_blocks  # noqa: E402


def test_skip_fenced_still_recovers_xml_invoke_markup():
    leaked = (
        "Sure, I'll look that up.\n"
        '<invoke name="web_search"><parameter name="query">latest python release</parameter></invoke>'
    )
    blocks = parse_tool_blocks(leaked, skip_fenced=True)
    assert len(blocks) == 1
    assert blocks[0].tool_type == "web_search"
    assert "latest python release" in blocks[0].content


def test_skip_fenced_still_recovers_dsml_markup():
    dsml = (
        "Let me search for that.\n"
        "<｜｜DSML｜｜tool_calls>"
        '<｜｜DSML｜｜invoke name="web_search">'
        '<｜｜DSML｜｜parameter name="query" string="true">latest python release</｜｜DSML｜｜parameter>'
        "</｜｜DSML｜｜invoke>"
        "</｜｜DSML｜｜tool_calls>"
    )
    blocks = parse_tool_blocks(dsml, skip_fenced=True)
    assert len(blocks) == 1
    assert blocks[0].tool_type == "web_search"
    assert "latest python release" in blocks[0].content


def test_skip_fenced_ignores_only_the_fenced_pattern():
    text = "```bash\nnpm run plan:articles\n```"
    assert parse_tool_blocks(text, skip_fenced=True) == []
    assert len(parse_tool_blocks(text, skip_fenced=False)) == 1


def test_resolve_tool_blocks_recovers_invoke_markup_for_native_model_with_no_native_calls():
    """End-to-end: a native model (is_api_model=True) that emitted no
    structured tool_calls but leaked an <invoke> call into its text content
    must still have that real call recovered — not dropped alongside the
    fenced-example gating."""
    leaked = (
        "I'll search for that now.\n"
        '<invoke name="web_search"><parameter name="query">odysseus changelog</parameter></invoke>'
    )
    blocks, used_native = al._resolve_tool_blocks(leaked, [], round_num=1, is_api_model=True)
    assert used_native is False
    assert len(blocks) == 1
    assert blocks[0].tool_type == "web_search"
    assert "odysseus changelog" in blocks[0].content


# ---------------------------------------------------------------------------
# strip_tool_blocks must mirror the same fenced-pattern gate so persisted text
# matches what was (not) executed: an illustrative fence that wasn't run for a
# native model shouldn't vanish from saved/reloaded history either — otherwise
# it streams once and then disappears on reload (Booyaka101's point #2).
# ---------------------------------------------------------------------------
def test_strip_tool_blocks_preserves_fence_when_skip_fenced():
    text = "Here's an example:\n\n```bash\nnpm run plan:articles\n```\n\nJust copy that."
    cleaned = strip_tool_blocks(text, skip_fenced=True)
    assert "```bash" in cleaned
    assert "npm run plan:articles" in cleaned


def test_strip_tool_blocks_still_strips_fence_by_default():
    text = "Here's an example:\n\n```bash\nnpm run plan:articles\n```\n\nJust copy that."
    cleaned = strip_tool_blocks(text, skip_fenced=False)
    assert "```bash" not in cleaned
    assert "npm run plan:articles" not in cleaned


def test_strip_tool_blocks_always_strips_invoke_and_dsml_regardless_of_skip_fenced():
    leaked = (
        "Searching now.\n"
        '<invoke name="web_search"><parameter name="query">q</parameter></invoke>'
        "\nDone."
    )
    for skip in (True, False):
        cleaned = strip_tool_blocks(leaked, skip_fenced=skip)
        assert "<invoke" not in cleaned
        assert "Searching now." in cleaned
        assert "Done." in cleaned
