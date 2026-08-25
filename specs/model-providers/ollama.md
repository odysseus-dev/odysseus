# Ollama Provider Shape

Last updated: dev@e71f8ce | 2026-08-25

## Scope

Canonical provider ID `ollama`; native Ollama chat/generate plus OpenAI
compatibility; reader `src/model_capability_readers/ollama.py`; discovery and
runtime code in `routes/model_routes.py` and `src/llm_core.py`.

## Catalog And Detail Shapes

Use two native steps:

1. `GET /api/tags` returns `models[]` identity (`name`/`model`, digest,
   `details.family|families`, format, parameter size, quantization). Tags do not
   claim capabilities.
2. `POST /api/show` for a selected model returns explicit `capabilities[]`,
   `details`, and `model_info`. Map completion/chat, embedding, vision, tools,
   and thinking/reasoning tokens. Map context from exact `context_length` or
   native `<architecture>.context_length` fields.

The reader does not parse model names or architecture names. It does parse a
two-column serialized `parameters` value and can take `num_ctx` from it before
falling back to exact or suffix `*.context_length` keys in structured mappings.
The parameters text is used only for that keyed limit lookup, not capability
inference.

## Request And Response Shape

Native chat uses `/api/chat`, `messages`, optional OpenAI-shaped tool
definitions, `format`, `options`, and model-dependent `think`. Responses use
`message.content`, `message.thinking`, and `message.tool_calls`. Generate uses
top-level `response` and `thinking`. OpenAI compatibility is a separate dialect
and can change control names independently.

Manual Ollama endpoints registered against the OpenAI-compatible `/v1` surface default to text/prompted tools unless the operator explicitly enables `supports_tools`; model naming alone does not opt that dialect into native function schemas.

Thinking control is model-specific: most documented reasoning families accept
a native bool, while GPT-OSS accepts low/medium/high and cannot be fully
disabled. A reported Ollama 0.20.6 Qwen3.5 OpenAI-compat path requires
`reasoning_effort: none` rather than `think: false` (#5503); keep it versioned
and low-confidence until corroborated.

## Fallback And Safety

Current reader detection identifies port 11434 as Ollama, in addition to an explicit endpoint kind or an exact/label-bounded `ollama.com` hostname. This is a normalization hint, not endpoint trust or capability evidence. Names that contain `vision`, `embed`, or `qwen` are not capability evidence (#3743, #4487).

## Current Gaps

- List discovery needs an orchestrated `/api/show` detail step per model.
- Runtime OpenAI-compat thinking suppression still contains name heuristics.
