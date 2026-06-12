"""Agent-loop tests for deterministic image-discovery pre-routing."""

import asyncio
import json

import src.agent_loop as al
from src import media_registry as mr


def _collect(gen):
    async def _run():
        return [c async for c in gen]

    return asyncio.run(_run())


def _events(chunks):
    out = []
    for chunk in chunks:
        if chunk.startswith("data: ") and not chunk.startswith("data: [DONE]"):
            try:
                out.append(json.loads(chunk[6:]))
            except Exception:
                pass
    return out


def _patch_loop_basics(monkeypatch):
    monkeypatch.setattr(al, "get_setting", lambda key, default=None: default, raising=False)
    monkeypatch.setattr(al, "get_mcp_manager", lambda: None, raising=False)
    monkeypatch.setattr(al, "estimate_tokens", lambda *a, **k: 10, raising=False)
    monkeypatch.setattr(al, "_load_mcp_disabled_map", lambda: {}, raising=False)
    monkeypatch.setattr(
        al,
        "_build_system_prompt",
        lambda messages, *a, **k: (messages, []),
        raising=False,
    )

    async def _no_teacher(*_a, **_k):
        return
        yield  # pragma: no cover

    monkeypatch.setattr(
        "src.teacher_escalation.run_teacher_inline",
        _no_teacher,
        raising=False,
    )

    def _trim(messages, *a, **k):
        return messages

    monkeypatch.setattr("src.context_compactor.trim_for_context", _trim, raising=False)


def _no_model_result():
    _, degraded = mr.default_image_model_or_degraded(
        settings={"media_models": [], "default_image_media_model": "", "image_model": ""},
    )
    text = mr.format_degraded_message(degraded)
    return {
        "results": text,
        "models": [],
        "status": degraded.get("status"),
        "available": False,
    }


_CREATION_PROMPT = "Generate an image of a red bicycle on a white background."


def test_deterministic_image_creation_answer_uses_degraded_text():
    payload = _no_model_result()
    answer = al._deterministic_image_creation_answer(payload)
    assert answer == payload["results"]
    assert "no image model" in answer.lower()
    assert "available as a tool" in answer.lower()


def test_deterministic_image_creation_answer_none_when_models_available():
    assert al._deterministic_image_creation_answer({
        "results": "Configured image models (1):",
        "available": True,
        "status": None,
    }) is None


def test_creation_preroute_streams_degraded_answer_without_model_call(monkeypatch):
    _patch_loop_basics(monkeypatch)
    model_called = []

    async def _fake_stream(*_a, **_k):
        model_called.append(True)
        yield "data: " + json.dumps({"delta": "I cannot generate an image"}) + "\n\n"
        yield "data: [DONE]\n\n"

    async def _fake_list_media_models(content, session_id=None, owner=None):
        return _no_model_result()

    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)
    monkeypatch.setattr(
        "src.agent_tools.media_tools.list_media_models",
        _fake_list_media_models,
        raising=False,
    )
    monkeypatch.setattr(
        "src.tool_index.should_preroute_image_discovery",
        lambda query, owner="", settings=None: "creation"
        if query == _CREATION_PROMPT
        else None,
        raising=False,
    )

    chunks = _collect(
        al.stream_agent_loop(
            "http://local.test/v1",
            "local-model",
            [{"role": "user", "content": _CREATION_PROMPT}],
            max_rounds=1,
            relevant_tools={"list_media_models", "bash"},
        )
    )
    events = _events(chunks)
    assert model_called == []
    tool_starts = [e for e in events if e.get("type") == "tool_start"]
    assert tool_starts and tool_starts[0]["tool"] == "list_media_models"
    agent_steps = [e for e in events if e.get("type") == "agent_step"]
    assert agent_steps and agent_steps[0]["round"] == 1
    deltas = "".join(e.get("delta", "") for e in events if "delta" in e)
    delta_idx = next(i for i, e in enumerate(events) if "delta" in e)
    agent_step_idx = next(i for i, e in enumerate(events) if e.get("type") == "agent_step")
    assert agent_step_idx < delta_idx, "agent_step must precede final delta for live render"
    assert "no image model" in deltas.lower()
    assert "available as a tool" in deltas.lower()
    assert "cannot generate" not in deltas.lower()
    metrics = next(e for e in events if e.get("type") == "metrics")
    assert metrics["data"]["round_texts"] == [deltas]


def test_capability_preroute_still_calls_model(monkeypatch):
    _patch_loop_basics(monkeypatch)
    model_called = []

    async def _fake_stream(*_a, **_k):
        model_called.append(True)
        yield "data: " + json.dumps({"delta": "Image generation is available as a tool."}) + "\n\n"
        yield "data: [DONE]\n\n"

    async def _fake_list_media_models(content, session_id=None, owner=None):
        return _no_model_result()

    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)
    monkeypatch.setattr(
        "src.agent_tools.media_tools.list_media_models",
        _fake_list_media_models,
        raising=False,
    )
    monkeypatch.setattr(
        "src.tool_index.should_preroute_image_discovery",
        lambda query, owner="", settings=None: "capability"
        if query == "Can you make images?"
        else None,
        raising=False,
    )

    _collect(
        al.stream_agent_loop(
            "http://local.test/v1",
            "local-model",
            [{"role": "user", "content": "Can you make images?"}],
            max_rounds=1,
            relevant_tools={"list_media_models"},
        )
    )
    assert model_called == [True]


def test_configured_creation_preroutes_generate_image_without_model_call(monkeypatch):
    _patch_loop_basics(monkeypatch)
    model_called = []
    gen_called = []

    async def _fake_stream(*_a, **_k):
        model_called.append(True)
        yield "data: " + json.dumps({"delta": "invented local_comfyui_sd_1_5"}) + "\n\n"
        yield "data: [DONE]\n\n"

    async def _fake_generate(content, session_id=None, owner=None, progress_cb=None):
        gen_called.append({
            "content": content,
            "session_id": session_id,
            "owner": owner,
        })
        if progress_cb is not None:
            await progress_cb({"type": "progress", "message": "Waiting for ComfyUI to finish…"})
        return {
            "results": "Generated image for: a red bicycle",
            "image_url": "/api/generated-image/abc.png",
            "image_id": "gid-1",
            "image_prompt": content,
            "image_model": "sd15-comfy",
            "image_size": "512x512",
        }

    async def _fail_list_media_models(*_a, **_k):
        raise AssertionError("configured creation must not call list_media_models")

    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)
    monkeypatch.setattr("src.ai_interaction.do_generate_image", _fake_generate, raising=False)
    monkeypatch.setattr(
        "src.agent_tools.media_tools.list_media_models",
        _fail_list_media_models,
        raising=False,
    )
    monkeypatch.setattr(
        "src.tool_index.should_preroute_image_discovery",
        lambda query, owner="", settings=None: "configured_creation"
        if query == _CREATION_PROMPT
        else None,
        raising=False,
    )

    chunks = _collect(
        al.stream_agent_loop(
            "http://local.test/v1",
            "local-model",
            [{"role": "user", "content": _CREATION_PROMPT}],
            max_rounds=1,
            relevant_tools={"list_media_models", "generate_image"},
            session_id="sess-live",
            owner="alice",
        )
    )
    events = _events(chunks)
    assert model_called == []
    assert gen_called == [{
        "content": _CREATION_PROMPT,
        "session_id": "sess-live",
        "owner": "alice",
    }]
    tool_starts = [e for e in events if e.get("type") == "tool_start"]
    assert len(tool_starts) == 1
    assert tool_starts[0]["tool"] == "generate_image"
    assert not any(e.get("tool") == "list_media_models" for e in tool_starts)
    tool_outputs = [e for e in events if e.get("type") == "tool_output"]
    assert tool_outputs[0]["tool"] == "generate_image"
    assert tool_outputs[0]["image_url"] == "/api/generated-image/abc.png"
    assert tool_outputs[0]["image_id"] == "gid-1"
    progress = [e for e in events if e.get("type") == "tool_progress"]
    assert progress and progress[0]["tool"] == "generate_image"
    deltas = "".join(e.get("delta", "") for e in events if "delta" in e)
    assert "Direct link:" in deltas
    assert "/api/generated-image/abc.png" in deltas
    assert "local_comfyui" not in deltas.lower()
    agent_steps = [e for e in events if e.get("type") == "agent_step"]
    assert agent_steps and agent_steps[0]["round"] == 1
    tool_out_idx = next(i for i, e in enumerate(events) if e.get("type") == "tool_output")
    agent_step_idx = next(i for i, e in enumerate(events) if e.get("type") == "agent_step")
    delta_idx = next(i for i, e in enumerate(events) if "delta" in e)
    assert tool_out_idx < agent_step_idx < delta_idx
    metrics = next(e for e in events if e.get("type") == "metrics")
    assert metrics["data"]["round_texts"] == [deltas]
    tool_events = metrics["data"]["tool_events"]
    assert tool_events[0]["tool"] == "generate_image"
    assert tool_events[0]["image_url"] == "/api/generated-image/abc.png"
    assert tool_events[0]["image_id"] == "gid-1"
    assert any(c == "data: [DONE]\n\n" for c in chunks)


def test_configured_creation_failure_returns_sanitized_error(monkeypatch):
    _patch_loop_basics(monkeypatch)

    async def _fake_generate(*_a, **_k):
        return {"error": "ComfyUI did not finish the image within 300s."}

    monkeypatch.setattr("src.ai_interaction.do_generate_image", _fake_generate, raising=False)
    monkeypatch.setattr(
        "src.tool_index.should_preroute_image_discovery",
        lambda query, owner="", settings=None: "configured_creation"
        if query == _CREATION_PROMPT
        else None,
        raising=False,
    )

    chunks = _collect(
        al.stream_agent_loop(
            "http://local.test/v1",
            "local-model",
            [{"role": "user", "content": _CREATION_PROMPT}],
            max_rounds=1,
        )
    )
    events = _events(chunks)
    tool_outputs = [e for e in events if e.get("type") == "tool_output"]
    assert tool_outputs[0]["exit_code"] == 1
    deltas = "".join(e.get("delta", "") for e in events if "delta" in e)
    assert "300s" in deltas
    assert "://" not in deltas
