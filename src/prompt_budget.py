"""
prompt_budget.py

Context-budget-aware helpers for small-context models (<=16k tokens).

When a model's context window is small, the system prompt + tool schemas
alone can consume most available tokens before the user's request starts.
These helpers detect small-context models and prune what gets injected.
"""

from typing import Dict, List, Optional

SMALL_CONTEXT_THRESHOLD = 16_000  # tokens: <= this triggers slim mode
TINY_CONTEXT_THRESHOLD = 8_000   # tokens: <= this triggers minimal mode


def slim_tool_schema(schema: dict) -> dict:
    """Return a slimmed OpenAI function-tool schema.

    Keeps name + description + required parameters (as minimal string stubs).
    Strips enum values, nested objects, optional fields, and descriptions per
    property — reducing per-tool cost from ~80-200 tokens to ~15-30 tokens.
    """
    fn = schema.get("function", {})
    if not fn:
        return schema

    slimmed_fn: Dict = {
        "name": fn.get("name", ""),
        "description": fn.get("description", ""),
    }

    params = fn.get("parameters", {})
    required = params.get("required", [])
    if required:
        slimmed_fn["parameters"] = {
            "type": "object",
            "properties": {k: {"type": "string"} for k in required},
            "required": required,
        }

    return {
        "type": schema.get("type", "function"),
        "function": slimmed_fn,
    }


_ALWAYS_FULL_SCHEMA_TOOLS = frozenset({"bash", "python"})


def apply_slim_schemas(
    schemas: List[dict],
    context_length: int,
    always_full: Optional[frozenset] = None,
) -> List[dict]:
    """Slim tool schemas when the model context window is small.

    context_length <= TINY_CONTEXT_THRESHOLD (8k):
        Returns empty list — fenced-block prompt already describes tools;
        sending function schemas on top would eat the entire context.

    context_length <= SMALL_CONTEXT_THRESHOLD (16k):
        Returns slimmed schemas (name + description + required params only).

    context_length > SMALL_CONTEXT_THRESHOLD or <= 0:
        Returns schemas unchanged.

    `always_full`: tool names that always keep their full schema.
    """
    if context_length <= 0 or context_length > SMALL_CONTEXT_THRESHOLD:
        return schemas

    if context_length <= TINY_CONTEXT_THRESHOLD:
        return []

    full_names = always_full if always_full is not None else _ALWAYS_FULL_SCHEMA_TOOLS
    return [
        schema if schema.get("function", {}).get("name") in full_names
        else slim_tool_schema(schema)
        for schema in schemas
    ]


def budget_for_context(context_length: int) -> dict:
    """Return prompt-assembly limits for a given context window size.

    Keys:
    - skill_max_injected (int | None): cap on injected skills; None = no override
    - doc_max_chars (int | None): max chars for active document content;
                                  None = no limit; 0 = skip injection
    - use_compact_prompt (bool): True = use compact (terse rules) system prompt
    """
    if context_length <= 0 or context_length > SMALL_CONTEXT_THRESHOLD:
        return {
            "skill_max_injected": None,
            "doc_max_chars": None,
            "use_compact_prompt": False,
        }
    if context_length <= TINY_CONTEXT_THRESHOLD:
        return {
            "skill_max_injected": 0,
            "doc_max_chars": 0,
            "use_compact_prompt": True,
        }
    # 8k < context <= 16k
    return {
        "skill_max_injected": 1,
        "doc_max_chars": 1500,
        "use_compact_prompt": True,
    }
