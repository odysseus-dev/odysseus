# tests/test_project_context_assembly.py
from services.project.context_assembly import assemble_system_messages


MAIN_INSTRUCTIONS = "MAIN_INSTRUCTIONS"
MAIN_PROMPT = "MAIN_PROMPT"
PROJECT_INSTRUCTIONS = "PROJECT_INSTRUCTIONS"
PROJECT_PROMPT = "PROJECT_PROMPT"


def _ids(messages):
    return [m["text"] for m in messages]


def test_append_append_order():
    """Instructions append → main.instructions first; Prompt append → main.prompt second."""
    msgs = assemble_system_messages(
        main_instructions=MAIN_INSTRUCTIONS, main_prompt=MAIN_PROMPT,
        project_instructions=PROJECT_INSTRUCTIONS, project_prompt=PROJECT_PROMPT,
        instructions_override_mode="append", prompt_override_mode="append",
    )
    assert _ids(msgs) == [
        MAIN_INSTRUCTIONS, MAIN_PROMPT, PROJECT_INSTRUCTIONS, PROJECT_PROMPT,
    ]


def test_override_append_order():
    """Instructions override → project only; Prompt append → main + project."""
    msgs = assemble_system_messages(
        main_instructions=MAIN_INSTRUCTIONS, main_prompt=MAIN_PROMPT,
        project_instructions=PROJECT_INSTRUCTIONS, project_prompt=PROJECT_PROMPT,
        instructions_override_mode="override", prompt_override_mode="append",
    )
    assert _ids(msgs) == [MAIN_PROMPT, PROJECT_INSTRUCTIONS, PROJECT_PROMPT]


def test_append_override_order():
    msgs = assemble_system_messages(
        main_instructions=MAIN_INSTRUCTIONS, main_prompt=MAIN_PROMPT,
        project_instructions=PROJECT_INSTRUCTIONS, project_prompt=PROJECT_PROMPT,
        instructions_override_mode="append", prompt_override_mode="override",
    )
    assert _ids(msgs) == [
        MAIN_INSTRUCTIONS, PROJECT_INSTRUCTIONS, PROJECT_PROMPT,
    ]


def test_override_override_order():
    msgs = assemble_system_messages(
        main_instructions=MAIN_INSTRUCTIONS, main_prompt=MAIN_PROMPT,
        project_instructions=PROJECT_INSTRUCTIONS, project_prompt=PROJECT_PROMPT,
        instructions_override_mode="override", prompt_override_mode="override",
    )
    assert _ids(msgs) == [PROJECT_INSTRUCTIONS, PROJECT_PROMPT]
