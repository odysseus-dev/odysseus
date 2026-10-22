"""Identical audit tasks and explicitly untrusted skill context."""

from src.prompt_security import untrusted_context_message

def _skill_test_task(skill: dict) -> str:
    """Build the one shared task used by both sides of an audit comparison."""
    if not isinstance(skill, dict):
        skill = {}
    ctx = (skill.get("when_to_use") or skill.get("description") or skill.get("name") or "").strip()
    return (
        "Complete this task end-to-end. Use this exact task context and do not ask "
        "the user for more input. If a harmless fixture is needed, create the "
        "smallest realistic one that satisfies the context, state it explicitly, "
        "then complete and verify the result. Do not perform destructive or "
        "externally visible actions. Task context: " + (ctx or "(general)")
    )


def _skill_baseline_task(skill: dict) -> str:
    """Backward-compatible alias; both audit arms must receive identical text."""
    return _skill_test_task(skill)


def _skill_test_messages(md: str, task: str) -> list[dict]:
    """Keep user-editable skill text out of the trusted system role."""
    return [
        {
            "role": "system",
            "content": (
                "You are TESTING a skill. Follow the supplied reusable procedure "
                "to complete the user's task for real, using available tools step "
                "by step. If the skill is wrong, unclear, or references tools that "
                "do not exist, do your best; the problems will be reviewed afterward."
            ),
        },
        untrusted_context_message("skill under test", md),
        {"role": "user", "content": task},
    ]


def _skill_baseline_messages(task: str) -> list[dict]:
    return [
        {
            "role": "system",
            "content": (
                "You are completing a baseline audit run. Do not use any saved "
                "skill text. Solve the user's task using only your normal tools "
                "and reasoning."
            ),
        },
        {"role": "user", "content": task},
    ]
