"""`ask_user` — the agent poses a multiple-choice question to the user.

The tool is a pure UI-control marker: it does no I/O. `execute_tool_block`
returns an `ask_user` payload that the agent loop turns into an `ask_user` SSE
event and then ends the turn so the chat waits for the user's selection.
"""
import asyncio
import json
from pathlib import Path

from src.agent_tools import ToolBlock, TOOL_TAGS  # noqa: E402  (import first to avoid circular)
from src.tool_execution import execute_tool_block
from src.tool_index import ALWAYS_AVAILABLE, BUILTIN_TOOL_DESCRIPTIONS
from src.tool_security import is_public_blocked_tool

ROOT = Path(__file__).resolve().parents[1]


def _run(content):
    return asyncio.run(execute_tool_block(ToolBlock("ask_user", content)))


def test_valid_question_returns_ask_user_payload():
    content = json.dumps({
        "question": "Which database should I use?",
        "options": [
            {"label": "PostgreSQL", "description": "Relational, ACID"},
            {"label": "SQLite", "description": "Zero-config, file-based"},
        ],
    })
    desc, result = _run(content)
    assert result.get("exit_code") == 0
    assert "error" not in result
    payload = result["ask_user"]
    assert payload["question"] == "Which database should I use?"
    assert [o["label"] for o in payload["options"]] == ["PostgreSQL", "SQLite"]
    assert payload["options"][0]["description"] == "Relational, ACID"
    assert payload["multi"] is False
    assert "PostgreSQL" in result["output"]


def test_multi_flag_is_carried():
    content = json.dumps({
        "question": "Which features?",
        "options": [{"label": "A"}, {"label": "B"}, {"label": "C"}],
        "multi": True,
    })
    _, result = _run(content)
    assert result["ask_user"]["multi"] is True
    assert len(result["ask_user"]["options"]) == 3


def test_string_options_are_accepted():
    content = json.dumps({"question": "Pick one", "options": ["Yes", "No"]})
    _, result = _run(content)
    labels = [o["label"] for o in result["ask_user"]["options"]]
    assert labels == ["Yes", "No"]


def test_options_are_capped_at_six():
    content = json.dumps({
        "question": "Pick",
        "options": [{"label": f"opt{i}"} for i in range(10)],
    })
    _, result = _run(content)
    assert len(result["ask_user"]["options"]) == 6


def test_batched_questions_are_normalized_and_ignore_legacy_fields():
    content = json.dumps({
        "question": "Ignored?",
        "options": [{"label": "Ignored A"}, {"label": "Ignored B"}],
        "multi": True,
        "questions": [
            {
                "question": "Which target?",
                "header": "Target",
                "options": [
                    {"label": "API", "description": "Backend route"},
                    {"label": "UI"},
                ],
            },
            {
                "question": "Which checks?",
                "options": [{"label": "Tests"}, {"label": "Lint"}],
                "multiSelect": True,
            },
        ],
    })
    desc, result = _run(content)
    assert result["exit_code"] == 0
    assert desc == "ask_user: 2 questions"
    payload = result["ask_user"]
    assert "question" not in payload
    assert payload["questions"][0]["header"] == "Target"
    assert payload["questions"][0]["multiSelect"] is False
    assert payload["questions"][1]["header"] == "Q2"
    assert payload["questions"][1]["multiSelect"] is True
    assert payload["questions"][0]["options"][0]["description"] == "Backend route"


def test_batched_questions_reject_question_bounds():
    valid = {"question": "Q?", "options": [{"label": "A"}, {"label": "B"}]}
    for questions in ([], [valid] * 5):
        _, result = _run(json.dumps({"questions": questions}))
        assert result["exit_code"] == 1
        assert "questions" in result["error"]


def test_batched_questions_reject_long_header():
    content = json.dumps({
        "questions": [{
            "question": "Pick one?",
            "header": "Too long header",
            "options": [{"label": "A"}, {"label": "B"}],
        }],
    })
    _, result = _run(content)
    assert result["exit_code"] == 1
    assert "header" in result["error"]


def test_batched_questions_reject_option_bounds():
    for options in ([{"label": "A"}], [{"label": str(i)} for i in range(5)]):
        content = json.dumps({"questions": [{"question": "Pick one?", "options": options}]})
        _, result = _run(content)
        assert result["exit_code"] == 1
        assert "options" in result["error"]


def test_batched_answer_assembly_uses_question_text_keys_and_joined_multi_select():
    renderer = (ROOT / "static" / "js" / "chatRenderer.js").read_text(encoding="utf-8")
    assert "export function buildAskUserAnswersPayload(questions, answerState)" in renderer
    assert "answers[question.question] = value.join(', ');" in renderer
    assert "send(JSON.stringify(buildAskUserAnswersPayload(batchQuestions, answerState)))" in renderer


def test_fewer_than_two_options_is_rejected():
    content = json.dumps({"question": "Only one?", "options": [{"label": "A"}]})
    _, result = _run(content)
    assert "error" in result
    assert result.get("exit_code") == 1


def test_missing_question_is_rejected():
    content = json.dumps({"options": [{"label": "A"}, {"label": "B"}]})
    _, result = _run(content)
    assert "error" in result


def test_serializer_round_trips_structured_args():
    from src.tool_schemas import function_call_to_tool_block
    args = {"question": "Q?", "options": [{"label": "A"}, {"label": "B"}], "multi": True}
    block = function_call_to_tool_block("ask_user", json.dumps(args))
    assert block is not None
    assert block.tool_type == "ask_user"
    assert json.loads(block.content) == args


def test_serializer_keeps_unicode_readable_for_tool_trace():
    from src.tool_schemas import function_call_to_tool_block

    args = {
        "question": "¿Qué proyecto prefieres?",
        "options": [{"label": "Reseñas"}, {"label": "Clasificación"}],
    }
    block = function_call_to_tool_block("ask_user", json.dumps(args, ensure_ascii=False))
    assert "¿Qué proyecto prefieres?" in block.content
    assert "Reseñas" in block.content
    assert "\\u00" not in block.content


def test_registered_everywhere():
    # TOOL_TAGS gate (serializer rejects unknown tools)
    assert "ask_user" in TOOL_TAGS
    # Always reachable + has a retrieval description
    assert "ask_user" in ALWAYS_AVAILABLE
    assert "ask_user" in BUILTIN_TOOL_DESCRIPTIONS
    # Function schema present
    from src.tool_schemas import FUNCTION_TOOL_SCHEMAS
    schemas = {s["function"]["name"]: s for s in FUNCTION_TOOL_SCHEMAS}
    names = set(schemas)
    assert "ask_user" in names
    ask_user_params = schemas["ask_user"]["function"]["parameters"]
    assert {"required": ["questions"]} in ask_user_params["anyOf"]
    questions = ask_user_params["properties"]["questions"]
    assert questions["minItems"] == 1
    assert questions["maxItems"] == 4
    assert questions["items"]["properties"]["header"]["maxLength"] == 12
    assert questions["items"]["properties"]["options"]["minItems"] == 2
    assert questions["items"]["properties"]["options"]["maxItems"] == 4
    assert questions["items"]["properties"]["multiSelect"]["default"] is False
    # Not admin/public-gated — any user can be asked
    assert is_public_blocked_tool("ask_user") is False
