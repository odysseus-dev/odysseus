"""Leftover bounded product jobs must use submit_model_job, not llm_call*.

These leftovers are typed one-shot jobs. After replacement they fail closed
through the isolated worker. No nested Odysseus completions loop. Specialist
image-gen and capability probes stay out of this file.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_LLM_PRIMITIVES = (
    "llm_call(",
    "llm_call_async(",
    "llm_call_async_with_fallback",
    "llm_call_async_with_route_fallback",
    "task_llm_call_async",
)


def _function_source(path: str, name: str) -> str:
    source = Path(path).read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(source, node) or ""
    raise AssertionError(f"{name} not found in {path}")


def _assert_bounded_job(body: str) -> None:
    assert "submit_model_job" in body
    assert "bounded_archetype" in body
    for primitive in _LLM_PRIMITIVES:
        assert primitive not in body, f"leftover primitive {primitive} in job"
    assert "/v1/chat/completions" not in body


@pytest.mark.parametrize(
    "path,name",
    [
        ("routes/document/document_routes.py", "ai_fill_annotations"),
        ("routes/document/document_routes.py", "ai_tidy_documents"),
        ("routes/note/note_routes.py", "dispatch_reminder"),
        ("routes/preset_routes.py", "expand_character_prompt"),
        ("routes/skills_routes.py", "_eval_skill_run"),
        ("routes/skills_routes.py", "_eval_skill_necessity"),
        ("routes/skills_routes.py", "_eval_skill_retrieval_precision"),
        ("routes/skills_routes.py", "_improve_skill_md"),
        ("routes/task/task_routes.py", "_generate_task_name"),
        ("routes/task/task_routes.py", "parse_task"),
        ("src/ai_interaction.py", "do_pipeline"),
        ("src/document_processor.py", "analyze_image_with_vl_result"),
    ],
)
def test_leftover_symbols_are_model_jobs_not_llm_call(path, name):
    _assert_bounded_job(_function_source(path, name))


def test_search_query_extract_is_model_job_not_llm_call():
    body = _function_source("src/chat_processor.py", "build_context_preface")
    _assert_bounded_job(body)


def test_do_pipeline_does_not_touch_image_gen_or_worker_client():
    pipeline = _function_source("src/ai_interaction.py", "do_pipeline")
    image = _function_source("src/ai_interaction.py", "do_generate_image")
    worker = _function_source("src/ai_interaction.py", "invoke_structured_model")
    _assert_bounded_job(pipeline)
    assert "submit_model_job" not in image
    assert "do_generate_image" not in pipeline
    assert "ODYSSEUS_MODEL_JOB_WORKER_URL" in worker
    assert "llm_call" not in worker


def test_skill_judges_do_not_open_nested_completions_loop():
    for name in (
        "_eval_skill_run",
        "_eval_skill_necessity",
        "_eval_skill_retrieval_precision",
        "_improve_skill_md",
    ):
        body = _function_source("routes/skills_routes.py", name)
        _assert_bounded_job(body)
        assert "stream_governed_agent" not in body
        assert "chat/completions" not in body


@pytest.mark.asyncio
async def test_eval_skill_run_uses_submit_model_job(monkeypatch):
    from routes.skills_routes import _eval_skill_run

    seen = {}

    def fake_job(archetype, payload, owner, **kwargs):
        seen["id"] = getattr(archetype, "id", None)
        seen["owner"] = owner
        seen["messages"] = payload.get("messages")
        return SimpleNamespace(
            output={"verdict": "pass", "confidence": 0.9, "summary": "ok", "issues": []}
        )

    monkeypatch.setattr(
        "routes.skills_routes.submit_model_job", fake_job, raising=False
    )
    result = await _eval_skill_run(
        "---\nname: demo\n---\nDo the thing.",
        "do it",
        "did it",
        "http://odysseus.local/v1/chat/completions",
        "nested-model",
        {"Authorization": "Bearer leak"},
        owner="alice",
    )
    assert result["verdict"] == "pass"
    assert seen["id"]
    assert seen["owner"] == "alice"
    assert seen["messages"]


def test_search_query_extract_uses_submit_model_job(monkeypatch):
    from src.chat_processor import ChatProcessor

    seen = {}

    def fake_job(archetype, payload, owner, **kwargs):
        seen["id"] = getattr(archetype, "id", None)
        seen["owner"] = owner
        return SimpleNamespace(output={"text": "extracted query"})

    mock_web_search = MagicMock(return_value=("Search Results", [{"url": "http://mock.com"}]))
    monkeypatch.setattr("src.chat_processor.submit_model_job", fake_job, raising=False)
    monkeypatch.setattr("src.chat_processor.comprehensive_web_search", mock_web_search)
    processor = ChatProcessor(memory_manager=MagicMock(), personal_docs_manager=MagicMock())
    session = SimpleNamespace(
        endpoint_url="http://local",
        model="test",
        headers={},
        owner="alice",
    )
    processor.build_context_preface(
        message="Some text.\n\nSearch for LLMs.",
        session=session,
        use_web=True,
        use_rag=False,
        use_memory=False,
        use_skills=False,
        owner="alice",
    )
    mock_web_search.assert_called_with("extracted query", time_filter=None, return_sources=True)
    assert seen["id"]
    assert seen["owner"] == "alice"


def test_vision_extract_uses_submit_model_job(monkeypatch, tmp_path):
    from src import document_processor as dp

    seen = {}

    def fake_job(archetype, payload, owner, **kwargs):
        seen["id"] = getattr(archetype, "id", None)
        seen["owner"] = owner
        seen["messages"] = payload.get("messages")
        return SimpleNamespace(output={"text": "description"}, audit={"resolved_model": "vision-job"})

    monkeypatch.setattr(dp, "_load_vl_settings", lambda: {"vision_enabled": True, "vision_model": "gpt-4o"})
    monkeypatch.setattr(
        dp,
        "_resolve_vl_model",
        lambda configured, owner=None: ("http://unused.test", "vision-primary", {}),
    )
    monkeypatch.setattr(dp, "submit_model_job", fake_job, raising=False)
    image = tmp_path / "image.png"
    image.write_bytes(b"not-a-real-png-but-base64-is-enough")
    assert dp.analyze_image_with_vl_result(str(image), owner="alice")["text"] == "description"
    assert seen["owner"] == "alice"
    assert seen["messages"]


@pytest.mark.asyncio
async def test_do_pipeline_uses_submit_model_job(monkeypatch):
    import src.ai_interaction as ai

    seen = []

    def fake_job(archetype, payload, owner, **kwargs):
        seen.append((getattr(archetype, "id", None), owner, payload.get("model")))
        return SimpleNamespace(output={"text": f"output from {payload.get('model')}"})

    monkeypatch.setattr(
        ai,
        "_resolve_model",
        lambda spec, owner=None: ("http://x/v1/chat/completions", "resolved-model", {}),
    )
    monkeypatch.setattr(ai, "submit_model_job", fake_job, raising=False)
    result = await ai.do_pipeline('[{"model": "m", "instruction": "go"}]', owner="u")
    assert "error" not in result, result
    assert "resolved-model" in str(result)
    assert seen
    assert seen[0][1] == "u"


def test_do_generate_image_source_unchanged_as_specialist():
    body = inspect.getsource(
        __import__("src.ai_interaction", fromlist=["do_generate_image"]).do_generate_image
    )
    assert "llm_call" not in body
    assert "submit_model_job" not in body
