"""Regression coverage: agent-mode turns must caption images too.

Background image captioning (see routes/chat_helpers.py's
_caption_multimodal_image_attachments, tested directly in
tests/test_multimodal_caption_provenance.py) originally only fired for
plain /api/chat and chat-mode /api/chat_stream turns — agent mode
deliberately skipped it, reasoning that a multi-round agent loop can hop
between fallback models mid-loop, so there's no single "the model that
answered and saw the image" to caption with.

That reasoning doesn't hold: the image (when present) is only ever sent as
part of round 1's request — the same content every candidate in the
fallback chain receives — so whichever candidate actually answered round 1
is the one confirmed model that saw it, independent of what any later
round falls over to. This asserts that source-level wiring is in place in
routes/chat_routes.py's agent-mode SSE handler, the same way
test_vision_owner_scope.py::test_request_vision_call_sites_pass_owner
guards a different set of vision call sites — there's no practical way to
drive the real SSE stream_agent_loop() generator end-to-end from a unit
test without reimplementing most of it.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHAT_ROUTES = (ROOT / "routes" / "chat_routes.py").read_text(encoding="utf-8")


def _agent_mode_block() -> str:
    start = CHAT_ROUTES.index("# ── Agent mode: full agent loop with tools ──")
    # The agent-mode branch runs to the except clause that handles a
    # disconnect/cancellation during the stream.
    end = CHAT_ROUTES.index("except (asyncio.CancelledError, GeneratorExit):", start)
    return CHAT_ROUTES[start:end]


def test_agent_mode_seeds_round_one_candidate_index():
    block = _agent_mode_block()
    assert "_agent_round_candidate_index = {1: 0}" in block


def test_agent_mode_fallback_handler_updates_round_candidate_index():
    block = _agent_mode_block()
    assert '_fallback_candidate_index = data.get("candidate_index")' in block
    assert "_agent_round_candidate_index[_event_round] = _fallback_candidate_index" in block


def test_agent_mode_passes_caption_params_from_round_one_candidate():
    block = _agent_mode_block()
    assert "_r1_candidate_index = _agent_round_candidate_index.get(1, 0)" in block
    assert "attachment_meta=ctx.preprocessed.attachment_meta" in block
    assert "caption_endpoint_url=_agent_cand[0] if _agent_cand else None" in block
    assert "caption_model=_agent_cand[1] if _agent_cand else None" in block
    assert "caption_headers=_agent_cand[2] if _agent_cand else None" in block
    assert "upload_handler=upload_handler" in block


def test_agent_mode_no_longer_carries_the_old_skip_rationale():
    # The old "deliberately not passing attachment_meta/caption_*" comment
    # described the exact gap this fix closes — its presence would mean the
    # fix regressed back to skipping agent-mode captioning.
    block = _agent_mode_block()
    assert "deliberately not passing" not in block
