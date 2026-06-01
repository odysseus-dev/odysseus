"""
Tests for do_pipeline() and do_second_opinion() and their sub-functions.

Follows pytest and Python standards (PEP 8, PEP 484).
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from typing import Dict


async def _pipeline(content: str) -> Dict:
    from src.ai_interaction import do_pipeline
    return await do_pipeline(content)


async def _second_opinion(content: str, session_id: str = "s1") -> Dict:
    from src.ai_interaction import do_second_opinion
    return await do_second_opinion(content, session_id=session_id)


# ---------------------------------------------------------------------------
# do_pipeline — input parsing
# ---------------------------------------------------------------------------

class TestPipelineInputParsing:

    def test_parse_steps_from_json(self) -> None:
        from src.ai_interaction import _pipeline_parse_steps
        steps, err = _pipeline_parse_steps('{"steps": [{"model": "a", "instruction": "do x"}]}')
        assert err is None
        assert len(steps) == 1
        assert steps[0]["model"] == "a"

    def test_parse_steps_from_json_list(self) -> None:
        from src.ai_interaction import _pipeline_parse_steps
        steps, err = _pipeline_parse_steps('[{"model": "a", "instruction": "do x"}]')
        assert err is None
        assert len(steps) == 1

    def test_parse_steps_from_pipe_format(self) -> None:
        from src.ai_interaction import _pipeline_parse_steps
        steps, err = _pipeline_parse_steps("model_a | Draft essay\nmodel_b | Review it")
        assert err is None
        assert len(steps) == 2
        assert steps[0]["model"] == "model_a"
        assert steps[1]["instruction"] == "Review it"

    def test_parse_empty_content_returns_error(self) -> None:
        from src.ai_interaction import _pipeline_parse_steps
        steps, err = _pipeline_parse_steps("")
        assert err is not None
        assert "error" in err

    def test_parse_line_without_pipe_returns_error(self) -> None:
        from src.ai_interaction import _pipeline_parse_steps
        steps, err = _pipeline_parse_steps("model_a do x without pipe")
        assert err is not None
        assert "error" in err


# ---------------------------------------------------------------------------
# do_pipeline — validation
# ---------------------------------------------------------------------------

class TestPipelineValidation:

    @pytest.mark.asyncio
    async def test_empty_pipeline_returns_error(self) -> None:
        result = await _pipeline("")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_too_many_steps_returns_error(self) -> None:
        from src.ai_interaction import MAX_PIPELINE_STEPS
        lines = "\n".join(f"model | step {i}" for i in range(MAX_PIPELINE_STEPS + 1))
        result = await _pipeline(lines)
        assert "error" in result
        assert str(MAX_PIPELINE_STEPS) in result["error"]

    @pytest.mark.asyncio
    async def test_step_missing_model_returns_error(self) -> None:
        with patch("src.ai_interaction._resolve_model", return_value=("url", "model", {})):
            result = await _pipeline('{"steps": [{"instruction": "do x"}]}')
        assert "error" in result

    @pytest.mark.asyncio
    async def test_step_missing_instruction_returns_error(self) -> None:
        with patch("src.ai_interaction._resolve_model", return_value=("url", "model", {})):
            result = await _pipeline('{"steps": [{"model": "gpt-4"}]}')
        assert "error" in result

    @pytest.mark.asyncio
    async def test_unknown_model_returns_error(self) -> None:
        with patch("src.ai_interaction._resolve_model", side_effect=ValueError("not found")):
            result = await _pipeline("nonexistent_model | do something")
        assert "error" in result


# ---------------------------------------------------------------------------
# do_pipeline — successful execution
# ---------------------------------------------------------------------------

class TestPipelineExecution:

    @pytest.mark.asyncio
    async def test_single_step_pipeline(self) -> None:
        with patch("src.ai_interaction._resolve_model", return_value=("url", "model-a", {})), \
             patch("src.ai_interaction.llm_call_async", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = "Step 1 output"
            result = await _pipeline("model-a | Draft an essay about the ocean")
        assert "results" in result
        assert "final_output" in result
        assert result["final_output"] == "Step 1 output"
        assert len(result["steps"]) == 1

    @pytest.mark.asyncio
    async def test_two_step_pipeline_chains_output(self) -> None:
        outputs = ["Draft output", "Reviewed output"]
        with patch("src.ai_interaction._resolve_model", return_value=("url", "model", {})), \
             patch("src.ai_interaction.llm_call_async", new_callable=AsyncMock) as mock_llm:
            mock_llm.side_effect = outputs
            result = await _pipeline("model-a | Draft\nmodel-b | Review")
        assert result["final_output"] == "Reviewed output"
        assert len(result["steps"]) == 2
        # Second call should receive first output
        second_call_args = mock_llm.call_args_list[1]
        messages = second_call_args[0][2]
        user_msg = next(m for m in messages if m["role"] == "user")
        assert "Draft output" in user_msg["content"]

    @pytest.mark.asyncio
    async def test_pipeline_result_contains_step_info(self) -> None:
        with patch("src.ai_interaction._resolve_model", return_value=("url", "model-a", {})), \
             patch("src.ai_interaction.llm_call_async", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = "output"
            result = await _pipeline("model-a | Do task")
        step = result["steps"][0]
        assert step["step"] == 1
        assert step["model"] == "model-a"
        assert "instruction" in step
        assert "output" in step

    @pytest.mark.asyncio
    async def test_pipeline_step_output_is_truncated(self) -> None:
        from src.ai_interaction import RESPONSE_TRUNCATE_PIPELINE
        long_output = "x" * (RESPONSE_TRUNCATE_PIPELINE + 100)
        with patch("src.ai_interaction._resolve_model", return_value=("url", "model", {})), \
             patch("src.ai_interaction.llm_call_async", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = long_output
            result = await _pipeline("model | do task")
        step_output = result["steps"][0]["output"]
        assert len(step_output) <= RESPONSE_TRUNCATE_PIPELINE + 30


# ---------------------------------------------------------------------------
# do_second_opinion — input validation
# ---------------------------------------------------------------------------

class TestSecondOpinionInput:

    @pytest.mark.asyncio
    async def test_missing_model_returns_error(self) -> None:
        result = await _second_opinion("")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_no_session_context_returns_error(self) -> None:
        """Without session manager, there is no context to review."""
        with patch("src.ai_interaction._resolve_model", return_value=("url", "model", {})), \
             patch("src.ai_interaction._session_manager", None):
            result = await _second_opinion("model-name", session_id=None)
        assert "error" in result

    @pytest.mark.asyncio
    async def test_model_not_found_returns_error(self) -> None:
        with patch("src.ai_interaction._resolve_model", side_effect=ValueError("not found")):
            result = await _second_opinion("nonexistent_model_xyz")
        assert "error" in result


# ---------------------------------------------------------------------------
# do_second_opinion — context extraction helper
# ---------------------------------------------------------------------------

class TestSecondOpinionContextExtraction:

    def test_extract_context_from_messages(self) -> None:
        from src.ai_interaction import _second_opinion_build_context
        messages = [
            {"role": "user", "content": "How do I use Python?"},
            {"role": "assistant", "content": "You can install it with pip."},
        ]
        context = _second_opinion_build_context(messages)
        assert "[USER]" in context
        assert "[ASSISTANT]" in context
        assert "How do I use Python?" in context

    def test_extract_context_limits_message_count(self) -> None:
        from src.ai_interaction import _second_opinion_build_context, SESSION_CONTEXT_WINDOW
        messages = [{"role": "user", "content": f"msg {i}"} for i in range(SESSION_CONTEXT_WINDOW + 10)]
        context = _second_opinion_build_context(messages)
        # Only last SESSION_CONTEXT_WINDOW messages should appear
        assert "msg 0" not in context
        assert f"msg {SESSION_CONTEXT_WINDOW + 9}" in context

    def test_extract_context_handles_multipart_content(self) -> None:
        from src.ai_interaction import _second_opinion_build_context
        messages = [
            {"role": "user", "content": [{"type": "text", "text": "Part 1"}, {"type": "text", "text": "Part 2"}]},
        ]
        context = _second_opinion_build_context(messages)
        assert "Part 1" in context
        assert "Part 2" in context

    def test_extract_context_truncates_long_messages(self) -> None:
        from src.ai_interaction import _second_opinion_build_context, CONTEXT_MESSAGE_TEXT_LIMIT
        messages = [{"role": "user", "content": "x" * (CONTEXT_MESSAGE_TEXT_LIMIT + 100)}]
        context = _second_opinion_build_context(messages)
        assert len(context) < CONTEXT_MESSAGE_TEXT_LIMIT + 200
